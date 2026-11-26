---
name: template-creator
description: 用户明确要求把 Word、PPT、Excel、文本或 HTML 参考材料制作为可复用模板 Skill，或更新该模板时使用。一次性套用模板交给相应文件技能。
allowed-tools: [document_read, view_image, create_new_skill, update_skill, review_draft]
---
# 创建可复用模板

明确用户要反复完成什么、需要哪些输入，以及参考材料中应保留的结构、样式、语气和可变内容。已有要求足够时直接推进，只询问会改变模板用途的缺口。

1. 用对应的 documents、presentations 或表格技能读取材料；视觉特征影响复用时渲染并查看实际页面。文本/HTML 检查结构和必要资产。不能仅凭文件名描述模板。
2. 提炼稳定规则和替换字段，区分必填、可选、重复区块及长度限制。明确缺数据时如何处理，不编造业务值。
3. 通过 skill_builder 创建普通 Skill：SKILL.md 写用途、触发条件、输入、保留规则、制作步骤和验收；把用户指定的原件及必要字体/图片等资产复制到 assets/，详细规则按需放 references/。脚本只承载确定性的重复操作。
4. 二进制资源用 resources 的 kind=asset、包内 path 和当前工作区 source_path。宿主使用暂存副本并在替换资源时处理实际冲突。参考原件保留，产出另存。
5. 用一组代表性的新内容试做，核对实际用到的替换字段、资源和输出。模板依赖版式时查看相关页面。
6. 明确创建/更新请求使用 create_new_skill/update_skill；从“沉淀为 Skill”启动的任务走 review_draft。模板随现有技能中心管理与 ZIP 导出，不增加独立模板库。

模板指令应引用包内 assets/，不能依赖原工作目录、个人绝对路径或临时下载。优先复制再修改原件以保留复杂样式；需要重建时明确原件中哪些特征可复现。可分享模板不要夹带无关私人内容，按用户指定材料范围保存。
