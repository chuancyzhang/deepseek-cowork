import hashlib
import json
import os
import re
import secrets
import time
import uuid


FAVORITE_DELIVERY_PROVIDER_FEISHU = "feishu"
FAVORITE_DELIVERY_PROVIDER_DINGTALK = "dingtalk"
FAVORITE_DELIVERY_PROVIDER_WECOM = "wecom"
FAVORITE_DELIVERY_PROVIDERS = {
    FAVORITE_DELIVERY_PROVIDER_FEISHU,
    FAVORITE_DELIVERY_PROVIDER_DINGTALK,
    FAVORITE_DELIVERY_PROVIDER_WECOM,
}

FAVORITE_DELIVERY_STATUS_PENDING = "pending"
FAVORITE_DELIVERY_STATUS_SENDING = "sending"
FAVORITE_DELIVERY_STATUS_RETRY_WAIT = "retry_wait"
FAVORITE_DELIVERY_STATUS_COMPLETED = "completed"
FAVORITE_DELIVERY_STATUS_PARTIAL = "partial"
FAVORITE_DELIVERY_STATUS_FAILED = "failed"
FAVORITE_DELIVERY_STATUS_UNKNOWN = "unknown"
FAVORITE_DELIVERY_STATUS_BLOCKED = "blocked"
FAVORITE_DELIVERY_TERMINAL_STATUSES = {
    FAVORITE_DELIVERY_STATUS_COMPLETED,
    FAVORITE_DELIVERY_STATUS_PARTIAL,
    FAVORITE_DELIVERY_STATUS_FAILED,
    FAVORITE_DELIVERY_STATUS_UNKNOWN,
    FAVORITE_DELIVERY_STATUS_BLOCKED,
}
FAVORITE_DELIVERY_RETRYABLE_STATUSES = {
    FAVORITE_DELIVERY_STATUS_PARTIAL,
    FAVORITE_DELIVERY_STATUS_FAILED,
    FAVORITE_DELIVERY_STATUS_UNKNOWN,
    FAVORITE_DELIVERY_STATUS_BLOCKED,
}

FAVORITE_DELIVERY_BINDING_TTL_SECONDS = 10 * 60
FAVORITE_DELIVERY_MAX_ATTEMPTS = 5
FAVORITE_DELIVERY_TEXT_CHUNK_CHARS = 3500
FAVORITE_DELIVERY_RETRY_DELAYS = (2, 10, 30, 120, 300)
FAVORITE_DELIVERY_BIND_COMMAND_RE = re.compile(r"^\s*绑定常用\s+(\d{6})\s*$")


def normalize_favorite_delivery(value):
    if value is None:
        return None
    source = value if isinstance(value, dict) else {}
    enabled = bool(source.get("enabled", False))
    binding_id = str(source.get("binding_id") or "").strip()
    if enabled and not binding_id:
        raise ValueError("启用企业消息投递前必须完成目标绑定。")
    return {
        "enabled": enabled,
        "binding_id": binding_id,
    }


def parse_favorite_binding_command(text):
    match = FAVORITE_DELIVERY_BIND_COMMAND_RE.fullmatch(str(text or ""))
    return match.group(1) if match else ""


