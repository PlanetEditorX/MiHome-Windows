# SPDX-License-Identifier: GPL-3.0-or-later
"""设置页分类（tab）回归测试（离屏运行）。

锁屏联动从「应用功能」页拆出单独成页，这里锁死拆分后的结构：
1. 三个分类 tab（主题界面 / 应用功能 / 锁屏联动）；
2. 锁屏的 8 张卡片归属锁屏页，应用功能页不再含它们；
3. 切页只显示当前页（多页叠放不能漏 hide，否则会出现重叠鬼影）；
4. 三页内容高度都在一屏内（拆分的目的就是这个，退化要能发现）；
5. 跨页保存路径不变：改锁屏配置后保存仍写回 settings.json；
6. 换肤后三页都在，且当前页仍是锁屏页（不能把 tab 状态打回第一页）。
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

LOCK_ITEMS = ("_lock_item", "_lock_device_item", "_lock_unlock_item",
              "_lock_lock_item", "_lock_time_item", "_lock_span_item",
              "_lock_net_item", "_lock_netname_item")
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

dlg = SettingsDialog(parent, [])
dlg.show()
for _ in range(5):
    app.processEvents()

# ---------- 1. 三个分类 tab ----------
assert len(_TAB_TITLES) == 3, _TAB_TITLES
assert [b.text() for b in dlg._tab_buttons] == list(_TAB_TITLES), \
    f"tab 文案不符: {[b.text() for b in dlg._tab_buttons]}"
assert list(_TAB_TITLES) == ["主题界面", "应用功能", "锁屏联动"]
print("1. 三个分类 tab OK")

# ---------- 2. 卡片归属：锁屏 8 项在锁屏页，应用功能页不含 ----------
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
assert len(lock_cards) == 8, f"锁屏页卡片数 {len(lock_cards)}"
assert len(feature_cards) == 7, f"应用功能页卡片数 {len(feature_cards)}"
# 控件本身必须真的挂在锁屏滚动区下（换页不会把它留在旧页里）
assert lock_page in ancestor_chain(dlg._lock_time_start), "时间字段未挂在锁屏页"
print("2. 卡片归属（锁屏 8 / 应用功能 7）OK")

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
for name, scroll in (("主题界面", dlg._appearance_scroll),
                     ("应用功能", features_page),
                     ("锁屏联动", lock_page)):
    hint = scroll.widget().sizeHint().height()
    vp = scroll.viewport().height()
    assert hint <= vp, f"{name} 页仍需滚动：内容 {hint} > 可视 {vp}"
print("4. 三页内容均不超过一屏 OK")

# ---------- 5. 跨页保存：改锁屏配置后仍写回 ----------
settings_store.set_lock_automation({
    "enabled": True, "did": "lamp1", "time_enabled": True,
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

print("\n设置页分类测试全部通过")
