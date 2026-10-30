# 历史编辑与工具文件链接验证

2026-10-30，Windows / PySide6，使用临时配置、合成会话与真实本地 Markdown 文件。未连接真实模型、外部账号或工具服务，未构建发行包。

## 修复与行为

- 短消息原来会将正文编辑器固定到 80 逻辑像素；进入编辑后只显示按钮，正文仍使用阅读宽度。现在编辑时使用可用气泡宽度，并计入边框；取消恢复原文及自然宽度。主题切换沿用当前编辑状态、字体和选择配色，隐藏会话仍按编辑状态计算宽度。
- 工具文件链接来自结构化参数及 `apply_patch` 文件头，覆盖读取、创建、更新、移动、删除；命令文本不猜测路径。参数原值、工具执行、消息历史和模型请求均不改变，运行时 prompt prefix 与缓存策略无影响。
- 链接使用主题 `chat_link`，显示蓝色、下划线和加粗。路径转义后显示，内部按索引选择原路径；保留空格、中文、`&` 和 `#`。多文件补丁有独立链接，工具时间线使用可用列宽，避免富文本首选宽度把路径挤成窄列。
- 点击链接绑定来源会话，复用右侧文件预览与未保存编辑确认。失效路径提示无法预览并保留当前文件，后台会话的过期点击不会影响当前会话。读取文件不会注册交付物。日志覆盖请求、打开、取消和拒绝原因，不新增文件正文日志。

## 自动检查

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pytest test/test_conversation_file_navigation.py test/test_conversation_actions.py test/test_tool_tasks_ui.py -q
.venv/Scripts/python.exe -X utf8 -B -m pytest test/test_theme_runtime_coverage.py -k 'all_primary or detached_message or visible_chat' -q
.venv/Scripts/python.exe -X utf8 -B -m pytest test/test_conversation_linear_interactions.py -k 'apply_patch_card or short_user_message or user_message_actions' -q
```

共 22 项测试、8 个子测试通过，覆盖编辑宽度、缩窄、取消、键盘提交、附件与引用保留、单独路径分发、缺失文件、来源会话隔离、分支文件映射，以及相关主题和工具记录回归。

## 窗口检查与截图

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/verify_conversation_file_navigation.py
.venv/Scripts/python.exe -X utf8 -B scripts/verify_conversation_file_navigation.py --platform windows --scale 1.5
```

两种模式均验证实际鼠标点击路径、右侧 Markdown 预览、多文件选择、缺失文件提示、独立工具详情、会话来回切换、历史重载两次并展开工具记录、主题预览取消及原生窗口生命周期。构造前安装 `QEvent.Show` 检查，未发现额外顶层窗口。

- 离屏：`QT_QPA_PLATFORM=offscreen`、`QT_SCALE_FACTOR=1`、DPR 1，检查 1440×900 与 1100×760 逻辑窗口。
- 原生：`QT_QPA_PLATFORM=windows`、系统缩放 150%、`QT_SCALE_FACTOR=1.5`，实际 DPR 2.25，窗口按屏幕可用范围限制为 1098×619 逻辑像素。额外 Qt 缩放叠加系统缩放，不等同于仅 Windows 150%。
- 验证脚本使用 WebEngine 软件渲染参数；首次离屏运行因 D3D context lost 中止，按仓库既有截图方式关闭 GPU 后完成。应用渲染设置没有改动，本次不覆盖发行包 GPU 路径。

| 场景 | 离屏 | Windows 原生与额外缩放 |
| --- | --- | --- |
| 历史编辑 | [查看](images/conversation-file-navigation/inline-edit-offscreen-1x.png) | [查看](images/conversation-file-navigation/inline-edit-windows-1.5x.png) |
| 文件路径与右侧预览 | [查看](images/conversation-file-navigation/file-preview-offscreen-1x.png) | [查看](images/conversation-file-navigation/file-preview-windows-1.5x.png) |
| 深色与窄窗口 | [查看](images/conversation-file-navigation/file-preview-dark-compact-offscreen-1x.png) | [查看](images/conversation-file-navigation/file-preview-dark-compact-windows-1.5x.png) |
