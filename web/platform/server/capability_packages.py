"""Read bounded capability drafts from portable ZIP files without extracting them."""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
import re
import stat
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
import zlib

from .security import PlatformError


MAX_ZIP_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1_024
MAX_BUNDLED_SKILLS = 8
_SPECIAL_PATH = re.compile(r'[\\<>:"|?*\x00-\x1f]')
_RESERVED_NAME = re.compile(r"(?i)(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?")
_PRIVATE_FILE = re.compile(r"(?i)(?:\.env(?:\..*)?|id_rsa|id_ed25519|credentials\.json|secrets\.(?:json|ya?ml))$")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_FRONTMATTER = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)(.*)", re.DOTALL)
_RESOURCE_DIRS = {"references", "scripts", "assets"}


def invalid_package(message, status=400):
    raise PlatformError("invalid_capability_package", message, status)


@dataclass
class CapabilityPackage:
    definition: dict
    bundled_skills: dict = field(default_factory=dict)


def _archive_files(data):
    if not data or len(data) > MAX_ZIP_BYTES:
        invalid_package("请选择不超过 4 MiB 的 ZIP 文件。", 413 if data else 400)
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
                invalid_package("ZIP 为空或包含过多文件。")
            if sum(entry.file_size for entry in entries) > MAX_ZIP_BYTES:
                invalid_package("ZIP 解压后超过 4 MiB，请减少文件后重试。", 413)
            seen, spellings, files = set(), {}, {}
            for entry in entries:
                if entry.is_dir() and entry.file_size:
                    invalid_package("ZIP 中的目录项包含文件内容。")
                name = entry.orig_filename[:-1] if entry.is_dir() else entry.orig_filename
                segments = name.split("/")
                if (len(name) > 256 or len(segments) > 12 or any(not segment or segment in {".", ".."}
                        or segment.startswith(".") or segment.endswith((".", " ")) or _SPECIAL_PATH.search(segment)
                        or _RESERVED_NAME.fullmatch(segment) for segment in segments)):
                    invalid_package("ZIP 含无效路径、隐藏文件或目录穿越，请检查后重新打包。")
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in {0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG} or entry.external_attr & 0x400:
                    invalid_package("ZIP 中不能包含链接或特殊文件。")
                if entry.flag_bits & 1 or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    invalid_package("请选择未加密、使用常规压缩方式的 ZIP。")
                canonical = name.casefold()
                if canonical in seen:
                    invalid_package("ZIP 中存在重复的文件或目录名。")
                seen.add(canonical)
                for index in range(1, len(segments) + 1):
                    prefix = "/".join(segments[:index])
                    if spellings.setdefault(prefix.casefold(), prefix) != prefix:
                        invalid_package("ZIP 中的目录或文件名存在大小写冲突。")
                if not entry.is_dir():
                    if _PRIVATE_FILE.fullmatch(segments[-1]) or name.lower().endswith((".pem", ".key", ".p12", ".pfx")):
                        invalid_package("企业能力包不能包含私钥或凭据文件。")
                    text = archive.read(entry).decode("utf-8-sig")
                    if "\x00" in text or _PRIVATE_KEY.search(text):
                        invalid_package("企业能力包只接受 UTF-8 文本，且不能包含私钥。")
                    files[name] = text
            paths = {name.casefold() for name in files}
            if any(any("/".join(parts[:index]) in paths for index in range(1, len(parts)))
                   for parts in (name.split("/") for name in seen)):
                invalid_package("ZIP 中文件和目录的路径冲突。")
            return files
    except (BadZipFile, OSError, NotImplementedError, RuntimeError, UnicodeError, zlib.error):
        invalid_package("无法读取 ZIP，请确认压缩包完整，且所有文件均为 UTF-8 文本。")


def _json(content, file_name):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    try:
        value = json.loads(content, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, RecursionError):
        invalid_package(f"{file_name} 应是字段不重复的 JSON 对象。")


def _unwrap(files, required):
    if required <= files.keys():
        return files
    roots = {name.partition("/")[0] for name in files}
    if len(roots) == 1:
        root = next(iter(roots))
        if all(f"{root}/{name}" in files for name in required):
            return {name.partition("/")[2]: value for name, value in files.items()}
    return files


