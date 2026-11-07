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
3.6+ 启动方向：应用启动时主动执行一次（开机没有解锁事件可等）、
   「不操作」空转、服务未就绪不报错而是重试、重试成功即控制、
   配置中途改动放弃、预算耗尽发 skipped
4. 主窗口接线：组件存在、成功回写开关记忆、启动时触发一次动作、
   start 后轮询兜底在跑
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
    "enabled": False, "targets": [],
    "time_enabled": False, "time_start": "08:00", "time_end": "23:00",
    "net_enabled": False, "net_names": [],
}, f"默认配置不符: {cfg}"

# 新结构：一条事件可以对多个目标各自指定动作（设备开/关、自动化启用/停用）
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [
        {"kind": "device", "id": "lamp1", "name": "台灯",
         "startup": "on", "unlock": "on", "lock": "off"},
        {"kind": "automation", "id": "sc9", "name": "无人关灯", "home_id": "h1",
         "startup": "none", "unlock": "off", "lock": "on"},
    ],
})
got = settings_store.get_lock_automation()
assert [t["id"] for t in got["targets"]] == ["lamp1", "sc9"], got["targets"]
assert got["targets"][1] == {
    "kind": "automation", "id": "sc9", "name": "无人关灯", "home_id": "h1",
    "startup": "none", "unlock": "off", "lock": "on",
}, got["targets"][1]

# 旧配置（v0.9.1 的单 did + 三个 action）读取时迁移成一条设备目标，
# 用户升级后原来的「解锁开灯 / 锁屏关灯」照旧生效
_raw = settings_store._read_raw()
_raw["lock_automation"] = {
    "enabled": True, "did": "lamp1",
    "startup_action": "on", "unlock_action": "off", "lock_action": "on",
}
settings_store._write_raw(_raw)
legacy = settings_store.get_lock_automation()["targets"]
assert legacy == [{
    "kind": "device", "id": "lamp1", "name": "", "home_id": "",
    "startup": "on", "unlock": "off", "lock": "on",
}], legacy

# 用户把目标删光（显式 targets: []）后，绝不能因为旧 did 还在就长回来
settings_store.set_lock_automation({"enabled": True, "targets": []})
assert settings_store.get_lock_automation()["targets"] == [], "删光的目标又复活了"

# 非法目标整条丢弃；重复 (kind,id) 只留第一条；越界动作收敛成 none
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [
        {"kind": "device", "id": "lamp1", "unlock": "on"},
        {"kind": "device", "id": "lamp1", "unlock": "off"},
        {"kind": "robot", "id": "x"},
        {"kind": "device", "id": 123},
        {"kind": "automation", "id": "sc1", "unlock": "toggle", "lock": None},
    ],
})
targets = settings_store.get_lock_automation()["targets"]
assert [t["id"] for t in targets] == ["lamp1", "sc1"], targets
assert targets[0]["unlock"] == "on", "重复目标未保留第一条"
assert targets[1]["unlock"] == "none" and targets[1]["lock"] == "none", targets[1]

# 生效时间非法 HH:MM 回落默认；网络名单过滤非字符串/去重/去空白
settings_store.set_lock_automation({
    "enabled": True, "targets": [{"kind": "device", "id": "lamp1"}],
    "time_start": "25:00", "time_end": "bad",
    "net_names": ["ok", "", None, 5, "ok", "  spaced  "],
})
bad = settings_store.get_lock_automation()
assert bad["time_start"] == "08:00" and bad["time_end"] == "23:00", \
    f"非法时刻未收敛: {bad}"
assert bad["net_names"] == ["ok", "spaced"], f"网络名单未收敛: {bad['net_names']}"

# 非 dict / None 不炸，按默认配置处理（目标为空）
settings_store.set_lock_automation("garbage")
assert settings_store.get_lock_automation()["enabled"] is False
settings_store.set_lock_automation(None)
assert settings_store.get_lock_automation()["targets"] == []
print("1. settings.json 锁屏联动配置存取/收敛/旧配置迁移 OK")

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
        # 只让某一台设备失败：验证「单个目标失败不拖累其余目标」
        self.fail_did: str | None = None
        self.calls: list[tuple[str, bool]] = []
        # 自动化开关调用：(scene_id, enabled, home_id)
        self.auto_calls: list[tuple[str, bool, str]] = []

    def login_status(self) -> bool:
        return self._logged_in

    def set_power_state(self, did: str, state: bool) -> None:
        if self._fail or did == self.fail_did:
            raise RuntimeError("云端拒绝")
        self.calls.append((did, state))

    def set_automation_enabled(self, automation, enable: bool) -> None:
        if self._fail:
            raise RuntimeError("云端拒绝")
        self.auto_calls.append(
            (automation.scene_id, enable, automation.home_id))


def _dev(tid, unlock="on", lock="off", startup="none", name="台灯"):
    """一条设备目标：默认即「解锁打开、锁屏关闭」。"""
    return {"kind": "device", "id": tid, "name": name,
            "startup": startup, "unlock": unlock, "lock": lock}


