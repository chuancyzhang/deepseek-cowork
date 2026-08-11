import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict

from .runtime_journal import RuntimeJournal


JOURNAL_VERSION = 1


class ChatRecoveryJournal:
    """Durable latest-snapshot journal for chat saves awaiting SQLite acknowledgement."""

    def __init__(self, history_dir, runtime_journal=None):
        self.directory = os.path.join(os.path.abspath(history_dir), "pending_chat_saves")
        os.makedirs(self.directory, exist_ok=True)
        self.runtime_journal = runtime_journal or RuntimeJournal(history_dir)

    @staticmethod
    def _safe_session_name(session_id):
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    def _path_for_session(self, session_id):
        return os.path.join(self.directory, f"{self._safe_session_name(session_id)}.json")

    @staticmethod
    def _payload_for_request(request):
        payload = asdict(request)
        payload["revision"] = max(0, int(payload.get("revision") or 0))
        payload["ready_at"] = 0.0
        return payload

    @staticmethod
    def _checksum(payload):
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def record(self, request):
        payload = self._payload_for_request(request)
        envelope = {
            "journal_version": JOURNAL_VERSION,
            "checksum": self._checksum(payload),
            "payload": payload,
        }
        target = self._path_for_session(payload.get("session_id"))
        fd, temp_path = tempfile.mkstemp(
            prefix=".pending-",
            suffix=".tmp",
            dir=self.directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        return payload["revision"]

    def acknowledge(self, session_id, revision):
        target = self._path_for_session(session_id)
        if not os.path.exists(target):
            return False
        envelope = self._read_envelope(target)
        payload = envelope["payload"]
        if str(payload.get("session_id") or "") != str(session_id or ""):
            raise ValueError("recovery journal session mismatch")
        if int(payload.get("revision") or 0) > int(revision or 0):
            return False
        os.unlink(target)
        return True

    def _read_envelope(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            envelope = json.load(handle)
        if int(envelope.get("journal_version") or 0) != JOURNAL_VERSION:
            raise ValueError("unsupported recovery journal version")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("recovery journal payload is invalid")
        if envelope.get("checksum") != self._checksum(payload):
            raise ValueError("recovery journal checksum mismatch")
        return envelope

    def recover_into(self, storage):
        recovered = []
        errors = []
        try:
            manifests = self.runtime_journal.list_manifests()
        except Exception as exc:
            manifests = []
            errors.append(
                {
                    "path": self.runtime_journal.root,
                    "error": f"runtime manifest scan failed: {exc}",
                }
            )
        for manifest in manifests:
            pending = manifest.get("pending_commit") if isinstance(manifest, dict) else None
            if not isinstance(pending, dict):
                continue
            session_id = str(manifest.get("session_id") or "").strip()
            run_id = str(pending.get("run_id") or "").strip()
            try:
                if not session_id or not run_id:
                    raise ValueError("runtime pending commit has no session_id or run_id")
                messages = [
                    item for item in (pending.get("messages") or []) if isinstance(item, dict)
                ]
                expected_hash = str(pending.get("messages_hash") or "")
                actual_hash = RuntimeJournal.messages_hash(messages)
                if not expected_hash or expected_hash != actual_hash:
                    raise ValueError("runtime pending commit checksum mismatch")
                storage.save_conversation_safely(
                    session_id,
                    messages,
                    title=pending.get("title") or "新任务",
                    status=pending.get("status") or "active",
                    meta=pending.get("meta") or {},
                )
                acknowledged = self.runtime_journal.acknowledge_commit(
                    session_id,
                    run_id,
                    storage.get_messages(session_id),
                )
                if not acknowledged:
                    raise RuntimeError(
                        "runtime pending commit did not match the recovered SQLite snapshot"
                    )
                recovered.append(session_id)
            except Exception as exc:
                errors.append(
                    {
                        "path": os.path.join(
                            self.runtime_journal.root,
                            "sessions",
                            hashlib.sha256(session_id.encode("utf-8")).hexdigest()
                            if session_id
                            else "unknown",
                            "manifest.json",
                        ),
                        "error": str(exc),
                    }
                )
        for name in sorted(os.listdir(self.directory)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.directory, name)
            try:
                payload = self._read_envelope(path)["payload"]
                session_id = str(payload.get("session_id") or "").strip()
                if not session_id:
                    raise ValueError("recovery journal has no session_id")
                messages = list(payload.get("messages") or [])
                owners = storage.get_message_owners(
                    [
                        message.get("id")
                        for message in messages
                        if isinstance(message, dict)
                    ]
                )
                remapped_messages = []
                for message in messages:
                    if not isinstance(message, dict):
                        remapped_messages.append(message)
                        continue
                    normalized_message = dict(message)
                    message_id = str(normalized_message.get("id") or "").strip()
                    owner = owners.get(message_id)
                    if message_id and owner and owner != session_id:
                        replacement = uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"deepseek-cowork-recovery:{session_id}:{message_id}",
                        ).hex
                        meta = (
                            dict(normalized_message.get("meta") or {})
                            if isinstance(normalized_message.get("meta"), dict)
                            else {}
                        )
                        meta["recovered_original_message_id"] = message_id
                        normalized_message["meta"] = meta
                        normalized_message["id"] = replacement
                    remapped_messages.append(normalized_message)
                messages = remapped_messages
                if messages and isinstance(messages[-1], dict):
                    last_message = dict(messages[-1])
                    last_meta = (
                        dict(last_message.get("meta") or {})
                        if isinstance(last_message.get("meta"), dict)
                        else {}
                    )
                    if last_meta.get("recovery_checkpoint"):
                        messages = messages[:-1]
                        run_id = str(last_message.get("id") or uuid.uuid4().hex)
                        self.runtime_journal.begin_run(
                            session_id,
                            run_id,
                            turn_id=last_meta.get("active_turn_id") or "",
                            writer_owner="recovery:v1",
                            base_messages=messages,
                            status="interrupted",
                            extra={
                                "draft_content": str(last_message.get("content") or ""),
                                "draft_reasoning": str(
                                    last_message.get("reasoning")
                                    or last_message.get("reasoning_content")
                                    or ""
                                ),
                                "legacy_recovery_checkpoint": True,
                                "finished_at": time.time(),
                            },
                        )
                        self.runtime_journal.append_event(
                            session_id,
                            run_id,
                            "legacy_checkpoint_recovered",
                            {
                                "content_length": len(str(last_message.get("content") or "")),
                            },
                        )
                status = str(payload.get("status") or "draft")
                if status == "running":
                    status = "interrupted"
                storage.save_conversation_safely(
                    session_id,
                    messages,
                    title=payload.get("title") or "新任务",
                    status=status,
                    meta=payload.get("meta") or {},
                )
                os.unlink(path)
                if session_id not in recovered:
                    recovered.append(session_id)
            except Exception as exc:
                errors.append({"path": path, "error": str(exc)})
        return recovered, errors
