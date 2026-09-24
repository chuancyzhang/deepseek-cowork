"""One lazily-created worker and a bounded queue for advisory checks."""
from concurrent.futures import ThreadPoolExecutor
import threading

_lock = threading.Lock()
_executor = None
_slots = threading.BoundedSemaphore(16)


def enqueue(function, *args):
    global _executor
    if not _slots.acquire(blocking=False):
        return None
    try:
        with _lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="data-security")
        future = _executor.submit(function, *args)
        future.add_done_callback(lambda _: _slots.release())
        return future
    except Exception:
        _slots.release()
        raise
