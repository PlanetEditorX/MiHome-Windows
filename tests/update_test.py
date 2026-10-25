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

# ---------- 8. 更新脚本模板 ----------
script = updater._SCRIPT_TEMPLATE.format(
    src=r"D:\tmp\staging", dst=r"C:\app", exe=updater.EXE_NAME,
    flag=updater.FAILURE_FLAG, log=updater.UPDATE_LOG)
# cmd 代码页随系统而变，脚本必须能编成纯 ASCII，否则中文乱码会被当命令执行
script.encode("ascii")
for token in ("tasklist", "robocopy", "start \"\"", "rd /s /q", "del \"%~f0\""):
    assert token in script, f"脚本缺少关键步骤: {token}"
assert r'"%DST%\%EXE%"' in script, "启动路径拼接错误"
# 覆盖失败要留标记，否则「更新失败」在用户眼里等于「更新成功」
assert updater.FAILURE_FLAG in script, "缺少失败标记写入"
assert updater.failure_flag_path().name == updater.FAILURE_FLAG

# 关键：先把新版本整包复制到安装目录旁，再整目录 rename 交换。
# 逐文件覆盖会在「有文件被占用」时留下新 dll + 旧 exe 的混搭，
# 那个组合启动即崩、且版本号仍是旧的，用户要更新两三次才好。
assert r'"%SRC%" "%NEW%"' in script, "必须先复制到 .new 再交换"
assert r'"%SRC%" "%DST%"' not in script, "不允许再直接覆盖安装目录"
assert r'move "%DST%" "%OLD%"' in script, "缺少安装目录改名"
assert r'move "%NEW%" "%DST%"' in script, "缺少新版本顶上"
assert r'move "%OLD%" "%DST%"' in script, "缺少失败回滚"
# 交换后要确认新版没在启动瞬间死掉，否则用户面对的是「点了没反应」
assert "rolling back" in script, "缺少启动存活检查"
# 诊断日志：更新失败时唯一能拿到现场的东西
assert r'>> "%LOG%"' in script, "缺少日志输出"
assert updater.UPDATE_LOG in script
# 启动时带上工作目录：cmd 的 CWD 是 %TEMP%，不指定会让程序以临时目录为起点
assert r'start "" /D "%DST%"' in script, "启动未指定工作目录"
print("8. 更新脚本模板 OK")

# ---------- 8b. 真实跑一遍替换脚本：占用时不能混搭，正常时一次成功 ----------
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile as _tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

