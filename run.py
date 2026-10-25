# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""程序入口。

用法: .venv\\Scripts\\python.exe run.py
"""

import os
import sys
import tempfile

from PySide6.QtCore import QLockFile, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from app import __version__
from app.ui.main_window import MainWindow

_SERVER_NAME = "MiHome-Windows"
_LOCK_NAME = "MiHome-Windows.lock"


def _set_console_visible(visible: bool) -> None:
    """Nuitka onefile 通过控制台解压文件，解压完成后隐藏控制台窗口；
    崩溃时再恢复，保证 start.bat 承诺的「截屏报错」可兑现。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 4 if visible else 0)  # SW_SHOWNOACTIVATE / SW_HIDE
    except Exception:
        pass


# 界面缩放乘数：QT_SCALE_FACTOR 是在 Qt 原生 DPI 之上再乘的全局
# 倍率，因此默认必须为 1.0——让每块屏幕跟随自己的系统缩放（200%
# 屏 DPR 2.0、125% 屏 DPR 1.25）。曾在此基础上叠加 1.25 的观感
# 基准，在 HiDPI 屏上产出 2.5×/1.5625× 的超大界面：窗口物理尺寸
# 超出屏幕、与 Qt 逻辑尺寸失配，只显示出界面的碎片（v0.6.1 修复）。
# 设置页的「界面缩放比例」是在此乘数之上的个人微调（默认 100%），
# 想放大观感的用户可自行调高，不再内置基准。
_BASE_UI_SCALE = 1.0


def _apply_ui_scale_env() -> None:
    """按「设置乘数 × DPI 校准系数」写入 QT_SCALE_FACTOR。

    必须在 QApplication 创建之前调用；Qt 只在初始化时读取该变量，
    更改后需重启生效。始终覆写：系统环境里可能残留外部设置的值，
    若不覆盖会与基准叠加导致界面异常巨大。

    MIHOME_DPI_FACTOR 是 _dpi_calibration_factor() 检测到 Qt 误读
    屏幕 DPI 时写入的一次性补偿系数，经重启继承（见其文档）。
    """
    from app.core.settings_store import get_ui_scale
    factor = float(os.environ.get("MIHOME_DPI_FACTOR", "1"))
    os.environ["QT_SCALE_FACTOR"] = f"{_BASE_UI_SCALE * factor * get_ui_scale():g}"


