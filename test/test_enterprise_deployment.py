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
    shutil.copytree(source, deployment, ignore=shutil.ignore_patterns("__pycache__", ".env", "certs", "data"))
    env = dict(os.environ, PYTHONPATH="", PYTHONUTF8="1", COWORK_DEVELOPMENT="1",
               COWORK_DATABASE_URL="sqlite:///" + str(tmp_path / "validation.sqlite3"),
               COWORK_DATA_DIR=str(tmp_path / "releases"), COWORK_PLATFORM_ID="isolated-deployment",
               COWORK_PLATFORM_NAME="部署验证", COWORK_PUBLIC_URL="http://localhost:8789",
               COWORK_ENCRYPTION_KEY=Fernet.generate_key().decode())
    migrated = subprocess.run([sys.executable, "-m", "server", "migrate"], cwd=deployment, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    code = """
from fastapi.testclient import TestClient
from server.app import create_app
with TestClient(create_app(), base_url='http://localhost:8789') as client:
    assert client.get('/healthz').json()['status'] == 'ok'
    assert client.get('/api/v1/platform-info').json()['platform_id'] == 'isolated-deployment'
    assert 'platform.js' in client.get('/app').text
    assert client.get('/assets/platform.css').status_code == 200
print('STANDALONE_PLATFORM_OK')
"""
    started = subprocess.run([sys.executable, "-c", code], cwd=deployment, env=env,
                             capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert started.returncode == 0, started.stdout + started.stderr
    assert "STANDALONE_PLATFORM_OK" in started.stdout
