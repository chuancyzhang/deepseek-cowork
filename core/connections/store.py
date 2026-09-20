"""Independent connection state; short SQLite transactions and per-connection refresh locks."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .errors import ConnectionError

log = logging.getLogger(__name__)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ConnectionStore:
    def __init__(self, data_dir, protector=None):
        self.data_dir = str(data_dir)
        self.path = os.path.join(self.data_dir, "connections.sqlite3")
        self.protector = protector

    @property
    def exists(self):
        return os.path.isfile(self.path)

    @contextmanager
    def db(self, write=False):
        os.makedirs(self.data_dir, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY, public TEXT NOT NULL,
                    secret BLOB, revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS grants(connection_id TEXT, capability TEXT, public TEXT NOT NULL,
                    PRIMARY KEY(connection_id, capability));
                CREATE TABLE IF NOT EXISTS selections(capability TEXT, requirement TEXT, connection_id TEXT NOT NULL,
                    PRIMARY KEY(capability, requirement));
                CREATE TABLE IF NOT EXISTS bindings(id TEXT PRIMARY KEY, task_id TEXT, capability TEXT,
                    requirement TEXT, public TEXT NOT NULL, UNIQUE(task_id, capability, requirement));
                CREATE TABLE IF NOT EXISTS waits(id TEXT PRIMARY KEY, task_id TEXT, binding_id TEXT,
                    status TEXT, public TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_sessions(id TEXT PRIMARY KEY, public TEXT NOT NULL);
            """)
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def crypt(self):
        if self.protector is None:
            from ..variable_store import WindowsDpapiProtector
            self.protector = WindowsDpapiProtector()
        return self.protector

    def get(self, connection_id, *, secret=False, db=None):
        if db is None:
            if not self.exists:
                raise ConnectionError("not_connected")
            with self.db() as conn:
                return self.get(connection_id, secret=secret, db=conn)
        row = db.execute("SELECT * FROM connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise ConnectionError("not_connected")
        public = json.loads(row["public"])
        public["revision"] = row["revision"]
        if secret:
            try:
                public["credentials"] = json.loads(self.crypt().unprotect(row["secret"]).decode("utf-8")) if row["secret"] else {}
            except Exception:
                raise ConnectionError("authentication_required") from None
        return public

    def list(self):
        if not self.exists:
            return []
        with self.db() as db:
            return [{**json.loads(row["public"]), "revision": row["revision"]}
                    for row in db.execute("SELECT public, revision FROM connections ORDER BY rowid")]

    def create(self, template, name=""):
        connection_id = uuid.uuid4().hex
        public = {"id": connection_id, "name": name.strip() or template["name"], "template": copy.deepcopy(template),
                  "state": "not_configured", "generation": uuid.uuid4().hex, "identity": None,
                  "scopes": [], "verified_at": None, "created_at": time.time(), "login_session_id": None}
        with self.db(write=True) as db:
            db.execute("INSERT INTO connections(id, public) VALUES(?,?)", (connection_id, encode(public)))
        return self.get(connection_id)

    def update(self, connection_id, patch, *, credentials=None, expected_revision=None, clear_secret=False):
        encrypted = self.crypt().protect(encode(credentials).encode("utf-8")) if credentials is not None else None
        with self.db(write=True) as db:
            item = self.get(connection_id, db=db)
            if expected_revision is not None and item["revision"] != expected_revision:
                raise ConnectionError("conflict")
            revision = item.pop("revision") + 1
            item.update(copy.deepcopy(patch))
            item.pop("credentials", None)
            db.execute("UPDATE connections SET public=?, revision=? WHERE id=?", (encode(item), revision, connection_id))
            if credentials is not None or clear_secret:
                db.execute("UPDATE connections SET secret=? WHERE id=?", (encrypted, connection_id))
        return self.get(connection_id)

    def grant(self, connection_id, capability, operations, resources=()):
        if not capability or not operations or any(not isinstance(x, str) or not x for x in [*operations, *resources]):
            raise ConnectionError("forbidden")
        with self.db(write=True) as db:
            item = self.get(connection_id, db=db)
            if not item.get("identity"):
                raise ConnectionError("not_connected")
            value = {"connection_id": connection_id, "capability": capability, "operations": sorted(set(operations)),
                     "resources": sorted(set(resources)), "revision": uuid.uuid4().hex, "generation": item["generation"]}
            previous = db.execute("SELECT public FROM grants WHERE connection_id=? AND capability=?", (connection_id, capability)).fetchone()
            if previous:
                old = json.loads(previous[0])
                if all(old[k] == value[k] for k in ("operations", "resources", "generation")):
                    return old
            db.execute("INSERT OR REPLACE INTO grants VALUES(?,?,?)", (connection_id, capability, encode(value)))
        return value

    def activate(self, connection_id, usages):
        """Commit the explicitly displayed grants and mode switches together."""
        with self.db(write=True) as db:
            item = self.get(connection_id, db=db)
            if item["state"] != "ready":
                raise ConnectionError("not_connected")
            for capability, requirement, operations, resources in usages:
                if not capability or not operations or any(not isinstance(x, str) or not x for x in [*operations, *resources]):
                    raise ConnectionError("forbidden")
                value = {"connection_id": connection_id, "capability": capability, "operations": sorted(set(operations)),
                         "resources": sorted(set(resources)), "revision": uuid.uuid4().hex, "generation": item["generation"]}
                previous = db.execute("SELECT public FROM grants WHERE connection_id=? AND capability=?", (connection_id, capability)).fetchone()
                if previous:
                    old = json.loads(previous[0])
                    if all(old[k] == value[k] for k in ("operations", "resources", "generation")):
                        value = old
                db.execute("INSERT OR REPLACE INTO grants VALUES(?,?,?)", (connection_id, capability, encode(value)))
                db.execute("INSERT OR REPLACE INTO selections VALUES(?,?,?)", (capability, requirement, connection_id))
        log.info("connection.activate connection=%s capabilities=%s", connection_id, ",".join(u[0] for u in usages))

    def grants(self, connection_id):
        if not self.exists:
            return []
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT public FROM grants WHERE connection_id=?", (connection_id,))]

    def revoke(self, connection_id, capability):
        with self.db(write=True) as db:
            db.execute("DELETE FROM grants WHERE connection_id=? AND capability=?", (connection_id, capability))

    def select(self, capability, requirement, connection_id):
        with self.db(write=True) as db:
            if connection_id:
                item = self.get(connection_id, db=db)
                if item["state"] != "ready":
                    raise ConnectionError("not_connected")
                db.execute("INSERT OR REPLACE INTO selections VALUES(?,?,?)", (capability, requirement, connection_id))
            else:
                db.execute("DELETE FROM selections WHERE capability=? AND requirement=?", (capability, requirement))

    def selection(self, capability, requirement="default"):
        if not self.exists:
            return None
        with self.db() as db:
            row = db.execute("SELECT connection_id FROM selections WHERE capability=? AND requirement=?", (capability, requirement)).fetchone()
            return row[0] if row else None

    def binding(self, task_id, capability, requirement):
        if not self.exists:
            return None
        with self.db() as db:
            row = db.execute("SELECT public FROM bindings WHERE task_id=? AND capability=? AND requirement=?",
                             (task_id, capability, requirement)).fetchone()
            return json.loads(row[0]) if row else None

    def bind(self, task_id, capability, requirement, connection_id, operations, resources=()):
        if not task_id:
            raise ConnectionError("forbidden")
        with self.db(write=True) as db:
            row = db.execute("SELECT public FROM bindings WHERE task_id=? AND capability=? AND requirement=?",
                             (task_id, capability, requirement)).fetchone()
            if row:
                return json.loads(row[0])
            item = self.get(connection_id, db=db)
            grant_row = db.execute("SELECT public FROM grants WHERE connection_id=? AND capability=?", (connection_id, capability)).fetchone()
            if not grant_row:
                raise ConnectionError("grant_required")
            grant = json.loads(grant_row[0])
            if (grant["generation"] != item["generation"] or not set(operations) <= set(grant["operations"])
                    or (grant["resources"] and (not resources or not set(resources) <= set(grant["resources"])))):
                raise ConnectionError("forbidden")
            if item["state"] == "disconnected":
                raise ConnectionError("revoked")
            value = {"id": uuid.uuid4().hex, "task_id": task_id, "capability": capability, "requirement": requirement,
                     "connection_id": connection_id, "identity": item["identity"], "generation": item["generation"],
                     "template": item["template"], "scopes": item["scopes"], "grant_revision": grant["revision"],
                     "operations": list(grant["operations"]), "resources": list(resources)}
            db.execute("INSERT INTO bindings VALUES(?,?,?,?,?)", (value["id"], task_id, capability, requirement, encode(value)))
        return value

    def check_binding(self, binding, operation=None, resources=()):
        # Caller must hold a host-produced binding; JSON from tools is never accepted here.
        item = self.get(binding["connection_id"])
        if item["state"] == "disconnected":
            raise ConnectionError("revoked")
        if any(item.get(k) != binding.get(k) for k in ("identity", "generation", "template", "scopes")):
            raise ConnectionError("identity_changed")
        grant = next((g for g in self.grants(item["id"]) if g["capability"] == binding["capability"]), None)
        if grant is None or grant["revision"] != binding["grant_revision"]:
            raise ConnectionError("revoked")
        if operation and operation not in binding["operations"]:
            raise ConnectionError("forbidden")
        if operation is not None and binding["resources"] and (not resources or not set(resources) <= set(binding["resources"])):
            raise ConnectionError("forbidden")
        return item

    def disconnect(self, connection_id):
        self.update(connection_id, {"state": "disconnected", "generation": uuid.uuid4().hex}, clear_secret=True)
        with self.db(write=True) as db:
            db.execute("DELETE FROM grants WHERE connection_id=?", (connection_id,))
            rows = db.execute("SELECT id, public FROM waits WHERE status='waiting'").fetchall()
            for row in rows:
                if json.loads(row["public"]).get("connection_id") == connection_id:
                    db.execute("UPDATE waits SET status='cancelled' WHERE id=?", (row["id"],))

    def delete(self, connection_id):
        self.disconnect(connection_id)
        with self.db(write=True) as db:
            db.execute("DELETE FROM connections WHERE id=?", (connection_id,))
            # Retain selected references so deletion cannot silently fall back to legacy mode.

    def save_session(self, connection):
        identity = connection["identity"]
        session_id = hashlib.sha256(encode(identity).encode()).hexdigest()
        value = {"id": session_id, "identity": identity, "adapter": connection["template"]["adapter"], "updated_at": time.time()}
        with self.db(write=True) as db:
            db.execute("INSERT OR REPLACE INTO login_sessions VALUES(?,?)", (session_id, encode(value)))
        return session_id

    def logout_session(self, session_id):
        for item in self.list():
            if item.get("login_session_id") == session_id:
                self.disconnect(item["id"])
        with self.db(write=True) as db:
            db.execute("DELETE FROM login_sessions WHERE id=?", (session_id,))
        log.info("connection.logout session=%s", session_id)

    def wait(self, binding, operation):
        public = {"id": uuid.uuid4().hex, "connection_id": binding["connection_id"], "operation": operation,
                  "identity": binding["identity"], "created_at": time.time()}
        with self.db(write=True) as db:
            # One durable wait per operation on this binding; never stores operation arguments.
            prior = db.execute("SELECT id, public FROM waits WHERE binding_id=? AND status='waiting'", (binding["id"],)).fetchone()
            if prior:
                return json.loads(prior["public"])
            db.execute("INSERT INTO waits VALUES(?,?,?,?,?)", (public["id"], binding["task_id"], binding["id"], "waiting", encode(public)))
        return public

    def waits(self):
        if not self.exists:
            return []
        with self.db() as db:
            return [{**json.loads(row["public"]), "status": row["status"], "task_id": row["task_id"], "binding_id": row["binding_id"]}
                    for row in db.execute("SELECT * FROM waits ORDER BY rowid DESC LIMIT 100")]

    def resolve_wait(self, wait_id, status):
        if status not in {"ready", "cancelled", "interrupted"}:
            raise ValueError("invalid wait status")
        with self.db(write=True) as db:
            db.execute("UPDATE waits SET status=? WHERE id=? AND status='waiting'", (status, wait_id))

    def cancel_task(self, task_id):
        if not self.exists:
            return
        with self.db(write=True) as db:
            db.execute("UPDATE waits SET status='cancelled' WHERE task_id=? AND status IN ('waiting','ready')", (task_id,))

    @contextmanager
    def refresh_lock(self, connection_id, cancelled=None):
        folder = os.path.join(self.data_dir, "connection_locks")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, hashlib.sha256(connection_id.encode()).hexdigest() + ".lock")
        with open(path, "a+b") as stream:
            # Windows permits locking a byte beyond EOF. Do not initialize the
            # byte before locking: another process may already own that range.
            deadline = time.monotonic() + 120
            acquired = False
            try:
                while not acquired:
                    if cancelled and cancelled():
                        raise ConnectionError("cancelled")
                    stream.seek(0)
                    try:
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except (OSError, BlockingIOError):
                        if time.monotonic() >= deadline:
                            raise ConnectionError("conflict") from None
                        time.sleep(0.05)
                yield
            finally:
                if acquired:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
