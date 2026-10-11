"""Render actual question-navigation widgets with 200 sample questions, in isolation."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from core.theme import DesignTokens, ThemeRuntimeManager
from core.theme_service import ThemeRepository
from main import QuestionNavigatorRail, QuestionNavigatorPreviewCard


def run():
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "segoeui.ttf"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei", 10))
    output = ROOT / "docs/assets/question-navigator"
    output.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    with tempfile.TemporaryDirectory(prefix="cowork-question-qa-") as temporary:
        manager = ThemeRuntimeManager(app, ThemeRepository(temporary))
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="question_navigation_capture"), manager.last_error
        pane = QWidget()
        pane.setObjectName("QuestionNavigationSample")
        title = QLabel("长对话中的历史提问", pane)
        caption = QLabel("200 条提问 · 导航组件示例", pane)
        rail = QuestionNavigatorRail(pane)
        card = QuestionNavigatorPreviewCard(pane)
        rail.previewRequested.connect(
            lambda entry, point: card.show_entry(entry, rail.mapTo(pane, QPoint(46, point.y())), pane.rect())
        )
        rail.previewDismissed.connect(card.hide)
        samples = [
            ("整理项目交付清单", "列出已完成的文档、仍需确认的事项，以及各项交付的负责人。"),
            ("这份方案还有哪些地方需要调整？", "建议先明确验收标准，再检查关键流程。已有成果保持不变，待确认项单独列出。"),
            ("请汇总本周的进展", "本周完成了方案评审和主要流程验证，接下来处理反馈并准备交付。"),
        ]
        entries = [dict(message_id=f"q{i}", title=samples[(i-1) % 3][0], answer=samples[(i-1) % 3][1])
                   for i in range(1, 201)]
        rail.set_entries(entries)

        def capture(name, width=560, height=440, active="q86", hover=True):
            pane.resize(width, height)
            pane.setStyleSheet(f"QWidget#QuestionNavigationSample {{background: {DesignTokens.bg_main};}}")
            title.setStyleSheet(f"color: {DesignTokens.text_primary}; font-size: 17px; font-weight: 600;")
            caption.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
            title.setGeometry(60, 24, width - 72, 32)
            caption.setGeometry(60, 58, width - 72, 25)
            rail.setGeometry(16, 104, 40, min(280, height - 116))
            pane.show()
            app.processEvents()
            rail.set_active_message_id(active)
            if active == "q86":
                rail.window_start = 77
                rail._scroll_position = 77.0
            rail.clearFocus()
            if hover:
                y = next(y for index, y in rail._marker_geometry() if entries[index]["message_id"] == active)
                rail._pointer = QPoint(18, int(y))
                rail._wave_y = y
                rail._start_motion()
                QTest.qWait(400)
            else:
                rail._pointer = None
                rail._wave_strength = 0
                card.hide()
                rail.update()
            app.processEvents()
            assert pane.grab().save(str(output / f"{name}-{scale}x.png"))
            pane.hide()

        capture("light-idle", hover=False)
        capture("light-wave")
        capture("light-small", 360, 370)
        capture("light-start", active="q1")
        capture("light-end", active="q200")
        profile = {
            "id": "question-navigation-dark", "name": "Question navigation dark", "schema_version": 2,
            "overrides": {"tokens": {"bg_main": "#202124", "text_primary": "#eeeeee",
                                      "text_secondary": "#b5b6ba", "text_tertiary": "#92959b",
                                      "border_subtle": "#414349"}},
            "assets": {}, "surfaces": {}, "components": {}, "content": {},
            "workspace_scene": {"attachment": "fixed", "layers": []},
        }
        assert manager.apply_profile(profile, preview=True, reason="question_navigation_capture")
        capture("dark-wave")
        assert manager.restore_saved_theme(reason="question_navigation_capture_cancel")
        capture("light-restored")
        pane.close()
    print(f"Question navigator screenshots: {output} ({scale}x)")


if __name__ == "__main__":
    run()
