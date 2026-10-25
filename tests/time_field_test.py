# SPDX-License-Identifier: GPL-3.0-or-later
"""时间字段（TimeField）回归测试（离屏运行）。

锁死「生效时段」这个现代时间输入控件的行为：
1. 取值/赋值与 QTime 语义一致；步进按钮 ±5 分钟、跨午夜回绕；
2. 滚轮不改值（设置页滚动时不能误改，与 themed_combo 同约定）；
3. 禁用传播到内部输入框与两个步进按钮（父开关联动依赖它）；
4. 主题切换可重复上色，图标随主题/禁用态重新着色；
5. 设置页接线：回显 store、保存写回 HH:mm、换肤不抛异常。
"""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtCore import QPoint, QPointF, QTime, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

app = QApplication([])

from app.core import settings_store  # noqa: E402
from app.ui.si_theme import set_theme  # noqa: E402
from app.ui.time_field import STEP_SECONDS, TimeField  # noqa: E402

# ---------- 1. 取值 / 赋值 ----------
field = TimeField(QTime(8, 0))
assert field.time() == QTime(8, 0), field.time()
field.setTime(QTime(23, 5))
assert field.time() == QTime(23, 5)
# 对外暴露的显示格式必须是 HH:mm（设置里存的就是这个格式）
assert field._edit.displayFormat() == "HH:mm"
print("1. 取值 / 赋值 OK")

# ---------- 2. 步进按钮：±5 分钟 + 跨午夜回绕 ----------
assert STEP_SECONDS == 300, "步进档位变了要同步更新说明文案"
field.setTime(QTime(8, 0))
field._up.click()
assert field.time() == QTime(8, 5), field.time()
field._down.click()
assert field.time() == QTime(8, 0)
# 23:58 + 5min -> 00:03（跨午夜）
field.setTime(QTime(23, 58))
field._up.click()
assert field.time() == QTime(0, 3), f"跨午夜回绕错误: {field.time()}"
# 00:02 - 5min -> 23:57（反向回绕）
field.setTime(QTime(0, 2))
field._down.click()
assert field.time() == QTime(23, 57), f"反向回绕错误: {field.time()}"
print("2. 步进 ±5 分钟 + 跨午夜回绕 OK")

# ---------- 3. 滚轮不改值 ----------
field.setTime(QTime(9, 30))
wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                    QPoint(0, 120), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.NoScrollPhase, False)
QApplication.sendEvent(field._edit, wheel)
assert field.time() == QTime(9, 30), f"滚轮不应改值: {field.time()}"
print("3. 滚轮不改值 OK")

# ---------- 4. 禁用传播 + 信号转发 ----------
seen: list[QTime] = []
field.timeChanged.connect(seen.append)
field._nudge(STEP_SECONDS)
assert seen and seen[-1] == QTime(9, 35), f"timeChanged 未转发: {seen}"
field.setEnabled(False)
assert not field._edit.isEnabled(), "内部输入框未随父控件禁用"
assert not field._up.isEnabled() and not field._down.isEnabled(), "步进按钮未禁用"
field.setEnabled(True)
assert field._edit.isEnabled() and field._up.isEnabled()
print("4. 禁用传播 + 信号转发 OK")

# ---------- 5. 主题切换可重复上色 ----------
for mode in ("dark", "light", "dark"):
    set_theme(mode)
    field.apply_theme()
    app.processEvents()
    assert not field._up.icon().isNull(), "步进按钮图标缺失"
    assert not field._down.icon().isNull()
assert field._edit.styleSheet(), "输入框样式为空"
# 尺寸约定：与设置页其他控件同高（32），宽够放下 ×2 + 步进按钮区
assert field.height() == 32, field.height()
assert field.width() >= 76, field.width()
print("5. 主题切换 / 尺寸约定 OK")

# ---------- 6. 设置页接线：回显 / 保存 / 换肤 ----------
# 保存流程会写 HKCU Run：测试绝不改动真实注册表
settings_store.set_autostart = lambda value: None

from app.ui.settings_dialog import SettingsDialog  # noqa: E402
from app.ui.theme_service import apply_theme  # noqa: E402

apply_theme("dark")
settings_store.set_lock_automation({
    "enabled": True, "did": "lamp1", "time_enabled": True,
    "time_start": "06:30", "time_end": "22:15"})

dlg = SettingsDialog(None, [])
assert dlg._lock_time_start.time() == QTime(6, 30), \
    f"未回显 time_start: {dlg._lock_time_start.time()}"
assert dlg._lock_time_end.time() == QTime(22, 15), \
    f"未回显 time_end: {dlg._lock_time_end.time()}"
# 改值后保存，配置里必须按 HH:mm 写回
dlg._lock_time_start.setTime(QTime(7, 45))
dlg._lock_time_end.setTime(QTime(23, 5))
dlg.retheme()
app.processEvents()
dlg._save_and_accept()

from PySide6.QtTest import QTest  # noqa: E402

QTest.qWait(300)   # 遮罩对话框淡出后才真正 done
assert dlg.result() == QDialog.DialogCode.Accepted, dlg.result()
cfg = settings_store.get_lock_automation()
assert cfg["time_start"] == "07:45", cfg["time_start"]
assert cfg["time_end"] == "23:05", cfg["time_end"]
print("6. 设置页回显 / 换肤 / 保存写回 OK")

# 子开关关闭时两个字段置灰（孙设置联动）
dlg2 = SettingsDialog(None, [])
dlg2._lock_time_toggle.setChecked(False)
app.processEvents()
assert not dlg2._lock_time_start.isEnabled(), "关闭「生效时间」后字段未置灰"
dlg2._lock_time_toggle.setChecked(True)
app.processEvents()
assert dlg2._lock_time_start.isEnabled()
dlg.deleteLater()
dlg2.deleteLater()
print("7. 「生效时间」开关联动 OK")

print("\n全部时间字段测试通过")
