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


def extract_renderable_html_response(text):
    stripped = str(text or "").strip()
    if not stripped:
        return ""

    if looks_like_complete_html_response(stripped):
        return stripped

    fenced_match = re.search(
        r"```(?:html|htm)\s*\n(.*?)(?:\n```|```$)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_match:
        fenced_html = fenced_match.group(1).strip()
        if looks_like_complete_html_response(fenced_html):
            return fenced_html

    document_match = re.search(
        r"(<!doctype\s+html\b.*?</html\s*>|<html\b.*?</html\s*>)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if document_match:
        return document_match.group(1).strip()

    return ""