def _auto(tid, unlock="none", lock="on", startup="none",
          name="无人关灯", home_id="home1"):
    """一条自动化目标：默认即「锁屏启用」（配合锁屏关灯用）。"""
    return {"kind": "automation", "id": tid, "name": name, "home_id": home_id,
            "startup": startup, "unlock": unlock, "lock": lock}


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
settings_store.set_lock_automation(
    {"enabled": False, "targets": [_dev("lamp1")]})
auto.reload_config()
stub.unlocked.emit()
stub.locked.emit()
assert jobs.tasks == [], "关闭时仍提交了任务"

# 3.2 启用 + 解锁 → 开灯（用户要的核心语义）
settings_store.set_lock_automation(
    {"enabled": True, "targets": [_dev("lamp1")]})
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
    {"enabled": True, "targets": [_dev("lamp1", unlock="none")]})
auto.reload_config()
stub.unlocked.emit()
assert jobs.tasks == [], "none 方向仍提交了任务"

# 3.5 未登录（凭据失效）：守卫在任务线程内判定，静默跳过——
# 任务会提交（条件/login 检查都在 fn 里），但不控制、不提示
service._logged_in = False
settings_store.set_lock_automation(
    {"enabled": True, "targets": [_dev("lamp1")]})
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
assert len(failures) == 1 and "云端拒绝" in failures[0] and \
    "台灯" in failures[0], f"失败未上报（应含目标名与原因）: {failures}"
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
# 3.16 多目标：解锁「开台灯 + 关挂灯 + 停用无人关灯」，
#      锁屏「关台灯 + 开挂灯 + 启用无人关灯」——本次需求的核心场景
auto_applied: list[tuple[str, bool]] = []
batches: list[list] = []
auto.automation_applied.connect(
    lambda sid, on: auto_applied.append((sid, on)))
auto.batch_applied.connect(batches.append)
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [
        _dev("lamp1"),
        _dev("lamp2", unlock="off", lock="on", name="挂灯"),
        _auto("sc9", unlock="off", lock="on"),
    ],
})
auto.reload_config()
jobs.tasks.clear()
service.calls.clear()
service.auto_calls.clear()
applied.clear()
auto_applied.clear()
batches.clear()
failures.clear()
stub.unlocked.emit()
assert len(jobs.tasks) == 1, "多目标应合并成一个批次任务"
_drain(jobs, service)
assert service.calls == [("lamp1", True), ("lamp2", False)], \
    f"解锁设备动作不符: {service.calls}"
assert service.auto_calls == [("sc9", False, "home1")], \
    f"解锁未停用自动化 / home_id 丢失: {service.auto_calls}"
assert applied == [("lamp1", True), ("lamp2", False)], applied
assert auto_applied == [("sc9", False)], auto_applied
assert len(batches) == 1 and len(batches[0]) == 3, \
    f"批量提示应汇总全部成功项: {batches}"
assert not failures, f"全成功却报了失败: {failures}"

stub.locked.emit()
_drain(jobs, service)
assert service.calls[-2:] == [("lamp1", False), ("lamp2", True)], \
    f"锁屏设备动作不符: {service.calls}"
assert service.auto_calls[-1] == ("sc9", True, "home1"), \
    f"锁屏未启用自动化: {service.auto_calls}"
assert auto_applied[-1] == ("sc9", True)

# 3.17 一个目标失败不应拖累同一批次里的其余目标
service.fail_did = "lamp1"
jobs.tasks.clear()
service.calls.clear()
service.auto_calls.clear()
applied.clear()
auto_applied.clear()
batches.clear()
failures.clear()
stub.unlocked.emit()
_drain(jobs, service)
assert service.calls == [("lamp2", False)], \
    f"台灯失败后挂灯仍应被控制: {service.calls}"
assert service.auto_calls == [("sc9", False, "home1")], \
    f"台灯失败后自动化仍应被控制: {service.auto_calls}"
assert applied == [("lamp2", False)] and auto_applied == [("sc9", False)]
assert len(batches) == 1 and len(batches[0]) == 2, \
    f"批量提示只应含成功项: {batches}"
assert len(failures) == 1 and "台灯" in failures[0], failures
service.fail_did = None

# 3.18 启动时同样支持多目标（只对配了 startup 的目标动手）
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [_dev("lamp1", startup="on", unlock="none", lock="none"),
                _auto("sc9", startup="off", unlock="none", lock="none")],
})
auto.reload_config()
jobs.tasks.clear()
service.calls.clear()
service.auto_calls.clear()
auto.run_startup()
assert len(jobs.tasks) == 1
_drain(jobs, service)
assert service.calls == [("lamp1", True)] and \
    service.auto_calls == [("sc9", False, "home1")], \
    f"启动方向多目标不符: {service.calls} {service.auto_calls}"

