"""Capture the actual conversation file-link controls in an isolated workspace."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(scale):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    with tempfile.TemporaryDirectory(prefix="cowork-file-link-ui-") as directory:
        os.environ["APPDATA"] = directory
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
        from core.theme import ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        from main import ChatBubble
        app = QApplication([])
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        theme = ThemeRuntimeManager(app, repository=ThemeRepository(str(Path(directory, "theme"))))
        assert theme.apply_profile({"id": "file-link-qa", "name": "文件链接验收", "overrides": {"tokens": {}}}, preview=True)
        workspace = Path(directory, "workspace")
        workspace.mkdir()
        (workspace / "index.html").write_text("<h1>交付页面</h1>", encoding="utf-8")
        shell = QWidget()
        shell.setStyleSheet("background: white;")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("工作区文件链接")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        bubble = ChatBubble("Agent", "", workspace_dir=str(workspace))
        layout.addWidget(bubble, alignment=Qt.AlignTop)
        layout.addStretch()
        shell.resize(500 if scale > 1 else 760, 440)
        shell.show()
        bubble.set_main_content("已完成 HTML 复现：[打开页面](index.html)。\n\n页面已保存到当前工作区，可以点击正文链接或下方文件入口查看。", final=True)
        QTest.qWait(150)
        app.processEvents()
        assert bubble._deliverable_paths == [str(workspace / "index.html")]
        assert bubble.content_rich_edit.isVisible()
        assert "打开页面" in bubble.content_rich_edit.toPlainText()
        assert bubble.deliverable_cards.isVisible()
        output = ROOT / "docs/guides/file-links"
        output.mkdir(parents=True, exist_ok=True)
        assert shell.grab().save(str(output / f"relative-link-light-{int(scale*100)}.png"))
        shell.close()
        shell.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1)
    main(parser.parse_args().scale)
