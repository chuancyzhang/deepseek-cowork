# Upstream sources

This bundled Skill contains Cowork-specific orchestration and wrappers. It does not copy the upstream Skill text.

- ListenHub ASR Skill documentation: <https://listenhub.ai/docs/en/skills/asr>
- `@marswave/coli` 0.0.20: <https://github.com/marswaveai/coli>, MIT License
- `sherpa-onnx-node` 1.12.33: <https://github.com/k2-fsa/sherpa-onnx>, Apache-2.0 License
- Pyannote segmentation model converted for sherpa-onnx: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models>; check the upstream model terms published with the release
- 3D-Speaker embedding model converted for sherpa-onnx: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models>; check the upstream 3D-Speaker model terms
- `ffmpeg-static` 5.3.0: <https://github.com/eugeneware/ffmpeg-static>, GPL-3.0-or-later; the binary is installed into the per-Skill runtime on first use and is not stored in this repository

