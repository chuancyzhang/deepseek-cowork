"""Render real conversation components, without model calls or user data."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from core.theme import DesignTokens, ThemeRuntimeManager
from core.theme_service import ThemeRepository
from main import InlineInteractionCard, ToolCallCard
from ui.conversation_files import ConversationFileTable


def run():
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows"), "Fonts", name)
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    output = ROOT / "docs/assets/conversation-runtime"
    output.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    with tempfile.TemporaryDirectory(prefix="cowork-conversation-qa-") as temporary:
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="conversation_capture"), manager.last_error
        saved = repository.load()
        host = QWidget()
        host.setObjectName("ConversationSample")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        caption = QLabel("对话组件示例")
        layout.addWidget(caption)
        failed = ToolCallCard("run_skill_script", {"skill_name": "knowledge-library", "script_name": "search"}, "failed", parent=host)
        failed.set_result("列表响应结构不兼容：items", {"status": "error", "error": "列表响应结构不兼容：items"})
        layout.addWidget(failed)
        question = InlineInteractionCard({"request_id": "sample", "kind": "questionnaire",
            "title": "需要你的输入", "message": "继续处理录音前，请选择运行环境。",
            "questions": [{"id": "runtime", "question": "使用哪个运行环境？", "options": [
                {"label": "使用已有环境", "value": "existing", "description": "提供本机 Python 和模型目录。"},
                {"label": "创建新的环境", "value": "new", "description": "在当前工作目录中准备环境。"},
                {"label": "自定义", "value": "__custom__"}]}]}, host)
        question.timeout_timer.stop()
        layout.addWidget(question)
        table = ConversationFileTable(host)
        paths = [str(Path(temporary, name)) for name in (
            "transcript.txt", "scripts/cli.py", "scripts/audio.py", "scripts/qwen_backend.py",
            "scripts/diarization.py", "scripts/merge.py", "scripts/editor.py", "validation.md")]
        paths[:0] = ["G:/qwen-runtime/text-results/第一份录音对话稿.wav.txt",
                     "G:/qwen-runtime/text-results/第二份录音对话稿.wav.txt"]
        table.set_files(paths, temporary)
        layout.addWidget(table)

        def capture(name, width):
            host.setStyleSheet(f"QWidget#ConversationSample {{ background: {DesignTokens.bg_main}; }}")
            caption.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
            host.setFixedWidth(width)
            host.show()
            QTest.qWait(80)
            host.adjustSize()
            QTest.qWait(80)
            host.adjustSize()
            app.processEvents()
            if app.focusWidget() is not None:
                app.focusWidget().clearFocus()
            for index in range(table.rows_layout.count()):
                row = table.rows_layout.itemAt(index).widget()
                assert row.path_label.geometry().right() < row.type_label.geometry().left()
            image = host.grab()
            assert image.save(str(output / f"{name}-{scale}x.png"))
            return image.toImage()

        # Prime layout once before comparing a preview round trip.
        capture("light-wide", 720)
        before = capture("light-wide", 720)
        capture("light-small", 380)
        table.toggle.click()
        capture("files-expanded", 720)
        table.toggle.click()
        assert manager.apply_profile({"id": "qa-dark", "name": "深色预览", "schema_version": 2,
            "overrides": {"tokens": {"bg_main": "#202124", "bg_app": "#202124", "bg_hover": "#35373d",
                "text_primary": "#eeeeee", "text_secondary": "#b5b6ba", "text_tertiary": "#92959b",
                "border_subtle": "#414349", "overlay_bg": "#202124", "overlay_border": "#414349"}},
            "assets": {}, "surfaces": {}, "components": {}, "content": {},
            "workspace_scene": {"attachment": "fixed", "layers": []}}, preview=True, reason="conversation_capture")
        capture("dark-small", 380)
        assert manager.restore_saved_theme(reason="conversation_capture_cancel")
        after = capture("light-restored", 720)
        assert repository.load() == saved
        assert before == after, "Theme cancellation must restore the original appearance"
        host.close()
        host.deleteLater()
        app.processEvents()
        manager.stop()
    print(f"Conversation screenshots: {output} ({scale}x)")


if __name__ == "__main__":
    run()
