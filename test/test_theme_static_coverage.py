import ast
import os
import re
import unittest


class ThemeStaticCoverageTests(unittest.TestCase):
    def test_native_ui_has_no_unregistered_hex_colors(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        checked_files = [
            os.path.join(repo_root, "main.py"),
            *[
                os.path.join(root, filename)
                for root, _dirs, files in os.walk(os.path.join(repo_root, "ui"))
                for filename in files
                if filename.endswith(".py")
            ],
        ]
        violations = []
        color_pattern = re.compile(
            r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?|rgba?\(\s*\d"
        )
        for path in checked_files:
            allowed = False
            with open(path, "r", encoding="utf-8") as stream:
                source = stream.read()
            content_colors = {}
            if os.path.basename(path) == "main.py":
                # These are generated QR/PDF pixels, independent of the UI theme.
                exceptions = {
                    ("ChannelQrDialog", "_render_qr"): {"#FFFFFF", "#111111"},
                    ("MainWindow", "_build_deliverable_markdown_pdf_document"): {"#ffffff"},
                }
                for cls in ast.parse(source).body:
                    if not isinstance(cls, ast.ClassDef):
                        continue
                    for method in cls.body:
                        colors = exceptions.get((cls.name, getattr(method, "name", "")))
                        if colors:
                            for line_number in range(method.lineno, method.end_lineno + 1):
                                content_colors[line_number] = colors
            for line_number, line in enumerate(source.splitlines(), 1):
                if "THEME_STATIC_AUDIT_ALLOW_BEGIN" in line:
                    allowed = True
                    continue
                if "THEME_STATIC_AUDIT_ALLOW_END" in line:
                    allowed = False
                    continue
                colors = color_pattern.findall(line)
                if not allowed and any(color not in content_colors.get(line_number, set()) for color in colors):
                    violations.append(
                        f"{os.path.relpath(path, repo_root)}:{line_number}: {line.strip()}"
                    )
        self.assertEqual(
            violations,
            [],
            "Cowork 原生 UI 出现未登记硬编码颜色：\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
