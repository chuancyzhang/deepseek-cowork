"""Read the saved working-directory changes with the existing Git runtime."""
import os
import subprocess
from pathlib import Path

from core.process_utils import posix_external_environment, subprocess_kwargs_no_window
from core.sandbox_runtime import get_workspace_git
from core.workbench import WorkbenchError

_TEXT_LIMIT = 1024 * 1024


def read_changes(root: Path, path: str | None = None):
    if not root.is_dir():
        raise WorkbenchError("workspace_missing", "工作目录暂时不可用。请恢复目录或重新选择后再试。")
    executable = get_workspace_git()
    if not executable:
        raise WorkbenchError("git_unavailable", "Git 组件暂时不可用。请修复应用安装后重试。")
    env = posix_external_environment()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        env.pop(name, None)
    env.update(LC_ALL="C", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")

    def git(*args, codes=(0,), probe=False):
        try:
            result = subprocess.run(
                [executable, "--no-optional-locks", "--literal-pathspecs", "-c", "core.fsmonitor=false",
                 "-c", f"core.bigFileThreshold={_TEXT_LIMIT}", *args],
                cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
                **subprocess_kwargs_no_window(),
            )
        except subprocess.TimeoutExpired as failure:
            raise WorkbenchError("git_timeout", "读取变更耗时过长，请重试。") from failure
        except OSError as failure:
            raise WorkbenchError("git_unavailable", "无法启动 Git 组件，请重试。") from failure
        if probe and b"not a git repository" in result.stderr:
            return ""
        if result.returncode not in codes:
            reason = result.stderr.decode("utf-8", "replace").splitlines()
            raise WorkbenchError("git_read_failed", "无法读取 Git 变更。" + (" " + reason[0] if reason else ""))
        return result.stdout.decode("utf-8", "replace")

    if git("rev-parse", "--is-inside-work-tree", probe=True).strip() != "true":
        return {"repository": False, "files": []} if path is None else {"patch": ""}
    base = git("rev-parse", "--verify", "-q", "HEAD", codes=(0, 1)).strip()
    if not base:
        base = git("hash-object", "-t", "tree", "--stdin").strip()
    diff = ("diff", "--relative", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color", "--ignore-submodules=none")
    untracked = ("ls-files", "--others", "--exclude-standard", "-z", "--")

    if path is not None:
        try:
            relative = Path(os.path.abspath(root / path)).relative_to(root).as_posix()
        except ValueError as failure:
            raise WorkbenchError("path_outside_workspace", "文件不属于当前工作目录。") from failure
        patch = git(*diff, "--patch", "--unified=3", base, "--", relative)
        if not patch and relative in git(*untracked, relative).split("\0"):
            patch = git("diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=3",
                        "--", "/dev/null", relative, codes=(0, 1))
        if len(patch) > 256_000 or patch.count("\n") > 4000:
            return {"patch": "", "too_large": True}
        return {"patch": patch}

    entries = {}
    records = iter(git(*diff, "--raw", "--numstat", "-z", base, "--", ".").split("\0"))
    for record in records:
        if not record:
            continue
        if record.startswith(":"):
            mode, _, _, status = record.split()[1:]
            relative = next(records)
            kind = {"A": "added", "D": "deleted"}.get(status, "modified")
            if mode == "160000":
                kind = "submodule"
            entries[relative] = _entry(root, relative, kind)
        else:
            added, removed, relative = record.split("\t", 2)
            entries[relative].update(added=int(added) if added != "-" else None,
                                     removed=int(removed) if removed != "-" else None)
    for relative in git(*untracked, ".").split("\0"):
        if not relative or relative.endswith("/"):
            continue
        if relative in entries:
            # A staged deletion can leave a local file that the editor can still open.
            entries[relative]["status"] = "untracked_after_delete"
            continue
        target = root / relative
        try:
            if target.is_symlink():
                data = os.fsencode(os.readlink(target))
            elif target.is_file():
                with target.open("rb") as stream:
                    data = stream.read(_TEXT_LIMIT + 1)
            else:
                continue
        except FileNotFoundError:
            continue
        lines = None if len(data) > _TEXT_LIMIT or b"\0" in data[:8000] else data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))
        entries[relative] = _entry(root, relative, "added", lines, 0 if lines is not None else None)
    return {"repository": True, "files": sorted(entries.values(), key=lambda entry: entry["path"].casefold())}


def _entry(root, path, status, added=None, removed=None):
    return {"path": path, "absolute_path": str(root / path), "status": status, "added": added, "removed": removed}
