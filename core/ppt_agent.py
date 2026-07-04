from dataclasses import dataclass


PPT_AGENT_STRATEGY_AUTO = "auto"
PPT_AGENT_STRATEGY_DEFAULT = "default"
PPT_AGENT_STRATEGY_GUIZANG = "guizang"
PPT_AGENT_STRATEGY_FRONTEND_SLIDES = "frontend_slides"
PPT_AGENT_STRATEGY_HUASHU = "huashu"

PPT_AGENT_STRATEGY_CHOICES = {
    PPT_AGENT_STRATEGY_AUTO,
    PPT_AGENT_STRATEGY_DEFAULT,
    PPT_AGENT_STRATEGY_GUIZANG,
    PPT_AGENT_STRATEGY_FRONTEND_SLIDES,
    PPT_AGENT_STRATEGY_HUASHU,
}

PPT_AGENT_PREFERENCE_AUTO = "auto"
PPT_AGENT_PREFERENCE_WEB = "web"
PPT_AGENT_PREFERENCE_TECH = "tech"
PPT_AGENT_PREFERENCE_BUSINESS = "business"
PPT_AGENT_PREFERENCE_TEMPLATE = "template"

PPT_AGENT_PREFERENCES = {
    PPT_AGENT_PREFERENCE_AUTO,
    PPT_AGENT_PREFERENCE_WEB,
    PPT_AGENT_PREFERENCE_TECH,
    PPT_AGENT_PREFERENCE_BUSINESS,
    PPT_AGENT_PREFERENCE_TEMPLATE,
}


@dataclass(frozen=True)
class PptHtmlCapability:
    key: str
    name: str
    summary: str
    suitable_for: str
    output: str = "HTML"


PPT_HTML_CAPABILITIES = {
    PPT_AGENT_STRATEGY_GUIZANG: PptHtmlCapability(
        key=PPT_AGENT_STRATEGY_GUIZANG,
        name="Guizang PPT Skill",
        summary="HTML 横向翻页 PPT / PPT 配图 / 强视觉表达",
        suitable_for="网页演示、视觉分享、封面图、风格化内容",
    ),
    PPT_AGENT_STRATEGY_FRONTEND_SLIDES: PptHtmlCapability(
        key=PPT_AGENT_STRATEGY_FRONTEND_SLIDES,
        name="Frontend Slides",
        summary="前端技术生成 HTML Slides / 适合产品和技术演示",
        suitable_for="产品发布、技术分享、网页化演示、交互式演示",
    ),
    PPT_AGENT_STRATEGY_HUASHU: PptHtmlCapability(
        key=PPT_AGENT_STRATEGY_HUASHU,
        name="Huashu Design",
        summary="高审美设计型 HTML PPT / 商业视觉表达",
        suitable_for="商业汇报、路演、品牌提案、发布会、产品介绍",
    ),
}

_GUIZANG_KEYWORDS = (
    "网页ppt",
    "网页 ppt",
    "网页演示",
    "横向翻页",
    "封面图",
    "配图",
    "视觉分享",
    "内容分享",
    "强风格",
    "风格化",
)
_FRONTEND_SLIDES_KEYWORDS = (
    "技术分享",
    "产品发布",
    "交互式",
    "前端",
    "slides",
    "slide",
    "网页化",
    "发布演示",
)
_HUASHU_KEYWORDS = (
    "高级感",
    "高审美",
    "设计感",
    "商业汇报",
    "路演",
    "品牌提案",
    "发布会",
    "产品介绍",
    "咨询风",
    "好看",
)


def normalize_ppt_agent_strategy(value):
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "guizang_ppt_skill": PPT_AGENT_STRATEGY_GUIZANG,
        "frontend": PPT_AGENT_STRATEGY_FRONTEND_SLIDES,
        "frontend_slides": PPT_AGENT_STRATEGY_FRONTEND_SLIDES,
        "huashu_design": PPT_AGENT_STRATEGY_HUASHU,
        "ppt_agent": PPT_AGENT_STRATEGY_DEFAULT,
    }
    text = aliases.get(text, text)
    return text if text in PPT_AGENT_STRATEGY_CHOICES else PPT_AGENT_STRATEGY_AUTO


def normalize_ppt_agent_preference(value):
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in PPT_AGENT_PREFERENCES else PPT_AGENT_PREFERENCE_AUTO


def choose_ppt_agent_strategy(request_text, preference=PPT_AGENT_PREFERENCE_AUTO, explicit_strategy=PPT_AGENT_STRATEGY_AUTO, template_file=""):
    explicit = normalize_ppt_agent_strategy(explicit_strategy)
    if explicit != PPT_AGENT_STRATEGY_AUTO:
        return explicit
    preference = normalize_ppt_agent_preference(preference)
    if preference == PPT_AGENT_PREFERENCE_WEB:
        return PPT_AGENT_STRATEGY_GUIZANG
    if preference == PPT_AGENT_PREFERENCE_TECH:
        return PPT_AGENT_STRATEGY_FRONTEND_SLIDES
    if preference == PPT_AGENT_PREFERENCE_BUSINESS:
        return PPT_AGENT_STRATEGY_HUASHU
    if preference == PPT_AGENT_PREFERENCE_TEMPLATE or template_file:
        return PPT_AGENT_STRATEGY_DEFAULT
    text = str(request_text or "").strip().lower()
    if any(keyword in text for keyword in _HUASHU_KEYWORDS):
        return PPT_AGENT_STRATEGY_HUASHU
    if any(keyword in text for keyword in _FRONTEND_SLIDES_KEYWORDS):
        return PPT_AGENT_STRATEGY_FRONTEND_SLIDES
    if any(keyword in text for keyword in _GUIZANG_KEYWORDS):
        return PPT_AGENT_STRATEGY_GUIZANG
    return PPT_AGENT_STRATEGY_DEFAULT