def _windows_primary_dpi() -> float | None:
    """主监视器的 Windows 权威 DPI（折算成以 96 为 1.0 的倍率）。

    取不到返回 None。这是系统里原生应用实际使用的缩放基准；
    Qt 的 QScreen.devicePixelRatio 在个别环境（RDP 虚拟显示）下
    会与它不一致，此时以本函数为准校准。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shcore = ctypes.windll.shcore
        point = wintypes.POINT(0, 0)
        hmon = user32.MonitorFromPoint(point, 1)  # MONITOR_DEFAULTTOPRIMARY
        if not hmon:
            return None
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x),
                                   ctypes.byref(dpi_y)) != 0:
            return None
        if dpi_x.value <= 0 or dpi_y.value <= 0:
            return None
        return (dpi_x.value + dpi_y.value) / 2 / 96.0
    except Exception:
        return None


def _dpi_calibration_factor(app) -> float | None:
    """检测 Qt 是否误读屏幕 DPI，返回需要的补偿系数（None = 正常）。

    远程桌面（RDP）等虚拟显示环境下，Qt 6 会把 192 DPI 的屏幕读成
    96（QScreen 几何尺寸正确、devicePixelRatio 恒为 1.0），界面整体
    只有同屏原生应用的一半大小。Qt 的 DPR 是界面缩放与窗口物理尺寸
    的唯一基准，读错时无任何本地设置可以纠正，只能用 Windows 权威
    DPI 反推补偿系数，通过 QT_SCALE_FACTOR 乘回去。

    只在「Qt 读数与 Windows 权威值整体成比例不一致」时校准；两者
    一致（含本地多屏异构缩放，Qt 读取正常）时返回 None，不影响。

    比较前必须先除掉 QT_SCALE_FACTOR：QScreen.devicePixelRatio() 是
    乘过该变量的最终值，而它同时承载用户的「界面缩放比例」微调。
    若不除，用户把缩放设成 150% 时 200% 屏会读到 3.0，被误判成 Qt
    误读并自动重启，重启后用户设置会被校准系数抵消掉。

    一次性：调用方把系数写入 MIHOME_DPI_FACTOR 并重启，新进程见到
    该变量即跳过，不会循环重启。
    """
    if os.environ.get("MIHOME_DPI_FACTOR"):
        return None
    screen = app.primaryScreen()
    if screen is None:
        return None
    try:
        injected = float(os.environ.get("QT_SCALE_FACTOR", "1") or 1)
    except ValueError:
        injected = 1.0
    if injected <= 0:
        injected = 1.0
    qt_dpr = screen.devicePixelRatio() / injected  # Qt 自己的原生读数
    win_dpr = _windows_primary_dpi()
    if win_dpr is None or qt_dpr <= 0:
        return None
    if abs(qt_dpr - win_dpr) <= max(0.02, 0.02 * win_dpr):
        return None
    return win_dpr / qt_dpr


def _install_qt_translator(app) -> None:
    """加载 Qt 自带控件的中文翻译，兼容源码与 Nuitka 打包两种布局。

    源码模式直接取 QLibraryInfo 指向的 PySide6/translations；打包后该
    目录被复制到 exe 旁边的 PySide6/translations，QLibraryInfo 不一定
    指得准，这里按候选路径逐个找，找到就装。
    """
    from pathlib import Path

    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    name = f"qtbase_{QLocale.system().name()}.qm"
    candidates: list[Path] = []
    try:
        candidates.append(
            Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / name)
    except Exception:
        pass
    exe_dir = Path(sys.executable).parent
    candidates.extend((
        exe_dir / "translations" / name,
        exe_dir / "PySide6" / "translations" / name,
    ))
    for path in candidates:
        if not path.is_file():
            continue
        translator = QTranslator(app)
        if translator.load(str(path)):
            app.installTranslator(translator)
            # 挂到 app 上保活：局部变量被回收后翻译会失效
            app._qt_translator = translator  # type: ignore[attr-defined]
            return


def main() -> int:
    # 日志先落地：控制台在运行期是隐藏的，异常与失败只能靠文件留存
    from app import setup_logging
    setup_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    _apply_ui_scale_env()
    app = QApplication(sys.argv)

    # Qt 自带控件的文案（取色板、消息框按钮等）按系统语言加载翻译：
    # 不加载时 QColorDialog 会整屏英文（"Basic colors"/"OK"/"Cancel"）
    _install_qt_translator(app)

    # 主题必须在创建任何控件之前生效：调色板决定全部内联样式的取值
    from app.core.settings_store import get_theme_mode
    from app.ui.theme_service import apply_theme
    # apply_theme 内部已设置全局 QSS，无需重复应用
    apply_theme(get_theme_mode())

    # 原生 QToolTip 在系统深色模式下始终是黑底，QSS 覆盖不稳定；
    # 统一换成自绘提示窗，配色跟随应用主题
    from app.ui.tooltip import TooltipController
    app._tooltip_controller = TooltipController(app)  # type: ignore[attr-defined]

    # 滚轮滚动统一走缓动动画，避免原生逐条跳变的抽帧感
    from app.ui.smooth_scroll import SmoothScrollFilter
    app._smooth_scroll = SmoothScrollFilter(app)  # type: ignore[attr-defined]

    # 单实例：仅允许一个进程，二次启动唤起已有窗口。
    # 自重启（MIHOME_RESTARTED=1）时跳过：旧进程即将退出，
    # 此时锁/server 尚在，走正常单实例会被当成「唤起旧窗口」。
    server = None
    if os.environ.get("MIHOME_RESTARTED") != "1":
        lock_path = os.path.join(tempfile.gettempdir(), _LOCK_NAME)
        lock = QLockFile(lock_path)
        # 默认 30s 视为过期，若上次崩溃残留可自动接管
        if not lock.tryLock(0):
            # 尝试唤起已有实例
            sock = QLocalSocket()
            sock.connectToServer(_SERVER_NAME)
            if sock.waitForConnected(400):
                try:
                    sock.write(b"show")
                    sock.waitForBytesWritten(300)
                except Exception:
                    pass
                try:
                    sock.disconnectFromServer()
                except Exception:
                    pass
                return 0
            # 连接失败视为残留锁/服务，强制清理后重试一次
            try:
                QLocalServer.removeServer(_SERVER_NAME)
            except Exception:
                pass
            try:
                lock.unlock()
            except Exception:
                pass
            if not lock.tryLock(0):
                return 0
        # 首实例：持有锁并监听唤起请求
        QLocalServer.removeServer(_SERVER_NAME)
        server = QLocalServer()
        # 监听失败不影响主流程，仅失去二次唤起能力
        try:
            server.listen(_SERVER_NAME)
        except Exception:
            pass
        # 防止被 GC 回收
        app._single_instance_lock = lock  # type: ignore[attr-defined]
        app._single_instance_server = server  # type: ignore[attr-defined]

    # 字体抗锯齿：优先抗锯齿而非网格对齐，明显减少小字号锯齿
    font = QFont("Microsoft YaHei UI", 9)
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)
    app.setApplicationName("MiHome-Windows")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    # DPI 校准：Qt 在 RDP 等虚拟显示环境下会误读屏幕 DPI（192 读成
    # 96），界面只有同屏原生应用一半大且无本地设置可纠正。检测到
    # 误读时带上补偿系数重启一次；本地多屏异构缩放下 Qt 读取正常，
    # 此处为无操作。重启发起失败则撤掉系数按当前缩放继续运行
    _calib_factor = _dpi_calibration_factor(app)
    if _calib_factor is not None:
        os.environ["MIHOME_DPI_FACTOR"] = f"{_calib_factor:g}"
        from app.ui.restart import restart_app
        if restart_app():
            return 0
        os.environ.pop("MIHOME_DPI_FACTOR", None)

    window = MainWindow()

    # 二次启动唤起：显示并置顶已有窗口
    def _on_show_request() -> None:
        try:
            while server.hasPendingConnections():  # type: ignore[union-attr]
                s = server.nextPendingConnection()
                if s is not None:
                    try:
                        s.waitForReadyRead(30)
                    except Exception:
                        pass
                    try:
                        s.deleteLater()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            window.show()
            window.raise_()
            window.activateWindow()
            if window.isMinimized():
                window.showNormal()
        except Exception:
            pass

    try:
        server.newConnection.connect(_on_show_request)  # type: ignore[union-attr]
    except Exception:
        pass

    from app.core.settings_store import get_start_minimized, get_minimize_to_tray
    # 以托盘方式静默启动：完全不显示主窗口（设备列表在后台加载，
    # 卡片网格延迟到首次唤出才构建，常驻内存显著更低）。曾用
    # 「透明 show 再隐藏」初始化原生窗口，但那会连带构建整页卡片
    _start_hidden = get_start_minimized() and get_minimize_to_tray()
    if not _start_hidden:
        window.show()
    # 登录检查放在 show 之后发起：回调经队列信号回到主线程，
    # 先启动事件循环可保证首次回调不被阻塞在窗口绘制之前
    window.start()
    if _start_hidden:
        # 事件循环转起来、后台加载完成后修剪工作集
        from app import trim_working_set

        QTimer.singleShot(500, trim_working_set)
    return app.exec()


if __name__ == "__main__":
    _set_console_visible(False)
    try:
        code = main()
    except BaseException:
        # 崩溃时恢复控制台并保留报错内容等用户确认，
        # 避免窗口一闪而过、用户按 start.bat 提示截屏时什么都看不到
        _set_console_visible(True)
        import traceback
        traceback.print_exc()
        try:
            input("程序异常退出，按回车键关闭…")
        except Exception:
            pass
        code = 1
    raise SystemExit(code)