# 3.19 目标为空 / 三个方向全「不操作」：连任务都不提交
settings_store.set_lock_automation({"enabled": True, "targets": []})
auto.reload_config()
jobs.tasks.clear()
stub.unlocked.emit()
stub.locked.emit()
auto.run_startup()
assert jobs.tasks == [], "没有目标不该提交空任务"
settings_store.set_lock_automation({
    "enabled": True,
    "targets": [_dev("lamp1", startup="none", unlock="none", lock="none")],
})
auto.reload_config()
stub.unlocked.emit()
assert jobs.tasks == [], "全为「不操作」不该提交空任务"

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
    "enabled": True, "targets": [_dev("lamp1")],
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
        "enabled": True, "targets": [_dev("lamp1")],
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

# ---------- 3.6+ 启动方向（应用启动时执行一次 + 未就绪重试） ----------
# 开机自启时既等不到解锁事件（开机本来就不算解锁），网络与令牌也常未就绪。
# 所以启动方向是「主动触发 + 落空重试」，与两条被动事件路径语义不同。

from PySide6.QtCore import QEventLoop, QTimer as _QTimer


def _pump(ms: int = 120) -> None:
    """转一圈事件循环：让 QTimer.singleShot 排的重试真正跑起来。"""
    loop = QEventLoop()
    _QTimer.singleShot(ms, loop.quit)
    loop.exec()


_real_retry_ms = _la._STARTUP_RETRY_MS
try:
    _la._STARTUP_RETRY_MS = 0   # 测试里不真等 20 秒

    # 3.10 启动=打开：主动触发一次开灯（用户要的核心语义）
    settings_store.set_lock_automation({
        "enabled": True,
        "targets": [_dev("lamp1", startup="on")],
    })
    auto.reload_config()
    jobs.tasks.clear()
    applied.clear()
    calls_before = len(service.calls)
    auto.run_startup()
    assert len(jobs.tasks) == 1, "启动动作未提交任务"
    _drain(jobs, service)
    assert len(service.calls) == calls_before + 1 and \
        service.calls[-1] == ("lamp1", True), f"启动未开灯: {service.calls}"
    assert applied[-1] == ("lamp1", True), "启动成功未回传状态"

    # 3.11 启动=不操作（默认值）：不提交任何任务
    settings_store.set_lock_automation({
        "enabled": True, "targets": [_dev("lamp1", startup="none")]})
    auto.reload_config()
    jobs.tasks.clear()
    auto.run_startup()
    assert jobs.tasks == [], "「不操作」仍提交了任务"

    # 3.12 总开关关闭：启动方向同样不该动
    settings_store.set_lock_automation({
        "enabled": False, "targets": [_dev("lamp1", startup="on")]})
    auto.reload_config()
    jobs.tasks.clear()
    auto.run_startup()
    assert jobs.tasks == [], "总开关关闭仍提交了任务"

    # 3.13 服务未就绪（未登录）：不算失败、不弹提示，排重试；
    # 就绪后重试那一枪必须真的把灯打开——否则「开机开灯」形同虚设
    settings_store.set_lock_automation({
        "enabled": True,
        "targets": [_dev("lamp1", startup="on", unlock="none", lock="none")]})
    auto.reload_config()
    jobs.tasks.clear()
    applied.clear()
    failures.clear()
    service._logged_in = False
    auto.run_startup()
    assert len(jobs.tasks) == 1, "未就绪守卫应仍在任务内执行"
    _drain(jobs, service)
    assert applied == [] and failures == [], "未就绪被当成成功/失败上报"
    assert auto._startup_tries == 1, \
        f"未就绪未排重试: tries={auto._startup_tries}"
    _pump()   # 0ms 重试定时器到点 → 再次提交
    assert len(jobs.tasks) == 1, "重试未重新提交任务"
    service._logged_in = True
    _drain(jobs, service)
    assert service.calls[-1] == ("lamp1", True), f"重试后仍未开灯: {service.calls}"
    assert applied[-1] == ("lamp1", True), "重试成功未回传状态"

    # 3.14 重试途中配置被改成「不操作」：放弃，不补偿动作
    settings_store.set_lock_automation({
        "enabled": True, "targets": [_dev("lamp1", startup="none")]})
    auto.reload_config()
    jobs.tasks.clear()
    auto._retry_startup()
    assert jobs.tasks == [], "配置已改为不操作，重试仍提交了任务"

    # 3.15 重试预算耗尽：发一次 skipped 说明，不静默吞掉
    skipped.clear()
    auto._startup_tries = _la._STARTUP_MAX_TRIES
    auto._schedule_startup_retry()
    assert skipped and "未生效" in skipped[0], f"预算耗尽未提示: {skipped}"
finally:
    _la._STARTUP_RETRY_MS = _real_retry_ms
    service._logged_in = True
print("3.6+ 启动方向（主动触发/不操作/未就绪重试/预算耗尽） OK")

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
