"""Launch the Electron desktop. Runtime work is owned by the native service."""
from scripts.run_desktop import main


if __name__ == "__main__":
    raise SystemExit(main())
