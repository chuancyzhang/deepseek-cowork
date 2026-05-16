import re


HTML_RENDER_ROOT_TAGS = (
    "html",
    "head",
    "body",
)

HTML_RENDER_BLOCK_TAGS = (
    "article",
    "aside",
    "blockquote",
    "details",
    "div",
    "figure",
    "figcaption",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
)


def looks_like_complete_html_response(text):
    stripped = str(text or "").strip()
    if not stripped or stripped.startswith("```"):
        return False

    lower = stripped.lower()
    if lower.startswith("<!doctype html"):
        return True

    root_pattern = "|".join(HTML_RENDER_ROOT_TAGS)
    if re.match(rf"^<({root_pattern})(?:\s|>|/)", lower):
        return True

    block_pattern = "|".join(HTML_RENDER_BLOCK_TAGS)
    match = re.match(rf"^<({block_pattern})(?:\s|>|/)", lower)
    if not match:
        return False

    tag_name = match.group(1)
    return f"</{tag_name}>" in lower or lower.endswith("/>")
