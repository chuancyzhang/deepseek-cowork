from __future__ import annotations

from dataclasses import dataclass, field
import os
import time

from .filesystem_ops import (
    MAX_TEXT_FILE_BYTES,
    _atomic_write_bytes,
    _build_error,
    _build_ok,
    _cache_key,
    _get_file_state,
    _read_file_bytes,
    _sha256_bytes,
    clear_read_state,
    decode_text_bytes,
    encode_text_bytes,
    get_verified_read_state,
    mark_file_written,
    resolve_path,
)


MAX_PATCH_BYTES = 12 * 1024 * 1024
MAX_PATCH_FILES = 100
STRUCTURED_DOCUMENT_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xls", ".pdf"}


class PatchError(Exception):
    def __init__(self, code, message, *, line=None, path=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.line = line
        self.path = path


@dataclass
class PatchChunk:
    context: str | None
    old_lines: list[str]
    new_lines: list[str]
    end_of_file: bool
    line: int


@dataclass
class PatchOperation:
    kind: str
    path: str
    line: int
    contents: str = ""
    move_path: str | None = None
    chunks: list[PatchChunk] = field(default_factory=list)


@dataclass
class PreparedChange:
    kind: str
    path: str
    abs_path: str
    rel_path: str
    expected_sha256: str | None = None
    data: bytes | None = None
    encoding: str = ""
    bom: bytes = b""
    newline: str = "\n"
    move_path: str | None = None
    abs_move_path: str | None = None
    rel_move_path: str | None = None
    has_content_update: bool = False


FILE_HEADERS = (
    "*** Add File: ",
    "*** Delete File: ",
    "*** Update File: ",
)


def _is_file_header(line):
    return any(line.startswith(prefix) for prefix in FILE_HEADERS)


def _normalize_marker_line(line):
    stripped = line.strip()
    if stripped in {
        "*** Begin Patch",
        "*** End Patch",
        "*** End of File",
        "@@",
    }:
        return stripped
    if any(
        stripped.startswith(prefix)
        for prefix in (*FILE_HEADERS, "*** Move to: ", "@@ ")
    ):
        return stripped
    return line


def _parse_path_header(line, prefix, line_number):
    path = line[len(prefix) :].strip()
    if not path:
        raise PatchError("invalid_patch", "Patch file paths must not be empty.", line=line_number)
    if "\x00" in path:
        raise PatchError("invalid_patch", "Patch file paths must not contain NUL bytes.", line=line_number)
    return path


def parse_patch(patch):
    if not isinstance(patch, str):
        raise PatchError("invalid_patch", "patch must be a string.")
    try:
        patch_bytes = patch.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise PatchError(
            "invalid_patch",
            f"Patch input is not valid Unicode text: {exc}",
        ) from exc
    if len(patch_bytes) > MAX_PATCH_BYTES:
        raise PatchError(
            "patch_too_large",
            f"Patch input exceeds {MAX_PATCH_BYTES // (1024 * 1024)} MiB.",
        )
    normalized = patch.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [_normalize_marker_line(line) for line in normalized.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "*** Begin Patch":
        raise PatchError("invalid_patch", "The first line must be '*** Begin Patch'.", line=1)
    if len(lines) < 2 or lines[-1] != "*** End Patch":
        raise PatchError(
            "invalid_patch",
            "The last line must be '*** End Patch'.",
            line=max(len(lines), 1),
        )

    operations = []
    index = 1
    end_index = len(lines) - 1
    while index < end_index:
        line = lines[index]
        line_number = index + 1
        if line.startswith("*** Add File: "):
            path = _parse_path_header(line, "*** Add File: ", line_number)
            index += 1
            content_lines = []
            while index < end_index and not _is_file_header(lines[index]):
                current = lines[index]
                if not current.startswith("+"):
                    raise PatchError(
                        "invalid_hunk",
                        "Every Add File content line must start with '+'.",
                        line=index + 1,
                        path=path,
                    )
                content_lines.append(current[1:])
                index += 1
            if not content_lines:
                raise PatchError(
                    "invalid_hunk",
                    "Add File requires at least one '+' content line.",
                    line=line_number,
                    path=path,
                )
            operations.append(
                PatchOperation(
                    kind="add",
                    path=path,
                    line=line_number,
                    contents="\n".join(content_lines) + "\n",
                )
            )
        elif line.startswith("*** Delete File: "):
            path = _parse_path_header(line, "*** Delete File: ", line_number)
            operations.append(PatchOperation(kind="delete", path=path, line=line_number))
            index += 1
            if index < end_index and not _is_file_header(lines[index]):
                raise PatchError(
                    "invalid_hunk",
                    "Delete File does not accept content lines.",
                    line=index + 1,
                    path=path,
                )
        elif line.startswith("*** Update File: "):
            path = _parse_path_header(line, "*** Update File: ", line_number)
            operation = PatchOperation(kind="update", path=path, line=line_number)
            index += 1
            if index < end_index and lines[index].startswith("*** Move to: "):
                operation.move_path = _parse_path_header(
                    lines[index],
                    "*** Move to: ",
                    index + 1,
                )
                index += 1
            while index < end_index and not _is_file_header(lines[index]):
                header = lines[index]
                if header == "@@":
                    context = None
                elif header.startswith("@@ "):
                    context = header[3:]
                    if not context:
                        raise PatchError(
                            "invalid_hunk",
                            "Update hunk context must not be empty after '@@ '.",
                            line=index + 1,
                            path=path,
                        )
                else:
                    raise PatchError(
                        "invalid_hunk",
                        "Update content must start with an '@@' hunk header.",
                        line=index + 1,
                        path=path,
                    )
                chunk_line = index + 1
                index += 1
                old_lines = []
                new_lines = []
                has_change = False
                end_of_file = False
                while index < end_index:
                    current = lines[index]
                    if current == "*** End of File":
                        end_of_file = True
                        index += 1
                        while index < end_index and not lines[index].strip():
                            index += 1
                        break
                    if current == "@@" or current.startswith("@@ ") or _is_file_header(current):
                        break
                    if not current or current[0] not in {" ", "+", "-"}:
                        raise PatchError(
                            "invalid_hunk",
                            "Hunk lines must start with a space, '+', or '-'.",
                            line=index + 1,
                            path=path,
                        )
                    prefix, value = current[0], current[1:]
                    if prefix in {" ", "-"}:
                        old_lines.append(value)
                    if prefix in {" ", "+"}:
                        new_lines.append(value)
                    if prefix in {"+", "-"}:
                        has_change = True
                    index += 1
                if not has_change:
                    raise PatchError(
                        "invalid_hunk",
                        "Update hunks must add or remove at least one line.",
                        line=chunk_line,
                        path=path,
                    )
                if not old_lines and not end_of_file:
                    raise PatchError(
                        "ambiguous_hunk",
                        "Pure additions must include '*** End of File'.",
                        line=chunk_line,
                        path=path,
                    )
                operation.chunks.append(
                    PatchChunk(
                        context=context,
                        old_lines=old_lines,
                        new_lines=new_lines,
                        end_of_file=end_of_file,
                        line=chunk_line,
                    )
                )
            if not operation.move_path and not operation.chunks:
                raise PatchError(
                    "invalid_hunk",
                    "Update File requires at least one hunk or a Move to destination.",
                    line=line_number,
                    path=path,
                )
            operations.append(operation)
        else:
            raise PatchError(
                "invalid_patch",
                "Expected Add File, Delete File, or Update File header.",
                line=line_number,
            )
        if len(operations) > MAX_PATCH_FILES:
            raise PatchError(
                "too_many_files",
                f"A patch may modify at most {MAX_PATCH_FILES} files.",
            )
    if not operations:
        raise PatchError("empty_patch", "Patch does not contain any file operations.")
    return operations


def _normalize_source_text(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    has_final_newline = normalized.endswith("\n")
    lines = normalized.split("\n")
    if has_final_newline:
        lines.pop()
    if lines == [""] and not normalized:
        lines = []
    return lines, has_final_newline


def _find_unique_sequence(lines, pattern, start, *, end_of_file, path, line):
    if not pattern:
        return len(lines)
    if len(pattern) > len(lines):
        raise PatchError(
            "context_not_found",
            "Expected hunk lines were not found in the file.",
            line=line,
            path=path,
        )
    candidates = []
    last = len(lines) - len(pattern)
    for index in range(max(start, 0), last + 1):
        if end_of_file and index != last:
            continue
        if lines[index : index + len(pattern)] == pattern:
            candidates.append(index)
    if not candidates:
        raise PatchError(
            "context_not_found",
            "Expected hunk lines were not found exactly in the file.",
            line=line,
            path=path,
        )
    if len(candidates) > 1:
        raise PatchError(
            "ambiguous_hunk",
            "Expected hunk lines occur more than once. Add more exact context.",
            line=line,
            path=path,
        )
    return candidates[0]


def _apply_chunks(text, chunks, path):
    lines, has_final_newline = _normalize_source_text(text)
    cursor = 0
    for chunk in chunks:
        if chunk.context is not None:
            context_matches = [
                index
                for index in range(cursor, len(lines))
                if lines[index] == chunk.context
            ]
            if not context_matches:
                raise PatchError(
                    "context_not_found",
                    f"Hunk context '{chunk.context}' was not found exactly.",
                    line=chunk.line,
                    path=path,
                )
            if len(context_matches) > 1:
                raise PatchError(
                    "ambiguous_hunk",
                    f"Hunk context '{chunk.context}' occurs more than once.",
                    line=chunk.line,
                    path=path,
                )
            cursor = context_matches[0] + 1
        start = _find_unique_sequence(
            lines,
            chunk.old_lines,
            cursor,
            end_of_file=chunk.end_of_file,
            path=path,
            line=chunk.line,
        )
        lines[start : start + len(chunk.old_lines)] = chunk.new_lines
        cursor = start + len(chunk.new_lines)
    result = "\n".join(lines)
    if has_final_newline:
        result += "\n"
    return result


def _structured_document_path(path):
    return os.path.splitext(str(path or ""))[1].lower() in STRUCTURED_DOCUMENT_EXTENSIONS


def _patch_error_payload(action, exc, *, applied_changes=None, pending_changes=None):
    extra = {}
    if exc.code == "partial_apply":
        extra["status"] = "partial_apply"
    if exc.line is not None:
        extra["line"] = exc.line
    if applied_changes is not None:
        extra["applied_changes"] = list(applied_changes)
    if pending_changes is not None:
        extra["pending_changes"] = list(pending_changes)
    return _build_error(action, exc.code, exc.message, path=exc.path, extra=extra)


def _emit_diagnostic(context, status, started_at, **fields):
    observability = context.get("observability_signal") if isinstance(context, dict) else None
    if not hasattr(observability, "emit"):
        return
    payload = {
        "type": "apply_patch",
        "status": status,
        "duration_seconds": round(max(time.time() - started_at, 0.0), 3),
        "timestamp": time.time(),
    }
    payload.update({key: value for key, value in fields.items() if value is not None})
    observability.emit(payload)


def _prepare_patch(workspace_dir, operations, context, action):
    prepared = []
    claimed_paths = set()
    delete_paths = []
    for operation in operations:
        if _structured_document_path(operation.path) or (
            operation.move_path and _structured_document_path(operation.move_path)
        ):
            raise PatchError(
                "structured_document_not_supported",
                "apply_patch only handles plain text files, not Office or PDF documents.",
                line=operation.line,
                path=operation.path,
            )
        abs_path, rel_path, error = resolve_path(
            workspace_dir,
            operation.path,
            context=context,
            action=action,
            must_exist=operation.kind != "add",
            reject_glob_for_write=True,
            for_write=True,
        )
        if error:
            err = error.get("error") or {}
            raise PatchError(
                err.get("code") or "path_invalid",
                err.get("message") or "Patch path is invalid.",
                line=operation.line,
                path=error.get("path") or operation.path,
            )
        path_key = _cache_key(abs_path)
        if path_key in claimed_paths:
            raise PatchError(
                "duplicate_path",
                "A patch path may only appear in one file operation.",
                line=operation.line,
                path=rel_path,
            )
        claimed_paths.add(path_key)

        if operation.kind == "add":
            if os.path.lexists(abs_path):
                raise PatchError(
                    "destination_exists",
                    "Add File refuses to overwrite an existing path.",
                    line=operation.line,
                    path=rel_path,
                )
            try:
                data = operation.contents.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise PatchError(
                    "text_encode_failed",
                    f"Added file content cannot be encoded as UTF-8: {exc}",
                    line=operation.line,
                    path=rel_path,
                ) from exc
            if len(data) > MAX_TEXT_FILE_BYTES:
                raise PatchError(
                    "file_too_large",
                    "Added file exceeds the 10 MiB plain-text limit.",
                    line=operation.line,
                    path=rel_path,
                )
            prepared.append(
                PreparedChange(
                    kind="add",
                    path=operation.path,
                    abs_path=abs_path,
                    rel_path=rel_path,
                    data=data,
                    encoding="utf-8",
                    newline="\n",
                )
            )
            continue

        if not os.path.isfile(abs_path):
            raise PatchError(
                "not_a_file",
                "Patch operations only support regular files.",
                line=operation.line,
                path=rel_path,
            )
        try:
            raw = _read_file_bytes(abs_path)
        except OSError as exc:
            raise PatchError(
                "read_failed",
                str(exc),
                line=operation.line,
                path=rel_path,
            ) from exc
        expected_sha256 = _sha256_bytes(raw)

        if operation.kind == "delete":
            delete_paths.append(rel_path)
            prepared.append(
                PreparedChange(
                    kind="delete",
                    path=operation.path,
                    abs_path=abs_path,
                    rel_path=rel_path,
                    expected_sha256=expected_sha256,
                )
            )
            continue

        abs_move_path = None
        rel_move_path = None
        if operation.move_path:
            abs_move_path, rel_move_path, error = resolve_path(
                workspace_dir,
                operation.move_path,
                context=context,
                action=action,
                must_exist=False,
                reject_glob_for_write=True,
                for_write=True,
            )
            if error:
                err = error.get("error") or {}
                raise PatchError(
                    err.get("code") or "path_invalid",
                    err.get("message") or "Move destination is invalid.",
                    line=operation.line,
                    path=error.get("path") or operation.move_path,
                )
            move_key = _cache_key(abs_move_path)
            if move_key in claimed_paths:
                raise PatchError(
                    "duplicate_path",
                    "Move destinations must not overlap another patch path.",
                    line=operation.line,
                    path=rel_move_path,
                )
            if os.path.lexists(abs_move_path):
                raise PatchError(
                    "destination_exists",
                    "Move to refuses to overwrite an existing path.",
                    line=operation.line,
                    path=rel_move_path,
                )
            claimed_paths.add(move_key)

        if operation.chunks:
            read_state, error = get_verified_read_state(abs_path, rel_path, context, action)
            if error:
                err = error.get("error") or {}
                raise PatchError(
                    err.get("code") or "read_required",
                    err.get("message") or "File must be fully read before modification.",
                    line=operation.line,
                    path=rel_path,
                )
            encoding = read_state.get("encoding") or "utf-8"
            bom = bytes.fromhex(str(read_state.get("bom_hex") or ""))
            newline = read_state.get("newline") or "\n"
            try:
                original, _encoding, _bom, _newline = decode_text_bytes(
                    raw,
                    selected_encoding=encoding,
                )
                updated = _apply_chunks(original, operation.chunks, rel_path)
                data = encode_text_bytes(updated, encoding, bom=bom, newline=newline)
            except PatchError:
                raise
            except Exception as exc:
                code = getattr(exc, "code", "text_update_failed")
                message = getattr(exc, "message", str(exc))
                raise PatchError(code, message, line=operation.line, path=rel_path) from exc
            if len(data) > MAX_TEXT_FILE_BYTES:
                raise PatchError(
                    "file_too_large",
                    "Updated file exceeds the 10 MiB plain-text limit.",
                    line=operation.line,
                    path=rel_path,
                )
        else:
            encoding = ""
            bom = b""
            newline = "\n"
            data = raw

        prepared.append(
            PreparedChange(
                kind="move" if operation.move_path else "update",
                path=operation.path,
                abs_path=abs_path,
                rel_path=rel_path,
                expected_sha256=expected_sha256,
                data=data,
                encoding=encoding,
                bom=bom,
                newline=newline,
                move_path=operation.move_path,
                abs_move_path=abs_move_path,
                rel_move_path=rel_move_path,
                has_content_update=bool(operation.chunks),
            )
        )
    return prepared, delete_paths


def _assert_expected_file(change):
    if not os.path.isfile(change.abs_path):
        raise PatchError(
            "stale_write",
            "Source file disappeared after preflight.",
            path=change.rel_path,
        )
    if _sha256_bytes(_read_file_bytes(change.abs_path)) != change.expected_sha256:
        raise PatchError(
            "stale_write",
            "Source file changed after preflight. Read it again before modifying.",
            path=change.rel_path,
        )


def _assert_commit_path(workspace_dir, path, expected_abs_path, context, action, *, must_exist):
    abs_path, rel_path, error = resolve_path(
        workspace_dir,
        path,
        context=context,
        action=action,
        must_exist=must_exist,
        reject_glob_for_write=True,
        for_write=True,
    )
    if error:
        err = error.get("error") or {}
        raise PatchError(
            err.get("code") or "path_invalid",
            err.get("message") or "Patch path became invalid before commit.",
            path=error.get("path") or path,
        )
    if _cache_key(abs_path) != _cache_key(expected_abs_path):
        raise PatchError(
            "path_changed",
            "Patch path resolved to a different location before commit.",
            path=rel_path,
        )


def _mark_moved_file(change, context):
    state = _get_file_state(context)
    reads = state.get("reads") if isinstance(state, dict) else None
    previous = reads.pop(_cache_key(change.abs_path), None) if isinstance(reads, dict) else None
    if previous and change.abs_move_path:
        reads[_cache_key(change.abs_move_path)] = previous


def _change_summary(change, *, change_type=None, path=None):
    effective_type = change_type or change.kind
    payload = {
        "change_type": effective_type,
        "path": path or change.rel_path,
    }
    if change.rel_move_path and effective_type == "move":
        payload["from_path"] = change.rel_path
        payload["to_path"] = change.rel_move_path
        payload["path"] = change.rel_move_path
    if change.data is not None and (
        change.kind in {"add", "update"}
        or (change.kind == "move" and change.has_content_update)
    ):
        payload["bytes_written"] = len(change.data)
    return payload


def apply_patch(workspace_dir, patch, *, context=None, confirm_delete=None, action="apply_patch"):
    context = context if isinstance(context, dict) else {}
    started_at = time.time()
    _emit_diagnostic(context, "start", started_at)
    try:
        operations = parse_patch(patch)
        prepared, delete_paths = _prepare_patch(workspace_dir, operations, context, action)
        _emit_diagnostic(
            context,
            "preflight",
            started_at,
            file_count=len(prepared),
            delete_count=len(delete_paths),
        )
    except PatchError as exc:
        _emit_diagnostic(context, "error", started_at, error_code=exc.code)
        return _patch_error_payload(action, exc)
    except Exception as exc:
        _emit_diagnostic(context, "error", started_at, error_code="preflight_failed")
        return _build_error(action, "preflight_failed", str(exc))

    if delete_paths:
        try:
            confirmed = bool(confirm_delete(delete_paths)) if callable(confirm_delete) else False
        except Exception as exc:
            _emit_diagnostic(context, "error", started_at, error_code="confirmation_failed")
            return _build_error(
                action,
                "confirmation_failed",
                str(exc),
                extra={"requires_confirmation": True, "delete_paths": delete_paths},
            )
        _emit_diagnostic(
            context,
            "confirm",
            started_at,
            delete_count=len(delete_paths),
            approved=confirmed,
        )
        if not confirmed:
            _emit_diagnostic(context, "error", started_at, error_code="cancelled")
            return _build_error(
                action,
                "cancelled",
                "Patch deletion was cancelled by the user.",
                extra={"requires_confirmation": True, "delete_paths": delete_paths},
            )

    applied = []
    _emit_diagnostic(context, "commit", started_at, file_count=len(prepared))
    for index, change in enumerate(prepared):
        failure_phase = "commit"
        try:
            _assert_commit_path(
                workspace_dir,
                change.path,
                change.abs_path,
                context,
                action,
                must_exist=change.kind != "add",
            )
            if change.abs_move_path and change.move_path:
                _assert_commit_path(
                    workspace_dir,
                    change.move_path,
                    change.abs_move_path,
                    context,
                    action,
                    must_exist=False,
                )
            if change.kind == "add":
                if os.path.lexists(change.abs_path):
                    raise PatchError(
                        "destination_exists",
                        "Add destination appeared after preflight.",
                        path=change.rel_path,
                    )
                _atomic_write_bytes(change.abs_path, change.data or b"", existed_before=False)
                applied.append(_change_summary(change))
                failure_phase = "audit_state"
                mark_file_written(
                    change.abs_path,
                    context,
                    data=change.data or b"",
                    encoding="utf-8",
                    newline="\n",
                )
            elif change.kind == "update":
                _assert_expected_file(change)
                _atomic_write_bytes(
                    change.abs_path,
                    change.data or b"",
                    existed_before=True,
                    expected_sha256=change.expected_sha256,
                )
                applied.append(_change_summary(change))
                failure_phase = "audit_state"
                mark_file_written(
                    change.abs_path,
                    context,
                    data=change.data or b"",
                    encoding=change.encoding,
                    bom=change.bom,
                    newline=change.newline,
                )
            elif change.kind == "delete":
                _assert_expected_file(change)
                os.remove(change.abs_path)
                clear_read_state(change.abs_path, context)
                applied.append(_change_summary(change))
            elif change.kind == "move":
                _assert_expected_file(change)
                if not change.abs_move_path or not change.rel_move_path:
                    raise PatchError("move_failed", "Move destination is missing.", path=change.rel_path)
                if os.path.lexists(change.abs_move_path):
                    raise PatchError(
                        "destination_exists",
                        "Move destination appeared after preflight.",
                        path=change.rel_move_path,
                    )
                parent = os.path.dirname(change.abs_move_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                if change.has_content_update:
                    _atomic_write_bytes(
                        change.abs_move_path,
                        change.data or b"",
                        existed_before=False,
                    )
                    try:
                        _assert_expected_file(change)
                        os.remove(change.abs_path)
                    except Exception:
                        applied.append(
                            _change_summary(
                                change,
                                change_type="add",
                                path=change.rel_move_path,
                            )
                        )
                        raise
                    clear_read_state(change.abs_path, context)
                    applied.append(_change_summary(change))
                    failure_phase = "audit_state"
                    mark_file_written(
                        change.abs_move_path,
                        context,
                        data=change.data or b"",
                        encoding=change.encoding,
                        bom=change.bom,
                        newline=change.newline,
                    )
                else:
                    os.rename(change.abs_path, change.abs_move_path)
                    _mark_moved_file(change, context)
                    applied.append(_change_summary(change))
        except PatchError as exc:
            pending = [_change_summary(item) for item in prepared[index + 1 :]]
            _emit_diagnostic(context, "error", started_at, error_code=exc.code)
            payload = _patch_error_payload(
                action,
                PatchError(
                    "partial_apply",
                    exc.message,
                    path=exc.path or change.rel_path,
                ),
                applied_changes=applied,
                pending_changes=pending,
            )
            failed_change = _change_summary(change)
            failed_change["failure_phase"] = failure_phase
            payload["failed_change"] = failed_change
            return payload
        except Exception as exc:
            pending = [_change_summary(item) for item in prepared[index + 1 :]]
            _emit_diagnostic(context, "error", started_at, error_code="partial_apply")
            failed_change = _change_summary(change)
            failed_change["failure_phase"] = failure_phase
            payload = _build_error(
                action,
                "partial_apply",
                str(exc),
                path=change.rel_path,
                extra={
                    "status": "partial_apply",
                    "applied_changes": applied,
                    "failed_change": failed_change,
                    "pending_changes": pending,
                },
            )
            return payload

    _emit_diagnostic(context, "finish", started_at, file_count=len(applied))
    counts = {
        kind: sum(1 for item in applied if item.get("change_type") == kind)
        for kind in ("add", "update", "move", "delete")
    }
    return _build_ok(
        action,
        {
            "changes": applied,
            "counts": counts,
            "requires_confirmation": bool(delete_paths),
        },
    )
