"""Exclusive data ownership and verified, additive desktop schema upgrades."""
from __future__ import annotations

import hashlib
from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

log = logging.getLogger(__name__)
UPGRADE_ID = "native-desktop-1"
FACT_TABLES = ("conversations", "messages", "agents", "agent_messages", "deliverables",
               "inline_visualizations", "inline_visualization_states")


class DataLease:
    """An OS-held lock; process death releases ownership without stale PID rules."""
    def __init__(self, directory):
        self.path = Path(directory) / ".desktop-service.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            if self.file.seek(0, 2) == 0:
                self.file.write(b"\0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("此数据目录已由另一个后台服务使用，请回到已有窗口。") from exc

    def close(self):
        if not self.file.closed:
            self.file.close()


@contextmanager
def _read_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as database:
        yield database


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _fingerprint(db, table, columns):
    fields = ",".join(map(_identifier, columns))
    digest = hashlib.sha256()
    count = 0
    for row in db.execute(f"SELECT {fields} FROM {_identifier(table)} ORDER BY {fields}"):
        value = json.dumps(row, ensure_ascii=False, separators=(",", ":"),
                           default=lambda blob: {"bytes": bytes(blob).hex()}).encode("utf-8")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def _atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DesktopUpgrade:
    """Back up before constructing ConfigManager or altering database schemas.

    A failure leaves both the source and backup available. This component never
    restores an older backup over data which could contain subsequent writes.
    """
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve()
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
        if not isinstance(config, dict):
            raise ValueError("现有配置文件格式无效，原数据已保留。")
        history = Path(config.get("chat_history_dir") or self.root / "chat_history").expanduser()
        if not history.is_absolute():
            raise ValueError("现有历史目录不是绝对路径，请先在原版修正目录设置。")
        if config.get("chat_history_dir") and not history.is_dir():
            raise RuntimeError("原历史目录暂不可访问，请恢复目录连接；不会创建空白历史替代原数据。")
        self.database = history / "chat_history.sqlite"
        self.marker = self.root / ".desktop-upgrade.json"
        self.backup = None
        self.baseline = {}
        if self.marker.exists():
            marker = json.loads(self.marker.read_text(encoding="utf-8"))
            if marker.get("upgrade") == UPGRADE_ID and marker.get("database") == str(self.database):
                if not self.database.is_file():
                    raise RuntimeError("已使用的历史数据库不存在，原备份仍保留；请恢复历史目录后重试。")
                return
        self.backup = self.root / "updates" / "data-backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        self.backup.mkdir(parents=True, exist_ok=False)
        try:
            if config_path.exists():
                shutil.copy2(config_path, self.backup / "config.json")
            if self.database.exists():
                with _read_database(self.database) as source:
                    with closing(sqlite3.connect(self.backup / "chat_history.sqlite")) as target:
                        source.backup(target)
                with _read_database(self.backup / "chat_history.sqlite") as snapshot:
                    result = snapshot.execute("PRAGMA integrity_check").fetchall()
                    if result != [("ok",)]:
                        raise RuntimeError("历史数据库完整性校验失败；已保留原数据库及备份。")
                    tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    for table in FACT_TABLES:
                        if table not in tables:
                            continue
                        columns = [row[1] for row in snapshot.execute(f"PRAGMA table_info({_identifier(table)})")]
                        self.baseline[table] = {"columns": columns, **_fingerprint(snapshot, table, columns)}
            _atomic_json(self.backup / "manifest.json", {"upgrade": UPGRADE_ID,
                         "database": str(self.database), "baseline": self.baseline})
            log.info("desktop_upgrade_backup_ready tables=%s", len(self.baseline))
        except Exception:
            log.exception("desktop_upgrade_backup_failed")
            raise

    def complete(self):
        if self.backup is None:
            return
        with _read_database(self.database) as live:
            if live.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("升级后的数据库校验失败，已有数据与升级前备份已保留。")
            for table, expected in self.baseline.items():
                actual = _fingerprint(live, table, expected["columns"])
                if actual != {key: expected[key] for key in ("rows", "sha256")}:
                    log.error("desktop_upgrade_facts_changed table=%s", table)
                    raise RuntimeError("升级校验发现历史内容变化，已停止启动；请使用升级前备份核对恢复。")
        _atomic_json(self.marker, {"upgrade": UPGRADE_ID, "database": str(self.database),
                                  "backup": str(self.backup), "verified_at": int(time.time())})
        log.info("desktop_upgrade_verified tables=%s", len(self.baseline))
