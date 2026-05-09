import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.single_instance import notify_existing_ui


def main():
    if len(sys.argv) < 2:
        return 2
    return 0 if notify_existing_ui(sys.argv[1], timeout_ms=2000) else 1


if __name__ == "__main__":
    raise SystemExit(main())
