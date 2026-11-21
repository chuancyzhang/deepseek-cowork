"""Prepare an Electron update as files ready to move into the installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
import zipfile

from core import updater as backend
from core.enterprise_connection import CONNECTION_FILE, MAX_CONNECTION_BYTES, EnterpriseConfigurationError, check_update_connection, parse_connection
from core.update_manifest import DESKTOP_FILES_NAME

MAX_INVENTORY_BYTES = 16 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


def _read_inventory(data):
    value = json.loads(data)
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise backend.UpdaterError("安装包文件清单格式无效。")
    for path, entry in files.items():
        if (backend._normalized_relative_path(path) != path or backend._is_update_excluded(path)
                or not isinstance(entry, dict) or type(entry.get("size")) is not int or entry["size"] < 0
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or set(entry) != {"size", "sha256"}):
            raise backend.UpdaterError("安装包文件清单包含无效条目。")
    if len({path.casefold() for path in files}) != len(files):
        raise backend.UpdaterError("安装包文件清单包含重复路径。")
    return files


def _copy_digest(source, cancel, destination=None, advance=None):
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
        backend.check_cancel(cancel)
        if destination is not None:
            destination.write(chunk)
        digest.update(chunk)
        if advance is not None:
            advance(len(chunk))
    return digest.hexdigest()


def _read_entry(archive, member, limit):
    if member.file_size > limit:
        raise backend.UpdaterError("安装包的版本或配置文件超过大小限制。")
    return archive.read(member)


def _inventory(archive, files, cancel):
    manifest = files.get(DESKTOP_FILES_NAME)
    if manifest is None:
        # Previously published archives have no index. Read their entries once
        # into the same plan representation, without materializing the tree.
        result = {}
        for path, member in files.items():
            backend.check_cancel(cancel)
            with archive.open(member) as source:
                result[path] = {"size": member.file_size, "sha256": _copy_digest(source, cancel)}
        return result
    raw = _read_entry(archive, manifest, MAX_INVENTORY_BYTES)
    result = _read_inventory(raw)
    expected = set(files) - {DESKTOP_FILES_NAME, CONNECTION_FILE}
    if set(result) != expected or any(entry["size"] != files[path].file_size for path, entry in result.items()):
        raise backend.UpdaterError("安装包中的文件与清单不一致。")
    result[DESKTOP_FILES_NAME] = {"size": manifest.file_size, "sha256": hashlib.sha256(raw).hexdigest()}
    return result


def _installed_inventory(install_dir):
    path = backend.platform_runtime.filesystem_path(Path(install_dir) / DESKTOP_FILES_NAME)
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_INVENTORY_BYTES + 1)
    except FileNotFoundError:
        return None, {}
    if len(raw) > MAX_INVENTORY_BYTES:
        raise backend.UpdaterError("当前安装的文件清单超过大小限制。")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        inventory = _read_inventory(raw)
    except (ValueError, backend.UpdaterError):
        # Repair can reconstruct a damaged installed index from actual files.
        return digest, {}
    inventory[DESKTOP_FILES_NAME] = {"size": len(raw), "sha256": digest}
    return digest, inventory


def _compare(install_dir, files, installed, repair, cancel):
    current = {path.casefold(): info for path, info in backend._collect_app_files(install_dir, cancel).items()}
    baseline = {path.casefold(): info for path, info in installed.items()}
    added, modified, deleted = [], [], []
    for path, info in files.items():
        backend.check_cancel(cancel)
        key = path.casefold()
        old = current.get(key)
        if old is None:
            added.append({"path": path, **info, "before_sha256": None})
            continue
        if not repair and old["size"] == info["size"] and baseline.get(key) == info:
            continue
        digest = backend._hash_file(old["full_path"], cancel)
        if digest != info["sha256"]:
            modified.append({"path": path, **info, "before_sha256": digest})
    # Files outside the installed release inventory are user-owned. Installations
    # created before the inventory use the existing app directory as the baseline.
    removed = (baseline.keys() if baseline else current.keys()) - {path.casefold() for path in files}
    for key in sorted(removed):
        backend.check_cancel(cancel)
        if key in current:
            old = current[key]
            deleted.append({"path": old["path"], "before_sha256": backend._hash_file(old["full_path"], cancel)})
    return {"format_version": backend.UPDATE_PLAN_FORMAT, "added": added, "modified": modified, "deleted": deleted,
            "summary": {"added": len(added), "modified": len(modified), "deleted": len(deleted),
                        "unchanged": len(files) - len(added) - len(modified),
                        "write_bytes": sum(item["size"] for item in added + modified)}}


def stage_dir(install_dir, identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise backend.UpdaterError("更新暂存编号无效。")
    root = Path(install_dir) / backend.DESKTOP_STAGE_ROOT
    stage = root / identity
    for path in (root, stage, stage / "files", stage / "backup"):
        native = backend.platform_runtime.filesystem_path(path)
        if native.is_symlink() or native.is_junction():
            raise backend.UpdaterError("更新暂存路径包含链接，请检查安装目录。")
    return stage


def prepare_payload(root, zip_path, install_dir, current_version, connection, cancel, progress, *,
                    asset=None, version=None, update_id=""):
    from core.update_coordinator import atomic_json, update_log

    install_dir = os.path.abspath(install_dir)
    identity = uuid.uuid4().hex
    stage = stage_dir(install_dir, identity)
    native_stage = backend.platform_runtime.filesystem_path(stage)
    source_dir = stage / "files"
    owns_stage = published = False
    started = time.monotonic()
    try:
        with backend.platform_runtime.filesystem_path(zip_path).open("rb") as package:
            before = os.fstat(package.fileno())
            backend._emit(progress, "正在校验安装包…", -1)
            digest = _copy_digest(package, cancel)
            if asset is not None:
                expected = asset.get("digest", "")
                if (asset.get("size") and before.st_size != asset["size"]) or (expected and expected != "sha256:" + digest):
                    raise backend.UpdaterError("安装包校验失败，请重新下载。")
            with zipfile.ZipFile(package) as archive:
                members = backend._safe_zip_members(archive, source_dir, cancel)
                metadata = [name for name in members if name.rsplit("/", 1)[-1] == "desktop-release.json"]
                if len(metadata) != 1:
                    raise backend.UpdaterError("安装包缺少唯一的新版工作台版本信息。")
                prefix = metadata[0][:-len("desktop-release.json")]
                if any(not name.startswith(prefix) for name in members):
                    raise backend.UpdaterError("安装包包含多个安装目录。")
                files = {name[len(prefix):]: member for name, member in members.items()}
                exe_name = next((name for name in backend.DESKTOP_EXE_NAMES if name in files), None)
                if (not exe_name or not {"resources/app.asar", "resources/service/cowork-service.exe"} <= files.keys()
                        or any(backend._is_update_excluded(path) for path in files)):
                    raise backend.UpdaterError("安装包缺少必要资源或包含用户数据。")
                release = backend.validate_desktop_identity(json.loads(_read_entry(archive, files["desktop-release.json"], 16384)))
                target_version = release["version"]
                comparison = backend.compare_versions(target_version, current_version)
                if comparison < 0 or (version is not None and target_version != version.lstrip("vV")):
                    raise backend.UpdaterError("安装包版本低于当前版本或与发布信息不一致。")
                notes, verified = ("", False) if asset else backend.desktop_local_sidecar(zip_path, target_version, before.st_size, digest)
                connection_data = _read_entry(archive, files[CONNECTION_FILE], MAX_CONNECTION_BYTES) if CONNECTION_FILE in files else None
                candidate = parse_connection(connection_data) if connection_data is not None else None
                check_update_connection(connection, candidate)
                if connection is None and candidate is not None:
                    raise backend.UpdaterError("普通版需要标准安装包，企业安装包请在对应的企业版使用。")
                backend._emit(progress, "正在比较需要更新的文件…", -1)
                compared = time.monotonic()
                inventory = _inventory(archive, files, cancel)
                if connection_data is not None:
                    inventory[CONNECTION_FILE] = {"size": len(connection_data), "sha256": hashlib.sha256(connection_data).hexdigest()}
                base_build, installed = _installed_inventory(install_dir)
                plan = _compare(install_dir, inventory, installed, comparison == 0, cancel)
                writes = plan["added"] + plan["modified"]
                if comparison > 0 and not (writes or plan["deleted"]):
                    raise backend.UpdaterError("安装包文件与当前版本相同，无法升级。")
                summary = plan["summary"]
                update_log(root, "plan_compared", update_id=update_id, summary=summary,
                           verify_ms=round((compared - started) * 1000), compare_ms=round((time.monotonic() - compared) * 1000))
                native_stage.mkdir(parents=True)
                owns_stage = True
                backend.platform_runtime.filesystem_path(source_dir).mkdir()
                count, copied, percent = 0, 0, -1
                write_bytes = max(1, summary["write_bytes"])
                backend._emit(progress, "正在准备变更文件…", 0)

                def advance(amount):
                    nonlocal copied, percent
                    copied += amount
                    done = int(copied * 100 / write_bytes)
                    if done != percent:
                        percent = done
                        backend._emit(progress, f"正在准备变更文件（{count}/{len(writes)}）…", done)

                for item in writes:
                    backend.check_cancel(cancel)
                    destination = backend.platform_runtime.filesystem_path(source_dir / item["path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(files[item["path"]]) as source, destination.open("xb") as output:
                        if _copy_digest(source, cancel, output, advance) != item["sha256"]:
                            raise backend.UpdaterError("安装包文件校验失败：" + item["path"])
                    count += 1
                backend._emit(progress, f"已准备变更文件（{count}/{len(writes)}）", 100)
            after = os.fstat(package.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise backend.UpdaterError("安装包在准备期间发生变化，请重新选择。")
        backend.check_cancel(cancel)
        plan_path = stage / "plan.json"
        atomic_json(plan_path, plan)
        update_log(root, "changes_staged", update_id=update_id, elapsed_ms=round((time.monotonic() - started) * 1000),
                   files=len(writes), bytes=copied)
        plan_digest = backend._hash_file(plan_path, cancel)
        published = True
        return {"stage_id": identity, "install_dir": install_dir, "staged_app_dir": str(source_dir), "exe_name": exe_name,
                "change_plan_path": str(plan_path), "change_plan_sha256": plan_digest,
                "change_summary": summary, "base_build": base_build, "sha256": digest,
                "package_size": before.st_size, "package_version": target_version, "package_notes": notes,
                "manifest_verified": verified}
    except zipfile.BadZipFile as exc:
        raise backend.UpdaterError("更新包已损坏，请重新获取完整 ZIP。") from exc
    except (ValueError, EnterpriseConfigurationError) as exc:
        raise backend.UpdaterError(str(exc)) from exc
    finally:
        if owns_stage and not published:
            try:
                shutil.rmtree(native_stage)
            except OSError as exc:
                update_log(root, "cache_cleanup_failed", artifact=identity, error=str(exc))


def verify_prepared(install_dir, result, cancel=None, *, payload=False):
    if os.path.normcase(result.get("install_dir", "")) != os.path.normcase(os.path.abspath(install_dir)):
        raise backend.UpdaterError("更新暂存不属于当前安装目录。")
    stage = stage_dir(install_dir, result.get("stage_id"))
    source = stage / "files"
    plan = stage / "plan.json"
    _native = backend.platform_runtime.filesystem_path
    baseline = _native(Path(install_dir) / DESKTOP_FILES_NAME)
    build = backend._hash_file(baseline, cancel) if baseline.is_file() else None
    if (result.get("staged_app_dir") != str(source) or result.get("change_plan_path") != str(plan)
            or not _native(source).is_dir() or not _native(plan).is_file() or _native(plan).is_symlink()
            or build != result.get("base_build")
            or backend._hash_file(plan, cancel) != result.get("change_plan_sha256")):
        raise backend.UpdaterError("更新准备已失效，请重新选择安装包或检查更新。")
    if payload:
        with _native(plan).open("rb") as handle:
            changes = json.load(handle)
        for item in changes["added"] + changes["modified"]:
            relative = backend._normalized_relative_path(item["path"])
            if (backend._is_update_excluded(relative)
                    or not _native(source / relative).is_file()
                    or backend._hash_file(source / relative, cancel) != item["sha256"]):
                raise backend.UpdaterError("已准备的更新文件发生变化，请重新选择安装包或检查更新。")
