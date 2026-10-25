# SPDX-License-Identifier: GPL-3.0-or-later
"""「隐藏离线设备」开关回归测试（离屏运行）。

覆盖：设置默认值与往返存取、主页网格/计数/托盘的展示过滤、房间空态
文案、耗材统计不被隐藏影响、设置页开关落盘与取消、切主题后开关仍在。
"""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtWidgets import QApplication, QDialog

app = QApplication([])

# 测试禁写运行数据：假设备会经指标回写等路径泄漏进 devices_cache.json
from app.core import cache as _device_cache

_device_cache.save = lambda *a, **k: None

from app.core import board_store, settings_store, tray_store
from app.core.models import DeviceInfo
from app.ui import si_theme
from app.ui.theme_service import apply_theme

apply_theme("dark")
tray_store.save([])
board_store.set_enabled(False)


def dev(did: str, name: str, room: str, online: bool) -> DeviceInfo:
    return DeviceInfo(did=did, name=name, model="a.b.c",
                      home_name="家", room_name=room, online=online)


DEVICES = [
    dev("1", "客厅灯", "客厅", True),
    dev("2", "客厅插座", "客厅", False),
    dev("3", "卧室门锁", "卧室", True),
    dev("4", "卧室台灯", "卧室", False),
    dev("5", "厨房插座", "厨房", False),   # 该房间只有离线设备
]

# ---------- 1. 设置项：默认关闭 + 往返存取 ----------
assert settings_store.get_hide_offline_devices() is False, "新开关默认应为关闭"
settings_store.set_hide_offline_devices(True)
assert settings_store.get_hide_offline_devices() is True
# 脏值（手改配置文件写成字符串）按真值语义收敛，不抛异常
settings_store.set_hide_offline_devices("")
assert settings_store.get_hide_offline_devices() is False
print("1. 默认关闭 + 往返存取 OK")

# ---------- 2. 关闭状态：全部设备照常展示 ----------
from app.ui.main_window import MainWindow

win = MainWindow()
win._apply_devices(list(DEVICES))
win.show()
app.processEvents()

assert win._hide_offline is False
assert len(win._visible_devices()) == 5, len(win._visible_devices())
assert len(win._displayed_devices()) == 5
assert len(win._cards) == 5
assert "共 5 台设备" in win._count_label.text(), win._count_label.text()
print("2. 关闭时 5 台全展示 OK")

# ---------- 3. 开启后热更新：网格 / 计数 / 托盘口径 ----------
settings_store.set_hide_offline_devices(True)
win._apply_hide_offline_setting()
app.processEvents()

assert win._hide_offline is True
assert sorted(d.did for d in win._visible_devices()) == ["1", "3"]
assert sorted(win._cards) == ["1", "3"], sorted(win._cards)
assert "共 2 台设备" in win._count_label.text()
assert "2 台在线" in win._count_label.text()
# 托盘快捷面板与主页同口径（_displayed_devices 是其数据源）
assert sorted(d.did for d in win._displayed_devices()) == ["1", "3"]
# 聚合口径必须仍看到全部设备，否则耗材不足会静默消失
assert len(win._visible_subset(include_hidden=True)) == 5
assert len(win._visible_subset()) == 2
print("3. 开启后网格/计数/托盘只剩在线设备，聚合口径不变 OK")

# ---------- 4. 房间 tab 不因隐藏而跳变；房间内全离线时给空态文案 ----------
assert "厨房" in win._tab_buttons, "房间 tab 应按全量设备保留，不随离线隐藏消失"
win._current_room = "厨房"
win._rebuild_grid()
app.processEvents()
assert not win._cards, "厨房只有离线设备，开启隐藏后应无卡片"
assert "没有在线设备" in win._empty_grid_hint(), win._empty_grid_hint()
# 网格里确实挂出了提示标签（不是白屏）
from PySide6.QtWidgets import QLabel

labels = [w.text() for w in win._grid.parentWidget().findChildren(QLabel)
          if "当前范围内" in w.text()]
assert labels, "空网格未插入提示标签"
# 关闭开关后空态提示应退回「暂无设备」语义之外的真实文案
settings_store.set_hide_offline_devices(False)
win._apply_hide_offline_setting()
app.processEvents()
assert len(win._cards) == 1 and "5" in win._cards, "关闭开关后离线设备应立刻回来"
print("4. 空房间提示 + 关闭即时恢复 OK")

# ---------- 5. 设置页：开关回显 / 落盘 / 取消不落盘 ----------
# 保存流程会写 HKCU Run：测试绝不改动真实注册表
settings_store.set_autostart = lambda value: None

from app.ui.settings_dialog import SettingsDialog

dlg = SettingsDialog(win, devices=list(DEVICES))
assert dlg._offline_toggle is not None
assert dlg._offline_toggle.isChecked() is False, "设置页未回显 store 中的关闭状态"
assert "离线" in dlg._offline_label.text()
# 切主题色后开关仍在且样式刷新不抛异常（新行必须进 _apply_styles 的三个元组）
dlg.retheme()
app.processEvents()
assert dlg._offline_toggle.isVisible() or dlg._offline_toggle.parent() is not None

dlg._offline_toggle.setChecked(True)
dlg._save_and_accept()
# 遮罩对话框的 accept 走 100ms 淡出动画后才真正 done，等它落地
from PySide6.QtTest import QTest

QTest.qWait(300)
assert dlg.result() == QDialog.DialogCode.Accepted, dlg.result()
assert settings_store.get_hide_offline_devices() is True, "保存后未落盘"
print("5. 设置页回显 / 保存落盘 OK")

# 再次打开 → 取消：不应写回
settings_store.set_hide_offline_devices(False)
dlg2 = SettingsDialog(win, devices=list(DEVICES))
assert dlg2._offline_toggle.isChecked() is False
dlg2._offline_toggle.setChecked(True)
dlg2.done(QDialog.DialogCode.Rejected)
assert settings_store.get_hide_offline_devices() is False, "取消不应落盘"
dlg.deleteLater()
dlg2.deleteLater()
print("5b. 取消不落盘 OK")

# ---------- 6. 重启读取：启动即按设置生效 ----------
settings_store.set_hide_offline_devices(True)
win._all_devices = []
win.close()
app.processEvents()

win2 = MainWindow()
assert win2._hide_offline is True, "启动时未读取设置"
win2._apply_devices(list(DEVICES))
win2.show()
app.processEvents()
assert sorted(win2._cards) == ["1", "3"], sorted(win2._cards)
win2._all_devices = []
win2.close()
app.processEvents()
print("6. 启动读取设置即生效 OK")

# 清理：把主题色/缩放等临时改动留在隔离目录，进程退出由 _isolate 删除
assert si_theme.current_theme() in ("dark", "light")
print("[OK] 隐藏离线设备开关全部通过")
