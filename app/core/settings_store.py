# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""应用设置的本地持久化。

与 tray.json / workbench.json 同目录，单独文件 settings.json，
保存用户在设置界面中调整的偏好。
"""

import sys
from pathlib import Path

from app.core import _json_store

_VERSION = 1
_FILENAME = "settings.json"

_DEFAULTS: dict = {
    "version": _VERSION,
    "minimize_to_tray": True,
    "start_minimized": False,
    "voice_fab_enabled": True,
    "theme": "system",  # system / light / dark
    # 主题色（#rrggbb）；空串 = 默认米家绿。派生 hover/dim 与底色上的文字色
    # 由 ui/si_theme.set_accent 统一计算，这里只存用户选的那个值。
    "theme_accent": "",
    "hide_no_func_devices": False,
    "show_scene_tab": True,  # 主页选项卡最左侧显示「场景」入口（手动场景一键执行）
    "default_speaker_did": "",  # 小爱指令默认输出音箱；空串=自动（第一个在线）
    # 托盘音响栏正在控制哪台音箱。与 default_speaker_did 刻意分开：
    # 「对谁说话」和「调谁的音量」经常不是同一台（房间里放着歌，
    # 但语音指令想发到客厅），共用一个偏好会互相覆盖。
    "tray_audio_speaker_did": "",
    "ui_scale": 1.0,    # 界面缩放个人微调乘数（在系统缩放之上再乘），需重启生效
    "tray_columns": 2,  # 托盘快捷窗口卡片列数：1 或 2
    "check_update_enabled": True,  # 启动时自动检查 GitHub 新版本
    # 锁屏联动：会话锁屏/解锁时自动开关指定设备（见 core/lock_automation.py）。
    # 默认语义即最常见的用法：解锁开灯、锁屏关灯。
    # time_*：生效时间段（每日，支持跨午夜）；关闭=不限制时段。
    # net_*：生效网络名单（WiFi 名/以太网名，包含匹配）；关闭或名单
    #        为空=不限制网络。两者任一不满足，本次事件跳过并提示。
    "lock_automation": {
        "enabled": False,
        "did": "",             # 目标设备 did；空串=尚未选择
        "unlock_action": "on",  # 解锁时对该设备做什么：on / off / none
        "lock_action": "off",   # 锁屏时对该设备做什么：on / off / none
        "time_enabled": False,  # 是否限定生效时间段
        "time_start": "08:00",  # 时段起 HH:MM（含）
        "time_end": "23:00",    # 时段止 HH:MM（不含）；起>止=跨午夜
        "net_enabled": False,   # 是否限定生效网络
        "net_names": [],        # 生效网络名列表（大小写不敏感包含匹配）
    },
}


def _read_raw() -> dict:
    raw = _json_store.read_json(_json_store.data_file(_FILENAME), dict(_DEFAULTS))
    if raw.get("version") != _VERSION:
        return dict(_DEFAULTS)
    # 补全缺失字段
    for k, v in _DEFAULTS.items():
        raw.setdefault(k, v)
    return raw


def _write_raw(raw: dict) -> None:
    _json_store.write_json(_json_store.data_file(_FILENAME), raw)


def get_minimize_to_tray() -> bool:
    """关闭窗口时是否最小化到托盘，默认 True。"""
    return bool(_read_raw().get("minimize_to_tray", True))


def set_minimize_to_tray(value: bool) -> None:
    raw = _read_raw()
    raw["minimize_to_tray"] = bool(value)
    _write_raw(raw)


def get_start_minimized() -> bool:
    """启动时是否以托盘方式静默启动，不唤出主界面。默认 False。"""
    return bool(_read_raw().get("start_minimized", False))


def set_start_minimized(value: bool) -> None:
    raw = _read_raw()
    raw["start_minimized"] = bool(value)
    _write_raw(raw)


def get_voice_fab_enabled() -> bool:
    """是否启用小爱同学悬浮对话按钮，默认 True。"""
    return bool(_read_raw().get("voice_fab_enabled", True))


def set_voice_fab_enabled(value: bool) -> None:
    raw = _read_raw()
    raw["voice_fab_enabled"] = bool(value)
    _write_raw(raw)


def get_default_speaker_did() -> str:
    """小爱指令默认输出音箱的 did；空串表示自动（第一个在线音箱）。

    设备被移除或离线时不强制：调用方找不到该 did 时回退自动选择。
    """
    value = _read_raw().get("default_speaker_did", "")
    return value if isinstance(value, str) else ""


def set_default_speaker_did(value: str) -> None:
    raw = _read_raw()
    raw["default_speaker_did"] = value if isinstance(value, str) else ""
    _write_raw(raw)


def get_tray_audio_speaker_did() -> str:
    """托盘音响栏正在控制的音箱 did；空串表示跟随（语音默认 → 第一个在线）。

    与 `default_speaker_did` 相互独立：改音响栏不会影响语音指令发往哪台，
    反之亦然。所选音箱离线时不强制回退写入，等它重新上线就自动回到它。
    """
    value = _read_raw().get("tray_audio_speaker_did", "")
    return value if isinstance(value, str) else ""


def set_tray_audio_speaker_did(value: str) -> None:
    raw = _read_raw()
    raw["tray_audio_speaker_did"] = value if isinstance(value, str) else ""
    _write_raw(raw)


def get_hide_no_func_devices() -> bool:
    """是否隐藏无可控制功能的设备（无 spec / spec 无属性），默认关闭。"""
    return bool(_read_raw().get("hide_no_func_devices", False))


def set_hide_no_func_devices(value: bool) -> None:
    raw = _read_raw()
    raw["hide_no_func_devices"] = bool(value)
    _write_raw(raw)


def get_show_scene_tab() -> bool:
    """是否在主页选项卡最左侧显示「场景」入口，默认开启。"""
    return bool(_read_raw().get("show_scene_tab", True))


def set_show_scene_tab(value: bool) -> None:
    raw = _read_raw()
    raw["show_scene_tab"] = bool(value)
    _write_raw(raw)


def get_tray_columns() -> int:
    """托盘快捷窗口卡片列数，仅认 1 / 2，其余值回退双列。"""
    try:
        value = int(_read_raw().get("tray_columns", 2))
    except (TypeError, ValueError):
        return 2
    return value if value in (1, 2) else 2


def set_tray_columns(value: int) -> None:
    raw = _read_raw()
    raw["tray_columns"] = int(value) if int(value) in (1, 2) else 2
    _write_raw(raw)


def get_check_update_enabled() -> bool:
    """启动时是否自动检查 GitHub 新版本，默认开启。"""
    return bool(_read_raw().get("check_update_enabled", True))


def set_check_update_enabled(value: bool) -> None:
    raw = _read_raw()
    raw["check_update_enabled"] = bool(value)
    _write_raw(raw)


# ---------- 锁屏联动（会话锁屏/解锁时自动控制设备） ----------

_LOCK_ACTIONS = ("on", "off", "none")
_LOCK_DEFAULTS = {
    "enabled": False,
    "did": "",
    "unlock_action": "on",
    "lock_action": "off",
    "time_enabled": False,
    "time_start": "08:00",
    "time_end": "23:00",
    "net_enabled": False,
    "net_names": [],
}


def _normalize_hhmm(value, default: str) -> str:
    """HH:MM 合法性收敛；非法（含非字符串）回落默认值。"""
    from app.core.lock_conditions import parse_hhmm, format_hhmm
    parsed = parse_hhmm(value)
    if parsed is None:
        return default
    return f"{parsed[0]:02d}:{parsed[1]:02d}"


def _normalize_net_names(value) -> list[str]:
    """网络名单收敛：只保留非空字符串，去重保序；非列表按空处理。"""
    if not isinstance(value, (list, tuple)):
        return []
    seen: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in seen:
            seen.append(item.strip())
    return seen


def _normalize_lock_automation(raw) -> dict:
    """把磁盘/界面传入的配置收敛成合法结构；非法字段回落默认值。

    动作只认 on / off / none（none=该方向不动作，例如只想关不想开）；
    did 必须是字符串（设备 id），其余类型一律按未选择处理；
    生效时间两端必须是合法 HH:MM；生效网络名单过滤非字符串并去重。
    """
    if not isinstance(raw, dict):
        raw = {}
    did = raw.get("did")
    if not isinstance(did, str):
        did = ""
    unlock = raw.get("unlock_action")
    lock = raw.get("lock_action")
    return {
        "enabled": bool(raw.get("enabled", _LOCK_DEFAULTS["enabled"])),
        "did": did,
        "unlock_action": unlock if unlock in _LOCK_ACTIONS else _LOCK_DEFAULTS["unlock_action"],
        "lock_action": lock if lock in _LOCK_ACTIONS else _LOCK_DEFAULTS["lock_action"],
        "time_enabled": bool(raw.get("time_enabled", _LOCK_DEFAULTS["time_enabled"])),
        "time_start": _normalize_hhmm(raw.get("time_start"), _LOCK_DEFAULTS["time_start"]),
        "time_end": _normalize_hhmm(raw.get("time_end"), _LOCK_DEFAULTS["time_end"]),
        "net_enabled": bool(raw.get("net_enabled", _LOCK_DEFAULTS["net_enabled"])),
        "net_names": _normalize_net_names(raw.get("net_names")),
    }


def get_lock_automation() -> dict:
    """读取锁屏联动配置（已规范化，字段必齐全）。"""
    raw = _read_raw().get("lock_automation", {})
    return _normalize_lock_automation(raw)


def set_lock_automation(value) -> dict:
    """整体写入锁屏联动配置，返回规范化后的实际落盘值。"""
    cfg = _normalize_lock_automation(value)
    raw = _read_raw()
    raw["lock_automation"] = cfg
    _write_raw(raw)
    return cfg


def get_lock_automation_hinted() -> bool:
    """「锁屏联动未配置」引导提示是否已展示过（只引导一次）。"""
    return bool(_read_raw().get("lock_automation_hinted", False))


def set_lock_automation_hinted(value: bool) -> None:
    raw = _read_raw()
    raw["lock_automation_hinted"] = bool(value)
    _write_raw(raw)


# 界面缩放档位（设置页下拉选项），范围 50%–200%
UI_SCALES = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
_UI_SCALE_MIN, _UI_SCALE_MAX = 0.5, 2.0


_UI_SCALE_DEFAULT = 1.0


def get_ui_scale() -> float:
    """界面缩放个人微调乘数（在 Qt 原生 DPI 之上再乘），默认 100%。

    档位值直接用；手工改配置文件的任意值按范围收窄；非法值回退默认。
    """
    try:
        value = float(_read_raw().get("ui_scale", _UI_SCALE_DEFAULT))
    except (TypeError, ValueError):
        return _UI_SCALE_DEFAULT
    if value in UI_SCALES:
        return value
    return min(max(value, _UI_SCALE_MIN), _UI_SCALE_MAX) if value > 0 else _UI_SCALE_DEFAULT


def set_ui_scale(value: float) -> None:
    raw = _read_raw()
    value = float(value)
    # 超出 50%-200% 范围的值钳制到边界
    raw["ui_scale"] = min(max(value, _UI_SCALE_MIN), _UI_SCALE_MAX)
    _write_raw(raw)


def get_theme_mode() -> str:
    """主题配色设置：system（跟随系统）/ light / dark，默认 system。"""
    value = str(_read_raw().get("theme", "system"))
    return value if value in ("system", "light", "dark") else "system"


def set_theme_mode(value: str) -> None:
    raw = _read_raw()
    raw["theme"] = value if value in ("system", "light", "dark") else "system"
    _write_raw(raw)


def get_theme_accent() -> str:
    """主题色（#rrggbb）；空串表示使用默认米家绿。"""
    value = _read_raw().get("theme_accent", "")
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    # 只认 #rrggbb：手改坏的值按默认色处理，不让界面刷成透明/黑块
    return value if len(value) == 7 and value.startswith("#") else ""


