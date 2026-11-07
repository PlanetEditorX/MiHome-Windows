# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""新版本提示对话框：版本对比 + GitHub Release 更新日志（Markdown 渲染）。

与关于页/设置页同一套扁平化观感：无边框圆角面板、遮罩、淡入淡出。
更新日志直接取 release 正文，用 QTextDocument 的 Markdown 渲染器展示，
标题/列表/粗体/行内代码/链接这些常用语法都能正常呈现。

更新动作分两条路：打包运行且 release 带 win64 资产时，「立即更新」
在对话框内下载并替换安装目录后自动重启；否则（源码运行、或该版本
没挂资产）主按钮退回「前往下载」，打开浏览器由用户手动处理。
"""

import logging

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

import qtawesome as qta

from app import __version__
from app.core import updater
from app.ui.overlay_dialog import OverlayDialog
from app.ui.si_theme import (
    PRIMARY_TEXT,
    SiColors,
    primary_button_qss,
    secondary_button_qss,
)
from app.ui.toast import Toast

_PANEL_W = 480
_PANEL_H = 520

_logger = logging.getLogger(__name__)


class UpdateDialog(OverlayDialog):
    """发现新版本：展示更新日志，一键前往下载。"""

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self._info = info
        self.setWindowTitle("发现新版本")

        panel = self._panel
        panel.setFixedSize(_PANEL_W, _PANEL_H)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(14)

        # ---- 标题栏：图标 + 品牌名 + 标题 ----
        head = QHBoxLayout()
        head.setSpacing(10)
        self._icon = QLabel()
        self._icon.setFixedSize(30, 30)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._icon)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        # 品牌名放在标题左侧：只写「发现新版本」看不出是哪个软件在更新
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._brand = QLabel("米家 - Windows")
        self._brand.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.DemiBold))
        title_row.addWidget(self._brand)
        self._title = QLabel("发现新版本")
        self._title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.DemiBold))
        title_row.addWidget(self._title)
        title_row.addStretch(1)
        title_col.addLayout(title_row)
        self._version_line = QLabel(
            f"当前 v{__version__}　→　最新 {info.tag}")
        title_col.addWidget(self._version_line)
        head.addLayout(title_col, stretch=1)
        lay.addLayout(head)

        # ---- 元信息：版本名 / 发布日期（两者都缺时整行收起）----
        self._meta = QLabel(self._meta_text())
        self._meta.setWordWrap(True)
        self._meta.setVisible(bool(self._meta.text()))
        lay.addWidget(self._meta)

        # ---- 更新日志：Markdown 渲染 ----
        self._notes = QTextBrowser()
        self._notes.setOpenExternalLinks(True)
        self._notes.setFrameShape(QFrame.Shape.NoFrame)
        self._notes.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._notes.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self._notes, stretch=1)
        self._render_notes()

        # ---- 更新进度：默认隐藏，点「立即更新」后出现 ----
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(5)
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        lay.addWidget(self._status)

        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        self._later_btn = QPushButton("稍后提醒")
        self._later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._later_btn)
        # 文案随「能否自更新」而定：能就地装就写「立即更新」，
        # 否则保持「前往下载」，不做无谓的承诺
        self._self_update = updater.can_self_update(info)
        # 上一次已经下完（或解压完）但没装成：这次直接装，不再重新下载
        self._has_cache = self._self_update and updater.has_pending_package(info)
        self._download_btn = QPushButton(
            " 立即更新" if self._self_update else " 前往下载")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.setIconSize(QSize(18, 18))
        self._download_btn.clicked.connect(self._start_update)
        if self._self_update and self._has_cache:
            # 这句话本身就是「上次没白下」的交代：重试不会再走一遍网络
            self._download_btn.setToolTip(
                "上次已下载好更新包，这次直接安装（失败也不会重新下载）")
        elif self._self_update and info.asset_size:
            # 在按下之前就让用户知道要下多少、之后会发生什么
            self._download_btn.setToolTip(
                f"下载约 {info.asset_size / 1048576:.0f} MB，"
                "完成后自动重启应用")
        btn_row.addWidget(self._download_btn)
        lay.addLayout(btn_row)

        self._downloader = None
        self._apply_styles()

    # ---------- 内容 ----------

    def _meta_text(self) -> str:
        parts = []
        if self._info.name and self._info.name != self._info.tag:
            parts.append(self._info.name)
        date = str(self._info.published_at or "")[:10]
        if date:
            parts.append(f"发布于 {date}")
        return "　·　".join(parts)

    def _render_notes(self) -> None:
        """把 release 正文按 Markdown 渲染；无正文时给一句说明。"""
        body = (self._info.body or "").strip()
        if not body:
            body = "本次发布未提供更新说明，可前往 GitHub 查看完整发布页。"
        # 文档默认样式在 setMarkdown 时求值：先挂样式再渲染，
        # 直接 setMarkdown 比"先转 HTML 再 setHtml"少一层转换
        self._notes.document().setDefaultStyleSheet(self._notes_qss())
        self._notes.setMarkdown(body)

    def _notes_qss(self) -> str:
        return f"""
            body {{ color: {SiColors.TEXT_PRIMARY}; }}
            p, li {{ font-size: 10pt; }}
            h1 {{ font-size: 15pt; }}
            h2 {{ font-size: 13pt; }}
            h3 {{ font-size: 11pt; }}
            a {{ color: {SiColors.THEME}; text-decoration: none; }}
            /* 代码块用窗口底色：与卡片色差更大，深色主题下也能一眼区分 */
            code {{ background-color: {SiColors.WINDOW_BG}; font-size: 9pt; }}
            pre {{ background-color: {SiColors.WINDOW_BG}; font-size: 9pt; }}
        """

    def _open_download(self) -> None:
        QDesktopServices.openUrl(QUrl(self._info.url))
        self.accept()

    # ---------- 应用内更新 ----------

    def _start_update(self) -> None:
        """主按钮：能就地更新就下载安装，否则退回打开下载页。"""
        dest = updater.install_dir()
        if not self._self_update or dest is None:
            self._open_download()
            return
        # 装到 Program Files 又没提权时，替换注定失败：别让用户先下完
        # 80 MB 再看到「更新未完成」，直接退回手动下载
        if not updater.dir_writable(dest) or not updater.dir_writable(dest.parent):
            Toast.info(
                self, "安装目录没有写权限，已改为打开下载页，请手动解压覆盖",
                6000)
            self._open_download()
            return
        self._download_btn.setEnabled(False)
        self._download_btn.setText(" 更新中…")
        self._later_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._set_status("正在准备更新包…")
        self._downloader = updater.UpdateDownloader(
            self._info.asset_url, self._info.tag, self._info.asset_size, self)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.status.connect(self._set_status)
        self._downloader.finished.connect(self._on_downloaded)
        self._downloader.start()

    def _on_progress(self, done: int, total: int) -> None:
        mb = 1024 * 1024
        if total > 0:
            self._progress.setValue(min(100, int(done * 100 / total)))
        # 解压阶段也在用同一条进度条，此时 status 已被文字覆盖，
        # 这里只在下载阶段补「已下载 / 总大小」
        if total > 0 and self._status.text().startswith("正在下载"):
            self._set_status(
                f"正在下载更新包… {done / mb:.1f} / {total / mb:.1f} MB")
        elif total <= 0 and self._status.text().startswith("正在下载"):
            self._set_status(f"正在下载更新包… {done / mb:.1f} MB")

    def _on_downloaded(self, staging, error) -> None:
        if error is not None:
            # 半截的包留在缓存里，下次点更新会续传，不会从 0 MB 重来
            self._restore_after_failure(f"更新未完成：{error}")
            return
        dest = updater.install_dir()
        if dest is None:
            self._restore_after_failure("当前不是安装版，无法自动更新")
            return
        try:
            old = updater.apply_update(staging, dest)
        except Exception as exc:
            _logger.warning("update: apply failed: %s", exc)
            self._restore_after_failure(f"安装失败：{exc}（更新包已保存，重试无需重新下载）")
            return
        # 启动新版并观察几秒：启动即崩会被自动还原，这里就能告诉用户，
        # 而不是等他下次双击 exe 才发现程序打不开
        self._set_status(f"正在启动 {self._info.tag}…")
        if not updater.relaunch_and_verify(old, dest):
            self._restore_after_failure("新版本启动失败，已自动还原到原版本，可重试或手动下载")
            return
        self._progress.setValue(100)
        self._set_status(f"已更新到 {self._info.tag}，正在关闭旧窗口…")
        self._download_btn.setText(" 重启中…")
        # 留一小段时间让用户看清状态，再把进程让给新版本
        QTimer.singleShot(500, self._quit_for_update)

    def _quit_for_update(self) -> None:
        """关闭对话框并退出进程：新版本已在另一个进程里跑起来了。"""
        self.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _restore_after_failure(self, message: str) -> None:
        """下载/安装失败：把界面还原成可重试状态并说明原因。"""
        if self._downloader is not None:
            self._downloader.deleteLater()
            self._downloader = None
        self._download_btn.setEnabled(True)
        self._download_btn.setText(
            " 立即更新" if self._self_update else " 前往下载")
        self._later_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setVisible(False)
        Toast.info(self, message, 6000)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(True)

    # ---------- 样式 ----------

    def _apply_styles(self) -> None:
        """主题相关内联样式：构造与 retheme 共用。"""
        self._icon.setPixmap(
            qta.icon("mdi.rocket-launch", color=SiColors.THEME).pixmap(26, 26))
        self._icon.setStyleSheet("background: transparent;")
        self._brand.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        self._title.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        self._version_line.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent;"
            " font-size: 9pt;")
        self._meta.setStyleSheet(
            f"color: {SiColors.TEXT_MUTED}; background: transparent;"
            " font-size: 9pt;")
        self._notes.setStyleSheet(
            f"QTextBrowser {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 10px;"
            f" padding: 10px 12px; color: {SiColors.TEXT_PRIMARY}; }}"
            "QScrollBar:vertical { background: transparent; width: 6px;"
            " margin: 0 2px 0 0; }"
            f"QScrollBar::handle:vertical {{ background: {SiColors.SCROLLBAR};"
            " border-radius: 3px; min-height: 30px; }"
            f"QScrollBar::handle:vertical:hover {{ background: {SiColors.SCROLLBAR_HOVER}; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: none; }")
        self._later_btn.setStyleSheet(secondary_button_qss("8px 20px"))
        self._download_btn.setStyleSheet(primary_button_qss("8px 20px"))
        self._progress.setStyleSheet(
            f"QProgressBar {{ background: {SiColors.WINDOW_BG};"
            " border: none; border-radius: 2px; }"
            f"QProgressBar::chunk {{ background: {SiColors.THEME};"
            " border-radius: 2px; }}")
        self._status.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent;"
            " font-size: 9pt;")
        # 主按钮文字固定白色，图标也要同色；ON_THEME_TEXT 在浅色强调色下是深色
        self._download_btn.setIcon(
            qta.icon("mdi.download", color=PRIMARY_TEXT))

    def retheme(self) -> None:
        super().retheme()
        self._apply_styles()
        # 文档默认样式在 setMarkdown 时求值，主题切换要整篇重渲染
        self._render_notes()

    # ---------- 定位 ----------

    def resizeEvent(self, event) -> None:  # noqa: N802
        # 遮罩铺满与淡入由 OverlayDialog 处理，这里只负责面板居中；
        # 重复实现 showEvent 会让 _fade_in 跑两次，动画互相打架
        super().resizeEvent(event)
        x = (self.width() - self._panel.width()) // 2
        y = (self.height() - self._panel.height()) // 2
        self._panel.move(x, y)
