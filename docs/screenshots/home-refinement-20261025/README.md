# 首页 UI 调整验证

本次仅调整首页呈现：显示标题上下的两句引导文案，将默认输入框整体高度从 174 缩至 140 个逻辑像素（减少 19.5%）。编辑区仍由既有 `AutoResizingInputEdit` 按内容增高，11 行示例下实测为 222，清空后恢复 70；原最大高度 240 保持不变。对话页输入尺寸、发送、粘贴、快捷任务、权限和模型等处理逻辑未修改。

多行内容或工作台选项导致首页滚动条出现时，通过既有延迟布局同步更新输入卡片宽度，避免右边界被 viewport 裁切。

| 状态 | 浅色、Qt 1× | 深色、小窗口、Qt 1.5× | Windows 原生 |
| --- | --- | --- | --- |
| 默认首页 | [查看](home-light-1x.png) | [查看](home-dark-1.5x-compact.png) | [查看](home-light-1x-windows.png) |
| 多行输入 | [查看](home-multiline-light-1x.png) | [查看](home-multiline-dark-1.5x-compact.png) | [查看](home-multiline-light-1x-windows.png) |
| 工作台选项 | [查看](home-options-light-1x.png) | [查看](home-options-dark-1.5x-compact.png) | [查看](home-options-light-1x-windows.png) |

普通窗口为 1440×900，紧凑窗口为 1020×760，均为逻辑尺寸。两组离屏检查的实际 device pixel ratio 分别为 1 和 1.5；Windows 原生检查设置 `QT_SCALE_FACTOR=1`，实际 device pixel ratio 为 1.5，逻辑 DPI 为 96。详细结果保存在同目录的 `report-*.json`。

三组截图检查均验证：默认高度缩短约 20%、两句文案可见、至少三行编辑空间、多行增高及清空回缩、滚动后输入卡片不超出 viewport、工作台选项原有展开／收起，以及主题预览不写入保存配置。使用隔离示例数据，不读取个人会话、不调用真实模型。

现有 `test_workspace_presentation.py` 与 `test_chat_experience.py` 共 30 项回归通过；补充滚动宽度同步后，再次通过 6 项工作台呈现回归。覆盖共享输入区草稿、首页与对话切换、权限、附件与粘贴、选择菜单及运行时主按钮等原有行为。

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/verify_composer_process.py --home-only --output docs/screenshots/home-refinement-20261025
.venv/Scripts/python.exe -X utf8 -B scripts/verify_composer_process.py --home-only --dark --scale 1.5 --compact --output docs/screenshots/home-refinement-20261025
.venv/Scripts/python.exe -X utf8 -B scripts/verify_composer_process.py --home-only --native --output docs/screenshots/home-refinement-20261025
.venv/Scripts/python.exe -X utf8 -X faulthandler -m pytest -q test/test_workspace_presentation.py test/test_chat_experience.py
```
