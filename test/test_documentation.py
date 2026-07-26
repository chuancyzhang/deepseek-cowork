import importlib.util
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT, "scripts", "check_docs.py")
SPEC = importlib.util.spec_from_file_location("check_docs", SCRIPT_PATH)
check_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_docs)


class DocumentationTests(unittest.TestCase):
    def test_canonical_document_links_and_versions(self):
        errors = []
        errors.extend(check_docs.validate_local_links(check_docs.markdown_files()))
        errors.extend(check_docs.validate_current_versions())
        errors.extend(check_docs.validate_legacy_paths_removed())
        self.assertEqual(errors, [])

    def test_product_concepts_and_screenshot_contract(self):
        errors = []
        errors.extend(check_docs.validate_product_concepts())
        errors.extend(check_docs.validate_screenshot_contract())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
