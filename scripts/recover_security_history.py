"""Export immutable run evidence for missing security-projected replies.

Read-only recovery: does not change chat SQLite, run status or edited history.
"""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def recover(history_dir, output_dir, sessions):
    root = Path(history_dir) / "runtime_journal_v2" / "sessions"
    recovered = []
    for session in sessions:
        directory = root / hashlib.sha256(session.encode()).hexdigest() / "runs"
        for path in directory.glob("*.json"):
            envelope = json.loads(path.read_text(encoding="utf-8"))
            run = envelope["payload"]
            encoded = json.dumps(run, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if hashlib.sha256(encoded).hexdigest() != envelope["checksum"]:
                raise ValueError("Run journal checksum mismatch")
            result = run.get("final_result") or {}
            if run.get("status") != "completed" or not any(
                (message.get("meta") or {}).get("data_security_tokens")
                for message in result.get("generated_messages", []) if isinstance(message, dict)
            ):
                continue
            recovered.append(run)
    recovered.sort(key=lambda run: run.get("created_at", 0))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    # Preserve the exact source evidence before rendering a readable copy.
    (output / "run-evidence.json").write_text(json.dumps(recovered, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 数据安全验收对话恢复副本", "",
             "按运行记录分别恢复，未覆盖现有会话，也未撤销用户编辑历史。包含本地原文，请按聊天记录保管。", ""]
    for run in recovered:
        result = run["final_result"]
        stamp = datetime.fromtimestamp(run.get("created_at", 0)).isoformat(sep=" ", timespec="seconds")
        lines.extend([f"## {stamp}", "", f"会话：`{run['session_id']}` · 运行：`{run['run_id']}`", ""])
        user = run.get("pending_user_message")
        if isinstance(user, dict) and user.get("content"):
            lines.extend(["### 当时的输入", "", str(user["content"]), ""])
        lines.extend(["### 当时的结果", "", str(result.get("content") or "（无最终正文，原始消息见证据文件）"), ""])
    (output / "恢复的对话.md").write_text("\n".join(lines), encoding="utf-8")
    return len(recovered)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--session", action="append", required=True)
    args = parser.parse_args()
    count = recover(args.history_dir, args.output_dir, args.session)
    print(f"Exported {count} completed runs; source history unchanged.")