def ppt_agent_strategy_label(strategy):
    strategy = normalize_ppt_agent_strategy(strategy)
    if strategy == PPT_AGENT_STRATEGY_DEFAULT:
        return "默认 PPT Agent"
    capability = PPT_HTML_CAPABILITIES.get(strategy)
    return capability.name if capability else "自动选择"


def ppt_agent_capability_prompt_lines():
    lines = []
    for capability in PPT_HTML_CAPABILITIES.values():
        lines.extend(
            [
                f"- {capability.name}: {capability.summary}",
                f"  适合: {capability.suitable_for}",
                f"  输出: {capability.output}，必须注册为 HTML deliverable，再复用 HTML→PPTX/DOCX/PDF 转换链路。",
            ]
        )
    return lines


def build_ppt_agent_prompt(request_text, preference=PPT_AGENT_PREFERENCE_AUTO, explicit_strategy=PPT_AGENT_STRATEGY_AUTO, template_file=""):
    request = str(request_text or "").strip()
    preference = normalize_ppt_agent_preference(preference)
    explicit_strategy = normalize_ppt_agent_strategy(explicit_strategy)
    selected_strategy = choose_ppt_agent_strategy(
        request,
        preference=preference,
        explicit_strategy=explicit_strategy,
        template_file=template_file,
    )
    selected_label = ppt_agent_strategy_label(selected_strategy)
    template_file = str(template_file or "").strip()
    capability_lines = "\n".join(ppt_agent_capability_prompt_lines())
    template_lines = ""
    if template_file:
        template_lines = (
            "\n用户提供了 PPTX 模板，后续导出 PPTX 时必须把 HTML 作为内容来源、模板作为视觉与结构来源，"
            "不要修改原模板文件。\n"
            f"- PPTX 模板: {template_file}\n"
        )
    explicit_note = "用户显式指定了内置能力，优先尊重该选择。" if explicit_strategy != PPT_AGENT_STRATEGY_AUTO else "请根据需求自动选择最合适的生成策略。"
    prompt = (
        "请以「PPT Agent」身份处理下面的演示文稿任务，并生成可预览、可继续转换的 HTML 演示文稿工作稿。\n\n"
        f"生成偏好: {preference}\n"
        f"策略选择: {selected_label}\n"
        f"{explicit_note}\n\n"
        "PPT Agent 职责:\n"
        "- 判断用户要做的 PPT 类型，例如汇报、课件、路演、方案、研究报告、产品发布或视觉演示。\n"
        "- 先生成大纲和页面规划，再落地为演示文稿形态的完整 HTML 文件。\n"
        "- 不要只输出普通文本、Markdown 摘要或长文网页。\n"
        "- 不要绕过现有交付物系统直接生成 PPTX；PPTX/DOCX/PDF 后续统一从 HTML 工作稿转换。\n"
        "- 完成后明确给出项目工作区内 HTML 文件的完整路径，便于右侧交付物视图自动预览。\n\n"
        "HTML 工作稿约束:\n"
        "- 默认 16:9，按 slide 分页，每页有明确标题层级。\n"
        "- 内容适合投影展示，控制文字密度，保留图片、图表、布局和视觉层级信息。\n"
        "- 如果使用 CSS/JS/图片资源，请放在可迁移的本地目录，并确保 HTML deliverable preview 能正常打开。\n"
        "- HTML 应让后续转换 PPTX 时不需要重新理解整篇长文。\n\n"
        "内置 html-ppt 能力:\n"
        f"{capability_lines}\n\n"
        "自动选择规则:\n"
        "- 普通汇报、课件、研究报告、会议总结: 默认 PPT Agent。\n"
        "- Word、PDF、Markdown 或长文本资料: 先整理内容结构，再生成 HTML 工作稿。\n"
        "- 提供 PPTX 模板: 优先生成符合模板转换要求的 HTML 工作稿，后续走模板化导出。\n"
        "- 网页 PPT、横向翻页、封面图、强风格内容分享: Guizang PPT Skill。\n"
        "- 产品发布、技术分享、交互式演示、前端风格 slides: Frontend Slides。\n"
        "- 高级感、商业汇报、发布会、路演、品牌提案、高审美: Huashu Design。\n"
        "- 如果某个外部 html-ppt 能力不可用，请清晰告知并使用默认 PPT Agent HTML 工作稿生成，不要静默降级。\n"
        f"{template_lines}\n"
        "[用户需求]\n"
        f"{request}"
    )
    return {
        "prompt": prompt,
        "selected_strategy": selected_strategy,
        "selected_label": selected_label,
    }
