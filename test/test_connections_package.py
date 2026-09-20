"""Connection distribution audit against a complete, controlled release fixture."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_package_release as release_tests

package_release = release_tests.package_release
ROOT = Path(__file__).resolve().parents[1]


class ConnectionPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        helper = release_tests.PackageReleaseTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.dist = helper._create_minimal_dist(self.temp.name)
        # The older fixture predates the WeCom bundle. Supply its actual checked-in
        # public metadata and executable for a full audit, without weakening checks.
        import shutil
        source = ROOT / "resources" / "wecom_cli"
        for relative in ("bundle.json", "LICENSE.txt", "bin/wecom-cli.exe"):
            shutil.copy2(source / relative, self.dist / "_internal/resources/wecom_cli" / relative)
        self.template = self.dist / "connection_templates.json"

    def test_public_template_bundle_passes_full_audit(self):
        self.template.write_bytes((ROOT / "docs/examples/connection_templates.json").read_bytes())
        report = package_release.audit_distribution(self.dist, max_dist_mb=20)
        self.assertGreater(report["file_count"], len(package_release.REQUIRED_PATHS))
        self.assertEqual(report["wecom_cli_assets"]["version"], package_release.WECOM_CLI_VERSION)

    def test_credentials_in_template_fail_before_packaging(self):
        value = json.loads((ROOT / "docs/examples/connection_templates.json").read_text(encoding="utf-8"))
        value["templates"][0]["parameters"]["client_secret"] = "forbidden-test-secret"
        self.template.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(package_release.PackageAuditError, "secret-bearing"):
            package_release.audit_distribution(self.dist, max_dist_mb=20)

    def test_local_state_and_database_sidecars_are_rejected(self):
        for name in ("connections.sqlite3", "connections.sqlite3-wal", "connections.sqlite3-shm",
                     "knowledge_library.sqlite3", "connection_templates.local.json", "app_variables.json",
                     "user_data/nested.json", "connection_locks/account.lock"):
            with self.subTest(name=name):
                path = self.dist / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("local-only", encoding="utf-8")
                try:
                    with self.assertRaisesRegex(package_release.PackageAuditError, "Forbidden packaged"):
                        package_release.audit_distribution(self.dist, max_dist_mb=20)
                finally:
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
