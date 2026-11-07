# SPDX-License-Identifier: GPL-3.0-or-later
"""「自动化」分页回归测试（离屏运行）。

覆盖：动作摘要解析、服务层列表/启停接口的请求形状、主页选项卡顺序、
全屋视图不含自动化、切页才建卡、拨开关的成功回写与失败回滚。
全程不联网：上游 api 用假对象顶替。
"""
import os
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtWidgets import QApplication

app = QApplication([])

# 测试禁写运行数据：假设备会经指标回写等路径泄漏进 devices_cache.json
from app.core import cache as _device_cache

_device_cache.save = lambda *a, **k: None

from app.core import board_store, tray_store
from app.core.models import AutomationInfo, DeviceInfo
from app.core.service import MijiaService, _automation_action_text
from app.ui.main_window import _ALL_ROOMS, _AUTO_TAB, _SCENE_TAB, MainWindow
from app.ui.theme_service import apply_theme

apply_theme("light")
tray_store.save([])
board_store.set_enabled(False)


def actions(*items) -> dict:
    return {"scene_action": {"actions": list(items)}}


def switch_action(device: str, on: bool) -> dict:
    return {"name": "", "payload_json": {
        "command": "set_properties", "device_name": device,
        "value": [{"siid": 2, "piid": 1, "value": on}]}}


def pump(rounds: int = 200, gap: float = 0.01) -> None:
    for _ in range(rounds):
        app.processEvents()
        time.sleep(gap)


# ---------- 1. 动作摘要解析 ----------
assert _automation_action_text(
    actions(switch_action("显示器挂灯", False))) == "关闭显示器挂灯"
assert _automation_action_text(
    actions({"name": "", "payload_json": {
        "command": "set_properties", "device_name": "挂灯",
        "value": [{"siid": 3, "piid": 6, "value": True}]}})) == "挂灯背灯开"
assert _automation_action_text(
    actions({"name": "", "payload_json": {
        "command": "send_keys", "device_name": "空调"}})) == "遥控空调"
# 有 name 时保留米家原文语序
assert _automation_action_text(actions({
    "name": "氛围灯开",
    "payload_json": {"command": "set_properties", "device_name": "挂灯",
                     "value": []}})) == "挂灯氛围灯开"
# 多个动作只报第一个 + 总数
assert _automation_action_text(actions(
    switch_action("A", True), switch_action("B", True))) == "打开 A 等 2 个动作"
# 解析不出来时留空，界面退化成「自动化」
assert _automation_action_text({}) == ""
assert _automation_action_text({"scene_action": {"actions": []}}) == ""
print("1. 动作摘要解析 OK")


# ---------- 2. 服务层：列表与启停请求形状 ----------
class FakeAPI:
    """只实现 service 用到的两件事：取家庭 owner 与打请求。"""

    def __init__(self, auto_list, set_result=True):
        self.auto_list = auto_list
        self.set_result = set_result
        self.calls: list[tuple[str, dict]] = []

    def _get_home_owner(self, home_id):  # noqa: D401
        return 1433283937

    def _request(self, uri, data):
        self.calls.append((uri, data))
        if uri.endswith("GetSimpleSceneList"):
            return {"manual_scene_info_list": [{"scene_id": "s1", "name": "手动"}],
                    "auto_scene_info_list": self.auto_list}
        if uri.endswith("SetSceneStatus"):
            return self.set_result
        return None


AUTO_RAW = {
    "scene_id": "1980990987329232896",
    "name": "客厅传感器无人存在指定时间后1分钟-挂灯关",
    "enable": True,
    "scene_action": {"actions": [switch_action("Yeelight 显示器挂灯", False)]},
}

svc = MijiaService()
svc._api = FakeAPI([AUTO_RAW])
svc._home_ids = {"我的家": "home-1"}

items = svc.automation_list()
assert len(items) == 1, f"只应返回自动化，手动场景不该混入: {items}"
one = items[0]
assert one.scene_id == "1980990987329232896"
assert one.enabled is True
assert one.home_id == "home-1" and one.home_name == "我的家"
assert one.action_text == "关闭 Yeelight 显示器挂灯", one.action_text
uri, payload = svc._api.calls[-1]
assert payload["get_type"] == 2, "自动化必须用 get_type=2"
assert payload["owner_uid"] == 1433283937, "缺 owner_uid 会被服务端拒绝"

