# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""更新检查与提示对话框的离屏回归测试。

覆盖版本比较、release 字段解析、Markdown 更新日志渲染与兜底文案。
与 theme_test 一样禁写运行数据（isolate 临时目录）。
"""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from tests._isolate import isolate

isolate()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

app = QApplication([])

from app.ui.theme_service import apply_theme

apply_theme("dark")

from app import __version__
from app.core.update_checker import ReleaseInfo, is_newer

# ---------- 1. 版本比较 ----------
assert is_newer("v0.5.0", "0.2.0"), "0.5.0 应比 0.2.0 新"
assert not is_newer("v0.2.0", "0.2.0"), "同版本不应判定为新"
assert is_newer("0.10.0", "0.9.9"), "两位数版本段应按数值比较"
assert is_newer("v1.0.0-beta", "0.9.0"), "预发布后缀不应参与比较"
assert not is_newer("0.9", "0.9.0"), "位数不同按 0 补齐后相等"
print("1. 版本比较 OK")

# ---------- 2. release 字段 ----------
info = ReleaseInfo(
    tag="v0.5.0",
    url="https://example.com/release",
    name="0.5.0 - 优化",
    body="## 更新内容\n\n- **桌面挂件**：新增筛选\n- 修复若干问题",
    published_at="2026-10-22T12:00:00Z",
)
assert info.body.startswith("## 更新内容")
print("2. release 字段 OK")

# ---------- 3. 更新对话框：Markdown 渲染 ----------
from app.ui.update_dialog import UpdateDialog

dlg = UpdateDialog(info, None)
dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
dlg.show()
app.processEvents()

assert "发现新版本" in dlg._title.text()
assert dlg._brand.text() == "米家 - Windows"
# 品牌名与标题同样字号、同样高亮，一眼看出是哪个软件在更新
assert dlg._brand.font().pointSizeF() == dlg._title.font().pointSizeF()
# 右上角 × 已去掉，由底部「稍后提醒」承担关闭
assert not hasattr(dlg, "_close_btn"), "更新对话框不应再有关闭按钮"
assert "v0.5.0" in dlg._version_line.text(), dlg._version_line.text()
assert "2026-10-22" in dlg._meta.text(), dlg._meta.text()
plain = dlg._notes.toPlainText()
assert "桌面挂件" in plain, f"更新日志未渲染: {plain!r}"
assert "**" not in plain, f"Markdown 粗体未渲染: {plain!r}"
assert "更新内容" in plain
assert dlg._download_btn.text().strip() == "前往下载"
assert dlg._later_btn.text() == "稍后提醒"
print("3. 更新对话框 Markdown 渲染 OK")

# ---------- 4. 无正文兜底 ----------
empty = ReleaseInfo(tag="v0.6.0", url="https://example.com", name="", body="")
dlg2 = UpdateDialog(empty, None)
dlg2.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
dlg2.show()
app.processEvents()
fallback = dlg2._notes.toPlainText()
assert "未提供更新说明" in fallback, f"空正文兜底文案缺失: {fallback!r}"
assert "v0.6.0" in dlg2._version_line.text()
print("4. 空正文兜底 OK")

# ---------- 5. 主题切换重渲染 ----------
apply_theme("light")
dlg.retheme()
plain_light = dlg._notes.toPlainText()
assert "桌面挂件" in plain_light, "切换主题后更新日志丢失"
print("5. 主题切换重渲染 OK")

# ---------- 6. 资产挑选：只认 win64 zip ----------
from app.core.update_checker import _pick_asset

assets = [
    # GitHub 会自动附带源码 zip，名字同样以 .zip 结尾——必须被排除
    {"name": "Source code (zip)", "browser_download_url": "https://x/src.zip",
     "size": 100},
    {"name": "MiHome-Windows-v0.6.1-win64.zip",
     "browser_download_url": "https://x/ok.zip", "size": 79710824},
    {"name": "checksums.txt", "browser_download_url": "https://x/sums.txt",
     "size": 64},
]
url, name, size = _pick_asset(assets)
assert url == "https://x/ok.zip", url
assert name == "MiHome-Windows-v0.6.1-win64.zip", name
assert size == 79710824, size
# 无资产 / 字段缺失时全部回空，界面据此回退「前往下载」
assert _pick_asset([]) == ("", "", 0)
assert _pick_asset(None) == ("", "", 0)
assert _pick_asset([{"name": "a-win64.zip"}]) == ("", "", 0)
print("6. 资产挑选 OK")

# ---------- 7. 打包态：主按钮变「立即更新」，进度区默认隐藏 ----------
from pathlib import Path

from app.core import updater

_orig_install_dir = updater.install_dir
updater.install_dir = lambda: Path(r"C:\fake\install")
try:
    info_asset = ReleaseInfo(
        tag="v0.6.1", url="https://example.com", name="v0.6.1", body="x",
        asset_url="https://x/ok.zip",
        asset_name="MiHome-Windows-v0.6.1-win64.zip", asset_size=79710824)
    dlg3 = UpdateDialog(info_asset, None)
    dlg3.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dlg3.show()
    app.processEvents()
    assert dlg3._self_update is True, "有 win64 资产应支持应用内更新"
    assert dlg3._download_btn.text().strip() == "立即更新", \
        dlg3._download_btn.text()
    # 进度条与状态文字在点击前不该占位
    assert dlg3._progress.isHidden(), "进度条应默认隐藏"
    assert dlg3._status.isHidden(), "状态文字应默认隐藏"
    dlg3.done(0)
finally:
    updater.install_dir = _orig_install_dir
app.processEvents()
print("7. 打包态主按钮 OK")

# ---------- 8. 缓存位置与暂存目录 ----------
import shutil  # noqa: E402
import tempfile as _tempfile  # noqa: E402
import time as _time  # noqa: E402
import zipfile as _zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

assert updater.failure_flag_path().name == updater.FAILURE_FLAG
# 版本号转目录名：容忍 v 前缀与大小写，且绝不能带出路径分隔符
assert updater._safe_tag("v0.9.1") == "0.9.1"
assert updater._safe_tag("V1.0.0-RC1") == "1.0.0-rc1"
assert "/" not in updater._safe_tag("../evil") and "\\" not in updater._safe_tag("a\\b")
assert updater._safe_tag("") == "latest"
# 缓存落在数据目录（会被用户看到、需要能手动清理），不在 %TEMP%
assert updater.cache_root().name == "update"
from app.core import _json_store  # noqa: E402

assert str(updater.cache_root()).startswith(str(_json_store.data_dir())), \
    "更新包必须缓存在数据目录，%TEMP% 会被系统清理"

_root = Path(_tempfile.mkdtemp(prefix="miupdate-"))
_dest = _root / "App"
_dest.mkdir()
(_dest / "old.dll").write_text("old", encoding="utf-8")
_staged = updater.staged_dir(_dest)


def _make_zip(path: Path, tag: str, extra: str = "new") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _zipfile.ZipFile(path, "w") as zf:
        zf.writestr(updater.EXE_NAME, b"MZ" + tag.encode())
        zf.writestr("part0.dll", extra.encode())
        zf.writestr("_internal/part1.dll", extra.encode())
    return path.read_bytes()


_orig_install = updater.install_dir
_real_rename = os.rename
updater.install_dir = lambda: _dest
try:
    # ---------- 8a. 缓存命中：一条请求都不该发 ----------
    _archive = updater.cached_archive("v9.9.9")
    _full = _make_zip(_archive, "v9.9.9")
    _hits = {"get": 0}
    _orig_get = updater.requests.get

    def _no_network(*a, **kw):
        _hits["get"] += 1
        raise AssertionError("缓存命中时不该再发下载请求")

    updater.requests.get = _no_network
    try:
        _dl = updater.UpdateDownloader("https://x/ok.zip", "v9.9.9", len(_full))
        _ready = _dl._prepare()
    finally:
        updater.requests.get = _orig_get
    assert _hits["get"] == 0, "已有完整更新包却仍然发起了下载"
    assert _ready == _staged, _ready
    assert (_staged / updater.EXE_NAME).exists(), "解压后缺少主程序"
    assert (_staged / "part0.dll").read_text(encoding="utf-8") == "new"
    # 多包一层目录的 zip 要拉平，否则安装目录里会多套一层文件夹
    assert not (_staged / "MiHome-Windows.exe").is_dir()
    assert updater.staged_ready(_staged, "v9.9.9"), "暂存标记应可复用"
    assert updater.has_pending_package("v9.9.9") is True
    assert updater.has_pending_package("v9.9.8") is False
    print("8a. 缓存命中 → 不发请求、直接解压复用 OK")

    # ---------- 8b. 已解压完的暂存目录：连解压都要跳过 ----------
    _sentinel = _staged / "keep-me.txt"
    _sentinel.write_text("x", encoding="utf-8")
    updater.requests.get = _no_network
    try:
        _dl2 = updater.UpdateDownloader("https://x/ok.zip", "v9.9.9", len(_full))
        assert _dl2._prepare() == _staged
    finally:
        updater.requests.get = _orig_get
    assert _sentinel.exists(), "暂存目录已完整时不该重新解压"
    print("8b. 暂存目录复用 → 不重复解压 OK")

    # ---------- 8c. 断点续传：Range 只补后半段 ----------
    _arc2 = updater.cached_archive("v9.9.10")
    _arc2.parent.mkdir(parents=True, exist_ok=True)
    _data = _make_zip(_root / "src.zip", "v9.9.10")
    _half = len(_data) // 2
    (_arc2.parent / (_arc2.name + ".part")).write_bytes(_data[:_half])
    _seen = []

    class _Resp:
        status_code = 206

        def __init__(self, body, headers):
            self._body, self.headers = body, headers

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            for i in range(0, len(self._body), size):
                yield self._body[i:i + size]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _range_get(url, **kw):
        _seen.append(kw.get("headers") or {})
        return _Resp(_data[_half:], {
            "Content-Range": f"bytes {_half}-{len(_data) - 1}/{len(_data)}",
            "Content-Length": str(len(_data) - _half),
        })

    updater.requests.get = _range_get
    try:
        _dl3 = updater.UpdateDownloader("https://x/ok.zip", "v9.9.10", len(_data))
        _dl3._download(_arc2)
    finally:
        updater.requests.get = _orig_get
    assert _seen and _seen[0].get("Range") == f"bytes={_half}-", \
        f"续传必须带 Range 头: {_seen}"
    assert _arc2.read_bytes() == _data, "续传结果与完整包不一致"
    assert not (_arc2.parent / (_arc2.name + ".part")).exists(), "半包未清理"
    print("8c. Range 断点续传 OK")

    # ---------- 8d. 服务端不支持续传：丢弃半包重下 ----------
    _arc3 = updater.cached_archive("v9.9.11")
    _arc3.parent.mkdir(parents=True, exist_ok=True)
    (_arc3.parent / (_arc3.name + ".part")).write_bytes(b"garbage")
    _data3 = _make_zip(_root / "src3.zip", "v9.9.11")
    _seen.clear()

    def _plain_get(url, **kw):
        _seen.append(kw.get("headers") or {})
        return _Resp(_data3, {"Content-Length": str(len(_data3))})

    _Resp.status_code = 200
    updater.requests.get = _plain_get
    try:
        _dl4 = updater.UpdateDownloader("https://x/ok.zip", "v9.9.11", len(_data3))
        _dl4._download(_arc3)
    finally:
        updater.requests.get = _orig_get
        _Resp.status_code = 206
    assert "Range" in _seen[0], "有半包时应先尝试续传"
    assert _arc3.read_bytes() == _data3, "服务端忽略 Range 后应从头下完整包"
    print("8d. 服务端忽略 Range → 从头重下 OK")

    # ---------- 8e. 校验失败：坏包不能留在缓存里 ----------
    _arc4 = updater.cached_archive("v9.9.12")
    _arc4.parent.mkdir(parents=True, exist_ok=True)

    _junk = b"not a zip at all!"
    def _corrupt_get(url, **kw):
        # 长度对得上、内容不是 zip：专门走「校验失败」这条分支
        return _Resp(_junk, {"Content-Length": str(len(_junk))})

    updater.requests.get = _corrupt_get
    _failed = False
    try:
        _dl5 = updater.UpdateDownloader("https://x/ok.zip", "v9.9.12", len(_junk))
        _dl5._download(_arc4)
    except RuntimeError as exc:
        _failed = "校验失败" in str(exc)
    finally:
        updater.requests.get = _orig_get
    assert _failed, "坏包必须报校验失败，不能直接拿去解压"
    assert not _arc4.exists(), "校验失败的包必须删掉，否则每次重试都白解压"
    print("8e. 校验失败 → 删包报错 OK")

    # ---------- 8f0. 真机回归：安装目录本体被占用（WinError 32） ----------
    # v0.9.1/0.9.2 在这里稳定失败：应用/资源管理器持有安装目录句柄，
    # 整目录改名必报 32，但目录内的文件与子目录改名完全正常（实机实测）。
    # 从这里起全程注入这个限制，下面的安装、回滚、清理都必须不再碰目录本体。
    _blocked = {"dir": 0}

    def _locked_dir_rename(src, dst):
        if Path(src) == _dest or Path(dst) == _dest:
            _blocked["dir"] += 1
            raise PermissionError(
                32, "另一个程序正在使用此文件，进程无法访问", str(src), 32)
        return _real_rename(src, dst)

    os.rename = _locked_dir_rename

    # ---------- 8f. 进程内整目录交换 ----------
    _old = updater.apply_update(_staged, _dest)
    assert (_dest / updater.EXE_NAME).read_bytes().startswith(b"MZ" + b"v9.9.9")
    assert not (_dest / "old.dll").exists(), "旧文件不该留在新版本目录里"
    assert (_dest / "part0.dll").read_text(encoding="utf-8") == "new"
    assert (_old / "old.dll").read_text(encoding="utf-8") == "old"
    assert (_old / updater.EXE_NAME).exists() is False
    # 暂存标记属于缓存元数据，装完后不该留在安装目录里
    assert not (_dest / updater._STAGED_MARK).exists()
    assert _blocked["dir"] == 0, "又去改安装目录本体了，真机必报 WinError 32"
    print("8f. 目录本体被锁时仍能只搬内容完成替换 OK")

    # ---------- 8g. 新版启动即崩 → 回滚 ----------
    updater._rollback(_dest, _old)
    assert (_dest / "old.dll").read_text(encoding="utf-8") == "old", \
        "回滚后必须还原成完整旧版本"
    assert (updater.EXE_NAME in [p.name for p in _dest.iterdir()]) is False
    assert not _dest.with_name(_dest.name + ".bad").exists(), "回滚后应清掉坏目录"
    assert updater.failure_flag_path().exists(), "回滚要留标记"
    updater.failure_flag_path().unlink(missing_ok=True)
    assert _blocked["dir"] == 0, "回滚也不能改安装目录本体"
    print("8g. 启动失败回滚 OK")

    # ---------- 8h. 交换失败要回滚，不能把安装目录弄丢 ----------
    _staged2 = updater.staged_dir(_dest)
    shutil.rmtree(_staged2, ignore_errors=True)
    _staged2.mkdir(parents=True)
    (_staged2 / "part0.dll").write_text("half", encoding="utf-8")
    _raised = False
    try:
        updater.apply_update(_staged2, _dest)
    except RuntimeError:
        _raised = True
    assert _raised, "暂存目录不完整时必须拒绝安装"
    assert (_dest / "old.dll").exists(), "拒绝安装时安装目录必须原样保留"
    print("8h. 暂存不完整 → 拒绝安装且不动现场 OK")

    # ---------- 8h2. 搬入到一半失败：整批还原，.new 留着可重试 ----------
    _staged3 = updater.staged_dir(_dest)
    shutil.rmtree(_staged3, ignore_errors=True)
    _staged3.mkdir(parents=True)
    (_staged3 / updater.EXE_NAME).write_bytes(b"MZnew")
    (_staged3 / "a.dll").write_text("A", encoding="utf-8")
    (_staged3 / "b.dll").write_text("B", encoding="utf-8")

    def _fail_second(src, dst):
        if Path(src).name == "b.dll" and Path(dst).parent == _dest:
            raise PermissionError(32, "共享冲突", str(src), 32)
        return _locked_dir_rename(src, dst)

    os.rename = _fail_second
    _raised = False
    try:
        updater.apply_update(_staged3, _dest)
    except RuntimeError as exc:
        _raised = "已还原旧版本" in str(exc)
    finally:
        os.rename = _locked_dir_rename
    assert _raised, "搬入失败时应报错并说明已还原"
    assert (_dest / "old.dll").read_text(encoding="utf-8") == "old", \
        "失败后安装目录必须是完整的旧版本"
    assert not (_dest / "a.dll").exists() and not (_dest / "b.dll").exists(), \
        "失败后不能留下半新半旧的目录"
    assert not _dest.with_name(_dest.name + ".old").exists(), "备份目录应清理"
    assert (_staged3 / updater.EXE_NAME).exists() and \
        (_staged3 / "b.dll").exists(), "新内容应退回 .new，供下次直接重试"
    print("8h2. 搬入中途失败 → 现场完整还原 OK")

    # ---------- 8i. 启动残留清理 ----------
    _stale = Path(_tempfile.mkdtemp(prefix="mihome-update-"))
    (_stale / "leftover.bin").write_text("x", encoding="utf-8")
    _old_ts = _time.time() - 3600
    os.utime(_stale, (_old_ts, _old_ts))
    _bak = _dest.with_name(_dest.name + ".old")
    _bak.mkdir(exist_ok=True)
    _bad = _dest.with_name(_dest.name + ".bad-123")
    _bad.mkdir(exist_ok=True)
    # 完整暂存要留住（那是「下次点更新不用重下」的凭据），半成品才清
    _staged2.mkdir(parents=True, exist_ok=True)
    (_staged2 / "half.dll").write_text("x", encoding="utf-8")
    updater.cleanup_leftovers()
    assert not _stale.exists(), "旧脚本的 staging 残留未清理"
    assert not _bak.exists() and not _bad.exists(), ".old/.bad 备份未清理"
    assert not _staged2.exists(), "半成品暂存目录未清理"
    print("8i. 启动残留清理 OK")

    # ---------- 8j. 缓存只保留最近的版本 ----------
    _keep = updater.cached_archive("v9.9.20")
    _make_zip(_keep, "v9.9.20")
    _drop = updater.cached_archive("v9.9.19")
    _make_zip(_drop, "v9.9.19")
    _other = _drop.parent
    _old_ts = _time.time() - 7200
    os.utime(_other, (_old_ts, _old_ts))
    updater._prune_cache()
    assert not _other.exists(), "旧版本缓存未清理"
    assert _keep.parent.exists(), "最新版本的缓存不该被清掉"
    print("8j. 缓存裁剪 OK")
finally:
    os.rename = _real_rename
    updater.install_dir = _orig_install
    shutil.rmtree(_root, ignore_errors=True)
    for _p in (_root,):
        shutil.rmtree(_p, ignore_errors=True)

# 写权限探测：可写目录返回 True，不存在/不可写的目录返回 False
_probe_dir = Path(_tempfile.mkdtemp(prefix="miperm-"))
try:
    assert updater.dir_writable(_probe_dir) is True
    assert updater.dir_writable(_probe_dir / "nope" / "deeper") is False
    # 探测不该留下文件
    assert not list(_probe_dir.glob(".mihome-write-probe"))
finally:
    shutil.rmtree(_probe_dir, ignore_errors=True)
print("8k. 写权限探测 OK")

# ---------- 9. 主界面手动入口 + 失败标记 ----------
from app.ui.main_window import MainWindow

win = MainWindow()
actions = [a.text() for a in win._more_menu.actions()]
assert "检测更新" in actions, f"「更多」菜单缺少检测更新: {actions}"
# 固定在「关于」之上，与其它低频项同组
assert actions.index("检测更新") < actions.index("关于"), actions

# 手动入口不受「启动时自动检查更新」开关约束
from app.core import settings_store

import app.ui.update_flow as flow

settings_store.set_check_update_enabled(False)
called = {}
_orig_check = flow.check_update
flow.check_update = lambda parent, manual: called.update(
    parent=parent, manual=manual)
try:
    win._check_update_manually()
finally:
    flow.check_update = _orig_check
    settings_store.set_check_update_enabled(True)
assert called.get("manual") is True, f"手动入口应传 manual=True: {called}"
assert called.get("parent") is win

# 更新失败标记：提示一次即清除，第二次静默
flag = updater.failure_flag_path()
flag.write_text("x", encoding="utf-8")
flow.notify_pending_update_failure(win)
assert not flag.exists(), "失败标记应在提示后清除"
flow.notify_pending_update_failure(win)  # 无标记时应不抛异常
print("9. 手动入口与失败标记 OK")

# ---------- 10. 上游基线：关于页与发布说明都要写清 ----------
from app import UPSTREAM_COMMIT, UPSTREAM_REPO, UPSTREAM_VERSION
from app.ui.about_dialog import AboutDialog

assert UPSTREAM_VERSION and UPSTREAM_REPO and UPSTREAM_COMMIT
about = AboutDialog()
about.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
about.show()
app.processEvents()
# 只写 fork 自己的版本号，用户无法判断某条行为来自上游还是这里的改动
_up = about._upstream_label.text()
assert "上游基线" in _up and f"v{UPSTREAM_VERSION}" in _up, _up
assert UPSTREAM_REPO in _up, _up
assert UPSTREAM_COMMIT in about._version_label.toolTip(), \
    about._version_label.toolTip()
# 版本行只放 fork 自己的版本与依赖版本，避免过长被右边界裁掉
assert __version__ in about._version_label.text()
assert "上游基线" not in about._version_label.text()
about.done(0)

# 发布说明必须同步上游版本：这是 issue「发布的说明里看不到上游版本」的回归
_notes = Path(__file__).resolve().parents[1] / ".github" / "release-notes" / \
    f"v{__version__}.md"
assert _notes.exists(), f"缺少当前版本的发布说明：{_notes.name}"
assert "上游基线" in _notes.read_text(encoding="utf-8"), \
    f"{_notes.name} 未写明上游基线版本"
print("10. 上游基线（关于页 + 发布说明）OK")

dlg.done(0)
dlg2.done(0)
app.processEvents()
print("UPDATE REGRESSION ALL PASS")
