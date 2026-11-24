"""Display-only, bounded file history. Never participates in provider messages.

Capture runs inside the existing file mutation lock, not around a shell command.
Metadata survives body eviction. Attachments and deliverables are never evicted.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import base64
import difflib
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid
import zlib

log = logging.getLogger(__name__)
FILE_LIMIT = 2 * 1024 * 1024
TURN_LIMIT = 16 * 1024 * 1024
TOTAL_LIMIT = 256 * 1024 * 1024
_owner = ContextVar("turn_file_owner", default=None)
_capturing = ContextVar("turn_file_paths", default=frozenset())


def attachment_root(config, cid):
    base = (Path(config.get_chat_history_dir()) / "attachments").resolve()
    target = (base / cid / "uploads").resolve()
    if target.parent.parent != base:
        raise ValueError("无效的附件所属对话。")
    return target


def _snapshot(path):
    try:
        target = Path(path)
        if not target.exists():
            return {"exists": False, "hash": "missing", "data": ""}
        if not target.is_file():
            return {"exists": True, "hash": "directory:" + str(target.stat().st_mtime_ns), "reason": "directory"}
        size = target.stat().st_size
        with target.open("rb") as handle:
            raw = handle.read(FILE_LIMIT + 1)
        if len(raw) > FILE_LIMIT:
            return {"exists": True, "size": size, "reason": "file_limit"}
        return {"exists": True, "size": len(raw), "hash": hashlib.sha256(raw).hexdigest(),
                "data": base64.b64encode(raw).decode("ascii")}
    except OSError:
        return {"exists": True, "reason": "read_failed"}


def _text(snapshot):
    if "data" not in snapshot:
        return None
    raw = base64.b64decode(snapshot["data"])
    from core.filesystem_ops import decode_text_bytes, TextFileCodecError
    try:
        text = decode_text_bytes(raw)[0]
        return None if "\x00" in text else text
    except TextFileCodecError:
        return None


class TurnFileStore:
    def __init__(self, history_dir, *, total_limit=TOTAL_LIMIT, turn_limit=TURN_LIMIT):
        root = Path(history_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "turn_files.sqlite"
        self.total_limit, self.turn_limit = total_limit, turn_limit
        with self.connect() as db:
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            db.execute("CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, cid TEXT NOT NULL, run TEXT NOT NULL, path TEXT NOT NULL, metadata TEXT NOT NULL, body BLOB, ended INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, UNIQUE(cid,run,path))")
            db.execute("CREATE TABLE IF NOT EXISTS refs (cid TEXT NOT NULL, message TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(cid,message))")
            db.execute("CREATE INDEX IF NOT EXISTS file_runs ON files(cid,run)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def references(self, cid, message, files):
        with self.connect() as db:
            row = db.execute("SELECT value FROM refs WHERE cid=? AND message=?", (cid, message)).fetchone()
            saved = json.loads(row[0]) if row else []
            merged = {os.path.normcase(item["path"]): item for item in saved}
            merged.update({os.path.normcase(item["path"]): item for item in files})
            result = list(merged.values())
            if result != saved:
                db.execute("INSERT OR REPLACE INTO refs VALUES(?,?,?)", (cid, message, json.dumps(result, ensure_ascii=False)))
            return result

    def begin(self, cid, run, root, path, before):
        path = str(Path(path).resolve())
        key = os.path.normcase(path)
        with self.connect() as db:
            previous = db.execute("SELECT * FROM files WHERE cid=? AND run=? AND path=?", (cid, run, key)).fetchone()
            previous = dict(previous) if previous else None
            record_id = previous["id"] if previous else uuid.uuid4().hex
            try:
                name = Path(path).relative_to(Path(root).resolve()).as_posix()
            except ValueError:
                name = path
            metadata = {"id": record_id, "run_id": run, "path": path, "name": name,
                        "created_at": previous["created"] if previous else time.time(),
                        "state": "pending", "status": "modified", "added": None, "removed": None}
            db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)", (record_id, cid, run, key,
                       json.dumps(metadata, ensure_ascii=False), previous["body"] if previous else None,
                       0, metadata["created_at"]))
        log.debug("turn_file_capture_started session=%s run=%s record=%s", cid, run, record_id)
        return {"cid": cid, "run": run, "previous": previous, "metadata": metadata, "before": before}

    def cancel(self, token):
        previous, meta = token["previous"], token["metadata"]
        with self.connect() as db:
            if previous:
                db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)", tuple(previous[key] for key in ("id", "cid", "run", "path", "metadata", "body", "ended", "created")))
            else:
                db.execute("DELETE FROM files WHERE id=?", (meta["id"],))

    def complete(self, token, after):
        before, previous, meta = token["before"], token["previous"], token["metadata"]
        if before.get("hash") and before.get("hash") == after.get("hash"):
            self.cancel(token)
            return
        baseline = before
        continuity = True
        if previous:
            prior = json.loads(previous["metadata"])
            continuity = bool(before.get("hash") and prior.get("after_hash") == before["hash"])
            baseline = json.loads(zlib.decompress(previous["body"]))["before"] if previous["body"] else {"exists": True, "reason": "baseline_missing"}
        state = "available" if continuity else "baseline_missing"
        if not continuity:
            baseline = {"exists": True, "reason": "baseline_missing"}
        if continuity and baseline.get("hash") and baseline["hash"] == after.get("hash"):
            with self.connect() as db:
                db.execute("DELETE FROM files WHERE id=?", (meta["id"],))
            log.info("turn_file_net_change_reverted session=%s run=%s record=%s", token["cid"], token["run"], meta["id"])
            return
        for snapshot in (baseline, after):
            if snapshot.get("reason"):
                state = snapshot["reason"]
        old, new = _text(baseline), _text(after)
        meta.update(state=state, after_hash=after.get("hash"),
                    status="deleted" if not after["exists"] else "added" if not baseline["exists"] else "modified")
        patch = ""
        if old is not None and new is not None:
            # Bound pathological diffs as well as bytes: repetitive long files can be quadratic.
            old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
            if len(old_lines) + len(new_lines) <= 20000:
                diff = list(difflib.unified_diff(old_lines, new_lines, fromfile="before/" + meta["name"], tofile="after/" + meta["name"], n=3))
                meta["added"] = sum(line.startswith("+") for line in diff[2:])
                meta["removed"] = sum(line.startswith("-") for line in diff[2:])
                patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
            else:
                meta["state"] = "diff_limit"
        elif state == "available":
            meta["state"] = "binary"
        body = zlib.compress(json.dumps({"before": baseline, "after": after, "patch": patch}).encode(), level=3)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT COALESCE(SUM(length(body)),0) FROM files WHERE cid=? AND run=? AND id!=?", (token["cid"], token["run"], meta["id"])).fetchone()[0]
            if used + len(body) > self.turn_limit:
                body, meta["state"] = None, "turn_limit"
            self._evict(db, reserve=len(body or b""), exclude=meta["id"])
            total = db.execute("SELECT COALESCE(SUM(length(body)),0) FROM files WHERE id!=?", (meta["id"],)).fetchone()[0]
            if total + len(body or b"") > self.total_limit:
                body, meta["state"] = None, "total_limit"
            db.execute("UPDATE files SET metadata=?,body=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), body, meta["id"]))
        log.info("turn_file_capture_completed session=%s run=%s record=%s state=%s", token["cid"], token["run"], meta["id"], meta["state"])

    def _evict(self, db, reserve=0, exclude=""):
        total = db.execute("SELECT COALESCE(SUM(length(body)),0) FROM files WHERE id!=?", (exclude,)).fetchone()[0]
        for row in db.execute("SELECT id,metadata,length(body) AS size FROM files WHERE ended=1 AND body IS NOT NULL AND id!=? ORDER BY created,id", (exclude,)).fetchall():
            if total + reserve <= self.total_limit:
                break
            meta = json.loads(row["metadata"])
            meta["state"] = "purged"
            db.execute("UPDATE files SET body=NULL,metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), row["id"]))
            total -= row["size"]
            log.info("turn_file_body_evicted record=%s bytes=%s", row["id"], row["size"])

    def finish(self, cid, run):
        with self.connect() as db:
            db.execute("UPDATE files SET ended=1 WHERE cid=? AND run=?", (cid, run))
            self._evict(db)
        with self.connect() as db:
            db.execute("PRAGMA incremental_vacuum(256)")

    def list(self, cid, runs=None):
        if runs is not None and not runs:
            return []
        with self.connect() as db:
            condition = " AND run IN (" + ",".join("?" for _ in runs) + ")" if runs is not None else ""
            rows = db.execute("SELECT metadata FROM files WHERE cid=?" + condition + " ORDER BY created,id", (cid, *(runs or []))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def read(self, cid, record_id):
        with self.connect() as db:
            row = db.execute("SELECT metadata,body FROM files WHERE cid=? AND id=?", (cid, record_id)).fetchone()
        if not row:
            raise ValueError("文件记录不存在或不属于当前对话。")
        meta = json.loads(row["metadata"])
        result = {**meta, "patch": "", "text": None}
        if row["body"] and meta["state"] not in {"purged", "pending"}:
            body = json.loads(zlib.decompress(row["body"]))
            saved = body["before"] if meta["status"] == "deleted" else body["after"]
            text = _text(saved)
            result.update(patch=body["patch"], text=text, binary=saved.get("data") if text is None else None)
        return result


@contextmanager
def capture_owner(config, cid, run, root):
    # Each background tool thread binds its immutable owner locally.
    value = (config, cid, run, root) if config and cid and run and root else None
    token = _owner.set(value)
    try:
        yield
    finally:
        _owner.reset(token)


@contextmanager
def capture_mutation(*paths, partial=False):
    owner = _owner.get()
    if not owner:
        yield
        return
    config, cid, run, root = owner
    store = TurnFileStore(config.get_chat_history_dir())
    tokens = []
    paths = [p for p in paths if p and os.path.normcase(str(Path(p).resolve())) not in _capturing.get()]
    scope = _capturing.set(_capturing.get() | {os.path.normcase(str(Path(p).resolve())) for p in paths})
    try:
        for path in dict.fromkeys(str(Path(p).resolve()) for p in paths if p):
            tokens.append(store.begin(cid, run, root, path, _snapshot(path)))
    except Exception:
        for token in tokens:
            store.cancel(token)
        log.exception("turn_file_capture_prepare_failed session=%s run=%s", cid, run)
        _capturing.reset(scope)
        raise
    try:
        yield
    except BaseException:
        for token in tokens:
            try:
                if not partial:
                    store.cancel(token)
            except Exception:
                log.exception("turn_file_capture_cancel_failed session=%s run=%s", cid, run)
        raise
    else:
        for token in tokens:
            try:
                store.complete(token, _snapshot(token["metadata"]["path"]))
            except Exception:
                # Mutation may already have committed. Keep the durable pending
                # record; never turn this into a retryable file-operation error.
                log.exception("turn_file_capture_finalize_failed session=%s run=%s record=%s", cid, run, token["metadata"]["id"])
    finally:
        _capturing.reset(scope)
