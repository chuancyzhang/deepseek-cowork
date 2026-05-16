import unittest

from core.html_render import looks_like_complete_html_response


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


if __name__ == "__main__":
    unittest.main()