def set_theme_accent(value: str) -> None:
    raw = _read_raw()
    text = (value or "").strip().lower()
    raw["theme_accent"] = text if len(text) == 7 and text.startswith("#") else ""
    _write_raw(raw)


# ---------- 开机自启动（Windows 注册表 HKCU Run） ----------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "MiHome-Windows"


def autostart_supported() -> bool:
    """仅构建版（Nuitka standalone exe）支持开机自启动。

    开发模式写入的是 venv 解释器 + run.py：任务管理器显示为无名
    "Python"、venv 一旦清理即失效、安全软件对解释器自启天然不信任
    （火绒报「无效/建议删除」即此情形），因此不提供。
    """
    from app import is_packaged

    return is_packaged() and sys.platform == "win32"


def _autostart_command() -> str:
    """注册表里写入的启动命令（仅打包形态调用）。

    不能用 sys.executable：Nuitka standalone 会在产物目录附带一个
    python.exe 且 sys.executable 指向它（已实测），写进注册表就是
    开机启动裸解释器——任务管理器显示 "Python"、安全软件报无效。
    sys.argv[0] 才是用户实际启动的 exe 路径（已实测）。
    """
    argv0 = Path(sys.argv[0]).resolve()
    return f'"{argv0}"'


def get_autostart() -> bool:
    """读取注册表实际状态（而非偏好文件），外部改动也能反映。

    目标文件已不存在的残留条目视为未开启（返回 False），开关重新
    打开时会用正确的命令覆写——否则无效条目会永远谎报「已开启」。
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_VALUE_NAME)
    except OSError:
        return False
    target = _command_target(value)
    return target is not None and target.exists()


def _command_target(value: str) -> Path | None:
    """从 Run 条目命令里解析目标可执行文件路径。"""
    value = value.strip()
    if not value:
        return None
    if value.startswith('"'):
        end = value.find('"', 1)
        if end == -1:
            return None
        inner = value[1:end]
        # 空引号命令解析为 Path(".")，目录 exists() 恒真，需显式拒绝
        return Path(inner) if inner else None
    bare = value.split(" ", 1)[0]
    return Path(bare) if bare else None


def set_autostart(value: bool) -> None:
    """开启自启动仅在构建版受支持；关闭（清理残留）任何形态都允许。"""
    if sys.platform != "win32":
        return
    if value and not autostart_supported():
        # 开发模式拒绝写入（调用方负责置灰开关并提示）
        return
    import winreg

    if value:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ,
                              _autostart_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _RUN_VALUE_NAME)
    except FileNotFoundError:
        pass
