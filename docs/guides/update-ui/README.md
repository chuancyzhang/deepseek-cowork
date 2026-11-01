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
| 高版本空更新拦截、浅色、125% | [查看](package-mismatch-light-125.png) |
| 高版本空更新拦截、深色窄窗口、200% | [查看](package-mismatch-dark-200.png) |
| 启动版本不符、浅色、125% | [查看](startup-failure-light-125.png) |
| 启动版本不符、深色、200% | [查看](startup-failure-dark-200.png) |
| 旧更新窗口未关闭、浅色、125% | [查看](installer-busy-light-125.png) |
| 旧更新窗口未关闭、深色窄窗口、200% | [查看](installer-busy-dark-200.png) |

等待状态复现：`.venv/Scripts/python.exe scripts/render_update_screenshots.py --panel --theme light --scale 1.5 --waiting`，将 `--scale` 改为 `1` 或 `2` 验证另外两个尺寸。检查取消按钮可用、安装按钮禁用和文字完整换行。

功能验证使用 `test/test_update_coordinator.py`、`test/test_update_lifecycle.py` 和 `test/test_update_installer_windows.py`。最后一项实际运行生成的安装脚本，在临时目录执行复制、占用检查、失败回滚、中断恢复以及模拟应用启动回执；EXE/DLL 夹具由系统 C# 编译器生成。未对用户正在使用的打包应用执行更新。

企业状态复现：`.venv/Scripts/python.exe scripts/render_update_screenshots.py --panel --enterprise --theme light --scale 2`；将主题改为 `dark` 并加 `--failure` 检查失败状态。`test/test_enterprise_updates.py` 使用独立本机 HTTP 服务和合成安装包验证推荐查询、真实下载、平台与摘要校验、来源隔离、离线本地包、点击更新衔接安装以及配置版本变化后的启动回执。

2026-10-24 验证：企业更新、更新协调、安装前保存、基础更新器和企业客户端共 109 项及 7 个子测试通过；Windows 安装事务 8 项通过。合计 117 项及 7 个子测试，未构建或替换用户的 EXE，未发布企业推荐版本。

2026-10-26 更新：更新就绪入口按用户确认嵌入左侧栏底部、设置入口上方，关闭只隐藏本次运行同版本提示，普通通知位置保持不变。截图中的 `9.0.0` 是隔离示例，不代表已发布版本。复现独立提示条使用 `--panel --notice --theme light --scale 2`；真实主窗口使用 `--workspace --theme light --scale 2`（可改 `dark`、`1.25` 或 `1.5`）。主窗口脚本使用临时用户数据、关闭启动服务和自动检查，只注入已就绪状态，并验证提示位置、关闭后不重复出现、主题预览不写盘及正常退出。图像为 Windows 上 Qt 离屏截图，缩放参数是 `QT_SCALE_FACTOR`，不等同于实际调整 Windows 系统缩放。

自动化验证：`test/test_public_updates.py` 使用隔离的本机 HTTP 服务验证静态清单、真实 ZIP 下载、公共源失败换 GitHub、固定版本和摘要、跨来源复用缓存、总尝试预算、取消、GitHub API 兼容与限流；`test/test_update_notice.py` 验证焦点不变、同版本去重、关闭和重新启动的提醒规则、安装请求复用协调器、主题预览及构造期间无临时顶层窗口。原更新器、协调器、企业更新、保存生命周期、打包和真实应用退出回归一并检查。

Windows 安装事务现有及新增共 10 项通过：实际执行生成的 PowerShell 脚本，新增 WinForms 后台握手、提交后等待主进程真正退出才显示窗口、隐藏阶段取消且不修改文件；保留复制、占用、失败回滚、中断恢复与启动回执验证。GUI 握手测试使用透明窗口避免打扰，安装窗口外观另由截图模式验证。没有构建或更新用户实际使用的 EXE，没有访问生产公共源、GitHub 发布资产或企业服务器。

2026-11-01 启动确认修复：本次相关 78 项测试及 6 个子测试通过，其中 Windows 安装器实测 15 项及 2 个子测试。`test/test_update_startup.py` 使用真实跨进程 Qt 本地通信验证主窗口加载前、加载后的更新确认转交；拒绝其他安装目录、事务和路径。协调器测试覆盖高版本但文件完全相同的包在退出前停止，以及同版本重装和实际升级仍可用。Windows 安装测试验证重复启动进程正常退出后延迟回执、非零退出码、旧回执清除和实际版本不符。更新、企业分发和保存生命周期回归沿用原测试，不对用户安装目录执行更新。

错误状态截图复现：`.venv/Scripts/python.exe -X utf8 -B scripts/render_update_screenshots.py --panel --package-mismatch --theme light --scale 1.25`，另用 `--theme dark --scale 2` 检查窄窗口；安装器用 `--startup-failure` 替换 `--panel --package-mismatch`。脚本对隔离的生成脚本注入启动失败，执行实际错误呈现分支，不启动应用。Qt 使用离屏和 `QT_SCALE_FACTOR`，WinForms 使用控件和字体缩放；均在 Windows 运行，不能等同于切换系统 DPI。确认长错误完整换行、重新选择/重新打开入口可达，未写入用户主题。

2026-11-01 晚间复现：失败的旧安装器窗口未关闭会继续持有安装锁。界面现在根据当前事务的 `startup_failed / mutex_wait / mutex_acquired=false` 显示原因及关闭旧窗口后的重试方式，不以进程候选列表猜测锁持有者。`test/test_update_lifecycle.py` 加上 Windows 安装器的互斥冲突、GUI 握手两项测试，共 20 项及 2 个子测试通过；实际验证失败窗口保留时重试被拒绝、关闭后新安装器可就绪，应用端保留任务、草稿和准备好的包，外来事务结果不能影响提示。

提示截图：`.venv/Scripts/python.exe -X utf8 -B scripts/render_update_screenshots.py --panel --installer-busy --theme light --scale 1.25`；深色窄窗口使用 `--theme dark --scale 2`。两张图检查原因完整换行及安装入口可达，沿用上述离屏和缩放边界。
