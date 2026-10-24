# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""Windows 会话锁屏 / 解锁状态监听（供「锁屏联动」使用）。

双通道设计，互相兜底：

1. **WTS 会话通知**（主通道，事件驱动、零延迟）
   对隐藏的顶层窗口调 `WTSRegisterSessionNotification`，在原生事件
   过滤器里接 `WM_WTSSESSION_CHANGE`（wParam = WTS_SESSION_LOCK(7) /
   WTS_SESSION_UNLOCK(8)）。消息发给注册过的顶层窗口，隐藏窗口同样
   收得到。

2. **LogonUI 进程轮询**（兜底通道，2 秒）
   锁屏界面由 LogonUI.exe 承载，用 Toolhelp 快照枚举进程名即可判定。
   存在的意义：WTS 注册失败（极少数会话/远程场景）、以及**休眠唤醒**
   后通知丢失的情形——睡醒时若仍锁屏，轮询能补上状态。

两路都汇入同一个状态机 `_set_locked`：只有状态**发生变化**才发信号，
重复通知天然去重；初始启动只取基线不发射，避免「启动时正好锁屏」
被当成一次解锁事件。

非 Windows 平台：start() 直接返回，两个信号永远不发（联动自动失效）。
"""

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, Signal

logger = logging.getLogger(__name__)

# ---- Windows 常量 ----
# WM_WTSSESSION_CHANGE = 0x02A8（WinUser.h）。注意勿与 0x02B1 混淆：
# 写错的后果是过滤器永远等不到系统真实投递的消息，表现为「注册
# 成功但收不到任何事件」，且测试若也用模块常量投递会自欺欺人。
WM_WTSSESSION_CHANGE = 0x02A8
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0

# 兜底轮询间隔：锁屏联动不是安全联锁，2 秒延迟用户无感，
# 同时把常驻开销压到可忽略（一次 Toolhelp 快照 < 1ms）
_POLL_INTERVAL_MS = 2000

# Toolhelp 进程快照常量
_TH32CS_SNAPPROCESS = 0x00000002


def is_supported() -> bool:
    """仅 Windows 提供会话锁屏通知能力。"""
    return sys.platform == "win32"


class _ProcessEntry32(ctypes.Structure):
    """PROCESSENTRY32 的精简声明（只用到进程名与 PID 字段）。"""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * wintypes.MAX_PATH),
    ]


def logon_ui_running() -> bool | None:
    """枚举进程判断锁屏界面是否在场。

    True=锁屏中；False=已解锁；None=枚举失败（调用方应保持原状态）。
    锁屏时系统会拉起 LogonUI.exe 承载登录/锁屏界面，这是判断会话
    锁定的经典手段，无需提权、无需 WMI。
    """
    if not is_supported():
        return None
    kernel32 = ctypes.windll.kernel32
    # 64 位下句柄是 8 字节：restype 必须显式声明，默认 c_int 会截断高位
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    invalid = wintypes.HANDLE(-1).value
    if not snapshot or snapshot == invalid:
        return None
    entry = _ProcessEntry32()
    entry.dwSize = ctypes.sizeof(_ProcessEntry32)
    try:
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.decode("utf-8", "ignore").lower() == "logonui.exe":
                return True
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
        return False
    except Exception:
        logger.debug("枚举进程判断锁屏状态失败", exc_info=True)
        return None
    finally:
        kernel32.CloseHandle(snapshot)


class _WtsNativeFilter(QAbstractNativeEventFilter):
    """接住隐藏窗口上的 WM_WTSSESSION_CHANGE，转给 watcher。

    过滤器装在 QGuiApplication 上（Qt 只提供应用级原生事件过滤），
    因此会收到进程内所有窗口的消息；按 msg.hwnd 过滤出注册过的那一个。
    """

    def __init__(self, watcher: "SessionLockWatcher"):
        super().__init__()
        self._watcher = watcher

    def nativeEventFilter(self, eventType, message):  # noqa: N802
        try:
            if eventType in (b"windows_generic_MSG", "windows_generic_MSG",
                             b"windows_dispatcher_MSG", "windows_dispatcher_MSG"):
                # PySide6 传入的 message 是窗口消息结构体（MSG）的地址。
                # 注意字段名大小写：ctypes.wintypes.MSG 的窗口句柄字段
                # 是 hWnd（不是 hwnd），写错会被下面的 except 静默吞掉
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_WTSSESSION_CHANGE:
                    hwnd = self._watcher.notify_hwnd()
                    if hwnd is None or msg.hWnd == hwnd:
                        if msg.wParam == WTS_SESSION_LOCK:
                            self._watcher._set_locked(True)
                        elif msg.wParam == WTS_SESSION_UNLOCK:
                            self._watcher._set_locked(False)
        except Exception:
            # 原生事件里的任何异常都不允许外抛：Qt 会直接终止进程
            logger.debug("处理 WTS 原生事件失败", exc_info=True)
        return False, 0


class SessionLockWatcher(QObject):
    """监听会话锁定/解锁，状态变化时发信号。

    用法:
        watcher = SessionLockWatcher()
        watcher.locked.connect(...)
        watcher.unlocked.connect(...)
        watcher.start()   # 登录完成后调用
        watcher.stop()    # 退出时调用
    """

    locked = Signal()
    unlocked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._locked: bool | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._on_poll)
        # WTS 注册所需的隐藏顶层窗口与事件过滤器，start() 时惰性创建
        self._notify_window = None
        self._native_filter: _WtsNativeFilter | None = None
        self._wts_registered = False

    # ---------- 状态 ----------

    @property
    def is_locked(self) -> bool | None:
        """当前是否锁屏；None = 尚未取得基线（未启动或探测失败）。"""
        return self._locked

    def notify_hwnd(self) -> int | None:
        """注册了会话通知的窗口句柄；未注册时返回 None。"""
        if self._notify_window is None:
            return None
        try:
            return int(self._notify_window.winId())
        except Exception:
            return None

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """取得基线并开始监听。已启动时重复调用无副作用。"""
        if not is_supported():
            logger.info("非 Windows 平台，锁屏监听不可用")
            return
        if self._notify_window is not None or self._poll_timer.isActive():
            return
        # 基线：只记录不发射，避免启动瞬间的状态被当成一次事件
        self._locked = logon_ui_running()
        self._register_wts()
        # 轮询始终开启：既是 WTS 注册失败时的主通道，
        # 也是休眠唤醒后补判状态的兜底（WTS 通知在睡眠期间会丢）
        self._poll_timer.start()
        logger.info("锁屏监听已启动（WTS 通知=%s，轮询兜底 2s）",
                    "已注册" if self._wts_registered else "未注册")

    def stop(self) -> None:
        """注销通知并停止轮询；状态复位为未知。"""
        self._poll_timer.stop()
        self._unregister_wts()
        self._locked = None

    # ---------- WTS 会话通知 ----------

    def _register_wts(self) -> bool:
        """创建隐藏顶层窗口、注册会话通知并挂上应用级原生事件过滤器。

        任何一步失败都返回 False（轮询兜底顶上），且**必须把已注册的
        窗口注销掉**——半途失败若不清理，WTS 注册会泄漏到会话结束。
        """
        try:
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QWidget

            window = QWidget()
            window.setWindowTitle("MiHome-Windows-SessionWatch")
            hwnd = int(window.winId())
            wts = ctypes.windll.wtsapi32
            wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            wts.WTSRegisterSessionNotification.restype = wintypes.BOOL
            if not wts.WTSRegisterSessionNotification(
                    wintypes.HWND(hwnd), wintypes.DWORD(NOTIFY_FOR_THIS_SESSION)):
                window.deleteLater()
                return False
            # 先落地窗口引用：后续任何异常都由 except 里的注销兜底
            self._notify_window = window
            self._wts_registered = True
            self._native_filter = _WtsNativeFilter(self)
            # 原生事件过滤器只能装在应用对象上（Qt 无窗口级安装），
            # 收到消息后按 msg.hwnd 过滤（见 _WtsNativeFilter）
            app = QGuiApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self._native_filter)
            return True
        except Exception:
            logger.info("WTS 会话通知注册失败，仅靠轮询兜底", exc_info=True)
            self._unregister_wts()
            return False

    def _unregister_wts(self) -> None:
        window = self._notify_window
        if window is None:
            return
        try:
            if self._wts_registered:
                ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification(
                    wintypes.HWND(int(window.winId())))
        except Exception:
            logger.debug("注销 WTS 会话通知失败", exc_info=True)
        finally:
            try:
                from PySide6.QtGui import QGuiApplication

                app = QGuiApplication.instance()
                if app is not None and self._native_filter is not None:
                    app.removeNativeEventFilter(self._native_filter)
            except Exception:
                logger.debug("移除原生事件过滤器失败", exc_info=True)
            try:
                window.deleteLater()
            except Exception:
                pass
            self._notify_window = None
            self._native_filter = None
            self._wts_registered = False

    # ---------- 轮询兜底 ----------

    def _on_poll(self) -> None:
        state = logon_ui_running()
        if state is None:
            return  # 枚举失败：保持原状态，下一轮再试
        self._set_locked(state)

    # ---------- 状态机 ----------

    def _set_locked(self, locked: bool | None) -> None:
        """唯一的状态入口：变化才发信号，重复通知天然去重。"""
        if locked is None or locked == self._locked:
            return
        self._locked = locked
        if locked:
            logger.info("检测到会话锁屏")
            self.locked.emit()
        else:
            logger.info("检测到会话解锁")
            self.unlocked.emit()
