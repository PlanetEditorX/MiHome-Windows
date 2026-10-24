import app.siui.components as components
import app.siui.components.widgets as widgets
import app.siui.core as core
import app.siui.core.globals

# 注意：不要在这里调用 gui.scale.reload_scale_factor()。该函数内部
# SetProcessDPIAware() 会把进程钉死为系统级 DPI 感知（Qt 6 默认的
# per-monitor 感知被破坏），并用系统 DPI 覆写 QT_SCALE_FACTOR。多屏
# 不同缩放（如 200% 笔记本屏 + 125% 外接屏）的机器上，二者叠加导致
# Qt 逻辑尺寸与原生窗口物理尺寸失配——窗口只显示出整个界面的一个
# 碎片（右上角只剩几个图标），见 v0.6.1 修复。
#
# 界面缩放统一由 run.py 在 QApplication 创建前写入 QT_SCALE_FACTOR，
# 默认 1.0 跟随各屏幕自己的系统缩放；设置页「界面缩放比例」是在此
# 之上的个人微调乘数。
