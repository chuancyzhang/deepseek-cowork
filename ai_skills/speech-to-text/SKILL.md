---
name: speech-to-text
description: Transcribe audio or video through the configured local pipeline or OpenAI-compatible ASR endpoint, with an explicit choice about optional polishing by the current AI model.
license: Apache-2.0
metadata: {"author": "deepseek-cowork team", "version": "1.0"}
allowed-tools: [transcribe_audio, save_transcript_result]
---

# 语音转文字

按能力设置使用本地 SenseVoice 组件或 OpenAI 兼容 `/audio/transcriptions` 接口转录音频或视频。配置由用户在能力设置中管理，不要向用户索取或在 Tool 参数中传递 API Key、接口地址和远程模型名。

## 必须先确认输出隐私模式

在调用转录工具前，必须先询问用户是否需要 AI 润色，并清楚说明两种模式：

- **不润色**：转录正文只写入工作区，工具结果不会把正文返回给当前对话模型。当前模型只能看到路径和运行状态等元数据，不得再读取生成的转录文件。本地模式不上传音频；远程模式仍会把音频发送到用户配置的 ASR 接口。
- **AI 润色**：转录完成后，原始文字会交给当前会话正在运行的模型处理；如果当前模型是云端服务，转录文字会发送给该服务商。润色结果随后写入工作区。

如果用户已经明确选择，不要重复询问。

## 工作流

1. 确认是否 AI 润色。若本轮恰好有一个由界面保留的语音转文字专用附件，调用工具时省略 `audio_path`，避免把附件路径写进模型上下文。
2. 调用 `transcribe_audio`，`polish` 必须与用户选择一致。通常省略 `model`、`diarize` 和 `speaker_count`，由已保存的后端配置决定默认行为。
3. 当 `polish=false`：
   - 工具已将原始稿写入工作区。
   - 只向用户报告输出路径，并根据工具返回的 `backend` 准确说明音频由本地组件处理或已发送到远程接口，同时说明正文没有提供给当前对话模型。
   - 不得调用任何文件读取工具打开该转录稿，也不得声称自己看过正文。
4. 当 `polish=true`：
   - 使用工具返回的 `transcript` 在当前模型中润色。
   - 保留原文已有的时间戳、Speaker 标签、原意和事实；只修正标点、断句、口头语和高置信度识别错误，不总结、不翻译、不合并不同说话人。
   - 工具会先把原文作为可恢复的最终稿写入 `output_path`；调用 `save_transcript_result`，以 `polished=true` 安全替换为润色稿。
   - 如果无法可靠完成润色，直接展示工具返回的原始稿；原文最终稿已经存在，也可调用 `save_transcript_result`，以 `polished=false` 明确确认原文结果。
5. 最终告诉用户输出文件路径、转录后端、是否经过 AI 润色、识别模型，以及工具实际返回的说话人数和时长。

## 后端差异

- 本地模式使用 SenseVoice，默认执行说话人分离和时间戳对齐，并支持中文、英文、日文、韩文和粤语。
- OpenAI 兼容接口模式上传原始音频并采用响应 JSON 的非空 `text` 字段，不调用本地语音组件，不自动切换或回退到本地模型。
- 远程模式不提供本地说话人分离或时间戳；不要传入 `model`，不要设置 `diarize=true` 或正数 `speaker_count`。用户需要说话人标签时，明确建议切换到本地模式。

## 本地说话人分离规则

- 默认自动估算说话人数；用户知道人数时，将其作为 `speaker_count` 传入。
- 输出使用 `Speaker 1`、`Speaker 2` 等中性标签，不猜测真实姓名或身份。
- 同一时间检测到多人说话时保留组合标签，例如 `Speaker 1 + Speaker 2`。
- 无法可靠对齐到任何说话人的文字标记为 `Speaker ?`，不得凭上下文猜测。
- 说话人分离或时间戳对齐失败时明确报错，不退化成伪造的单说话人结果。

## 本地模式首次使用

本地语音模型和运行依赖统一由“设置 → 组件与依赖 → 语音转文字组件”管理。用户从 Cowork GitHub Release 获取独立的 Windows x64 语音组件 ZIP，再点击“选择安装包”离线部署。远程模式不需要该组件。

如果组件未安装或损坏，`transcribe_audio` 会明确返回 `component_not_ready`，提醒用户选择 Release 安装包进行安装或修复；不得在 Tool 调用期间或组件安装期间静默下载模型、运行 npm 或切换下载源。组件就绪后只使用本地文件。

## 边界

- 只处理用户明确指定的工作区文件或本会话附件。
- 当本会话已指定本 Skill 时，界面会把音频附件保留给该 Skill：当前对话模型只看到不含文件名和路径的占位说明，工具从本轮附件授权元数据中选择文件。
- 支持 WAV、MP3、M4A、AAC、FLAC、OGG、Opus、MP4 和 WebM；不支持实时麦克风听写。
- 本地模式下，长音频会切成短片段识别，再按绝对时间戳与整段说话人分离结果对齐；处理时长会随录音长度增加。远程模式把单个原始文件直接交给接口，大小与时长限制由该服务决定。
- 本地模式下，如果 FFmpeg 检测到损坏或不可解码的音频包，结果必须明确展示 `warnings`，并说明转录只覆盖可解码区间。
- 默认不覆盖已有输出文件；需要覆盖时必须得到用户明确同意并传入 `overwrite=true`。
- 不自动生成摘要、翻译、会议纪要或字幕文件，除非用户另行要求。
