import unittest
from unittest.mock import MagicMock, patch

import main


class TestWebEngineRuntime(unittest.TestCase):
    def setUp(self):
        main.QWebEngineView = None
        main.WEBENGINE_AVAILABLE = None
        main.WEBENGINE_IMPORT_ERROR = None
        main.WEBENGINE_IMPORT_TRACEBACK = ""

    def tearDown(self):
        self.setUp()

    def test_load_webengine_records_success(self):
        view_class = object()
        module = type("_WebEngineModule", (), {"QWebEngineView": view_class})()

        with patch.object(main.importlib, "import_module", return_value=module):
            result = main.load_qwebengine_view()

        self.assertIs(result, view_class)
        self.assertTrue(main.WEBENGINE_AVAILABLE)
        self.assertIsNone(main.WEBENGINE_IMPORT_ERROR)
        self.assertEqual(main.WEBENGINE_IMPORT_TRACEBACK, "")

    def test_missing_module_has_actionable_message_and_diagnostic_log(self):
        error = ModuleNotFoundError("No module named 'PySide6.QtPositioning'", name="PySide6.QtPositioning")
        logger = MagicMock()

        with patch.object(main.importlib, "import_module", side_effect=error), \
             patch.object(main, "log_sub_agent_runtime", logger):
            result = main.load_qwebengine_view()

        self.assertIsNone(result)
        self.assertFalse(main.WEBENGINE_AVAILABLE)
        self.assertIn("PySide6.QtPositioning", main.webengine_unavailable_message())
        self.assertIn("ModuleNotFoundError", main.WEBENGINE_IMPORT_TRACEBACK)
        logger.assert_called_once()
        self.assertEqual(logger.call_args.args[0], "qt_webengine_import_failed")

    def test_dll_import_failure_uses_safe_user_message(self):
        error = ImportError("DLL load failed while importing QtWebEngineCore: C:\\private\\Qt6.dll")

        with patch.object(main.importlib, "import_module", side_effect=error), \
             patch.object(main, "log_sub_agent_runtime"):
            result = main.load_qwebengine_view()

        message = main.webengine_unavailable_message()
        self.assertIsNone(result)
        self.assertIn("依赖库加载失败", message)
        self.assertNotIn("C:\\private", message)


if __name__ == "__main__":
    unittest.main()
