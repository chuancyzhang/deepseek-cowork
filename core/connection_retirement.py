"""One-time retirement of desktop shared connections; never reads their secrets.

The journal lives alongside library state. It is not an account store: only
retired capability IDs and explicit reconfiguration checkpoints are retained.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

log = logging.getLogger(__name__)
SOURCES = {"weknora", "tencent-docs", "lexiang"}
RECONFIGURE = "原资料服务连接已停用，请在此功能中保存独立配置，再点击“测试连接”。"
VERIFY = "独立配置已保存，请点击“测试连接”；验证成功后恢复使用。"
INCOMPATIBLE_SKILL = "此能力仍声明已停用的 connection_requirements，请更新能力以使用独立认证后重新保存配置。"
RETIREMENT_FAILED = "旧连接状态处理失败，原文件和资料已保留。请关闭旧版程序，检查用户数据目录后刷新或重新测试；不会自动使用其他账号。"


class RetirementError(RuntimeError):
    pass


def data_directory(config_manager=None):
    value = getattr(config_manager, "data_dir", None)
    if isinstance(value, (str, os.PathLike)):
        return os.fspath(value)
    from .env_utils import get_app_data_dir
    return get_app_data_dir()


@contextmanager
def _journal(data_dir):
    db = None
    try:
        os.makedirs(data_dir, exist_ok=True)
        db = sqlite3.connect(os.path.join(data_dir, "knowledge_library.sqlite3"), timeout=40)
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS connection_retirement (version INTEGER PRIMARY KEY)")
        db.execute("""CREATE TABLE IF NOT EXISTS retired_capabilities
                      (capability TEXT PRIMARY KEY, stage TEXT NOT NULL, fingerprint TEXT NOT NULL DEFAULT '')""")
        yield db
        db.commit()
    except (OSError, sqlite3.Error) as exc:
        if db is not None:
            db.rollback()
        log.warning("connection_retirement journal_failed reason=%s", type(exc).__name__)
        raise RetirementError(RETIREMENT_FAILED) from None
    except Exception:
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            db.close()


def _legacy_selections(path):
    if not path.exists():
        return []
    # Read public selections only, including dangling bindings. Never SELECT secret.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as legacy:
        return list(legacy.execute("SELECT capability, requirement FROM selections"))


def ensure_retired(data_dir):
    """Back up dormant native logins and retire bindings in one local transaction."""
    try:
        journal_path = Path(data_dir) / "knowledge_library.sqlite3"
        legacy_path = Path(data_dir) / "connections.sqlite3"
        if not journal_path.exists() and not legacy_path.exists():
            return
        if journal_path.exists():
            with closing(sqlite3.connect(journal_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=40)) as probe:
                if (probe.execute("SELECT 1 FROM sqlite_master WHERE name='connection_retirement'").fetchone()
                        and probe.execute("SELECT 1 FROM connection_retirement WHERE version=1").fetchone()):
                    return
        with _journal(data_dir) as db:
            if db.execute("SELECT 1 FROM connection_retirement WHERE version=1").fetchone():
                return
            log.info("connection_retirement start")
            selections = _legacy_selections(legacy_path)
            sources = set()
            for capability, requirement in selections:
                if not isinstance(capability, str) or not isinstance(requirement, str):
                    raise ValueError("invalid public selection")
                source = capability.removeprefix("library:") if capability.startswith("library:") else (
                    requirement if capability == "knowledge-library" else None)
                if source in SOURCES:
                    sources.add(source)
                    capability = "library:" + source
                db.execute("INSERT OR IGNORE INTO retired_capabilities(capability, stage) VALUES(?, 'reconfigure')", (capability,))
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if sources:
                db.execute("""CREATE TABLE IF NOT EXISTS retired_source_logins
                              (source TEXT, origin TEXT, public TEXT, secret BLOB,
                               PRIMARY KEY(source, origin))""")
            for source in sorted(sources):
                if "source_connections" in tables:
                    db.execute("""INSERT OR IGNORE INTO retired_source_logins
                                  SELECT source, 'source_connections', public, secret FROM source_connections WHERE source=?""", (source,))
                    db.execute("DELETE FROM source_connections WHERE source=?", (source,))
                if source == "weknora" and "connection" in tables:
                    db.execute("""INSERT OR IGNORE INTO retired_source_logins
                                  SELECT 'weknora', 'connection', public, secret FROM connection""")
                    db.execute("DELETE FROM connection")
            db.execute("INSERT INTO connection_retirement VALUES(1)")
        log.info("connection_retirement complete sources=%s capabilities=%s", sorted(sources), len(selections))
    except (OSError, sqlite3.Error, ValueError) as exc:
        log.warning("connection_retirement failed reason=%s", type(exc).__name__)
        raise RetirementError(RETIREMENT_FAILED) from None


def pending(data_dir, capability):
    ensure_retired(data_dir)
    path = Path(data_dir) / "knowledge_library.sqlite3"
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=40)) as db:
            row = db.execute("SELECT stage, fingerprint FROM retired_capabilities WHERE capability=?", (capability,)).fetchone()
    except (OSError, sqlite3.Error) as exc:
        log.warning("connection_retirement read_failed reason=%s", type(exc).__name__)
        raise RetirementError(RETIREMENT_FAILED) from None
    return {"stage": row[0], "fingerprint": row[1]} if row else None


def require_library_retirement(data_dir):
    # Imported here to keep the upgrade journal independent of provider initialization.
    try:
        ensure_retired(data_dir)
    except RetirementError as exc:
        from .knowledge_library import KnowledgeError
        raise KnowledgeError("retirement_failed", str(exc)) from None


def clear_library_pending(db, source):
    db.execute("DELETE FROM retired_capabilities WHERE capability=?", ("library:" + source,))


def library_notice(data_dir, source):
    try:
        return "原资料服务连接已停用，请在当前资料库重新登录。历史资料选择和上传记录已保留。" if pending(data_dir, "library:" + source) else ""
    except RetirementError as exc:
        return str(exc)


def mcp_fingerprint(server, config_manager):
    value = {k: v for k, v in server.items() if k not in {"connection_ref", "connection_setup_required"}}
    skill = str((server.get("auth") or {}).get("skill_name") or server.get("source_skill") or "")
    config = config_manager.get_skill_config(skill) if skill and config_manager is not None else {}
    return hashlib.sha256(json.dumps([value, config], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def mcp_issue(server, config_manager=None, *, testing=False):
    try:
        # A native library adapter also uses the MCP transport, without a saved
        # MCP config. Its source-scoped store owns retirement and authentication.
        # Do not route that transport through a similarly named user's MCP entry.
        state = pending(data_directory(config_manager), "mcp:" + str(server.get("id") or "")) if config_manager is not None else None
        if "connection_ref" in server or server.get("connection_setup_required"):
            return RECONFIGURE
        if not state:
            return ""
        if testing and state["stage"] == "verify" and config_manager is not None:
            current = next((s for s in config_manager.get_mcp_servers() if s.get("id") == server.get("id")), None)
            if (current and mcp_fingerprint(current, config_manager) == state["fingerprint"]
                    and mcp_fingerprint(server, config_manager) == state["fingerprint"]):
                return ""
        return VERIFY if state["stage"] == "verify" else RECONFIGURE
    except RetirementError as exc:
        return str(exc)


def register_explicit_refs(data_dir, servers):
    ensure_retired(data_dir)
    servers = [s for s in servers if isinstance(s, dict) and ("connection_ref" in s or s.get("connection_setup_required"))]
    if not servers:
        return
    with _journal(data_dir) as db:
        for server in servers:
            db.execute("INSERT OR IGNORE INTO retired_capabilities(capability, stage) VALUES(?, 'reconfigure')",
                       ("mcp:" + str(server.get("id") or ""),))


def prepare_mcp_reconfiguration(config_manager, server_id):
    """Only an explicit configuration save may open the subsequent test step."""
    directory = data_directory(config_manager)
    servers = config_manager.get_mcp_servers()
    register_explicit_refs(directory, servers)
    server = next((s for s in servers if s.get("id") == server_id), None)
    if not server or not pending(directory, "mcp:" + server_id):
        return
    server.pop("connection_ref", None)
    server.pop("connection_setup_required", None)
    config_manager.set_mcp_servers(servers)
    fingerprint = mcp_fingerprint(server, config_manager)
    with _journal(directory) as db:
        # A test of the previous configuration may finish while this save runs.
        # The explicit new save must still require its own verification.
        db.execute("INSERT OR REPLACE INTO retired_capabilities VALUES(?, 'verify', ?)", ("mcp:" + server_id, fingerprint))
    log.info("connection_retirement configured capability=mcp:%s", server_id)


def complete_mcp_reconfiguration(server, config_manager):
    if config_manager is None:
        return
    directory = data_directory(config_manager)
    capability = "mcp:" + str(server.get("id") or "")
    state = pending(directory, capability)
    if not state:
        return
    with _journal(directory) as db:
        state = db.execute("SELECT stage, fingerprint FROM retired_capabilities WHERE capability=?", (capability,)).fetchone()
        if state is None:
            return  # Another successful test already completed this transition.
        # Read the current normalized in-memory config while holding the journal
        # transaction. get_mcp_servers() may itself write the journal for imports.
        current = next((s for s in config_manager.config.get("mcp_servers", []) if s.get("id") == server.get("id")), None)
        if (state[0] != "verify" or not current or "connection_ref" in current or current.get("connection_setup_required")
                or mcp_fingerprint(current, config_manager) != state[1]
                or mcp_fingerprint(server, config_manager) != state[1]):
            raise RetirementError("测试期间配置已变化，请保存当前配置后重新测试。")
        deleted = db.execute("DELETE FROM retired_capabilities WHERE capability=? AND stage='verify' AND fingerprint=?",
                             (capability, state[1])).rowcount
        if not deleted:
            raise RetirementError("测试期间配置已变化，请重新测试。")
    log.info("connection_retirement verified capability=%s", capability)


def skill_issue(skill, spec, config_manager=None, *, draft=False):
    if "connection_requirements" in spec:
        return INCOMPATIBLE_SKILL
    if draft:
        return ""
    try:
        if pending(data_directory(config_manager), skill):
            return "原资料服务连接已停用，请更新此能力并保存其独立配置。"
    except RetirementError as exc:
        return str(exc)
    return ""


def complete_skill_reconfiguration(config_manager, skill):
    directory = data_directory(config_manager)
    ensure_retired(directory)
    with _journal(directory) as db:
        db.execute("DELETE FROM retired_capabilities WHERE capability=?", (skill,))
