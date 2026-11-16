---
name: documents
description: Read Office/PDF in bounded ranges; create, edit and verify Word documents, preserving supplied templates and formatting. Use presentations for PPTX authoring and data-analysis for quantitative conclusions.
description_cn: 按需读取 Office/PDF，创建、编辑、套用模板并检查 Word 文档。
type: builtin
source_type: builtin
default_enabled: true
metadata:
  author: deepseek-cowork team
  version: "2.0"
allowed-tools: [document_read, document_render, run_python_code, view_image, review_draft]
---

# 文档处理

只问内容就读取回答，要求创建或修改才写文件。输入格式不决定交付格式。PPTX 制作使用 presentations，数据口径与计算使用 data-analysis。本技能不提供旧 document-reader 的别名或配置迁移。

## 读取

- Office/PDF 使用 document_read，纯文本使用 text_file_read。
- max_chars 限制完整 JSON（含元数据及 sections），默认 12000，范围 2000–60000。
- 查看 has_more、range，需要时将 next_cursor 作为 cursor 续读，保持同一路径与选择范围；文件变化后重新开始。不自动加载所有批次。
- Excel 优先指定 sheet_name、cell_range；否则按工作簿顺序读取。sheet_count 是总表数，sheet_names 只列本批工作表。公式值是缓存值，不能据此声称已经重算。
- PDF 可用 pages="2-4"。段落、单元格或页面可能跨批；重建文本直接拼接 content，不插入分隔符。扫描 PDF 的空文本不代表页面没有内容，可以渲染查看，但不承诺 OCR。
- 文本提取不能证明排版正确；模板、图形与布局判断需要页面检查。

## 创建与修改 Word

明确用途、读者与内容结构，信息足够直接推进。创建、编辑或套模板时按需读取 [Word 指导](references/word.md)，完整路径见 References。

- 用 run_python_code 和随包 python-docx。不引入 Pandoc 或其他宿主专用工具。
- 默认 A4、实际可用的中文字体，用户模板与明确要求优先。使用真实标题样式、段落、编号、表格与图片，不用空格排版或整页图片替代可编辑内容。
- 修改前读取目标文件，保留原件并另存，除非用户要求原位修改。检查工作区未保存编辑，保持现有样式、编号与结构，不用全文提取后重建冒充格式保留。
- 批注、修订、复杂域、公式、宏及嵌入对象需要针对性实现与验证，不能未经验证承诺无损，也不能用图片或普通文字冒充原生对象。

## 验证与交付

1. 重新打开生成文件，检查所需正文、表格、图片和结构。
2. document_render(path=...) 渲染 DOCX/PPTX/PDF，每批最多 4 页，返回 page_count、source_version、图片路径与 next_pages。
3. 用 view_image 检查页面。续批携带原 source_version；源文件变化后重新开始。新文档检查全部页，局部修改检查受影响页与相邻分页，整体重排重新完整检查。
4. 检查缺字、裁切、遮挡、表格跨页、留白与页眉页脚。修复后检查最新文件，不能沿用旧截图的结论。

Office 需要可选 LibreOffice 组件，PDF 使用随包 PDFium。渲染失败保留成果并报告原因，通过现有工作区预览入口继续。模型不能看图时说明尚未视觉检查。文件生成、结构检查与视觉检查分别报告，不声称在未打开的 Word/PowerPoint 中验证过。

普通任务交付用户需要的文件，不附带预览缓存或额外 PDF。已有“办公稿”草稿上下文时调用 review_draft(kind="office", file_path=..., draft_id=...)，由现有审阅界面处理保存；不为普通任务额外创建审批。
