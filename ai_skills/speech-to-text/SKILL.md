---
name: speech-to-text
description: Transcribe local audio or video files into speaker-separated text, with an explicit choice between fully local output and optional polishing by the current AI model.
license: Apache-2.0
metadata: {"author": "deepseek-cowork team", "version": "1.0"}
allowed-tools: [transcribe_audio, save_transcript_result]
---

# 语音转文字

使用本地小模型转录音频或视频文件，并默认执行说话人分离。支持中文、英文、日文、韩文和粤语；英文音频也可选择更轻量的 Whisper tiny.en。

## 必须先确认隐私模式

在调用转录工具前，必须先询问用户是否需要 AI 润色，并清楚说明两种模式：

- **不润色（完全本地）**：音频内容和转录正文只在本机处理并写入工作区；工具结果不会把正文返回给当前模型。当前模型只能看到路径和运行状态等元数据，不得再读取生成的转录文件。
- **AI 润色**：本地转录完成后，原始文字会交给当前会话正在运行的模型处理；如果当前模型是云端服务，转录文字会发送给该服务商。润色结果随后写入工作区。

如果用户已经明确选择，不要重复询问。

## 工作流

1. 确认音频文件、是否 AI 润色，以及用户是否明确指定说话人数。说话人数未知时使用自动估算。
2. 调用 `transcribe_audio`，`polish` 必须与用户选择一致；`diarize` 默认保持 `true`。
3. 当 `polish=false`：
   - 工具已将带说话人标签的原始稿写入工作区。
   - 只向用户报告输出路径，并说明音频内容和转录正文均由本地模型处理、没有提供给当前大模型。
   - 不得调用任何文件读取工具打开该转录稿，也不得声称自己看过正文。
4. 当 `polish=true`：
   - 使用工具返回的 `transcript` 在当前模型中润色。
   - 保留时间戳、Speaker 标签、原意和事实；只修正标点、断句、口头语和高置信度识别错误，不总结、不翻译、不合并不同说话人。
   - 调用 `save_transcript_result`，以 `polished=true` 写入最终稿。
   - 如果无法可靠完成润色，直接展示工具返回的原始稿，并调用 `save_transcript_result`，以 `polished=false` 将本地原始稿提升为最终稿。
5. 最终告诉用户输出文件路径、是否经过 AI 润色、识别模型、说话人数和时长。

## 说话人分离规则

- 默认自动估算说话人数；用户知道人数时，将其作为 `speaker_count` 传入。
- 输出使用 `Speaker 1`、`Speaker 2` 等中性标签，不猜测真实姓名或身份。
- 同一时间检测到多人说话时保留组合标签，例如 `Speaker 1 + Speaker 2`。
- 无法可靠对齐到任何说话人的文字标记为 `Speaker ?`，不得凭上下文猜测。
- 说话人分离或时间戳对齐失败时明确报错，不退化成伪造的单说话人结果。

## 首次使用

首次调用由应用现有依赖协调器安装本 Skill 的 Node.js 运行包，过程日志会显示安装状态；安装成功后按依赖哈希复用，不重复安装。首次使用某个本地模型时还会下载对应模型文件，后续调用复用本机缓存。

## 边界

- 只处理用户明确指定的工作区文件或本会话附件。
- 支持 WAV、MP3、M4A、AAC、FLAC、OGG、Opus、MP4 和 WebM；不支持实时麦克风听写。
- 默认不覆盖已有输出文件；需要覆盖时必须得到用户明确同意并传入 `overwrite=true`。
- 不自动生成摘要、翻译、会议纪要或字幕文件，除非用户另行要求。
