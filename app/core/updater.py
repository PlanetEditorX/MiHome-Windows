# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""应用内更新：下载 Release 资产 → 解压暂存 → 交外部脚本替换并重启。

为什么替换必须交给外部脚本：Windows 不允许覆盖正在运行的 exe，
而「先退出再复制」又需要有人在本进程之外继续动作。这里的做法是
生成一个一次性 cmd 脚本，脚本按镜像名轮询到本进程彻底退出后，
把暂存目录**整目录交换**进安装位置，再启动新版并自删。

为什么是目录交换而不是逐文件覆盖：早期版本用 robocopy 直接覆盖安装
目录，只要有一个文件被占用（exe 尚未释放、杀软正在扫刚写出的 dll），
复制就会中途失败，目录里留下「新 dll + 旧 exe」的混搭——这样的目录
启动即崩（用户看到的就是闪退），而下次启动又是旧版本号、再次提示更新，
于是要更新两三次才装好。现在改为：先把新版本完整复制到安装目录旁的
`<安装目录>.new`，再用 rename 把 `<安装目录>` 换成 `<安装目录>.old`、
把 `.new` 顶上去。Windows 只有在目录内所有句柄都释放后才允许改名，
因此「改名成功」本身就是进程真的退出了的证明；任一步失败就整体回滚
到旧目录，绝不留下半新半旧的安装。

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
set "LOG=%TEMP%\\{log}"

rem sibling dirs used for the swap: same volume, so rename stays atomic
for %%A in ("%DST%") do (
  set "DPARENT=%%~dpA"
  set "DNAME=%%~nxA"
)
set "NEW=%DPARENT%%DNAME%.new"
set "OLD=%DPARENT%%DNAME%.old"

echo [%DATE% %TIME%] start src="%SRC%" dst="%DST%" >> "%LOG%"
del "%FLAG%" 2>nul

rem ---- 0. drop leftovers from a previous attempt ----
if exist "%OLD%" rd /s /q "%OLD%" 2>nul
if exist "%NEW%" rd /s /q "%NEW%" 2>nul

rem ---- 1. stage the new build beside the install dir (never touches the live exe) ----
"%SYS%\\robocopy.exe" "%SRC%" "%NEW%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
echo [%DATE% %TIME%] stage rc=%ERRORLEVEL% >> "%LOG%"
if not exist "%NEW%\\%EXE%" (
  echo [%DATE% %TIME%] stage incomplete, abort >> "%LOG%"
  echo stage failed > "%FLAG%"
  goto keepold
)

rem ---- 2. swap whole directories ----
rem A directory can be renamed only after every handle inside it is gone, so
rem retrying the rename is a far more accurate "is it still running?" test
rem than matching the image name in tasklist (which would also match a copy
rem installed somewhere else, and make us wait for nothing).
set /a n=0
:swap
move "%DST%" "%OLD%" >nul 2>&1
if not errorlevel 1 goto swapped
set /a n+=1
if !n! lss 90 (
  "%SYS%\\ping.exe" -n 2 127.0.0.1 >nul
  goto swap
)
echo [%DATE% %TIME%] dst busy, rename failed after !n! tries >> "%LOG%"
echo rename failed > "%FLAG%"
goto keepold

:swapped
move "%NEW%" "%DST%" >nul 2>&1
if errorlevel 1 (
  rem put the old build back: a missing install dir is worse than a failed update
  move "%OLD%" "%DST%" >nul 2>&1
  echo [%DATE% %TIME%] swap failed, rolled back >> "%LOG%"
  echo swap failed > "%FLAG%"
  goto keepold
)
echo [%DATE% %TIME%] swapped ok >> "%LOG%"

rem ---- 3. start the new build and make sure it survives ----
start "" /D "%DST%" "%DST%\\%EXE%"
if errorlevel 1 echo [%DATE% %TIME%] start failed rc=%ERRORLEVEL% >> "%LOG%"
"%SYS%\\ping.exe" -n 5 127.0.0.1 >nul
"%SYS%\\tasklist.exe" /FI "IMAGENAME eq %EXE%" 2>nul | "%SYS%\\findstr.exe" /I "%EXE%" >nul
if errorlevel 1 (
  rem the fresh build died right after launch: put the previous one back,
  rem otherwise the user is left with an app that never opens
  echo [%DATE% %TIME%] new build exited, rolling back >> "%LOG%"
  move "%DST%" "%NEW%" >nul 2>&1
  if not errorlevel 1 move "%OLD%" "%DST%" >nul 2>&1
  echo startup failed > "%FLAG%"
  if exist "%DST%\\%EXE%" start "" /D "%DST%" "%DST%\\%EXE%"
) else (
  rd /s /q "%OLD%" 2>nul
)

rem ---- 4. cleanup ----
rd /s /q "%SRC%" 2>nul
rd /s /q "%NEW%" 2>nul
echo [%DATE% %TIME%] done >> "%LOG%"
rem (goto) makes cmd stop reading this file first, so deleting it here does
rem not produce a trailing "batch file cannot be found" error
(goto) 2>nul & del "%~f0"
exit /b 0

:keepold
rem Never leave a half-updated dir behind: it starts and crashes immediately,
rem which reads as "update broke the app". Bring the old build back instead.
if exist "%DST%\\%EXE%" start "" /D "%DST%" "%DST%\\%EXE%"
rd /s /q "%SRC%" 2>nul
rd /s /q "%NEW%" 2>nul
echo [%DATE% %TIME%] aborted, old build kept >> "%LOG%"
(goto) 2>nul & del "%~f0"
exit /b 1
"""

# 更新失败标记：脚本放弃替换时写到 %TEMP%，下次启动由界面提示并清除
FAILURE_FLAG = "mihome_update_failed.txt"

# 替换脚本的诊断日志：每次更新追加一段，失败时把路径告诉用户
UPDATE_LOG = "mihome_update.log"


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


def dir_writable(dest: Path) -> bool:
    """安装目录能不能写：决定取不取这条下载（别下完 76 MB 才发现没权限）。

    装在 Program Files 且未提权的场景很常见，提前探一次比让用户白等
    几分钟强。
    """
    probe = dest / ".mihome-write-probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


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
            src=str(staging), dst=str(dest), exe=EXE_NAME, flag=FAILURE_FLAG,
            log=UPDATE_LOG),
        encoding="ascii",
        errors="replace",
    )
    launched = QProcess.startDetached("cmd.exe", ["/c", str(script)])
    if not launched:
        raise RuntimeError("无法启动更新脚本")
    return script


def cleanup_leftovers() -> None:
    """清理上次更新留下的残渣（启动时调用，失败静默）。

    两类东西会留下来：%TEMP% 下的 staging 目录（替换脚本收尾时 `rd` 被
    杀软占着删不掉），以及安装目录旁的 `<目录>.old` / `.new` 备份目录。
    它们不影响运行，但每次更新叠一个，久了会占几百 MB。
    """
    import shutil
    import time

    tmp = Path(tempfile.gettempdir())
    cutoff = time.time() - 300      # 跳过 5 分钟内刚建的：可能正是本次更新在用
    try:
        for path in tmp.glob("mihome-update-*"):
            if not path.is_dir():
                continue
            try:
                if path.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass

    dest = install_dir()
    if dest is None:
        return
    for suffix in (".old", ".new"):
        stale = dest.with_name(dest.name + suffix)
        try:
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass
