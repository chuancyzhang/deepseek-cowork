"""Render real conversation components, without model calls or user data."""
import os
import sys
import tempfile
import json
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from core.theme import DesignTokens, ThemeRuntimeManager
from core.theme_service import ThemeRepository
from core.theme_presets import builtin_theme
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


def run_interaction_states():
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows"), "Fonts", name)
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    output = ROOT / "docs/assets/conversation-runtime"
    output.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    with tempfile.TemporaryDirectory(prefix="cowork-interaction-qa-") as temporary:
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="interaction_capture"), manager.last_error
        saved = repository.load()
        host = QWidget()
        host.setObjectName("InteractionSample")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        waiting = InlineInteractionCard({"kind": "approval", "title": "请确认",
            "message": "是否删除工作目录中的临时文件？"}, host)
        expired = InlineInteractionCard({"kind": "approval", "title": "已结束的确认",
            "message": "是否删除工作目录中的临时文件？"}, host)
        expired.finish_waiting("timeout")
        failed = InlineInteractionCard({"kind": "text", "title": "提交暂未确认",
            "message": "请补充希望优先处理的内容。"}, host)
        failed.text_input.setText("先检查文档中的数字与引用。")
        failed.restore_after_resolution_error("暂时无法确认是否提交成功，输入已保留")
        stopped = InlineInteractionCard({"kind": "text", "title": "停止后的输入",
            "message": "请补充希望优先处理的内容。"}, host)
        stopped.text_input.setText("这段输入已保留，可以选择和复制。")
        stopped.stop_waiting()
        for card in (waiting, expired, failed, stopped):
            layout.addWidget(card)

        def capture(name, width):
            host.setStyleSheet(f"QWidget#InteractionSample {{ background: {DesignTokens.bg_main}; }}")
            host.setFixedWidth(width)
            host.show()
            QTest.qWait(60)
            host.adjustSize()
            QTest.qWait(60)
            if app.focusWidget() is not None:
                app.focusWidget().clearFocus()
            for card in (waiting, expired, failed, stopped):
                assert card.geometry().right() <= host.width()
                for button in (card.submit_btn, card.cancel_btn):
                    if not button.isHidden():
                        assert button.geometry().right() <= card.width()
                if not card.validation_label.isHidden():
                    assert card.validation_label.geometry().bottom() <= card.height()
            image = host.grab()
            assert image.save(str(output / f"{name}-{scale}x.png"))
            return image.toImage()

        capture("interactions-wide", 720)
        before = capture("interactions-wide", 720)
        capture("interactions-small", 380)
        assert manager.apply_profile({"id": "interaction-preview", "name": "交互状态预览", "schema_version": 2,
            "overrides": {"tokens": {"bg_main": "#f4f4f4", "bg_app": "#f4f4f4"}},
            "assets": {}, "surfaces": {}, "components": {}, "content": {},
            "workspace_scene": {"attachment": "fixed", "layers": []}}, preview=True, reason="interaction_capture")
        assert manager.restore_saved_theme(reason="interaction_capture_cancel")
        after = capture("interactions-wide", 720)
        assert repository.load() == saved
        assert before == after, "Theme preview must not change saved appearance"
        assert expired.submit_btn.isHidden() and stopped.submit_btn.isHidden()
        assert failed.submit_btn.isEnabled() and stopped.text_input.isReadOnly()
        host.close()
        host.deleteLater()
        app.processEvents()
        manager.stop()
    print(f"Interaction screenshots: {output} ({scale}x)")


