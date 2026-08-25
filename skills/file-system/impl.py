import os

from core.apply_patch import apply_patch as apply_text_patch
from core.filesystem_ops import (
    _build_error,
    delete_path,
    list_files as list_files_core,
    read_text_file,
    rename_path,
)
from core.interaction import ask_user


STRUCTURED_DOCUMENT_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xls", ".pdf"}


def _structured_document_error(action, path):
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext not in STRUCTURED_DOCUMENT_EXTENSIONS:
        return None
    return _build_error(
        action,
        "structured_document_not_supported",
        (
            "This tool only handles plain text files and workspace paths. "
            "Use the bundled document-reader capability and document_read to read "
            "DOCX, PPTX, XLSX, XLS, or PDF files. To create or modify those formats, "
            "use run_python_code with an appropriate document library."
        ),
        path=path,
    )


def _confirm_delete(rel_path, recursive_flag, context):
    prompt = f"Confirm delete recursively: '{rel_path}'?" if recursive_flag else f"Confirm delete: '{rel_path}'?"
    return bool(ask_user(prompt, _context=context, title="请确认", timeout_seconds=120))


def _confirm_patch_deletions(paths, context):
    normalized = [str(path or "").strip() for path in paths or [] if str(path or "").strip()]
    lines = "\n".join(f"- {path}" for path in normalized)
    prompt = f"补丁将删除以下 {len(normalized)} 个文件：\n{lines}\n\n是否继续应用整个补丁？"
    return bool(ask_user(prompt, _context=context, title="确认应用补丁", timeout_seconds=120))


def workspace_list_files(workspace_dir, path=".", recursive=False, include_hidden=False, limit=200, _context=None):
    """List files and directories in the workspace without reading file contents."""
    return list_files_core(
        workspace_dir,
        path=path,
        recursive=recursive,
        include_hidden=include_hidden,
        limit=limit,
        context=_context,
    )


def text_file_read(workspace_dir, path, offset=1, limit=None, encoding=None, _context=None):
    """Read a plain text file. Does not parse DOCX, PPTX, XLSX, XLS, or PDF documents."""
    action = "text_file_read"
    error = _structured_document_error(action, path)
    if error:
        return error
    return read_text_file(
        workspace_dir,
        path,
        offset=offset,
        limit=limit,
        encoding=encoding,
        context=_context,
        action=action,
    )


def apply_patch(workspace_dir, patch, _context=None):
    """Apply a structured plain-text patch inside the current workspace."""
    return apply_text_patch(
        workspace_dir,
        patch,
        context=_context,
        confirm_delete=lambda paths: _confirm_patch_deletions(paths, _context),
        action="apply_patch",
    )


def workspace_rename_path(workspace_dir, old_path, new_path, _context=None):
    """Rename or move a workspace file or directory without parsing its contents."""
    return rename_path(
        workspace_dir,
        old_path,
        new_path,
        context=_context,
        action="workspace_rename_path",
    )


def workspace_delete_path(workspace_dir, path, recursive=False, _context=None):
    """Delete a workspace file or directory after user confirmation."""
    return delete_path(
        workspace_dir,
        path,
        recursive=recursive,
        confirm_callback=lambda rel_path, recursive_flag: _confirm_delete(rel_path, recursive_flag, _context),
        context=_context,
        action="workspace_delete_path",
    )


TOOL_EXPORTS = [
    {
        "name": "workspace_list_files",
        "handler": workspace_list_files,
        "description": "List files and directories in the workspace. This does not read or parse file contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative directory path."},
                "recursive": {"type": "boolean", "description": "Whether to list recursively."},
                "include_hidden": {"type": "boolean", "description": "Whether to include hidden files."},
                "limit": {"type": "integer", "description": "Maximum number of entries to return."},
            },
            "required": [],
        },
        "read_only": True,
        "search_hint": "workspace list files directories path",
    },
    {
        "name": "text_file_read",
        "handler": text_file_read,
        "description": (
            "Strictly read up to 10 MiB of a plain-text file. A full read from offset 1 with no limit "
            "establishes the SHA-256 write audit required by apply_patch. Refuses DOCX, PPTX, XLSX, XLS, and PDF."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative plain text file path."},
                "offset": {"type": "integer", "description": "1-based line offset."},
                "limit": {"type": "integer", "description": "Maximum number of lines to return."},
                "encoding": {
                    "type": "string",
                    "description": "Optional explicit text encoding for files without a Unicode BOM or valid UTF-8.",
                },
            },
            "required": ["path"],
        },
        "read_only": True,
        "search_hint": "plain text file read",
    },
    {
        "name": "apply_patch",
        "handler": apply_patch,
        "description": (
            "Create, update, move, or delete plain-text files with an exact structured patch. "
            "Existing files with content changes must be fully read with text_file_read first. "
            "The patch may contain at most 100 files and 12 MiB of UTF-8 input. Delete operations require user confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": (
                        "Patch text enclosed by *** Begin Patch and *** End Patch, containing "
                        "Add File, Update File, Delete File, optional Move to, and @@ hunks."
                    ),
                },
            },
            "required": ["patch"],
        },
        "destructive": True,
        "requires_user_interaction": True,
        "search_hint": "apply patch plain text file edit create update move delete",
    },
    {
        "name": "workspace_rename_path",
        "handler": workspace_rename_path,
        "description": "Rename or move a workspace file or directory without parsing file contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "old_path": {"type": "string", "description": "Existing workspace-relative path."},
                "new_path": {"type": "string", "description": "New workspace-relative path."},
            },
            "required": ["old_path", "new_path"],
        },
        "destructive": True,
        "search_hint": "workspace rename move path",
    },
    {
        "name": "workspace_delete_path",
        "handler": workspace_delete_path,
        "description": "Delete a workspace file or directory after user confirmation.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path to delete."},
                "recursive": {"type": "boolean", "description": "Whether to delete directories recursively."},
            },
            "required": ["path"],
        },
        "destructive": True,
        "requires_user_interaction": True,
        "search_hint": "workspace delete remove path",
    },
]
