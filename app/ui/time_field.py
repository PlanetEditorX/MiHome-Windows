# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""现代风格时间字段：HH:mm 输入框 + 右内侧自绘上下步进按钮。

原生 QTimeEdit 的 spin 按钮由系统样式（QStyle）绘制，是一对带硬边
凸起的小方块，和设置页里其他胶囊控件（下拉框、按钮、开关）完全
不同源，截图里一眼就能看出是「系统控件混进来」。

这里保留 QTimeEdit 的输入/键盘/合法性校正能力（重写一套时间输入
容易出错），只做两件事：

1. QSS 把原生 up/down-button 宽度压成 0，彻底不画；
2. 在字段右内侧叠两个 chevron 小按钮，圆角、hover 底、图标颜色全部
   走主题调色板，与下拉框的 chevron 同款图标（mdi.chevron-up/down）。

步进 5 分钟，跨午夜自动回绕（23:55 → 00:00）。滚轮刻意禁用：设置页
本身可滚动，指针划过时间框就改值属于误操作（与 themed_combo 同约定）。
"""

from PySide6.QtCore import QEvent, QSize, QTime, Qt, Signal
from PySide6.QtWidgets import QTimeEdit, QToolButton, QWidget

import qtawesome as qta

from app.ui.si_theme import SiColors

# 按钮步进：5 分钟一档（与定时对话框的分钟档位一致）
STEP_SECONDS = 300
_FIELD_H = 32
_FIELD_W = 84
_BTN_W, _BTN_H = 22, 14
_EDGE = 2


class _WheelSafeTimeEdit(QTimeEdit):
    """禁用滚轮的 QTimeEdit：事件交给父级，滚动的是设置页而不是时间。"""

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class TimeField(QWidget):
    """时间输入字段（HH:mm）。

    对外 API 与 QTimeEdit 常用部分保持一致（``time()`` / ``setTime()`` /
    ``setEnabled()`` / ``timeChanged``），设置页里可直接替换原控件。
    """

    timeChanged = Signal(QTime)

    def __init__(self, value: QTime | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._edit = _WheelSafeTimeEdit(self)
        self._edit.setDisplayFormat("HH:mm")
        self._edit.setTime(value if value is not None else QTime(8, 0))
        self._edit.setAlignment(Qt.AlignmentFlag.AlignLeft
                                | Qt.AlignmentFlag.AlignVCenter)
        self._edit.setFixedHeight(_FIELD_H)
        self._edit.timeChanged.connect(self.timeChanged)

        self._up = self._make_button("mdi.chevron-up", "加 5 分钟", STEP_SECONDS)
        self._down = self._make_button("mdi.chevron-down", "减 5 分钟",
                                       -STEP_SECONDS)

        self.setFixedSize(_FIELD_W, _FIELD_H)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.apply_theme()

    # ── 构建 ────────────────────────────────────────────────────────
    def _make_button(self, icon_name: str, tip: str, delta: int) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("timeFieldStep")
        btn.setFixedSize(_BTN_W, _BTN_H)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 不抢输入框焦点
        btn.setToolTip(tip)
        btn.setAutoRaise(True)
        # 长按连续步进：现代数值控件的标配，比连点轻松
        btn.setAutoRepeat(True)
        btn.setAutoRepeatDelay(400)
        btn.setAutoRepeatInterval(80)
        btn._icon_name = icon_name  # type: ignore[attr-defined]
        btn.clicked.connect(lambda: self._nudge(delta))
        btn.installEventFilter(self)  # hover 时图标换主题色
        return btn

    # ── 取值 / 赋值 ────────────────────────────────────────────────
    def time(self) -> QTime:
        return self._edit.time()

    def setTime(self, value: QTime) -> None:  # noqa: N802
        self._edit.setTime(value)

    def _nudge(self, delta: int) -> None:
        # QTime.addSecs 自动按 24h 回绕，23:55 + 5min 得到 00:00
        self._edit.setTime(self._edit.time().addSecs(delta))

    # ── 主题 ────────────────────────────────────────────────────────
    def apply_theme(self) -> None:
        """按当前调色板上色（构造期与主题切换都必须调用）。"""
        self._edit.setStyleSheet(f"""
            QTimeEdit {{
                background: {SiColors.SURFACE};
                border: 1px solid {SiColors.LINE};
                border-radius: 8px;
                padding: 4px 26px 4px 10px;
                color: {SiColors.TEXT_PRIMARY};
                font-size: 9pt;
            }}
            QTimeEdit:hover, QTimeEdit:focus {{ border-color: {SiColors.THEME}; }}
            QTimeEdit:disabled {{ color: {SiColors.TEXT_DISABLED}; }}
            QTimeEdit::up-button, QTimeEdit::down-button {{
                subcontrol-origin: border;
                width: 0px; border: none; background: transparent;
            }}
            QTimeEdit::up-arrow, QTimeEdit::down-arrow {{
                width: 0px; height: 0px; image: none;
            }}
        """)
        hover = _rgba(SiColors.THEME, 0.16)
        press = _rgba(SiColors.THEME, 0.28)
        self._up.setStyleSheet(_btn_qss(hover, press))
        self._down.setStyleSheet(_btn_qss(hover, press))
        self._apply_icons()

    def _apply_icons(self) -> None:
        enabled = self.isEnabled()
        base = SiColors.TEXT_SECONDARY if enabled else SiColors.TEXT_DISABLED
        for btn in (self._up, self._down):
            hovering = getattr(btn, "_hovering", False) and enabled
            color = SiColors.THEME if hovering else base
            btn.setIcon(qta.icon(btn._icon_name, color=color))  # type: ignore[attr-defined]
            btn.setIconSize(QSize(14, 14))

    # ── 事件 ────────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._edit.setGeometry(0, 0, self.width(), _FIELD_H)
        x = self.width() - _EDGE - _BTN_W
        y0 = (self.height() - _BTN_H * 2) // 2
        self._up.move(x, y0)
        self._down.move(x, y0 + _BTN_H)
        self._up.raise_()
        self._down.raise_()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange:
            self._apply_icons()
        super().changeEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj in (self._up, self._down):
            kind = event.type()
            if kind in (QEvent.Type.Enter, QEvent.Type.Leave):
                obj._hovering = kind == QEvent.Type.Enter  # type: ignore[attr-defined]
                self._apply_icons()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 点字段空白处也能直接编辑，省得瞄准文本
        self._edit.setFocus(Qt.FocusReason.MouseFocusReason)
        self._edit.selectAll()
        super().mousePressEvent(event)


def _btn_qss(hover: str, press: str) -> str:
    return (
        "QToolButton { background: transparent; border: none;"
        " border-radius: 4px; }"
        f"QToolButton:hover {{ background: {hover}; }}"
        f"QToolButton:pressed {{ background: {press}; }}"
        "QToolButton:disabled { background: transparent; }"
    )


def _rgba(hex_color: str, alpha: float) -> str:
    """#rrggbb -> rgba()，用于主题色淡底（hover/pressed）。"""
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return hex_color
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"
