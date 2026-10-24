# SPDX-License-Identifier: GPL-3.0-or-later
"""锁屏联动回归测试（离屏）。

锁死这些行为：
1. settings.json 锁屏联动配置的存取与非法值收敛（含生效时间/网络字段）
1.5 生效条件纯函数：时间窗（普通/跨午夜/全天/边界/非法）、网络匹配
    （包含/大小写/空名单放行/不可知拦截）、网络名输入解析
2. 锁屏监听状态机：只有状态变化才发信号（重复通知去重、None 不发射）
3. 联动执行：解锁开/锁屏关、none 方向不动作、未登录跳过、
   在途去重、失败发 failed 且 pending 清理（后续事件仍可执行）
3.5+ 生效时间/网络条件门：未命中不控制但发 skipped 带原因；
   网络命中（大小写不敏感包含）才执行；查询失败保守跳过
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
    "time_enabled": False, "time_start": "08:00", "time_end": "23:00",
    "net_enabled": False, "net_names": [],
}, f"默认配置不符: {cfg}"

settings_store.set_lock_automation(
    {"enabled": True, "did": "lamp1", "unlock_action": "off", "lock_action": "on"})
assert settings_store.get_lock_automation() == {
    "enabled": True, "did": "lamp1",
    "unlock_action": "off", "lock_action": "on",
    "time_enabled": False, "time_start": "08:00", "time_end": "23:00",
    "net_enabled": False, "net_names": [],
}, "存取往返不一致"

# 非法值一律收敛：did 非字符串按未选择，动作越界回落默认
settings_store.set_lock_automation(
    {"enabled": True, "did": 123, "unlock_action": "toggle", "lock_action": None})
bad = settings_store.get_lock_automation()
assert bad["did"] == "" and bad["unlock_action"] == "on" and bad["lock_action"] == "off", \
    f"非法值未收敛: {bad}"

# 生效时间非法 HH:MM 回落默认；网络名单过滤非字符串/去重/去空白
settings_store.set_lock_automation({
    "enabled": True, "did": "lamp1",
    "time_start": "25:00", "time_end": "bad",
    "net_names": ["ok", "", None, 5, "ok", "  spaced  "],
})
bad = settings_store.get_lock_automation()
assert bad["time_start"] == "08:00" and bad["time_end"] == "23:00", \
    f"非法时刻未收敛: {bad}"
assert bad["net_names"] == ["ok", "spaced"], f"网络名单未收敛: {bad['net_names']}"

# 非 dict / None 不炸，按默认配置处理
settings_store.set_lock_automation("garbage")
assert settings_store.get_lock_automation()["enabled"] is False
settings_store.set_lock_automation(None)
assert settings_store.get_lock_automation()["did"] == ""
print("1. settings.json 锁屏联动配置存取/收敛 OK")

# ---------- 1.5 生效条件纯函数（时间窗 / 网络匹配） ----------

from datetime import datetime, timedelta

from app.core.lock_conditions import (
    format_hhmm, in_time_window, match_network, parse_hhmm, parse_network_text,
)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 25, hour, minute)


# 普通窗 08:00–23:00：左闭右开
assert in_time_window("08:00", "23:00", _at(8, 0)) is True
assert in_time_window("08:00", "23:00", _at(7, 59)) is False
assert in_time_window("08:00", "23:00", _at(22, 59)) is True
assert in_time_window("08:00", "23:00", _at(23, 0)) is False
# 跨午夜窗 23:00–06:00：晚于起点或早于终点
assert in_time_window("23:00", "06:00", _at(23, 0)) is True
assert in_time_window("23:00", "06:00", _at(23, 59)) is True
assert in_time_window("23:00", "06:00", _at(0, 0)) is True
assert in_time_window("23:00", "06:00", _at(5, 59)) is True
assert in_time_window("23:00", "06:00", _at(6, 0)) is False
assert in_time_window("23:00", "06:00", _at(12, 0)) is False
# 起=止视为 24 小时全天
assert in_time_window("09:00", "09:00", _at(3, 0)) is True
assert in_time_window("09:00", "09:00", _at(23, 59)) is True
# 任一端非法：不限制（防御分支，settings_store 已先行收敛）
assert in_time_window("abc", "23:00", _at(12, 0)) is True
assert in_time_window("08:00", "8:70", _at(12, 0)) is True

# HH:MM 解析/格式化
assert parse_hhmm("7:05") == (7, 5)
assert parse_hhmm("07:05") == (7, 5)
assert parse_hhmm("24:00") is None
assert parse_hhmm("12:60") is None
assert parse_hhmm("nope") is None
assert parse_hhmm(None) is None
assert format_hhmm("7:5") == "07:05"
assert format_hhmm("bad") == "08:00"

# 网络名单匹配：空名单/None 不限制；None 当前值保守拦截；大小写不敏感包含
assert match_network(["CMCC-Home"], []) is True
assert match_network(["CMCC-Home"], None) is True
assert match_network(["CMCC-Home"], ["cmcc-home"]) is True
assert match_network(["CMCC-8f3a"], ["CMCC"]) is True
assert match_network(["ChinaNet-x"], ["CMCC"]) is False
assert match_network(None, ["CMCC"]) is False
assert match_network([], ["CMCC"]) is False

# 网络名输入解析：中英文逗号/分号/换行分隔，去重保序
assert parse_network_text("a, b，c;d\n e") == ["a", "b", "c", "d", "e"]
assert parse_network_text(" a , a , b ") == ["a", "b"]
assert parse_network_text("") == []
assert parse_network_text(None) == []
print("1.5 生效条件纯函数（时间窗/网络匹配/解析） OK")

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
    """按 JobExecutor 的语义执行在途任务：fn 异常走 on_error，
    on_success 收到 fn 的返回值（条件门靠它回传 skip/done）。"""
    fn, on_success, on_error, _prio = jobs.tasks.pop(0)
    try:
        ret = fn()
    except Exception as exc:  # noqa: BLE001 - 与 executor 的 worker 行为一致
        if on_error is not None:
            on_error(exc)
    else:
        if on_success is not None:
            on_success(ret)


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

# 3.5 未登录（凭据失效）：守卫在任务线程内判定，静默跳过——
# 任务会提交（条件/login 检查都在 fn 里），但不控制、不提示
service._logged_in = False
settings_store.set_lock_automation(
    {"enabled": True, "did": "lamp1", "unlock_action": "on", "lock_action": "off"})
auto.reload_config()
applied_before = len(applied)
failures_before = len(failures)
stub.unlocked.emit()
assert len(jobs.tasks) == 1, "未登录守卫应仍在任务内执行"
_drain(jobs, service)
assert len(applied) == applied_before and len(failures) == failures_before, \
    "未登录时不应发成功/失败信号"
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

# ---------- 3.5+ 生效时间/网络条件门 ----------
# 条件在任务线程内判定（网络查询是同步子进程，不能冻结界面）；
# 未命中时不控制设备，但发 skipped 信号让界面说明原因。

import app.core.lock_automation as _la

_now = datetime.now()
skipped: list[str] = []
auto.skipped.connect(skipped.append)

# 3.8 生效时间：构造一个「此刻必定不命中」的跨午夜窗
# （起点=此刻+1 分钟，终点=此刻-1 分钟 → 窗口在前后两侧，不含现在）
settings_store.set_lock_automation({
    "enabled": True, "did": "lamp1",
    "unlock_action": "on", "lock_action": "off",
    "time_enabled": True,
    "time_start": (_now + timedelta(minutes=1)).strftime("%H:%M"),
    "time_end": (_now - timedelta(minutes=1)).strftime("%H:%M"),
})
auto.reload_config()
skipped.clear()
calls_before = len(service.calls)
stub.unlocked.emit()
assert len(jobs.tasks) == 1, "时间条件应在任务内判定，任务要提交"
_drain(jobs, service)
assert len(service.calls) == calls_before, "时间条件未满足却控制了设备"
assert skipped and "生效时段" in skipped[0], f"跳过原因未上报: {skipped}"

# 3.9 生效网络：命中才执行；未命中/不可知都保守跳过
_real_net = _la.current_network_names
try:
    settings_store.set_lock_automation({
        "enabled": True, "did": "lamp1",
        "unlock_action": "on", "lock_action": "off",
        "time_enabled": False,
        "net_enabled": True, "net_names": ["CMCC"],
    })
    auto.reload_config()

    # 未命中：ChinaNet-x 不含 CMCC
    _la.current_network_names = lambda: ["ChinaNet-x"]
    skipped.clear()
    calls_before = len(service.calls)
    stub.unlocked.emit()
    _drain(jobs, service)
    assert len(service.calls) == calls_before and skipped, \
        "网络未命中应跳过并上报原因"

    # 命中：CMCC-8f3a 包含 CMCC（大小写不敏感）
    _la.current_network_names = lambda: ["cmcc-8f3a"]
    skipped.clear()
    stub.unlocked.emit()
    _drain(jobs, service)
    assert service.calls[-1] == ("lamp1", True), "网络命中应正常执行"
    assert not skipped, f"命中时不应跳过: {skipped}"

    # 查询失败（None）：保守跳过
    _la.current_network_names = lambda: None
    skipped.clear()
    calls_before = len(service.calls)
    stub.unlocked.emit()
    _drain(jobs, service)
    assert len(service.calls) == calls_before and skipped, \
        "网络不可知应保守跳过"
finally:
    _la.current_network_names = _real_net
print("3.5+ 生效时间/网络条件门 OK")

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
