"""Optional, host-owned connections. Importing this package performs no initialization."""

from .errors import ConnectionError

__all__ = ["ConnectionBroker", "ConnectionError"]


def __getattr__(name):
    if name == "ConnectionBroker":
        from .broker import ConnectionBroker
        return ConnectionBroker
    raise AttributeError(name)
