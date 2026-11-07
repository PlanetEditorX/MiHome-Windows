# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""应用内更新：下载（可续传）→ 落盘缓存 → 解压暂存 → 进程内替换目录内容 → 重启。

v0.8.3~v0.9.0 用的是「写一个 cmd 脚本、退出进程、由脚本 robocopy + move +
start」的做法。实测在目标机器上这条链路会在中途静默中断：脚本写了第一行
日志之后就再没有下文，用户看到的是「下载完成 → 没有任何反应 → 手动点开
还是旧版本 → 再点更新又从零下载」。脚本本身不可观测、不可调试，也不受
应用控制（被杀软拦、被 HIPS 终止都表现为「什么都没发生」）。

因此这里把替换动作整个搬回进程内。v0.9.1/0.9.2 用的是「整目录改名」
（`<安装目录>` → `.old`，`.new` → `<安装目录>`），但真机上会稳定失败：

    [WinError 32] 另一个程序正在使用此文件: '<安装目录>' -> '<安装目录>.old'

实测同一台机器、同一个正在运行的应用：**目录里的文件和子目录都能改名，
唯独安装目录本身不能**——应用（或资源管理器/杀软）持有该目录的句柄。
注意这与「notepad 单文件」的探测结论不同，别再拿那个结论当依据。

现在改成**只搬目录里的顶层条目，永远不改名安装目录本体**：

1. 旧内容逐条改名到 `<安装目录>.old/`（每条独立重试；失败即整批还原）
2. 新内容逐条从 `<安装目录>.new/` 改名进安装目录（失败同样整批还原）
3. 替换完立刻启动新版本并观察 6 秒：活着才算成功；启动即崩把旧内容搬回来，
   应用继续照常运行（不会出现「点完更新，程序没了」）
4. 所有失败都在对话框里当场说明，不再依赖「下次启动看标记文件」

每一步都是同盘 rename（元数据操作，47 个条目毫秒级完成），且旧内容在
`.old` 里完整保留，任何一步失败都能还原现场，不会留下「新 dll + 旧 exe」。

另外两点：
- **下载包落盘缓存**：按版本号存在数据目录的 `update/` 下，一次下不完支持
  断点续传（HTTP Range）。第二次点更新先用上次的包，包不完整才续传，
  续传失败才从头下载——不再每次都从 0 MB 开始。
- **解压结果也复用**：`<安装目录>.new` 若已经解压完整且版本号匹配，直接拿来
  用，重试时既不用下载也不用解压。
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QProcess, Signal

# 打包产物里主程序的文件名（build.ps1 固定产出）
EXE_NAME = "MiHome-Windows.exe"

# 兼容旧版本留下的失败标记与日志名：v0.9.1 之前由 cmd 脚本写入 / 提示里
# 引用，升级上来的机器可能还留着，读取路径保持不变
FAILURE_FLAG = "mihome_update_failed.txt"
UPDATE_LOG = "mihome_update.log"

# 暂存目录里标记「已解压完整」的文件；内容是 {"tag": "v0.9.1"}
_STAGED_MARK = ".mihome-staged.json"

_CHUNK = 256 * 1024

# 启动新版后观察多久才认定「活着」。太短会把正常启动误判成崩溃，太长
# 让用户对着「正在重启」干等
_VERIFY_SECONDS = 6.0

# 改名重试窗口：刚退出/杀软还在扫 exe 时改名会短暂失败，重试即可
_RENAME_SECONDS = 8.0

_logger = logging.getLogger(__name__)

