"""Repeatable offscreen screenshots; only disposable local sample data is used."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QScrollArea
from PySide6.QtGui import QFont, QFontDatabase
from core.enterprise_client import EnterpriseStore
from core.theme import apply_theme
from ui.account_connections import AccountConnectionsPage


def main():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt":
        for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + filename)
        app.setFont(QFont("Microsoft YaHei", 10))
    apply_theme(app)
    destination = Path(__file__).resolve().parents[1] / "docs/images/enterprise"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-enterprise-qt-") as temporary:
        store = EnterpriseStore(temporary)
        logged_in = {"connection": {"schema_version": 1, "config_version": 1, "platform_id": "sample-company",
            "platform_name": "示例企业", "platform_url": "https://cowork.example.invalid", "distribution_channel": "enterprise"},
            "member": {"id": "sample-member", "name": "李明"}, "client_id": "sample-installation", "generation": "sample",
            "expires_at": time.time() + 3600, "models": [{"id": "sample-model", "name": "企业通用模型", "model_name": "sample",
                "provider": "openai", "protocol": "chat_completions", "base_url": "https://model.example.invalid/v1", "options": {}}]}
        store.write(logged_in)
        page = AccountConnectionsPage(config_manager=SimpleNamespace(data_dir=temporary, get_mcp_servers=lambda: []))
        scale = os.environ.get("QT_SCALE_FACTOR", "1").replace(".", "_")
        for state, suffix in ((logged_in, ""), ({k: logged_in[k] for k in ("connection", "client_id", "generation")}, "-login")):
            store.write(state)
            page.enterprise.refresh()
            for width, height, label in ((760, 640, "desktop"), (460, 540, "desktop-narrow")):
                page.resize(width, height)
                page.show()
                app.processEvents()
                scroll = page.findChild(QScrollArea)
                assert scroll.horizontalScrollBar().maximum() == 0
                path = destination / f"{label}{suffix}-{scale}.png"
                assert page.grab().save(str(path))
                print(path)
        page.enterprise.jobs.pool.shutdown(wait=True)
        page.close()
        page.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
