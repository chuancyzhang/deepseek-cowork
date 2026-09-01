import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

from .conversation_integrity import canonical_ledger_messages_hash


RUNTIME_JOURNAL_VERSION = 2
PENDING_COMMIT_FORMAT_APPEND_V1 = "ledger_append_v1"
RUN_TERMINAL_STATUSES = {"completed", "failed", "interrupted", "cancelled"}
RUN_NONTERMINAL_STATUSES = {"running", "finalizing"}
RUN_STATUSES = RUN_NONTERMINAL_STATUSES | RUN_TERMINAL_STATUSES


class RuntimeJournalError(RuntimeError):
    pass


class RuntimeJournal:
    """Versioned sidecar state for active chat runs.

    The journal deliberately lives outside chat_history.sqlite so older
    application versions can continue to read the conversation database.
    """

    _thread_locks_guard = threading.Lock()
    _thread_locks = {}

    def __init__(self, history_dir):
        self.root = os.path.join(os.path.abspath(history_dir), "runtime_journal_v2")
        os.makedirs(self.root, exist_ok=True)

    @staticmethod
    def _session_key(session_id):
        text = str(session_id or "").strip()
        if not text:
            raise ValueError("session_id is required")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_id(value, name):
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{name} is required")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_json(value):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def checksum(cls, value):
        return hashlib.sha256(cls._canonical_json(value).encode("utf-8")).hexdigest()

    @classmethod
    def messages_hash(cls, messages):
        return canonical_ledger_messages_hash(messages)

    @classmethod
    def legacy_snapshot_messages_hash(cls, messages):
        """Hash a v2 full snapshot exactly as already distributed builds did."""

        normalized = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            meta = message.get("meta")
            if isinstance(meta, dict):
                meta = dict(meta)
                meta.pop("sequence", None)
                if not meta:
                    meta = None
            normalized.append({
                "id": str(message.get("id") or ""),
                "role": str(message.get("role") or ""),
                "content": message.get("content"),
                "tool_calls": message.get("tool_calls"),
                "reasoning_content": (
                    message.get("reasoning_content")
                    if message.get("reasoning_content") is not None
                    else message.get("reasoning")
                ),
                "content_parts": message.get("content_parts"),
                "meta": meta,
                "result_obj": message.get("result_obj"),
                "token_count": message.get("token_count"),
                "tool_call_id": message.get("tool_call_id"),
            })
        return cls.checksum(normalized)

    def _session_dir(self, session_id):
        path = os.path.join(self.root, "sessions", self._session_key(session_id))
        os.makedirs(path, exist_ok=True)
        return path

    def _category_dir(self, session_id, category):
        path = os.path.join(self._session_dir(session_id), category)
        os.makedirs(path, exist_ok=True)
        return path

    def _manifest_path(self, session_id):
        return os.path.join(self._session_dir(session_id), "manifest.json")

    def _record_path(self, session_id, category, record_id):
        return os.path.join(
            self._category_dir(session_id, category),
            f"{self._safe_id(record_id, f'{category}_id')}.json",
        )

    def _jsonl_path(self, session_id, category, record_id):
        return os.path.join(
            self._category_dir(session_id, category),
            f"{self._safe_id(record_id, f'{category}_id')}.jsonl",
        )

    def _event_path(self, session_id, run_id):
        return self._jsonl_path(session_id, "events", run_id)

    def _attempt_path(self, session_id, attempt_id):
        return self._jsonl_path(session_id, "attempts", attempt_id)

    @staticmethod
    def _envelope(payload):
        return {
            "journal_version": RUNTIME_JOURNAL_VERSION,
            "checksum": RuntimeJournal.checksum(payload),
            "payload": payload,
        }

    def _atomic_write(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        envelope = self._envelope(payload)
        for attempt in range(3):
            temp_path = ""
            try:
                fd, temp_path = tempfile.mkstemp(
                    prefix=".runtime-",
                    suffix=".tmp",
                    dir=os.path.dirname(path),
                )
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(envelope, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
                return
            except PermissionError:
                try:
                    if temp_path:
                        os.unlink(temp_path)
                except OSError:
                    pass
                if attempt == 2:
                    raise
                time.sleep(0.05)
            except Exception:
                try:
                    if temp_path:
                        os.unlink(temp_path)
                except OSError:
                    pass
                raise

    def _read(self, path, default=None):
        if not os.path.isfile(path):
            return default
        with open(path, "r", encoding="utf-8") as handle:
            envelope = json.load(handle)
        if int(envelope.get("journal_version") or 0) != RUNTIME_JOURNAL_VERSION:
            raise RuntimeJournalError(f"unsupported runtime journal version: {path}")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeJournalError(f"invalid runtime journal payload: {path}")
        if envelope.get("checksum") != self.checksum(payload):
            raise RuntimeJournalError(f"runtime journal checksum mismatch: {path}")
        return payload

    def _decode_jsonl_payload(self, raw_line, path, record_type):
        try:
            envelope = json.loads(raw_line)
        except Exception as exc:
            raise RuntimeJournalError(
                f"invalid runtime {record_type} JSON: {path} | {exc}"
            ) from exc
        if int(envelope.get("journal_version") or 0) != RUNTIME_JOURNAL_VERSION:
            raise RuntimeJournalError(
                f"unsupported runtime {record_type} version: {path}"
            )
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or envelope.get("checksum") != self.checksum(payload):
            raise RuntimeJournalError(
                f"runtime {record_type} checksum mismatch: {path}"
            )
        return payload

    def _append_jsonl(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(self._canonical_json(self._envelope(payload)) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _last_nonempty_line(path):
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return ""
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            buffer = b""
            while position > 0:
                size = min(8192, position)
                position -= size
                handle.seek(position)
                buffer = handle.read(size) + buffer
                stripped = buffer.rstrip(b"\r\n")
                if b"\n" in stripped or position == 0:
                    raw = stripped.rsplit(b"\n", 1)[-1].rstrip(b"\r")
                    return raw.decode("utf-8")
        return ""

    @staticmethod
    def _process_role():
        return "daemon" if "--daemon" in sys.argv else "ui"

    def _write_error(
        self,
        *,
        operation,
        path,
        exc,
        run_id="",
        writer_owner="",
    ):
        return RuntimeJournalError(
            f"runtime journal write failed: operation={operation} path={path} "
            f"pid={os.getpid()} process_role={self._process_role()} "
            f"run_id={str(run_id or '')} writer_owner={str(writer_owner or '')} "
            f"errno={getattr(exc, 'errno', None)} "
            f"winerror={getattr(exc, 'winerror', None)} error={exc}"
        )

    @classmethod
    def _thread_lock(cls, path):
        with cls._thread_locks_guard:
            lock = cls._thread_locks.get(path)
            if lock is None:
                lock = threading.RLock()
                cls._thread_locks[path] = lock
            return lock

    @staticmethod
    def _lock_file(handle):
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock_file(handle):
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def session_lock(self, session_id):
        lock_path = os.path.join(self._session_dir(session_id), "session.lock")
        thread_lock = self._thread_lock(lock_path)
        with thread_lock:
            with open(lock_path, "a+b") as handle:
                self._lock_file(handle)
                try:
                    yield
                finally:
                    self._unlock_file(handle)

    def load_manifest(self, session_id):
        payload = self._read(self._manifest_path(session_id), default=None)
        if payload is not None:
            return payload
        return {
            "session_id": str(session_id),
            "revision": 0,
            "writer_owner": "",
            "sqlite_messages_hash": "",
            "pending_commit_run_id": "",
            "excluded_message_ids": [],
            "updated_at": 0.0,
        }

    def list_manifests(self):
        manifests, errors = self.scan_manifests()
        if errors:
            first = errors[0]
            raise RuntimeJournalError(
                f"runtime manifest scan failed: {first['path']} | {first['error']}"
            )
        return [
            {
                key: value
                for key, value in manifest.items()
                if key != "_runtime_manifest_path"
            }
            for manifest in manifests
        ]

    def scan_manifests(self):
        """Read every manifest independently and retain exact per-file failures."""
        sessions_dir = os.path.join(self.root, "sessions")
        if not os.path.isdir(sessions_dir):
            return [], []
        manifests = []
        errors = []
        for name in sorted(os.listdir(sessions_dir)):
            path = os.path.join(sessions_dir, name, "manifest.json")
            if not os.path.isfile(path):
                continue
            try:
                manifest = self._read(path)
                if not isinstance(manifest, dict):
                    raise RuntimeJournalError("runtime manifest must be a JSON object")
                manifest = dict(manifest)
                manifest["_runtime_manifest_path"] = path
                manifests.append(manifest)
            except Exception as exc:
                errors.append({"path": path, "error": str(exc), "source": "runtime_manifest"})
        return manifests, errors

    def quarantine_manifest_file(self, manifest_path, *, reason):
        """Atomically remove a malformed manifest from the active recovery scan."""

        source = os.path.abspath(str(manifest_path or ""))
        sessions_root = os.path.abspath(os.path.join(self.root, "sessions"))
        if (
            not source
            or os.path.basename(source) != "manifest.json"
            or os.path.dirname(os.path.dirname(source)) != sessions_root
        ):
            raise RuntimeJournalError(
                f"refusing to quarantine unexpected runtime manifest path: {source}"
            )
        if not os.path.isfile(source):
            return ""
        quarantined_at = time.time()
        quarantine_dir = os.path.join(self.root, "quarantined_manifests")
        os.makedirs(quarantine_dir, exist_ok=True)
        source_key = os.path.basename(os.path.dirname(source))
        destination = os.path.join(
            quarantine_dir,
            f"{source_key}.{int(quarantined_at * 1000)}.manifest.json.quarantined",
        )
        os.replace(source, destination)
        self._atomic_write(
            f"{destination}.meta.json",
            {
                "source_path": source,
                "quarantine_path": destination,
                "reason": str(reason or "malformed_runtime_manifest"),
                "quarantined_at": quarantined_at,
            },
        )
        return destination

    def update_manifest(self, session_id, patch=None, *, expected_revision=None):
        with self.session_lock(session_id):
            manifest = self.load_manifest(session_id)
            current_revision = int(manifest.get("revision") or 0)
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RuntimeJournalError(
                    f"runtime manifest revision conflict for {session_id}: "
                    f"expected {int(expected_revision)}, got {current_revision}"
                )
            if isinstance(patch, dict):
                manifest.update(patch)
            manifest["session_id"] = str(session_id)
            manifest["revision"] = current_revision + 1
            manifest["updated_at"] = time.time()
            self._atomic_write(self._manifest_path(session_id), manifest)
            return manifest

    def begin_run(
        self,
        session_id,
        run_id,
        *,
        turn_id="",
        writer_owner="",
        base_messages=None,
        status="running",
        extra=None,
    ):
        now = time.time()
        record = {
            "session_id": str(session_id),
            "run_id": str(run_id),
            "turn_id": str(turn_id or ""),
            "status": str(status or "running"),
            "writer_owner": str(writer_owner or ""),
            "base_messages_hash": self.messages_hash(base_messages or []),
            "base_message_ids": [
                str(message.get("id") or "")
                for message in (base_messages or [])
                if isinstance(message, dict)
            ],
            "last_event_sequence": 0,
            "draft_content": "",
            "draft_reasoning": "",
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
        }
        if isinstance(extra, dict):
            record.update(extra)
        with self.session_lock(session_id):
            manifest = self.load_manifest(session_id)
            active_run_id = str(manifest.get("active_run_id") or "")
            if active_run_id and active_run_id != str(run_id):
                active_record = self._read(
                    self._record_path(session_id, "runs", active_run_id),
                    default=None,
                )
                active_status = str((active_record or {}).get("status") or "")
                if active_status not in RUN_TERMINAL_STATUSES:
                    raise RuntimeJournalError(
                        f"session {session_id} already has active run {active_run_id}"
                    )
            current_owner = str(manifest.get("writer_owner") or "")
            requested_owner = str(writer_owner or current_owner)
            if current_owner and requested_owner and current_owner != requested_owner:
                if str(manifest.get("pending_commit_run_id") or ""):
                    raise RuntimeJournalError(
                        f"writer owner transfer blocked by pending commit for session {session_id}"
                    )
                if active_run_id:
                    active_record = self._read(
                        self._record_path(session_id, "runs", active_run_id),
                        default=None,
                    )
                    if str((active_record or {}).get("status") or "") not in RUN_TERMINAL_STATUSES:
                        raise RuntimeJournalError(
                            f"writer owner transfer blocked for active session {session_id}"
                        )
            record["writer_owner"] = requested_owner
            self._atomic_write(self._record_path(session_id, "runs", run_id), record)
            active_manifest_run_id = (
                ""
                if record.get("status") in RUN_TERMINAL_STATUSES
                else str(run_id)
            )
            manifest.update({
                "active_run_id": active_manifest_run_id,
                "writer_owner": requested_owner,
                "sqlite_messages_hash": record["base_messages_hash"],
            })
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = now
            self._atomic_write(self._manifest_path(session_id), manifest)
        return record

    def get_run(self, session_id, run_id):
        with self.session_lock(session_id):
            record = self._read(
                self._record_path(session_id, "runs", run_id),
                default=None,
            )
            if record is not None:
                record["last_event_sequence"] = self._last_event_sequence_unlocked(
                    session_id,
                    run_id,
                )
            return record

    def list_runs(self, session_id):
        """Return runtime records newest-first for terminal reconciliation."""

        directory = self._category_dir(session_id, "runs")
        records = []
        for filename in os.listdir(directory):
            if not filename.endswith(".json"):
                continue
            record = self._read(os.path.join(directory, filename), default=None)
            if isinstance(record, dict):
                records.append(record)
        return sorted(
            records,
            key=lambda item: float(item.get("updated_at") or 0.0),
            reverse=True,
        )

    def update_run(self, session_id, run_id, patch):
        with self.session_lock(session_id):
            path = self._record_path(session_id, "runs", run_id)
            record = self._read(path, default=None)
            if record is None:
                raise RuntimeJournalError(f"runtime run not found: {run_id}")
            record["last_event_sequence"] = self._last_event_sequence_unlocked(
                session_id,
                run_id,
            )
            incoming = dict(patch or {})
            if record.get("stop_requested"):
                incoming.pop("status", None)
                incoming.pop("terminal_error", None)
                incoming.pop("finished_at", None)
            current_status = str(record.get("status") or "running")
            incoming_status = str(incoming.get("status") or "").strip()
            if incoming_status:
                if incoming_status not in RUN_STATUSES:
                    raise RuntimeJournalError(
                        f"invalid runtime status transition target: {incoming_status}"
                    )
                if current_status in RUN_TERMINAL_STATUSES and incoming_status != current_status:
                    raise RuntimeJournalError(
                        f"terminal runtime status cannot be replaced: "
                        f"{current_status} -> {incoming_status}"
                    )
                if current_status == "finalizing" and incoming_status == "running":
                    raise RuntimeJournalError(
                        "runtime status cannot regress from finalizing to running"
                    )
            record.update(incoming)
            record["updated_at"] = time.time()
            if record.get("status") in RUN_TERMINAL_STATUSES:
                record["finished_at"] = record.get("finished_at") or time.time()
            self._atomic_write(path, record)
            if record.get("status") in RUN_TERMINAL_STATUSES:
                manifest = self.load_manifest(session_id)
                if str(manifest.get("active_run_id") or "") == str(run_id):
                    manifest["active_run_id"] = ""
                    manifest["revision"] = int(manifest.get("revision") or 0) + 1
                    manifest["updated_at"] = time.time()
                    self._atomic_write(self._manifest_path(session_id), manifest)
            return record

    def interrupt_run(self, session_id, run_id, *, reason="interrupted by user", patch=None):
        """Atomically make user interruption win over a late daemon commit."""

        with self.session_lock(session_id):
            path = self._record_path(session_id, "runs", run_id)
            record = self._read(path, default=None)
            if record is None:
                raise RuntimeJournalError(f"runtime run not found: {run_id}")
            if str(record.get("status") or "") in RUN_TERMINAL_STATUSES:
                return record
            record["last_event_sequence"] = self._last_event_sequence_unlocked(
                session_id,
                run_id,
            )
            record.update(dict(patch or {}))
            record.update({
                "status": "interrupted",
                "stop_requested": True,
                "terminal_error": str(reason or "interrupted by user"),
                "updated_at": time.time(),
                "finished_at": time.time(),
            })
            self._atomic_write(path, record)
            manifest = self.load_manifest(session_id)
            if str(manifest.get("active_run_id") or "") == str(run_id):
                manifest["active_run_id"] = ""
            if str(manifest.get("pending_commit_run_id") or "") == str(run_id):
                manifest.pop("pending_commit", None)
                manifest["pending_commit_run_id"] = ""
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.time()
            self._atomic_write(self._manifest_path(session_id), manifest)
            return record

    def append_event(self, session_id, run_id, event_type, payload=None, provider_sequence=None):
        with self.session_lock(session_id):
            run_path = self._record_path(session_id, "runs", run_id)
            record = self._read(run_path, default=None)
            if record is None:
                raise RuntimeJournalError(f"runtime run not found: {run_id}")
            sequence = self._last_event_sequence_unlocked(session_id, run_id) + 1
            event = {
                "sequence": sequence,
                "type": str(event_type or "event"),
                "payload": payload if isinstance(payload, dict) else {"value": payload},
                "provider_sequence": provider_sequence,
                "created_at": time.time(),
            }
            event_path = self._event_path(session_id, run_id)
            try:
                self._append_jsonl(event_path, event)
            except Exception as exc:
                raise self._write_error(
                    operation=f"append_{event['type']}",
                    path=event_path,
                    exc=exc,
                    run_id=run_id,
                    writer_owner=record.get("writer_owner"),
                ) from exc
            return event

    def read_events(self, session_id, run_id, starting_after=0):
        with self.session_lock(session_id):
            return self._read_events_unlocked(session_id, run_id, starting_after)

    def _read_events_unlocked(self, session_id, run_id, starting_after=0):
        path = self._event_path(session_id, run_id)
        if not os.path.isfile(path):
            return []
        events = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = self._decode_jsonl_payload(line, path, "event")
                if int(event.get("sequence") or 0) > int(starting_after or 0):
                    events.append(event)
        return events

    def _last_event_sequence_unlocked(self, session_id, run_id):
        path = self._event_path(session_id, run_id)
        line = self._last_nonempty_line(path)
        if not line:
            return 0
        event = self._decode_jsonl_payload(line, path, "event")
        sequence = int(event.get("sequence") or 0)
        if sequence < 1:
            raise RuntimeJournalError(f"invalid runtime event sequence: {path}")
        return sequence

    def record_attempt(self, session_id, attempt_id, payload):
        patch = dict(payload or {})
        patch.update({
            "session_id": str(session_id),
            "attempt_id": str(attempt_id),
            "updated_at": time.time(),
        })
        path = self._attempt_path(session_id, attempt_id)
        with self.session_lock(session_id):
            existing = self._read_attempt_unlocked(session_id, attempt_id) or {}
            patch.setdefault("created_at", existing.get("created_at") or time.time())
            try:
                self._append_jsonl(path, patch)
            except Exception as exc:
                manifest = self.load_manifest(session_id)
                raise self._write_error(
                    operation="append_provider_attempt",
                    path=path,
                    exc=exc,
                    run_id=patch.get("run_id") or existing.get("run_id"),
                    writer_owner=manifest.get("writer_owner"),
                ) from exc
            existing.update(patch)
            return existing

    def get_attempt(self, session_id, attempt_id):
        with self.session_lock(session_id):
            return self._read_attempt_unlocked(session_id, attempt_id)

    def _read_attempt_unlocked(self, session_id, attempt_id):
        path = self._attempt_path(session_id, attempt_id)
        if not os.path.isfile(path):
            return None
        record = {}
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                patch = self._decode_jsonl_payload(line, path, "provider attempt")
                if str(patch.get("session_id") or "") != str(session_id):
                    raise RuntimeJournalError(
                        f"runtime provider attempt session mismatch: {path}"
                    )
                if str(patch.get("attempt_id") or "") != str(attempt_id):
                    raise RuntimeJournalError(
                        f"runtime provider attempt id mismatch: {path}"
                    )
                record.update(patch)
        return record or None

    def record_tool(self, session_id, execution_id, payload):
        record = dict(payload or {})
        record.update({
            "session_id": str(session_id),
            "execution_id": str(execution_id),
            "updated_at": time.time(),
        })
        path = self._record_path(session_id, "tools", execution_id)
        with self.session_lock(session_id):
            existing = self._read(path, default={}) or {}
            existing.update(record)
            existing.setdefault("created_at", time.time())
            self._atomic_write(path, existing)
        return existing

    def get_tool(self, session_id, execution_id):
        return self._read(self._record_path(session_id, "tools", execution_id), default=None)

    def find_tool_execution(
        self,
        session_id,
        *,
        name,
        args_hash,
        statuses=None,
        committed=None,
    ):
        directory = self._category_dir(session_id, "tools")
        allowed = {str(item) for item in (statuses or []) if str(item)}
        newest = None
        for filename in os.listdir(directory):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(directory, filename)
            record = self._read(path, default=None)
            if not isinstance(record, dict):
                continue
            if str(record.get("name") or "") != str(name or ""):
                continue
            if str(record.get("args_hash") or "") != str(args_hash or ""):
                continue
            if allowed and str(record.get("status") or "") not in allowed:
                continue
            if committed is not None and bool(record.get("committed")) != bool(committed):
                continue
            if newest is None or float(record.get("updated_at") or 0) > float(newest.get("updated_at") or 0):
                newest = record
        return newest

    def mark_pending_commit(self, session_id, run_id, messages, *, title="", status="active", meta=None):
        canonical_messages = [
            item for item in (messages or []) if isinstance(item, dict)
        ]
        with self.session_lock(session_id):
            run = self._read(
                self._record_path(session_id, "runs", run_id),
                default=None,
            )
            if not isinstance(run, dict):
                raise RuntimeJournalError(f"runtime run not found: {run_id}")
            if run.get("stop_requested"):
                raise RuntimeJournalError(
                    f"pending commit rejected for interrupted run {run_id}"
                )
            base_message_ids = [
                str(message_id or "")
                for message_id in (run.get("base_message_ids") or [])
            ]
            if len(base_message_ids) > len(canonical_messages):
                raise RuntimeJournalError(
                    f"pending commit is shorter than its base for session {session_id}"
                )
            actual_base_ids = [
                str(message.get("id") or "")
                for message in canonical_messages[:len(base_message_ids)]
            ]
            if actual_base_ids != base_message_ids:
                raise RuntimeJournalError(
                    f"pending commit does not extend its base for session {session_id}"
                )
            actual_base_hash = self.messages_hash(
                canonical_messages[:len(base_message_ids)]
            )
            base_messages_hash = str(run.get("base_messages_hash") or "")
            if actual_base_hash != base_messages_hash:
                raise RuntimeJournalError(
                    f"pending commit base checksum mismatch for session {session_id}"
                )
            append_messages = canonical_messages[len(base_message_ids):]
            pending = {
                "format": PENDING_COMMIT_FORMAT_APPEND_V1,
                "run_id": str(run_id),
                "base_message_ids": base_message_ids,
                "base_messages_hash": base_messages_hash,
                "append_messages": append_messages,
                "append_messages_hash": self.messages_hash(append_messages),
                "title": str(title or ""),
                "status": str(status or "active"),
                "meta": dict(meta) if isinstance(meta, dict) and meta else None,
                "expected_messages_hash": self.messages_hash(canonical_messages),
                "created_at": time.time(),
            }
            manifest = self.load_manifest(session_id)
            manifest["pending_commit"] = pending
            manifest["pending_commit_run_id"] = str(run_id)
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.time()
            self._atomic_write(self._manifest_path(session_id), manifest)
            return manifest

    def acknowledge_commit(self, session_id, run_id, sqlite_messages):
        with self.session_lock(session_id):
            manifest = self.load_manifest(session_id)
            pending_run_id = str(manifest.get("pending_commit_run_id") or "")
            if pending_run_id and pending_run_id != str(run_id or ""):
                return False
            pending = manifest.get("pending_commit")
            sqlite_messages_hash = self.messages_hash(sqlite_messages or [])
            if isinstance(pending, dict):
                if pending.get("format") == PENDING_COMMIT_FORMAT_APPEND_V1:
                    expected_messages_hash = str(
                        pending.get("expected_messages_hash") or ""
                    )
                else:
                    expected_messages_hash = self.messages_hash(
                        pending.get("messages") or []
                    )
                if expected_messages_hash and expected_messages_hash != sqlite_messages_hash:
                    return False
            manifest.pop("pending_commit", None)
            manifest["pending_commit_run_id"] = ""
            manifest["sqlite_messages_hash"] = sqlite_messages_hash
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.time()
            self._atomic_write(self._manifest_path(session_id), manifest)
            tools_dir = self._category_dir(session_id, "tools")
            for filename in os.listdir(tools_dir):
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(tools_dir, filename)
                record = self._read(path, default=None)
                if not isinstance(record, dict):
                    continue
                if str(record.get("run_id") or "") != str(run_id or ""):
                    continue
                record["committed"] = True
                record["committed_at"] = time.time()
                record["updated_at"] = time.time()
                self._atomic_write(path, record)
            event_path = os.path.join(
                self._category_dir(session_id, "events"),
                f"{self._safe_id(run_id, 'run_id')}.jsonl",
            )
            try:
                os.unlink(event_path)
            except FileNotFoundError:
                pass
            return True

    def acknowledge_superseded_commit(
        self,
        session_id,
        run_id,
        expected_messages,
        sqlite_messages,
    ):
        """Clear a pending snapshot only after proving SQLite strictly extends it."""

        expected = [
            message for message in (expected_messages or []) if isinstance(message, dict)
        ]
        committed = [
            message for message in (sqlite_messages or []) if isinstance(message, dict)
        ]
        if len(committed) <= len(expected):
            return False
        expected_ids = [str(message.get("id") or "") for message in expected]
        committed_prefix = committed[:len(expected)]
        if [str(message.get("id") or "") for message in committed_prefix] != expected_ids:
            return False
        if self.messages_hash(committed_prefix) != self.messages_hash(expected):
            return False
        with self.session_lock(session_id):
            manifest = self.load_manifest(session_id)
            if str(manifest.get("pending_commit_run_id") or "") != str(run_id or ""):
                return False
            pending = manifest.get("pending_commit")
            if not isinstance(pending, dict):
                return False
            if pending.get("format") == PENDING_COMMIT_FORMAT_APPEND_V1:
                if str(pending.get("expected_messages_hash") or "") != self.messages_hash(expected):
                    return False
            else:
                legacy_messages = [
                    message
                    for message in (pending.get("messages") or [])
                    if isinstance(message, dict)
                ]
                if (
                    str(pending.get("messages_hash") or "")
                    != self.legacy_snapshot_messages_hash(legacy_messages)
                    or self.messages_hash(legacy_messages) != self.messages_hash(expected)
                ):
                    return False
            manifest.pop("pending_commit", None)
            manifest["pending_commit_run_id"] = ""
            manifest["sqlite_messages_hash"] = self.messages_hash(committed)
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.time()
            self._atomic_write(self._manifest_path(session_id), manifest)
            return True

    def quarantine_pending_commit(
        self,
        session_id,
        run_id,
        *,
        reason,
        committed_messages=None,
    ):
        """Move an irreconcilable pending snapshot out of the active manifest."""

        with self.session_lock(session_id):
            manifest = self.load_manifest(session_id)
            if str(manifest.get("pending_commit_run_id") or "") != str(run_id or ""):
                return None
            pending = manifest.get("pending_commit")
            if not isinstance(pending, dict):
                return None
            quarantined_at = time.time()
            quarantine_id = f"{run_id}:{int(quarantined_at * 1000)}"
            record = {
                "session_id": str(session_id),
                "run_id": str(run_id),
                "reason": str(reason or "history_divergence"),
                "pending_commit": pending,
                "committed_messages_hash": self.messages_hash(committed_messages or []),
                "quarantined_at": quarantined_at,
            }
            self._atomic_write(
                self._record_path(
                    session_id,
                    "quarantined_commits",
                    quarantine_id,
                ),
                record,
            )
            manifest.pop("pending_commit", None)
            manifest["pending_commit_run_id"] = ""
            manifest["last_quarantined_commit"] = {
                "run_id": str(run_id),
                "reason": record["reason"],
                "quarantined_at": quarantined_at,
            }
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = quarantined_at
            self._atomic_write(self._manifest_path(session_id), manifest)
            return record

    def record_quarantined_commit(
        self,
        session_id,
        run_id,
        pending_commit,
        *,
        reason,
        committed_messages=None,
    ):
        """Quarantine a malformed legacy commit that lacks a usable manifest key."""

        quarantined_at = time.time()
        quarantine_id = f"{run_id or 'unknown'}:{int(quarantined_at * 1000)}"
        record = {
            "session_id": str(session_id or "unknown"),
            "run_id": str(run_id or "unknown"),
            "reason": str(reason or "history_divergence"),
            "pending_commit": (
                dict(pending_commit) if isinstance(pending_commit, dict) else {}
            ),
            "committed_messages_hash": self.messages_hash(committed_messages or []),
            "quarantined_at": quarantined_at,
        }
        self._atomic_write(
            self._record_path(
                session_id or "unknown",
                "quarantined_commits",
                quarantine_id,
            ),
            record,
        )
        return record
