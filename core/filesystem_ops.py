import codecs
import fnmatch
import hashlib
import os
import re
import shutil
import stat
import tempfile


DEFAULT_EXCLUDE_DIRS = {".git", ".idea", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
GLOB_PATTERN_REGEX = re.compile(r"[*?[\]{}]")
MAX_TEXT_FILE_BYTES = 10 * 1024 * 1024
MAX_SEARCH_WARNINGS = 100


class TextFileCodecError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _is_god_mode(context):
    if isinstance(context, dict):
        cfg = context.get("config_manager")
        if cfg:
            try:
                return bool(cfg.get_god_mode())
            except Exception:
                return False
    return False


def _normalize_rel_path(path):
    normalized = os.path.normpath(path or ".")
    if normalized == ".":
        return "."
    return normalized.replace("\\", "/")


def _cache_key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_file_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _newline_for_text(value):
    crlf = value.count("\r\n")
    without_crlf = value.replace("\r\n", "")
    lf = without_crlf.count("\n")
    cr = without_crlf.count("\r")
    if crlf and crlf >= lf and crlf >= cr:
        return "\r\n"
    if cr and cr > lf:
        return "\r"
    return "\n"


def decode_text_bytes(data, selected_encoding=None):
    raw = bytes(data or b"")
    bom_candidates = (
        (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    )
    for bom, encoding in bom_candidates:
        if bom and raw.startswith(bom):
            try:
                text = raw[len(bom) :].decode(encoding, errors="strict")
            except UnicodeDecodeError as exc:
                raise TextFileCodecError(
                    "text_decode_failed",
                    f"File declares {encoding} but cannot be decoded strictly: {exc}",
                ) from exc
            return text, encoding, bom, _newline_for_text(text)

    selected = str(selected_encoding or "").strip()
    if selected:
        try:
            normalized = codecs.lookup(selected).name
            if normalized == "utf-8-sig":
                normalized = "utf-8"
            if normalized in {"utf-16", "utf-32"}:
                raise TextFileCodecError(
                    "encoding_requires_bom",
                    f"{selected} requires a BOM. Specify an explicit endian encoding such as {normalized}-le or {normalized}-be.",
                )
            text = raw.decode(normalized, errors="strict")
        except TextFileCodecError:
            raise
        except LookupError as exc:
            raise TextFileCodecError(
                "unsupported_encoding",
                f"Unknown text encoding: {selected}",
            ) from exc
        except UnicodeDecodeError as exc:
            raise TextFileCodecError(
                "text_decode_failed",
                f"File cannot be decoded strictly as {selected}: {exc}",
            ) from exc
        return text, normalized, b"", _newline_for_text(text)

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TextFileCodecError(
            "encoding_required",
            "Text encoding cannot be determined. Read the file again with an explicit encoding.",
        ) from exc
    return text, "utf-8", b"", _newline_for_text(text)


def encode_text_bytes(text, encoding="utf-8", bom=b"", newline="\n"):
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    newline_value = newline if newline in {"\n", "\r\n", "\r"} else "\n"
    normalized = normalized.replace("\n", newline_value)
    try:
        encoded = normalized.encode(encoding or "utf-8", errors="strict")
    except (LookupError, UnicodeEncodeError) as exc:
        raise TextFileCodecError(
            "text_encode_failed",
            f"Text cannot be encoded as {encoding or 'utf-8'}: {exc}",
        ) from exc
    return bytes(bom or b"") + encoded


def _is_reparse_point(path, *, strict=False):
    try:
        if os.path.islink(path):
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if callable(isjunction) and isjunction(path):
            return True
        file_attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(reparse_attribute and file_attributes & reparse_attribute)
    except OSError:
        if strict:
            raise
        return False


def _write_reparse_component(workspace_abs, abs_path):
    try:
        relative = os.path.relpath(abs_path, workspace_abs)
    except ValueError:
        relative = abs_path
    if relative == ".":
        candidates = [abs_path]
    elif os.path.isabs(relative) or relative.startswith(".." + os.sep) or relative == "..":
        drive, tail = os.path.splitdrive(abs_path)
        current = drive + os.sep if drive else os.sep
        candidates = []
        for part in [item for item in tail.split(os.sep) if item]:
            current = os.path.join(current, part)
            candidates.append(current)
    else:
        current = workspace_abs
        candidates = [workspace_abs]
        for part in [item for item in relative.split(os.sep) if item and item != "."]:
            current = os.path.join(current, part)
            candidates.append(current)
    for candidate in candidates:
        if os.path.lexists(candidate) and _is_reparse_point(candidate, strict=True):
            return candidate
    return ""


def _is_hidden_name(name):
    return (name or "").startswith(".")


def _is_unc_path(path):
    text = str(path or "")
    return text.startswith("\\\\") or text.startswith("//")


def _build_error(action, code, message, path=None, extra=None):
    payload = {"ok": False, "action": action, "error": {"code": code, "message": message}}
    if path is not None:
        payload["path"] = path
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _build_ok(action, extra=None):
    payload = {"ok": True, "action": action}
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _parse_positive_int(value, field_name, action, path=None, allow_none=False):
    if value is None and allow_none:
        return None, None
    try:
        parsed = int(value)
    except Exception:
        return None, _build_error(action, "invalid_argument", f"{field_name} must be an integer.", path=path)
    if parsed <= 0:
        return None, _build_error(action, "invalid_argument", f"{field_name} must be greater than 0.", path=path)
    return parsed, None


def _get_file_state(context):
    if not isinstance(context, dict):
        return {}
    state = context.setdefault("file_state", {})
    if not isinstance(state, dict):
        state = {}
        context["file_state"] = state
    reads = state.setdefault("reads", {})
    if not isinstance(reads, dict):
        reads = {}
        state["reads"] = reads
    return state


def record_full_read_state(
    abs_path,
    context,
    *,
    data=None,
    encoding="",
    bom=b"",
    newline="\n",
):
    state = _get_file_state(context)
    if "reads" not in state:
        return None
    raw = _read_file_bytes(abs_path) if data is None else bytes(data)
    file_stat = os.stat(abs_path)
    entry = {
        "full": True,
        "sha256": _sha256_bytes(raw),
        "size": len(raw),
        "mtime_ns": int(file_stat.st_mtime_ns),
        "encoding": str(encoding or ""),
        "bom_hex": bytes(bom or b"").hex(),
        "newline": newline if newline in {"\n", "\r\n", "\r"} else "\n",
    }
    state["reads"][_cache_key(abs_path)] = entry
    return dict(entry)


def mark_file_written(
    abs_path,
    context,
    *,
    data=None,
    encoding="utf-8",
    bom=b"",
    newline="\n",
):
    record_full_read_state(
        abs_path,
        context,
        data=data,
        encoding=encoding,
        bom=bom,
        newline=newline,
    )


def clear_read_state(abs_path, context, recursive=False):
    state = _get_file_state(context)
    reads = state.get("reads", {})
    if not isinstance(reads, dict):
        return
    key = _cache_key(abs_path)
    reads.pop(key, None)
    if recursive:
        prefix = key + os.sep
        stale = [item for item in reads.keys() if item.startswith(prefix)]
        for item in stale:
            reads.pop(item, None)


def resolve_path(
    workspace_dir,
    path,
    context=None,
    action="filesystem",
    must_exist=False,
    reject_glob_for_write=False,
    for_write=False,
):
    if not workspace_dir:
        return None, None, _build_error(action, "workspace_not_selected", "Workspace not selected.")

    raw_path = str(path if path is not None else ".").strip() or "."
    if _is_unc_path(raw_path):
        return None, None, _build_error(action, "unc_path_rejected", "UNC paths are not allowed.", path=raw_path)

    if reject_glob_for_write and GLOB_PATTERN_REGEX.search(raw_path):
        return None, None, _build_error(
            action,
            "glob_not_allowed_for_write",
            "Glob patterns are not allowed in write operations. Please use an exact path.",
            path=raw_path,
        )

    workspace_abs = os.path.abspath(workspace_dir)
    if os.path.isabs(raw_path):
        abs_path = os.path.abspath(raw_path)
    else:
        abs_path = os.path.abspath(os.path.join(workspace_abs, raw_path))

    god_mode = _is_god_mode(context)
    if not god_mode:
        try:
            common = os.path.commonpath([workspace_abs, abs_path])
        except Exception:
            return None, None, _build_error(action, "path_outside_workspace", "Path is outside the workspace.", path=raw_path)
        if os.path.normcase(common) != os.path.normcase(workspace_abs):
            return None, None, _build_error(action, "path_outside_workspace", "Path is outside the workspace.", path=raw_path)

        workspace_real = os.path.realpath(workspace_abs)
        path_real = os.path.realpath(abs_path)
        try:
            real_common = os.path.commonpath([workspace_real, path_real])
        except Exception:
            return None, None, _build_error(action, "path_outside_workspace", "Path resolves outside the workspace.", path=raw_path)
        if os.path.normcase(real_common) != os.path.normcase(workspace_real):
            return None, None, _build_error(
                action,
                "path_outside_workspace",
                "Path resolves outside the workspace through a symbolic link or directory junction.",
                path=raw_path,
            )

    if for_write:
        try:
            reparse_component = _write_reparse_component(workspace_abs, abs_path)
        except OSError as exc:
            return None, None, _build_error(
                action,
                "path_inspection_failed",
                f"Could not inspect the write path for symbolic links or directory junctions: {exc}",
                path=raw_path,
            )
        if reparse_component:
            return None, None, _build_error(
                action,
                "reparse_point_not_allowed",
                "Write paths cannot traverse symbolic links or directory junctions.",
                path=raw_path,
            )

    if must_exist and not os.path.exists(abs_path):
        display_path = _normalize_rel_path(os.path.relpath(abs_path, workspace_abs)) if not os.path.isabs(raw_path) else raw_path
        return None, None, _build_error(action, "path_not_found", "Path does not exist.", path=display_path)

    if god_mode:
        try:
            common = os.path.commonpath([workspace_abs, abs_path])
        except Exception:
            common = ""
        rel_path = (
            _normalize_rel_path(os.path.relpath(abs_path, workspace_abs))
            if os.path.normcase(common) == os.path.normcase(workspace_abs)
            else abs_path
        )
    else:
        rel_path = _normalize_rel_path(os.path.relpath(abs_path, workspace_abs))
    return abs_path, rel_path, None


def ensure_existing_file_write_allowed(abs_path, rel_path, context, action):
    if not os.path.exists(abs_path):
        return None
    if not os.path.isfile(abs_path):
        return _build_error(action, "not_a_file", "Target path is not a regular file.", path=rel_path)

    state = _get_file_state(context)
    reads = state.get("reads", {})
    entry = reads.get(_cache_key(abs_path)) if isinstance(reads, dict) else None
    if not isinstance(entry, dict) or not entry.get("full"):
        return _build_error(
            action,
            "read_required",
            "Existing files must be fully read before modification.",
            path=rel_path,
        )

    try:
        current_bytes = _read_file_bytes(abs_path)
    except Exception as exc:
        return _build_error(action, "read_failed", str(exc), path=rel_path)
    current_sha256 = _sha256_bytes(current_bytes)
    if current_sha256 != entry.get("sha256"):
        return _build_error(
            action,
            "stale_write",
            "File changed since it was read. Read it again before modifying.",
            path=rel_path,
        )
    return None


def get_verified_read_state(abs_path, rel_path, context, action):
    error = ensure_existing_file_write_allowed(abs_path, rel_path, context, action)
    if error:
        return None, error
    state = _get_file_state(context)
    entry = (state.get("reads") or {}).get(_cache_key(abs_path))
    return dict(entry or {}), None


def list_files(workspace_dir, path=".", recursive=False, include_hidden=False, limit=200, context=None):
    action = "list_files"
    abs_path, rel_path, error = resolve_path(workspace_dir, path, context=context, action=action, must_exist=True)
    if error:
        return error
    if not os.path.isdir(abs_path):
        return _build_error(action, "not_a_directory", "Path is not a directory.", path=rel_path)

    parsed_limit, error = _parse_positive_int(limit, "limit", action, path=rel_path)
    if error:
        return error

    items = []
    truncated = False
    recursive_flag = bool(recursive)
    include_hidden_flag = bool(include_hidden)

    try:
        if recursive_flag:
            for root, dirs, files in os.walk(abs_path):
                dirs[:] = [
                    name
                    for name in dirs
                    if name not in DEFAULT_EXCLUDE_DIRS and (include_hidden_flag or not _is_hidden_name(name))
                ]
                current_entries = sorted(dirs) + sorted(files)
                for name in current_entries:
                    if name in DEFAULT_EXCLUDE_DIRS:
                        continue
                    if not include_hidden_flag and _is_hidden_name(name):
                        continue
                    child_abs = os.path.join(root, name)
                    child_rel = _normalize_rel_path(os.path.relpath(child_abs, workspace_dir))
                    items.append(child_rel)
                    if len(items) >= parsed_limit:
                        truncated = True
                        return _build_ok(
                            action,
                            {"path": rel_path, "items": items[:parsed_limit], "count": len(items[:parsed_limit]), "truncated": truncated},
                        )
        else:
            for name in sorted(os.listdir(abs_path)):
                if name in DEFAULT_EXCLUDE_DIRS:
                    continue
                if not include_hidden_flag and _is_hidden_name(name):
                    continue
                child_abs = os.path.join(abs_path, name)
                child_rel = _normalize_rel_path(os.path.relpath(child_abs, workspace_dir))
                items.append(child_rel)
                if len(items) >= parsed_limit:
                    truncated = len(sorted(os.listdir(abs_path))) > parsed_limit
                    break
        return _build_ok(action, {"path": rel_path, "items": items[:parsed_limit], "count": len(items[:parsed_limit]), "truncated": truncated})
    except Exception as exc:
        return _build_error(action, "list_failed", str(exc), path=rel_path)


def glob_paths(workspace_dir, pattern="*", path=".", limit=200, include_hidden=False, context=None):
    action = "glob"
    abs_path, rel_path, error = resolve_path(workspace_dir, path, context=context, action=action, must_exist=True)
    if error:
        return error
    if not os.path.isdir(abs_path):
        return _build_error(action, "not_a_directory", "Path is not a directory.", path=rel_path)

    parsed_limit, error = _parse_positive_int(limit, "limit", action, path=rel_path)
    if error:
        return error

    include_hidden_flag = bool(include_hidden)
    normalized_pattern = str(pattern or "*").strip() or "*"
    items = []
    truncated = False
    warnings = []
    skipped_count = 0

    def add_warning(target, code, message):
        nonlocal skipped_count
        skipped_count += 1
        if len(warnings) < MAX_SEARCH_WARNINGS:
            warnings.append(
                {
                    "path": _normalize_rel_path(os.path.relpath(target, workspace_dir)),
                    "code": code,
                    "message": message,
                }
            )

    def walk_error(exc):
        add_warning(getattr(exc, "filename", None) or abs_path, "path_read_failed", str(exc))

    def result_payload(current_items, is_truncated):
        return {
            "path": rel_path,
            "items": current_items,
            "count": len(current_items),
            "truncated": is_truncated,
            "warnings": warnings,
            "skipped_count": skipped_count,
            "warnings_truncated": skipped_count > len(warnings),
        }

    if _is_reparse_point(abs_path):
        add_warning(abs_path, "reparse_point_skipped", "Symbolic-link and junction traversal is disabled.")
        return _build_ok(action, result_payload([], False))

    try:
        for root, dirs, files in os.walk(abs_path, topdown=True, followlinks=False, onerror=walk_error):
            retained_dirs = []
            for name in dirs:
                child_abs = os.path.join(root, name)
                if name in DEFAULT_EXCLUDE_DIRS or (not include_hidden_flag and _is_hidden_name(name)):
                    continue
                if _is_reparse_point(child_abs):
                    add_warning(child_abs, "reparse_point_skipped", "Symbolic-link and junction traversal is disabled.")
                    continue
                retained_dirs.append(name)
            dirs[:] = sorted(retained_dirs)
            for name in sorted(files):
                if not include_hidden_flag and _is_hidden_name(name):
                    continue
                child_abs = os.path.join(root, name)
                if _is_reparse_point(child_abs):
                    add_warning(child_abs, "reparse_point_skipped", "Symbolic-link files are not searched.")
                    continue
                child_rel = _normalize_rel_path(os.path.relpath(child_abs, workspace_dir))
                basename = os.path.basename(child_rel)
                if fnmatch.fnmatch(child_rel, normalized_pattern) or fnmatch.fnmatch(basename, normalized_pattern):
                    items.append(child_rel)
                    if len(items) >= parsed_limit:
                        truncated = True
                        return _build_ok(action, result_payload(items[:parsed_limit], truncated))
        return _build_ok(action, result_payload(items, truncated))
    except Exception as exc:
        return _build_error(action, "glob_failed", str(exc), path=rel_path)


def _normalize_exclude_patterns(exclude):
    patterns = set(DEFAULT_EXCLUDE_DIRS)
    if exclude is None:
        return patterns
    if isinstance(exclude, str):
        raw_items = [item.strip() for item in exclude.split(",")]
    elif isinstance(exclude, (list, tuple, set)):
        raw_items = [str(item).strip() for item in exclude]
    else:
        raw_items = []
    for item in raw_items:
        if item:
            patterns.add(item)
    return patterns


def grep_contents(workspace_dir, pattern, path=".", include="*", exclude=None, recursive=True, limit=200, context=None):
    action = "grep"
    abs_path, rel_path, error = resolve_path(workspace_dir, path, context=context, action=action, must_exist=True)
    if error:
        return error
    if not os.path.isdir(abs_path):
        return _build_error(action, "not_a_directory", "Path is not a directory.", path=rel_path)

    parsed_limit, error = _parse_positive_int(limit, "limit", action, path=rel_path)
    if error:
        return error

    try:
        regex = re.compile(str(pattern))
    except re.error as exc:
        return _build_error(action, "invalid_regex", str(exc), path=rel_path)

    include_pattern = str(include or "*")
    exclude_patterns = _normalize_exclude_patterns(exclude)
    recursive_flag = bool(recursive)
    matches = []
    truncated = False
    warnings = []
    skipped_count = 0

    def add_warning(target, code, message):
        nonlocal skipped_count
        skipped_count += 1
        if len(warnings) < MAX_SEARCH_WARNINGS:
            warnings.append(
                {
                    "path": _normalize_rel_path(os.path.relpath(target, workspace_dir)),
                    "code": code,
                    "message": message,
                }
            )

    def walk_error(exc):
        add_warning(getattr(exc, "filename", None) or abs_path, "path_read_failed", str(exc))

    def result_payload(current_matches, is_truncated):
        return {
            "path": rel_path,
            "matches": current_matches,
            "count": len(current_matches),
            "truncated": is_truncated,
            "warnings": warnings,
            "skipped_count": skipped_count,
            "warnings_truncated": skipped_count > len(warnings),
        }

    if _is_reparse_point(abs_path):
        add_warning(abs_path, "reparse_point_skipped", "Symbolic-link and junction traversal is disabled.")
        return _build_ok(action, result_payload([], False))

    try:
        for root, dirs, files in os.walk(abs_path, topdown=True, followlinks=False, onerror=walk_error):
            retained_dirs = []
            for name in dirs:
                child_abs = os.path.join(root, name)
                if (
                    name in exclude_patterns
                    or fnmatch.fnmatch(name, ".*")
                    or any(fnmatch.fnmatch(name, p) for p in exclude_patterns if p not in DEFAULT_EXCLUDE_DIRS)
                ):
                    continue
                if _is_reparse_point(child_abs):
                    add_warning(child_abs, "reparse_point_skipped", "Symbolic-link and junction traversal is disabled.")
                    continue
                retained_dirs.append(name)
            dirs[:] = sorted(retained_dirs)
            for name in sorted(files):
                if name in exclude_patterns or any(fnmatch.fnmatch(name, p) for p in exclude_patterns):
                    continue
                if not fnmatch.fnmatch(name, include_pattern):
                    continue
                child_abs = os.path.join(root, name)
                child_rel = _normalize_rel_path(os.path.relpath(child_abs, workspace_dir))
                if _is_reparse_point(child_abs):
                    add_warning(child_abs, "reparse_point_skipped", "Symbolic-link files are not searched.")
                    continue
                try:
                    file_size = os.path.getsize(child_abs)
                    if file_size > MAX_TEXT_FILE_BYTES:
                        add_warning(
                            child_abs,
                            "file_too_large",
                            f"File exceeds the {MAX_TEXT_FILE_BYTES // (1024 * 1024)} MiB text search limit.",
                        )
                        continue
                    raw = _read_file_bytes(child_abs)
                    if len(raw) > MAX_TEXT_FILE_BYTES:
                        add_warning(
                            child_abs,
                            "file_too_large",
                            f"File exceeded the {MAX_TEXT_FILE_BYTES // (1024 * 1024)} MiB text search limit while being read.",
                        )
                        continue
                    text, _encoding, _bom, _newline = decode_text_bytes(raw)
                    if "\x00" in text:
                        add_warning(child_abs, "binary_file_skipped", "Binary files are not searched as text.")
                        continue
                    for index, line in enumerate(text.splitlines(), start=1):
                        if regex.search(line):
                            matches.append({"path": child_rel, "line": index, "text": line})
                            if len(matches) >= parsed_limit:
                                truncated = True
                                return _build_ok(action, result_payload(matches[:parsed_limit], truncated))
                except TextFileCodecError as exc:
                    add_warning(child_abs, exc.code, exc.message)
                    continue
                except OSError as exc:
                    add_warning(child_abs, "file_read_failed", str(exc))
                    continue
            if not recursive_flag:
                break
        return _build_ok(action, result_payload(matches, truncated))
    except Exception as exc:
        return _build_error(action, "grep_failed", str(exc), path=rel_path)


def read_text_file(
    workspace_dir,
    path,
    offset=1,
    limit=None,
    encoding=None,
    context=None,
    action="read_file",
):
    abs_path, rel_path, error = resolve_path(workspace_dir, path, context=context, action=action, must_exist=True)
    if error:
        return error
    if not os.path.isfile(abs_path):
        return _build_error(action, "not_a_file", "Path is not a file.", path=rel_path)

    parsed_offset, error = _parse_positive_int(offset, "offset", action, path=rel_path)
    if error:
        return error
    parsed_limit, error = _parse_positive_int(limit, "limit", action, path=rel_path, allow_none=True)
    if error:
        return error

    try:
        file_size = os.path.getsize(abs_path)
        if file_size > MAX_TEXT_FILE_BYTES:
            return _build_error(
                action,
                "file_too_large",
                f"Plain text files larger than {MAX_TEXT_FILE_BYTES // (1024 * 1024)} MiB are not supported.",
                path=rel_path,
                extra={"file_size": file_size, "max_bytes": MAX_TEXT_FILE_BYTES},
            )
        raw = _read_file_bytes(abs_path)
        file_stat = os.stat(abs_path)
        text, detected_encoding, bom, newline = decode_text_bytes(raw, selected_encoding=encoding)
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        start_index = min(parsed_offset - 1, total_lines)
        if parsed_limit is None:
            selected = lines[start_index:]
            truncated = False
        else:
            end_index = start_index + parsed_limit
            selected = lines[start_index:end_index]
            truncated = end_index < total_lines
        content = "".join(selected)
        returned_lines = len(selected)
        audit_complete = parsed_offset == 1 and not truncated
        if audit_complete:
            record_full_read_state(
                abs_path,
                context,
                data=raw,
                encoding=detected_encoding,
                bom=bom,
                newline=newline,
            )
        return _build_ok(
            action,
            {
                "path": rel_path,
                "content": content,
                "encoding": detected_encoding,
                "bom": bool(bom),
                "newline": newline,
                "sha256": _sha256_bytes(raw),
                "file_size": len(raw),
                "mtime_ns": int(file_stat.st_mtime_ns),
                "truncated": truncated,
                "start_line": parsed_offset,
                "returned_lines": returned_lines,
                "total_lines": total_lines,
                "next_offset": parsed_offset + returned_lines if truncated else None,
                "audit_complete": audit_complete,
            },
        )
    except TextFileCodecError as exc:
        return _build_error(action, exc.code, exc.message, path=rel_path)
    except Exception as exc:
        return _build_error(action, "read_failed", str(exc), path=rel_path)


def _atomic_write_bytes(abs_path, data, *, existed_before, expected_sha256=None):
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(abs_path)}.cowork-",
        suffix=".tmp",
        dir=parent or None,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if existed_before:
            if not os.path.isfile(abs_path):
                raise FileNotFoundError("Target file disappeared before commit.")
            if expected_sha256 and _sha256_bytes(_read_file_bytes(abs_path)) != expected_sha256:
                raise RuntimeError("File changed before commit.")
            mode_bits = stat.S_IMODE(os.stat(abs_path).st_mode)
            os.chmod(temp_path, mode_bits)
            os.replace(temp_path, abs_path)
        elif os.name == "nt":
            os.rename(temp_path, abs_path)
        else:
            os.link(temp_path, abs_path)
            os.unlink(temp_path)
        temp_path = ""
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def rename_path(workspace_dir, old_path, new_path, context=None, action="rename_file"):
    abs_old, rel_old, error = resolve_path(
        workspace_dir,
        old_path,
        context=context,
        action=action,
        must_exist=True,
        for_write=True,
    )
    if error:
        return error
    abs_new, rel_new, error = resolve_path(
        workspace_dir,
        new_path,
        context=context,
        action=action,
        must_exist=False,
        reject_glob_for_write=True,
        for_write=True,
    )
    if error:
        return error

    if os.path.exists(abs_new):
        return _build_error(action, "destination_exists", "Destination path already exists.", path=rel_new)

    parent = os.path.dirname(abs_new)
    if parent:
        os.makedirs(parent, exist_ok=True)

    old_key = _cache_key(abs_old)
    new_key = _cache_key(abs_new)

    try:
        os.rename(abs_old, abs_new)
        state = _get_file_state(context)
        reads = state.get("reads", {})
        if isinstance(reads, dict):
            moved_entry = reads.pop(old_key, None)
            if moved_entry is not None:
                reads[new_key] = moved_entry
            prefix = old_key + os.sep
            moved_children = [(key, value) for key, value in reads.items() if key.startswith(prefix)]
            for key, value in moved_children:
                reads.pop(key, None)
                suffix = key[len(old_key) :]
                reads[new_key + suffix] = value
        return _build_ok(
            action,
            {"from_path": rel_old, "to_path": rel_new, "change_type": "rename"},
        )
    except Exception as exc:
        return _build_error(action, "rename_failed", str(exc), path=rel_old)


def delete_path(workspace_dir, path, recursive=False, confirm_callback=None, context=None, action="delete_file"):
    abs_path, rel_path, error = resolve_path(
        workspace_dir,
        path,
        context=context,
        action=action,
        must_exist=True,
        reject_glob_for_write=True,
        for_write=True,
    )
    if error:
        return error

    if confirm_callback is not None:
        try:
            confirmed = confirm_callback(rel_path, bool(recursive))
        except Exception:
            confirmed = False
        if confirmed is not True:
            return _build_error(
                action,
                "cancelled",
                "Deletion cancelled by user.",
                path=rel_path,
                extra={"requires_confirmation": True},
            )

    recursive_flag = bool(recursive)
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            clear_read_state(abs_path, context, recursive=False)
            removed = "file"
        elif os.path.isdir(abs_path):
            if recursive_flag:
                shutil.rmtree(abs_path)
                removed = "directory_recursive"
            else:
                os.rmdir(abs_path)
                removed = "directory"
            clear_read_state(abs_path, context, recursive=True)
        else:
            return _build_error(action, "unknown_path_type", "Unknown path type.", path=rel_path)
        return _build_ok(
            action,
            {
                "path": rel_path,
                "change_type": "delete",
                "removed": removed,
                "requires_confirmation": True,
            },
        )
    except Exception as exc:
        return _build_error(action, "delete_failed", str(exc), path=rel_path)