# QProcess 的局部变量被回收时子进程可能被判定为「孤儿」，挂到模块上保活
_launched_proc: QProcess | None = None


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
    """安装目录能不能写：决定取不取这条下载（别下完 80 MB 才发现没权限）。

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


# ---------------------------------------------------------------- 缓存位置

def _safe_tag(tag: str) -> str:
    """版本号转目录名：去掉 v 前缀与任何路径分隔符。"""
    text = str(tag or "").strip().lower().lstrip("v")
    keep = "".join(c for c in text if c.isalnum() or c in "._-")
    return keep or "latest"


def cache_root() -> Path:
    """更新包缓存目录。放在数据目录而非 %TEMP%：TEMP 会被系统/清理软件
    随时删掉，而这里的包正是「下次点更新别再重下」的凭据。"""
    from app.core import _json_store

    return _json_store.data_dir() / "update"


def cached_archive(tag: str) -> Path:
    safe = _safe_tag(tag)
    return cache_root() / safe / f"MiHome-Windows-{safe}-win64.zip"


def staged_dir(dest: Path) -> Path:
    """新版本的暂存目录：安装目录的同级 `<安装目录>.new`（同卷才能改名）。"""
    return dest.with_name(dest.name + ".new")


def _read_staged_mark(staged: Path) -> dict:
    try:
        return json.loads((staged / _STAGED_MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def staged_ready(staged: Path, tag: str) -> bool:
    """暂存目录是否「完整且就是目标版本」。"""
    if not (staged / EXE_NAME).exists():
        return False
    mark = _read_staged_mark(staged)
    return bool(mark) and str(mark.get("tag") or "") == str(tag or "")


def has_pending_package(info_or_tag) -> bool:
    """是否已有可直接安装的下载/解压结果（界面据此把按钮写成「立即安装」）。

    只做便宜的判断（文件存在 + 有几个字节），真正的完整性校验放到安装
    阶段——在对话框构建时跑一遍 80 MB 的 CRC 校验会让窗口卡住一两秒。
    """
    tag = getattr(info_or_tag, "tag", info_or_tag)
    dest = install_dir()
    if dest is None:
        return False
    if staged_ready(staged_dir(dest), tag):
        return True
    archive = cached_archive(tag)
    try:
        return archive.is_file() and archive.stat().st_size > 0
    except OSError:
        return False


def archive_ok(path: Path) -> bool:
    """缓存 zip 是否完整可用：能打开、含主程序、CRC 全对。

    必须校验 CRC——半截的包解压出来会缺 DLL，装上去就是「启动即崩」，
    比重新下载慢得多。
    """
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with zipfile.ZipFile(path) as zf:
            names = [m.filename.replace("\\", "/").rsplit("/", 1)[-1]
                     for m in zf.infolist()]
            if EXE_NAME not in names:
                return False
            return zf.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


# ---------------------------------------------------------------- 下载器

class UpdateDownloader(QObject):
    """后台准备更新包（下载 → 校验 → 解压暂存）；信号都回主线程。

    三条短路依次生效，越靠前越省事：
    1. `.new` 已解压完整 → 什么都不做，直接安装；
    2. 缓存 zip 完整 → 跳过下载，只解压；
    3. 缓存 zip 半截 → 带 Range 续传，续传被拒才从头下。
    """

    # (已下载字节, 总字节)；总字节为 0 表示服务端未给长度
    progress = Signal(int, int)
    # 阶段文案（下载中 / 解压中 / 复用缓存…），直接显示在对话框里
    status = Signal(str)
    # (暂存目录 Path 或 None, 异常或 None)
    finished = Signal(object, object)

    def __init__(self, url: str, tag: str = "", size: int = 0, parent=None):
        super().__init__(parent)
        self._url = url
        self._tag = str(tag or "")
        self._size = int(size or 0)
        self._cancelled = False

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        """请求取消；下载/解压循环在下一个分片处退出。"""
        self._cancelled = True

    def _run(self) -> None:
        try:
            staged = self._prepare()
        except Exception as exc:  # 网络/解压异常全部转回调，不打崩进程
            _logger.warning("update: prepare failed: %s", exc)
            self.finished.emit(None, exc)
        else:
            self.finished.emit(staged, None)

    # ---------- 三个步骤 ----------

    def _prepare(self) -> Path:
        dest = install_dir()
        if dest is None:
            raise RuntimeError("当前不是安装版，无法应用内更新")

        staged = staged_dir(dest)
        if staged_ready(staged, self._tag):
            _logger.info("update: reuse staged %s", staged)
            self.status.emit("使用上次已解压好的更新包…")
            self.progress.emit(1, 1)
            return staged

        archive = cached_archive(self._tag)
        if archive_ok(archive):
            _logger.info("update: reuse archive %s", archive)
            self.status.emit("使用已下载的更新包，无需重新下载…")
        else:
            self._download(archive)

        self._extract(archive, staged)
        return staged

    def _download(self, archive: Path) -> None:
        from app import __version__

        archive.parent.mkdir(parents=True, exist_ok=True)
        part = archive.parent / (archive.name + ".part")
        have = part.stat().st_size if part.exists() else 0

        # 上次其实已经下完、只是没来得及改名（进程被杀/断电）。此时若还带
        # Range 去续传，服务端会回 416，用户看到的是莫名其妙的客户端报错
        if have and self._size and have >= self._size:
            part.replace(archive)
            if archive_ok(archive):
                _logger.info("update: leftover part was already complete")
                return
            archive.unlink(missing_ok=True)
            have = 0

        headers = {
            "User-Agent": f"MiHome-Windows/{__version__}",
            "Accept": "application/octet-stream",
        }
        if have:
            headers["Range"] = f"bytes={have}-"
            _logger.info("update: resume from %d bytes", have)
            self.status.emit("继续下载上次未完成的更新包…")

        with requests.get(self._url, stream=True, timeout=(8, 60),
                          headers=headers) as resp:
            if have and resp.status_code == 200:
                # 服务端不认 Range：只能丢掉半包重来
                _logger.info("update: server ignored Range, restart download")
                have = 0
                part.unlink(missing_ok=True)
            elif have and resp.status_code != 206:
                raise RuntimeError(f"续传失败（HTTP {resp.status_code}），请重试")
            resp.raise_for_status()

            total = _total_bytes(resp, have, self._size)
            done = have
            self.progress.emit(done, total)
            if not have:
                self.status.emit("正在下载更新包…")
            with open(part, "ab" if have else "wb") as fp:
                for chunk in resp.iter_content(_CHUNK):
                    if self._cancelled:
                        raise RuntimeError("更新已取消")
                    if not chunk:
                        continue
                    fp.write(chunk)
                    done += len(chunk)
                    self.progress.emit(done, total)

        if self._size and part.stat().st_size < self._size:
            # 连接中途断掉：把半包留下，下次续传
            raise RuntimeError(
                f"下载未完成（{part.stat().st_size} / {self._size} 字节），请重试")

        part.replace(archive)
        if not archive_ok(archive):
            archive.unlink(missing_ok=True)
            raise RuntimeError("更新包校验失败（可能下载不完整），请重试")

    def _extract(self, archive: Path, staged: Path) -> None:
        self.status.emit("正在解压新版本…")
        # 上一轮的半成品必须先清掉：留下缺 DLL 的目录会装出「启动即崩」的版本
        if staged.exists():
            _rmtree(staged)
        staged.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as zf:
                members = zf.infolist()
                total = max(1, len(members))
                root = staged.resolve()
                for index, member in enumerate(members, 1):
                    if self._cancelled:
                        raise RuntimeError("更新已取消")
                    target = (staged / member.filename).resolve()
                    # 只接受解压目录内的路径，挡住 zip 里的 .. 穿越与绝对路径
                    if target != root and root not in target.parents:
                        raise RuntimeError("更新包包含非法路径")
                    zf.extract(member, staged)
                    if index % 40 == 0 or index == total:
                        # 先改文案再动进度条：进度回调会按阶段决定要不要
                        # 覆盖状态文字，顺序反了会闪一下「正在下载」
                        self.status.emit(
                            f"正在解压新版本… {index * 100 // total}%")
                        self.progress.emit(index, total)
            _flatten(staged)
            if not (staged / EXE_NAME).exists():
                raise RuntimeError(f"更新包里没有 {EXE_NAME}")
            (staged / _STAGED_MARK).write_text(
                json.dumps({"tag": self._tag}), encoding="utf-8")
        except Exception:
            _rmtree(staged)
            raise


def _total_bytes(resp, have: int, fallback: int) -> int:
    """算总长：优先 Content-Range 的总量，其次 Content-Length + 已有，最后用资产大小。"""
    rng = resp.headers.get("Content-Range") or ""
    if "/" in rng:
        tail = rng.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    length = resp.headers.get("Content-Length")
    if length and str(length).isdigit():
        return have + int(length)
    return fallback


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


def _flatten(staged: Path) -> None:
    """把 zip 里多出来的那层同名目录拉平，让 staged 本身就是程序根。"""
    inner = _app_root(staged)
    if inner == staged:
        return
    tmp = staged.with_name(staged.name + ".flat")
    _rmtree(tmp)
    os.rename(inner, tmp)
    _rmtree(staged)
    os.rename(tmp, staged)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------- 安装

def _rename_retry(src: Path, dst: Path, timeout: float = _RENAME_SECONDS) -> None:
    """在超时窗口内反复尝试改名。

    刚退出的进程、正在扫描新文件的杀软都会让改名短暂失败（共享冲突），
    重试几百毫秒基本都能过；一直失败就抛出去由调用方回滚并提示。
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.rename(src, dst)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _entries(path: Path) -> list[Path]:
    """目录里的顶层条目，按名字排序（两个阶段/回滚的顺序要能对上）。"""
    return sorted(path.iterdir(), key=lambda p: p.name)


