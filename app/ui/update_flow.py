# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""版本检查的界面流程：发起检查 → 有新版本弹对话框，否则按场景反馈。

自动（启动时）与手动（关于页/设置入口）共用这一条链路，仅反馈策略
不同：自动检查在「无新版本」与「网络失败」时完全静默，不打扰
用户；手动检查两种情况都要给出明确回显，否则用户无法区分
「没新版」与「断网了」。
"""

from app.core.update_checker import UpdateChecker, is_newer
from app.ui.toast import Toast


def check_update(parent, manual: bool) -> None:
    """发起一次检查；parent 用于挂 checker 与弹对话框。"""
    checker = UpdateChecker(parent)

    def _finish(info, error) -> None:
        checker.deleteLater()
        if error is not None:
            if manual:
                Toast.info(parent, f"检查更新失败：{error}", 4000)
            return
        from app import __version__
        if info is None or not is_newer(info.tag, __version__):
            if manual:
                Toast.info(parent, "当前已是最新版本", 2500)
            return
        prompt_new_version(parent, info)

    checker.check_finished.connect(_finish)
    checker.check()


def prompt_new_version(parent, info) -> None:
    """弹「发现新版本」对话框：版本对比 + Markdown 更新日志 + 前往下载。"""
    from app.ui.update_dialog import UpdateDialog

    # 主窗体可能已隐藏到托盘（启动静默场景），给隐藏父级弹
    # 模态框位置会落在不可见区域，此时改挂屏幕中央
    host = None if (parent is not None and parent.isHidden()) else parent
    dlg = UpdateDialog(info, host)
    try:
        dlg.exec()
    finally:
        dlg.deleteLater()


def notify_pending_update_failure(parent) -> None:
    """上次应用内更新没成功时提示一次。

    替换脚本发现安装目录被占用（进程/杀软还没放手）时会整体放弃、
    保留旧版本并启动它，同时写标记。现在不会留下半新半旧的目录，
    所以这里说清「还是旧版本、可以重试」，并给出日志位置供排查。
    """
    from app.core import updater

    marker = updater.failure_flag_path()
    try:
        if not marker.exists():
            return
        marker.unlink()
    except OSError:
        return
    Toast.info(
        parent,
        "上次更新未完成（安装目录被占用或权限不足），仍是原版本。"
        "请关闭其他窗口后重试，或到 GitHub 手动下载"
        f"（日志：%TEMP%\\{updater.UPDATE_LOG}）",
        8000,
    )
