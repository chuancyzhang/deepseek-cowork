"""Ordered runtime facts: one durable append writer and one SQLite projector."""
from collections import OrderedDict, deque
from concurrent.futures import Future
from contextlib import contextmanager, closing, ExitStack
import copy
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time

from core.platform_runtime import platform_runtime
from core.runtime_journal import RuntimeJournal, RuntimeJournalError

log = logging.getLogger("runtime.ledger")


class RuntimeLedger:
    INTERVAL = .016
    BATCH_RECORDS = 64
    BATCH_BYTES = 256 * 1024
    MAX_RECORDS = 4096
    MAX_BYTES = 8 * 1024 * 1024
    SEGMENT_BYTES = 16 * 1024 * 1024
    MAX_BACKLOG_BYTES = 64 * 1024 * 1024

    def __init__(self, storage):
        self.storage = storage
        self.root = Path(storage.db_path).parent / "runtime_wal"
        self.root.mkdir(exist_ok=True)
        self.condition = threading.Condition(threading.RLock())
        self.queues = OrderedDict()
        self.pending_count = self.pending_bytes = self.backlog_bytes = 0
        self.projection = deque()
        self.latest = {}
        self.error = None
        self.closing = False
        self.writer_done = False
        self.metrics = {"batches": 0, "records": 0, "projection_transactions": 0}
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runtime_records (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runtime_events (
                    key TEXT NOT NULL, sequence INTEGER NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(key, sequence));
                CREATE TABLE IF NOT EXISTS runtime_cursor (id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL);
                INSERT OR IGNORE INTO runtime_cursor VALUES(1,0);
            """)
            self.projected = self.durable = int(db.execute("SELECT sequence FROM runtime_cursor WHERE id=1").fetchone()[0])
        self._recover()
        self.handle = None
        self.segment = None
        self.writer = threading.Thread(target=self._write, name="runtime-wal", daemon=True)
        self.projector = threading.Thread(target=self._project, name="runtime-projector", daemon=True)
        self.writer.start()
        self.projector.start()

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.storage.db_path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            from core.sidebar_search import search_tokens
            db.create_function("sidebar_search_tokens", 1, search_tokens, deterministic=True)
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db

    def _apply(self, db, record):
        for op in record["operations"]:
            if op["kind"] == "record":
                db.execute("INSERT INTO runtime_records VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (op["key"], json.dumps(op["value"], ensure_ascii=False)))
            elif op["kind"] == "event":
                value = op["value"]
                db.execute("INSERT INTO runtime_events VALUES(?,?,?)", (op["key"], value["sequence"], json.dumps(value, ensure_ascii=False)))
            elif op["kind"] == "messages":
                cid = op["conversation_id"]
                if op["mode"] == "rewrite":
                    self.storage._invalidate_context_for_rewrite(db, cid, op["messages"])
                    db.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
                else:
                    for identity in op.get("removed", []):
                        db.execute("DELETE FROM messages WHERE conversation_id=? AND id=?", (cid, identity))
                    for position, identity in enumerate(op.get("retained_ids", [])):
                        db.execute("UPDATE messages SET position=? WHERE conversation_id=? AND id=?", (position, cid, identity))
                self.storage._upsert_conversation_in_connection(db, cid, title=op["title"], status=op["status"],
                    meta=op["meta"], touch_activity=bool(op["messages"]) or op["mode"] == "rewrite")
                for position, message in enumerate(op["messages"], op["start"]):
                    self.storage._insert_message_row(db, "messages", "conversation_id", cid, message, position, op["created_at"])
            elif op["kind"] == "submission":
                from core.desktop_store import DesktopStore
                DesktopStore.apply_submission(db, self.storage, op)
            elif op["kind"] == "run_status":
                db.execute("UPDATE desktop_runs SET status=?,error=?,updated_at=? WHERE id=?", (op["status"], op["error"], op["updated_at"], op["run_id"]))
            elif op["kind"] == "history_undo":
                db.execute("INSERT INTO desktop_view_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,revision=revision+1",
                           (op["key"], json.dumps(op["value"], ensure_ascii=False)))
            elif op["kind"] == "agent_record":
                value = op["value"]
                columns = list(value)
                db.execute("INSERT INTO agents (" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) +
                           ") ON CONFLICT(id) DO UPDATE SET " + ",".join(key + "=excluded." + key for key in columns if key not in {"id", "created_at"}),
                           tuple(value.values()))
            elif op["kind"] == "agent_input":
                self.storage._enqueue_agent_input(db, op["agent_id"], op["content"], op["message_id"], op["source"])
            elif op["kind"] == "agent_claim":
                self.storage._claim_agent_input(db, op["agent_id"], op["run_id"])
            elif op["kind"] == "agent_settle":
                self.storage._settle_agent_input(db, op["agent_id"], op["run_id"], op["outcome"])
            elif op["kind"] == "agent_messages":
                self.storage._merge_agent_messages(db, op["agent_id"], op["messages"], op["run_id"])
            elif op["kind"] == "context_window":
                self.storage._commit_context_window(db, op["conversation_id"], op["payload"], now=op["accepted_at"])
            else:
                raise RuntimeJournalError("Unknown runtime operation: " + op["kind"])
        db.execute("UPDATE runtime_cursor SET sequence=? WHERE id=1", (record["sequence"],))

    def _recover(self):
        paths = sorted(self.root.glob("*.jsonl"))
        previous = 0
        for index, path in enumerate(paths):
            pending = []
            with path.open("rb+") as handle:
                while True:
                    start = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        if index != len(paths) - 1:
                            raise RuntimeJournalError(f"运行日志中间段不完整：{path.name}")
                        tail = path.with_suffix(".incomplete")
                        with tail.open("wb") as saved:
                            saved.write(line)
                            platform_runtime.sync_file(saved)
                        handle.truncate(start)
                        platform_runtime.sync_file(handle)
                        log.warning("wal_incomplete_tail segment=%s bytes=%s", path.name, len(line))
                        break
                    envelope = json.loads(line)
                    record = envelope["payload"]
                    if envelope.get("journal_version") != 3 or envelope.get("checksum") != RuntimeJournal.checksum(record):
                        raise RuntimeJournalError(f"运行日志校验失败：{path.name}:{start}")
                    sequence = record["sequence"]
                    if previous and sequence != previous + 1:
                        raise RuntimeJournalError(f"运行日志顺序不连续：{path.name}:{start}")
                    previous = sequence
                    if sequence > self.projected:
                        if sequence != self.durable + 1:
                            raise RuntimeJournalError("数据库检查点之后缺少运行日志，原数据已保留。")
                        pending.append(record)
                        self.durable = sequence
                    if len(pending) >= self.BATCH_RECORDS:
                        with self.connection() as db:
                            for record in pending:
                                self._apply(db, record)
                        self.projected = pending[-1]["sequence"]
                        pending.clear()
            if pending:
                with self.connection() as db:
                    for record in pending:
                        self._apply(db, record)
                self.projected = pending[-1]["sequence"]
        log.info("wal_recovered cursor=%s segments=%s", self.projected, len(paths))

    def append(self, session_id, operations):
        operations = copy.deepcopy(operations)
        size = len(json.dumps(operations, ensure_ascii=False).encode("utf-8"))
        future = Future()
        with self.condition:
            while ((self.pending_count and (self.pending_count >= self.MAX_RECORDS or self.pending_bytes + size > self.MAX_BYTES))
                   or self.backlog_bytes >= self.MAX_BACKLOG_BYTES):
                self._check()
                self.condition.wait()
            self._check()
            self.queues.setdefault(session_id, deque()).append((operations, size, future))
            self.pending_count += 1
            self.pending_bytes += size
            self.condition.notify_all()
        return future.result()

    def _check(self):
        if self.error:
            raise RuntimeJournalError("运行保存失败，已持久化内容保留：" + str(self.error)) from self.error
        if self.closing:
            raise RuntimeJournalError("运行存储正在关闭。")

    def barrier(self, cursor=None):
        with self.condition:
            target = self.durable if cursor is None else cursor
            while self.projected < target:
                if self.error:
                    raise RuntimeJournalError("运行记录尚未完成整理：" + str(self.error)) from self.error
                self.condition.wait()
            return target

    def record(self, key, default=None):
        with self.condition:
            item = self.latest.get(key)
            if item:
                return copy.deepcopy(item[1])
            with self.connection() as db:
                row = db.execute("SELECT value FROM runtime_records WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else copy.deepcopy(default)

    def records(self, prefix):
        self.barrier()
        with self.connection() as db:
            return {row[0]: json.loads(row[1]) for row in db.execute(
                "SELECT key,value FROM runtime_records WHERE substr(key,1,?)=?", (len(prefix), prefix))}

    def events(self, key, after=0):
        self.barrier()
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM runtime_events WHERE key=? AND sequence>? ORDER BY sequence", (key, after))]

    def _fail(self, error, batch=()):
        log.exception("wal_failed durable=%s projected=%s", self.durable, self.projected)
        with self.condition:
            self.error = error
            for _, _, future in [*batch, *(item for queue in self.queues.values() for item in queue)]:
                if not future.done():
                    future.set_exception(error)
            self.queues.clear()
            self.condition.notify_all()

    def _new_segment(self, sequence):
        if self.handle:
            self.handle.close()
        self.segment = self.root / f"{sequence:020d}.jsonl"
        temporary = self.segment.with_suffix(".new")
        with temporary.open("wb") as handle:
            platform_runtime.sync_file(handle)
        platform_runtime.replace(temporary, self.segment)
        self.handle = self.segment.open("ab")

    def _write(self):
        batch = []
        try:
            while True:
                with self.condition:
                    while not self.queues:
                        if self.closing or self.error:
                            return
                        self.condition.wait()
                    deadline = time.monotonic() + self.INTERVAL
                    while self.pending_count < self.BATCH_RECORDS and self.pending_bytes < self.BATCH_BYTES and not self.closing:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.condition.wait(remaining)
                    batch, size = [], 0
                    while self.queues and len(batch) < self.BATCH_RECORDS:
                        session, queue = self.queues.popitem(last=False)
                        item = queue.popleft()
                        batch.append(item)
                        size += item[1]
                        if queue:
                            self.queues[session] = queue
                        if size >= self.BATCH_BYTES:
                            break
                    self.pending_count -= len(batch)
                    self.pending_bytes -= size
                    self.condition.notify_all()
                records = [{"sequence": self.durable + i + 1, "operations": item[0]} for i, item in enumerate(batch)]
                raw = b"".join((json.dumps({"journal_version": 3, "checksum": RuntimeJournal.checksum(record), "payload": record},
                                         ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8") for record in records)
                if self.handle is None or self.handle.tell() >= self.SEGMENT_BYTES:
                    self._new_segment(records[0]["sequence"])
                self.handle.write(raw)
                platform_runtime.sync_file(self.handle)
                with self.condition:
                    for record in records:
                        for op in record["operations"]:
                            if op["kind"] == "record":
                                self.latest[op["key"]] = (record["sequence"], op["value"])
                    self.durable = records[-1]["sequence"]
                    self.projection.append((records, len(raw)))
                    self.backlog_bytes += len(raw)
                    self.metrics["batches"] += 1
                    self.metrics["records"] += len(records)
                    self.condition.notify_all()
                for record, item in zip(records, batch):
                    item[2].set_result(record["sequence"])
                batch = []
        except Exception as error:
            self._fail(error, batch)
        finally:
            if self.handle:
                self.handle.close()
            with self.condition:
                self.writer_done = True
                self.condition.notify_all()

    def _project(self):
        try:
            while True:
                with self.condition:
                    while not self.projection:
                        if self.writer_done or self.error:
                            return
                        self.condition.wait()
                    records, size = self.projection.popleft()
                with self.connection() as db:
                    for record in records:
                        self._apply(db, record)
                with self.condition:
                    self.projected = records[-1]["sequence"]
                    for record in records:
                        for op in record["operations"]:
                            current = self.latest.get(op.get("key"))
                            if current and current[0] <= self.projected:
                                self.latest.pop(op["key"], None)
                    self.backlog_bytes -= size
                    self.metrics["projection_transactions"] += 1
                    self.condition.notify_all()
                # Only closed segments wholly covered by this SQLite commit.
                paths = sorted(self.root.glob("*.jsonl"))
                for old, following in zip(paths, paths[1:]):
                    if old != self.segment and int(following.stem) <= self.projected + 1:
                        old.unlink()
        except Exception as error:
            self._fail(error)

    def close(self):
        with self.condition:
            self.closing = True
            self.condition.notify_all()
        self.writer.join()
        self.projector.join()
        log.info("wal_closed durable=%s projected=%s metrics=%s", self.durable, self.projected, self.metrics)
        if self.error:
            raise RuntimeJournalError(str(self.error)) from self.error


class LedgerJournal(RuntimeJournal):
    """The existing journal contract backed by the shared durable log."""
    def __init__(self, ledger):
        self.ledger = ledger
        self.root = str(ledger.root)
        self.legacy_root = platform_runtime.filesystem_path(ledger.root.parent / "runtime_journal_v2")
        self.retain_committed_events = True
        self._diagnostic_context = threading.local()
        self._transaction = threading.local()
        self._sequences = {}

    def stage(self, operation):
        operations = getattr(self._transaction, "operations", None)
        if operations is None:
            self.ledger.append(operation.get("conversation_id", ""), [operation])
        else:
            operations.append(copy.deepcopy(operation))

    def _legacy_record(self, key, default=None):
        path = self.legacy_root / key
        if not path.is_file():
            return default
        if path.suffix == ".json":
            return RuntimeJournal._read(self, str(path), default)
        value = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    value.update(self._decode_jsonl_payload(line, str(path), "legacy record"))
        return value or default

    def _session_dir(self, session_id):
        return "sessions/" + self._session_key(session_id)

    def _category_dir(self, session_id, category):
        return self._session_dir(session_id) + "/" + category

    @contextmanager
    def session_lock(self, session_id, run_id=""):
        with self.session_locks([session_id]):
            yield

    @contextmanager
    def session_locks(self, session_ids):
        with ExitStack() as locks:
            for session_id in sorted(set(session_ids)):
                locks.enter_context(self._thread_lock(self.root + "/" + self._session_key(session_id)))
            nested = getattr(self._transaction, "operations", None)
            if nested is not None:
                yield
                return
            operations = self._transaction.operations = []
            try:
                yield
                if operations:
                    self.ledger.append(session_ids[0], operations)
                    for op in operations:
                        if op["kind"] == "event":
                            self._sequences[op["key"]] = op["value"]["sequence"]
            finally:
                self._transaction.operations = None

    def _read(self, path, default=None):
        key = str(path).replace("\\", "/")
        for op in reversed(getattr(self._transaction, "operations", None) or []):
            if op["kind"] == "record" and op["key"] == key:
                return copy.deepcopy(op["value"])
        current = self.ledger.record(key)
        legacy = self._legacy_record(key)
        if legacy is not None and (current is None or legacy.get("updated_at", 0) > current.get("updated_at", 0)):
            return legacy
        return current if current is not None else default

    def _atomic_write(self, path, payload):
        op = {"kind": "record", "key": str(path).replace("\\", "/"), "value": copy.deepcopy(payload)}
        operations = getattr(self._transaction, "operations", None)
        if operations is None:
            self.ledger.append(payload.get("session_id", ""), [op])
        else:
            operations.append(op)

    def _list_records(self, directory):
        prefix = str(directory).replace("\\", "/") + "/"
        records = self.ledger.records(prefix)
        for op in getattr(self._transaction, "operations", None) or []:
            if op["kind"] == "record" and op["key"].startswith(prefix):
                records[op["key"]] = op["value"]
        names = {key.rsplit("/", 1)[-1] for key in records}
        legacy = self.legacy_root / str(directory).replace("\\", "/")
        if legacy.is_dir():
            names.update(path.name for path in legacy.iterdir())
        return sorted(names)

    def scan_manifests(self):
        keys = {key for key in self.ledger.records("sessions/") if key.endswith("/manifest.json")}
        if (self.legacy_root / "sessions").is_dir():
            keys.update(path.relative_to(self.legacy_root).as_posix()
                        for path in (self.legacy_root / "sessions").glob("*/manifest.json"))
        manifests, errors = [], []
        for key in sorted(keys):
            try:
                manifests.append(self._read(key))
            except Exception as error:
                errors.append({"path": key, "error": str(error), "source": "runtime_manifest"})
        return manifests, errors

    def _last_event_sequence_unlocked(self, session_id, run_id):
        key = self._event_path(session_id, run_id).replace("\\", "/")
        if key not in self._sequences:
            with self.ledger.connection() as db:
                self._sequences[key] = db.execute("SELECT COALESCE(MAX(sequence),0) FROM runtime_events WHERE key=?", (key,)).fetchone()[0]
            legacy_path = str(self.legacy_root / key)
            tail = self._last_nonempty_line(legacy_path)
            if tail:
                self._sequences[key] = max(self._sequences[key], int(self._decode_jsonl_payload(tail, legacy_path, "event")["sequence"]))
        pending = [op["value"]["sequence"] for op in getattr(self._transaction, "operations", None) or [] if op["kind"] == "event" and op["key"] == key]
        return max([self._sequences[key], *pending])

    def append_events(self, session_id, run_id, entries):
        entries = list(entries)
        if not entries:
            return []
        started = time.monotonic()
        key = self._event_path(session_id, run_id).replace("\\", "/")
        with self.session_lock(session_id, run_id):
            if self._read(self._record_path(session_id, "runs", run_id)) is None:
                raise RuntimeJournalError("runtime run not found: " + run_id)
            sequence = self._last_event_sequence_unlocked(session_id, run_id)
            events = [{"sequence": sequence + i + 1, "type": entry["type"], "payload": entry.get("payload") or {},
                       "provider_sequence": entry.get("provider_sequence"), "created_at": entry.get("created_at", time.time())}
                      for i, entry in enumerate(entries)]
            self._transaction.operations.extend({"kind": "event", "key": key, "value": event} for event in events)
        self._diagnostic_context.append_metrics = {"persist_ms": (time.monotonic()-started)*1000, "batch_events": len(events)}
        return events

    def _append_jsonl(self, path, payload):
        key = str(path).replace("\\", "/")
        if "/events/" in key:
            self._transaction.operations.append({"kind": "event", "key": key, "value": copy.deepcopy(payload)})
        else:
            self._atomic_write(path, {**(self._read(path) or {}), **payload})

    def _read_events_unlocked(self, session_id, run_id, starting_after=0):
        key = self._event_path(session_id, run_id).replace("\\", "/")
        events = {}
        path = self.legacy_root / key
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        event = self._decode_jsonl_payload(line, str(path), "event")
                        if event["sequence"] > int(starting_after or 0):
                            events[event["sequence"]] = event
        for event in self.ledger.events(key, int(starting_after or 0)):
            previous = events.get(event["sequence"])
            if previous is not None and previous != event:
                raise RuntimeJournalError("原运行记录与新版事件序号冲突，请查看诊断日志。")
            events[event["sequence"]] = event
        return [events[sequence] for sequence in sorted(events)]

    def _read_attempt_unlocked(self, session_id, attempt_id):
        return self._read(self._attempt_path(session_id, attempt_id))
