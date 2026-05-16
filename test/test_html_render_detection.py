import unittest

from core.html_render import extract_renderable_html_response, looks_like_complete_html_response


class TestHtmlRenderDetection(unittest.TestCase):
    def test_detects_full_html_document(self):
        self.assertTrue(
            looks_like_complete_html_response(
                "<!doctype html><html><body><h1>Hello</h1></body></html>"
            )
        )

    def test_detects_complete_block_html_fragments(self):
        self.assertTrue(looks_like_complete_html_response("<div><p>Hello</p></div>"))
        self.assertTrue(looks_like_complete_html_response("<table><tr><td>A</td></tr></table>"))

    def test_markdown_is_not_detected_as_html(self):
        self.assertFalse(looks_like_complete_html_response("# Title\n\nRegular **Markdown**"))
        self.assertFalse(looks_like_complete_html_response("Use <strong>bold</strong> in Markdown."))

    def test_html_code_block_is_not_detected_as_html(self):
        self.assertFalse(
            looks_like_complete_html_response(
                "```html\n<div><p>Hello</p></div>\n```"
            )
        )

    def test_app_protocol_tags_are_not_detected_as_html(self):
        self.assertFalse(
            looks_like_complete_html_response(
                "<proposed_plan>\n# Plan\n</proposed_plan>"
            )
        )

    def test_extracts_html_document_after_intro_text(self):
        html = extract_renderable_html_response(
            "下面是一个完整测试页面 HTML：\n\n"
            "<!DOCTYPE html><html><body><h1>Hello</h1></body></html>"
        )
        self.assertTrue(html.lower().startswith("<!doctype html>"))
        self.assertIn("<h1>Hello</h1>", html)

    def test_extracts_fenced_html_document(self):
        html = extract_renderable_html_response(
            "可保存为 test.html：\n\n"
            "```html\n<!DOCTYPE html><html><body><h1>Hello</h1></body></html>\n```"
        )
        self.assertTrue(html.lower().startswith("<!doctype html>"))
        self.assertNotIn("```", html)

    def test_does_not_extract_plain_markdown(self):
        self.assertEqual("", extract_renderable_html_response("# Title\n\nRegular **Markdown**"))


if __name__ == "__main__":
    unittest.main()
