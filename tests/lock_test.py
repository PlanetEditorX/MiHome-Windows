# SPDX-License-Identifier: GPL-3.0-or-later
"""锁屏联动回归测试（离屏）。

锁死这些行为：
1. settings.json 锁屏联动配置的存取与非法值收敛
2. 锁屏监听状态机：只有状态变化才发信号（重复通知去重、None 不发射）
3. 联动执行：解锁开/锁屏关、none 方向不动作、未登录跳过、
   在途去重、失败发 failed 且 pending 清理（后续事件仍可执行）
4. 主窗口接线：组件存在、成功回写开关记忆、start 后轮询兜底在跑
"""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

app = QApplication([])

# ---------- 1. 设置存取与规范化 ----------

from app.core import settings_store

cfg = settings_store.get_lock_automation()
assert cfg == {
    "enabled": False, "did": "",
    "unlock_action": "on", "lock_action": "off",
}, f"默认配置不符: {cfg}"

settings_store.set_lock_automation(
    {"enabled": True, "did": "lamp1", "unlock_action": "off", "lock_action": "on"})
assert settings_store.get_lock_automation() == {
    "enabled": True, "did": "lamp1",
    "unlock_action": "off", "lock_action": "on",
}, "存取往返不一致"

# 非法值一律收敛：did 非字符串按未选择，动作越界回落默认
settings_store.set_lock_automation(
    {"enabled": True, "did": 123, "unlock_action": "toggle", "lock_action": None})
bad = settings_store.get_lock_automation()
assert bad["did"] == "" and bad["unlock_action"] == "on" and bad["lock_action"] == "off", \
    f"非法值未收敛: {bad}"

# 非 dict / None 不炸，按默认配置处理
settings_store.set_lock_automation("garbage")
assert settings_store.get_lock_automation()["enabled"] is False
settings_store.set_lock_automation(None)
assert settings_store.get_lock_automation()["did"] == ""
print("1. settings.json 锁屏联动配置存取/收敛 OK")

# ---------- 2. 监听状态机 ----------

from app.core.session_watch import SessionLockWatcher, is_supported, logon_ui_running

watcher = SessionLockWatcher()
events: list[str] = []
watcher.locked.connect(lambda: events.append("locked"))
watcher.unlocked.connect(lambda: events.append("unlocked"))

assert watcher.is_locked is None, "未启动时应为未知状态"
watcher._set_locked(True)
assert events == ["locked"] and watcher.is_locked is True
# 重复锁屏通知不重复发射（轮询与 WTS 双通道会互相喂同值）
watcher._set_locked(True)
assert events == ["locked"], f"重复锁屏被当成新事件: {events}"
watcher._set_locked(False)
assert events == ["locked", "unlocked"], f"解锁事件缺失: {events}"
# None（探测失败）不得被当成状态变化
watcher._set_locked(None)
assert events == ["locked", "unlocked"], f"None 被当成事件: {events}"

# 非 Windows 上 start() 必须安全空转；Windows 上枚举函数不得抛异常
if is_supported():
    result = logon_ui_running()
    assert result is None or isinstance(result, bool), f"枚举返回值异常: {result!r}"
else:
    watcher.start()
    assert watcher.is_locked is None
print("2. 锁屏状态机去重 OK")

# ---------- 3. 联动执行 ----------

from app.core.jobs import PRIORITY_CONTROL
from app.core.lock_automation import LockAutomation


class _StubWatcher(QObject):
    """只提供信号源，绕过真实 Windows 会话（测试不锁用户的屏）。"""

    locked = Signal()
    unlocked = Signal()


class _FakeJobs:
    def __init__(self):
        self.tasks: list[tuple] = []

    def submit(self, fn, on_success=None, on_error=None, priority=0):
        self.tasks.append((fn, on_success, on_error, priority))


class _FakeService:
    def __init__(self, logged_in: bool = True, fail: bool = False):
        self._logged_in = logged_in
        self._fail = fail
        self.calls: list[tuple[str, bool]] = []

    def login_status(self) -> bool:
        return self._logged_in

    def set_power_state(self, did: str, state: bool) -> None:
        if self._fail:
            raise RuntimeError("云端拒绝")
        self.calls.append((did, state))


def _drain(jobs: _FakeJobs, service: _FakeService) -> None:
    """按 JobExecutor 的语义执行在途任务：fn 异常走 on_error。"""
    fn, on_success, on_error, _prio = jobs.tasks.pop(0)
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 与 executor 的 worker 行为一致
        if on_error is not None:
            on_error(exc)
    else:
        if on_success is not None:
            on_success(None)


stub = _StubWatcher()
jobs = _FakeJobs()
service = _FakeService()
auto = LockAutomation(service, jobs, stub)
applied: list[tuple[str, bool]] = []
failures: list[str] = []
auto.state_applied.connect(lambda d, on: applied.append((d, on)))
auto.failed.connect(failures.append)

# 3.1 总开关关闭：任何事件都不提交
settings_store.set_lock_automation({"enabled": False, "did": "lamp1"})
auto.reload_config()
stub.unlocked.emit()
stub.locked.emit()
assert jobs.tasks == [], "关闭时仍提交了任务"

