"""Local team-space data. No credentials or old-project migrations."""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid

log = logging.getLogger(__name__)


class TeamSpaceError(RuntimeError):
    def __init__(self, message, *, code="team_space_error", status=0):
        super().__init__(message)
        self.code, self.status = code, status


class TeamSpacePaused(TeamSpaceError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def validate_binding(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise TeamSpaceError("空间会话绑定损坏或版本不支持。历史已保留，无法继续外发。", code="binding_invalid")
    for field in ("platform_id", "member_id", "space_id", "workspace_id", "workspace_dir", "data_dir"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise TeamSpaceError("空间会话归属不完整。历史已保留，请恢复原绑定后继续。", code="binding_invalid")
    return json.loads(encode(value))


class TeamSpaceConversationStorage:
    """Mixin: binding and detection marker commit in the conversation DB together."""

    def _ensure_team_space_schema(self):
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS conversation_team_spaces (
                conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                binding TEXT NOT NULL, binding_hash TEXT NOT NULL, created_at REAL NOT NULL)""")

    def bind_team_space(self, conversation_id, binding):
        binding = validate_binding(binding)
        marker = checksum(binding)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT meta FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not row or conn.execute("SELECT 1 FROM messages WHERE conversation_id=? LIMIT 1", (conversation_id,)).fetchone():
                raise TeamSpaceError("团队空间只能绑定新建且尚未发送消息的会话。", code="new_conversation_required")
            old = conn.execute("SELECT binding_hash FROM conversation_team_spaces WHERE conversation_id=?", (conversation_id,)).fetchone()
            if old:
                if old[0] != marker:
                    raise TeamSpaceError("已有空间会话不能重新绑定其他空间或目录。", code="binding_immutable")
                return binding
            meta = json.loads(row["meta"] or "{}")
            if "team_space_required" in meta:
                raise TeamSpaceError("此会话已有空间标记，不能覆盖缺失的归属记录。", code="binding_invalid")
            meta["team_space_required"] = marker
            conn.execute("INSERT INTO conversation_team_spaces VALUES(?,?,?,?)",
                         (conversation_id, encode(binding), marker, time.time()))
            conn.execute("UPDATE conversations SET meta=? WHERE id=?", (encode(meta), conversation_id))
        log.info("team_space.conversation_bound conversation=%s space=%s member=%s", conversation_id,
                 binding["space_id"], binding["member_id"])
        return binding

    def get_team_space_binding(self, conversation_id):
        with self._connect() as conn:
            row = conn.execute("SELECT binding,binding_hash FROM conversation_team_spaces WHERE conversation_id=?", (conversation_id,)).fetchone()
            conversation = conn.execute("SELECT meta FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        try:
            meta = json.loads(conversation[0] or "{}") if conversation else {}
            marker = meta.get("team_space_required")
            if not row and marker is None and meta.get("workspace_source") != "team_space":
                return None
            if not row or not marker:
                raise ValueError()
            binding = validate_binding(json.loads(row[0]))
            if checksum(binding) != marker or marker != row[1]:
                raise ValueError()
            return binding
        except (ValueError, TypeError, AttributeError):
            raise TeamSpaceError("空间会话归属校验失败。内容仍在本机，已阻止继续外发。", code="binding_invalid") from None

    @staticmethod
    def _preserve_team_space_marker(existing_json, meta):
        if meta is None:
            return None
        if not existing_json or "team_space_required" not in existing_json:
            return meta
        old = json.loads(existing_json or "{}")
        if isinstance(old, dict) and "team_space_required" in old:
            if "team_space_required" in meta and meta["team_space_required"] != old["team_space_required"]:
                raise TeamSpaceError("保存不能改变已有空间归属。", code="binding_immutable")
            meta = {**meta, "team_space_required": old["team_space_required"]}
        return meta

    def list_team_space_conversations(self, platform_id, member_id, space_id):
        with self._connect() as conn:
            rows = conn.execute("SELECT c.id,c.title,c.status,c.updated_at,b.binding FROM conversations c "
                "JOIN conversation_team_spaces b ON b.conversation_id=c.id ORDER BY c.updated_at DESC").fetchall()
        result = []
        for row in rows:
            binding = json.loads(row["binding"])
            if (binding.get("platform_id"), binding.get("member_id"), binding.get("space_id")) == (platform_id, member_id, space_id):
                result.append({key: row[key] for key in ("id", "title", "status", "updated_at")})
        return result


def actual_path(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(str(path)))))


def overlaps(left, right):
    try:
        return os.path.commonpath([left, right]) in {left, right}
    except ValueError:
        return False


class TeamSpaceStore:
    def __init__(self, data_dir=None):
        if data_dir is None:
            from .env_utils import get_app_data_dir
            data_dir = get_app_data_dir()
        self.root = Path(data_dir)
        self.path = self.root / "team_spaces.sqlite3"

    @contextmanager
    def db(self):
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, scope TEXT NOT NULL, directory TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS drafts (scope TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(scope,key))")
            conn.execute("CREATE TABLE IF NOT EXISTS operations (scope TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, result TEXT, PRIMARY KEY(scope,id))")
            conn.execute("CREATE TABLE IF NOT EXISTS cache (scope TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(scope,key))")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def scope(identity, space_id=""):
        return encode([identity["platform_id"], identity["member_id"], space_id])

    def workspace(self, identity, space_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM workspaces WHERE scope=? ORDER BY rowid DESC LIMIT 1",
                             (self.scope(identity, space_id),)).fetchone()
        return json.loads(row[0]) if row else None

    def bind_workspace(self, identity, space_id, directory, local_projects=()):
        directory = actual_path(directory)
        path = Path(directory)
        with self.db() as db:
            old = db.execute("SELECT payload FROM workspaces WHERE directory=?", (directory,)).fetchone()
            if old:
                value = json.loads(old[0])
                if (value["platform_id"], value["member_id"], value["space_id"]) == (identity["platform_id"], identity["member_id"], space_id):
                    if not path.is_dir():
                        raise TeamSpaceError("原空间目录已不可用，请恢复该目录或为新任务选择新目录。")
                    return value
                raise TeamSpaceError("此目录已属于其他空间或账号，请选择独立目录。")
            forbidden = [actual_path(p) for p in local_projects if p]
            forbidden += [row[0] for row in db.execute("SELECT directory FROM workspaces")]
            if any(overlaps(directory, other) for other in forbidden):
                raise TeamSpaceError("目录与已有项目或空间相同、互相嵌套或指向同一实际位置，请选择独立目录。")
            if path.exists() and (not path.is_dir() or any(path.iterdir())):
                raise TeamSpaceError("首版使用新建目录或空目录，不迁入已有文件。")
            path.mkdir(parents=True, exist_ok=True)
            value = {"version": 1, "platform_id": identity["platform_id"], "member_id": identity["member_id"],
                "space_id": space_id, "workspace_id": uuid.uuid4().hex, "workspace_dir": directory, "data_dir": str(self.root)}
            db.execute("INSERT INTO workspaces VALUES(?,?,?,?)", (value["workspace_id"], self.scope(identity, space_id), directory, encode(value)))
        return value

    def draft(self, identity, space_id, key, value=None, *, delete=False):
        scope = self.scope(identity, space_id)
        with self.db() as db:
            if delete:
                db.execute("DELETE FROM drafts WHERE scope=? AND key=?", (scope, key))
            elif value is not None:
                db.execute("INSERT OR REPLACE INTO drafts VALUES(?,?,?)", (scope, key, encode(value)))
            row = db.execute("SELECT payload FROM drafts WHERE scope=? AND key=?", (scope, key)).fetchone()
        return json.loads(row[0]) if row else None

    def stage_operation(self, identity, operation_id, action, space_id, payload):
        scope = self.scope(identity)
        content = encode({"action": action, "space_id": space_id, "payload": payload})
        with self.db() as db:
            row = db.execute("SELECT payload,result FROM operations WHERE scope=? AND id=?", (scope, operation_id)).fetchone()
            if row:
                if row[0] != content:
                    raise TeamSpaceError("这次提交内容已经冻结，请确认原操作后再提交修改。", code="operation_conflict")
                return json.loads(row[1]) if row[1] else None
            db.execute("INSERT INTO operations VALUES(?,?,?,NULL)", (scope, operation_id, content))
        return None

    def confirm_operation(self, identity, operation_id, result):
        with self.db() as db:
            db.execute("UPDATE operations SET result=? WHERE scope=? AND id=?", (encode(result), self.scope(identity), operation_id))

    def cached(self, identity, space_id, key, value=None):
        scope = self.scope(identity, space_id)
        with self.db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?)", (scope, key, encode(value), time.time()))
            row = db.execute("SELECT payload,updated_at FROM cache WHERE scope=? AND key=?", (scope, key)).fetchone()
        return {"value": json.loads(row[0]), "updated_at": row[1]} if row else None
