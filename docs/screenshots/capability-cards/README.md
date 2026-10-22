# 能力卡片界面验证

2026-10-22，使用实际 PySide6 主窗口、隔离的 AppData 和示例数据生成。服务测试失败为受控演示状态，未连接真实 MCP 服务。

三页共用卡片、操作和响应式网格。验证窗口为 1120×820 与 900×680 个逻辑像素；`QT_SCALE_FACTOR` 分别为 1、1.25、1.5。截图脚本检查可见卡片的标题、说明和按钮均在卡片边界内。

- [技能](skills-1.png)、[MCP 服务](mcp-1.png)、[专家](experts-1.png)
- [小窗口技能 / 150%](skills-small-1.5.png)、[小窗口专家 / 125%](experts-small-1.25.png)
- [MCP 测试失败](mcp-error-small-1.png)、[搜索无结果](mcp-empty-1.png)
- [MCP 编辑页 / 150%](mcp-editor-1.5.png)、[专家编辑页 / 150%](expert-editor-small-1.5.png)
- [主题预览](experts-preview-1.png)、[取消预览后](experts-restored-1.png)
- [深色技能](skills-dark-1.png)、[深色 MCP](mcp-dark-1.png)、[深色专家](experts-dark-1.png)

复现：

```powershell
.\.venv\Scripts\python.exe scripts/render_capability_cards.py
$env:QT_SCALE_FACTOR = '1.25'
.\.venv\Scripts\python.exe scripts/render_capability_cards.py
$env:QT_SCALE_FACTOR = '1.5'
.\.venv\Scripts\python.exe scripts/render_capability_cards.py
Remove-Item Env:QT_SCALE_FACTOR
```

针对性回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -q test/test_capability_cards.py test/test_skillhub_ui.py test/test_unified_management.py test/test_browser_skill_ui.py test/test_settings_ui_styles.py
```

结果为 **40 项测试、4 个子用例通过**。行为检查覆盖搜索后的操作目标、卡片开关保存与放弃、内置专家恢复、只读专家复制、测试中状态、测试结果与配置绑定、无结果状态、编辑返回、切换页面后的网格重排及调整窗口时的键盘焦点。SkillHub 的两个旧测试夹具已按现有产品规则补全：自建技能使用明确来源身份，版本选择发出用户激活事件；这两项在未修改的 `HEAD` 中也能复现原失败。

额外执行 `test/test_theme_static_coverage.py` 有 1 项既有失败：二维码绘制和 HTML 打印样式共 4 处固定颜色。使用相同扫描规则比较未修改 `HEAD` 与工作区，命中的代码内容完全相同；本次新增卡片组件及修改的技能文字组件均无固定颜色命中。未改动二维码或打印行为。

这些检查不包含安装包构建、真实外部服务联调或整仓回归。
