# 输入区目录、专家与级联菜单

2026-10-23，Windows / Python 3.14 / PySide6。使用临时配置与合成项目、专家和历史记录，没有连接真实模型或 MCP 服务。

- 工作目录显示在输入框外侧下方，复用原有项目选择逻辑；完整路径通过提示显示，已有内容的对话仍禁止切换。
- “+”中的模式、专家、技能、连接器、对话支持悬停 280 ms 或点击展开。父菜单保留，子菜单贴近分类箭头；搜索、切换分类、Esc、外部点击和窗口缩放均覆盖。
- 当前专家显示在输入区底部，点击切换、点“×”移除；按会话保存，任务运行中保持可见且禁止修改，未选择时隐藏。仍然每次使用一位专家。
- 740×620 最小窗口中，工作目录、专家、模型、发送区域无裁切或重叠；首页在窄窗口使用箭头提交按钮，为专家名称保留空间。
- 100% / 150% Qt 缩放各生成 17 张截图，包含独立聊天、项目、已有内容、五种子菜单、首页、最小窗口和深色预览。取消主题预览后，已保存主题配置不变。
- 本次只调整入口和呈现，模型请求前缀、工具顺序、专家角色与引用快照流程保持原有实现。

相关回归共 **66 项通过，另有 2 个子用例**：聊天体验、会话引用、专家与工作区呈现共 54 项；菜单及共享浮层 10 项；窄窗口与会话切换 2 项。输入区最终宽度调整后重跑了受影响的聊天体验、工作区和布局用例；构造期间隐藏事件的问题修复后，菜单子集再次全部通过。语法编译与 `git diff --check` 通过。未运行打包程序或系统级 Windows 显示缩放人工验收。

## 复现

在隔离配置下运行回归：

```powershell
@'
import os, tempfile
with tempfile.TemporaryDirectory(prefix='cowork-cascade-tests-') as temp:
    os.environ.update(APPDATA=temp, LOCALAPPDATA=temp, QT_QPA_PLATFORM='offscreen', COWORK_EXTERNAL_BACKGROUND_SERVICES='0')
    import pytest
    result = pytest.main(['test/test_chat_experience.py', 'test/test_conversation_actions.py', 'test/test_conversation_agents.py', 'test/test_workspace_presentation.py', '-q'])
raise SystemExit(result)
'@ | .venv/Scripts/python.exe -X utf8 -
```

同样的隔离环境中另行运行：

```python
pytest.main(['test/test_conversation_linear_interactions.py', 'test/test_main_workspace_linear.py',
             '-k', 'skill_picker or composer_add_popover or product_action_row or product_popover or toolbar_reflows or session_switch_after_sidebar_cycle', '-q'])
```

生成并检查截图：

```powershell
foreach ($scale in @('1', '1.5')) {
    $env:QT_SCALE_FACTOR = $scale
    .venv/Scripts/python.exe -X utf8 scripts/verify_chat_experience.py --workspace-only
}
Remove-Item Env:QT_SCALE_FACTOR
```

代表状态：[专家与目录](selected-expert-1x.png)、[技能子菜单](skills-1x.png)、[专家子菜单](experts-1x.png)、[最小首页](home-minimum-1.5x.png)、[深色子菜单](cascade-dark-1.5x.png)。
