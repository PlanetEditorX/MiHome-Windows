# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""GitHub Releases 新版本检查：取最新 release 版本号与当前版本对比。

检查走独立后台线程而非 JobExecutor 串行队列：这是与米家无关的第三方
请求，混进设备任务队列会让弱网下最长十几秒的超时连带阻塞设备加载与
用户操作。结果经信号发回主线程，界面线程永不受网络等待影响。
"""

import re
import threading
from dataclasses import dataclass

import requests
from PySide6.QtCore import QObject, Signal

_REPO = "PlanetEditorX/MiHome-Windows"
LATEST_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{_REPO}/releases/latest"


@dataclass
class ReleaseInfo:
    tag: str
    url: str
    name: str
    # release 正文（GitHub Releases 里填的更新日志，Markdown 原文）
    body: str = ""
    # ISO 8601 发布时间，展示时只取日期
    published_at: str = ""
    # 打包产物（CI 产出的 win64 zip）的下载地址与元信息；release 未挂
    # 资产（如早期版本或只在网页发布）时三者都为空/0，此时界面回退到
    # 「打开浏览器下载页」，应用内更新不可用
    asset_url: str = ""
    asset_name: str = ""
    asset_size: int = 0


def _pick_asset(assets) -> tuple[str, str, int]:
    """从 release 资产里挑出 Windows 打包 zip：返回 (地址, 名字, 字节数)。

    只认 .zip 且文件名含 win64，避免误取源码 zip（GitHub 自动附带的
    Source code (zip) 也以 .zip 结尾，但名字里没有 win64）。
    """
    for item in assets or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        lower = name.lower()
        if not lower.endswith(".zip") or "win64" not in lower:
            continue
        url = str(item.get("browser_download_url") or "")
        if not url:
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        return url, name, size
    return "", "", 0


def fetch_latest_release() -> ReleaseInfo:
    """拉取仓库最新 release；网络失败/响应异常都抛异常由调用方处理。"""
    from app import __version__

    resp = requests.get(
        LATEST_API,
        timeout=(5, 8),
        headers={
            "User-Agent": f"MiHome-Windows/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("最新版本信息缺少版本号")
    asset_url, asset_name, asset_size = _pick_asset(data.get("assets"))
    return ReleaseInfo(
        tag=tag,
        url=str(data.get("html_url") or RELEASES_PAGE),
        name=str(data.get("name") or tag),
        body=str(data.get("body") or ""),
        published_at=str(data.get("published_at") or ""),
        asset_url=asset_url,
        asset_name=asset_name,
        asset_size=asset_size,
    )


def _version_parts(value: str) -> tuple[int, ...]:
    """版本号解析为数字段：容忍 v 前缀、预发布/构建后缀与非数字段。"""
    core = value.strip().lower().lstrip("v")
    core = re.split(r"[-+].*$", core)[0]
    parts: list[int] = []
    for seg in core.split("."):
        m = re.match(r"\d+", seg)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新；位数不等时按 0 补齐再逐段比较。"""
    a = list(_version_parts(latest))
    b = list(_version_parts(current))
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return tuple(a) > tuple(b)


class UpdateChecker(QObject):
    """后台线程查一次最新版本；check_finished 回主线程（info, error）。"""

    check_finished = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)

    def check(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            info = fetch_latest_release()
        except Exception as exc:  # 网络异常全部转回调，绝不打崩进程
            self.check_finished.emit(None, exc)
        else:
            self.check_finished.emit(info, None)