_probe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
if _probe.exists():
    _root = Path(_tempfile.mkdtemp(prefix="miupdate-"))
    _dst = _root / "App"
    _src = _root / "staged"
    _exe = "probe.exe"
    _flag = _root / "failed.txt"
    _log = _root / "update.log"

    def _fill(folder: Path, tag: str) -> None:
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        shutil.copy2(_probe, folder / _exe)
        for i in range(4):
            (folder / f"part{i}.dll").write_text(tag, encoding="utf-8")

    def _run_script(skip_launch: bool = True) -> None:
        _fill(_src, "new")
        text = updater._SCRIPT_TEMPLATE.format(
            src=str(_src), dst=str(_dst), exe=_exe,
            flag=_flag.name, log=_log.name)
        # 缩短重试：正常路径不该在测试里等两分钟
        text = text.replace("lss 90", "lss 3")
        text = text.replace('set "FLAG=%TEMP%\\', f'set "FLAG={_root}\\').replace(
            'set "LOG=%TEMP%\\', f'set "LOG={_root}\\')
        if skip_launch:
            # 探针程序启动即退出，会误触发「启动即崩」回滚，跳过第 3 段
            import re as _re
            text = _re.sub(r"rem ---- 3\. start the new build.*?rem ---- 4\.",
                           "rem ---- 4.", text, flags=_re.S)
        _script = _root / "swap.cmd"
        _script.write_text(text, encoding="ascii")
        subprocess.run(["cmd.exe", "/c", str(_script)], capture_output=True,
                       timeout=120)

    def _part0() -> str:
        p = _dst / "part0.dll"
        return p.read_text(encoding="utf-8") if p.exists() else "<gone>"

    # 8b-1 安装目录被占用：必须整体放弃，目录保持完整旧版（不能混搭）
    _fill(_dst, "old")
    _holder = open(_dst / _exe, "rb")
    try:
        _run_script()
    finally:
        _holder.close()
    assert _part0() == "old", "占用时旧版必须原样保留，不能出现新 dll"
    assert _flag.exists(), "放弃替换必须留失败标记"
    assert _dst.is_dir(), "不能把安装目录弄丢"
    print("8b-1. 目录被占用 → 保持完整旧版 + 失败标记 OK")

    # 8b-2 无人占用：一次替换成功，且不留备份目录
    _flag.unlink(missing_ok=True)
    _run_script()
    assert _part0() == "new", "空闲时应一次替换成功"
    assert not _flag.exists(), "成功时不该留失败标记"
    assert not (_root / "App.new").exists(), ".new 未清理"
    assert not _src.exists(), "staging 未清理"
    print("8b-2. 空闲时一次替换成功 OK")

    # 8b-3 上次残留的 .new / .old：开跑前必须清掉，否则交换会撞名
    _fill(_dst, "old")
    shutil.rmtree(_root / "App.old", ignore_errors=True)
    (_root / "App.new").mkdir()
    (_root / "App.new" / "junk.txt").write_text("x", encoding="utf-8")
    _flag.unlink(missing_ok=True)
    _run_script()
    assert _part0() == "new", "残留目录没清干净，交换失败"
    assert not (_root / "App.new").exists(), "残留 .new 未清理"
    print("8b-3. 残留 .new/.old 清理 OK")

    # 8b-4 新版启动即退出：必须回滚到旧版并留标记（否则用户面对空窗）
    _fill(_dst, "old")
    _flag.unlink(missing_ok=True)
    _run_script(skip_launch=False)
    assert _part0() == "old", "新版启动失败时必须回滚"
    assert _flag.exists(), "启动失败应留标记"
    assert _dst.is_dir()
    print("8b-4. 新版启动即崩 → 自动回滚旧版 OK")

    shutil.rmtree(_root, ignore_errors=True)

# 启动残留清理：staging 与 .old/.new 都不该永久堆积
import time as _time  # noqa: E402

_stage = Path(_tempfile.mkdtemp(prefix="mihome-update-"))
(_stage / "leftover.bin").write_text("x", encoding="utf-8")
# 5 分钟内的 staging 视为「可能正被本次更新使用」，清理函数会跳过它，
# 这里把时间戳拨旧，模拟上一次更新遗留
_old_ts = _time.time() - 3600
os.utime(_stage, (_old_ts, _old_ts))
_orig = updater.install_dir
_fake_dst = Path(_tempfile.mkdtemp(prefix="miold-")) / "MiHome-Windows"
_fake_dst.mkdir()
(_fake_dst.with_name(_fake_dst.name + ".old")).mkdir()
updater.install_dir = lambda: _fake_dst
try:
    updater.cleanup_leftovers()
finally:
    updater.install_dir = _orig
assert not _stage.exists(), "staging 残留未清理"
assert not _fake_dst.with_name(_fake_dst.name + ".old").exists(), ".old 未清理"
print("8c. 启动残留清理 OK")

# 写权限探测：可写目录返回 True，不存在/不可写的目录返回 False
assert updater.dir_writable(_fake_dst) is True
assert updater.dir_writable(_fake_dst / "nope" / "deeper") is False
# 探测不该留下文件
assert not list(_fake_dst.glob(".mihome-write-probe"))
print("8d. 写权限探测 OK")

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

dlg.done(0)
dlg2.done(0)
app.processEvents()
print("UPDATE REGRESSION ALL PASS")
