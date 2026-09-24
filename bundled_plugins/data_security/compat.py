"""Exact-token compatibility only. No sensitive-data recognition or scanning."""
from __future__ import annotations

import re
from .vault import TokenVault

TOKEN = re.compile(r"\[\[CW:[a-f0-9]{12}:[a-z_]+:[a-f0-9]{16}\]\]")
# Host-owned implementations only; never infer safe fields from third-party schemas.
SAFE_FIELDS = {
    "text_file_read": {"path"}, "document_read": {"path", "file_path"},
    "workspace_list_files": {"path"}, "read_web_article": {"url"},
}


class Restorer:
    def __init__(self, scope, data_dir=None):
        self.vault = TokenVault(scope, data_dir)
        self.values = None

    def restore(self, text):
        if self.values is None:
            self.values = self.vault.read()
        return TOKEN.sub(lambda match: self.values.get(match[0], match[0]), text)


def resolve_arguments(name, arguments, restore, *, trusted=False):
    if not isinstance(arguments, dict):
        return {"status": "original_required", "arguments": arguments}
    allowed = SAFE_FIELDS.get(name, set()) if trusted else set()
    result = dict(arguments)
    for key, value in arguments.items():
        if "[[CW:" not in str(value):
            continue
        if key not in allowed or not isinstance(value, str):
            return {"status": "original_required", "arguments": arguments}
        result[key] = restore(value)
        if "[[CW:" in result[key]:
            return {"status": "original_required", "arguments": arguments}
    return {"status": "complete", "arguments": result}