def _move_entries(src: Path, dst_dir: Path, moved: list[str]) -> None:
    """把 src 下的顶层条目逐条改名到 dst_dir，成功后记下名字供回滚。

    这里刻意**不改名 src 目录本身**：应用自己（或资源管理器、杀软）往往
    持有安装目录的句柄，整目录改名会稳定报 WinError 32；里面的条目改名
    则不受影响（真机实测：文件 OK、子目录 OK、目录本体 FAIL）。
    """
    for item in _entries(src):
        _rename_retry(item, dst_dir / item.name)
        moved.append(item.name)


def _restore_entries(src_dir: Path, dst: Path, moved: list[str]) -> None:
    """尽力把 _move_entries 搬走的东西搬回去（失败只记日志，继续还原其余）。"""
    for name in reversed(moved):
        src, target = src_dir / name, dst / name
        if not src.exists():
            continue
        try:
            _rename_retry(src, target, timeout=1.0)
        except OSError:
            _logger.exception("update: cannot restore %s", target)


def _fresh_old_dir(dest: Path) -> Path:
    """建一个空的备份目录（优先 `<安装目录>.old`），返回它的路径。"""
    preferred = dest.with_name(dest.name + ".old")
    if preferred.exists():
        _rmtree(preferred)
    if preferred.exists():
        # 上一轮的旧 exe 还被占用，删不干净：换个名字，绝不往里混内容
        preferred = dest.with_name(f"{dest.name}.old-{int(time.time())}")
    preferred.mkdir(parents=True, exist_ok=True)
    return preferred


