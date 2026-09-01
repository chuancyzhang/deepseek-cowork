import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
import zipfile
from urllib.parse import urlparse

import requests

from core.env_utils import get_app_data_dir
from core.process_utils import subprocess_kwargs_no_window


PYTHON_SOURCES = {
    "pypi": {"name": "PyPI", "url": "https://pypi.org/simple"},
    "tsinghua": {"name": "清华镜像", "url": "https://pypi.tuna.tsinghua.edu.cn/simple"},
    "aliyun": {"name": "阿里云镜像", "url": "https://mirrors.aliyun.com/pypi/simple/"},
}
NODE_SOURCES = {
    "nodejs": {"name": "Node.js 官方", "url": "https://nodejs.org/dist/"},
    "npmmirror": {"name": "npmmirror", "url": "https://npmmirror.com/mirrors/node/"},
}

NODE_VERSION = "v24.14.1"
NODE_ARCHIVE = "node-v24.14.1-win-x64.zip"
NODE_SHA256 = "6E50CE5498C0CEBC20FD39AB3FF5DF836ED2F8A31AA093CECAD8497CFF126D70"
TOOLKIT_MARKER_SCHEMA = 2
SPEECH_TO_TEXT_COMPONENT_ID = "speech-to-text"
SPEECH_TO_TEXT_COMPONENT_SCHEMA = 1
SPEECH_TO_TEXT_PACKAGE_SCHEMA = 1
SPEECH_TO_TEXT_PACKAGE_PLATFORM = "win32-x64"
SPEECH_TO_TEXT_PACKAGE_FILENAME = "deepseek-cowork-speech-to-text-v1-win-x64.zip"
SPEECH_TO_TEXT_PACKAGE_MANIFEST = "speech-to-text-package.json"
SPEECH_TO_TEXT_PACKAGE_MAX_FILES = 50_000
SPEECH_TO_TEXT_PACKAGE_MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
SPEECH_TO_TEXT_SKILL_ID = "speech-to-text"
SPEECH_TO_TEXT_NODE_DEPENDENCIES = [
    "ffmpeg-static@5.3.0",
    "sherpa-onnx-node@1.12.33",
]
SPEECH_TO_TEXT_NPM_REGISTRY = "https://registry.npmmirror.com"

SPEECH_TO_TEXT_ASSETS = {
    "sensevoice_model": {
        "filename": "model.int8.onnx",
        "size": 239_233_841,
        "sha256": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        "official_url": (
            "https://huggingface.co/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/"
            "6a65851692da9706cbddfac66ea9b96ebb1dee21/model.int8.onnx"
        ),
        "mirror_url": (
            "https://hf-mirror.com/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/"
            "6a65851692da9706cbddfac66ea9b96ebb1dee21/model.int8.onnx"
        ),
    },
    "sensevoice_tokens": {
        "filename": "tokens.txt",
        "size": 315_894,
        "sha256": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        "official_url": (
            "https://huggingface.co/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/"
            "6a65851692da9706cbddfac66ea9b96ebb1dee21/tokens.txt"
        ),
        "mirror_url": (
            "https://hf-mirror.com/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/"
            "6a65851692da9706cbddfac66ea9b96ebb1dee21/tokens.txt"
        ),
    },
    "segmentation": {
        "filename": "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
        "size": 6_958_444,
        "sha256": "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
        "official_url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        ),
    },
    "embedding": {
        "filename": "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
        "size": 39_593_761,
        "sha256": "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b",
        "official_url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-recongition-models/"
            "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
        ),
    },
}

SPEECH_TO_TEXT_MODEL_SOURCES = {
    "hf-mirror": {
        "id": "hf-mirror",
        "name": "HF-Mirror 国内加速 + sherpa-onnx 官方源",
    },
    "official": {
        "id": "official",
        "name": "Hugging Face + sherpa-onnx 官方源",
    },
}

TOOLKITS = {
    "documents": {
        "name": "文档工具包",
        "description": "读取与生成 XLSX、DOCX、PPTX 和 PDF",
        "packages": ["openpyxl", "python-docx", "python-pptx", "Pillow", "pypdf", "reportlab"],
        "imports": ["openpyxl", "docx", "pptx", "PIL.Image", "pypdf", "reportlab"],
        "skills": ["document-reader"],
        "bundled": True,
    },
    "data-analysis": {
        "name": "数据分析工具包",
        "description": "数据处理、科学计算、可视化与机器学习",
        "packages": ["numpy", "pandas", "scipy", "matplotlib", "seaborn", "scikit-learn"],
        "imports": ["numpy", "pandas", "scipy", "matplotlib", "seaborn", "sklearn"],
        "skills": [],
    },
    "finance": {
        "name": "金融分析工具包",
        "description": "金融数据查询与研究辅助",
        "packages": ["pandas", "akshare"],
        "imports": ["pandas", "akshare"],
        "skills": ["financial-data-akshare"],
    },
    "web-research": {
        "name": "网页研究工具包",
        "description": "网页搜索、解析与结构化采集",
        "packages": ["tavily-python==0.7.26"],
        "imports": ["tavily"],
        "skills": ["web-search"],
    },
}


def speech_to_text_component_root():
    return os.path.join(
        get_app_data_dir(),
        "runtime_sandbox",
        "v1",
        "components",
        SPEECH_TO_TEXT_COMPONENT_ID,
    )


def speech_to_text_component_paths(root=None):
    base = os.path.abspath(root or speech_to_text_component_root())
    return {
        "root": base,
        "marker": os.path.join(base, "component.json"),
        "sensevoice_model": os.path.join(base, "models", "sensevoice", "model.int8.onnx"),
        "sensevoice_tokens": os.path.join(base, "models", "sensevoice", "tokens.txt"),
        "segmentation": os.path.join(base, "models", "diarization", "segmentation.onnx"),
        "embedding": os.path.join(base, "models", "diarization", "embedding.onnx"),
    }


