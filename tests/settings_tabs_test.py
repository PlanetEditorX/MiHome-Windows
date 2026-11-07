# SPDX-License-Identifier: GPL-3.0-or-later
"""设置页分类（tab）回归测试（离屏运行）。

锁屏联动从「应用功能」页拆出单独成页，这里锁死拆分后的结构：
1. 三个分类 tab（主题界面 / 应用功能 / 锁屏联动）；
2. 锁屏的 6 张卡片（含动态联动目标卡片）归属锁屏页；
3. 切页只显示当前页（多页叠放不能漏 hide，否则会出现重叠鬼影）；
4. 三页内容高度都在一屏内（拆分的目的就是这个，退化要能发现）；
5. 跨页保存路径不变：改锁屏配置后保存仍写回 settings.json；
6. 换肤后三页都在，且当前页仍是锁屏页（不能把 tab 状态打回第一页）；
7. 联动目标行：多目标/混合类型/独立动作/增删/保存；
8. 自动化列表未加载时，已配置目标保存后不丢失。
"""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from tests._isolate import isolate

isolate()

from PySide6.QtWidgets import QApplication, QDialog, QWidget

app = QApplication([])

from app.core import settings_store  # noqa: E402
from app.ui.settings_dialog import (  # noqa: E402
    SettingsDialog, _TAB_FEATURES, _TAB_LOCK, _TAB_TITLES,
)
from app.ui.theme_service import apply_theme  # noqa: E402

# 保存流程会写 HKCU Run：测试绝不改动真实注册表
settings_store.set_autostart = lambda value: None
apply_theme("dark")

LOCK_ITEMS = ("_lock_item", "_lock_targets_card", "_lock_time_item",
              "_lock_span_item", "_lock_net_item", "_lock_netname_item")
FEATURE_ITEMS = ("_autostart_item", "_speaker_item", "_tray_item",
                 "_start_min_item", "_board_item", "_hide_item",
                 "_update_item")


def page_items(scroll) -> list:
    """滚动区内容布局里的卡片（最后一项是 addStretch，不是 widget）。"""
    body = scroll.widget().layout()
    return [body.itemAt(i).widget() for i in range(body.count())
            if body.itemAt(i).widget() is not None]


def ancestor_chain(widget) -> list:
    chain, cur = [], widget
    while cur is not None:
        chain.append(cur)
        cur = cur.parent()
    return chain


# 模拟用户 200% 屏下的主窗口尺寸（1130×752 逻辑像素）
parent = QWidget()
parent.resize(1130, 752)

# 真实场景必须带设备：设备下拉按最长选项自适应到 300px 宽，会挤压
# 左侧描述区；用空设备列表测出来的「一屏」是假象（下拉只有 150px）
from app.core.models import DeviceInfo  # noqa: E402

DEMO_DEVICES = [
    DeviceInfo(f"d{i}", name, "model", "家", room, True)
    for i, (name, room) in enumerate((
        ("Yeelight 显示器挂灯", "书房"),
        ("米家台灯 Pro", "卧室"),
        ("客厅主灯", "客厅"),
        ("空调伴侣", "卧室"),
        ("扫地机器人", "客厅"),
        ("空气净化器", "书房"),
        ("智能插座", "厨房"),
        ("温湿度计", "阳台"),
        ("窗帘电机", "卧室"),
        ("小爱音箱 Pro", "客厅"),
    ))
]

dlg = SettingsDialog(parent, DEMO_DEVICES)
dlg.show()
for _ in range(5):
    app.processEvents()

# ---------- 1. 三个分类 tab ----------
assert len(_TAB_TITLES) == 3, _TAB_TITLES
assert [b.text() for b in dlg._tab_buttons] == list(_TAB_TITLES), \
    f"tab 文案不符: {[b.text() for b in dlg._tab_buttons]}"
assert list(_TAB_TITLES) == ["主题界面", "应用功能", "锁屏联动"]
print("1. 三个分类 tab OK")

