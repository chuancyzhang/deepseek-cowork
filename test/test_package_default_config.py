"""Electron packages allow shipped defaults, never private settings or old paths."""
import json

import pytest
import test_package_release as release_tests


DEFAULT = {"api_key": "", "base_url": "https://api.deepseek.com", "disabled_skills": []}
CONFIG_PATH = "resources/service/_internal/config.json"


@pytest.mark.parametrize("path,content,allowed", [
    (CONFIG_PATH, json.dumps(DEFAULT).encode(), True),
    (CONFIG_PATH, json.dumps({**DEFAULT, "api_key": "test-secret"}).encode(), False),
    (CONFIG_PATH, json.dumps({**DEFAULT, "base_url": "https://custom.invalid"}).encode(), False),
    (CONFIG_PATH, json.dumps({**DEFAULT, "disabled_skills": ["custom"]}).encode(), False),
    (CONFIG_PATH, json.dumps({**DEFAULT, "extra": "private"}).encode(), False),
    (CONFIG_PATH, b'{"api_key":"secret","api_key":"","base_url":"https://api.deepseek.com","disabled_skills":[]}', False),
    (CONFIG_PATH, b"not-json", False),
    (CONFIG_PATH, b"\xff", False),
    (CONFIG_PATH, b" " * 8193 + json.dumps(DEFAULT).encode(), False),
    ("config.json", json.dumps(DEFAULT).encode(), False),
    ("_internal/config.json", json.dumps(DEFAULT).encode(), False),
    ("_internal/Config.json", json.dumps(DEFAULT).encode(), False),
    ("resources/service/_internal/Config.json", json.dumps(DEFAULT).encode(), False),
])
def test_electron_audit_validates_config_content(tmp_path, path, content, allowed):
    helper = release_tests.PackageReleaseTests()
    helper.setUp()
    try:
        dist = helper._create_minimal_dist(tmp_path)
        (dist / path).parent.mkdir(parents=True, exist_ok=True)
        (dist / path).write_bytes(content)
        if allowed:
            release_tests.package_release.audit_distribution(dist)
        else:
            with pytest.raises(release_tests.package_release.PackageAuditError):
                release_tests.package_release.audit_distribution(dist)
    finally:
        helper.tearDown()
