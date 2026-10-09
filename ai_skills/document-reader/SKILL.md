---
name: document-reader
description: Bundled document reader for structured documents through one unified tool.
description_cn: 随应用提供的文档读取能力：通过统一工具读取 DOCX、PPTX、XLSX、XLS 和 PDF 文档。
license: Apache-2.0
type: bundled_plugin
source_type: bundled_plugin
default_enabled: true
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: medium
allowed-tools: [document_read]
---

# Document Reader

This bundled capability is enabled by default and reads structured document formats that plain text file tools intentionally refuse.

## Tool

### document_read

Reads DOCX, PPTX, XLSX, XLS, or PDF files from the current workspace and returns a unified structured payload with extracted text.

- The first call returns a bounded batch. `max_chars` limits the entire JSON result (including `sections` and metadata), defaults to 12000, and accepts 2000–60000.
- Check `has_more` and `range`. Continue only as needed by passing `next_cursor` as `cursor` with the same `path`; omit selectors or keep them identical. A changed file requires a fresh read.
- For Excel, prefer `sheet_name` and `cell_range` (for example `A1:D100`); without a sheet selector, batches traverse sheets in workbook order. `metadata.sheet_count` is the workbook total, while `sheet_names` lists only sheets included in the current batch.
- PDF supports `pages`, for example `2-4`. Long pages, paragraphs and cells may span batches; concatenate `content` without adding separators to reconstruct the selected text.
- Do not automatically load every batch. Read the ranges needed for the user's task, and keep findings concise before continuing.

## Boundaries

- Use `text_file_read` only for plain text files.
- Use `document_read` for Office/PDF reads. If the user explicitly disabled this capability, ask them to enable it in the capability store.
- There is no Office/PDF write tool. When the user asks to create or modify Office/PDF files, use `run_python_code` with an appropriate library and normal workspace write safety rules.
- This plugin does not use Pandoc.
