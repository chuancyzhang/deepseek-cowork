---
name: file-system
description: Provides unified file discovery, read, write, edit and search capabilities in the workspace, including Office documents (DOCX, PPTX, XLSX, PDF).
description_cn: 提供工作区内统一的文件发现、读取、写入、编辑与搜索能力，并支持常用办公文档（DOCX, PPTX, XLSX, PDF）。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.1"
security_level: high
allowed-tools: ["list_files", "read_file", "write_file", "update_file", "rename_file", "delete_file", "glob", "grep", "read_docx", "write_docx", "read_pptx", "create_pptx", "read_excel", "write_excel", "read_pdf"]
---

# File System Skill

This skill allows the agent to interact with the local file system within the allowed workspace.
It handles both standard file operations and Office document processing.

## Capabilities

### General File Operations
1. **List Files**: Explore directory structure with recursive and hidden-file controls.
2. **Read Files**: Read file content with optional range parameters. Automatically dispatches DOCX, PPTX, XLSX, and PDF.
3. **Write Files**: Create or overwrite files with structured write results.
4. **Update Files**: Replace text with strict matching rules.
5. **Rename Files**: Rename or move files and directories.
6. **Delete Files**: Delete files or directories (requires confirmation, optional recursive mode).
7. **Glob Search**: Path/name pattern search.
8. **Grep Search**: Content search with regex support.

### Office Suite Operations
1. **Word (DOCX)**: Read text from documents and create/write new documents.
2. **PowerPoint (PPTX)**: Read text from slides and create new presentations.
3. **Excel (XLSX)**: Read data from sheets and write data to new or existing sheets.
4. **PDF**: Read text from PDF files.

## Usage Guidelines
- **Unified JSON Output**: All tools return structured JSON strings.
- **Safety First**: Existing files must be fully read before `write_file`/`update_file` modifications.
- **Sandboxed**: Access is restricted to the selected workspace unless God Mode is enabled.
- **Pathing**: Prefer relative paths (e.g., `data.csv`, `subdir/config.json`).
- **Search Split**: Use `glob` for path matching and `grep` for content matching.
- **Glob Patterns**: The tool already searches recursively. For known filename fragments, prefer patterns like `*AI 赋能数据分析*` instead of `**/AI 赋能数据分析*`, because `**/name*` can miss files located in the workspace root.
- **Scoped Search**: If the target directory is known, use `path` to narrow the scope and keep `pattern` focused on the filename or relative path shape.
- **Read-Only Parallelism**: Multiple independent reads/searches can be grouped through `parallel_tools`; writes, updates, renames, and deletes must stay as direct single-tool calls.
- **Dependencies**: Office operations require `python-docx`, `python-pptx`, `openpyxl`, `pypdf`.

## Current Runtime Notes
- Session-level selected skills or agent profiles may narrow which file tools are visible.
- Clarifying mode permits read-oriented exploration only; modifying tools are hidden or denied until normal execution resumes.