# 启停：enable 必须是 JSON 布尔
svc.set_automation_enabled(one, False)
uri, payload = svc._api.calls[-1]
assert uri.endswith("SetSceneStatus"), uri
assert payload["scene_id"] == "1980990987329232896"
assert payload["enable"] is False, f"enable 必须传 bool: {payload['enable']!r}"

# 服务端返回假值视为失败
svc._api.set_result = False
try:
    svc.set_automation_enabled(one, True)
except Exception as exc:  # noqa: BLE001
    assert "启用" in str(exc), exc
else:
    raise AssertionError("服务端返回 False 时应抛错")
print("2. 服务层列表与启停 OK")


# ---------- 3. 选项卡顺序：自动化在房间之后 ----------
DEVICES = [
    DeviceInfo("1", "挂灯", "yeelink.light.lamp15", "我的家", "卧室", True),
    DeviceInfo("2", "扫地机", "d103", "我的家", "客厅", True),
    DeviceInfo("3", "床头灯", "yeelink.light", "我的家", "侧室", True),
]
AUTOMATIONS = [
    AutomationInfo("a1", "21:00 每天-挂灯开", "home-1", "我的家", True, "打开挂灯"),
    AutomationInfo("a2", "01:30 每天-挂灯关", "home-1", "我的家", False, "关闭挂灯"),
]

class StubService:
    """顶替真实服务的桩：列表直接给固定数据，启停记录调用可配失败。"""

    def __init__(self, automations):
        self.automations = automations
        self.calls: list[tuple[str, bool]] = []
        self.fail = False

    def automation_list(self, home_name=None):
        return list(self.automations)

    def set_automation_enabled(self, automation, enable):
        self.calls.append((automation.scene_id, enable))
        if self.fail:
            raise RuntimeError("云端拒绝了")

    # 主窗口在后台还会本地化设备名、轮询开关状态，桩对象缺了会刷出无关告警
    def localized_product_names(self, *args, **kwargs):
        return {}

    def power_states(self, *args, **kwargs):
        return {}


stub = StubService(AUTOMATIONS)
win = MainWindow()
# 必须在 select_room 之前换掉：_load_automations 的 lambda 在任务线程里
# 才解析 self._service，晚换会让已经在跑的任务把新建的卡片列表清空
win._service = stub
win._all_devices = list(DEVICES)
win._current_home = "全部"
win._homes = ["全部"]
win._rebuild_tabs()
win._automations = list(AUTOMATIONS)
win.show()
app.processEvents()

tabs = list(win._tab_buttons)
assert tabs[-1] == _AUTO_TAB, f"自动化必须在最后: {tabs}"
assert tabs.index(_AUTO_TAB) > tabs.index("客厅"), tabs
assert tabs[:2] == [_SCENE_TAB, _ALL_ROOMS], tabs
print("3. 选项卡顺序 OK", tabs)


# ---------- 4. 全屋视图不含自动化 ----------
win._select_room(_ALL_ROOMS)
pump(30, 0.005)
assert not win._automation_cards, "全屋不该出现自动化卡片"
assert sorted(win._cards) == ["1", "2", "3"], sorted(win._cards)
assert "台设备" in win._count_label.text(), win._count_label.text()
print("4. 全屋不含自动化 OK")


# ---------- 5. 切到自动化页才建卡 ----------
win._select_room(_AUTO_TAB)
pump(60, 0.01)
assert sorted(win._automation_cards) == ["a1", "a2"], sorted(win._automation_cards)
assert not win._cards, "自动化页不该残留设备卡片"
assert "自动化" in win._count_label.text(), win._count_label.text()
assert "1 个已启用" in win._count_label.text(), win._count_label.text()
# 已启用的排在前面
assert list(win._automation_cards)[0] == "a1", list(win._automation_cards)
print("5. 切页建卡与排序 OK")


# ---------- 6. 拨开关：成功回写 / 失败回滚 ----------
win._on_automation_toggled("a2", True)
pump()
assert stub.calls == [("a2", True)], stub.calls
assert win._automation_cards["a2"].automation.enabled is True, "模型未回写"
assert "2 个已启用" in win._count_label.text(), win._count_label.text()

stub.fail = True
win._on_automation_toggled("a2", False)
pump()
card = win._automation_cards["a2"]
assert stub.calls[-1] == ("a2", False), stub.calls
assert card.automation.enabled is True, "失败不应改动模型状态"
assert card._switch.isChecked() is True, "失败必须把开关拨回原位"
assert "2 个已启用" in win._count_label.text(), win._count_label.text()
print("6. 开关成功回写与失败回滚 OK")

win.close()
app.processEvents()
print("[OK] 自动化分页全部通过")
