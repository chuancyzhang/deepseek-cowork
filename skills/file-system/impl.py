import os

from core.filesystem_ops import (
    _build_error,
    delete_path,
    list_files as list_files_core,
    read_text_file,
    rename_path,
    update_text_file,
    write_text_file,
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
            "Enable the optional document-reader plugin and use document_read to read "
            "DOCX, PPTX, XLSX, XLS, or PDF files. To create or modify those formats, "
            "use run_python_code with an appropriate document library."
        ),
        path=path,
    )


def _confirm_delete(rel_path, recursive_flag, context):
    prompt = f"Confirm delete recursively: '{rel_path}'?" if recursive_flag else f"Confirm delete: '{rel_path}'?"
    return bool(ask_user(prompt, _context=context, title="请确认", timeout_seconds=120))


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


def text_file_read(workspace_dir, path, offset=1, limit=None, _context=None):
    """Read a plain text file. Does not parse DOCX, PPTX, XLSX, XLS, or PDF documents."""
    action = "text_file_read"
    error = _structured_document_error(action, path)
    if error:
        return error
    return read_text_file(workspace_dir, path, offset=offset, limit=limit, context=_context, action=action)


def text_file_write(workspace_dir, path, content, mode="overwrite", _context=None):
    """Create or overwrite a plain text file. Does not create Office or PDF documents."""
    action = "text_file_write"
    error = _structured_document_error(action, path)
    if error:
        return error
    return write_text_file(
        workspace_dir,
        path,
        content,
        mode=mode,
        context=_context,
        action=action,
    )


def text_file_update(workspace_dir, path, old_string, new_string, replace_all=False, _context=None):
    """Replace text inside a plain text file. Does not edit Office or PDF documents."""
    action = "text_file_update"
    error = _structured_document_error(action, path)
    if error:
        return error
    return update_text_file(
        workspace_dir,
        path,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
        context=_context,
        action=action,
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
        "description": "Read a plain text file. Refuses DOCX, PPTX, XLSX, XLS, and PDF; use document_read for those formats.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative plain text file path."},
                "offset": {"type": "integer", "description": "1-based line offset."},
                "limit": {"type": "integer", "description": "Maximum number of lines to return."},
            },
            "required": ["path"],
        },
        "read_only": True,
        "search_hint": "plain text file read",
    },
    {
        "name": "text_file_write",
        "handler": text_file_write,
        "description": "Create or overwrite a plain text file. Refuses Office and PDF document formats.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative plain text file path."},
                "content": {"type": "string", "description": "Text content to write."},
                "mode": {"type": "string", "description": "Write mode, usually overwrite or append."},
            },
            "required": ["path", "content"],
        },
        "destructive": True,
        "search_hint": "plain text file write create",
    },
    {
        "name": "text_file_update",
        "handler": text_file_update,
        "description": "Replace text in a plain text file. Refuses Office and PDF document formats.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative plain text file path."},
                "old_string": {"type": "string", "description": "Exact text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."},
                "replace_all": {"type": "boolean", "description": "Whether to replace all matches."},
            },
            "required": ["path", "old_string", "new_string"],
        },
        "destructive": True,
        "search_hint": "plain text file update edit replace",
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