# ---------- 2. 卡片归属：锁屏 9 项在锁屏页，应用功能页不含 ----------
lock_page = dlg._lock_scroll
features_page = dlg._features_scroll
lock_cards = page_items(lock_page)
feature_cards = page_items(features_page)
for name in LOCK_ITEMS:
    card = getattr(dlg, name)
    assert card in lock_cards, f"{name} 不在锁屏页"
    assert card not in feature_cards, f"{name} 仍留在应用功能页"
for name in FEATURE_ITEMS:
    card = getattr(dlg, name)
    assert card in feature_cards, f"{name} 不在应用功能页"
    assert card not in lock_cards, f"{name} 误入锁屏页"
assert len(lock_cards) == 6, f"锁屏页卡片数 {len(lock_cards)}"
assert len(feature_cards) == 8, f"应用功能页卡片数 {len(feature_cards)}"
# 控件本身必须真的挂在锁屏滚动区下（换页不会把它留在旧页里）
assert lock_page in ancestor_chain(dlg._lock_time_start), "时间字段未挂在锁屏页"
print("2. 卡片归属（锁屏 6 / 应用功能 8）OK")

# ---------- 3. 切页可见性：只有当前页可见 ----------
pages = (dlg._appearance_scroll, features_page, lock_page)
for index in (0, 1, 2):
    dlg._switch_tab(index)
    for _ in range(3):
        app.processEvents()
    vis = [p.isVisible() for p in pages]
    expect = [i == index for i in range(3)]
    assert vis == expect, f"切到 tab {index} 后可见性异常: {vis}"
assert dlg._current_tab == _TAB_LOCK
print("3. 切页可见性（一次只显示一页）OK")

# ---------- 4. 三页都在一屏内（拆分的目的） ----------
# 判据用滚动条的滚动范围而不是 sizeHint：QLabel 开了 wordWrap 后
# sizeHint 会高估（实测 615 vs 真实 543），据此断言会逼着把 UI 压得过扁；
# 滚动范围 >0 才是「真的需要滚」。
for index, (name, scroll) in enumerate((("主题界面", dlg._appearance_scroll),
                                        ("应用功能", features_page),
                                        ("锁屏联动", lock_page))):
    dlg._switch_tab(index)
    for _ in range(4):
        app.processEvents()
    overflow = scroll.verticalScrollBar().maximum()
    assert overflow == 0, f"{name} 页仍需滚动：溢出 {overflow}px"
print("4. 三页内容均不超过一屏 OK")

# ---------- 5. 跨页保存：改锁屏配置后仍写回 ----------
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [{"kind": "device", "id": "d0", "name": "Yeelight 显示器挂灯",
                 "unlock": "on", "lock": "off"}],
    "time_enabled": True,
    "time_start": "08:00", "time_end": "23:00"})
dlg2 = SettingsDialog(parent, [])
dlg2._switch_tab(_TAB_LOCK)
app.processEvents()
dlg2._lock_time_start.setTime(dlg2._lock_time_start.time().addSecs(300))
dlg2._lock_net_edit.setText("Home-WiFi")
dlg2._save_and_accept()

from PySide6.QtTest import QTest  # noqa: E402

QTest.qWait(300)
assert dlg2.result() == QDialog.DialogCode.Accepted, dlg2.result()
cfg = settings_store.get_lock_automation()
assert cfg["time_start"] == "08:05", cfg["time_start"]
assert cfg["net_names"] == ["Home-WiFi"], cfg["net_names"]
print("5. 跨页保存写回 OK")

# ---------- 6. 换肤后 tab 状态保持 ----------
dlg3 = SettingsDialog(parent, [])
dlg3._switch_tab(_TAB_LOCK)
app.processEvents()
dlg3.retheme()
for _ in range(3):
    app.processEvents()
assert dlg3._current_tab == _TAB_LOCK, "换肤把当前 tab 打回第一页"
assert len(dlg3._tab_buttons) == 3
# 未 show 的窗口里 isVisible() 恒为 False，用相对可见性判断页是否被切走
assert dlg3._lock_scroll.isVisibleTo(dlg3), "换肤后锁屏页被切走"
assert not dlg3._features_scroll.isVisibleTo(dlg3)

