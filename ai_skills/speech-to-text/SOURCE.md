# Upstream sources

This bundled Skill contains Cowork-specific orchestration and wrappers. It does not copy the upstream Skill text.

- ListenHub ASR Skill documentation: <https://listenhub.ai/docs/en/skills/asr>
- `@marswave/coli` / ListenHub 的本地优先工作流作为产品设计参考；运行实现直接使用 sherpa-onnx 的已校验模型组件
- `sherpa-onnx-node` 1.12.33: <https://github.com/k2-fsa/sherpa-onnx>, Apache-2.0 License
- Pyannote segmentation model converted for sherpa-onnx: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models>; check the upstream model terms published with the release
- 3D-Speaker embedding model converted for sherpa-onnx: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models>; check the upstream 3D-Speaker model terms
- `ffmpeg-static` 5.3.0: <https://github.com/eugeneware/ffmpeg-static>, GPL-3.0-or-later; the binary is installed into the per-Skill runtime by the Components & Dependencies installer and is not stored in this repository

SenseVoice 默认从 ModelScope 国内镜像下载，并使用 sherpa-onnx 官方 GitHub Release 公布的 SHA-256 校验；说话人分离模型来自 sherpa-onnx 官方 GitHub Release。运行时不会自动切换镜像。
