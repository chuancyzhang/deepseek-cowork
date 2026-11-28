"""Serialize desktop configuration access and report durable-save failures."""
import copy
from contextlib import contextmanager
from functools import wraps
import threading

from core.config_manager import ConfigManager


def _serialized(method):
    read_only = method.__name__ == "get" or method.__name__.startswith(("get_", "list_", "iter_", "has_", "is_"))
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self._desktop_lock:
            if read_only or self._desktop_write_depth or self._save_batch_depth:
                return method(self, *args, **kwargs)
            previous = copy.deepcopy(self.config)
            self._desktop_write_depth += 1
            try:
                return method(self, *args, **kwargs)
            except BaseException:
                self.config = previous
                raise
            finally:
                self._desktop_write_depth -= 1
    return invoke


class DesktopConfigManager(ConfigManager):
    def __init__(self, *args, **kwargs):
        self._desktop_lock = threading.RLock()
        self._desktop_write_depth = 0
        super().__init__(*args, **kwargs)

    @contextmanager
    def batch_save(self):
        with self._desktop_lock:
            with super().batch_save():
                yield self


# Serialize public entry points. Read access never needs a rollback snapshot;
# nested writes share their caller's snapshot and batch_save owns its rollback.
for _name, _method in ConfigManager.__dict__.items():
    if callable(_method) and not _name.startswith("_") and _name != "batch_save":
        setattr(DesktopConfigManager, _name, _serialized(_method))
