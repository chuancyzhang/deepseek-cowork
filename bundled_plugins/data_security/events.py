"""Bounded asynchronous event persistence; no scanned content is accepted."""
import json
import os
import queue
import threading
import time

_queue = queue.Queue(maxsize=128)
_thread = None
_lock = threading.Lock()


def append(event):
    global _thread
    with _lock:
        if _thread is None:
            _thread = threading.Thread(target=_writer, name="data-security-events", daemon=True)
            _thread.start()
    try:
        _queue.put_nowait({"timestamp": time.time(), **event})
    except queue.Full:
        from core.data_security import publish
        publish({"event": "log", "status": "partial", "message": "安全日志队列已满，部分事件未保存；任务继续。"})


def _writer():
    from core.env_utils import get_app_data_dir
    while True:
        event = _queue.get()
        try:
            root = os.path.join(get_app_data_dir(), "data_security")
            os.makedirs(root, exist_ok=True)
            path = os.path.join(root, "events.jsonl")
            if os.path.isfile(path) and os.path.getsize(path) > 512 * 1024:
                os.replace(path, path + ".previous")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:
            from core.data_security import publish
            publish({"event": "log", "status": "failed", "error_type": type(exc).__name__,
                     "message": "安全日志保存失败，任务继续。"})
        finally:
            _queue.task_done()


def read_recent(limit=60):
    """Read existing bounded log files only; never starts the writer or creates IO state."""
    from core.env_utils import get_app_data_dir
    from collections import deque
    result = deque(maxlen=limit)
    path = os.path.join(get_app_data_dir(), "data_security", "events.jsonl")
    for candidate in (path + ".previous", path):
        if not os.path.isfile(candidate):
            continue
        with open(candidate, encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # the writer may still be finishing the last line
                if isinstance(event, dict):
                    result.append(event)
    return list(result)
