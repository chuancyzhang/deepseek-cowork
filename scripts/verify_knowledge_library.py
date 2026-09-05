"""Run the native WeKnora page for live sign-in, or render deterministic UI fixtures."""

import argparse
import logging
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", metavar="DIRECTORY")
    options = parser.parse_args()
    if options.screenshots:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.knowledge_library import KnowledgeService, KnowledgeStore
    from ui.knowledge_library import KnowledgePage

    app = QApplication([])
    app.setStyle("Fusion")
    if options.screenshots:
        font_path = os.path.join(os.environ["WINDIR"], "Fonts", "msyh.ttc")
        if QFontDatabase.addApplicationFont(font_path) < 0:
            raise RuntimeError("截图验证未能加载 Microsoft YaHei 字体。")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    temporary = None
    if options.screenshots:
        sys.path.insert(0, os.path.join(ROOT, "test"))
        from test_knowledge_library import TestProtector, WeKnoraFixture
        temporary = tempfile.TemporaryDirectory()
        service = KnowledgeService(KnowledgeStore(temporary.name, TestProtector()), WeKnoraFixture())
        service.login("http://localhost", "reader@example.test", "password")
    else:
        service = KnowledgeService()
        logging.basicConfig(filename=os.path.join(service.store.data_dir, "knowledge_library_validation.log"),
                            encoding="utf-8", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
    page = KnowledgePage(service=service)
    page.setWindowTitle("Cowork 资料库 · 接入验证")
    page.referenceRequested.connect(lambda ref, mode: page.notice.setText(
        f"引用已验证：{ref['title']}。新建对话、当前对话和项目集成请在 Cowork 主窗口使用。"))
    page.resize(1180, 800)
    page.show()
    if not options.screenshots:
        result = app.exec()
        page.jobs.pool.shutdown(wait=True)
        return result

    def wait_for(predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(10)
        if not predicate():
            raise RuntimeError(page.notice.text())

    destination = os.path.abspath(options.screenshots)
    os.makedirs(destination, exist_ok=True)
    wait_for(lambda: page.tree.topLevelItemCount() == 6)
    page.open_kb({"id": "kb-a", "name": "产品资料"})
    wait_for(lambda: page.items.count() == 1)
    app.processEvents()
    page.grab().save(os.path.join(destination, "browse.png"))
    page.select_item(page.items.item(0))
    wait_for(lambda: "只读用户可以阅读" in page.reader.toPlainText())
    app.processEvents()
    page.grab().save(os.path.join(destination, "desktop.png"))
    page.resize(650, 780)
    app.processEvents()
    page.grab().save(os.path.join(destination, "small.png"))
    service.transport.fail_status = 403
    page.query.setText("权限")
    page.search()
    wait_for(lambda: "无权" in page.notice.text())
    page.grab().save(os.path.join(destination, "forbidden.png"))
    service.transport.fail_status = None
    service.logout()
    page.refresh()
    app.processEvents()
    page.grab().save(os.path.join(destination, "login.png"))
    page.jobs.pool.shutdown(wait=True)
    page.close()
    temporary.cleanup()
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
