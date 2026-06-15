---
name: file-system
description: Provides plain-text file and workspace path operations. It intentionally does not parse Office or PDF documents.
description_cn: 提供普通文本文件与工作区路径操作；不解析 Office 或 PDF 文档。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.2"
security_level: high
allowed-tools: ["workspace_list_files", "text_file_read", "text_file_write", "text_file_update", "workspace_rename_path", "workspace_delete_path"]
---

# File System Skill

This built-in skill handles workspace path operations and plain text files only.

## Capabilities

1. **List Paths**: Explore workspace files and directories.
2. **Read Plain Text**: Read `.txt`, `.md`, `.json`, source code, logs, and similar text files.
3. **Write Plain Text**: Create or overwrite plain text files.
4. **Update Plain Text**: Replace exact text in plain text files.
5. **Rename Paths**: Rename or move files and directories without parsing contents.
6. **Delete Paths**: Delete files or directories after confirmation.

## Boundaries

- `text_file_read`, `text_file_write`, and `text_file_update` refuse DOCX, PPTX, XLSX, XLS, and PDF paths.
- To read Office/PDF documents, enable the optional `document-reader` plugin and use `document_read`.
- To create or modify Office/PDF documents, use `run_python_code` with the appropriate document library.
- Path search is owned by `command-tools` through `glob` and `grep`.
- User confirmation is owned by `interaction`; delete confirmation uses the shared interaction bridge internally.

## Usage Guidelines

- Prefer relative workspace paths.
- Read a file fully before modifying it when the existing content matters.
- Multiple independent plain-text reads can be grouped through `parallel_tools`.
- Writes, updates, renames, and deletes must stay as direct single-tool calls.
