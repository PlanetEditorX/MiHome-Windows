# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""米家自动化卡片（主页「自动化」选项卡）。

与场景卡片的区别：场景是「点一下执行」，自动化是「有个云端总开关」。
所以右侧放一枚主题色开关而不是运行按钮，拨动即等价于在米家 APP 里
启用/停用那条自动化。

卡片比设备卡宽（自动化名是米家自动生成的，往往三十多个字符），
两行标题才放得下；宽度仍固定，列数由主窗口按实际宽度换算。
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.models import AutomationInfo
from app.siui.components.container import SiRowCard
from app.ui.device_card import _CARD_FIXED_HEIGHT
from app.ui.si_theme import SiColors, themed_switch

# 比设备卡（200）宽：米家自动生成的自动化名常在 30 字以上
CARD_WIDTH = 340
_MARGIN_H = 16
_SWITCH_W = 40
_GAP = 12
_TITLE_PT = 10
_SUB_PT = 9
# 正文可用宽度：卡片宽 - 左右边距 - 开关 - 文字与开关的间距
_TEXT_W = CARD_WIDTH - _MARGIN_H * 2 - _SWITCH_W - _GAP


def _title_font() -> QFont:
    return QFont("Microsoft YaHei UI", _TITLE_PT, QFont.Weight.DemiBold)


def _wrap_elided(text: str, font: QFont, width: int, max_lines: int) -> str:
    """按给定宽度换行，超过 max_lines 时在末行截断加省略号。

    QLabel 的 wordWrap 只负责换行、不负责截断，固定高度的卡片里长文本
    会把副标题挤出边界；这里先用 QTextLayout 按真实换行规则量一遍，
    确认放不下才截，正常长度保持原文（不引入多余的省略号）。
    """
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    lines: list[str] = []
    try:
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start = line.textStart()
            lines.append(text[start:start + line.textLength()])
            if len(lines) > max_lines:
                break
    finally:
        layout.endLayout()
    if len(lines) <= max_lines:
        return text
    keep = "".join(lines[:max_lines]).rstrip()
    # 去掉一个字符再补省略号，避免「…」自己又撑出新的一行
    return keep[:-1] + "…"


def _sync_switch(switch) -> None:
    """siui 开关的进度是动画内部量，setChecked 后要手动对齐。"""
    switch.progress = 1 if switch.isChecked() else 0
    switch.progress_ani.fromProperty()


class AutomationCard(SiRowCard):
    """自动化卡片：右侧开关拨动后发出 toggled(scene_id, 目标状态)。"""

    toggled = Signal(str, bool)  # scene_id, 期望的启用状态

    def __init__(self, automation: AutomationInfo, parent=None):
        super().__init__(parent, self.LeftToRight)
        self.automation = automation
        self._hovered = False
        self._busy = False
        self._last_checked = automation.enabled

        self.style_data.background_color = QColor(SiColors.CARD)
        self.style_data.border_radius = 14.0
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(CARD_WIDTH, _CARD_FIXED_HEIGHT)
        self.muteStretchWidget()

        title_font = _title_font()
        self._title = QLabel(_wrap_elided(
            automation.name, title_font, _TEXT_W, 2))
        self._title.setFont(title_font)
        # 截断后允许 QLabel 自己重排；这里宽度由布局给，不设固定值
        self._title.setWordWrap(True)
        self._title.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")

        self._sub = QLabel(automation.action_text or "自动化")
        self._sub.setFont(QFont("Microsoft YaHei UI", _SUB_PT))
        self._sub.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent;")

        text_col = QWidget()
        text_col.setAttribute(Qt.WA_TranslucentBackground)
        text_lay = QVBoxLayout(text_col)
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(3)
        text_lay.addStretch(1)
        text_lay.addWidget(self._title)
        text_lay.addWidget(self._sub)
        text_lay.addStretch(1)

        self._switch = themed_switch()
        self._switch.setChecked(automation.enabled)
        _sync_switch(self._switch)
        self._switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch.toggled.connect(self._on_switch_toggled)

        lay = self.layout()
        lay.setContentsMargins(_MARGIN_H, 10, _MARGIN_H, 10)
        lay.addWidget(text_col)
        lay.addStretch(1)
        lay.addWidget(self._switch, alignment=Qt.AlignVCenter)

        self._apply_tooltip()

    # ---------- 状态 ----------

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._switch.setEnabled(not busy)

    def set_enabled_state(self, enabled: bool) -> None:
        """回写云端确认后的状态（不触发 toggled）。"""
        self._last_checked = enabled
        self._switch.blockSignals(True)
        try:
            self._switch.setChecked(enabled)
            _sync_switch(self._switch)
        finally:
            self._switch.blockSignals(False)
        self._apply_tooltip()

    def _apply_tooltip(self) -> None:
        state = "已启用" if self._last_checked else "已停用"
        action = self.automation.action_text or "自动化"
        self.setToolTip(f"{self.automation.name}\n{action}\n当前：{state}")

    def _on_switch_toggled(self, checked: bool) -> None:
        if self._busy:
            # 忙碌期间理论上点不动（已 setEnabled(False)），兜一层防止
            # 双击把同一台设备连拨两次
            return
        self._last_checked = checked
        self._apply_tooltip()
        self.toggled.emit(self.automation.scene_id, checked)

    # ---------- hover 观感 ----------

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        self._hovered = True
        self._apply_card_color()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        self._hovered = False
        self._apply_card_color()
        super().leaveEvent(event)

    def _apply_card_color(self) -> None:
        self.style_data.background_color = QColor(
            SiColors.CARD_HOVER if self._hovered else SiColors.CARD
        )
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt 命名约定)
        return QSize(CARD_WIDTH, _CARD_FIXED_HEIGHT)