def apply_update(staged: Path, dest: Path) -> Path:
    """把暂存的新版本换进安装目录，返回旧版本所在的备份目录。

    只搬目录内容、不改名安装目录本体；任何一步失败都整批还原，绝不留下
    「新 dll + 旧 exe」的混搭目录——那正是「要装两三次」的根因。
    """
    if not (staged / EXE_NAME).exists():
        raise RuntimeError(f"暂存目录里没有 {EXE_NAME}")
    if not dir_writable(dest):
        raise RuntimeError("安装目录不可写（可能装在了 Program Files）")
    if not dir_writable(dest.parent):
        raise RuntimeError("安装目录所在文件夹不可写，无法完成替换")

    old = _fresh_old_dir(dest)
    _logger.info("update: swap contents of %s -> %s", dest, old)
    moved_out: list[str] = []
    moved_in: list[str] = []

    # 阶段一：旧内容整体让位。此时安装目录短暂为空，但目录本体一直在，
    # 应用持有的句柄、快捷方式、当前工作目录都不受影响。
    try:
        _move_entries(dest, old, moved_out)
    except OSError as exc:
        _logger.warning("update: cannot move old files aside: %s", exc)
        _restore_entries(old, dest, moved_out)
        _rmtree(old)
        # 文案保持短：Toast 显示不下完整 WinError，技术细节只进日志
        raise RuntimeError(
            "安装目录有文件被占用；请关闭该文件夹的窗口后重试") from exc

    # 阶段二：新内容就位。失败时先把新文件退回 .new，再把旧文件放回安装目录。
    try:
        _move_entries(staged, dest, moved_in)
    except OSError as exc:
        _logger.warning("update: cannot move new files in: %s", exc)
        _restore_entries(dest, staged, moved_in)
        _restore_entries(old, dest, moved_out)
        _rmtree(old)
        raise RuntimeError("新版本就位失败，已还原旧版本，可再试一次") from exc

    (dest / _STAGED_MARK).unlink(missing_ok=True)
    _rmtree(staged)       # 已经搬空，去掉空壳，下次重新解压即可
    _logger.info("update: swapped, new build at %s", dest)
    return old


