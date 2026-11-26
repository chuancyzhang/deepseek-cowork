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
allowed-tools: [document_read, document_render, document_copy, run_python_code, view_image, review_draft]
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
- 修改前读取目标文件，用 document_copy 创建任务副本，后续脚本只修改返回的副本路径。此默认流程不是操作系统写入隔离，不承诺通用脚本无法改原件。检查工作区未保存编辑，保持现有样式、编号与结构，不用全文提取后重建冒充格式保留。不提供一键替换原件。
- 批注、修订、复杂域、公式、宏及嵌入对象需要针对性实现与验证，不能未经验证承诺无损，也不能用图片或普通文字冒充原生对象。

## 验证与交付

1. 保存后打开成品一次，核对任务要求的正文、表格、图片和结构。
2. 需要验版式时用 document_render(path=..., review=true, constraints=...) 检查改动页和复杂页。每批细节最多 4 页，可用 pages="1,8,16,24"，未指定时自动选页。按任务要求扩大范围或使用 review_kind="overview" 查看跨页风格。总览不计入逐页检查。
3. constraints 保留用户要求的版式和例外，续批沿用相同约束和 renderer。文件版本由宿主管理。相同快照的已有检查可复用，改稿后复查受影响页。每轮审稿共享页次、请求、时间、像素和用量预算，到达限制时说明实际检查范围与剩余问题。

Excel 读数据仍用 document_read／表格视图；需要按版式读宽表时指定 excel_options.mode="region"，可限定 sheet、range，显式指定 identity_columns（如 A:B）和 header_rows（如 1:2）。区域阅读只调整副本；打印效果检查使用 mode="print" 保留原分页。过宽合并列组不自动切断，按返回原因缩小范围或改用表格视图。

Office 使用本机 Microsoft Office／WPS 或显式选择的 LibreOffice，PDF 使用随包 PDFium。渲染失败时交付可打开的成品，说明版式检查的具体失败原因和范围。用户可在工作区或本机应用继续查看。超时只停止等待，旧任务退出前不重复启动同一实例。review=false 仅生成图片。独立审图把实际结论和覆盖返回主对话，原图留在检查记录。

普通任务交付用户需要的文件，不附带预览缓存或额外 PDF。已有“办公稿”草稿上下文时调用 review_draft(kind="office", file_path=..., draft_id=...)，由现有审阅界面处理保存；不为普通任务额外创建审批。
