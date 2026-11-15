"""The deployment folder must work without the desktop repository on sys.path."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from cryptography.fernet import Fernet


def test_platform_folder_runs_on_its_own(tmp_path):
    source = Path(__file__).resolve().parents[1] / "web/platform"
    deployment = tmp_path / "standalone-platform"
    from scripts.package_enterprise import copy_source
    copy_source(source, deployment)
    env = dict(os.environ, PYTHONPATH="", PYTHONUTF8="1", COWORK_DEVELOPMENT="1",
               COWORK_DATABASE_URL="sqlite:///" + str(tmp_path / "validation.sqlite3"),
               COWORK_DATA_DIR=str(tmp_path / "releases"), COWORK_PLATFORM_ID="isolated-deployment",
               COWORK_PLATFORM_NAME="部署验证", COWORK_PUBLIC_URL="http://localhost:8789",
               COWORK_ENCRYPTION_KEY=Fernet.generate_key().decode())
    migrated = subprocess.run([sys.executable, "-m", "server", "migrate"], cwd=deployment, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    assert "数据库结构版本 4 已就绪" in migrated.stdout
    assert not (deployment / ".env.local").exists()
    code = """
from fastapi.testclient import TestClient
from server.app import create_app
with TestClient(create_app(), base_url='http://localhost:8789') as client:
    assert client.get('/healthz').json()['status'] == 'ok'
    assert client.get('/healthz').json()['schema_version'] == 4
    assert client.get('/api/v1/platform-info').json()['platform_id'] == 'isolated-deployment'
    assert client.get('/api/v1/platform-info').json()['client_distribution']['app_layouts'] == ['electron']
    assert client.get('/api/v1/client-releases/recommended?app_layout=qt').status_code == 410
    assert 'platform.js' in client.get('/app').text
    assert client.get('/assets/platform.css').status_code == 200
print('STANDALONE_PLATFORM_OK')
"""
    started = subprocess.run([sys.executable, "-c", code], cwd=deployment, env=env,
                             capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert started.returncode == 0, started.stdout + started.stderr
    assert "STANDALONE_PLATFORM_OK" in started.stdout


def test_deployment_snapshot_excludes_credentials_and_changes_with_source(tmp_path):
    from scripts.package_enterprise import copy_source, source_digest, validate_destination
    import pytest
    source = Path(__file__).resolve().parents[1] / "web/platform"
    copied = tmp_path / "source"
    original = copy_source(source, copied)
    (copied / ".env.local").write_text("PRIVATE_CONFIGURATION=do-not-ship")
    (copied / "server/__pycache__").mkdir()
    (copied / "server/__pycache__/stale.pyc").write_bytes(b"stale")
    clean = tmp_path / "clean"
    assert copy_source(copied, clean) == original
    assert not (clean / ".env.local").exists()
    assert not (clean / "server/__pycache__").exists()
    validate_destination(clean)
    with pytest.raises(ValueError, match="不能覆盖"):
        validate_destination(copied)
    (clean / "server/releases.py").write_text("changed source")
    changed = copy_source(clean, tmp_path / "changed")
    assert source_digest(changed) != source_digest(original)


def test_offline_start_verifies_bundle_before_import_and_never_creates_credentials(tmp_path):
    import json
    import pytest
    from scripts.package_enterprise import copy_source, sha256
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell startup contract")
    package = tmp_path / "offline"
    hashes = copy_source(Path(__file__).resolve().parents[1] / "web/platform", package)
    (package / "images.tar").write_bytes(b"test image archive")
    hashes["images.tar"] = sha256(package / "images.tar")
    manifest = {"schema_version": 1, "target": "linux-amd64", "database_schema": 4, "version": "test",
        "platform_image": "qa-platform", "platform_image_id": "sha256:platform",
        "database_image": "qa-database", "database_image_id": "sha256:database", "files": hashes}
    (package / "deployment.json").write_text(json.dumps(manifest), encoding="utf-8")
    calls = tmp_path / "docker-calls"
    def quote(path):
        return "'" + str(path).replace("'", "''") + "'"
    wrapper = tmp_path / "verify.ps1"
    wrapper.write_text("""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
function global:docker {
    $global:LASTEXITCODE = 0
    Add-Content -LiteralPath CALLS -Value $args[0]
    if ($args[0] -eq 'image') {
        if ($args[-1] -eq 'qa-platform') { 'sha256:platform linux/amd64' }
        else { 'sha256:database linux/amd64' }
    }
}
& SCRIPT -VerifyOnly
""".replace("CALLS", quote(calls)).replace("SCRIPT", quote(package / "start-local.ps1")), encoding="utf-8")
    def run():
        return subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    valid = run()
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert not (package / ".env.local").exists()
    assert calls.read_text().splitlines() == ["info", "load", "image", "image", "run"]
    calls.unlink()
    (package / "images.tar").write_bytes(b"corrupt archive")
    corrupt = run()
    assert corrupt.returncode != 0 and "checksum mismatch" in corrupt.stderr
    assert calls.read_text().splitlines() == ["info"]
    calls.unlink()
    (package / "deployment.json").unlink()
    legacy = run()
    assert legacy.returncode != 0 and "Legacy offline package" in legacy.stderr
    assert calls.read_text().splitlines() == ["info"]
