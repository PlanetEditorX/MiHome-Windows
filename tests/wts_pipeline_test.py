# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""WTS 消息管道回归（真实窗口子系统，离屏跑不了）。

离屏平台（QT_QPA_PLATFORM=offscreen）下 QWidget.winId() 恒为 0，
WTSRegisterSessionNotification 必然失败，因此本测试由
`tests/lock_test.py` 以子进程方式拉起，且子进程**不设** offscreen。

锁死的链路：注册窗口 -> 应用级原生事件过滤 -> MSG 解析 ->
状态机去重 -> locked/unlocked 信号。三个曾真实踩过的坑：

1. ctypes.wintypes.MSG 的句柄字段是 **hWnd（大写 W）**，写成
   msg.hwnd 会被 except 静默吞掉，表现为「注册成功但永远收不到事件」；
2. SendMessage 直达 WndProc、不过消息队列，Qt 过滤层看不见，
   验证必须用 PostMessageW；
3. WM_WTSSESSION_CHANGE 必须等于 **0x02A8**（WinUser.h 权威值）——
   曾误写成 0x02B1，系统真实投递的消息永远匹配不上。

因此本文件的输入（消息号/wParam）全部硬编码权威值，绝不 import
被测模块的常量：用错的投、用错的比，会自欺欺人全绿。
"""

import sys

from tests._isolate import isolate

isolate()

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

# 权威值（WinUser.h / WTSAPI），与实现无关
WM_WTSSESSION_CHANGE = 0x02A8  # noqa: N816
WTS_SESSION_LOCK = 7           # noqa: N816
WTS_SESSION_UNLOCK = 8         # noqa: N816

from app.core import session_watch as sw  # noqa: E402


def main() -> int:
    # 常量守门：实现改了名可以，改错值不行
    assert sw.WM_WTSSESSION_CHANGE == WM_WTSSESSION_CHANGE, (
        f"WM_WTSSESSION_CHANGE 应为 {WM_WTSSESSION_CHANGE:#x}，"
        f"实际 {sw.WM_WTSSESSION_CHANGE:#x}")
    assert sw.WTS_SESSION_LOCK == WTS_SESSION_LOCK
    assert sw.WTS_SESSION_UNLOCK == WTS_SESSION_UNLOCK

    watcher = sw.SessionLockWatcher()
    events: list[str] = []
    watcher.locked.connect(lambda: events.append("locked"))
    watcher.unlocked.connect(lambda: events.append("unlocked"))
    watcher.start()

    if not watcher._wts_registered:
        # 个别环境（无窗口子系统/服务被禁）注册失败属预期，轮询兜底不受影响
        print("SKIP: WTS 注册失败（无窗口子系统？），轮询兜底仍生效")
        watcher.stop()
        return 0

    hwnd = watcher.notify_hwnd()
    assert hwnd, "已注册但取不到通知窗口句柄"

    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    def post(wparam: int) -> None:
        ok = user32.PostMessageW(
            wintypes.HWND(hwnd), WM_WTSSESSION_CHANGE, wparam, 0)
        assert ok, f"PostMessage 失败 wParam={wparam}"
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)

    # 基线取决于跑测试时的真实会话状态：如果电脑正锁着（LogonUI 在跑），
    # start() 取的基线就是 locked，紧接着的 LOCK 会被去重成"无变化"，
    # 看起来像管道丢了第一个事件。先发一个 UNLOCK 把状态归一化。
    if watcher.is_locked:
        post(WTS_SESSION_UNLOCK)
        assert watcher.is_locked is False, "基线归一化失败"
        events.clear()

    # 真实场景顺序：锁屏 -> 解锁 -> 锁屏
    post(WTS_SESSION_LOCK)
    assert watcher.is_locked is True, "锁屏消息未驱动状态机"
    post(WTS_SESSION_UNLOCK)
    assert watcher.is_locked is False, "解锁消息未驱动状态机"
    post(WTS_SESSION_LOCK)
    assert events == ["locked", "unlocked", "locked"], \
        f"消息管道异常: {events}"
    # 重复锁屏不得重复发射（双通道会互相喂同值）
    post(WTS_SESSION_LOCK)
    assert events == ["locked", "unlocked", "locked"], \
        f"重复消息未去重: {events}"

    watcher.stop()
    assert not watcher._poll_timer.isActive(), "stop 后轮询仍在跑"
    print("WTS 消息管道 OK（注册/过滤/解析/状态机/去重）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