def _rollback(dest: Path, old: Path) -> None:
    """新版启动即崩：把坏内容挪到 .bad，再把 .old 里的旧内容搬回安装目录。

    同样只操作目录内容。整目录改名在这里一样会撞 WinError 32，不能用。
    """
    bad = dest.with_name(dest.name + ".bad")
    _rmtree(bad)
    try:
        bad.mkdir(parents=True, exist_ok=True)
    except OSError:
        _logger.exception("update: cannot create .bad dir")
        return

    moved: list[str] = []
    try:
        _move_entries(dest, bad, moved)
    except OSError:
        _logger.exception("update: cannot move broken build aside")
        _restore_entries(bad, dest, moved)
        return

    restored: list[str] = []
    try:
        _move_entries(old, dest, restored)
    except OSError:
        _logger.exception("update: rollback move failed")
        # 先把已经搬回来的退回 old，再把坏版本放回 dest，尽量保持可用
        _restore_entries(dest, old, restored)
        _restore_entries(bad, dest, moved)
        return

    _rmtree(bad)
    _rmtree(old)
    try:
        failure_flag_path().write_text("startup failed", encoding="utf-8")
    except OSError:
        pass


def relaunch_and_verify(old: Path, dest: Path,
                        wait: float = _VERIFY_SECONDS) -> bool:
    """启动新版本并确认它活着；启动即崩则回滚旧版本并返回 False。

    由**旧进程**执行：先带 MIHOME_RESTARTED=1 起新进程（跳过单实例判定，
    否则新进程会被当成「唤醒旧窗口」直接退出），观察 wait 秒。期间持续
    处理界面事件，进度条不会假死。
    """
    global _launched_proc

    exe = dest / EXE_NAME
    if not exe.exists():
        _logger.error("update: %s missing after swap", exe)
        _rollback(dest, old)
        return False

    os.environ["MIHOME_RESTARTED"] = "1"
    proc = QProcess()
    proc.setProgram(str(exe))
    proc.setWorkingDirectory(str(dest))
    proc.start()
    if not proc.waitForStarted(15000):
        _logger.error("update: new build failed to start")
        _rollback(dest, old)
        return False

    _launched_proc = proc
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.state() == QProcess.ProcessState.NotRunning:
            break
        if app is not None:
            app.processEvents()
        time.sleep(0.05)

    if proc.state() == QProcess.ProcessState.NotRunning:
        _logger.error(
            "update: new build exited immediately (code %s), rolling back",
            proc.exitCode())
        _rollback(dest, old)
        return False

    _logger.info("update: new build alive, old build kept at %s", old)
    return True


# ---------------------------------------------------------------- 清理

def cleanup_leftovers() -> None:
    """清理上次更新留下的残渣（启动时调用，失败静默）。

    三类东西会留下来：旧版脚本在 %TEMP% 下的解压目录、安装目录旁的
    `.old` / `.bad` 备份、以及缓存里其它版本的包。`.new` 只要还是完整的
    就保留——那正是「下次点更新不用重下」的凭据。
    """
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
            _rmtree(path)
    except OSError:
        pass

    dest = install_dir()
    if dest is None:
        return

    # .old / .bad（含带时间戳的变体）：旧版本进程退出后就能删了
    for stale in dest.parent.glob(dest.name + ".*"):
        suffix = stale.name[len(dest.name):]
        if not suffix.startswith((".old", ".bad")):
            continue
        for _ in range(3):
            if not stale.exists():
                break
            _rmtree(stale)
            if stale.exists():
                time.sleep(0.4)

    # 暂存目录：完整且新鲜就留着，半成品或放太久一律清掉
    staged = staged_dir(dest)
    try:
        if staged.is_dir():
            age = time.time() - staged.stat().st_mtime
            complete = bool(_read_staged_mark(staged)) and (staged / EXE_NAME).exists()
            if not complete or age > 7 * 86400:
                _rmtree(staged)
    except OSError:
        pass

    _prune_cache()


def _prune_cache(keep: int = 1) -> None:
    """缓存只留最近处理过的 keep 个版本，避免每个版本堆 80 MB。"""
    root = cache_root()
    try:
        dirs = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for extra in dirs[keep:]:
        _rmtree(extra)
