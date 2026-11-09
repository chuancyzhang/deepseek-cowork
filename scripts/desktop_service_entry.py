"""Frozen stdio service entry; Electron owns the service lifecycle."""
from core.desktop_service import main

if __name__ == "__main__":
    raise SystemExit(main())
