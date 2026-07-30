import json
import os
import tempfile
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage

from core.theme import default_design_tokens
from core.theme_package import build_asset_record
from core.theme_service import ThemeRepository, theme_manifest_schema, validate_theme_manifest


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
            "workspace_scene": {"attachment": "fixed", "layers": []},
            "surfaces": {},
            "components": {"composer.submit": {"visible": False}},
            "content": {},
        }
        with self.assertRaisesRegex(ValueError, "受保护"):
            validate_theme_manifest(base, self.defaults)
        base["components"] = {"left.capabilities": {"action": "delete_all"}}
        with self.assertRaisesRegex(ValueError, "禁止|未知"):
            validate_theme_manifest(base, self.defaults)

    def test_manifest_rejects_retired_system_titlebar_fields_explicitly(self):
        base = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "native-titlebar",
            "name": "Native titlebar",
            "overrides": {},
            "assets": {},
            "workspace_scene": {"attachment": "fixed", "layers": []},
            "surfaces": {},
            "components": {},
            "content": {},
        }
        cases = (
            ("surfaces", "window.titlebar", {"style": {"background": "#ffffff"}}),
            ("components", "titlebar.close", {"visible": True}),
            ("content", "brand.tagline", "AI workspace"),
        )
        for section, field, value in cases:
            with self.subTest(field=field):
                payload = {**base, section: {field: value}}
                with self.assertRaisesRegex(ValueError, "系统标题栏不支持主题覆盖"):
                    validate_theme_manifest(payload, self.defaults)

    def test_manifest_keeps_brand_title_as_window_copy(self):
        payload = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "brand-title",
            "name": "Brand title",
            "overrides": {},
            "assets": {},
            "workspace_scene": {"attachment": "fixed", "layers": []},
            "surfaces": {},
            "components": {},
            "content": {"brand.title": "My Cowork"},
        }
        validated = validate_theme_manifest(payload, self.defaults)
        self.assertEqual(validated["content"]["brand.title"], "My Cowork")

    def test_home_card_schema_uses_new_semantic_ids_and_accepts_legacy_aliases(self):
        schema = theme_manifest_schema()
        self.assertIn("home.card.finance", schema["components"])
        self.assertIn("home.card.data", schema["components"])
        self.assertIn("home.card.browser", schema["components"])
        self.assertNotIn("home.card.files", schema["components"])
        self.assertEqual(
            schema["content_keys"]["home.card.data.title"],
            "数据分析",
        )

        payload = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "legacy-home-cards",
            "name": "Legacy home cards",
            "overrides": {},
            "assets": {},
            "workspace_scene": {"attachment": "fixed", "layers": []},
            "surfaces": {},
            "components": {
                "home.card.files": {
                    "style": {"background": "#f4f4ff"},
                    "layout": {"row": 0, "column": 1},
                },
                "home.card.images": {"visible": False},
                "home.card.office": {"icon": {"source": "builtin", "name": "fa5s.globe"}},
            },
            "content": {
                "home.card.files.title": "市场研究",
                "home.card.images.description": "分析本地数据",
                "home.card.office.title": "操作网页",
            },
        }

        normalized = validate_theme_manifest(payload, self.defaults)

        self.assertEqual(
            normalized["components"]["home.card.finance"]["layout"],
            {"row": 0, "column": 1},
        )
        self.assertFalse(normalized["components"]["home.card.data"]["visible"])
        self.assertEqual(
            normalized["components"]["home.card.browser"]["icon"]["name"],
            "fa5s.globe",
        )
        self.assertEqual(normalized["content"]["home.card.finance.title"], "市场研究")
        self.assertEqual(normalized["content"]["home.card.data.description"], "分析本地数据")
        self.assertEqual(normalized["content"]["home.card.browser.title"], "操作网页")
        self.assertNotIn("home.card.files", normalized["components"])

    def test_home_card_manifest_rejects_mixed_legacy_and_canonical_keys(self):
        payload = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "mixed-home-cards",
            "name": "Mixed home cards",
            "overrides": {},
            "assets": {},
            "workspace_scene": {"attachment": "fixed", "layers": []},
            "surfaces": {},
            "components": {
                "home.card.files": {"visible": True},
                "home.card.finance": {"visible": True},
            },
            "content": {},
        }
        with self.assertRaisesRegex(ValueError, "新旧组件键"):
            validate_theme_manifest(payload, self.defaults)

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
                    "path": "/workspace_scene/layers",
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
            loaded["workspace_scene"]["layers"][0]["asset"],
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
            "workspace_scene": {"attachment": "fixed", "layers": []},
            "surfaces": {},
            "components": {},
            "content": {},
        }
        with self.assertRaisesRegex(ValueError, "重复图片"):
            validate_theme_manifest(payload, self.defaults)
        payload["assets"] = {}
        payload["workspace_scene"] = {
            "attachment": "fixed",
            "layers": [{"type": "solid", "color": "rgb(999, 0, 0)"}],
        }
        with self.assertRaisesRegex(ValueError, "0–255"):
            validate_theme_manifest(payload, self.defaults)

    def test_workspace_scene_is_required_and_rejects_nested_backgrounds(self):
        payload = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "scene-owner",
            "name": "Scene owner",
            "overrides": {},
            "assets": {},
            "surfaces": {},
            "components": {},
            "content": {},
        }
        with self.assertRaisesRegex(ValueError, "必须声明 workspace_scene"):
            validate_theme_manifest(payload, self.defaults)
        payload["workspace_scene"] = {"attachment": "fixed", "layers": []}
        payload["surfaces"] = {
            "conversation.timeline": {"background": {"layers": []}}
        }
        with self.assertRaisesRegex(ValueError, "未知字段.*background"):
            validate_theme_manifest(payload, self.defaults)

    def test_workspace_scene_validates_order_unique_image_and_major_grid(self):
        payload = {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "scene-grid",
            "name": "Scene grid",
            "overrides": {},
            "assets": {},
            "workspace_scene": {
                "attachment": "fixed",
                "layers": [
                    {"type": "solid", "color": "#f4f1e8"},
                    {
                        "type": "grid",
                        "color": "rgba(166,116,24,0.10)",
                        "spacing": 32,
                        "major_every": 4,
                        "major_color": "rgba(200,63,104,0.12)",
                    },
                ],
            },
            "surfaces": {
                "conversation.timeline": {
                    "material": {"kind": "tint", "color": "#ffffff", "opacity": 0.9}
                }
            },
            "components": {},
            "content": {},
        }
        normalized = validate_theme_manifest(payload, self.defaults)
        self.assertEqual(normalized["workspace_scene"]["layers"][1]["major_every"], 4)
        payload["workspace_scene"]["layers"] = [
            payload["workspace_scene"]["layers"][1],
            payload["workspace_scene"]["layers"][0],
        ]
        with self.assertRaisesRegex(ValueError, "图层顺序"):
            validate_theme_manifest(payload, self.defaults)


if __name__ == "__main__":
    unittest.main()
