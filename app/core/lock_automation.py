# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""锁屏联动：锁屏 / 解锁时自动控制指定设备的开关。

典型配置（也是默认语义）：**解锁 → 开灯，锁屏 → 关灯**。
用户带着电脑离开时锁屏即关灯，回来解锁即亮灯，无需任何手动操作。

执行链与定时开关完全同构：状态变化只提交一个任务进 JobExecutor
串行队列（PRIORITY_CONTROL，插队执行），网络调用不占界面线程；
成功后经 state_applied 信号回主线程同步三处界面（卡片 / 托盘 /
桌面挂件），失败经 failed 信号提示，与「定时任务」的用户体验一致。

防抖与守卫：
- `_pending` 单飞行标志：解锁后 2 秒内又锁屏，只执行最后一次动作，
  不会把队列塞满（米家云端对高频写入会限流）；
- 未登录（凭据失效）时静默跳过，登录流程由主窗口负责；
- 配置关闭 / 未选设备 / 该方向设为「不操作」时直接跳过；
- 生效时间段 / 生效网络条件（见 core/lock_conditions.py）在**任务线程**
  内判定：网络查询是同步子进程（最长 5s），放在这里不会冻结界面；
  条件不满足发 skipped 信号提示原因，不消耗设备控制。
"""

import logging

from PySide6.QtCore import QObject, Signal

from app.core.jobs import PRIORITY_CONTROL
from app.core.lock_conditions import (
    current_network_names, in_time_window, match_network,
)

logger = logging.getLogger(__name__)


class LockAutomation(QObject):
    """把会话锁屏事件翻译成一次设备开关控制。

    用法:
        automation = LockAutomation(service, jobs, watcher)
        automation.state_applied.connect(main._apply_scheduled_state)
        automation.failed.connect(lambda msg: Toast.info(main, msg, 5000))
        automation.skipped.connect(lambda msg: Toast.info(main, msg, 4000))
        # 登录完成后调用；设置对话框保存后调用 reload_config()
    """

    # (did, turn_on)：控制成功，供界面同步卡片 / 托盘 / 挂件
    state_applied = Signal(str, bool)
    # 失败信息（用户可读），由界面层决定如何提示
    failed = Signal(str)
    # 条件未满足被跳过的原因（用户可读）；登录态缺失等静默跳过不发
    skipped = Signal(str)

    def __init__(self, service, jobs, watcher, parent=None):
        super().__init__(parent)
        self._service = service
        self._jobs = jobs
        self._watcher = watcher
        # 单飞行：一次控制在途时忽略后续事件（下一次状态变化再执行）
        self._pending = False
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

    def _run(self, event: str) -> None:
        """event: "lock" | "unlock"；按配置决定是否控制设备。"""
        cfg = self._config
        if not cfg.get("enabled"):
            return
        did = str(cfg.get("did") or "")
        action = str(cfg.get(f"{event}_action") or "none")
        if not did or action not in ("on", "off"):
            return
        if self._pending:
            logger.info("锁屏联控制在途，跳过本次%s事件", event)
            return
        turn_on = action == "on"
        logger.info("锁屏联动：%s → %s「%s」",
                    "锁屏" if event == "lock" else "解锁",
                    "打开" if turn_on else "关闭", did)
        self._pending = True

        def _work(d=did, on=turn_on, c=cfg):
            # 条件门在任务线程执行：网络查询是同步子进程（最长 5s），
            # 放主线程会冻结界面；时间判断虽轻也一并放这里统一处理。
            # 返回 (kind, reason)：done=已控制 / skip=跳过（附原因）。
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
            # 未登录时凭据无效，控制必然失败；静默跳过比弹错更安静
            try:
                if not self._service.login_status():
                    return ("skip", None)
            except Exception:
                return ("skip", None)
            self._service.set_power_state(d, on)
            return ("done", None)

        def _on_success(result, d=did, on=turn_on):
            self._pending = False
            kind, reason = result if isinstance(result, tuple) else ("done", None)
            if kind == "skip":
                if reason:
                    logger.info("锁屏联动跳过：%s", reason)
                    self.skipped.emit(reason)
                return
            self.state_applied.emit(d, on)

        def _on_error(error, d=did, on=turn_on):
            self._pending = False
            logger.warning("锁屏联动执行失败 did=%s on=%s: %s", d, on, error)
            self.failed.emit(str(error))

        self._jobs.submit(
            _work,
            on_success=_on_success,
            on_error=_on_error,
            priority=PRIORITY_CONTROL,
        )