# 3.2 启用 + 解锁 → 开灯（用户要的核心语义）
settings_store.set_lock_automation(
    {"enabled": True, "did": "lamp1", "unlock_action": "on", "lock_action": "off"})
auto.reload_config()
stub.unlocked.emit()
assert len(jobs.tasks) == 1
fn, _ok, _err, priority = jobs.tasks[0]
assert priority == PRIORITY_CONTROL, "联动控制未按 CONTROL 优先级插队"
_drain(jobs, service)
assert service.calls == [("lamp1", True)], f"解锁未开灯: {service.calls}"
assert applied == [("lamp1", True)], f"成功信号未回传: {applied}"

# 3.3 锁屏 → 关灯
stub.locked.emit()
assert len(jobs.tasks) == 1
_drain(jobs, service)
assert service.calls[-1] == ("lamp1", False), f"锁屏未关灯: {service.calls}"

# 3.4 该方向设为「不操作」时不提交
settings_store.set_lock_automation(
    {"enabled": True, "did": "lamp1", "unlock_action": "none", "lock_action": "off"})
auto.reload_config()
stub.unlocked.emit()
assert jobs.tasks == [], "none 方向仍提交了任务"

# 3.5 未登录（凭据失效）时静默跳过
service._logged_in = False
settings_store.set_lock_automation(
    {"enabled": True, "did": "lamp1", "unlock_action": "on", "lock_action": "off"})
auto.reload_config()
stub.unlocked.emit()
assert jobs.tasks == [], "未登录时仍提交了任务"
service._logged_in = True

# 3.6 失败：发 failed 且 pending 清理，后续事件仍可执行
service._fail = True
applied_before = len(applied)
stub.unlocked.emit()
assert len(jobs.tasks) == 1
_drain(jobs, service)
assert failures == ["云端拒绝"], f"失败未上报: {failures}"
assert len(applied) == applied_before, "失败不应发成功信号"
# pending 已清理：下一次锁屏事件能再次提交
service._fail = False
stub.locked.emit()
assert len(jobs.tasks) == 1, "失败后 pending 未清理，后续事件被吞"
_drain(jobs, service)
assert service.calls[-1] == ("lamp1", False)

# 3.7 在途去重：连珠事件只执行最后一次动作
jobs.tasks.clear()
stub.unlocked.emit()
stub.unlocked.emit()
stub.locked.emit()
assert len(jobs.tasks) == 1, f"在途事件未去重: {len(jobs.tasks)} 个任务"
_drain(jobs, service)
assert service.calls[-1] == ("lamp1", True)
print("3. 联动执行（语义/守卫/失败/去重） OK")

# ---------- 4. 主窗口接线 ----------

from app.core.models import DeviceInfo
from app.ui.main_window import MainWindow

MainWindow._refresh_power_states = lambda self, force: None
MainWindow._refresh_metrics = lambda self: None
MainWindow._load_card_icons = lambda self: None
MainWindow._maybe_localize_names = lambda self: None
MainWindow._load_scenes = lambda self: None

win = MainWindow()
assert isinstance(win._lock_watcher, SessionLockWatcher), "锁屏监听未接入主窗口"
assert isinstance(win._lock_automation, LockAutomation), "联动执行未接入主窗口"

win._apply_devices([DeviceInfo("lamp1", "台灯", "m", "家", "书房", True)])

# 联动成功回写：与定时任务同一套（开关记忆 + 卡片 + 托盘 + 挂件）
win._on_lock_state_applied("lamp1", True)
assert win._known_power["lamp1"] is True, "联动成功后未回写开关记忆"

# 启动链路：标记置位、轮询兜底计时器在跑（Windows 上）
win._start_lock_automation()
assert win._lock_started is True
win._start_lock_automation()  # 重复启动无副作用
assert win._lock_started is True
if is_supported():
    assert win._lock_watcher._poll_timer.isActive(), "轮询兜底未启动"

# 关闭链路：stop 后计时器停、状态复位
win._lock_watcher.stop()
assert not win._lock_watcher._poll_timer.isActive(), "stop 后轮询仍在跑"
assert win._lock_watcher.is_locked is None
print("4. 主窗口接线 OK")

# ---------- 5. WTS 消息管道（仅 Windows；子进程跑真实窗口子系统） ----------
# 本文件顶部写死了 QT_QPA_PLATFORM=offscreen，而离屏平台 winId() 恒为 0、
# WTS 注册必然失败——消息管道必须在真实窗口子系统里验证，因此拆到
# tests/wts_pipeline_test.py 由子进程拉起（子进程不设 offscreen）。
# 那条链路曾因 WM_WTSSESSION_CHANGE 误写成 0x02B1 而整体静默失效，
# 且旧测试 import 错误常量投递消息、自欺欺人全绿；现已改为硬编码
# SDK 权威值 + 常量守门断言。
if is_supported():
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items()
           if k != "QT_QPA_PLATFORM"}
    proc = subprocess.run(
        [sys.executable, "-m", "tests.wts_pipeline_test"],
        capture_output=True, text=True, timeout=120, env=env)
    out = (proc.stdout + proc.stderr).strip()
    assert proc.returncode == 0, f"WTS 管道子进程失败:\n{out}"
    print(f"5. WTS 消息管道 {out}")
else:
    print("5. WTS 消息管道跳过（非 Windows 平台）")

print("\n全部通过")
