"""Reproducible synthetic sidebar-search benchmark; never opens user history.

Run: .venv/Scripts/python.exe -X utf8 scripts/benchmark_history_search.py
"""

import argparse
import json
from pathlib import Path
import random
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.chat_storage import ChatStorage


def legacy_search(storage, query, limit=200):
    """The previous title + word FTS + unconditional substring-scan query."""
    matches = {}
    pattern = "%" + query + "%"
    with storage._connect() as conn:
        for index, row in enumerate(conn.execute(
            "SELECT id FROM conversations WHERE title LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (pattern, limit),
        )):
            matches[row[0]] = index
        for priority, sql, args in (
            (1000, """SELECT m.conversation_id, MIN(m.position) FROM messages_fts f
                      JOIN messages m ON m.rowid=f.rowid WHERE messages_fts MATCH ?
                      GROUP BY m.conversation_id LIMIT ?""", ('"' + query.replace('"', '""') + '"', limit)),
            (2000, """SELECT conversation_id, MIN(position) FROM messages
                      WHERE content LIKE ? OR reasoning_content LIKE ?
                      GROUP BY conversation_id LIMIT ?""", (pattern, pattern, limit)),
        ):
            for row in conn.execute(sql, args):
                score = priority + int(row[1] or 0)
                matches[row[0]] = min(matches.get(row[0], score), score)
    return sorted(matches, key=matches.get)[:limit]


def seed(storage, conversations, per_conversation):
    rng = random.Random(20261021)
    words = "项目 工作 完成 记录 报告 文件 数据 同步 查看 模型 用户 过程 搜索 保存 测试 处理 结果 需要 原始 功能 history message index content application".split()
    corpus = [" ".join(rng.choices(words, k=440)) for _ in range(128)]
    with storage._connect() as conn:
        conn.executemany("INSERT INTO conversations(id,title,updated_at,status,meta) VALUES (?,?,?,?,?)",
                         [(f"c{i:05}", f"对话 {i}", i, "completed", "{}") for i in range(conversations)])
        rows = []
        for i in range(conversations):
            for j in range(per_conversation):
                content = corpus[rng.randrange(len(corpus))]
                if i % 97 == 0 and j == 0:
                    content += "稀有截图归档完成，BuildTokenQ782。"
                rows.append((f"m{i}-{j}", f"c{i:05}", "assistant", content,
                             "核对推理路径。" if j % 3 == 0 else "", j, i))
        start = time.perf_counter()
        conn.executemany("""INSERT INTO messages
            (id,conversation_id,role,content,reasoning_content,position,created_at)
            VALUES (?,?,?,?,?,?,?)""", rows)
    return round(time.perf_counter() - start, 3)


def measure(fn, query):
    samples = []
    matches = []
    for _ in range(7):
        start = time.perf_counter()
        matches = fn(query)
        samples.append((time.perf_counter() - start) * 1000)
    return {"median_ms": round(statistics.median(samples), 2),
            "max_ms": round(max(samples), 2), "matches": len(matches)}


def measure_save(storage):
    samples = []
    for index in range(7):
        identity = f"write-probe-{index}"
        start = time.perf_counter()
        storage.save_conversation(identity, [{"id": identity, "role": "assistant",
                                             "content": "这是用于衡量保存开销的普通回复。 history save " * 100}])
        samples.append((time.perf_counter() - start) * 1000)
        storage.delete_conversation(identity)
    return {"median_ms": round(statistics.median(samples), 2), "max_ms": round(max(samples), 2)}


def run(conversations=1000, per_conversation=24):
    with tempfile.TemporaryDirectory(prefix="cowork-search-benchmark-") as root:
        path = str(Path(root) / "history.sqlite")
        storage = ChatStorage(path)
        seeded_sec = seed(storage, conversations, per_conversation)
        with storage._connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before_bytes = Path(path).stat().st_size
        report = {"sqlite": sqlite3.sqlite_version, "conversations": conversations,
                  "messages": conversations * per_conversation, "seed_sec": seeded_sec,
                  "database_before_mb": round(before_bytes / 1024**2, 2)}
        queries = ["图", "截图", "截图归档", "TokenQ78", "不存在的关键词987654", "报告"]
        report["legacy"] = {q: measure(lambda text: legacy_search(storage, text), q) for q in queries}
        report["legacy_save"] = measure_save(storage)
        if hasattr(storage, "prepare_sidebar_search"):
            start = time.perf_counter()
            storage.prepare_sidebar_search()
            report["backfill_sec"] = round(time.perf_counter() - start, 3)
            report["indexed"] = {q: measure(lambda text: storage.search_conversations(text, limit=200), q)
                                 for q in queries}
            report["indexed_save"] = measure_save(storage)
            report["sidebar_with_summaries"] = {
                q: measure(lambda text: storage.search_sidebar_conversations(text)[0], q) for q in queries
            }
            with storage._connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            report["index_added_mb"] = round((Path(path).stat().st_size - before_bytes) / 1024**2, 2)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversations", type=int, default=1000)
    parser.add_argument("--messages", type=int, default=24)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.conversations, args.messages)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
