# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""锁屏联动的生效条件：生效时间段 + 生效网络。

两个维度都是「锦上添花」的约束，默认关闭；开启后锁屏/解锁事件
要先过条件门才真正驱动设备：

- 生效时间：只在每天的某个时段内响应。支持跨午夜窗口
  （如 23:00–06:00，夜间锁屏不关灯、清晨解锁不开灯这类需求）。
- 生效网络：只在电脑连接到指定网络时响应。典型场景是
  「只在家里 WiFi 下联动」——在公司、手机热点下锁屏/解锁
  不动作，避免远程场景误触设备。

设计原则：
- 时间判断是纯函数（可注入 now），单测全覆盖、零系统依赖；
- 网络查询依赖 Windows（PowerShell NetConnectionProfile），
  查询失败返回 None，由调用方保守处理（未配置名单=不限制，
  查询不到=不执行，宁可不动也不误动）；
- 事件频率极低（每次锁屏/解锁各一次），网络查询用一次性
  子进程即可，无需常驻监听网络变化。
"""

import logging
import re
import subprocess
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

# 生效网络查询的子进程超时（秒）。PowerShell 冷启动通常 1 秒内，
# 卡死时最多阻塞一次锁屏事件，不会拖垮队列。
_NET_QUERY_TIMEOUT = 5

_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{1,2})\s*$")


def is_supported() -> bool:
    """网络查询仅 Windows 有意义（PowerShell NetConnectionProfile）。"""
    return sys.platform == "win32"


def parse_hhmm(text) -> tuple[int, int] | None:
    """解析 "HH:MM"（宽松接受 "H:MM"），非法返回 None。"""
    m = _HHMM_RE.match(str(text))
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def format_hhmm(value) -> str:
    """把 (h, m) 或 datetime.time 规格化成 "HH:MM"；失败回落 "08:00"。"""
    if isinstance(value, str):
        parsed = parse_hhmm(value)
        if parsed:
            return f"{parsed[0]:02d}:{parsed[1]:02d}"
        return "08:00"
    hour = getattr(value, "hour", None)
    minute = getattr(value, "minute", None)
    if hour is None or minute is None:
        return "08:00"
    return f"{hour:02d}:{minute:02d}"


def in_time_window(start, end, now: datetime | None = None) -> bool:
    """判断 now 是否落在 [start, end) 每日时段内。

    - start == end：视为 24 小时全天（关闭该约束的等价语义）；
    - start < end：普通窗口，如 08:00–23:00；
    - start > end：跨午夜窗口，如 23:00–06:00；
    - 任一端非法字符串：视为不限制（返回 True）并记日志——
      配置在 settings_store 层已收敛，这里只是防御。
    边界取左闭右开：end 时刻整已不在窗口内。
    """
    s = parse_hhmm(start)
    e = parse_hhmm(end)
    if s is None or e is None:
        logger.warning("生效时间配置非法（%r–%r），本次不限制", start, end)
        return True
    now = now or datetime.now()
    t = now.hour * 60 + now.minute
    smin = s[0] * 60 + s[1]
    emin = e[0] * 60 + e[1]
    if smin == emin:
        return True
    if smin < emin:
        return smin <= t < emin
    # 跨午夜：晚于起点或早于终点
    return t >= smin or t < emin


def current_network_names() -> list[str] | None:
    """当前已连接的网络名称列表（WiFi 下通常即 SSID）。

    None = 无法判断（非 Windows / 查询失败 / 超时），调用方应
    保守处理。空列表不会出现——PowerShell 无输出时返回 None。
    """
    if not is_supported():
        return None
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-NetConnectionProfile | ForEach-Object { $_.Name }"],
            capture_output=True, text=True, timeout=_NET_QUERY_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        logger.debug("查询当前网络名失败", exc_info=True)
        return None
    if proc.returncode != 0:
        logger.debug("Get-NetConnectionProfile 退出码 %s", proc.returncode)
        return None
    names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not names:
        return None
    return names


def match_network(names: list[str] | None, wanted) -> bool:
    """当前网络是否命中用户配置的生效网络名单。

    - wanted 为空/未配置：不限制，返回 True；
    - names 为 None（查询失败）或空（未连接）：保守返回 False，
      记日志——宁可不动也不误动；
    - 匹配语义：大小写不敏感的「包含」（名单里写 "CMCC" 可命中
      "CMCC-8f3a"；用户写全名即全等）。
    """
    wanted_list = [w.strip() for w in (wanted or [])
                   if isinstance(w, str) and w.strip()]
    if not wanted_list:
        return True
    if not names:
        logger.info("生效网络已开启但当前网络不可知，本次联动跳过")
        return False
    current = [n.lower() for n in names]
    for w in wanted_list:
        wl = w.lower()
        if any(wl in c for c in current):
            return True
    logger.info("当前网络 %s 不在生效名单 %s，本次联动跳过", names, wanted_list)
    return False


def parse_network_text(text: str) -> list[str]:
    """把设置界面的网络名输入（逗号/分号/换行分隔）解析成去重名单。"""
    parts = re.split(r"[,;，；\n]", str(text or ""))
    seen: list[str] = []
    for p in parts:
        name = p.strip()
        if name and name not in seen:
            seen.append(name)
    return seen
