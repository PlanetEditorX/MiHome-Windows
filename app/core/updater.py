# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""应用内更新：下载 Release 资产 → 解压暂存 → 交外部脚本替换并重启。

为什么替换必须交给外部脚本：Windows 不允许覆盖正在运行的 exe，
而「先退出再复制」又需要有人在本进程之外继续动作。这里的做法是
生成一个一次性 cmd 脚本，脚本按镜像名轮询到本进程彻底退出后，
用 robocopy 把暂存目录覆盖进安装目录，再启动新版并自删。

网络请求走独立后台线程（同 update_checker）：下载 76 MB 在弱网下
可能几十秒，绝不能占住界面线程。
"""

import sys
import tempfile
import threading
import zipfile
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QProcess, Signal

# 打包产物里主程序的文件名（build.ps1 固定产出）
EXE_NAME = "MiHome-Windows.exe"

_CHUNK = 256 * 1024

# 更新脚本模板。刻意保持纯 ASCII：cmd 的代码页随系统而变，中文注释
# 在部分环境会乱码甚至被当成命令执行；路径由调用方以完整绝对路径填入。
#
# 所有系统工具都写 %SystemRoot%\System32 绝对路径：裸名会走 PATH，而
# 用户装了 Git/MSYS、Cygwin 等工具链时 PATH 里常有同名 find.exe，会抢在
# 系统的 find 之前被调用（实测 `find /I` 被 GNU find 当成路径而报错），
# 让「等进程退出」的轮询静默失效、进而覆盖正在使用的 exe。
_SCRIPT_TEMPLATE = """@echo off
setlocal enabledelayedexpansion
set "SYS=%SystemRoot%\\System32"
set "SRC={src}"
set "DST={dst}"
set "EXE={exe}"
set "FLAG=%TEMP%\\{flag}"

rem clear any stale failure mark from a previous attempt
del "%FLAG%" 2>nul

rem ---- 1. wait until this app has really exited (max 60s) ----
set /a n=0
:waitloop
"%SYS%\\tasklist.exe" /FI "IMAGENAME eq %EXE%" 2>nul | "%SYS%\\findstr.exe" /I "%EXE%" >nul
if not errorlevel 1 (
  set /a n+=1
  if !n! lss 60 (
    ping -n 2 127.0.0.1 >nul
    goto waitloop
  )
)

rem ---- 2. copy staged files over the install dir (robocopy 0-7 = ok) ----
set /a tries=0
:retry
"%SYS%\\robocopy.exe" "%SRC%" "%DST%" /E /IS /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  set /a tries+=1
  if !tries! lss 20 (
    ping -n 2 127.0.0.1 >nul
    goto retry
  )
  rem could not overwrite (locked or no permission): leave a mark so the
  rem next start can tell the user, then still bring the app back up
  echo robocopy failed > "%FLAG%"
)

rem ---- 3. start the new build, then clean up staging and self ----
start "" "%DST%\\%EXE%"
ping -n 3 127.0.0.1 >nul
rd /s /q "%SRC%" 2>nul
rem (goto) makes cmd stop reading this file first, so deleting it here
rem does not produce a trailing "batch file cannot be found" error
(goto) 2>nul & del "%~f0"
"""

# 更新失败标记：脚本覆盖失败时写到 %TEMP%，下次启动由界面提示并清除
FAILURE_FLAG = "mihome_update_failed.txt"


def failure_flag_path() -> Path:
    return Path(tempfile.gettempdir()) / FAILURE_FLAG


def install_dir() -> Path | None:
    """当前安装目录；源码运行返回 None（没有可替换的安装目录）。"""
    from app import is_packaged

    if not is_packaged():
        return None
    return Path(sys.executable).resolve().parent


def can_self_update(info) -> bool:
    """是否具备应用内更新条件：打包运行 + release 带有 win64 资产。"""
    return install_dir() is not None and bool(getattr(info, "asset_url", ""))


class UpdateDownloader(QObject):
    """后台下载并解压更新包；progress/finished 都在主线程发出。"""

    # (已下载字节, 总字节)；总字节为 0 表示服务端未给 Content-Length
    progress = Signal(int, int)
    # (暂存目录 Path 或 None, 异常或 None)
    finished = Signal(object, object)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._cancelled = False

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        """请求取消；下载循环在下一个分片处退出。"""
        self._cancelled = True

    def _run(self) -> None:
        try:
            staging = self._fetch()
        except Exception as exc:  # 网络/解压异常全部转回调，不打崩进程
            self.finished.emit(None, exc)
        else:
            self.finished.emit(staging, None)

    def _fetch(self) -> Path:
        from app import __version__

        # 解压直接落在 mkdtemp 根目录：交出去的路径本身就是「可以整个
        # 删掉」的临时目录，替换脚本用一次 rd /s /q 就能清理干净，不必
        # 去动它的父目录（父目录里可能有别的东西）
        staging = Path(tempfile.mkdtemp(prefix="mihome-update-"))
        archive = staging / "update.zip"
        try:
            with requests.get(
                self._url,
                stream=True,
                timeout=(8, 60),
                headers={
                    "User-Agent": f"MiHome-Windows/{__version__}",
                    "Accept": "application/octet-stream",
                },
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                self.progress.emit(0, total)
                with open(archive, "wb") as fp:
                    for chunk in resp.iter_content(_CHUNK):
                        if self._cancelled:
                            raise RuntimeError("更新已取消")
                        if not chunk:
                            continue
                        fp.write(chunk)
                        done += len(chunk)
                        self.progress.emit(done, total)

            with zipfile.ZipFile(archive) as zf:
                # 只接受普通文件/目录，挡住 zip 里的绝对路径与 .. 穿越
                for member in zf.infolist():
                    target = (staging / member.filename).resolve()
                    if not str(target).startswith(str(staging.resolve())):
                        raise RuntimeError("更新包包含非法路径")
                zf.extractall(staging)
            archive.unlink(missing_ok=True)

            root = _app_root(staging)
            if not (root / EXE_NAME).exists():
                raise RuntimeError(f"更新包里没有 {EXE_NAME}")
            return root
        except Exception:
            # 失败时清掉暂存，避免 %TEMP% 里堆半成品
            _rmtree(staging)
            raise


def _app_root(root: Path) -> Path:
    """定位 zip 内程序根目录；打包时可能多包一层同名目录。"""
    if (root / EXE_NAME).exists():
        return root
    try:
        for child in root.iterdir():
            if child.is_dir() and (child / EXE_NAME).exists():
                return child
    except OSError:
        pass
    return root


def _rmtree(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def apply_update(staging: Path, dest: Path) -> Path:
    """生成并启动替换脚本，返回脚本路径；调用方随后应立即退出进程。"""
    script = Path(tempfile.gettempdir()) / "mihome_update.cmd"
    script.write_text(
        _SCRIPT_TEMPLATE.format(
            src=str(staging), dst=str(dest), exe=EXE_NAME, flag=FAILURE_FLAG),
        encoding="ascii",
        errors="replace",
    )
    launched = QProcess.startDetached("cmd.exe", ["/c", str(script)])
    if not launched:
        raise RuntimeError("无法启动更新脚本")
    return script
