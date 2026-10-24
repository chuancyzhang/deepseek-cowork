# 瞬时系统小窗口检查

## 2026-10-24：历史对话与附件展示回归

修复后，在 Windows 原生平台以 `QT_SCALE_FACTOR=1` 和 `1.5` 分别检查 40 个阶段，异常顶层窗口均为 **0**。专家回复现在只保留一个专家名称标题；普通助手显示 Cowork，旧回复不会跟随当前专家选择改名。

本次定位到的引入位置：

- `96d03f54`（统一消息附件与对话引用的气泡内展示）新增了用户正文和附件区域的 `setVisible(condition)`，但两个控件在调用时尚无父级。[修复前事件记录](baseline-history.json)在历史渲染、重载和附件消息中捕获到 4 次原生窗口显示，分别指向 `AutoResizingTextEdit` 和 `UserMessageContext`。修复为构造时即绑定消息气泡父级。
- `2b6ee8be`（首页、聊天等页面展示调整）给助手气泡新增 Cowork 标题，但历史摘要仍保留旧的专家标签，造成两套身份标题。历史摘要、实时轮次及单独回复现共用 `ConversationIdentityHeader`；历史摘要的正文和展开阶段不再重复标题，子任务回复沿用面板的专家标题。旧专家标签工厂已移除，历史摘要也在构造时绑定聊天容器。
- 昨天 `ded91baa` 修过的入口仍通过。此前检查集中在菜单和页面构造，本次补充历史重载、实际对话切换、纯附件消息、详情展开、专家切换及深色主题预览。上次思考时间线验证没有覆盖消息构造期间的原生窗口事件。

验证范围与结果：

- [Windows 倍率 1](report-history-windows-1x.json)、[倍率 1.5](report-history-windows-1.5x.json)：每次 40 阶段，零异常窗口。倍率会叠加系统 DPI，不等同于 Windows 显示设置百分比。
- `test_child_window_lifecycle.py`、`test_conversation_agents.py`、`test_workspace_presentation.py`：31 项通过，8 个子测试通过。
- `test_theme_runtime_coverage.py` 中原生窗口、未挂载消息和可见消息布局的 3 项回归通过。共 34 项测试，8 个子测试通过。
- 扫描 `main.py` 和 `ui/*.py` 的标准 Qt 控件及派生控件构造路径，修复后没有发现直接“无父级构造 → 显示 → 加入布局”的剩余命中。
- 使用隔离配置和示例数据。未执行真实模型请求、外部服务授权或打包版本测试；消息保存、模型请求及 prompt prefix 未变。

| 场景 | 倍率 1 | 倍率 1.5 |
| --- | --- | --- |
| 专家历史回复 | [查看](history-expert-1x.png) | [查看](history-expert-1.5x.png) |
| 展开详情与普通助手回复 | [查看](history-details-1x.png) | [查看](history-details-1.5x.png) |
| 深色主题与窄窗口 | [查看](history-dark-narrow-1x.png) | [查看](history-dark-narrow-1.5x.png) |

```powershell
$env:QT_QPA_PLATFORM='windows'
$env:QT_SCALE_FACTOR='1'
.\.venv\Scripts\python.exe scripts/verify_child_window_lifecycle.py --history-screenshots --output docs/screenshots/child-window-audit/report-history-windows-1x.json
$env:QT_SCALE_FACTOR='1.5'
.\.venv\Scripts\python.exe scripts/verify_child_window_lifecycle.py --history-screenshots --output docs/screenshots/child-window-audit/report-history-windows-1.5x.json
$env:QT_QPA_PLATFORM='offscreen'
$env:COWORK_EXTERNAL_BACKGROUND_SERVICES='0'
.\.venv\Scripts\python.exe -m pytest test/test_child_window_lifecycle.py test/test_conversation_agents.py test/test_workspace_presentation.py -q
.\.venv\Scripts\python.exe -m pytest test/test_theme_runtime_coverage.py -q -k 'active_theme_does_not_flash or detached_message_theme_visibility or visible_chat_layout_keeps'
```

## 2026-10-23：首次排查

2026-10-23：修复子控件在加入页面前执行 `setVisible(True)`，短暂弹出带系统标题栏小窗口的问题。修复只调整控件父级和构造顺序。

## 已确认的触发位置

