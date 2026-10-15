# 应用更新界面验证

截图来自隔离的真实 Qt 更新控件与生成的 PowerShell/WinForms 安装窗口。示例版本 `9.0.0` 仅用于展示，不代表已发布版本；不访问网络、不修改实际安装目录或用户主题。

复现：在仓库根目录运行 `.venv\Scripts\python.exe scripts/render_update_screenshots.py`。Qt 使用 `QT_SCALE_FACTOR`，WinForms 使用对应的控件和字体缩放，覆盖 125%、150%、200% 的排版；这不等同于在每台设备上切换系统 DPI 的人工测试。

| 场景 | 截图 |
| --- | --- |
| 浅色、125%、已准备 | [查看](ready-light-125.png) |
| 深色、150%、已准备 | [查看](ready-dark-150.png) |
| 窄窗口、200%、按钮纵向排列 | [查看](ready-light-200.png) |
| 深色、200%、下载失败 | [查看](error-dark-200.png) |
| 安装窗口、浅色、125% | [查看](installer-light-125.png) |
| 安装窗口、深色、150% | [查看](installer-dark-150.png) |
| 安装窗口、浅色、200% | [查看](installer-light-200.png) |
| 等待安装程序、安全软件确认，100% | [查看](waiting-light-100.png) |
| 等待安装程序、安全软件确认，150% | [查看](waiting-light-150.png) |
| 等待安装程序、窄窗口，200% | [查看](waiting-light-200.png) |

等待状态复现：`.venv/Scripts/python.exe scripts/render_update_screenshots.py --panel --theme light --scale 1.5 --waiting`，将 `--scale` 改为 `1` 或 `2` 验证另外两个尺寸。检查取消按钮可用、安装按钮禁用和文字完整换行。

功能验证使用 `test/test_update_coordinator.py`、`test/test_update_lifecycle.py` 和 `test/test_update_installer_windows.py`。最后一项实际运行生成的安装脚本，在临时目录执行复制、占用检查、失败回滚、中断恢复以及模拟应用启动回执；EXE/DLL 夹具由系统 C# 编译器生成。未对用户正在使用的打包应用执行更新。
