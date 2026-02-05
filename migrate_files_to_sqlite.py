import argparse
import glob
import json
import os
import time

from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager


def _compute_title(messages):
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            content = msg.get("content").strip()
            if content:
                return content[:30]
    return "新对话"


def migrate(history_dir):
    db_path = os.path.join(history_dir, "chat_history.sqlite")
    storage = ChatStorage(db_path)
    files = glob.glob(os.path.join(history_dir, "chat_history_*.json"))
    files.sort(key=os.path.getmtime)
    migrated = 0
    skipped = 0
    for file_path in files:
        filename = os.path.basename(file_path)
        session_id = filename.replace("chat_history_", "").replace(".json", "")
        if storage.has_conversation(session_id):
            skipped += 1
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                skipped += 1
                continue
            title = _compute_title(data)
            meta = {}
            storage.save_conversation(session_id, data, title=title, meta=meta)
            migrated += 1
        except Exception:
            skipped += 1
    return migrated, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-dir", default=None)
    args = parser.parse_args()

    if args.history_dir:
        history_dir = args.history_dir
    else:
        config = ConfigManager()
        history_dir = config.get_chat_history_dir()

    if not os.path.exists(history_dir):
        print("History directory not found.")
        return

    start = time.time()
    migrated, skipped = migrate(history_dir)
    duration = time.time() - start
    print(f"Migrated: {migrated}")
    print(f"Skipped: {skipped}")
    print(f"Duration: {duration:.2f}s")


if __name__ == "__main__":
    main()