| 入口 / 条件 | 被误当作独立窗口的控件 |
| --- | --- |
| 输入框“+ → 专家 / 对话” | 选择器标题 |
| 带“取消 / 应用”的技能选择器 | 取消按钮 |
| 资料库切换腾讯文档 | 已完成授权按钮 |
| 资料库切换乐享 | 打开官方配置页按钮 |
| 可编辑技能的文件页 | 保存并重载按钮 |
| 编辑已有计划的常用任务 | 立即运行计划按钮 |
| 新增 MCP 连接 | 导入配置按钮 |
| 已选择自定义 Python / Node 下载源时进入设置或项目页 | 下载源地址输入框 |
| 对话中填写文本、单选或多选的自定义回答 | 输入框 |
| 子任务事件包含详情 | 详情按钮 |

聊天软件搜索框也补齐父级：当前 5 个渠道不会显示该框，回归测试用 7 个渠道覆盖已有的 `> 6` 条件。

直接切换即时技能、连接器、模式和模型，在本次检查中未单独触发异常窗口。它们与专家选择的连续切换流程已覆盖。

## 证据与结果

- [初次定位](baseline-initial.json)：20 个阶段捕获 6 次异常显示，包含专家重复打开、对话引用、事务式技能选择和两种云资料库连接页。
- [扩展定位](baseline-expanded.json)：先修复上述入口，再覆盖自定义下载源及编辑器、对话卡片等条件，另捕获 11 次异常显示。两份基线属于不同检查批次。
- [离屏复查](report-offscreen.json)：27 个阶段，异常顶层窗口为 0。
- [Windows 原生复查，倍率 1](report-windows-1x.json)与[倍率 1.5](report-windows-1.5x.json)：每次 27 个阶段，异常顶层窗口均为 0。文件名的倍率指 `QT_SCALE_FACTOR`，会叠加操作系统 DPI，不代表系统显示设置的百分比。
- 测试共 **88 项通过，15 个子测试通过**。另有 1 项既有失败：`FavoritesUiTests.test_favorites_follow_theme_preview_and_restore_without_changing_data` 的卡片颜色断言。单独加载 `git show HEAD:main.py` 复跑也在同一断言失败，本次未修改该主题逻辑。
- Python 编译和 `git diff --check` 通过。

事件过滤器在构造控件前安装，记录每次 `QEvent.Show` 中意外出现的 `isWindow()` 对象及其调用栈；这样可以捕获截图前已经消失的小窗口。另扫描了 `main.py`、`ui/`、`core/` 中标准 Qt 控件“无父级构造 → 显示 → 加入布局”的直接路径。剩余静态命中是团队空间已挂载后的展开回调，人工检查排除。

检查采用临时配置和示例数据，覆盖真实主窗口、级联菜单切换、7 个页面路由、3 个资料库来源及相关条件表单。没有验证外部授权、真实模型请求、MCP 网络连接或打包版本。

## 截图

| 场景 | 倍率 1 | 倍率 1.5 |
| --- | --- | --- |
| 专家选择 | [查看](expert-picker-1x.png) | [查看](expert-picker-1.5x.png) |
| 历史对话引用 | [查看](conversation-picker-1x.png) | [查看](conversation-picker-1.5x.png) |
| 腾讯文档连接页 | [查看](tencent-library-1x.png) | [查看](tencent-library-1.5x.png) |
| 乐享连接页 | [查看](lexiang-library-1x.png) | [查看](lexiang-library-1.5x.png) |

## 复现

```powershell
$env:QT_QPA_PLATFORM='windows'
$env:QT_SCALE_FACTOR='1'
.\.venv\Scripts\python.exe scripts/verify_child_window_lifecycle.py --screenshots --output docs/screenshots/child-window-audit/report-windows-1x.json
$env:QT_SCALE_FACTOR='1.5'
.\.venv\Scripts\python.exe scripts/verify_child_window_lifecycle.py --screenshots --output docs/screenshots/child-window-audit/report-windows-1.5x.json
```

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:COWORK_EXTERNAL_BACKGROUND_SERVICES='0'
.\.venv\Scripts\python.exe -m pytest test/test_child_window_lifecycle.py test/test_conversation_actions.py test/test_favorites_ui.py test/test_sub_agent_monitor_ui.py test/test_knowledge_library_ui.py -q
.\.venv\Scripts\python.exe -m pytest test/test_conversation_linear_interactions.py -q -k 'skill_picker or product_popover or product_action_row or inline_choice or inline_questionnaire or legacy_interaction_dialog'
```

第一组为 77 项通过、15 个子测试通过和上述 1 项基线失败；第二组为 11 项通过。
