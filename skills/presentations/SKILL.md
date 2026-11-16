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
allowed-tools: [document_read, document_render, run_python_code, view_image, review_draft]
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
- 编辑已有稿件保留原件，限定修改范围，保持其他页面及模板元素。动画、媒体、SmartArt 和嵌入对象需要专项验证，不能承诺无损。

## 检查与交付

重新打开 PPTX，检查页数、顺序、关键内容、数值和可编辑性。用 document_render 分批生成页面，再用 view_image 检查；续批携带相同 source_version。

新稿检查全部页，局部编辑检查相关页，整体布局变化重新完整检查。重点看中文换行、缺字、字号、遮挡、图片比例、图例和标签。对象坐标检查不能替代实际页面检查；拥挤时先改内容或布局，不一味缩小字体。

渲染不可用、模型不能看图或检查范围有限时明确说明并保留成果。生成成功、结构有效、视觉检查通过分别报告，不声称在未使用的 PowerPoint 中检查过。

普通任务交付最终 PPTX，PDF、预览图或构建代码只在需要时交付。已有办公稿草稿上下文时提交 review_draft(kind="office", file_path=..., draft_id=...)，沿用审阅保存流程，不额外创建审批。
