"""Shared, read-only checks for the CLI runtimes shipped in Windows builds."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess


RIPGREP_VERSION = "15.1.0"
RIPGREP_ARCHIVE = "ripgrep-15.1.0-x86_64-pc-windows-msvc.zip"
RIPGREP_URL = f"https://github.com/BurntSushi/ripgrep/releases/download/{RIPGREP_VERSION}/{RIPGREP_ARCHIVE}"
RIPGREP_ARCHIVE_SHA256 = "124510B94B6BAA3380D051FDF4650EAA80A302C876D611E9DBA0B2E18D87493A"
RIPGREP_FILE_HASHES = {
    "rg.exe": "DECDD4992F3F1B9A5EF9898F1B40AB16886D579D6516B4EFD3D5EAA19364E408",
    "COPYING": "DFE7D0A6134A17D3DE7409762E08DC02133303912875CAB40A74BA07A390F85A",
    "LICENSE-MIT": "970813655A1BF777D2EAD189CEF71AB73AB92CE04DC864027D61A45DE03E6728",
    "UNLICENSE": "640514163B17F977ADC997CB16F51871122CFB0555EBAD1A3F01E167B7BA8857",
}
GIT_REQUIRED_FILES = (
    "bin/bash.exe",
    "cmd/git.exe",
    "etc/gitconfig",
    "mingw64/bin/git-credential-manager.exe",
    "mingw64/bin/git-credential-manager.exe.config",
    "mingw64/bin/gcmcore.dll",
    "mingw64/bin/Avalonia.Skia.dll",
    "mingw64/bin/SkiaSharp.dll",
    "mingw64/bin/libSkiaSharp.dll",
)


def _require_files(root: Path, names, label: str):
    missing = [name for name in names if not (root / name).is_file()]
    if missing:
        raise ValueError(
            f"Missing {label} runtime files at {root}: {', '.join(missing)}. "
            "Run scripts/fetch_runtimes.ps1 before building."
        )


def validate_ripgrep_bundle(root: Path) -> dict:
    root = Path(root)
    _require_files(root, RIPGREP_FILE_HASHES, "ripgrep")
    for name, expected in RIPGREP_FILE_HASHES.items():
        digest = hashlib.sha256()
        with (root / name).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest().upper()
        if actual != expected:
            raise ValueError(
                f"ripgrep {RIPGREP_VERSION} SHA256 mismatch for {name}: "
                f"expected={expected} actual={actual}"
            )
    return {
        "version": RIPGREP_VERSION,
        "sha256": RIPGREP_FILE_HASHES["rg.exe"],
        "bytes": (root / "rg.exe").stat().st_size,
    }


def _git_config_values(root: Path, key: str) -> list[str]:
    # Query only the shipped file; never read a developer's per-user config.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_CONFIG")}
    result = subprocess.run(
        [str(root / "cmd/git.exe"), "config", "--file", str(root / "etc/gitconfig"),
         "--no-includes", "--get-all", key],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=10, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode not in (0, 1):
        raise ValueError(f"Cannot inspect bundled Git {key}: {result.stderr.strip()}")
    return result.stdout.splitlines()


def validate_git_bundle(root: Path) -> dict:
    root = Path(root)
    _require_files(root, GIT_REQUIRED_FILES, "Git / Credential Manager")
    helpers = _git_config_values(root, "credential.helper")
    if helpers != ["manager"]:
        raise ValueError(
            f"Bundled Git must default to credential.helper=manager; found {helpers!r} in "
            f"{root / 'etc/gitconfig'}. Run scripts/fetch_runtimes.ps1 before building."
        )
    for key in ("credential.interactive", "credential.guiPrompt"):
        if any(value.lower() in {"false", "never", "no", "0"} for value in _git_config_values(root, key)):
            raise ValueError(f"Bundled Git disables account authorization with {key}.")
    return {"credential_helper": "manager", "gui_authentication": True}