dlg.deleteLater()
dlg2.deleteLater()
dlg3.deleteLater()
print("6. 换肤后 tab 状态保持 OK")

# ---------- 7. 联动目标行：多目标、混合类型、独立动作、增删与保存 ----------
from app.core.models import AutomationInfo  # noqa: E402
from app.ui.settings_dialog import LockTargetRow  # noqa: E402

AUTOMATIONS = [
    AutomationInfo("sc1", "无人关灯", "home1", "家", True, "挂灯关"),
    AutomationInfo("sc2", "夜间回家开灯", "home1", "家", False, "开灯"),
]
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [
        {"kind": "device", "id": "d0", "name": "Yeelight 显示器挂灯",
         "startup": "none", "unlock": "on", "lock": "off"},
        {"kind": "automation", "id": "sc1", "name": "无人关灯",
         "home_id": "home1", "startup": "none", "unlock": "off", "lock": "on"},
    ],
})
dlg4 = SettingsDialog(parent, DEMO_DEVICES, AUTOMATIONS)
dlg4._switch_tab(_TAB_LOCK)
app.processEvents()
assert len(dlg4._lock_rows) == 2, "已配置的目标未生成两行"

# 设备行动作是「打开/关闭」，自动化行是「启用/停用」，且各自状态独立
dev_row, auto_row = dlg4._lock_rows
assert dev_row._actions["unlock"].currentText() == "打开"
assert auto_row._actions["unlock"].currentText() == "停用"
assert auto_row._actions["lock"].currentText() == "启用"
assert dev_row.value()["home_id"] == ""
assert auto_row.value()["home_id"] == "home1", \
    "自动化的 home_id 丢了，云端拨开关会失败"
assert auto_row.value()["unlock"] == "off" and dev_row.value()["unlock"] == "on"

def _select_label(row, label: str) -> None:
    """按可见文案选目标（下拉按名称排序，索引不等于设备顺序）。"""
    index = row._combo.findText(label)
    assert index >= 0, f"下拉里找不到「{label}」"
    row._combo.setCurrentIndex(index)


# 已选目标必须从其它行下拉里消失，自己那行仍保留
assert dev_row._combo.findText("自动化·无人关灯") == -1, \
    "第二行已选的自动化仍出现在第一行下拉里"
assert auto_row._combo.findText("Yeelight 显示器挂灯（书房）") == -1, \
    "第一行已选的设备仍出现在第二行下拉里"
assert auto_row._combo.findText("自动化·无人关灯") >= 0, \
    "本行自己选中的目标不该被过滤掉"
assert dev_row._combo.findText("自动化·夜间回家开灯") >= 0

# 换目标类型时动作下拉文案跟着换、值不丢；同时验证过滤是实时的
_select_label(dev_row, "自动化·夜间回家开灯")
assert dev_row._actions["unlock"].currentText() == "启用", \
    "设备换成自动化后动作文案没跟着变"
assert dev_row._actions["unlock"].currentIndex() == 0, \
    "换目标类型把已选动作打乱了"
assert auto_row._combo.findText("自动化·夜间回家开灯") == -1, \
    "第一行改选后，新目标没有从第二行下拉里过滤掉"
assert auto_row._combo.findText("Yeelight 显示器挂灯（书房）") >= 0, \
    "第一行释放的旧设备没有回到第二行下拉里"
_select_label(dev_row, "Yeelight 显示器挂灯（书房）")  # 换回台灯
assert auto_row._combo.findText("自动化·夜间回家开灯") >= 0

# 新增一行、选一个目标、设置三个方向
dlg4._add_lock_row()
assert len(dlg4._lock_rows) == 3
new_row = dlg4._lock_rows[-1]
assert new_row.value() is None, "未选目标的空行不该写进配置"
_select_label(new_row, "米家台灯 Pro（卧室）")
new_row._actions["startup"].setCurrentIndex(0)   # 打开
new_row._actions["unlock"].setCurrentIndex(2)    # 不操作
new_row._actions["lock"].setCurrentIndex(1)      # 关闭
assert new_row.value()["id"] == "d1"

