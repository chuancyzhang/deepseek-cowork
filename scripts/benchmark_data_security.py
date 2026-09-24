"""Local synthetic request benchmark; never calls a model or external service."""
import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.data_security import begin_run, normalize_config


def measure(mode, size, directory, repeats=40):
    policy = normalize_config({"enabled": mode != "off", "credential_warning": mode == "warning",
                              "tokenize": mode in {"tokenize", "all_categories"}, "categories": {"phone": True, "email": True}})
    if mode == "all_categories":
        policy["categories"] = dict.fromkeys(policy["categories"], True)
    run = begin_run(policy=policy, scope="benchmark", data_dir=directory)
    padding = ("ordinary document text 12345. " * (size // 30 + 1))[:size - 60]
    samples = []
    for i in range(repeats + 1):
        params = {"messages": [{"role": "user", "content": f"{i}: alice@example.org 13800138000 " + padding}]}
        result = None
        start = time.perf_counter_ns()
        result = run.project_request(params, "chat_completions")
        elapsed = (time.perf_counter_ns() - start) / 1e6
        if run.raw_mode:
            raise RuntimeError("Benchmark entered raw mode; timing is invalid")
        if policy["tokenize"] and "alice@example.org" in result["messages"][0]["content"]:
            raise RuntimeError("Benchmark failed to tokenize")
        if i:
            samples.append(elapsed)
        if run._warning_future is not None:
            run._warning_future.result(timeout=5)
    hot = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        run.project_request(params, "chat_completions")
        hot.append((time.perf_counter_ns()-start)/1e6)
    run.close()
    return {"mode": mode, "bytes": size, "samples": repeats,
            "p50_ms": round(sorted(samples)[len(samples)//2], 4),
            "p95_ms": round(sorted(samples)[int(len(samples)*0.95)-1], 4),
            "cached_p95_ms": round(sorted(hot)[int(len(hot)*0.95)-1], 4)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as root, patch("bundled_plugins.data_security.events.append"):
        result = {"python": platform.python_version(), "platform": platform.platform(),
                  "notes": "Synthetic local projection, real DPAPI; no model/network. Warm engine; unique text per sample. Event disk writes excluded (asynchronous).",
                  "results": [measure(mode, size, root) for mode in ("off", "warning", "tokenize", "all_categories")
                              for size in (100 * 1024, 1024 * 1024)]}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
