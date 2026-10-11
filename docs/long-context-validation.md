# 超长上下文验证记录

验证日期：2026-10-11。以下结果针对源码工作区，不代表已发布安装包。

## 确定性验证

```powershell
.venv/Scripts/python.exe -X utf8 -W ignore -m unittest discover -s test -p 'test_long_context*.py' -q
```

31 项通过。覆盖配置显式保存与重启、保存失败回滚、模型容量持久化、默认关闭路径、四窗口投影、真实 Agent Loop 三个以上窗口、18 次工具各执行一次、窗口内前缀、超大工具结果、中文检索与分页、会话隔离、历史编辑与重排、分支、事务失败、提交回执丢失、停止后的迟到提交、检查点格式修复、过大当前输入、关闭后完整历史超限、请求启动失败日志、原生服务端工具禁用、协议输出预留、容量与计费分离、运行提示去重和主题撤销。

界面用同一组测试在 `QT_SCALE_FACTOR=1`、`1.25`、`1.5` 下各验证 6 项；截图检查确认设置和提示可阅读，150% 时浮窗有滚动入口且 Esc 可关闭。生成截图可设置 `COWORK_CONTEXT_SCREENSHOTS` 输出目录。

已通过的相关回归：消息存储 29 项、保存队列 8 项、运行日志 21 项、启动流程 14 项、DeepSeek/OpenAI Provider 70 项、Anthropic Provider 4 项、执行授权 24 项、会话操作 5 项、默认模型设置 8 项、对话完整性 11 项、数据安全集成 8 项。

现有 `test_token_usage.py` 有 7 条失败断言仍期待旧版按钮/详情文案。在 `HEAD:main.py` 基线上独立运行得到相同的 7 条失败；本次未改变对应文案函数，也没有改旧断言来掩盖差异。

`scripts/check_docs.py` 未通过：12 份既有文档的当前版本声明仍为 5.2.5，而 `APP_VERSION` 为 5.2.6。已核对 `HEAD` 中同样存在该差异，本次未扩展修改无关发布文档。`git diff --check` 通过。

代表性验收截图：[125% 设置](images/long-context/settings-125.png)、[150% 用量浮窗](images/long-context/usage-150.png)、[压缩完成](images/long-context/compression-completed.png)、[主题预览](images/long-context/theme-preview.png)。

## 真实模型连续性

复现入口（会产生 API 用量）：

```powershell
.venv/Scripts/python.exe -X utf8 -W ignore scripts/smoke_long_context.py --live
```

使用当前已配置的 `gpt-5.6-sol` / Responses，独立临时数据库和合成会话。为在有限成本内触发切换，仅在测试进程中声明 32000 tokens 容量、8192 输出预留；300k 目标下有效上限为 21760。用户配置和真实会话未修改。此测试验证续接行为，不验证服务是否真的支持 300k 或 1m。

早期证据独立存于被压缩的历史消息中，当前用户要求不重复携带答案。第二窗口把颜色从 blue 改为 teal，第三窗口的最近用户消息只有“继续任务”。三次模型回答均正确：

| 窗口 | 发票总额 | 有效颜色 | 早期证据码 |
| --- | --- | --- | --- |
| 1 | 42 | blue | EMBER-42 |
| 2 | 42 | teal | EMBER-42 |
| 3 | 42 | teal | EMBER-42 |

该次运行约 153 秒，形成 3 个窗口、保留 34 条历史消息，早期证据仍能按 ID 完整读取。15 次检查点请求共返回输入 113344、输出 5547、总计 118891 tokens，其中缓存输入 58880；另有 3 次验证回答总计 15028 tokens。缓存输入包含在输入中，不能重复相加。服务未返回本次独立测试的实际货币费用，未按未知价格推算。

## 持久化和缓存约束

`ContextWindowManager` 只生成模型请求副本。UI 的普通消息快照先经过 `ChatSaveWorker`，其后的 prepare/commit 请求独立排队、不可合并。来源 ID、内容指纹、历史代次、前窗口和操作有效期在提交时复核。短事务同时保存检查点并切换活动窗口，停止状态与提交在同一运行日志锁下协调。

