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
        f"引用已验证：{len(ref) if isinstance(ref, list) else 1} 份资料。新建对话、当前对话和项目集成请在 Cowork 主窗口使用。"))
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
    page.add_item("用户研究", {"document": {"id": "doc-b", "knowledge_base_id": "kb-a", "title": "用户研究"}})
    page.check_page(True)
    app.processEvents()
    page.grab().save(os.path.join(destination, "batch.png"))
    page.check_page(False)
    service.transport.wiki_pages = [
        {"slug": "index", "title": "资料概览", "page_type": "index"},
        {"slug": "concept", "title": "认知力", "page_type": "concept", "category_path": ["投资基础"]},
        {"slug": "summary", "title": "投资观 · 文档摘要", "page_type": "summary"}]
    page.show_wiki()
    wait_for(lambda: page.items.count() == 3)
    app.processEvents()
    page.grab().save(os.path.join(destination, "wiki.png"))
    page.open_kb({"id": "kb-a", "name": "产品资料"})
    wait_for(lambda: page.items.count() == 1)
    page.select_item(page.items.item(0))
    wait_for(lambda: "只读用户可以阅读" in page.reader.toPlainText())
    app.processEvents()
    page.grab().save(os.path.join(destination, "desktop.png"))
    page.show_html(b"<!doctype html><html><head><style>body{font:18px sans-serif;padding:32px;color:#272735}h1{color:#6563cf}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}section{padding:24px;background:#f4f4fa}</style></head><body><h1>HTML preview</h1><p>Local and remote files can be previewed while indexing.</p><div class='grid'><section>Original layout</section><section>Independent preview permissions</section></div></body></html>")
    html_text = []
    def html_loaded():
        page.html_view.page().toPlainText(lambda value: html_text.append(value))
        return any("HTML preview" in value for value in html_text)
    wait_for(html_loaded)
    QTest.qWait(300)
    app.processEvents()
    page.grab().save(os.path.join(destination, "html.png"))
    page.reader_stack.setCurrentIndex(0)
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
    from PySide6.QtWidgets import QWidget, QPushButton, QVBoxLayout
    from ui.knowledge_library import KnowledgeReferencePopover, KnowledgeProjectPicker
    host = QWidget()
    host.resize(800, 650)
    host_layout = QVBoxLayout(host)
    host_layout.addStretch()
    anchor = QPushButton("资料 · 2")
    host_layout.addWidget(anchor)
    host.show()
    app.processEvents()
    popover = KnowledgeReferencePopover([{"title": "产品研究.md"}, {"title": "业务分析.html"}], host)
    popover.show_for(anchor, prefer_above=True)
    app.processEvents()
    host.grab().save(os.path.join(destination, "references.png"))
    popover.close()
    host.close()
    picker = KnowledgeProjectPicker([{"name": "产品研究", "path": "D:/projects/research"}, {"name": "文档工作", "path": "D:/projects/documents"}])
    picker.show()
    app.processEvents()
    picker.grab().save(os.path.join(destination, "projects.png"))
    picker.close()
    page.jobs.pool.shutdown(wait=True)
    page.close()
    temporary.cleanup()
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
