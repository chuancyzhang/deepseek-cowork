import json
import os
import tempfile
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage

from core.theme import default_design_tokens
from core.theme_package import build_asset_record
from core.theme_service import ThemeRepository, validate_theme_manifest


class ThemePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = ThemeRepository(self.temp_dir.name)
        self.defaults = default_design_tokens()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _image_path(self):
        path = os.path.join(self.temp_dir.name, "background.png")
        image = QImage(64, 48, QImage.Format_ARGB32)
        image.fill(QColor("#336699"))
        self.assertTrue(image.save(path, "PNG"))
        return path

    def test_manifest_rejects_hidden_protected_component_and_action_fields(self):
        base = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "safe",
            "name": "Safe",
            "overrides": {},
            "assets": {},
            "surfaces": {},
            "components": {"composer.submit": {"visible": False}},
            "content": {},
        }
        with self.assertRaisesRegex(ValueError, "受保护"):
            validate_theme_manifest(base, self.defaults)
        base["components"] = {"left.capabilities": {"action": "delete_all"}}
        with self.assertRaisesRegex(ValueError, "禁止|未知"):
            validate_theme_manifest(base, self.defaults)

    def test_preview_asset_import_and_background_package_round_trip(self):
        preview = self.repository.write_preview(
            name="Asset theme",
            overrides={},
            default_tokens=self.defaults,
        )
        imported = self.repository.import_preview_asset(
            preview_id=preview["preview_id"],
            preview_revision=preview["revision"],
            asset_id="main-bg",
            source_path=self._image_path(),
            default_tokens=self.defaults,
        )
        patched = self.repository.patch_preview(
            preview_id=preview["preview_id"],
            preview_revision=imported["revision"],
            set_overrides={},
            unset_tokens=[],
            operations=[
                {
                    "op": "set",
                    "path": "/surfaces/conversation.canvas/background/layers",
                    "value": [{"type": "image", "asset": "main-bg", "fit": "cover"}],
                }
            ],
            default_tokens=self.defaults,
        )
        result = self.repository.commit_preview(
            preview_id=preview["preview_id"],
            preview_revision=patched["revision"],
            activate=True,
            default_tokens=self.defaults,
        )
        path = self.repository.theme_path(result["theme"]["id"])
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertIn(manifest["assets"]["main-bg"]["path"], archive.namelist())
        loaded = self.repository.get_theme(result["theme"]["id"])
        self.assertEqual(
            loaded["surfaces"]["conversation.canvas"]["background"]["layers"][0]["asset"],
            "main-bg",
        )

    def test_package_reader_rejects_path_traversal(self):
        path = os.path.join(self.temp_dir.name, "bad.cowork-theme")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../escape.png", b"bad")
            archive.writestr("manifest.json", "{}")
        with self.assertRaisesRegex(ValueError, "不安全"):
            self.repository.read_theme_file(path, self.defaults)

    def test_asset_magic_not_extension_controls_format(self):
        path = os.path.join(self.temp_dir.name, "fake.png")
        with open(path, "wb") as stream:
            stream.write(b"not a png")
        with self.assertRaisesRegex(ValueError, "格式无效"):
            build_asset_record("fake", path)

    def test_manifest_rejects_invalid_rgb_channels_and_duplicate_asset_hashes(self):
        record, _data = build_asset_record("one", self._image_path())
        payload = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "strict",
            "name": "Strict",
            "overrides": {},
            "assets": {
                "one": record,
                "two": {**record, "path": "assets/duplicate.png"},
            },
            "surfaces": {},
            "components": {},
            "content": {},
        }
        with self.assertRaisesRegex(ValueError, "重复图片"):
            validate_theme_manifest(payload, self.defaults)
        payload["assets"] = {}
        payload["surfaces"] = {
            "home.hero": {
                "background": {
                    "layers": [{"type": "solid", "color": "rgb(999, 0, 0)"}]
                }
            }
        }
        with self.assertRaisesRegex(ValueError, "0–255"):
            validate_theme_manifest(payload, self.defaults)


if __name__ == "__main__":
    unittest.main()