def split_delivery_text(text, limit=FAVORITE_DELIVERY_TEXT_CHUNK_CHARS):
    value = str(text or "").strip()
    if not value:
        return []
    limit = max(256, int(limit or FAVORITE_DELIVERY_TEXT_CHUNK_CHARS))
    chunks = []
    remaining = value
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind("。", 0, limit + 1)
            if split_at >= limit // 2:
                split_at += 1
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def prepare_binding_target(provider, event, provider_config=None):
    provider = str(provider or "").strip().lower()
    if provider not in FAVORITE_DELIVERY_PROVIDERS:
        raise ValueError("当前企业消息渠道暂不支持常用任务投递。")
    event = event if isinstance(event, dict) else {}
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    if provider == FAVORITE_DELIVERY_PROVIDER_FEISHU:
        chat_id = str(event.get("chat_id") or "").strip()
        if not chat_id:
            raise ValueError("飞书消息缺少可绑定的会话标识。")
        return {
            "target_type": "chat_id",
            "target_value": chat_id,
            "display_name": "当前飞书会话",
        }
    if provider == FAVORITE_DELIVERY_PROVIDER_WECOM:
        chat_id = str(event.get("chat_id") or "").strip()
        user_id = str(event.get("user_id") or "").strip()
        if user_id == "unknown":
            user_id = ""
        target_value = chat_id or user_id
        if not target_value:
            raise ValueError("企业微信消息缺少可绑定的会话标识。")
        return {
            "target_type": "chat_id" if chat_id else "user_id",
            "target_value": target_value,
            "display_name": "当前企业微信会话",
        }
    webhook_url = str(provider_config.get("webhook_url") or "").strip()
    if not webhook_url:
        raise ValueError("钉钉需要先配置固定 Webhook 才能绑定常用任务投递。")
    if not webhook_url.lower().startswith("https://"):
        raise ValueError("钉钉固定 Webhook 必须使用 HTTPS。")
    return {
        "target_type": "webhook",
        "target_value": webhook_url,
        "display_name": "钉钉固定 Webhook",
    }


def collect_feishu_artifacts(paths, workspace_dir):
    raw_workspace = str(workspace_dir or "").strip()
    if not raw_workspace:
        return []
    workspace = os.path.abspath(os.path.normpath(raw_workspace))
    results = []
    seen = set()
    for value in paths if isinstance(paths, list) else []:
        candidate = os.path.abspath(os.path.normpath(str(value or "").strip()))
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            if os.path.commonpath([os.path.normcase(candidate), os.path.normcase(workspace)]) != os.path.normcase(workspace):
                continue
        except ValueError:
            continue
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        results.append(candidate)
    return results


