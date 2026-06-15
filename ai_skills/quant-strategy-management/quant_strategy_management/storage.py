from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import StrategyDSL, utc_now_iso


class StrategyStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    raw_prompt TEXT NOT NULL,
                    deployment_status TEXT NOT NULL DEFAULT 'research_only',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_version (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    dsl_json TEXT NOT NULL,
                    raw_dsl_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backtest_result (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id INTEGER NOT NULL,
                    strategy_version_id INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    equity_curve_path TEXT NOT NULL,
                    trades_path TEXT NOT NULL,
                    report_path TEXT NOT NULL,
                    ai_comment TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def create_strategy_from_payload(
        self,
        raw_prompt: str,
        raw_dsl: dict[str, Any],
        compiled_dsl: StrategyDSL,
        deployment_status: str = "research_only",
    ) -> tuple[int, int]:
        now = utc_now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO strategy(name, raw_prompt, deployment_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (compiled_dsl.strategy_name, raw_prompt, deployment_status, now, now)
            )
            strategy_id = int(cur.lastrowid)
            version_id = self._insert_version(conn, strategy_id, compiled_dsl, raw_dsl)
        return strategy_id, version_id

    def _insert_version(self, conn: sqlite3.Connection, strategy_id: int, dsl: StrategyDSL, raw_dsl: dict[str, Any]) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM strategy_version WHERE strategy_id = ?",
            (strategy_id,)
        ).fetchone()
        version = int(row["next_version"])
        cur = conn.execute(
            "INSERT INTO strategy_version(strategy_id, version, dsl_json, raw_dsl_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                strategy_id,
                version,
                json.dumps(dsl.to_dict(), ensure_ascii=False),
                json.dumps(raw_dsl, ensure_ascii=False),
                utc_now_iso()
            )
        )
        return int(cur.lastrowid)

    def list_strategies(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = list(
                conn.execute(
                    """
                    SELECT s.*, sv.id AS strategy_version_id, sv.version
                    FROM strategy s
                    JOIN strategy_version sv ON sv.strategy_id = s.id
                    WHERE sv.version = (
                        SELECT MAX(version) FROM strategy_version WHERE strategy_id = s.id
                    )
                    ORDER BY s.updated_at DESC
                    LIMIT ?
                    """,
                    (limit,)
                )
            )
        return [dict(row) for row in rows]

    def get_strategy(self, strategy_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, sv.id AS strategy_version_id, sv.version, sv.dsl_json, sv.raw_dsl_json
                FROM strategy s
                JOIN strategy_version sv ON sv.strategy_id = s.id
                WHERE s.id = ?
                ORDER BY sv.version DESC
                LIMIT 1
                """,
                (strategy_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"strategy {strategy_id} not found")
            backtests = list(
                conn.execute(
                    """
                    SELECT *
                    FROM backtest_result
                    WHERE strategy_id = ?
                    ORDER BY created_at DESC
                    """,
                    (strategy_id,)
                )
            )
        payload = dict(row)
        payload["dsl"] = json.loads(payload.pop("dsl_json"))
        payload["raw_dsl"] = json.loads(payload.pop("raw_dsl_json"))
        payload["backtests"] = [dict(item) | {"summary": json.loads(item["summary_json"])} for item in backtests]
        for item in payload["backtests"]:
            item.pop("summary_json", None)
        return payload

    def load_latest_dsl(self, strategy_id: int) -> StrategyDSL:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT dsl_json
                FROM strategy_version
                WHERE strategy_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (strategy_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"strategy {strategy_id} not found")
        return StrategyDSL.from_dict(json.loads(row["dsl_json"]))

    def save_backtest(
        self,
        strategy_id: int,
        strategy_version_id: int,
        summary: dict[str, Any],
        equity_curve_path: str,
        trades_path: str,
        report_path: str,
        ai_comment: str,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO backtest_result(
                    strategy_id, strategy_version_id, summary_json, equity_curve_path, trades_path, report_path, ai_comment, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    strategy_version_id,
                    json.dumps(summary, ensure_ascii=False),
                    equity_curve_path,
                    trades_path,
                    report_path,
                    ai_comment,
                    utc_now_iso()
                )
            )
            return int(cur.lastrowid)