def speech_to_text_skill_runtime_root():
    return os.path.join(
        get_app_data_dir(),
        "runtime_sandbox",
        "v1",
        "skills",
        SPEECH_TO_TEXT_SKILL_ID,
    )


def _speech_to_text_definition_hash():
    payload = {
        "schema": SPEECH_TO_TEXT_COMPONENT_SCHEMA,
        "node_dependencies": SPEECH_TO_TEXT_NODE_DEPENDENCIES,
        "npm_registry": SPEECH_TO_TEXT_NPM_REGISTRY,
        "assets": {
            name: {
                "filename": item["filename"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
            for name, item in SPEECH_TO_TEXT_ASSETS.items()
        },
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _read_speech_to_text_marker(root=None):
    marker_path = speech_to_text_component_paths(root)["marker"]
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _speech_source(source=None):
    source_id = str((source or {}).get("id") or "hf-mirror").strip().lower()
    if source_id not in SPEECH_TO_TEXT_MODEL_SOURCES:
        raise ValueError(f"未知语音模型下载源：{source_id}")
    return dict(SPEECH_TO_TEXT_MODEL_SOURCES[source_id])


def _speech_asset_url(asset_name, source_id):
    spec = SPEECH_TO_TEXT_ASSETS[asset_name]
    if asset_name in {"sensevoice_model", "sensevoice_tokens"} and source_id == "hf-mirror":
        return spec["mirror_url"]
    return spec["official_url"]


def _probe_speech_asset_urls(source_id, progress_callback=None):
    for index, asset_name in enumerate(SPEECH_TO_TEXT_ASSETS):
        spec = SPEECH_TO_TEXT_ASSETS[asset_name]
        url = _speech_asset_url(asset_name, source_id)
        if progress_callback:
            progress_callback(f"正在检查 {spec['filename']} 下载地址…", 1 + index)
        with requests.get(
            url,
            stream=True,
            timeout=(15, 30),
            allow_redirects=True,
            headers={
                "User-Agent": "deepseek-cowork-components",
                "Range": "bytes=0-0",
            },
        ) as response:
            response.raise_for_status()
            if response.status_code not in {200, 206}:
                raise RuntimeError(
                    f"{spec['filename']} 下载地址检查失败：HTTP {response.status_code}"
                )


def _download_verified_speech_asset(asset_name, target, source_id, progress_callback=None, progress_range=(0, 100)):
    spec = SPEECH_TO_TEXT_ASSETS[asset_name]
    url = _speech_asset_url(asset_name, source_id)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    digest = hashlib.sha256()
    downloaded = 0
    expected_size = int(spec["size"])
    start_progress, end_progress = progress_range
    with requests.get(
        url,
        stream=True,
        timeout=(15, 120),
        headers={"User-Agent": "deepseek-cowork-components"},
    ) as response:
        response.raise_for_status()
        with open(target, "wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress_callback and expected_size:
                    ratio = min(1.0, downloaded / expected_size)
                    progress_callback(
                        f"正在下载 {spec['filename']}…",
                        int(start_progress + (end_progress - start_progress) * ratio),
                    )
    if downloaded != expected_size:
        raise RuntimeError(
            f"{spec['filename']} 大小校验失败：期望 {expected_size}，实际 {downloaded}"
        )
    actual_hash = digest.hexdigest().lower()
    if actual_hash != spec["sha256"]:
        raise RuntimeError(
            f"{spec['filename']} SHA-256 校验失败："
            f"期望 {spec['sha256']}，实际 {actual_hash}"
        )
    return {"url": url, "size": downloaded, "sha256": actual_hash}


def _extract_named_tar_member(archive_path, suffix, target):
    normalized_suffix = str(suffix).replace("\\", "/")
    with tarfile.open(archive_path, mode="r:bz2") as archive:
        candidates = [
            member
            for member in archive.getmembers()
            if member.isfile()
            and member.name.replace("\\", "/").endswith(normalized_suffix)
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                f"模型归档 {os.path.basename(archive_path)} 中未找到唯一的 {normalized_suffix}。"
            )
        source = archive.extractfile(candidates[0])
        if source is None:
            raise RuntimeError(f"无法读取模型归档成员：{normalized_suffix}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def _speech_component_file_records(paths):
    root = paths["root"]
    records = {}
    for key in ("sensevoice_model", "sensevoice_tokens", "segmentation", "embedding"):
        path = paths[key]
        if not os.path.isfile(path):
            raise RuntimeError(f"语音组件缺少文件：{path}")
        records[key] = {
            "path": os.path.relpath(path, root).replace("\\", "/"),
            "size": os.path.getsize(path),
            "sha256": _sha256_file(path),
        }
    return records


def speech_to_text_component_status(include_size=False):
    from core.sandbox_runtime import read_skill_dependency_status, skill_dependency_hash

    paths = speech_to_text_component_paths()
    root_exists = os.path.isdir(paths["root"])
    marker = _read_speech_to_text_marker()
    installed = bool(root_exists and marker)
    definition_hash = _speech_to_text_definition_hash()
    needs_update = bool(installed and marker.get("definition_hash") != definition_hash)
    errors = []
    if root_exists and not marker:
        errors.append("缺少语音组件健康标记。")
    if installed and int(marker.get("schema") or 0) != SPEECH_TO_TEXT_COMPONENT_SCHEMA:
        errors.append("语音组件由旧版本安装，需要更新。")
    if needs_update:
        errors.append("语音组件定义已变化，需要更新。")

    file_records = marker.get("files") if isinstance(marker.get("files"), dict) else {}
    if installed and not needs_update:
        for key in ("sensevoice_model", "sensevoice_tokens", "segmentation", "embedding"):
            expected = file_records.get(key) if isinstance(file_records.get(key), dict) else {}
            path = paths[key]
            if not expected or not os.path.isfile(path):
                errors.append(f"缺少已验证模型文件：{key}")
                continue
            if os.path.getsize(path) != int(expected.get("size") or -1):
                errors.append(f"模型文件大小异常：{key}")
                continue
            if include_size and _sha256_file(path) != str(expected.get("sha256") or "").lower():
                errors.append(f"模型文件 SHA-256 异常：{key}")

    dependency_status = read_skill_dependency_status(SPEECH_TO_TEXT_SKILL_ID)
    dependency_hash = skill_dependency_hash([], SPEECH_TO_TEXT_NODE_DEPENDENCIES)
    dependencies_ready = bool(
        dependency_status.get("ok")
        and dependency_status.get("hash") == dependency_hash
    )
    if installed and not dependencies_ready:
        errors.append("语音转文字 Node 依赖未就绪。")
    node_status = node_runtime_status()
    node_ready = bool(
        node_status.get("installed")
        and str(node_status.get("version") or "") == NODE_VERSION
    )
    if installed and not node_ready:
        errors.append(f"语音组件需要 Node.js {NODE_VERSION} Windows x64 运行时。")

    skill_runtime_root = speech_to_text_skill_runtime_root()
    for package_name in ("ffmpeg-static", "sherpa-onnx-node"):
        package_manifest = os.path.join(
            skill_runtime_root,
            "node",
            "node_modules",
            package_name,
            "package.json",
        )
        if installed and not os.path.isfile(package_manifest):
            errors.append(f"语音组件缺少离线 Node 依赖：{package_name}。")

    healthy = bool(installed and not needs_update and dependencies_ready and node_ready and not errors)
    return {
        "id": SPEECH_TO_TEXT_COMPONENT_ID,
        "name": "语音转文字组件",
        "description": "SenseVoice 本地识别、FFmpeg 音频解码与说话人分离",
        "skills": [SPEECH_TO_TEXT_SKILL_ID],
        "installed": installed,
        "healthy": healthy,
        "ready": healthy,
        "needs_update": needs_update,
        "needs_repair": bool(root_exists and not healthy and not needs_update),
        "health_error": "\n".join(errors),
        "source": str(marker.get("source_name") or ""),
        "source_id": str(marker.get("source_id") or ""),
        "package_name": str(marker.get("package_name") or ""),
        "node_version": str(node_status.get("version") or ""),
        "size": (
            _directory_size(paths["root"])
            + _directory_size(
                os.path.join(
                    get_app_data_dir(),
                    "runtime_sandbox",
                    "v1",
                    "skills",
                    SPEECH_TO_TEXT_SKILL_ID,
                )
            )
            if include_size
            else 0
        ),
        "model_paths": {
            key: paths[key]
            for key in ("sensevoice_model", "sensevoice_tokens", "segmentation", "embedding")
        } if healthy else {},
    }


def _normalize_speech_package_path(value):
    raw = str(value or "").replace("\\", "/")
    normalized = raw.strip("/")
    if (
        not normalized
        or raw.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", raw)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise RuntimeError(f"语音组件安装包包含不安全路径：{value}")
    return normalized


def _verify_speech_package_archive(package_path, extract_root, progress_callback=None):
    package_path = os.path.abspath(str(package_path or ""))
    if not os.path.isfile(package_path):
        raise RuntimeError(f"语音组件安装包不存在：{package_path or '未提供路径'}")
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("语音转文字组件安装包仅支持 Windows x64。")
    if progress_callback:
        progress_callback("正在验证语音组件安装包…", 5)
    try:
        archive = zipfile.ZipFile(package_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"语音组件安装包不是有效 ZIP：{exc}") from exc
    with archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        if len(infos) > SPEECH_TO_TEXT_PACKAGE_MAX_FILES:
            raise RuntimeError("语音组件安装包文件数量超出限制。")
        if sum(max(0, int(item.file_size)) for item in infos) > SPEECH_TO_TEXT_PACKAGE_MAX_UNPACKED_BYTES:
            raise RuntimeError("语音组件安装包解压后体积超出限制。")
        archive_files = {}
        for info in infos:
            path = _normalize_speech_package_path(info.filename)
            if ((int(info.external_attr) >> 16) & 0o170000) == 0o120000:
                raise RuntimeError(f"语音组件安装包不允许符号链接：{path}")
            if path in archive_files:
                raise RuntimeError(f"语音组件安装包包含重复路径：{path}")
            archive_files[path] = info
        manifest_info = archive_files.get(SPEECH_TO_TEXT_PACKAGE_MANIFEST)
        if manifest_info is None or manifest_info.file_size > 5 * 1024 * 1024:
            raise RuntimeError("语音组件安装包缺少有效 manifest。")
        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError(f"语音组件安装包 manifest 无法解析：{exc}") from exc
        if not isinstance(manifest, dict):
            raise RuntimeError("语音组件安装包 manifest 必须是 JSON 对象。")
        expected_identity = {
            "schema": SPEECH_TO_TEXT_PACKAGE_SCHEMA,
            "component_id": SPEECH_TO_TEXT_COMPONENT_ID,
            "platform": SPEECH_TO_TEXT_PACKAGE_PLATFORM,
            "definition_hash": _speech_to_text_definition_hash(),
            "node_version": NODE_VERSION,
        }
        mismatches = [key for key, value in expected_identity.items() if manifest.get(key) != value]
        if mismatches:
            raise RuntimeError("语音组件安装包不兼容：" + "、".join(mismatches))
        if list(manifest.get("node_dependencies") or []) != SPEECH_TO_TEXT_NODE_DEPENDENCIES:
            raise RuntimeError("语音组件安装包 Node 依赖版本不匹配。")
        records = manifest.get("files")
        if not isinstance(records, list) or not records:
            raise RuntimeError("语音组件安装包 manifest 缺少文件清单。")
        expected_files = {}
        for record in records:
            if not isinstance(record, dict):
                raise RuntimeError("语音组件安装包文件清单格式无效。")
            path = _normalize_speech_package_path(record.get("path"))
            try:
                size = int(record["size"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"语音组件安装包文件大小无效：{path}") from exc
            sha256 = str(record.get("sha256") or "").strip().lower()
            if path in expected_files or size < 0 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise RuntimeError(f"语音组件安装包文件记录无效：{path}")
            expected_files[path] = {"size": size, "sha256": sha256}
        actual_payload = set(archive_files) - {SPEECH_TO_TEXT_PACKAGE_MANIFEST}
        if actual_payload != set(expected_files):
            missing = sorted(set(expected_files) - actual_payload)
            extra = sorted(actual_payload - set(expected_files))
            raise RuntimeError(f"语音组件安装包文件清单不一致：缺少 {missing}；多余 {extra}")
        required_paths = {
            f"assets/{item['filename']}" for item in SPEECH_TO_TEXT_ASSETS.values()
        } | {
            f"node-runtime/{NODE_ARCHIVE}",
            "skill-runtime/node/package.json",
            "skill-runtime/node/node_modules/ffmpeg-static/package.json",
            "skill-runtime/node/node_modules/sherpa-onnx-node/package.json",
        }
        if not required_paths.issubset(expected_files):
            raise RuntimeError("语音组件安装包缺少模型、Node.js 或离线依赖。")
        for asset_name, spec in SPEECH_TO_TEXT_ASSETS.items():
            record = expected_files[f"assets/{spec['filename']}"]
            if record != {"size": int(spec["size"]), "sha256": str(spec["sha256"]).lower()}:
                raise RuntimeError(f"语音模型固定校验信息不匹配：{asset_name}")
        node_record = expected_files[f"node-runtime/{NODE_ARCHIVE}"]
        if node_record["sha256"].upper() != NODE_SHA256:
            raise RuntimeError("语音组件安装包内 Node.js SHA-256 不匹配。")
        for index, (path, record) in enumerate(sorted(expected_files.items()), start=1):
            info = archive_files[path]
            if int(info.file_size) != record["size"]:
                raise RuntimeError(f"语音组件安装包文件大小不匹配：{path}")
            digest = hashlib.sha256()
            with archive.open(info, "r") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest().lower() != record["sha256"]:
                raise RuntimeError(f"语音组件安装包文件 SHA-256 不匹配：{path}")
            if progress_callback and index % max(1, len(expected_files) // 20) == 0:
                progress_callback("正在校验语音组件文件…", 5 + int(index * 35 / len(expected_files)))
        expected_dependency_versions = {
            item.rsplit("@", 1)[0]: item.rsplit("@", 1)[1]
            for item in SPEECH_TO_TEXT_NODE_DEPENDENCIES
        }
        try:
            runtime_package = json.loads(
                archive.read("skill-runtime/node/package.json").decode("utf-8")
            )
            installed_versions = {
                name: str(version)
                for name, version in dict(runtime_package.get("dependencies") or {}).items()
            }
            if installed_versions != expected_dependency_versions:
                raise RuntimeError("语音组件安装包 package.json 依赖版本不匹配。")
            for dependency_name, expected_version in expected_dependency_versions.items():
                package_path = f"skill-runtime/node/node_modules/{dependency_name}/package.json"
                dependency_package = json.loads(archive.read(package_path).decode("utf-8"))
                if str(dependency_package.get("version") or "") != expected_version:
                    raise RuntimeError(f"语音组件安装包依赖版本不匹配：{dependency_name}")
        except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(f"语音组件安装包依赖信息无效：{exc}") from exc
        for path, info in archive_files.items():
            target = os.path.abspath(os.path.join(extract_root, *path.split("/")))
            if os.path.commonpath([os.path.abspath(extract_root), target]) != os.path.abspath(extract_root):
                raise RuntimeError(f"语音组件安装包包含不安全路径：{path}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info, "r") as source_handle, open(target, "wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
    return manifest


def _replace_speech_roots_transactionally(pairs, health_check):
    committed = []
    try:
        for staged, target in pairs:
            backup = target + ".speech-previous"
            if os.path.isdir(backup):
                shutil.rmtree(backup)
            had_existing = os.path.isdir(target)
            if had_existing:
                os.replace(target, backup)
            try:
                os.replace(staged, target)
            except Exception:
                if had_existing and os.path.isdir(backup) and not os.path.exists(target):
                    os.replace(backup, target)
                raise
            committed.append((target, backup, had_existing))
        result = health_check()
    except Exception:
        for target, backup, had_existing in reversed(committed):
            if os.path.isdir(target):
                shutil.rmtree(target)
            if had_existing and os.path.isdir(backup):
                os.replace(backup, target)
        raise
    for _target, backup, _had_existing in committed:
        if os.path.isdir(backup):
            shutil.rmtree(backup)
    return result


def install_speech_to_text_component(source=None, progress_callback=None, force=False):
    del force
    from core.sandbox_runtime import reset_native_library_dir_caches, reset_runtime_cache, skill_dependency_hash

    package_path = (
        str(source.get("package_path") or "")
        if isinstance(source, dict)
        else str(source or "")
    )
    sandbox_root = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1")
    os.makedirs(sandbox_root, exist_ok=True)
    transaction_root = tempfile.mkdtemp(prefix=".speech-package-", dir=sandbox_root)
    try:
        unpacked = os.path.join(transaction_root, "unpacked")
        os.makedirs(unpacked)
        manifest = _verify_speech_package_archive(package_path, unpacked, progress_callback)
        if progress_callback:
            progress_callback("正在准备本地语音模型和运行环境…", 45)
        staged_component = os.path.join(transaction_root, "component.next")
        staged_skill = os.path.join(transaction_root, "skill.next")
        staged_node = os.path.join(transaction_root, "node.next")
        staged_paths = speech_to_text_component_paths(staged_component)
        os.makedirs(os.path.dirname(staged_paths["sensevoice_model"]), exist_ok=True)
        os.makedirs(os.path.dirname(staged_paths["segmentation"]), exist_ok=True)
        asset_records = {}
        for asset_name in ("sensevoice_model", "sensevoice_tokens", "embedding"):
            spec = SPEECH_TO_TEXT_ASSETS[asset_name]
            source_path = os.path.join(unpacked, "assets", spec["filename"])
            target_path = staged_paths[asset_name]
            shutil.copy2(source_path, target_path)
            asset_records[asset_name] = {
                "filename": spec["filename"],
                "size": int(spec["size"]),
                "sha256": str(spec["sha256"]).lower(),
            }
        segmentation_spec = SPEECH_TO_TEXT_ASSETS["segmentation"]
        segmentation_archive = os.path.join(unpacked, "assets", segmentation_spec["filename"])
        _extract_named_tar_member(segmentation_archive, "/model.onnx", staged_paths["segmentation"])
        asset_records["segmentation"] = {
            "filename": segmentation_spec["filename"],
            "size": int(segmentation_spec["size"]),
            "sha256": str(segmentation_spec["sha256"]).lower(),
        }
        shutil.copytree(os.path.join(unpacked, "skill-runtime"), staged_skill)
        dependency_status = {
            "ok": True,
            "hash": skill_dependency_hash([], SPEECH_TO_TEXT_NODE_DEPENDENCIES),
            "message": "Installed from verified local release package.",
            "installed": True,
        }
        with open(os.path.join(staged_skill, "dependency_status.json"), "w", encoding="utf-8") as handle:
            json.dump(dependency_status, handle, ensure_ascii=False, indent=2)
        node_extract = os.path.join(transaction_root, "node-extract")
        os.makedirs(node_extract)
        with zipfile.ZipFile(os.path.join(unpacked, "node-runtime", NODE_ARCHIVE), "r") as archive:
            _safe_extract(archive, node_extract)
        node_dirs = [item.path for item in os.scandir(node_extract) if item.is_dir()]
        if len(node_dirs) != 1 or not os.path.isfile(os.path.join(node_dirs[0], "node.exe")):
            raise RuntimeError("语音组件安装包内 Node.js 结构无效。")
        shutil.move(node_dirs[0], staged_node)
        with open(os.path.join(staged_node, ".cowork_runtime_source"), "w", encoding="utf-8") as handle:
            handle.write(f"{NODE_VERSION}|local-release-package")
        marker = {
            "schema": SPEECH_TO_TEXT_COMPONENT_SCHEMA,
            "id": SPEECH_TO_TEXT_COMPONENT_ID,
            "definition_hash": _speech_to_text_definition_hash(),
            "verified": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "source_id": "local-release-package",
            "source_name": "本地 Release 安装包",
            "package_name": os.path.basename(package_path),
            "package_schema": int(manifest.get("schema") or 0),
            "assets": asset_records,
            "files": _speech_component_file_records(staged_paths),
        }
        with open(staged_paths["marker"], "w", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback("正在部署已验证的语音组件…", 85)
        runtime_node_target = os.path.join(sandbox_root, "runtimes", "node")
        component_target = speech_to_text_component_root()
        skill_target = speech_to_text_skill_runtime_root()
        for target in (runtime_node_target, component_target, skill_target):
            os.makedirs(os.path.dirname(target), exist_ok=True)

        def health_check():
            reset_runtime_cache()
            reset_native_library_dir_caches(SPEECH_TO_TEXT_SKILL_ID)
            status = speech_to_text_component_status(include_size=True)
            if not status.get("healthy"):
                raise RuntimeError(status.get("health_error") or "语音组件健康检查失败。")
            node_exe = str(node_runtime_status().get("path") or "")
            module_root = os.path.join(skill_target, "node", "node_modules")
            probe = subprocess.run(
                [
                    node_exe,
                    "-e",
                    "require('ffmpeg-static'); require('sherpa-onnx-node');",
                ],
                cwd=os.path.join(skill_target, "node"),
                env={**os.environ, "NODE_PATH": module_root},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **subprocess_kwargs_no_window(),
            )
            if probe.returncode != 0:
                raise RuntimeError(
                    "语音组件离线 Node 依赖验收失败："
                    + (probe.stderr or probe.stdout or "未知错误").strip()
                )
            return status

        try:
            status = _replace_speech_roots_transactionally(
                [
                    (staged_node, runtime_node_target),
                    (staged_component, component_target),
                    (staged_skill, skill_target),
                ],
                health_check,
            )
        except Exception:
            reset_runtime_cache()
            reset_native_library_dir_caches(SPEECH_TO_TEXT_SKILL_ID)
            raise
        if progress_callback:
            progress_callback("语音转文字组件已从本地安装包部署并验证完成。", 100)
        return status
    finally:
        shutil.rmtree(transaction_root, ignore_errors=True)


def uninstall_speech_to_text_component():
    from core.sandbox_runtime import reset_native_library_dir_caches

    component_root = speech_to_text_component_root()
    skill_root = speech_to_text_skill_runtime_root()
    for target in (component_root, skill_root):
        if os.path.isdir(target):
            shutil.rmtree(target)
    reset_native_library_dir_caches(SPEECH_TO_TEXT_SKILL_ID)
    return speech_to_text_component_status(include_size=True)


def default_download_sources():
    return {
        "python": {"selected": "tsinghua", "custom": []},
        "node": {"selected": "nodejs", "custom": []},
    }


def load_saved_download_sources():
    path = os.path.join(get_app_data_dir(), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return normalize_download_sources(payload.get("download_sources"))
    except (OSError, ValueError, TypeError):
        return default_download_sources()


def selected_python_index_url():
    return selected_source("python", load_saved_download_sources())["url"]


def normalize_download_sources(value):
    source = value if isinstance(value, dict) else {}
    result = default_download_sources()
    for kind, presets in (("python", PYTHON_SOURCES), ("node", NODE_SOURCES)):
        cfg = source.get(kind) if isinstance(source.get(kind), dict) else {}
        custom = []
        seen = set()
        for item in cfg.get("custom") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if not name or not valid_https_source(url):
                continue
            if not item_id:
                item_id = "custom-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
            if item_id in seen or item_id in presets:
                continue
            seen.add(item_id)
            custom.append({"id": item_id, "name": name, "url": url.rstrip("/") + "/"})
        result[kind]["custom"] = custom
        available = set(presets) | {item["id"] for item in custom}
        selected = str(cfg.get("selected") or result[kind]["selected"])
        result[kind]["selected"] = selected if selected in available else result[kind]["selected"]
    return result


def valid_https_source(url):
    try:
        parsed = urlparse(str(url or "").strip())
        return parsed.scheme.lower() == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
    except Exception:
        return False


def source_options(kind, settings):
    normalized = normalize_download_sources(settings)
    presets = PYTHON_SOURCES if kind == "python" else NODE_SOURCES
    options = [{"id": key, **value, "custom": False} for key, value in presets.items()]
    options.extend({**item, "custom": True} for item in normalized[kind]["custom"])
    return options


def selected_source(kind, settings):
    normalized = normalize_download_sources(settings)
    selected = normalized[kind]["selected"]
    for item in source_options(kind, normalized):
        if item["id"] == selected:
            return item
    raise RuntimeError(f"未找到已选择的{kind}下载源：{selected}")


def test_source(kind, source, timeout=12):
    url = str((source or {}).get("url") or "").strip()
    if not valid_https_source(url):
        raise ValueError("下载源必须是有效的 HTTPS 地址，且不能在 URL 中包含凭据。")
    target = url
    if kind == "node":
        target = f"{url.rstrip('/')}/{NODE_VERSION}/{NODE_ARCHIVE}"
    response = requests.get(target, stream=True, timeout=timeout, headers={"User-Agent": "deepseek-cowork-components"})
    try:
        response.raise_for_status()
    finally:
        response.close()
    return True


def toolkits_root():
    path = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1", "toolkits")
    os.makedirs(path, exist_ok=True)
    return path


def toolkit_path(toolkit_id):
    return os.path.join(toolkits_root(), toolkit_id, "site-packages")


def _toolkit_definition_hash(toolkit_id):
    spec = TOOLKITS[toolkit_id]
    payload = {
        "packages": spec["packages"],
        "imports": spec["imports"],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _read_toolkit_marker(toolkit_id):
    marker_path = os.path.join(os.path.dirname(toolkit_path(toolkit_id)), "toolkit.json")
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
        return marker if isinstance(marker, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _toolkit_marker_health(toolkit_id, marker):
    from core.sandbox_runtime import get_runtime_executable

    if not marker:
        return False, "缺少工具包健康标记。"
    if marker.get("schema") != TOOLKIT_MARKER_SCHEMA:
        return False, "工具包由旧版本安装，需要更新后重新验收。"
    if marker.get("definition_hash") != _toolkit_definition_hash(toolkit_id):
        return False, "工具包依赖定义已变化，需要更新。"
    current_python = os.path.normcase(os.path.abspath(get_runtime_executable("python") or ""))
    marker_python = os.path.normcase(os.path.abspath(marker.get("python_executable") or ""))
    if not current_python or marker_python != current_python:
        return False, "沙箱 Python 运行时已变化，需要重新安装工具包。"
    if not marker.get("verified"):
        return False, marker.get("verification_error") or "工具包尚未通过完整性验证。"
    return True, ""


def installed_toolkit_paths():
    paths = []
    for toolkit_id in TOOLKITS:
        if TOOLKITS[toolkit_id].get("bundled"):
            continue
        path = toolkit_path(toolkit_id)
        marker = _read_toolkit_marker(toolkit_id)
        healthy, _error = _toolkit_marker_health(toolkit_id, marker)
        if os.path.isdir(path) and healthy:
            paths.append(path)
    return paths


def _directory_size(path):
    total = 0
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    return total


def toolkit_status(toolkit_id, include_size=False):
    spec = TOOLKITS[toolkit_id]
    if spec.get("bundled"):
        from core.sandbox_runtime import get_runtime_executable

        python_exe = get_runtime_executable("python")
        health_error = ""
        healthy = False
        if not python_exe:
            health_error = "随应用安装的沙箱 Python 不可用，请重新安装完整的 Cowork 分发包。"
        else:
            runtime_site_packages = os.path.join(
                os.path.dirname(os.path.abspath(python_exe)),
                "Lib",
                "site-packages",
            )
            try:
                _verify_toolkit_candidate(python_exe, toolkit_id, runtime_site_packages)
                healthy = True
            except Exception as exc:
                health_error = (
                    "随应用安装的文档工具包不完整，请重新安装完整的 Cowork 分发包。"
                    f"\n{exc}"
                )
        return {
            "id": toolkit_id,
            **spec,
            "installed": True,
            "healthy": healthy,
            "needs_update": False,
            "needs_repair": not healthy,
            "health_error": health_error,
            "missing_packages": [],
            "source": "随应用安装",
            "size": 0,
        }
    root = os.path.dirname(toolkit_path(toolkit_id))
    marker = _read_toolkit_marker(toolkit_id)
    installed_packages = {
        str(package).strip().lower()
        for package in (marker.get("packages") or [])
        if str(package).strip()
    }
    required_packages = {package.lower() for package in spec["packages"]}
    missing_packages = sorted(required_packages - installed_packages)
    installed = bool(marker and os.path.isdir(toolkit_path(toolkit_id)))
    healthy, health_error = _toolkit_marker_health(toolkit_id, marker) if installed else (False, "")
    needs_update = installed and (
        marker.get("schema") != TOOLKIT_MARKER_SCHEMA
        or marker.get("definition_hash") != _toolkit_definition_hash(toolkit_id)
        or bool(missing_packages)
    )
    needs_repair = installed and not needs_update and not healthy
    return {
        "id": toolkit_id,
        **spec,
        "installed": installed,
        "healthy": healthy,
        "needs_update": needs_update,
        "needs_repair": needs_repair,
        "health_error": health_error,
        "missing_packages": missing_packages if installed else [],
        "source": marker.get("source") or "",
        "size": _directory_size(root) if include_size else 0,
    }


def _verify_toolkit_candidate(python_exe, toolkit_id, candidate_path):
    from core.sandbox_runtime import build_sandbox_env

    imports = TOOLKITS[toolkit_id]["imports"]
    code = (
        "import importlib,json,traceback\n"
        f"checks={json.dumps(imports, ensure_ascii=False)}\n"
        "result={'ok': True, 'checked': [], 'error': ''}\n"
        "try:\n"
        "    for name in checks:\n"
        "        importlib.import_module(name)\n"
        "        result['checked'].append(name)\n"
        "except Exception:\n"
        "    result['ok'] = False\n"
        "    result['error'] = traceback.format_exc()\n"
        "print(json.dumps(result, ensure_ascii=False))\n"
        "raise SystemExit(0 if result['ok'] else 1)\n"
    )
    env = build_sandbox_env()
    bootstrap_entries = [
        item
        for item in (env.get("PYTHONPATH") or "").split(os.pathsep)
        if item and os.path.basename(item) == "python_bootstrap"
    ]
    env["PYTHONPATH"] = os.pathsep.join(bootstrap_entries + [candidate_path])
    completed = subprocess.run(
        [python_exe, "-X", "utf8", "-c", code],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_kwargs_no_window(),
    )
    output = (completed.stdout or "").strip()
    payload = {}
    if output:
        try:
            payload = json.loads(output.splitlines()[-1])
        except (ValueError, TypeError):
            payload = {}
    if completed.returncode != 0 or not payload.get("ok"):
        detail = payload.get("error") or completed.stderr or output or "未知导入错误"
        raise RuntimeError(f"{TOOLKITS[toolkit_id]['name']}完整性验证失败：\n{detail.strip()}")
    return payload.get("checked") or []


def _repair_python_runner_import_conflicts(python_exe, toolkit_id, candidate_path):
    from core.sandbox_runtime import build_sandbox_env

    skill_path = os.path.join(
        get_app_data_dir(),
        "runtime_sandbox",
        "v1",
        "skills",
        "python-runner",
        "python",
        "site-packages",
    )
    if not os.path.isdir(skill_path):
        return []
    base_env = build_sandbox_env(skill_id="python-runner")
    bootstrap_entries = [
        item
        for item in (base_env.get("PYTHONPATH") or "").split(os.pathsep)
        if item and os.path.basename(item) == "python_bootstrap"
    ]
    repaired = []
    for import_name in TOOLKITS[toolkit_id]["imports"]:
        env = dict(base_env)
        env["PYTHONPATH"] = os.pathsep.join(bootstrap_entries + [skill_path, candidate_path])
        probe = subprocess.run(
            [python_exe, "-X", "utf8", "-c", f"import importlib; importlib.import_module({import_name!r})"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_kwargs_no_window(),
        )
        if probe.returncode == 0:
            continue
        top_level = import_name.split(".", 1)[0]
        candidates = [
            os.path.join(skill_path, top_level),
            os.path.join(skill_path, top_level + ".py"),
        ]
        conflict_paths = [path for path in candidates if os.path.exists(path)]
        if not conflict_paths:
            continue
        for path in conflict_paths:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        retry = subprocess.run(
            [python_exe, "-X", "utf8", "-c", f"import importlib; importlib.import_module({import_name!r})"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_kwargs_no_window(),
        )
        if retry.returncode != 0:
            raise RuntimeError(
                f"清理 python-runner 中冲突的 {top_level} 后仍无法导入 {import_name}：\n"
                f"{(retry.stderr or retry.stdout or '未知导入错误').strip()}"
            )
        repaired.append(top_level)
    return sorted(set(repaired))


def _replace_toolkit_root(staged_root, target_root):
    backup_root = target_root + ".previous"
    if os.path.isdir(backup_root):
        shutil.rmtree(backup_root)
    had_existing = os.path.isdir(target_root)
    if had_existing:
        os.replace(target_root, backup_root)
    try:
        os.replace(staged_root, target_root)
    except Exception:
        if had_existing and os.path.isdir(backup_root) and not os.path.exists(target_root):
            os.replace(backup_root, target_root)
        raise
    if os.path.isdir(backup_root):
        shutil.rmtree(backup_root)


def install_toolkit(toolkit_id, python_source, progress_callback=None, force=False):
    if toolkit_id not in TOOLKITS:
        raise KeyError(f"未知工具包：{toolkit_id}")
    if TOOLKITS[toolkit_id].get("bundled"):
        raise RuntimeError(f"{TOOLKITS[toolkit_id]['name']}随应用安装，不能单独安装或覆盖。")
    from core.sandbox_runtime import build_sandbox_env, get_runtime_executable

    python_exe = get_runtime_executable("python")
    if not python_exe:
        raise RuntimeError("沙箱 Python 不可用。")
    source_url = str((python_source or {}).get("url") or "")
    if not valid_https_source(source_url):
        raise ValueError("Python 下载源必须是有效的 HTTPS 地址。")
    spec = TOOLKITS[toolkit_id]
    target_root = os.path.dirname(toolkit_path(toolkit_id))
    staged_root = tempfile.mkdtemp(prefix=f".{toolkit_id}-", dir=toolkits_root())
    staged_target = os.path.join(staged_root, "site-packages")
    os.makedirs(staged_target)
    try:
        if progress_callback:
            progress_callback(f"正在从 {python_source.get('name') or source_url} 下载并安装 {spec['name']}…", 20)
        command = [python_exe, "-m", "pip", "install", "--index-url", source_url, "--upgrade", "--target", staged_target]
        if force:
            command.append("--force-reinstall")
        command.extend(spec["packages"])
        completed = subprocess.run(
            command,
            env=build_sandbox_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_kwargs_no_window(),
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "pip 安装失败").strip())
        if progress_callback:
            progress_callback(f"正在验证 {spec['name']} 的全部依赖…", 75)
        checked_imports = _verify_toolkit_candidate(python_exe, toolkit_id, staged_target)
        repaired_conflicts = _repair_python_runner_import_conflicts(
            python_exe,
            toolkit_id,
            staged_target,
        )
        marker = {
            "schema": TOOLKIT_MARKER_SCHEMA,
            "id": toolkit_id,
            "packages": spec["packages"],
            "imports": checked_imports,
            "definition_hash": _toolkit_definition_hash(toolkit_id),
            "python_executable": os.path.abspath(python_exe),
            "verified": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "repaired_python_runner_conflicts": repaired_conflicts,
            "source": python_source.get("name") or source_url,
            "source_url": source_url,
        }
        with open(os.path.join(staged_root, "toolkit.json"), "w", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback(f"正在启用 {spec['name']}…", 92)
        _replace_toolkit_root(staged_root, target_root)
        staged_root = ""
        if progress_callback:
            progress_callback(f"{spec['name']}安装并验证完成。", 100)
    finally:
        if staged_root and os.path.isdir(staged_root):
            shutil.rmtree(staged_root, ignore_errors=True)
    return toolkit_status(toolkit_id, include_size=True)


def uninstall_toolkit(toolkit_id):
    if toolkit_id not in TOOLKITS:
        raise KeyError(f"未知工具包：{toolkit_id}")
    if TOOLKITS[toolkit_id].get("bundled"):
        raise RuntimeError(f"{TOOLKITS[toolkit_id]['name']}随应用安装，不能单独卸载。")
    root = os.path.dirname(toolkit_path(toolkit_id))
    if os.path.isdir(root):
        shutil.rmtree(root)
    return toolkit_status(toolkit_id, include_size=True)


def node_runtime_status():
    from core.sandbox_runtime import get_runtime_executable
    path = get_runtime_executable("node")
    version = ""
    if path:
        try:
            completed = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                **subprocess_kwargs_no_window(),
            )
            if completed.returncode == 0:
                version = str(completed.stdout or completed.stderr or "").strip()
        except (OSError, subprocess.SubprocessError):
            version = ""
    return {"installed": bool(path and version), "path": path, "version": version}


def _safe_extract(archive, target):
    target_abs = os.path.abspath(target)
    for member in archive.infolist():
        candidate = os.path.abspath(os.path.join(target_abs, member.filename))
        if os.path.commonpath([target_abs, candidate]) != target_abs:
            raise RuntimeError("Node.js 压缩包包含不安全路径。")
    archive.extractall(target)


def install_node_runtime(node_source, progress_callback=None):
    from core.sandbox_runtime import reset_runtime_cache

    source_url = str((node_source or {}).get("url") or "")
    if not valid_https_source(source_url):
        raise ValueError("Node.js 下载源必须是有效的 HTTPS 地址。")
    url = f"{source_url.rstrip('/')}/{NODE_VERSION}/{NODE_ARCHIVE}"
    runtime_root = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1", "runtimes")
    target = os.path.join(runtime_root, "node")
    os.makedirs(runtime_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-node-", dir=runtime_root) as temp_dir:
        archive_path = os.path.join(temp_dir, NODE_ARCHIVE)
        digest = hashlib.sha256()
        if progress_callback:
            progress_callback(f"正在从 {node_source.get('name') or source_url} 下载 Node.js…", 1)
        with requests.get(url, stream=True, timeout=30, headers={"User-Agent": "deepseek-cowork-components"}) as response:
            response.raise_for_status()
            expected = int(response.headers.get("content-length") or 0)
            downloaded = 0
            with open(archive_path, "wb") as handle:
                for chunk in response.iter_content(512 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback and expected:
                        progress_callback("正在下载 Node.js…", min(85, int(downloaded * 85 / expected)))
        actual = digest.hexdigest().upper()
        if actual != NODE_SHA256:
            raise RuntimeError(f"Node.js SHA-256 校验失败：期望 {NODE_SHA256}，实际 {actual}")
        extract_dir = os.path.join(temp_dir, "extract")
        os.makedirs(extract_dir)
        with zipfile.ZipFile(archive_path, "r") as archive:
            _safe_extract(archive, extract_dir)
        dirs = [item.path for item in os.scandir(extract_dir) if item.is_dir()]
        if len(dirs) != 1 or not os.path.isfile(os.path.join(dirs[0], "node.exe")):
            raise RuntimeError("Node.js 压缩包结构无效。")
        staged = os.path.join(runtime_root, "node.next")
        if os.path.isdir(staged):
            shutil.rmtree(staged)
        shutil.move(dirs[0], staged)
        with open(os.path.join(staged, ".cowork_runtime_source"), "w", encoding="utf-8") as handle:
            handle.write(f"{NODE_VERSION}|{source_url}")
        if os.path.isdir(target):
            shutil.rmtree(target)
        os.replace(staged, target)
    reset_runtime_cache()
    if progress_callback:
        progress_callback("Node.js 安装完成。", 100)
    return node_runtime_status()


def uninstall_node_runtime():
    from core.sandbox_runtime import reset_runtime_cache
    target = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1", "runtimes", "node")
    if os.path.isdir(target):
        shutil.rmtree(target)
    reset_runtime_cache()
    return node_runtime_status()
