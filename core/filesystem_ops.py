import fnmatch
import json
import os
import re
import shutil


DEFAULT_EXCLUDE_DIRS = {".git", ".idea", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
GLOB_PATTERN_REGEX = re.compile(r"[*?[\]{}]")


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
    return os.path.normcase(os.path.abspath(path))


def _safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except Exception:
        return None


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
    return json.dumps(payload, ensure_ascii=False)


def _build_ok(action, extra=None):
    payload = {"ok": True, "action": action}
    if isinstance(extra, dict):
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


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


def record_full_read_state(abs_path, context):
    state = _get_file_state(context)
    state["reads"][_cache_key(abs_path)] = {"full": True, "mtime": _safe_mtime(abs_path)}


def mark_file_written(abs_path, context):
    record_full_read_state(abs_path, context)


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


def resolve_path(workspace_dir, path, context=None, action="filesystem", must_exist=False, reject_glob_for_write=False):
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
        if common != workspace_abs:
            return None, None, _build_error(action, "path_outside_workspace", "Path is outside the workspace.", path=raw_path)

    if must_exist and not os.path.exists(abs_path):
        display_path = _normalize_rel_path(os.path.relpath(abs_path, workspace_abs)) if not os.path.isabs(raw_path) else raw_path
        return None, None, _build_error(action, "path_not_found", "Path does not exist.", path=display_path)

    if god_mode:
        rel_path = abs_path if not abs_path.startswith(workspace_abs) else _normalize_rel_path(os.path.relpath(abs_path, workspace_abs))
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

    recorded_mtime = entry.get("mtime")
    current_mtime = _safe_mtime(abs_path)
    if recorded_mtime is not None and current_mtime is not None and current_mtime != recorded_mtime:
        return _build_error(
            action,
            "stale_write",
            "File changed since it was read. Read it again before modifying.",
            path=rel_path,
        )
    return None


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

    try:
        for root, dirs, files in os.walk(abs_path):
            dirs[:] = [
                name
                for name in dirs
                if name not in DEFAULT_EXCLUDE_DIRS and (include_hidden_flag or not _is_hidden_name(name))
            ]
            for name in sorted(files):
                if not include_hidden_flag and _is_hidden_name(name):
                    continue
                child_abs = os.path.join(root, name)
                child_rel = _normalize_rel_path(os.path.relpath(child_abs, workspace_dir))
                basename = os.path.basename(child_rel)
                if fnmatch.fnmatch(child_rel, normalized_pattern) or fnmatch.fnmatch(basename, normalized_pattern):
                    items.append(child_rel)
                    if len(items) >= parsed_limit:
                        truncated = True
                        return _build_ok(
                            action,
                            {"path": rel_path, "items": items[:parsed_limit], "count": len(items[:parsed_limit]), "truncated": truncated},
                        )
        return _build_ok(action, {"path": rel_path, "items": items, "count": len(items), "truncated": truncated})
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

    try:
        for root, dirs, files in os.walk(abs_path):
            dirs[:] = [
                name
                for name in dirs
                if name not in exclude_patterns
                and not fnmatch.fnmatch(name, ".*")
                and not any(fnmatch.fnmatch(name, p) for p in exclude_patterns if p not in DEFAULT_EXCLUDE_DIRS)
            ]
            for name in sorted(files):
                if name in exclude_patterns or any(fnmatch.fnmatch(name, p) for p in exclude_patterns):
                    continue
                if not fnmatch.fnmatch(name, include_pattern):
                    continue
                child_abs = os.path.join(root, name)
                child_rel = _normalize_rel_path(os.path.relpath(child_abs, workspace_dir))
                try:
                    with open(child_abs, "rb") as handle:
                        sample = handle.read(4096)
                    if b"\0" in sample:
                        continue
                    with open(child_abs, "r", encoding="utf-8", errors="replace") as handle:
                        for index, line in enumerate(handle, start=1):
                            if regex.search(line):
                                matches.append({"path": child_rel, "line": index, "text": line.rstrip("\r\n")})
                                if len(matches) >= parsed_limit:
                                    truncated = True
                                    return _build_ok(
                                        action,
                                        {"path": rel_path, "matches": matches[:parsed_limit], "count": len(matches[:parsed_limit]), "truncated": truncated},
                                    )
                except Exception:
                    continue
            if not recursive_flag:
                break
        return _build_ok(action, {"path": rel_path, "matches": matches, "count": len(matches), "truncated": truncated})
    except Exception as exc:
        return _build_error(action, "grep_failed", str(exc), path=rel_path)


def read_text_file(workspace_dir, path, offset=1, limit=None, context=None, action="read_file"):
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
        with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines(keepends=True)
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
        if parsed_offset == 1 and parsed_limit is None:
            record_full_read_state(abs_path, context)
        return _build_ok(
            action,
            {
                "path": rel_path,
                "content": content,
                "encoding": "utf-8",
                "truncated": truncated,
                "start_line": parsed_offset,
                "returned_lines": returned_lines,
                "total_lines": total_lines,
            },
        )
    except Exception as exc:
        return _build_error(action, "read_failed", str(exc), path=rel_path)


def write_text_file(workspace_dir, path, content, mode="overwrite", context=None, action="write_file"):
    abs_path, rel_path, error = resolve_path(
        workspace_dir,
        path,
        context=context,
        action=action,
        must_exist=False,
        reject_glob_for_write=True,
    )
    if error:
        return error

    mode_value = str(mode or "overwrite").strip().lower() or "overwrite"
    if mode_value not in {"overwrite", "append"}:
        return _build_error(action, "invalid_mode", "mode must be 'overwrite' or 'append'.", path=rel_path)

    existed_before = os.path.exists(abs_path)
    if existed_before:
        error = ensure_existing_file_write_allowed(abs_path, rel_path, context, action)
        if error:
            return error
    else:
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    if os.path.isdir(abs_path):
        return _build_error(action, "not_a_file", "Target path is a directory.", path=rel_path)

    text = content if isinstance(content, str) else str(content)
    write_mode = "a" if mode_value == "append" else "w"
    try:
        with open(abs_path, write_mode, encoding="utf-8") as handle:
            handle.write(text)
        mark_file_written(abs_path, context)
        if not existed_before:
            change_type = "create"
        elif mode_value == "append":
            change_type = "append"
        else:
            change_type = "update"
        return _build_ok(
            action,
            {
                "path": rel_path,
                "change_type": change_type,
                "bytes_written": len(text.encode("utf-8")),
            },
        )
    except Exception as exc:
        return _build_error(action, "write_failed", str(exc), path=rel_path)


def update_text_file(workspace_dir, path, old_string, new_string, replace_all=False, context=None, action="update_file"):
    abs_path, rel_path, error = resolve_path(
        workspace_dir,
        path,
        context=context,
        action=action,
        must_exist=True,
        reject_glob_for_write=True,
    )
    if error:
        return error
    if not os.path.isfile(abs_path):
        return _build_error(action, "not_a_file", "Path is not a file.", path=rel_path)

    error = ensure_existing_file_write_allowed(abs_path, rel_path, context, action)
    if error:
        return error

    old_text = old_string if isinstance(old_string, str) else str(old_string)
    new_text = new_string if isinstance(new_string, str) else str(new_string)
    replace_all_flag = bool(replace_all)

    if old_text == new_text:
        return _build_error(action, "identical_replacement", "old_string and new_string must be different.", path=rel_path)

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
            original = handle.read()
    except Exception as exc:
        return _build_error(action, "read_failed", str(exc), path=rel_path)

    occurrences = original.count(old_text)
    if occurrences == 0:
        return _build_error(action, "target_not_found", "old_string was not found in file.", path=rel_path)
    if occurrences > 1 and not replace_all_flag:
        return _build_error(
            action,
            "ambiguous_match",
            "Multiple matches found. Set replace_all=true to replace all occurrences.",
            path=rel_path,
        )

    if replace_all_flag:
        updated = original.replace(old_text, new_text)
        replaced_count = occurrences
    else:
        updated = original.replace(old_text, new_text, 1)
        replaced_count = 1

    try:
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(updated)
        mark_file_written(abs_path, context)
        return _build_ok(
            action,
            {
                "path": rel_path,
                "change_type": "update",
                "bytes_written": len(updated.encode("utf-8")),
                "replaced_count": replaced_count,
            },
        )
    except Exception as exc:
        return _build_error(action, "write_failed", str(exc), path=rel_path)


def rename_path(workspace_dir, old_path, new_path, context=None, action="rename_file"):
    abs_old, rel_old, error = resolve_path(workspace_dir, old_path, context=context, action=action, must_exist=True)
    if error:
        return error
    abs_new, rel_new, error = resolve_path(
        workspace_dir,
        new_path,
        context=context,
        action=action,
        must_exist=False,
        reject_glob_for_write=True,
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
