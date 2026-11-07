# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""锁屏联动：锁屏 / 解锁 / 启动时，按配置控制一组设备与自动化。

典型配置（也是默认语义）：**解锁 → 开灯，锁屏 → 关灯**。
用户带着电脑离开时锁屏即关灯，回来解锁即亮灯，无需任何手动操作。

v0.9.2 起目标从「一台设备」升级为**一组目标**，每个目标各自持有三个
方向的状态，因此一条事件可以同时做多件事，例如：

    解锁 → 打开台灯、停用「无人自动关灯」
    锁屏 → 关闭台灯、启用「无人自动关灯」

目标分两类，但执行链同构（都是往云端写一个布尔值）：
- `device`：开 / 关（service.set_power_state）
- `automation`：启用 / 停用（service.set_automation_enabled）

此外还有第三个方向「**启动时**」（默认不操作）：应用随系统自启动后
主动执行一次，用来覆盖「开机时灯是灭的」这种冷启动场景——此时既没有
解锁事件（本来就没锁），也没有人手动触发。由主窗口在登录完成后显式
调用 `run_startup()`，不走会话事件。

执行链与定时开关完全同构：状态变化只提交一个任务进 JobExecutor
串行队列（PRIORITY_CONTROL，插队执行），网络调用不占界面线程。
一个批次内的多个目标**串行执行、单个失败不影响其余**，最后统一回传：
成功的经 state_applied / automation_applied 通知界面同步，失败的汇总
成一条 failed 说明。

防抖与守卫：
- `_pending` 单飞行标志：解锁后 2 秒内又锁屏，只执行最后一次动作，
  不会把队列塞满（米家云端对高频写入会限流）；
- 未登录（凭据失效）时静默跳过，登录流程由主窗口负责；
- 配置关闭 / 该方向全部为「不操作」时直接跳过；
- 生效时间段 / 生效网络条件（见 core/lock_conditions.py）在**任务线程**
  内判定：网络查询是同步子进程（最长 5s），放在这里不会冻结界面；
  条件不满足发 skipped 信号提示原因，不消耗任何控制。
- 「启动时」动作带重试：随系统自启时 Wi-Fi 常未连上、令牌还在刷新，
  第一次几乎必然落空，见 _STARTUP_RETRY_MS。
