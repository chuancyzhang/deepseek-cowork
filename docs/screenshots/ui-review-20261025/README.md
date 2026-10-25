# UI/UX 复核与实现（2026-10-25）

本轮以现有产品功能为基础调整呈现，没有删除业务功能。实现涉及首页、常用任务、能力页、设置标题、右侧技术详情、运行失败提示和用量浮窗。资料库、团队空间和项目管理复用原流程，仅共用页面标题整理。

## 实际检查的功能

| 区域 | 修改前的电脑操作检查及保留范围 |
| --- | --- |
| 首页与输入 | 四个快捷入口、最近任务、工作台选项、专家/技能/MCP/对话引用、模型与工作目录 |
| 工作区 | 文件/网页/终端标签，浮动目录、来源/类型/排序/固定，预览和文件操作入口 |
| 任务详情与子任务 | 执行上下文、调用记录、技术详情、完整原文复制及子任务入口 |
| 常用任务 | 搜索、筛选、新建、编辑、能力、运行项目、定时、消息绑定、历史与恢复入口 |
| 设置 | 外观与阅读、回答偏好、模型与服务、消息连接、企业账号、隐私与数据、文件与存储、高级与维护及其子页 |
| 能力 | 发现/我的/内置技能、详情与导入；MCP 编辑/导入/测试；专家详情/编辑/复制 |
| 资料库 | 三种来源、目录、最近访问/本地文件/上传记录、阅读、引用、来源菜单与连接失败状态 |
| 项目及团队空间 | 项目、对话、记忆、归档入口；团队空间登录失效说明 |

以上检查包含阅读、导航和打开表单，不代表执行了删除、安装、登录、上传、发送或所有外部服务操作。原型没有展示的功能继续保留。

## 实现与验证

- 首页只保留一个任务标题，工作台选项改为轻量分隔行；现有快捷任务提示词未修改。
- 常用任务取消固定窄侧栏，按填写顺序纵向布局；完整任务、能力、计划和消息配置仍走原保存流程。
- 能力页去掉宣传标题和重复页面头，添加技能与分类导航同行；MCP、专家保留各自操作。
- 技术详情未选中时显示调用记录入口；选中、失败详情与切换会话分别验证，完整结果及选择位置保留。
- 失败提示保留实际原因、重试和错误详情，移除泛化重复标题。
- 用量浮窗限制在应用与屏幕交集内，完整统计可一键复制；展开原文、滚动与 Esc 保持可用。
- 共享标题、提示及新控件绑定父级与主题。模型请求、系统提示词、工具定义/顺序、历史组织及权限没有变化；运行时稳定 prompt prefix 不受影响。主题文案默认值只改变 UI 文案及主题工具返回的文案说明。

针对性回归：41 项测试及 9 个子测试通过，覆盖共享输入区、页面切换、未保存守卫、MCP 导入编辑、完整技术结果、会话隔离和主题包。

```powershell
.venv/Scripts/python -m pytest test/test_workspace_presentation.py test/test_unified_management.py test/test_task_details.py test/test_theme_package.py -q
.venv/Scripts/python scripts/verify_child_window_lifecycle.py --output docs/screenshots/ui-review-20261025/window-audit.json
.venv/Scripts/python scripts/verify_ui_review.py
.venv/Scripts/python scripts/verify_ui_review.py --dark --scale 1.5 --compact
.venv/Scripts/python scripts/verify_ui_review.py --native --interactive
```

每个截图配置验证 7 项关键不变量并生成 14 个状态截图，数据均为独立临时数据，不调用模型。原生模式另通过电脑操作复核常用任务进入/展开/返回、能力分类、MCP 添加表单及无修改返回。

文件名中的 100/150 表示 `QT_SCALE_FACTOR=1/1.5`，实际平台、逻辑 DPI 与设备倍率记录在各 `report-*.json` 中；离屏 150% 不等同于修改 Windows 系统缩放。`window-audit.json` 的 40 个阶段均未发现意外顶层窗口。

| 状态 | 浅色 | 深色小窗口 |
| --- | --- | --- |
| 首页选项 | [查看](home-options-light-100.png) | [查看](home-options-dark-150-compact.png) |
| 常用任务 | [查看](favorite-editor-light-100.png) | [查看](favorite-editor-dark-150-compact.png) |
| 定时设置 | [查看](favorite-schedule-light-100.png) | [查看](favorite-schedule-dark-150-compact.png) |
| MCP | [查看](capabilities-mcp-light-100.png) | [查看](capabilities-mcp-dark-150-compact.png) |
| 失败恢复 | [查看](failure-light-100.png) | [查看](failure-dark-150-compact.png) |
| 用量详情 | [查看](usage-light-100.png) | [查看](usage-dark-150-compact.png) |

## 既有测试与边界

扩展运行 `test_token_usage.py`、`test_ui_design_system.py` 得到 19 项通过、12 项失败；加载未修改的 `HEAD:main.py` 后相同 12 项失败复现，主要涉及旧用量文案预期、旧能力卡片/专家预期及字体环境。未据此修改用量计算或能力业务规则，也不声明全仓测试通过。

修正两处验证自身问题：旧管理测试仍要求切换分类时丢弃草稿，现按已存在的“切页保留、离开确认”验证；技术详情测试复用异步退出辅助函数，避免保存线程尚未结束就删除窗口。

未构建或发布安装包；真实模型生成、企业登录、MCP 远端测试、资料上传和定时消息发送未做端到端执行。当前运行的旧发行版不会因源码修改自动更新。
