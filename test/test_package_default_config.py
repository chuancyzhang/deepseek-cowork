"""Local and server audits agree on shipped defaults versus private settings."""
import io
import json
from types import SimpleNamespace
import zipfile

import pytest
import test_package_release as release_tests
from web.platform.server.releases import REQUIRED_FILES, inspect_archive
from web.platform.server.security import PlatformError


DEFAULT = {"api_key": "", "base_url": "https://api.deepseek.com", "disabled_skills": []}


@pytest.mark.parametrize("path,content,allowed", [
    ("_internal/config.json", json.dumps(DEFAULT).encode(), True),
    ("_internal/config.json", json.dumps({**DEFAULT, "api_key": "test-secret"}).encode(), False),
    ("_internal/config.json", json.dumps({**DEFAULT, "base_url": "https://custom.invalid"}).encode(), False),
    ("_internal/config.json", json.dumps({**DEFAULT, "disabled_skills": ["custom"]}).encode(), False),
    ("_internal/config.json", json.dumps({**DEFAULT, "extra": "private"}).encode(), False),
    ("_internal/config.json", b'{"api_key":"secret","api_key":"","base_url":"https://api.deepseek.com","disabled_skills":[]}', False),
    ("_internal/config.json", b"not-json", False),
    ("_internal/config.json", b"\xff", False),
    ("_internal/config.json", b" " * 8193 + json.dumps(DEFAULT).encode(), False),
    ("config.json", json.dumps(DEFAULT).encode(), False),
    ("_internal/Config.json", json.dumps(DEFAULT).encode(), False),
])
def test_both_audits_validate_config_content(tmp_path, path, content, allowed):
    helper = release_tests.PackageReleaseTests()
    helper.setUp()
    try:
        dist = helper._create_minimal_dist(tmp_path)
        for name in REQUIRED_FILES:
            target = dist / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"runtime fixture")
        (dist / path).write_bytes(content)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for file in dist.rglob("*"):
                if file.is_file():
                    archive.write(file, "deepseek-cowork/" + file.relative_to(dist).as_posix())
        with zipfile.ZipFile(buffer) as archive:
            if allowed:
                release_tests.package_release.audit_distribution(dist)
                assert path in inspect_archive(archive, SimpleNamespace(max_unpacked_bytes=1024**3))
            else:
                with pytest.raises(release_tests.package_release.PackageAuditError):
                    release_tests.package_release.audit_distribution(dist)
                with pytest.raises(PlatformError) as error:
                    inspect_archive(archive, SimpleNamespace(max_unpacked_bytes=1024**3))
                assert error.value.code == "private_archive"
    finally:
        helper.tearDown()
