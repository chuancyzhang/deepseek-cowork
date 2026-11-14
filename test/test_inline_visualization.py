import os
import tempfile
import unittest
from unittest.mock import patch

from core.chat_storage import ChatStorage
from core.inline_visualization import (
    build_visualization_document,
    find_inline_visualization_files,
    publish_visualization_fragment,
    strip_inline_visualization_directives,
    validate_visualization_fragment,
)


class TestInlineVisualization(unittest.TestCase):
    def test_directive_parser_only_accepts_exact_standalone_directive(self):
        source = (
            "说明\n\n"
            '::cowork-inline-vis{file="customer-explorer-a1b2c3d4.html"}\n\n'
            "结论"
        )
        self.assertEqual(
            find_inline_visualization_files(source),
            ["customer-explorer-a1b2c3d4.html"],
        )
        self.assertEqual(
            strip_inline_visualization_directives(source, ["customer-explorer-a1b2c3d4.html"]),
            "说明\n\n结论",
        )
        self.assertEqual(find_inline_visualization_files("prefix ::cowork-inline-vis{file=\"x.html\"}"), [])

    def test_fragment_validation_rejects_complete_document(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bad.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("<!doctype html><html><body><div id='x'></div></body></html>")
            with self.assertRaisesRegex(ValueError, "HTML Fragment"):
                validate_visualization_fragment(path)

    def test_fragment_validation_allows_svg_namespace_without_network_origin(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "svg.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    '<div id="chart"><svg xmlns="http://www.w3.org/2000/svg" '
                    'xmlns:xlink="http://www.w3.org/1999/xlink"><title>收入</title></svg></div>'
                    '<script>document.createElementNS("http://www.w3.org/2000/svg", "path")</script>'
                )
            validated = validate_visualization_fragment(path)
            self.assertEqual(validated["origins"], [])

    def test_fragment_validation_accepts_trusted_cdn_origins(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "remote.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    '<div id="remote" style="background-image:url(https://cdn.jsdelivr.net/npm/x/bg.png)">'
                    '<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>'
                    '</div><script type="module">import "https://esm.sh/d3@7";</script>'
                )
            validated = validate_visualization_fragment(path)
            self.assertEqual(
                validated["origins"],
                ["https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com", "https://esm.sh"],
            )

    def test_fragment_validation_rejects_untrusted_external_urls(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "remote.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('<div id="remote"><script src="https://cdn.example.com/chart.js"></script></div>')
            with self.assertRaisesRegex(ValueError, "不在受信 CDN 白名单"):
                validate_visualization_fragment(path)

    def test_google_fonts_adds_its_trusted_font_asset_origin(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "fonts.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    '<div id="fonts">图表</div>'
                    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">'
                )
            validated = validate_visualization_fragment(path)
            self.assertEqual(
                validated["origins"],
                ["https://fonts.googleapis.com", "https://fonts.gstatic.com"],
            )

    def test_fragment_validation_rejects_insecure_resource_url(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "remote.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('<div id="remote"><img src="http://cdn.jsdelivr.net/image.png"></div>')
            with self.assertRaisesRegex(ValueError, "仅支持 HTTPS"):
                validate_visualization_fragment(path)

    def test_read_only_document_blocks_interaction_and_reports_runtime_errors(self):
        document = build_visualization_document(
            '<div id="demo"><script>throw new Error("boom")</script></div>',
            read_only=True,
            origins=["https://cdn.jsdelivr.net"],
        )
        self.assertIn('tabindex="-1"', document)
        self.assertIn("pointer-events:none", document)
        self.assertIn("bridge.reportError", document)
        self.assertIn("https://cdn.jsdelivr.net", document)
        self.assertIn("connect-src &amp;#x27;none&amp;#x27;", document)
        self.assertIn("securitypolicyviolation", document)
        self.assertIn("外部资源加载失败", document)

    def test_document_rejects_untrusted_recorded_origin(self):
        with self.assertRaisesRegex(ValueError, "不在受信 CDN 白名单"):
            build_visualization_document(
                '<div id="demo">ok</div>',
                origins=["https://cdn.example.com"],
            )


    def test_publish_register_and_restore_state(self):
        with tempfile.TemporaryDirectory() as data_dir:
            conversation_id = "conversation-1"
            with patch("core.inline_visualization.get_app_data_dir", return_value=data_dir):
                from core.inline_visualization import visualization_staging_dir

                staging = visualization_staging_dir(conversation_id, create=True)
                source = os.path.join(staging, "customer-explorer.html")
                with open(source, "w", encoding="utf-8") as handle:
                    handle.write("<div id='customer-explorer'><button>选择</button></div><script>void 0</script>")
                artifact = publish_visualization_fragment(conversation_id, "customer-explorer.html")

            storage = ChatStorage(os.path.join(data_dir, "history", "chat.sqlite"))
            storage.upsert_conversation(conversation_id, title="demo")
            record = storage.register_inline_visualization(conversation_id, artifact)
            self.assertEqual(record["file"], artifact["file"])
            self.assertTrue(os.path.isfile(record["path"]))

            storage.save_inline_visualization_state(
                conversation_id,
                artifact["file"],
                artifact["sha256"],
                {"selected": "A"},
            )
            self.assertEqual(
                storage.get_inline_visualization_state(
                    conversation_id,
                    artifact["file"],
                    artifact["sha256"],
                ),
                {"selected": "A"},
            )
            self.assertEqual(
                storage.get_inline_visualization_state(
                    conversation_id,
                    artifact["file"],
                    "different-hash",
                ),
                {},
            )

    def test_publish_and_storage_preserve_detected_origins(self):
        with tempfile.TemporaryDirectory() as data_dir:
            conversation_id = "conversation-cdn"
            with patch("core.inline_visualization.get_app_data_dir", return_value=data_dir):
                from core.inline_visualization import visualization_staging_dir

                staging = visualization_staging_dir(conversation_id, create=True)
                source = os.path.join(staging, "cdn-chart.html")
                with open(source, "w", encoding="utf-8") as handle:
                    handle.write(
                        '<div id="cdn-chart"></div>'
                        '<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>'
                    )
                artifact = publish_visualization_fragment(conversation_id, "cdn-chart.html")

            self.assertEqual(artifact["origins"], ["https://cdn.jsdelivr.net"])
            storage = ChatStorage(os.path.join(data_dir, "history", "chat.sqlite"))
            storage.upsert_conversation(conversation_id, title="cdn")
            record = storage.register_inline_visualization(conversation_id, artifact)
            self.assertEqual(record["origins"], ["https://cdn.jsdelivr.net"])






if __name__ == "__main__":
    unittest.main()