# 删掉中间那行自动化
dlg4._remove_lock_row(auto_row)
assert len(dlg4._lock_rows) == 2

dlg4._save_and_accept()
cfg = settings_store.get_lock_automation()
assert len(cfg["targets"]) == 2, cfg["targets"]
assert cfg["targets"][0]["id"] == "d0"
assert cfg["targets"][1] == {
    "kind": "device", "id": "d1", "name": "米家台灯 Pro", "home_id": "",
    "startup": "on", "unlock": "none", "lock": "off",
}, cfg["targets"][1]
dlg4.deleteLater()
print("7. 联动目标行（多目标/混合类型/独立动作/增删/保存）OK")

# ---------- 8. 自动化列表未加载时，已配置目标不能被保存抹掉 ----------
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [{"kind": "automation", "id": "scX", "name": "旧自动化",
                 "home_id": "homeX", "unlock": "off", "lock": "on"}],
})
dlg5 = SettingsDialog(parent, DEMO_DEVICES, [])
dlg5._switch_tab(_TAB_LOCK)
app.processEvents()
assert len(dlg5._lock_rows) == 1
assert "未在列表" in dlg5._lock_rows[0]._combo.currentText()
assert dlg5._lock_rows[0].value()["home_id"] == "homeX"
dlg5._save_and_accept()
cfg = settings_store.get_lock_automation()
assert len(cfg["targets"]) == 1 and cfg["targets"][0]["id"] == "scX", \
    f"打开设置点保存把未加载的自动化目标抹掉了: {cfg['targets']}"
dlg5.deleteLater()
print("8. 未加载的目标保存后不丢失 OK")

# ---------- 9. 无黑色原生 tooltip；过滤随增删实时更新 ----------
from PySide6.QtCore import Qt  # noqa: E402

settings_store.set_lock_automation({
    "enabled": True,
    "targets": [
        {"kind": "device", "id": "d0", "name": "Yeelight 显示器挂灯",
         "unlock": "on", "lock": "off"},
        {"kind": "automation", "id": "sc1", "name": "无人关灯",
         "home_id": "home1", "unlock": "off", "lock": "on"},
    ],
})
dlg6 = SettingsDialog(parent, DEMO_DEVICES, AUTOMATIONS)
dlg6._switch_tab(_TAB_LOCK)
app.processEvents()
for row in dlg6._lock_rows:
    assert row._combo.toolTip() == "", "目标下拉不该挂原生 tooltip"
    for i in range(row._combo.count()):
        assert row._combo.itemData(i, Qt.ItemDataRole.ToolTipRole) is None, \
            "下拉项挂了原生 tooltip，鼠标扫过会弹黑色悬浮窗"
    assert row._combo.view().textElideMode() == Qt.TextElideMode.ElideNone, \
        "弹出列表应显示完整名称，不能靠 tooltip 补"
    assert row._combo.view().minimumWidth() >= 260, \
        "弹出列表太窄，长自动化名会被裁掉"

first, second = dlg6._lock_rows
assert first._combo.findText("自动化·无人关灯") == -1
dlg6._remove_lock_row(second)
app.processEvents()
assert first._combo.findText("自动化·无人关灯") >= 0, \
    "删掉占用行后目标没有回到下拉里"

# 新增行也遵守过滤，且过滤重建不能弄丢选中项与三个动作值
dlg6._add_lock_row()
third = dlg6._lock_rows[-1]
assert third._combo.findText("Yeelight 显示器挂灯（书房）") == -1
_select_label(third, "自动化·无人关灯")
assert first._combo.findText("自动化·无人关灯") == -1
assert third.value()["id"] == "sc1" and third.value()["home_id"] == "home1"
third._actions["unlock"].setCurrentIndex(1)  # 停用
dlg6._remove_lock_row(first)
app.processEvents()
assert third.value()["id"] == "sc1", "过滤重建把选中目标弄丢了"
assert third.value()["unlock"] == "off", "过滤重建把动作值弄丢了"
dlg6.deleteLater()
print("9. 无黑色 tooltip + 目标过滤随增删实时更新 OK")

print("\n设置页分类测试全部通过")
