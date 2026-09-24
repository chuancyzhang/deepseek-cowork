"""DPAPI-encrypted per-conversation mappings; short cross-process transactions."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager, closing


class TokenVault:
    def __init__(self, scope, data_dir=None, protector=None):
        if data_dir is None:
            from core.env_utils import get_app_data_dir
            data_dir = get_app_data_dir()
        self.path = os.path.join(data_dir, "data_security", "mappings.sqlite3")
        self.scope = str(scope)
        self.protector = protector

    def crypt(self):
        if self.protector is None:
            from core.variable_store import WindowsDpapiProtector
            self.protector = WindowsDpapiProtector()
        return self.protector

    def _decode(self, row):
        if row is None:
            return {"namespace": uuid.uuid4().hex[:12], "key": uuid.uuid4().hex + uuid.uuid4().hex, "values": {}}
        return json.loads(self.crypt().unprotect(row[0]).decode("utf-8"))

    @contextmanager
    def transaction(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS mappings(scope TEXT PRIMARY KEY, secret BLOB NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT secret FROM mappings WHERE scope=?", (self.scope,)).fetchone()
            state = self._decode(row)
            before = json.dumps(state, ensure_ascii=False, sort_keys=True)
            yield state
            after = json.dumps(state, ensure_ascii=False, sort_keys=True)
            if before != after:
                secret = self.crypt().protect(after.encode("utf-8"))
                db.execute("INSERT OR REPLACE INTO mappings VALUES (?, ?)", (self.scope, secret))

    def read(self):
        if not os.path.isfile(self.path):
            return {}
        with closing(sqlite3.connect("file:" + self.path.replace("\\", "/") + "?mode=ro", uri=True, timeout=0.05)) as db:
            row = db.execute("SELECT secret FROM mappings WHERE scope=?", (self.scope,)).fetchone()
            return self._decode(row)["values"] if row else {}

    def delete(self):
        if not os.path.isfile(self.path):
            return
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db, db:
            db.execute("DELETE FROM mappings WHERE scope=?", (self.scope,))