class FavoriteDeliveryService:
    def __init__(self, chat_storage):
        self.chat_storage = chat_storage

    @staticmethod
    def _hash_code(code, salt):
        return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()

    @staticmethod
    def _json(value, fallback):
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except Exception:
            return fallback
        return parsed if isinstance(parsed, type(fallback)) else fallback

    @staticmethod
    def _binding_row(row, include_target=False):
        if not row:
            return None
        result = {
            "id": row["id"],
            "favorite_id": row["favorite_id"],
            "provider": row["provider"],
            "target_type": row["target_type"],
            "display_name": row["display_name"] or "企业消息会话",
            "status": row["status"],
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }
        if include_target:
            result["target_value"] = row["target_value"]
        return result

    def create_binding_request(self, favorite_id, binding_id="", now_ts=None):
        favorite_id = str(favorite_id or "").strip()
        if not favorite_id:
            raise ValueError("常用项 ID 不能为空。")
        now_ts = int(now_ts or time.time())
        binding_id = str(binding_id or "").strip() or f"binding-{uuid.uuid4().hex}"
        request_id = f"bindreq-{uuid.uuid4().hex}"
        expires_at = now_ts + FAVORITE_DELIVERY_BINDING_TTL_SECONDS
        with self.chat_storage._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE favorite_delivery_binding_requests
                SET status = 'expired'
                WHERE favorite_id = ? AND status = 'pending'
                """,
                (favorite_id,),
            )
            pending_codes = conn.execute(
                """
                SELECT code_salt, code_hash
                FROM favorite_delivery_binding_requests
                WHERE status = 'pending' AND expires_at > ?
                """,
                (now_ts,),
            ).fetchall()
            code = ""
            for _attempt in range(20):
                candidate = f"{secrets.randbelow(1_000_000):06d}"
                if not any(
                    secrets.compare_digest(
                        self._hash_code(candidate, row["code_salt"]),
                        row["code_hash"],
                    )
                    for row in pending_codes
                ):
                    code = candidate
                    break
            if not code:
                raise RuntimeError("暂时无法生成唯一绑定码，请稍后重试。")
            salt = secrets.token_hex(16)
            code_hash = self._hash_code(code, salt)
            conn.execute(
                """
                INSERT INTO favorite_delivery_binding_requests (
                    id, favorite_id, binding_id, code_salt, code_hash, status,
                    expires_at, created_at, claimed_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, 0)
                """,
                (request_id, favorite_id, binding_id, salt, code_hash, expires_at, now_ts),
            )
        return {
            "request_id": request_id,
            "favorite_id": favorite_id,
            "binding_id": binding_id,
            "code": code,
            "expires_at": expires_at,
            "command": f"绑定常用 {code}",
        }

    def find_pending_request(self, code, now_ts=None):
        code = str(code or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return None
        now_ts = int(now_ts or time.time())
        with self.chat_storage._connect() as conn:
            conn.execute(
                """
                UPDATE favorite_delivery_binding_requests
                SET status = 'expired'
                WHERE status = 'pending' AND expires_at <= ?
                """,
                (now_ts,),
            )
            rows = conn.execute(
                """
                SELECT id, favorite_id, binding_id, code_salt, code_hash,
                       status, expires_at, created_at
                FROM favorite_delivery_binding_requests
                WHERE status = 'pending' AND expires_at > ?
                ORDER BY created_at DESC
                """,
                (now_ts,),
            ).fetchall()
        for row in rows:
            if secrets.compare_digest(self._hash_code(code, row["code_salt"]), row["code_hash"]):
                return {
                    "request_id": row["id"],
                    "favorite_id": row["favorite_id"],
                    "binding_id": row["binding_id"],
                    "expires_at": int(row["expires_at"] or 0),
                }
        return None

    def claim_binding_request(self, request_id, provider, target, now_ts=None):
        request_id = str(request_id or "").strip()
        provider = str(provider or "").strip().lower()
        if provider not in FAVORITE_DELIVERY_PROVIDERS:
            raise ValueError("当前企业消息渠道暂不支持常用任务投递。")
        target = target if isinstance(target, dict) else {}
        target_type = str(target.get("target_type") or "").strip()
        target_value = str(target.get("target_value") or "").strip()
        display_name = str(target.get("display_name") or "企业消息会话").strip()
        if not target_type or not target_value:
            raise ValueError("企业消息绑定目标不完整。")
        now_ts = int(now_ts or time.time())
        expired = False
        invalid = False
        binding_id = ""
        with self.chat_storage._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, favorite_id, binding_id, status, expires_at
                FROM favorite_delivery_binding_requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            if not row or row["status"] != "pending":
                invalid = True
            elif int(row["expires_at"] or 0) <= now_ts:
                conn.execute(
                    "UPDATE favorite_delivery_binding_requests SET status = 'expired' WHERE id = ?",
                    (request_id,),
                )
                expired = True
            else:
                binding_id = row["binding_id"]
                conn.execute(
                    """
                    INSERT INTO favorite_delivery_bindings (
                        id, favorite_id, provider, target_type, target_value,
                        display_name, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        favorite_id = excluded.favorite_id,
                        provider = excluded.provider,
                        target_type = excluded.target_type,
                        target_value = excluded.target_value,
                        display_name = excluded.display_name,
                        status = 'active',
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["binding_id"], row["favorite_id"], provider,
                        target_type, target_value, display_name, now_ts, now_ts,
                    ),
                )
                conn.execute(
                    """
                    UPDATE favorite_delivery_binding_requests
                    SET status = 'claimed', claimed_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now_ts, request_id),
                )
                conn.execute(
                    """
                    UPDATE favorite_delivery_binding_requests
                    SET status = 'expired'
                    WHERE favorite_id = ? AND id <> ? AND status = 'pending'
                    """,
                    (row["favorite_id"], request_id),
                )
        if invalid:
            raise ValueError("绑定码已使用或已失效。")
        if expired:
            raise ValueError("绑定码已过期，请在桌面端重新生成。")
        return self.get_binding(binding_id)

    def get_binding(self, binding_id, include_target=False):
        binding_id = str(binding_id or "").strip()
        if not binding_id:
            return None
        with self.chat_storage._connect() as conn:
            row = conn.execute(
                """
                SELECT id, favorite_id, provider, target_type, target_value,
                       display_name, status, created_at, updated_at
                FROM favorite_delivery_bindings
                WHERE id = ?
                """,
                (binding_id,),
            ).fetchone()
        return self._binding_row(row, include_target=include_target)

    def get_pending_request_for_favorite(self, favorite_id, now_ts=None):
        favorite_id = str(favorite_id or "").strip()
        now_ts = int(now_ts or time.time())
        with self.chat_storage._connect() as conn:
            conn.execute(
                """
                UPDATE favorite_delivery_binding_requests
                SET status = 'expired'
                WHERE status = 'pending' AND expires_at <= ?
                """,
                (now_ts,),
            )
            row = conn.execute(
                """
                SELECT id, favorite_id, binding_id, status, expires_at, created_at
                FROM favorite_delivery_binding_requests
                WHERE favorite_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (favorite_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "request_id": row["id"],
            "favorite_id": row["favorite_id"],
            "binding_id": row["binding_id"],
            "status": row["status"],
            "expires_at": int(row["expires_at"] or 0),
            "created_at": int(row["created_at"] or 0),
        }

    def unbind(self, binding_id, now_ts=None):
        binding_id = str(binding_id or "").strip()
        if not binding_id:
            return False
        now_ts = int(now_ts or time.time())
        with self.chat_storage._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE favorite_delivery_bindings
                SET status = 'unbound', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now_ts, binding_id),
            )
        return bool(cursor.rowcount)

    @staticmethod
    def _job_row(row):
        if not row:
            return None
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        return {
            "id": row["id"],
            "run_history_id": row["run_history_id"],
            "favorite_id": row["favorite_id"],
            "session_id": row["session_id"] or "",
            "binding_id": row["binding_id"],
            "provider": row["provider"],
            "status": row["status"],
            "payload": payload if isinstance(payload, dict) else {},
            "attempt_count": int(row["attempt_count"] or 0),
            "next_attempt_at": int(row["next_attempt_at"] or 0),
            "error": row["error"] or "",
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
            "finished_at": int(row["finished_at"] or 0),
        }

    def enqueue_delivery(
        self,
        *,
        run_history_id,
        favorite_id,
        session_id,
        binding_id,
        favorite_name,
        terminal_status,
        content,
        error="",
        artifacts=None,
        now_ts=None,
    ):
        run_history_id = str(run_history_id or "").strip()
        if not run_history_id:
            raise ValueError("运行历史 ID 不能为空。")
        binding = self.get_binding(binding_id, include_target=True)
        if not binding or binding.get("status") != "active":
            raise ValueError("企业消息绑定不存在或已解除。")
        provider = binding["provider"]
        now_ts = int(now_ts or time.time())
        job_id = f"delivery-{uuid.uuid4().hex}"
        name = str(favorite_name or "常用任务").strip()
        terminal_status = str(terminal_status or "completed").strip().lower()
        if terminal_status == "completed":
            text = f"常用任务「{name}」已完成"
            if str(content or "").strip():
                text += "\n\n" + str(content or "").strip()
        elif terminal_status == "interrupted":
            text = f"常用任务「{name}」已停止"
            detail = str(error or content or "").strip()
            if detail:
                text += "\n\n" + detail
        else:
            text = f"常用任务「{name}」运行失败"
            detail = str(error or content or "未知错误").strip()
            text += "\n\n原因：" + detail
        items = []
        for index, chunk in enumerate(split_delivery_text(text)):
            item_id = f"text-{index + 1}"
            items.append({
                "id": item_id,
                "type": "text",
                "text": chunk,
                "status": "pending",
                "attempts": 0,
                "error": "",
                "idempotency_key": hashlib.sha256(f"{job_id}:{item_id}".encode("utf-8")).hexdigest()[:32],
            })
        if provider == FAVORITE_DELIVERY_PROVIDER_FEISHU and terminal_status == "completed":
            for index, path in enumerate(artifacts if isinstance(artifacts, list) else []):
                item_id = f"artifact-{index + 1}"
                items.append({
                    "id": item_id,
                    "type": "artifact",
                    "path": str(path or ""),
                    "name": os.path.basename(str(path or "")),
                    "status": "pending",
                    "attempts": 0,
                    "error": "",
                    "idempotency_key": hashlib.sha256(f"{job_id}:{item_id}".encode("utf-8")).hexdigest()[:32],
                })
        payload = {
            "favorite_name": name,
            "terminal_status": terminal_status,
            "target_type": binding["target_type"],
            "target_value": binding["target_value"],
            "items": items,
        }
        with self.chat_storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO favorite_delivery_jobs (
                    id, run_history_id, favorite_id, session_id, binding_id,
                    provider, status, payload, attempt_count, next_attempt_at,
                    error, created_at, updated_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, 0, '', ?, ?, 0)
                """,
                (
                    job_id, run_history_id, str(favorite_id or ""), str(session_id or ""),
                    binding_id, provider, json.dumps(payload, ensure_ascii=False), now_ts, now_ts,
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id):
        with self.chat_storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM favorite_delivery_jobs WHERE id = ?",
                (str(job_id or ""),),
            ).fetchone()
        return self._job_row(row)

    def get_jobs(self, job_ids):
        ids = [str(item or "").strip() for item in job_ids if str(item or "").strip()]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.chat_storage._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM favorite_delivery_jobs WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return [self._job_row(row) for row in rows]

    def claim_next_job(self, provider, now_ts=None):
        provider = str(provider or "").strip().lower()
        now_ts = int(now_ts or time.time())
        with self.chat_storage._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM favorite_delivery_jobs
                WHERE provider = ?
                  AND status IN ('pending', 'retry_wait')
                  AND next_attempt_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (provider, now_ts),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE favorite_delivery_jobs
                SET status = 'sending', attempt_count = attempt_count + 1,
                    updated_at = ?, error = ''
                WHERE id = ? AND status IN ('pending', 'retry_wait')
                """,
                (now_ts, row["id"]),
            )
        return self.get_job(row["id"])

    def recover_interrupted_jobs(self, now_ts=None):
        now_ts = int(now_ts or time.time())
        with self.chat_storage._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE favorite_delivery_jobs
                SET status = 'unknown',
                    error = CASE
                        WHEN error IS NULL OR error = '' THEN '发送进程在确认结果前中断，请手动重试。'
                        ELSE error
                    END,
                    updated_at = ?, finished_at = ?
                WHERE status = 'sending'
                """,
                (now_ts, now_ts),
            )
        return int(cursor.rowcount or 0)

    def save_job_state(self, job_id, payload, status, error="", next_attempt_at=0, now_ts=None):
        now_ts = int(now_ts or time.time())
        terminal = status in FAVORITE_DELIVERY_TERMINAL_STATUSES
        with self.chat_storage._connect() as conn:
            conn.execute(
                """
                UPDATE favorite_delivery_jobs
                SET payload = ?, status = ?, error = ?, next_attempt_at = ?,
                    updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False),
                    status,
                    str(error or "")[:1200],
                    int(next_attempt_at or 0),
                    now_ts,
                    now_ts if terminal else 0,
                    str(job_id or ""),
                ),
            )
        return self.get_job(job_id)

    def retry_job(self, job_id, now_ts=None):
        job = self.get_job(job_id)
        if not job or job["status"] not in FAVORITE_DELIVERY_RETRYABLE_STATUSES:
            return None
        payload = dict(job.get("payload") or {})
        items = []
        for raw in payload.get("items") or []:
            item = dict(raw or {})
            if item.get("status") != "sent":
                item["status"] = "pending"
                item["error"] = ""
                item["attempts"] = 0
            items.append(item)
        payload["items"] = items
        now_ts = int(now_ts or time.time())
        with self.chat_storage._connect() as conn:
            conn.execute(
                """
                UPDATE favorite_delivery_jobs
                SET payload = ?, status = 'pending', attempt_count = 0,
                    next_attempt_at = 0, error = '', updated_at = ?, finished_at = 0
                WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), now_ts, str(job_id or "")),
            )
        return self.get_job(job_id)

    @staticmethod
    def retry_delay(attempt_count):
        index = max(0, min(int(attempt_count or 1) - 1, len(FAVORITE_DELIVERY_RETRY_DELAYS) - 1))
        return FAVORITE_DELIVERY_RETRY_DELAYS[index]
