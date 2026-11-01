"""Carry an installer's startup request to the UI that actually becomes ready."""
import json
import os
from pathlib import Path
import re

from core.update_coordinator import atomic_json, update_log


def update_startup_request(install_dir, *, consume=False):
    read = os.environ.pop if consume else os.environ.get
    receipt = read("COWORK_UPDATE_RECEIPT", "")
    identity = read("COWORK_UPDATE_ID", "")
    if not receipt and not identity:
        return None
    return {"id": identity, "receipt": receipt, "install_dir": os.path.abspath(install_dir)}


def _same_path(left, right):
    return isinstance(left, str) and bool(left) and os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def acknowledge_update_request(request, root, install_dir, version):
    """Never let IPC choose a write path, another installation, or an unfinished install."""
    identity = request.get("id", "") if isinstance(request, dict) else ""
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        update_log(root, "startup_receipt_rejected", reason="invalid_transaction")
        return False
    transaction = Path(root, "transactions", identity)
    receipt = transaction / "opened.json"
    try:
        if not _same_path(request.get("receipt"), receipt) or not _same_path(request.get("install_dir"), install_dir):
            raise ValueError("启动确认不属于当前安装目录或更新事务。")
        config = json.loads((transaction / "config.json").read_text(encoding="utf-8-sig"))
        commit = json.loads((transaction / "commit.json").read_text(encoding="utf-8-sig"))
        state = json.loads((transaction / "transaction.json").read_text(encoding="utf-8-sig"))
        if (any(item.get("id") != identity for item in (config, commit, state))
                or not _same_path(config.get("install_dir"), install_dir)
                or state.get("phase") not in ("installed", "installed_not_opened")
                or (transaction / "abort.json").exists()):
            raise ValueError("更新事务尚未安装完成或不属于当前应用。")
        # Report the actual running version; only the installer decides whether
        # it matches the target. A wrongly labelled package must never pass.
        atomic_json(str(receipt), {"id": identity, "version": version, "pid": os.getpid(),
                                   "install_dir": os.path.abspath(install_dir)})
        update_log(root, "startup_receipt_written", transaction=identity, app_pid=os.getpid(),
                   app_version=version, expected_version=config.get("version"))
        return True
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        update_log(root, "startup_receipt_rejected", transaction=identity, error=str(exc))
        return False
