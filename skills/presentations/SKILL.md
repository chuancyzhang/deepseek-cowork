---
name: presentations
description: Create, edit and follow templates for editable local PPTX presentations, with page planning and rendered slide verification. Use document_read for content-only questions and data-analysis for calculations.
description_cn: 创建、编辑和套用模板制作可编辑 PPTX，并检查逐页内容与版式。
type: builtin
source_type: builtin
default_enabled: true
metadata:
  author: deepseek-cowork team
  version: "1.0"
allowed-tools: [document_read, document_render, document_copy, run_python_code, view_image, review_draft]
---

# 演示文稿

交付本地 .pptx，使用随包 python-pptx 和现有 Python 工具。只问原稿内容就读取回答，不改文件。不调用 Cowork 没有的 Spaces、Artifact Session 或专属 PPT Agent。

## 制作

明确受众、用途、时长或页数，安排每页内容。信息足够直接制作，只有缺失信息实质改变结果时才询问，不强制选模板或审批大纲。创建或改变版式时读取 [制作与模板指导](references/authoring.md)，实际路径见 References。

- 用户指定的顺序、页数、模板优先。无模板默认 16:9，一致的字体、间距与对齐。
- 每页有明确用途，根据内容选文字、比较、图表、流程或图片，不全部套相同卡片。
- 文字、表格、形状与支持的图表保持可编辑，不默认把整页做成图片。图表复用 data-analysis 核验的同一份结果，单位与范围一致。
- 图片保持比例，不裁掉证据；生成的说明图不能冒充真实事件照片。标识使用原素材。
- 来源放相关页或备注，影响观众判断的假设与限制留在页面。
- 编辑已有稿件先用 document_copy 创建任务副本，限定修改范围，保持其他页面及模板元素。该流程不等于系统写入隔离。动画、媒体、SmartArt 和嵌入对象需要专项验证，不能承诺无损。

## 检查与交付

保存后打开 PPTX 一次，核对页数、顺序、关键内容、数值和可编辑性。任务需要验版式时，用 document_render(review=true, constraints=...) 检查改动页和复杂页，每批细节最多 4 页。续批沿用相同 renderer 和版式约束。总览和逐页检查按任务要求启用，总览只检查跨页风格。原图留在检查记录。

重点看中文换行、缺字、字号、遮挡、图片比例、图例和标签。修正问题后复查受影响的页面。拥挤时先改内容或布局，再考虑字号。

渲染不可用、模型不能看图或检查预算用完时，交付可打开的成品并说明原因和实际检查范围。

普通任务交付最终 PPTX，PDF、预览图或构建代码只在需要时交付。已有办公稿草稿上下文时提交 review_draft(kind="office", file_path=..., draft_id=...)，沿用审阅保存流程，不额外创建审批。
