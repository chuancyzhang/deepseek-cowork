# Upstream sources

This bundled Skill contains Cowork-specific orchestration and wrappers. It does not copy the upstream Skill text.

- ListenHub ASR Skill documentation: <https://listenhub.ai/docs/en/skills/asr>
- `@marswave/coli` / ListenHub 的本地优先工作流作为产品设计参考；运行实现直接使用 sherpa-onnx 的已校验模型组件
- `sherpa-onnx-node` 1.12.33: <https://github.com/k2-fsa/sherpa-onnx>, Apache-2.0 License
- SenseVoice ONNX model repository: <https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17>
- Pyannote segmentation model converted for sherpa-onnx: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models>; check the upstream model terms published with the release
- 3D-Speaker embedding model converted for sherpa-onnx: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models>; check the upstream 3D-Speaker model terms
- `ffmpeg-static` 5.3.0: <https://github.com/eugeneware/ffmpeg-static>, GPL-3.0-or-later; the binary is installed into the per-Skill runtime by the Components & Dependencies installer and is not stored in this repository

SenseVoice 默认通过 HF-Mirror 国内加速地址下载，并固定到模型作者 Hugging Face 仓库的 commit `6a65851692da9706cbddfac66ea9b96ebb1dee21`；ONNX 和 tokens 分别执行固定大小与 SHA-256 校验。说话人分离模型来自 sherpa-onnx 官方 GitHub Release。安装前会检查全部模型 URL，运行时不会自动切换镜像。
