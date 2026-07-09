import os
import shutil
import subprocess
import sys


WRITE_PATTERNS = {
    ("runs", "trigger"),
    ("runs", "trigger-wait"),
    ("runs", "clear"),
    ("runs", "delete"),
    ("dags", "pause"),
    ("dags", "unpause"),
}
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


def is_truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_write_command(args):
    lowered = [str(item).strip().lower() for item in args if str(item).strip()]
    if len(lowered) >= 2 and (lowered[0], lowered[1]) in WRITE_PATTERNS:
        return True
    if lowered[:1] == ["api"]:
        for index, item in enumerate(args):
            if str(item).strip() in {"-X", "--method"} and index + 1 < len(args):
                if str(args[index + 1]).strip().upper() in WRITE_METHODS:
                    return True
    return False


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: run_af.py <af arguments>", file=sys.stderr)
        return 2

    if is_truthy(os.environ.get("AF_READ_ONLY", "true")) and is_write_command(args):
        print(
            "Blocked by AF_READ_ONLY=true. Confirm the exact Airflow target, set AF_READ_ONLY=false in the skill config, and retry.",
            file=sys.stderr,
        )
        return 3

    af_exe = shutil.which("af")
    if af_exe:
        command = [af_exe] + args
    else:
        uvx_exe = shutil.which("uvx")
        if not uvx_exe:
            print("Neither `af` nor `uvx` was found on PATH. Install astro-airflow-mcp or make uvx available.", file=sys.stderr)
            return 127
        command = [uvx_exe, "--from", "astro-airflow-mcp", "af"] + args

    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
