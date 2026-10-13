# 对话运行与文件展示验证

本次验证覆盖停止、失败工具记录、人工提问及回答末尾的文件列表。截图来自真实 Qt 组件的隔离场景，不代表完整应用或真实模型端到端验收；最终产品验收由用户进行。

## 已验证行为

- 后台工具提问通过守护进程到达所属会话，回答可返回；其他会话或旧运行的请求不会串入。
- 停止取消等待及工具任务，Windows 实际命令的父子进程均退出；守护进程等待工具清理后才确认释放。
- 失败工具保留卡片和详情，写入 SQLite 后重载仍可查看；Provider 历史投影保持原规则。
- 自定义回答直接输入，占位提示为“自定义”；输入会清除单选预设，选择预设会清空自定义。提交未被接收时保留回答。
- 文件默认显示 5 行，可展开、收起，通过鼠标或键盘打开；长路径、小窗口和跨磁盘路径可用，文件登记不因折叠丢失。实际工作区外文件的 Markdown 引用可显示并打开，行内显示完整路径。
- 100% 和 150% 缩放、明暗主题及主题预览取消恢复；预览使用临时主题仓库，取消后浅色截图逐像素一致，保存配置未变化。

## 复现入口

从仓库根目录使用项目虚拟环境：

```powershell
.venv/Scripts/python.exe -m unittest discover -s test -p test_interaction_tool_routing.py -q
.venv/Scripts/python.exe -m unittest discover -s test -p test_tool_tasks.py -q
.venv/Scripts/python.exe -m unittest discover -s test -p test_message_persistence.py -q
.venv/Scripts/python.exe -m unittest discover -s test -p test_conversation_runtime_fixes.py -q
```

另对既有对话测试中的人工提问、停止状态与工具循环保护，以及工具任务 UI、消息存储和运行日志执行了相关回归。未运行整个仓库测试集，未调用真实模型。

截图脚本使用临时数据，不读取用户对话：

```powershell
$env:QT_SCALE_FACTOR = '1'
.venv/Scripts/python.exe scripts/render_conversation_runtime_screenshots.py
$env:QT_SCALE_FACTOR = '1.5'
.venv/Scripts/python.exe scripts/render_conversation_runtime_screenshots.py
Remove-Item Env:QT_SCALE_FACTOR
```

## 截图

| 场景 | 100% | 150% |
| --- | --- | --- |
| 浅色常规宽度 | [查看](assets/conversation-runtime/light-wide-1x.png) | [查看](assets/conversation-runtime/light-wide-1.5x.png) |
| 浅色窄窗口 | [查看](assets/conversation-runtime/light-small-1x.png) | [查看](assets/conversation-runtime/light-small-1.5x.png) |
| 文件展开 | [查看](assets/conversation-runtime/files-expanded-1x.png) | [查看](assets/conversation-runtime/files-expanded-1.5x.png) |
| 深色窄窗口 | [查看](assets/conversation-runtime/dark-small-1x.png) | [查看](assets/conversation-runtime/dark-small-1.5x.png) |
| 取消主题预览后 | [查看](assets/conversation-runtime/light-restored-1x.png) | [查看](assets/conversation-runtime/light-restored-1.5x.png) |
