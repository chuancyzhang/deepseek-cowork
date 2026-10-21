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

## 确认请求结束同步（2026-10-21）

- 请求结束由交互服务统一通知，包含会话、运行、请求 ID 与最终状态。旧删除确认仍返回布尔值；执行授权、180 秒安装确认、24 小时连接等待、普通问卷超时自动选择及拷问模式规则均保持原接口与配置。
- 结束通知进入现有守护进程运行事件流，先保存再发送；重连按现有游标重放。真实 TCP 测试覆盖旧工具只返回布尔值、没有 `interaction_response` 的超时路径，确认结束事件先于最终回答，并可从请求后的游标取回。
- 界面不再另设超时计时器。已结束请求清除“待输入”、关联等待通知及操作按钮，保留可复制输入；明确接收成功仍关闭卡片。空响应、通信错误不视为提交成功，过期不进入重新等待。相同结束通知可重复处理，迟到的失败不能恢复已结束的按钮，旧运行不能清理新运行的请求。
- 请求响应、超时、取消共用锁确定结果；终态通知在锁外发送。定向用例覆盖回答先到、取消先到、过期、非法回答、全部输入类型，以及停止与会话隔离。

在隔离 `APPDATA/LOCALAPPDATA`、`QT_QPA_PLATFORM=offscreen` 和 `COWORK_EXTERNAL_BACKGROUND_SERVICES=0` 的进程中执行：

```powershell
.venv/Scripts/python.exe -m pytest -q test/test_interaction_lifecycle.py test/test_interaction_skill.py test/test_execution_authorization.py test/test_interaction_tool_routing.py test/test_core.py::TestInteractionService test/test_conversation_runtime_fixes.py
.venv/Scripts/python.exe -m pytest -q test/test_runtime_stream.py test/test_im_gateway.py test/test_connections.py test/test_core.py::TestDaemonInteractionRoundtrip::test_get_pending_interaction_and_respond_roundtrip
```

界面截图使用真实组件，覆盖正常确认、超时、通信未确认、停止后输入、小窗口及主题预览取消；分别设置 `QT_SCALE_FACTOR=1` 和 `1.5` 执行：

```powershell
.venv/Scripts/python.exe scripts/render_conversation_runtime_screenshots.py --interactions
```

| 场景 | 100% | 150% |
| --- | --- | --- |
| 交互状态，常规宽度 | [查看](assets/conversation-runtime/interactions-wide-1x.png) | [查看](assets/conversation-runtime/interactions-wide-1.5x.png) |
| 交互状态，小窗口 | [查看](assets/conversation-runtime/interactions-small-1x.png) | [查看](assets/conversation-runtime/interactions-small-1.5x.png) |

本次不修改模型提示词、工具定义、模型历史或缓存前缀；不调用真实模型、不执行真实删除、不打包。最终产品验收由用户完成。

验证边界：相关完整窗口用例在同一进程组合运行时出现进程退出，未给出断言错误；用 `HEAD` 中的 `main.py` 与对应原测试源码作独立进程对照，也在相同停止用例处退出，未定位该进程问题的根因。此次涉及的正常提交、失败保留、后台提示、完成状态及停止用例已分别验证通过；不宣称整个窗口测试文件或全仓测试通过。

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
