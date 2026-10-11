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

当前限制：容量是估算，未对所有模型逐一校准；多模态预算不可用时明确拒绝开启该请求的压缩；此轮未构建冻结发行包。
