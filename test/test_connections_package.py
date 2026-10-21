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
        self.template = self.dist / "connection_templates.json"

    def test_retired_templates_are_not_distributable(self):
        self.template.write_text('{"templates": []}', encoding="utf-8")
        with self.assertRaisesRegex(package_release.PackageAuditError, "Forbidden packaged"):
            package_release.audit_distribution(self.dist)

    def test_local_state_and_database_sidecars_are_rejected(self):
        for name in ("connections.sqlite3", "connections.sqlite3-wal", "connections.sqlite3-shm",
                     "knowledge_library.sqlite3", "knowledge_library.sqlite3.backup", "connections.sqlite3.backup", "connection_templates.local.json", "app_variables.json",
                     "user_data/nested.json", "connection_locks/account.lock"):
            with self.subTest(name=name):
                path = self.dist / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("local-only", encoding="utf-8")
                try:
                    with self.assertRaisesRegex(package_release.PackageAuditError, "Forbidden packaged"):
                        package_release.audit_distribution(self.dist)
                finally:
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
