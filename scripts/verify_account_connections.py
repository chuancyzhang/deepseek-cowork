"""Render isolated account/connection states without contacting external services."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path, required=True)
    parser.add_argument("--width", type=int, default=720)
    parser.add_argument("--scale", default="1")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = args.scale
    from PySide6.QtWidgets import QApplication, QScrollArea
    from PySide6.QtGui import QFont, QFontDatabase
    from core.connections.broker import ConnectionBroker
    from core.connections.store import ConnectionStore
    from ui.account_connections import AccountConnectionsPage
    app = QApplication.instance() or QApplication([])
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 10))
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-connections-qa-") as folder:
        store = ConnectionStore(folder)
        broker = ConnectionBroker(folder, folder, store=store)
        t = {"id": "company-library", "version": 1, "name": "公司资料库", "service": "weknora",
             "adapter": "weknora", "base_url": "https://knowledge.example.com", "parameters": {}}
        broker.catalog.write(broker.catalog.distributed_path, [t])
        page = AccountConnectionsPage(broker=broker)
        page.resize(args.width, 860)
        page.show()
        app.processEvents()
        page.grab().save(str(args.screenshots / "empty.png"))
        item = store.create(t)
        page.current_id = item["id"]
        page.refresh()
        app.processEvents()
        page.grab().save(str(args.screenshots / "login.png"))
        store.update(item["id"], {"state": "ready", "identity": {"issuer": t["base_url"], "subject": "zhang", "tenant": "研发团队", "kind": "person"},
                     "profile": {"label": "张同学 · 公司账号"}, "verified_at": time.time()})
        page.current_id = item["id"]
        page.refresh()
        app.processEvents()
        page.grab().save(str(args.screenshots / "connected.png"))
        scroll = page.findChild(QScrollArea)
        page.scope_button.setChecked(True)
        page.more_button.setChecked(True)
        app.processEvents()
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        app.processEvents()
        page.grab().save(str(args.screenshots / "permissions.png"))
        store.update(item["id"], {"state": "authentication_required"})
        page.more_button.setChecked(False)
        page.scope_button.setChecked(False)
        page.refresh()
        page.notice.setText("登录已失效。任务已保留已完成的内容，请使用原账号重新认证后返回任务继续。")
        scroll.verticalScrollBar().setValue(0)
        app.processEvents()
        page.grab().save(str(args.screenshots / "authentication-required.png"))
        page.jobs.pool.shutdown(wait=True)
        page.close()
    print(f"Screenshots: {args.screenshots.resolve()}")


if __name__ == "__main__":
    main()
