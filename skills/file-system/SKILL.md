---
name: file-system
description: Provides plain-text file and workspace path operations. It intentionally does not parse Office or PDF documents.
description_cn: 提供普通文本文件与工作区路径操作；不解析 Office 或 PDF 文档。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.3"
security_level: high
allowed-tools: ["workspace_list_files", "text_file_read", "apply_patch", "workspace_rename_path", "workspace_delete_path"]
---

# File System Skill

This built-in skill handles workspace path operations and plain text files only.

## Capabilities

1. **List Paths**: Explore workspace files and directories.
2. **Read Plain Text**: Strictly decode and page through `.txt`, `.md`, `.json`, source code, logs, and similar text files up to 10 MiB.
3. **Apply Text Patches**: Create, update, move, or delete up to 100 plain-text files with one exact patch.
4. **Rename Paths**: Rename or move files and directories without parsing contents.
5. **Delete Paths**: Delete files or directories after confirmation.

## Boundaries

- `text_file_read` and `apply_patch` refuse DOCX, PPTX, XLSX, XLS, and PDF paths.
- To read Office/PDF documents, use the bundled `document-reader` capability and `document_read`. If it was explicitly disabled, enable it in the capability store.
- To create or modify Office/PDF documents, use `run_python_code` with the appropriate document library.
- Path search is owned by `command-tools` through `glob` and `grep`.
- User confirmation is owned by `interaction`; delete confirmation uses the shared interaction bridge internally.
- Normal mode rejects paths outside the workspace, UNC paths, resolved path escapes, and write paths that traverse symbolic links or directory junctions. God Mode retains its existing outside-workspace authorization, but write paths still reject reparse-point traversal.

## Usage Guidelines

- Prefer relative workspace paths.
- Use `glob` to discover paths and `grep` to locate matching lines. Neither tool returns the complete ordered file or grants a write credential.
- Before changing existing content, call `text_file_read` with `offset=1` and no `limit`. A paged read never grants write audit even when it reaches the end of the file.
- Files with a recognized Unicode BOM or valid UTF-8 need no encoding argument. Other encodings must be explicit; decode failures are returned and never replaced or ignored.
- Use `apply_patch` for every plain-text creation or content change. The patch must start with `*** Begin Patch`, end with `*** End Patch`, and contain `*** Add File`, `*** Update File`, `*** Delete File`, optional `*** Move to`, and exact `@@` hunks. Pure EOF additions require `*** End of File`.
- Use enough unchanged context to make each hunk unique. Matching is exact: whitespace and Unicode variants are not normalized.
- All deletes in one patch are confirmed together after preflight. Rejection or timeout leaves the entire patch unapplied.
- Multiple independent plain-text reads can be grouped through `parallel_tools`.
- Patches, renames, and deletes must stay as direct single-tool calls.
