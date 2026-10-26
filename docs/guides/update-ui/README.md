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
| 企业自动更新、浅色、125% | [查看](enterprise-ready-light-125.png) |
| 企业自动更新、窄窗口、200% | [查看](enterprise-ready-light-200.png) |
| 企业下载失败、本地入口保留、深色、200% | [查看](enterprise-error-dark-200.png) |
| 侧栏提示条、浅色、125% | [查看](notice-light-125.png) |
| 侧栏提示条、深色、150% | [查看](notice-dark-150.png) |
| 窄侧栏提示条、浅色、200% | [查看](notice-light-200.png) |
| 真实主窗口、浅色、125% | [查看](workspace-light-125.png) |
| 真实主窗口、深色、150% | [查看](workspace-dark-150.png) |
| 真实小窗口、浅色、200% | [查看](workspace-light-200.png) |

等待状态复现：`.venv/Scripts/python.exe scripts/render_update_screenshots.py --panel --theme light --scale 1.5 --waiting`，将 `--scale` 改为 `1` 或 `2` 验证另外两个尺寸。检查取消按钮可用、安装按钮禁用和文字完整换行。

功能验证使用 `test/test_update_coordinator.py`、`test/test_update_lifecycle.py` 和 `test/test_update_installer_windows.py`。最后一项实际运行生成的安装脚本，在临时目录执行复制、占用检查、失败回滚、中断恢复以及模拟应用启动回执；EXE/DLL 夹具由系统 C# 编译器生成。未对用户正在使用的打包应用执行更新。

企业状态复现：`.venv/Scripts/python.exe scripts/render_update_screenshots.py --panel --enterprise --theme light --scale 2`；将主题改为 `dark` 并加 `--failure` 检查失败状态。`test/test_enterprise_updates.py` 使用独立本机 HTTP 服务和合成安装包验证推荐查询、真实下载、平台与摘要校验、来源隔离、离线本地包、点击更新衔接安装以及配置版本变化后的启动回执。

2026-10-24 验证：企业更新、更新协调、安装前保存、基础更新器和企业客户端共 109 项及 7 个子测试通过；Windows 安装事务 8 项通过。合计 117 项及 7 个子测试，未构建或替换用户的 EXE，未发布企业推荐版本。

2026-10-26 更新：更新就绪入口按用户确认嵌入左侧栏底部、设置入口上方，关闭只隐藏本次运行同版本提示，普通通知位置保持不变。截图中的 `9.0.0` 是隔离示例，不代表已发布版本。复现独立提示条使用 `--panel --notice --theme light --scale 2`；真实主窗口使用 `--workspace --theme light --scale 2`（可改 `dark`、`1.25` 或 `1.5`）。主窗口脚本使用临时用户数据、关闭启动服务和自动检查，只注入已就绪状态，并验证提示位置、关闭后不重复出现、主题预览不写盘及正常退出。图像为 Windows 上 Qt 离屏截图，缩放参数是 `QT_SCALE_FACTOR`，不等同于实际调整 Windows 系统缩放。

自动化验证：`test/test_public_updates.py` 使用隔离的本机 HTTP 服务验证静态清单、真实 ZIP 下载、公共源失败换 GitHub、固定版本和摘要、跨来源复用缓存、总尝试预算、取消、GitHub API 兼容与限流；`test/test_update_notice.py` 验证焦点不变、同版本去重、关闭和重新启动的提醒规则、安装请求复用协调器、主题预览及构造期间无临时顶层窗口。原更新器、协调器、企业更新、保存生命周期、打包和真实应用退出回归一并检查。

Windows 安装事务现有及新增共 10 项通过：实际执行生成的 PowerShell 脚本，新增 WinForms 后台握手、提交后等待主进程真正退出才显示窗口、隐藏阶段取消且不修改文件；保留复制、占用、失败回滚、中断恢复与启动回执验证。GUI 握手测试使用透明窗口避免打扰，安装窗口外观另由截图模式验证。没有构建或更新用户实际使用的 EXE，没有访问生产公共源、GitHub 发布资产或企业服务器。
