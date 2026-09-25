"""Advisory checks never wait in the SDK request path."""
import hashlib
import time
from .jobs import enqueue


def submit(run, texts):
    digest = hashlib.sha256()
    for text in texts:
        digest.update(text.encode("utf-8"))
        digest.update(b"\x00")
    key = digest.digest()
    if key in run._warning_keys:
        return
    if len(run._warning_keys) >= 128:
        run._warning_keys.clear()
    run._warning_keys.add(key)
    future = enqueue(_check, run, tuple(texts))
    if future is None:
        run.notice("credential", "partial", "凭证提醒队列已满，本次未检查；请求照常发送。")
    else:
        run._warning_future = future


def _check(run, texts):
    count = 0
    try:
        from .rules import SECRET, EXAMPLES
        for text in texts:
            # A candidate is at most ~550 chars. Overlap preserves chunk-boundary
            # matches without counting them twice; yield the GIL between chunks.
            for start in range(0, len(text), 16384):
                if callable(run.abort_check) and run.abort_check():
                    return
                end = min(start + 16384, len(text))
                offset = max(0, start-1)
                chunk = text[offset:min(len(text), end+1024)]
                for match in SECRET.finditer(chunk):
                    if not start <= match.start() + offset < end:
                        continue
                    if match[1] and match[1].lower() in EXAMPLES:
                        continue
                    count += 1
                time.sleep(0)
        if count:
            run.notice("credential", "complete", "请求文本中发现疑似凭证；仅提醒，本次内容可能已发送，未作阻断。", count=count)
    except Exception as exc:
        run.notice("credential", "failed", "凭证检查未完成，请求照常发送。", error_type=type(exc).__name__)