窗口与检查点使用专用表，历史代次使用独立列，普通会话 metadata 保存不能覆盖窗口指针。检查点请求不携带函数工具、原生搜索或图片生成工具；具有独立请求 ID、用量和诊断状态。正常请求和整理请求共用 Provider 的取消、网络重试及数据安全处理。

输出预留沿用各协议的请求字段：OpenAI 官方 Chat Completions 使用 `max_completion_tokens`，兼容服务使用 `max_tokens`，Responses 使用 `max_output_tokens`，Anthropic 使用 `max_tokens`。OpenAI 官方字段同时限制可见输出和 reasoning tokens，依据[官方 API 定义](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)及本地 SDK 核对；没有为兼容错误增加自动切换协议或重复请求。

当前限制：容量是估算，未对所有模型逐一校准；没有计量规则或无法取得尺寸的图片不计入本地预算，文本仍正常统计和压缩，图片继续按原流程发送；其他未支持的附件类型仍明确报错。此轮未构建冻结发行包。

## 历史查询与图片组合回归（2026-10-11）

后台工具入口现在先由运行所有者完成历史保存屏障，再把绑定本会话的上下文传入冻结调用器。Agent Loop 回归覆盖压缩换窗、生成新的工具证据、`history_search` 检索和 `history_read` 分页读取，并检查保存发生在运行所有者线程；保存失败时不启动后台读取。

图片估算只作用于预算副本，未修改图片精度、实际请求或聊天账本。已知 GPT 模型按[官方图片计量规则](https://developers.openai.com/api/docs/guides/images-vision)估算，Claude 按[官方视觉计量规则](https://platform.claude.com/docs/en/build-with-claude/vision)估算；缩放后的分块数采用上界，仍由实际用量向上校准。未知模型不读取图片文件、不猜测图片用量，仅记录未计入数量。远程图片不为估算额外下载。

新增用例覆盖：短图片请求、纯文本内容块、图片原样进入下一轮及实际 Responses 投影、已知与未知模型的文本换窗、最近完整工具轮次保留、原始账本不变、窗口内前缀稳定，以及未计量图片在用量浮窗中的说明。此轮使用离线 Provider 模拟和实际协议序列化，不将结果宣称为真实模型理解验收。

运行入口：`test_long_context*.py`（37 项）、`test_context_images.py`（4 项）、`test_tool_tasks*.py`（23 项），全部通过。用量浮窗另在 100% 和 150% 下验证，未计量图片提示截图见[100% 用量说明](images/long-context/image-budget.png)、[150% 用量说明](images/long-context/image-budget-150.png)。

## 启用后直接沿用档位（2026-10-11）

取消模型容量未知时的启动拦截，移除编辑模型中的容量和输出预留填写项。未知容量使用设置中的输入目标，快照保留 `model_tokens=null` 和 `budget_source=target_setting`，不把目标伪装成模型真实容量。已知容量继续自动限制预算；已有模型元数据在编辑其他字段时保留。关闭功能后，未知容量不会被错误地按实验档位拦截完整历史。

回归覆盖：未填写容量的自定义模型在 300k／1m 下实际进入 Agent 请求流程；超出 300k 档位触发点的历史可正常压缩并保持完整保存；用量浮窗沿用所选档位且比例正确；编辑模型不再出现额外填写项。此项只调整预算来源及界面，不改变系统提示、工具定义、消息排列和既有窗口提交规则。

`test_long_context*.py` 40 项及 `test_default_model_settings.py` 8 项通过。模型编辑与用量浮窗在 100%／150% 下各 2 项通过，并检查了实际截图：[模型编辑](images/long-context/model-editor.png)、[150% 模型编辑](images/long-context/model-editor-150.png)、[150% 用量浮窗](images/long-context/usage-150.png)。此轮使用离线 Provider 模拟，未额外发起真实 API 请求。
