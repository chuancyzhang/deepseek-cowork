"""Serialize desktop configuration access and report durable-save failures."""
import copy
from contextlib import contextmanager
from functools import wraps
import threading

from core.config_manager import ConfigManager


def _serialized(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self._desktop_lock:
            previous = copy.deepcopy(getattr(self, "config", None))
            try:
                return method(self, *args, **kwargs)
            except BaseException:
                if previous is not None:
                    self.config = previous
                raise
    return invoke


class DesktopConfigManager(ConfigManager):
    def __init__(self, *args, **kwargs):
        self._desktop_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def _write_config(self):
        # The legacy UI could print an error and report success to its caller.
        # RPC must retain the form and return a failure if fsync/replace fails.
        self._write_config_or_raise()

    @contextmanager
    def batch_save(self):
        with self._desktop_lock:
            previous = copy.deepcopy(self.config)
            try:
                with super().batch_save():
                    yield
            except BaseException:
                self.config = previous
                raise


# Existing normalization and migration stay authoritative. Lock public entry
# points together, including getters that persist a normalized legacy value.
for _name, _method in ConfigManager.__dict__.items():
    if callable(_method) and not _name.startswith("_") and _name != "batch_save":
        setattr(DesktopConfigManager, _name, _serialized(_method))