"""

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.jobs import PRIORITY_CONTROL
from app.core.lock_conditions import (
    current_network_names, in_time_window, match_network,
)

logger = logging.getLogger(__name__)

# 「启动时」动作的重试节奏。开机自启的真实情况是：进程先起来了，
# Wi-Fi 还没连上、米家令牌还在刷新，第一枪 login_status() 大概率是 False。
# 只打一枪等于「开机开灯」这个需求根本不会生效，所以按 20 秒一轮重试，
# 9 轮约 3 分钟——足够覆盖开机后网络就绪的窗口，又不会无限重试。
_STARTUP_RETRY_MS = 20_000
_STARTUP_MAX_TRIES = 9

# 一个批次里最多处理多少个目标：用户手滑配了几十条时，锁屏瞬间会连续
# 打几十个云端写请求，既慢又容易触发限流。超过的截断并记日志。
_MAX_TARGETS = 20


def _target_name(target: dict) -> str:
    """目标显示名：优先用配置里存的名称，缺失时退化成 id 尾号。"""
    name = str(target.get("name") or "").strip()
    if name:
        return name
    tid = str(target.get("id") or "")
    return f"…{tid[-6:]}" if len(tid) > 6 else (tid or "未命名目标")


class LockAutomation(QObject):
    """把会话锁屏事件翻译成一批设备/自动化控制。

    用法:
        automation = LockAutomation(service, jobs, watcher)
        automation.state_applied.connect(main._apply_scheduled_state)
        automation.automation_applied.connect(main._on_lock_automation_applied)
        automation.failed.connect(lambda msg: Toast.info(main, msg, 5000))
        automation.skipped.connect(lambda msg: Toast.info(main, msg, 4000))
        # 登录完成后调用；设置对话框保存后调用 reload_config()
    """

    # (did, turn_on)：设备控制成功，供界面同步卡片 / 托盘 / 挂件
    state_applied = Signal(str, bool)
    # (scene_id, enabled)：自动化开关已拨动，供界面同步自动化卡片
    automation_applied = Signal(str, bool)
    # [(kind, id, name, on)]：整批成功项，供界面一次提示（不逐项刷 Toast）
    batch_applied = Signal(list)
    # 失败信息（用户可读），由界面层决定如何提示
    failed = Signal(str)
    # 条件未满足被跳过的原因（用户可读）；登录态缺失等静默跳过不发
    skipped = Signal(str)

    def __init__(self, service, jobs, watcher, parent=None):
        super().__init__(parent)
        self._service = service
        self._jobs = jobs
        self._watcher = watcher
        # 单飞行：一次控制批次在途时忽略后续事件（下一次状态变化再执行）
        self._pending = False
        # 「启动时」已重试次数（每次 run_startup 重置）
        self._startup_tries = 0
        # 配置在构造时读一次；start() 前主窗口可能还没登录，无副作用
        from app.core.settings_store import get_lock_automation
        self._config = get_lock_automation()

        watcher.locked.connect(self._on_locked)
        watcher.unlocked.connect(self._on_unlocked)

    # ---------- 配置 ----------

    def reload_config(self) -> None:
        """设置对话框保存后调用，让改动对下一次锁屏事件生效。"""
        from app.core.settings_store import get_lock_automation
        self._config = get_lock_automation()

    @property
    def config(self) -> dict:
        return dict(self._config)

    # ---------- 事件入口 ----------

    def _on_locked(self) -> None:
        self._run("lock")

    def _on_unlocked(self) -> None:
        self._run("unlock")

    def run_startup(self) -> None:
        """应用启动（登录完成）时按配置执行一次动作。

        与锁屏/解锁不同，这条路径的触发时机是被动的：随系统自启动时
        网络往往还没就绪，所以落空后按 _STARTUP_RETRY_MS 继续重试，
        最多 _STARTUP_MAX_TRIES 轮。被用户主动关闭的配置不会重试。
        """
        cfg = self._config
        if not cfg.get("enabled"):
            return
        entries = self._entries("startup", cfg)
        if not entries:
            return
        self._startup_tries = 0
        self._dispatch(entries, "启动", retry=True)

    def _run(self, event: str) -> None:
        """event: "lock" | "unlock"；按配置挑出该方向要动的目标。"""
        cfg = self._config
        if not cfg.get("enabled"):
            return
        entries = self._entries(event, cfg)
        if not entries:
            return
        self._dispatch(entries, "锁屏" if event == "lock" else "解锁")

    def _entries(self, event: str, cfg: dict) -> list[tuple[dict, bool]]:
        """取出该事件真正要执行的目标：[(target, turn_on)]。

        「不操作」的目标在提交任务前就过滤掉——空批次连任务都不该提交，
        否则每次锁屏都白占一个队列位置。
        """
        targets = cfg.get("targets") or []
        if len(targets) > _MAX_TARGETS:
            logger.warning("锁屏联动目标过多（%d 个），只执行前 %d 个",
                           len(targets), _MAX_TARGETS)
            targets = targets[:_MAX_TARGETS]
        entries: list[tuple[dict, bool]] = []
        for target in targets:
            action = str(target.get(event) or "none")
            if action in ("on", "off"):
                entries.append((target, action == "on"))
        return entries

    # ---------- 执行 ----------

    def _apply_target(self, target: dict, turn_on: bool) -> None:
        """把单个目标写成目标状态；失败抛异常由调用方收集。"""
        kind = target.get("kind")
        tid = str(target.get("id") or "")
        if kind == "automation":
            from app.core.models import AutomationInfo
            self._service.set_automation_enabled(
                AutomationInfo(
                    scene_id=tid,
                    name=_target_name(target),
                    home_id=str(target.get("home_id") or ""),
                ),
                turn_on,
            )
            return
        self._service.set_power_state(tid, turn_on)

    def _dispatch(
            self, entries: list[tuple[dict, bool]], label: str,
            retry: bool = False) -> None:
        """执行一批目标；label 只用于日志文案。

        retry=True 表示这是「启动时」路径：服务未就绪（未登录 / 令牌还在
        刷新）不报错而是排重试；失败也不弹提示，交由重试兜到成功为止。
        """
        cfg = self._config
        if self._pending:
            logger.info("锁屏联动控制在途，跳过本次%s事件", label)
            if retry:
                # 开机瞬间可能正好撞上一条在途控制：不能把这一枪算作
                # 已尝试，否则竞争会把重试预算白白耗光
                QTimer.singleShot(_STARTUP_RETRY_MS, self, self._retry_startup)
            return
        logger.info("锁屏联动：%s → %s", label, "、".join(
            f"{'打开' if on else '关闭'}「{_target_name(t)}」"
            for t, on in entries))
        self._pending = True

        def _work(items=tuple(entries), c=cfg):
            # 条件门在任务线程执行：网络查询是同步子进程（最长 5s），
            # 放主线程会冻结界面；时间判断虽轻也一并放这里统一处理。
            # 返回 (kind, payload)：
            #   done  → payload=(成功列表, 失败列表)
            #   skip  → payload=原因文案
            #   retry → payload=None（服务未就绪，交给启动重试）
            if c.get("time_enabled") and not in_time_window(
                    c.get("time_start"), c.get("time_end")):
                return ("skip", f"不在生效时段"
                                f"（{c.get('time_start')}–{c.get('time_end')}）")
            if c.get("net_enabled"):
                names = current_network_names()
                if not match_network(names, c.get("net_names")):
                    if names:
                        return ("skip", f"当前网络（{names[0]}）不在生效网络列表")
                    return ("skip", "当前网络不可知，生效网络条件未满足")
            # 未登录时凭据无效，控制必然失败。锁屏方向静默跳过更安静；
            # 启动方向则要重试——开机早期网络/令牌未就绪是常态，不是错误
            try:
                if not self._service.login_status():
                    return ("retry" if retry else "skip", None)
            except Exception:
                return ("retry" if retry else "skip", None)
            done: list[tuple[dict, bool]] = []
            errors: list[tuple[dict, str]] = []
            for target, on in items:
                try:
                    self._apply_target(target, on)
                except Exception as exc:  # noqa: BLE001 - 单项失败不拖累整批
                    logger.warning("锁屏联动目标失败 %s/%s: %s",
                                   target.get("kind"), target.get("id"), exc)
                    errors.append((target, str(exc) or exc.__class__.__name__))
                else:
                    done.append((target, on))
            return ("done", (done, errors))

        def _on_success(result):
            self._pending = False
            kind, payload = result if isinstance(result, tuple) else ("done", ([], []))
            if kind == "retry":
                self._schedule_startup_retry()
                return
            if kind == "skip":
                if payload:
                    logger.info("锁屏联动跳过：%s", payload)
                    self.skipped.emit(payload)
                return
            done, errors = payload
            for target, on in done:
                if target.get("kind") == "automation":
                    self.automation_applied.emit(str(target.get("id") or ""), on)
                else:
                    self.state_applied.emit(str(target.get("id") or ""), on)
            if done:
                # 一条事件动了多个目标时逐项弹 Toast 会瞬间叠满屏幕，
                # 这里合成一条（界面层决定怎么排版）
                self.batch_applied.emit([
                    (target.get("kind"), str(target.get("id") or ""),
                     _target_name(target), on)
                    for target, on in done
                ])
            if errors:
                self.failed.emit(_failure_text(errors))

        def _on_error(error):
            self._pending = False
            logger.warning("锁屏联动执行失败: %s", error)
            if retry:
                # 开机早期的失败大多也是网络问题的变体，先重试；
                # 重试预算耗尽时会发一次 skipped 说明，不静默吞掉
                self._schedule_startup_retry()
                return
            self.failed.emit(str(error))

        self._jobs.submit(
            _work,
            on_success=_on_success,
            on_error=_on_error,
            priority=PRIORITY_CONTROL,
        )

    # ---------- 启动方向的重试 ----------

    def _schedule_startup_retry(self) -> None:
        """排下一轮启动重试；预算耗尽后发一次 skipped 说明并放弃。"""
        if self._startup_tries >= _STARTUP_MAX_TRIES:
            logger.info("启动联动重试 %d 轮仍未成功，放弃", self._startup_tries)
            self.skipped.emit(
                f"启动联动未生效：{_STARTUP_MAX_TRIES} 轮重试内网络或登录状态"
                f"未就绪")
            return
        self._startup_tries += 1
        logger.info("启动联动将在 %d 秒后重试（第 %d/%d 轮）",
                    _STARTUP_RETRY_MS // 1000, self._startup_tries,
                    _STARTUP_MAX_TRIES)
        QTimer.singleShot(_STARTUP_RETRY_MS, self, self._retry_startup)

    def _retry_startup(self) -> None:
        """到点重试一次启动动作；配置已被改动则放弃。"""
        cfg = self._config
        if not cfg.get("enabled"):
            logger.info("启动联动配置已变更，停止重试")
            return
        entries = self._entries("startup", cfg)
        if not entries:
            logger.info("启动联动配置已变更，停止重试")
            return
        self._dispatch(entries, "启动", retry=True)


def _failure_text(errors: list[tuple[dict, str]]) -> str:
    """把失败目标汇总成一句用户能看懂的话。

    单个失败说清是谁 + 原因；多个失败只列名字与首条原因——米家同一次
    网络故障会让每个目标报同一句话，重复 5 遍没人看。
    """
    names = "、".join(f"「{_target_name(t)}」" for t, _ in errors)
    reason = errors[0][1]
    if len(errors) == 1:
        return f"{names}未成功：{reason}"
    return f"{len(errors)} 个目标未成功：{names}（{reason}）"
