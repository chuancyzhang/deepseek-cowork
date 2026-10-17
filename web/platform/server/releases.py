"""Immutable, validated ZIP releases; no uploaded executable is ever launched."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import re
import stat
import time
import zipfile

from sqlalchemy import select, update

from .connection_contract import CONNECTION_FILE, validate_connection
from .database import ClientRelease, Operation, Platform, uid
from .security import PlatformError, digest, encode

log = logging.getLogger(__name__)
ROOT = "deepseek-cowork"
REQUIRED_FILES = {"deepseek-cowork.exe", "_internal/python_env/python.exe",
                  "_internal/PySide6/Qt6Core.dll", "_internal/web/editors/dist/terminal.html"}
FORBIDDEN_ROOTS = {"user_data", "chat_history", "conversation_workspaces", ".git", ".env", "updates"}
FORBIDDEN_FILES = {"config.json", "connections.sqlite3", "connection_templates.local.json", "variables.sqlite3",
                   "enterprise.sqlite3", "enterprise.sqlite3-wal", "enterprise.sqlite3-shm",
                   "enterprise_session.json", "enterprise_sessions.sqlite3", "enterprise_binding.json"}
DEVICE_NAMES = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(part)
    return result.hexdigest()


def inspect_archive(archive, settings, *, enterprise=False):
    files, total, seen = {}, 0, set()
    infos = archive.infolist()
    if not infos or len(infos) > 50000:
        raise PlatformError("invalid_archive", "安装包为空或包含过多文件。")
    for info in infos:
        name = info.filename
        parts = name.rstrip("/").split("/")
        kind = stat.S_IFMT(info.external_attr >> 16)
        if ("\\" in name or name.startswith("/") or len(parts) < 1 or parts[0] != ROOT
                or any(not p or p in {".", ".."} or ":" in p or p.endswith((".", " "))
                       or DEVICE_NAMES.fullmatch(p) or any(ord(c) < 32 for c in p) for p in parts)
                or kind not in {0, stat.S_IFDIR, stat.S_IFREG} or info.flag_bits & 1):
            raise PlatformError("unsafe_archive", "安装包包含越界路径、链接或不受支持的文件类型。")
        relative = "/".join(parts[1:])
        canonical = name.rstrip("/").casefold()
        if canonical in seen:
            raise PlatformError("unsafe_archive", "安装包包含重复或大小写冲突的路径。")
        seen.add(canonical)
        if any(p.casefold() in FORBIDDEN_ROOTS for p in parts[1:]) or PurePosixPath(relative).name.casefold() in FORBIDDEN_FILES:
            raise PlatformError("private_archive", "标准包包含用户数据或本机配置，不能分发。")
        if relative == CONNECTION_FILE and not enterprise:
            raise PlatformError("already_enterprise", "请上传不含企业连接文件的标准客户端包。")
        total += info.file_size
        if total > settings.max_unpacked_bytes or (info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 1000):
            raise PlatformError("archive_too_large", "安装包展开体积或压缩比超过限制。")
        if not info.is_dir():
            files[relative] = info
    if not REQUIRED_FILES <= set(files):
        raise PlatformError("missing_runtime", "安装包缺少 EXE 或必要运行资源。")
    capability = files.get("_internal/enterprise_capabilities.json")
    if not capability or capability.file_size > 8192:
        raise PlatformError("incompatible_archive", "标准包尚未包含企业接入能力声明，请使用新版构建。")
    try:
        data = json.loads(archive.read(capability))
        if data != {"supported_connection_schemas": [1], "supported_platform_api_versions": [1]}:
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        raise PlatformError("incompatible_archive", "安装包中的企业能力声明无效。") from None
    if enterprise and CONNECTION_FILE not in files:
        raise PlatformError("missing_connection", "企业包缺少连接配置。")
    return files


def public_release(item, *, admin=False):
    value = {"id": item.id, "state": item.state, "app_version": item.app_version, "target_os": item.target_os,
             "arch": item.arch, "notes": item.notes, "recommended": item.recommended, "sha256": item.output_sha256,
             "bytes": item.output_bytes, "published_at": item.published_at, "revision": item.revision}
    if admin:
        value.update(stage=item.stage, error=item.error, created_at=item.created_at,
                     source_sha256=item.source_sha256, source_bytes=item.source_bytes, connection=item.snapshot)
    return value


class Releases:
    def __init__(self, settings, database):
        self.settings, self.database = settings, database
        self.root = settings.data_dir / "releases"
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, release_id):
        if not re.fullmatch(r"[a-f0-9]{32}", release_id):
            raise PlatformError("not_found", "发布记录不存在。", 404)
        directory = self.root / release_id
        directory.mkdir(exist_ok=True)
        return directory

    def create(self, payload, actor):
        version = payload.get("app_version", "")
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version) or payload.get("arch", "x64") != "x64":
            raise PlatformError("invalid_release", "请填写三段版本号；当前客户端发行包支持 Windows x64。")
        notes = str(payload.get("notes", ""))
        if len(notes) > 20000:
            raise PlatformError("invalid_release", "更新说明过长。")
        with self.database.transaction() as db:
            item = ClientRelease(app_version=version, arch=payload.get("arch", "x64"), notes=notes, created_by=actor)
            db.add(item)
            db.flush()
            self.database.audit(db, "release.created", member_id=actor, target=item.id)
            result = public_release(item, admin=True)
        return result

    async def upload(self, release_id, stream, report, actor):
        required = {"app_version", "zip_sha256", "zip_bytes", "supported_connection_schemas", "supported_platform_api_versions"}
        if not isinstance(report, dict) or not required <= set(report):
            raise PlatformError("incompatible_report", "检查报告缺少版本、摘要、大小或兼容性字段。")
        for key in ("supported_connection_schemas", "supported_platform_api_versions"):
            if not isinstance(report[key], list) or not report[key] or any(type(v) is not int for v in report[key]) or 1 not in report[key]:
                raise PlatformError("incompatible_report", "需要支持企业连接 v1 与中心 API v1 的标准包。")
        directory = self.directory(release_id)
        generation = uid()
        with self.database.transaction() as db:
            item = db.scalar(select(ClientRelease).where(ClientRelease.id == release_id).with_for_update())
            if not item:
                raise PlatformError("not_found", "发布记录不存在。", 404)
            if item.state not in {"draft", "failed"} or (item.stage == "receive" and item.state != "failed"):
                raise PlatformError("release_busy", "当前记录不可上传，请等待处理完成或新建记录。", 409)
            if report["app_version"] != item.app_version:
                raise PlatformError("version_mismatch", "报告版本与发布记录不一致。")
            item.stage, item.generation, item.error = "receive", generation, ""
            item.revision += 1
        temporary = directory / (generation + ".upload")
        count, hasher = 0, hashlib.sha256()
        try:
            with temporary.open("xb") as handle:
                async for block in stream:
                    count += len(block)
                    if count > self.settings.max_archive_bytes:
                        raise PlatformError("archive_too_large", "标准 ZIP 超过上传上限。", 413)
                    hasher.update(block)
                    handle.write(block)
            if count != report["zip_bytes"] or hasher.hexdigest() != str(report["zip_sha256"]).lower():
                raise PlatformError("checksum_mismatch", "实际文件大小或 SHA-256 与报告不符。")
            source = directory / (hasher.hexdigest() + ".source.zip")
            temporary.replace(source)
            with self.database.transaction() as db:
                item = db.get(ClientRelease, release_id)
                if item.generation != generation or item.stage != "receive":
                    raise PlatformError("release_changed", "上传对应的发布状态已变化。", 409)
                item.source_sha256, item.source_bytes, item.report = hasher.hexdigest(), count, report
                item.stage, item.state = "received", "draft"
                self.database.audit(db, "release.uploaded", member_id=actor, target=release_id)
            return {"state": "draft", "stage": "received"}
        except BaseException:
            with self.database.transaction() as db:
                item = db.get(ClientRelease, release_id)
                if item and item.generation == generation:
                    item.stage, item.state, item.error = "receive", "failed", "上传中断或文件检查失败，请重新上传。"
                    self.database.audit(db, "release.upload_failed", member_id=actor, target=release_id, outcome="receive_failed")
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def prepare(self, release_id, actor):
        with self.database.transaction() as db:
            item = db.scalar(select(ClientRelease).where(ClientRelease.id == release_id).with_for_update())
            if not item or item.state not in {"draft", "failed"} or not item.source_sha256 or item.stage == "receive":
                raise PlatformError("release_not_ready", "请先完整上传标准包和报告。", 409)
            source_path = self.directory(release_id) / (item.source_sha256 + ".source.zip")
            if not source_path.is_file():
                raise PlatformError("source_missing", "完整源文件不存在，请重新上传。", 409)
            platform = db.get(Platform, 1)
            item.snapshot = validate_connection({"schema_version": 1, "config_version": platform.config_version,
                                                 "platform_id": platform.platform_id, "platform_name": platform.name,
                                                 "platform_url": platform.public_url, "distribution_channel": "enterprise"},
                                                development=self.settings.development)
            item.state, item.stage, item.error = "processing", "validate", ""
            item.revision += 1
            generation = uid()
            # A distinct operation generation invalidates cancelled worker callbacks.
            item.generation = generation
            self.database.audit(db, "release.prepare", member_id=actor, target=release_id)
        return generation

    def stage(self, release_id, generation, name):
        with self.database.transaction() as db:
            changed = db.execute(update(ClientRelease).where(ClientRelease.id == release_id,
                ClientRelease.generation == generation, ClientRelease.state == "processing").values(stage=name)).rowcount
            if not changed:
                raise PlatformError("cancelled", "处理已取消。", 409)

    def process(self, release_id, generation):
        directory = self.directory(release_id)
        candidate = directory / (generation + ".candidate.zip")
        try:
            with self.database.transaction() as db:
                item = db.get(ClientRelease, release_id)
                snapshot, expected_sha, expected_bytes = item.snapshot, item.source_sha256, item.source_bytes
            source_path = directory / (expected_sha + ".source.zip")
            if source_path.stat().st_size != expected_bytes or sha256(source_path) != expected_sha:
                raise PlatformError("checksum_mismatch", "完整源文件摘要复核失败，请重新上传。")
            source_hashes = {}
            with zipfile.ZipFile(source_path) as source:
                files = inspect_archive(source, self.settings)
                self.stage(release_id, generation, "package")
                with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
                    for index, (name, info) in enumerate(files.items()):
                        if index % 100 == 0:
                            self.stage(release_id, generation, "package")
                        hasher = hashlib.sha256()
                        with source.open(info) as reader, output.open(info, "w", force_zip64=True) as writer:
                            for block in iter(lambda: reader.read(1024 * 1024), b""):
                                hasher.update(block)
                                writer.write(block)
                        source_hashes[name] = hasher.hexdigest()
                    output.writestr(ROOT + "/" + CONNECTION_FILE, encode(snapshot).encode("utf-8"))
            self.stage(release_id, generation, "recheck")
            with zipfile.ZipFile(candidate) as output:
                files = inspect_archive(output, self.settings, enterprise=True)
                if set(files) != set(source_hashes) | {CONNECTION_FILE}:
                    raise PlatformError("output_changed", "封装结果的文件集合不一致。")
                for name, expected in source_hashes.items():
                    hasher = hashlib.sha256()
                    with output.open(files[name]) as reader:
                        for block in iter(lambda: reader.read(1024 * 1024), b""):
                            hasher.update(block)
                    if hasher.hexdigest() != expected:
                        raise PlatformError("output_changed", "封装改变了标准文件内容。")
            result_hash, result_bytes = sha256(candidate), candidate.stat().st_size
            self.stage(release_id, generation, "ready")
            final = directory / (generation + ".enterprise.zip")
            candidate.replace(final)
            with self.database.transaction() as db:
                changed = db.execute(update(ClientRelease).where(ClientRelease.id == release_id,
                    ClientRelease.generation == generation, ClientRelease.state == "processing")
                    .values(state="ready", stage="ready", output_sha256=result_hash, output_bytes=result_bytes)).rowcount
                if not changed:
                    final.unlink(missing_ok=True)
                    return
                self.database.audit(db, "release.ready", target=release_id)
        except Exception as exc:
            reason = exc.message if isinstance(exc, PlatformError) else "校验或封装失败，请检查源文件与服务器存储。"
            with self.database.transaction() as db:
                db.execute(update(ClientRelease).where(ClientRelease.id == release_id,
                    ClientRelease.generation == generation, ClientRelease.state == "processing")
                    .values(state="failed", error=reason))
                self.database.audit(db, "release.prepare_failed", target=release_id, outcome=type(exc).__name__[:80])
            log.warning("release.failed release=%s reason=%s", release_id, type(exc).__name__)
        finally:
            candidate.unlink(missing_ok=True)

    def cancel(self, release_id, actor):
        with self.database.transaction() as db:
            changed = db.execute(update(ClientRelease).where(ClientRelease.id == release_id,
                ClientRelease.state == "processing").values(state="draft", stage="cancelled", error="上次处理已取消。")).rowcount
            if not changed:
                raise PlatformError("not_processing", "此发布记录当前未在处理。", 409)
            self.database.audit(db, "release.cancelled", member_id=actor, target=release_id)
        return {"state": "draft"}

    def mutate(self, release_id, action, payload, actor):
        operation_id = payload.get("operation_id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", operation_id):
            raise PlatformError("invalid_operation", "缺少稳定操作标识。")
        request_hash = digest(encode([release_id, action, payload]))
        with self.database.transaction() as db:
            platform = db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
            old = db.get(Operation, operation_id)
            if old:
                if old.member_id != actor or old.request_hash != request_hash:
                    raise PlatformError("operation_conflict", "同一操作标识不能用于不同请求。", 409)
                return old.result
            if payload.get("catalog_revision") != platform.catalog_revision:
                raise PlatformError("catalog_conflict", "发布目录已变化，请刷新后核对。", 409)
            item = db.get(ClientRelease, release_id)
            if not item:
                raise PlatformError("not_found", "发布记录不存在。", 404)
            target = (ClientRelease.target_os == item.target_os, ClientRelease.arch == item.arch)
            if action == "publish":
                if item.state != "ready" or not self.download_path(item).is_file():
                    raise PlatformError("not_publishable", "最终包尚未就绪。", 409)
                item.state, item.published_at = "published", time.time()
                if payload.get("recommended"):
                    db.execute(update(ClientRelease).where(*target).values(recommended=False))
                    item.recommended = True
            elif action == "recommend":
                if item.state != "published":
                    raise PlatformError("not_published", "只能推荐已发布版本。", 409)
                db.execute(update(ClientRelease).where(*target).values(recommended=False))
                item.recommended = True
            elif action == "withdraw":
                if item.state != "published":
                    raise PlatformError("not_published", "只能撤下已发布版本。", 409)
                reason = str(payload.get("reason", "")).strip()
                if not reason or len(reason) > 1000:
                    raise PlatformError("reason_required", "请填写撤下原因。")
                if item.recommended:
                    if "replacement_id" not in payload:
                        raise PlatformError("replacement_required", "请明确选择替代版本或清空推荐。")
                    replacement_id = payload["replacement_id"]
                    if replacement_id:
                        replacement = db.get(ClientRelease, replacement_id)
                        if (not replacement or replacement.id == item.id or replacement.state != "published"
                                or (replacement.target_os, replacement.arch) != (item.target_os, item.arch)):
                            raise PlatformError("invalid_replacement", "替代版本必须是同系统架构的另一已发布版本。")
                        replacement.recommended = True
                item.state, item.recommended, item.error = "withdrawn", False, reason
            else:
                raise PlatformError("invalid_operation", "发布操作无效。")
            platform.catalog_revision += 1
            item.revision += 1
            db.flush()
            result = {"release": public_release(item, admin=True), "catalog_revision": platform.catalog_revision}
            db.add(Operation(id=operation_id, member_id=actor, request_hash=request_hash, result=result))
            self.database.audit(db, "release." + action, member_id=actor, target=release_id)
            return result

    def download_path(self, item):
        return self.directory(item.id) / (item.generation + ".enterprise.zip")
