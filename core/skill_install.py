"""Deterministic source preparation. No model calls or installation-plan state."""
from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time
from urllib.parse import urljoin, urlparse

import requests

from core.skill_adapter import resolve_agent_skill_source
from core.skill_package import MAX_PACKAGE_BYTES, file_digest, package_path, copy_asset

log = logging.getLogger("skills.install")


def validate_public_https_url(value):
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Skill source must be a public HTTPS URL without embedded credentials.")
    host = parsed.hostname.rstrip(".")
    if host.casefold() == "localhost" or host.casefold().endswith(".localhost"):
        raise ValueError("Local network addresses are not remote skill sources.")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Skill source must resolve to public IP addresses.")
    return value


def check_cancel(context=None):
    from core.execution_authorization import current_authorization
    context = context or {}
    authorization = current_authorization(context)
    if authorization is not None:
        authorization.check()
    abort = context.get("abort_check")
    if callable(abort) and abort():
        raise RuntimeError("Skill operation cancelled.")


def _download(url, target, context):
    started = time.monotonic()
    for _ in range(6):
        check_cancel(context)
        url = validate_public_https_url(url)
        with requests.get(url, stream=True, allow_redirects=False, timeout=(10, 20)) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("Location", ""))
                continue
            response.raise_for_status()
            total = 0
            with open(target, "wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    check_cancel(context)
                    total += len(chunk)
                    if total > MAX_PACKAGE_BYTES or time.monotonic() - started > 90:
                        raise ValueError("Skill download exceeds 50 MiB or 90 seconds.")
                    handle.write(chunk)
            return url
    raise ValueError("Too many redirects in skill download.")


def _git(args, directory, context):
    from core.execution_authorization import require_process_authorization
    from core.process_utils import popen_external_program, subprocess_kwargs_no_window, terminate_process_tree
    from core.sandbox_runtime import build_sandbox_env
    require_process_authorization()
    env = build_sandbox_env()
    executable = shutil.which("git", path=env.get("PATH"))
    if not executable:
        raise ValueError("Git runtime is unavailable; repair the application's Git component.")
    # Disable hooks, helper prompts and protocol changes for source preparation.
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)
    command = [executable, "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
               "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
               "-c", "http.followRedirects=false", *args]
    check_cancel(context)
    # File-backed output avoids pipe blockage during large fetches.
    with tempfile.TemporaryFile() as output:
        process = popen_external_program(command, cwd=directory, env=env, stdout=output,
                                         stderr=output, start_new_session=os.name != "nt",
                                         **subprocess_kwargs_no_window())
        started = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel(context)
                if time.monotonic() - started > 90:
                    raise TimeoutError("Git skill download timed out after 90 seconds.")
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                if not terminate_process_tree(process):
                    raise RuntimeError("Git process cleanup failed; inspect the running source download before retrying.")
        output.seek(0)
        result = output.read(64 * 1024).decode("utf-8", errors="replace")
    if process.returncode:
        raise ValueError("Git source preparation failed: " + result[-1500:])
    return result.strip()


@contextmanager
def prepare_source(source_path, *, skill_paths=None, ref="", source_type="auto", expected_sha256="", context=None):
    """Resolve source directories and provenance before staging the installable contents."""
    if source_type not in {"auto", "repository", "package"}:
        raise ValueError("source_type must be auto, repository or package.")
    if skill_paths is not None and (not isinstance(skill_paths, list) or not skill_paths
                                   or any(not isinstance(path, str) for path in skill_paths)):
        raise ValueError("skill_paths must be a non-empty array of relative directories.")
    if ref and (not isinstance(ref, str) or ref.startswith("-") or re.search(r"[\s\x00-\x1f]", ref)):
        raise ValueError("Invalid Git ref.")
    temporary = []
    source = str(source_path or "").strip()
    provenance = {}
    try:
        check_cancel(context)
        if source.startswith("https://"):
            validate_public_https_url(source)
            parsed = urlparse(source)
            parts = parsed.path.strip("/").split("/")
            is_repository = source_type == "repository" or (source_type == "auto" and (
                parsed.path.endswith(".git") or parsed.hostname == "github.com" and len(parts) >= 2
                and (len(parts) == 2 or parts[2] == "tree")))
            temporary.append(tempfile.mkdtemp(prefix="cowork-skill-download-"))
            work = Path(temporary[-1])
            if is_repository:
                if parsed.hostname == "github.com" and len(parts) > 2:
                    if parts[2] != "tree" or len(parts) < 4:
                        raise ValueError("Use a repository root with explicit ref and skill_paths.")
                    if ref and ref != parts[3]:
                        raise ValueError("URL ref and requested ref differ; use the repository root URL.")
                    ref = ref or parts[3]
                    skill_paths = skill_paths or (["/".join(parts[4:])] if len(parts) > 4 else None)
                    source = f"https://github.com/{parts[0]}/{parts[1]}"
                _git(["init", "--quiet"], work, context)
                _git(["fetch", "--depth=1", "--no-tags", "--", source, ref or "HEAD"], work, context)
                _git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], work, context)
                commit = _git(["rev-parse", "HEAD"], work, context)
                if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
                    raise ValueError("Git did not return a valid installed commit.")
                provenance = {"repository_url": source, "requested_ref": ref or "HEAD", "commit": commit}
                root = work
            else:
                if ref:
                    raise ValueError("ref applies only to Git repositories.")
                extension = Path(parsed.path).suffix.casefold()
                if extension not in {".zip", ".md", ".markdown"}:
                    raise ValueError("Provide a direct ZIP/Markdown URL or a repository with skill_paths; installation pages are not packages.")
                downloaded = work / ("source" + extension)
                final_url = _download(source, downloaded, context)
                provenance = {"url": source, "resolved_url": final_url}
                if expected_sha256:
                    digest = file_digest(downloaded)
                    if digest != expected_sha256:
                        raise ValueError("Downloaded package differs from the inspected content.")
                    provenance["download_sha256"] = digest
                root, extracted = resolve_agent_skill_source(str(downloaded), select_root=not skill_paths)
                if extracted:
                    temporary.append(extracted)
        else:
            if "://" in source:
                raise ValueError("Remote skill sources require HTTPS.")
            if ref:
                raise ValueError("ref applies only to remote Git repositories.")
            path = Path(source)
            provenance = {"local_name": path.name}
            if path.is_file():
                temporary.append(tempfile.mkdtemp(prefix="cowork-skill-local-"))
                retained = Path(temporary[-1]) / ("source" + path.suffix)
                evidence = copy_asset(path, retained, expected_sha256, include_digest=False)
                if expected_sha256:
                    provenance["package_sha256"] = evidence["sha256"]
                source = str(retained)
                path = retained
            if path.is_dir() and skill_paths:
                root = path
            else:
                root, extracted = resolve_agent_skill_source(source, select_root=not skill_paths)
                if extracted:
                    temporary.append(extracted)
        root = Path(root)
        selected = [package_path(root, path) for path in skill_paths] if skill_paths else [root]
        if len({str(path).casefold() for path in selected}) != len(selected):
            raise ValueError("Duplicate skill paths.")
        entries = []
        for path in selected:
            if not (path / "SKILL.md").is_file():
                raise ValueError("SKILL.md not found; specify the skill directory inside the repository.")
            entries.append((path, {**provenance, "skill_path": path.relative_to(root).as_posix()}))
        check_cancel(context)
        yield entries
    finally:
        for directory in reversed(temporary):
            shutil.rmtree(directory, ignore_errors=True)