def run_questionnaire_states():
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows"), "Fonts", name)
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    output = ROOT / "docs/assets/conversation-runtime"
    output.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    payload = {"kind": "questionnaire", "title": "明确你的 A 股分析需求", "questions": [
        {"id": "goal", "header": "分析重点", "question": "分析重点是什么？", "options": [
            {"value": "market", "label": "理解大盘走势", "description": "先了解指数、成交量与板块轮动，再决定深入方向。"},
            {"value": "holdings", "label": "评估现有持仓", "description": "围绕已有股票或基金，梳理风险与观察信号。"},
            {"value": "timing", "label": "判断入场时机", "description": "关注市场变化与需要进一步确认的条件。"},
            {"value": "research", "label": "寻找研究方向", "description": "比较板块表现、基本面与近期催化因素。"}]},
        {"id": "horizon", "header": "观察周期", "question": "主要关注多长时间？", "options": [
            {"value": "medium", "label": "中期（3–6 个月）", "description": "兼顾近期变化与中期趋势。"},
            {"value": "short", "label": "近期（1 个月以内）"}, {"value": "long", "label": "长期（1 年以上）"}]},
        {"id": "format", "header": "呈现方式", "question": "希望怎样呈现分析？", "options": [
            {"value": "summary", "label": "先给结论，再展开依据", "description": "先看关键判断，需要时再深入证据。"},
            {"value": "table", "label": "用对比表组织"}, {"value": "report", "label": "提供完整分析报告"}]}]}

    class Tracker(QObject):
        def __init__(self):
            super().__init__()
            self.unexpected = []

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
                self.unexpected.append({"class": type(obj).__name__, "name": obj.objectName(), "stack": traceback.format_stack()})
            return False

    with tempfile.TemporaryDirectory(prefix="cowork-questionnaire-qa-") as temporary:
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="questionnaire_capture"), manager.last_error
        saved = repository.load()
        host = QWidget()
        host.setObjectName("QuestionnaireSample")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(16, 16, 16, 16)
        host.show()
        app.processEvents()
        tracker = Tracker()
        app.installEventFilter(tracker)
        card = InlineInteractionCard(payload, host)
        layout.addWidget(card)

        def capture(name, width):
            host.setStyleSheet(f"QWidget#QuestionnaireSample {{ background: {DesignTokens.bg_main}; }}")
            host.setFixedWidth(width)
            QTest.qWait(50)
            host.adjustSize()
            QTest.qWait(50)
            if app.focusWidget() is not None:
                app.focusWidget().clearFocus()
            if not card.questionnaire.isHidden():
                assert card.questionnaire.scroll.height() <= 420
                for button in (card.submit_btn, card.cancel_btn):
                    assert card.rect().contains(button.mapTo(card, button.rect().bottomRight()))
                for control in card.question_controls:
                    for button in control["buttons"]:
                        if button.isVisible():
                            assert button.height() >= button.heightForWidth(button.width()) - 2
            assert not tracker.unexpected, tracker.unexpected
            image = host.grab()
            assert image.save(str(output / f"questionnaire-{name}-{scale}x.png"))
            return image.toImage()

        capture("light-wide", 720)
        before = capture("light-small", 380)
        assert manager.apply_profile(builtin_theme("default", dark=True), preview=True, reason="questionnaire_dark"), manager.last_error
        capture("dark-small", 380)
        assert manager.restore_saved_theme(reason="questionnaire_preview_cancel"), manager.last_error
        after = capture("light-small", 380)
        assert before == after, "Cancelling a preview must restore the original appearance"
        assert repository.load() == saved
        card.questionnaire.sections[1].header.click()
        card.question_controls[1]["input"].setText("先关注未来两周")
        card.restore_after_resolution_error("连接暂时不可用，输入已保留")
        capture("retry", 380)
        answers = card.questionnaire_answers()
        card.show_receipt(answers)
        capture("receipt", 720)
        card.receipt.view_btn.click()
        capture("answers", 720)
        screen = app.primaryScreen()
        report = {"platform": app.platformName(), "qt_scale_factor": scale,
                  "logical_dpi": screen.logicalDotsPerInch(), "device_pixel_ratio": screen.devicePixelRatio(),
                  "unexpected_native_windows": tracker.unexpected, "theme_preview_restored": True}
        (output / f"questionnaire-validation-{scale}x.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        app.removeEventFilter(tracker)
        host.close()
        host.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        manager.stop()
    print(f"Questionnaire screenshots: {output} ({scale}x)")


if __name__ == "__main__":
    if "--questionnaire" in sys.argv:
        run_questionnaire_states()
    elif "--interactions" in sys.argv:
        run_interaction_states()
    else:
        run()
