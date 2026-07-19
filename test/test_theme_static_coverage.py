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
                for line_number, line in enumerate(stream, 1):
                    if "THEME_STATIC_AUDIT_ALLOW_BEGIN" in line:
                        allowed = True
                        continue
                    if "THEME_STATIC_AUDIT_ALLOW_END" in line:
                        allowed = False
                        continue
                    if not allowed and color_pattern.search(line):
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