def _skill_definition(files):
    if "SKILL.md" not in files:
        invalid_package("专家包中的每个 Skill 文件夹都需要包含 SKILL.md。")
    raw = files["SKILL.md"].replace("\r\n", "\n")
    header = _FRONTMATTER.match(raw)
    if not header:
        invalid_package("请在 SKILL.md 文件头填入 name 和 description，并添加技能指引。")
    metadata = {}
    block = None
    for line in header[1].splitlines():
        if line[:1].isspace() and block:
            metadata[block] = (metadata[block] + " " + line.strip()).strip()
            continue
        block = None
        key, _, value = line.partition(":")
        if key not in {"name", "display_name", "description"}:
            continue
        if key in metadata:
            invalid_package("SKILL.md 文件头的名称或说明出现重复字段。")
        value = value.strip()
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            block = key
            metadata[key] = ""
        else:
            metadata[key] = value.strip("\"'")
    name, instructions = metadata.get("display_name") or metadata.get("name"), header[2].strip()
    if not name or not instructions:
        invalid_package("SKILL.md 缺少 name 或技能指引。")
    assets = {}
    for path, value in files.items():
        if path == "SKILL.md":
            continue
        if path == "skill.json":
            config = _json(value, "skill.json")
            if any(config.get(key) for key in ("tools", "tool_refs", "authorization", "mcp_server_presets",
                   "python_dependencies", "node_dependencies", "config_fields", "config_requirements")):
                invalid_package("此桌面 Skill 依赖本机工具或额外配置。请制作仅包含指引和随包资源的企业 Skill。")
        elif path == "README.md" or path.startswith("LICENSE") and "/" not in path:
            target = "references/" + path
            if target.casefold() in {item.casefold() for item in files}:
                invalid_package("Skill 根目录文档与 references/ 内的文件重名。")
            assets[target] = value
        elif "/" in path and path.partition("/")[0] in _RESOURCE_DIRS:
            assets[path] = value
        else:
            invalid_package("Skill ZIP 仅支持 SKILL.md、兼容的 skill.json 和 references/、scripts/、assets/ 内的文本。")
    return {"kind": "skill", "name": name, "description": metadata.get("description", ""),
            "config": {"instructions": instructions, "files": assets}}


def read_capability_package(kind, data):
    if kind not in {"skill", "expert"}:
        invalid_package("只有 Skill 和专家支持 ZIP 上传。")
    files = _archive_files(data)
    if kind == "skill":
        files = _unwrap(files, {"SKILL.md"})
        if "SKILL.md" not in files:
            invalid_package("Skill ZIP 应包含一个 SKILL.md，可放在 ZIP 根目录或单一外层目录内。")
        return CapabilityPackage(_skill_definition(files))
    required = {"expert.json", "prompt.md"}
    files = _unwrap(files, required)
    if not required <= files.keys():
        invalid_package("专家 ZIP 根目录需要包含 expert.json 和 prompt.md。")
    expert = _json(files["expert.json"], "expert.json")
    if set(expert) - {"name", "description", "default_model_id", "skill_ids", "mcp_ids"}:
        invalid_package("expert.json 含不支持的字段，请根据下载模板填写。")
    instructions = files["prompt.md"].strip()
    if not instructions:
        invalid_package("prompt.md 需要包含专家提示词。")
    groups = {}
    for path, content in files.items():
        if path in {"expert.json", "prompt.md"}:
            continue
        parts = path.split("/", 2)
        if len(parts) != 3 or parts[0] != "skills":
            invalid_package("专家附带的 Skill 应放在 skills/ 下，每个 Skill 使用独立目录。")
        groups.setdefault(parts[1].casefold(), {})[parts[2]] = content
    if not 1 <= len(groups) <= MAX_BUNDLED_SKILLS:
        invalid_package("每个专家 ZIP 须附带至少一个 Skill，最多 8 个。")
    bundled = {name: _skill_definition(assets) for name, assets in sorted(groups.items())}
    config = {key: expert[key] for key in ("default_model_id", "skill_ids", "mcp_ids") if key in expert}
    definition = {"kind": "expert", "name": expert.get("name"), "description": expert.get("description", ""),
                  "config": {**config, "instructions": instructions}}
    return CapabilityPackage(definition, bundled)


def capability_zip_template(kind):
    skill = "---\nname: 周报整理\ndescription: 根据工作记录整理周报\n---\n# 周报整理\n\n阅读用户提供的工作记录，列出进展、风险和下一步。\n"
    if kind == "skill":
        files = {"SKILL.md": skill, "references/周报结构.md": "# 进展\n\n# 风险\n\n# 下一步\n"}
    elif kind == "expert":
        files = {"expert.json": json.dumps({"name": "周报助手", "description": "协助整合团队周报"}, ensure_ascii=False, indent=2),
                 "prompt.md": "你是周报助手。结合随附 Skill 整理用户提供的记录，缺失信息请标明。\n",
                 "skills/weekly-summary/SKILL.md": skill}
    else:
        invalid_package("只有 Skill 和专家提供 ZIP 模板。", 404)
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8"))
    return output.getvalue()
