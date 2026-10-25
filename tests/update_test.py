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
    flag=updater.FAILURE_FLAG)
# cmd 代码页随系统而变，脚本必须能编成纯 ASCII，否则中文乱码会被当命令执行
script.encode("ascii")
for token in ("tasklist", "robocopy", "start \"\"", "rd /s /q", "del \"%~f0\""):
    assert token in script, f"脚本缺少关键步骤: {token}"
assert r'"%DST%\%EXE%"' in script, "启动路径拼接错误"
# 覆盖失败要留标记，否则「更新失败」在用户眼里等于「更新成功」
assert updater.FAILURE_FLAG in script, "缺少失败标记写入"
assert updater.failure_flag_path().name == updater.FAILURE_FLAG
print("8. 更新脚本模板 OK")

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
