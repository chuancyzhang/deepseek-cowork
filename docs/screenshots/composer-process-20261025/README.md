# 输入区、过程与通知验证（2026-10-25）

## 用户可见变化

- 模式、专家、技能、连接器和对话使用一致的列表样式：左侧图标、两级文字、整行悬停底色和选中勾选。保留 `>` 级联展开及键盘操作。
- 输入区专家点击即生效，取消专家可从标签或选择器操作；技能和连接器即时多选。组合 `@` 选择器保留原暂选／应用边界。
- 点击菜单外的正文、空白或输入区会关闭菜单链；窗口失活也关闭，关闭不清空草稿。Esc 先关闭当前层。
- 运行时只有一个主按钮：空草稿为停止，有文字或附件为发送补充，清空后恢复停止。写补充时可从“+”停止，草稿保留；回车始终是发送。正在停止时禁用主按钮。
- 过程按实际思考、说明和工具显示时间线图标。工具标题、状态、详情同排；技术名称及目标路径、失败原因各占独立行。未知思考耗时不显示虚构数字。
- 全局通知移至任务区域底部居中，聊天时位于输入框上方。会话内可恢复错误保留原位原因、重试和详情入口。

## 代表性截图

| 场景 | 浅色 | 深色 150% 小窗口 |
| --- | --- | --- |
| 技能悬停 | [截图](skill-cascade-light-1x.png) | [截图](skill-cascade-dark-1.5x-compact.png) |
| 专家 | [截图](expert-cascade-light-1x.png) | [截图](expert-cascade-dark-1.5x-compact.png) |
| 模式 | [截图](mode-cascade-light-1x.png) | [截图](mode-cascade-dark-1.5x-compact.png) |
| 连接器选中 | [截图](connector-cascade-light-1x.png) | [截图](connector-cascade-dark-1.5x-compact.png) |
| 运行中停止 | [截图](running-stop-light-1x.png) | [截图](running-stop-dark-1.5x-compact.png) |
| 运行中补充 | [截图](running-send-light-1x.png) | [截图](running-send-dark-1.5x-compact.png) |
| 历史过程 | [截图](history-expanded-light-1x.png) | [截图](history-expanded-dark-1.5x-compact.png) |
| 错误恢复 | [截图](conversation-recovery-light-1x.png) | [截图](conversation-recovery-dark-1.5x-compact.png) |
| 通知位置 | [截图](toast-chat-light-1x.png) | [截图](toast-chat-dark-1.5x-compact.png) |

带 `windows` 后缀为 Windows 平台的原生控件渲染。额外人工式原生点击验证：打开主菜单后点击正文关闭；打开专家子菜单后点击外部空白，同时关闭两层。所有数据均为隔离验证数据。

## 通知入口检查

已检查直接 `add_system_toast`、统一 `QMessageBox.information` 适配层、导出回调与 `_show_conversation_notice` 调用。按用户任务归纳如下：

| 入口 | 当前呈现与处理 |
| --- | --- |
| 设置、主题、模型、能力、MCP 配置与校验 | 统一短通知；已有页面错误或保存状态继续留在原页面 |
| 文件粘贴、附件、导入导出、工作区文件操作 | 简短结果用通知；失败保留输入或文件，并提供可理解原因 |
| 项目、归档、常用、计划任务和知识库管理 | 统一位置与宽度；原有影响确认边界保持 |
| 任务启动、能力选择与能力变更 | 已有按钮、标签或运行状态反馈时移除重复成功通知；外部能力变更通知保留 |
| 运行异常、重试、分支失败、专家成果打开失败 | 会话内原位提示，保留重试、详情或打开成果入口，不用短通知替代恢复动作 |
| 后台会话、技能沉淀、通用界面异常 | 保留会话归属及必要结果；通用异常可到设置中的运行日志诊断 |

## 验证命令

```powershell
.venv/Scripts/python -X utf8 -X faulthandler -m pytest test/test_chat_experience.py test/test_workspace_presentation.py -q
.venv/Scripts/python -X utf8 -X faulthandler -m pytest test/test_conversation_linear_interactions.py -q -k 'product_popover or skill_picker or steerable_running_composer or normalize_session_ui or composer_context_button_reopens'
.venv/Scripts/python -X utf8 -X faulthandler -m pytest test/test_conversation_actions.py -q -k 'picker or expert_checks or expert_clear'
.venv/Scripts/python -X utf8 scripts/verify_composer_process.py
.venv/Scripts/python -X utf8 scripts/verify_composer_process.py --dark --scale 1.5 --compact
.venv/Scripts/python -X utf8 scripts/verify_composer_process.py --native
```

截图脚本检查草稿保留、按钮切换、父子菜单、工具文字与错误不重叠、真实思考耗时、历史消息不变、折叠后成果和已展开详情保留、详情定位、历史重载无重复工具、通知位置及恢复回调、主题预览不写入保存配置。各次结果保存在同目录 `report-*.json`。

定向回归共 42 项通过：输入区与共享组件 30 项（菜单反复打开与失活关闭新增用例单独确认）、菜单与旧运行状态 8 项、引用／暂选边界 4 项。三组截图脚本各完成 12 项检查；`compileall` 与 `git diff --check` 通过。扩展验证发现的 `window` 字段遮蔽 Qt 方法问题已修复，并以仅主菜单失活、子菜单失活及反复打开覆盖；未将失败的中间运行计为通过。

本次只改变展示和既有操作入口；模型请求构造、系统前缀、工具顺序及运行时专家内容均未改变。运行中的发送和停止验证调用既有入口，未发起真实模型任务。
