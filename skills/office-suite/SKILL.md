---
name: office-suite
description: Provides capabilities to read and write common office documents (DOCX, PPTX, XLSX, PDF).
description_cn: 提供读取和写入常用办公软件文件（DOCX, PPTX, XLSX, PDF）的能力。
license: Apache-2.0
metadata:
  author: cowork-team
  version: "1.0"
security_level: high
allowed-tools: read_docx write_docx read_pptx create_pptx read_excel write_excel read_pdf
---

# Office Suite Skill

This skill allows the agent to interact with Office documents within the user's workspace.

## Capabilities
1. **Word (DOCX)**: Read text from documents and create/write new documents.
2. **PowerPoint (PPTX)**: Read text from slides and create new presentations with slides.
3. **Excel (XLSX)**: Read data from sheets and write data to new or existing sheets.
4. **PDF**: Read text from PDF files.

## Usage Guidelines
- **Pathing**: All paths must be relative to the workspace directory.
- **Data Safety**: When writing files, be careful not to overwrite important data unless intended.
- **Dependencies**: Requires `python-docx`, `python-pptx`, `openpyxl`, `pypdf`, `pandas`.
