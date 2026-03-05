---
name: file-system
description: Provides capabilities to list, read, write and manipulate files in the user's workspace, including Office documents (DOCX, PPTX, XLSX, PDF).
description_cn: 提供在用户工作区中列出、读取、写入和操作文件的能力，包括常用的办公软件文件（DOCX, PPTX, XLSX, PDF）。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.1"
security_level: high
allowed-tools: ["list_files", "list_file", "read_file", "rename_file", "delete_file", "read_docx", "write_docx", "read_pptx", "create_pptx", "read_excel", "write_excel", "read_pdf"]
---

# File System Skill

This skill allows the agent to interact with the local file system within the allowed workspace.
It handles both standard file operations and Office document processing.

## Capabilities

### General File Operations
1. **List Files**: Explore the directory structure.
2. **Read Files**: Read content of files. Automatically detects and reads text, DOCX, PPTX, XLSX, and PDF files.
3. **Rename Files**: Rename or move files.
4. **Delete Files**: Delete files or empty directories (requires confirmation).

### Office Suite Operations
1. **Word (DOCX)**: Read text from documents and create/write new documents.
2. **PowerPoint (PPTX)**: Read text from slides and create new presentations.
3. **Excel (XLSX)**: Read data from sheets and write data to new or existing sheets.
4. **PDF**: Read text from PDF files.

## Usage Guidelines
- **Safety First**: Always check if a file exists using `list_files` before trying to read it.
- **Sandboxed**: You can only access files within the user-selected workspace (unless God Mode is active).
- **Pathing**: Use relative paths (e.g., `data.csv` or `subdir/config.json`).
- **Dependencies**: Office operations require `python-docx`, `python-pptx`, `openpyxl`, `pypdf`.
