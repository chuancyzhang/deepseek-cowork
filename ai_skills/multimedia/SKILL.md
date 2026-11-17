---
name: multimedia
description: Generate or edit images, generate videos, and analyze videos with the user's configured media models.
description_cn: 生成或编辑图片、生成视频、分析视频内容，使用技能配置中对应的模型。
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
allowed-tools: media_create media_task
---

# 多媒体

用户需要生成或编辑图片、生成视频、理解视频时使用。图片理解继续使用对话模型的视觉能力；本技能不提供音频能力。

1. 根据需求选择 `image_generate`、`video_generate` 或 `video_understand`，通过 `tool_search` 查找 `media_create` 和 `media_task`。模型、服务和凭据由宿主按技能配置提供，不要求用户在聊天中粘贴密钥。
2. 将完整需求、素材和必要参数交给 `media_create`。视频生成的素材是参考图片；视频理解及 Agnes 视频参考图需要服务可访问的 URL。没有可用链接时说明要求，不擅自上传本地文件或换用其他服务。
3. 记录返回的 `task_id`，使用 `media_task` 查询、等待和恢复原任务。每次等待最多 30 秒。持续跟进，直到完成、失败、等待用户配置或用户停止。
4. `needs_configuration` 表示需求已经保存但尚未提交。引导用户打开“能力 → 技能 → 多媒体”，配置模型后返回聊天点击“继续任务”。不要重复调用 `media_create`，也不要让用户重新发送原需求。
5. `unknown` 表示提交结果未确认，先核查服务后台并保留原任务。恢复仅查询原远端任务或下载已有成果，不能重新生成。`stop_waiting` 仅停止本地等待，不能声称远端任务已取消。
6. 交付真实完成的文件，使用本地 Markdown 文件链接；图片、视频可直接展示。失败时说明原因、已保留的需求与成果及可用恢复入口，不伪造完成状态。

停用技能阻止新任务；已有任务和成果保留。保存配置不发起生成。运行中的任务使用启动时的模型与服务配置，不能悄悄切换模型重做。
