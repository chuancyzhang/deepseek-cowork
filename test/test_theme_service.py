import json
import os
import tempfile
import unittest
import zipfile

from core.theme import default_design_tokens
from core.theme_service import (
    DEFAULT_THEME_ID,
    ThemeRepository,
    resolve_theme,
    theme_contrast_warnings,
    validate_theme_overrides,
)


class ThemeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = ThemeRepository(self.temp_dir.name)
        self.defaults = default_design_tokens()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_store_is_valid_default_state(self):
        snapshot = self.repository.load()
        self.assertEqual(snapshot.active_theme_id, DEFAULT_THEME_ID)
        self.assertEqual(snapshot.themes, tuple())

    def test_primary_override_derives_interaction_colors(self):
        resolved = resolve_theme(
            {
                "id": "custom",
                "name": "Blue",
                "base": DEFAULT_THEME_ID,
                "overrides": {"tokens": {"primary": "#3366cc"}},
            },
            self.defaults,
        )
        self.assertEqual(resolved["tokens"]["primary"], "#3366cc")
        self.assertNotEqual(resolved["tokens"]["primary_hover"], self.defaults["primary_hover"])
        self.assertEqual(resolved["tokens"]["accent_ai"], "#3366cc")

    def test_explicit_derived_token_wins(self):
        resolved = resolve_theme(
            {
                "id": "custom",
                "name": "Blue",
                "base": DEFAULT_THEME_ID,
                "overrides": {
                    "tokens": {
                        "primary": "#3366cc",
                        "primary_hover": "#123456",
                    }
                },
            },
            self.defaults,
        )
        self.assertEqual(resolved["tokens"]["primary_hover"], "#123456")

    def test_low_contrast_is_advisory(self):
        resolved = resolve_theme(
            {
                "id": "low-contrast",
                "name": "Low contrast",
                "base": DEFAULT_THEME_ID,
                "overrides": {
                    "tokens": {
                        "text_primary": "#ffffff",
                        "bg_main": "#ffffff",
                    }
                },
            },
            self.defaults,
        )
        warnings = theme_contrast_warnings(resolved)
        self.assertTrue(
            any(
                item["foreground"] == "text_primary" and item["background"] == "bg_main"
                for item in warnings
            )
        )

    def test_rejects_unknown_tokens_and_bad_ranges(self):
        with self.assertRaisesRegex(ValueError, "未知"):
            validate_theme_overrides(
                {"tokens": {"not_a_theme_token": "#ffffff"}},
                self.defaults,
            )
        with self.assertRaisesRegex(ValueError, "0.8"):
            validate_theme_overrides({"font_scale": 2}, self.defaults)
        with self.assertRaisesRegex(ValueError, "file_tab_min_width"):
            resolve_theme(
                {
                    "id": "invalid-tabs",
                    "name": "Invalid tabs",
                    "base": DEFAULT_THEME_ID,
                    "overrides": {
                        "tokens": {
                            "file_tab_min_width": 300,
                            "file_tab_preferred_width": 200,
                        }
                    },
                },
                self.defaults,
            )

    def test_repository_crud_and_active_theme(self):
        created = self.repository.upsert_theme(
            name="工作主题",
            overrides={"tokens": {"primary": "#3366cc"}},
            default_tokens=self.defaults,
        )["theme"]
        snapshot = self.repository.activate_theme(created["id"])
        self.assertEqual(snapshot.active_theme_id, created["id"])
        with open(self.repository.store_path, "r", encoding="utf-8") as stream:
            self.assertEqual(json.load(stream), {"active_theme_id": created["id"]})
        self.assertEqual(self.repository.get_theme(created["id"])["name"], "工作主题")
        snapshot = self.repository.delete_theme(created["id"])
        self.assertEqual(snapshot.active_theme_id, DEFAULT_THEME_ID)
        with open(self.repository.store_path, "r", encoding="utf-8") as stream:
            self.assertEqual(json.load(stream), {})

    def test_preview_commit_is_explicit_and_clears_ephemeral_file(self):
        preview = self.repository.write_preview(
            name="AI 主题",
            overrides={"density": "comfortable", "tokens": {"primary": "#2255aa"}},
            default_tokens=self.defaults,
            session_id="session-1",
        )
        self.assertTrue(os.path.exists(self.repository.preview_path))
        result = self.repository.commit_preview(
            preview_id=preview["preview_id"],
            preview_revision=preview["revision"],
            activate=True,
            default_tokens=self.defaults,
        )
        self.assertEqual(result["theme"]["name"], "AI 主题")
        self.assertFalse(os.path.exists(self.repository.preview_path))
        self.assertEqual(self.repository.load().active_theme_id, result["theme"]["id"])

    def test_export_import_round_trip_generates_new_identity(self):
        created = self.repository.upsert_theme(
            name="Exported",
            overrides={"radius_scale": 0.8},
            default_tokens=self.defaults,
        )["theme"]
        payload = self.repository.export_theme(created["id"], self.defaults)
        self.repository.delete_theme(created["id"])
        imported = self.repository.import_theme(payload, self.defaults)["theme"]
        self.assertNotEqual(imported["id"], created["id"])
        self.assertEqual(imported["overrides"], {"radius_scale": 0.8})

    def test_store_write_is_valid_theme_package(self):
        self.repository.upsert_theme(
            name="Persisted",
            overrides={},
            default_tokens=self.defaults,
        )
        with open(self.repository.store_path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        self.assertEqual(payload, {})
        theme_files = [
            name for name in os.listdir(self.repository.themes_dir)
            if name.endswith(".cowork-theme") and not name.startswith("_")
        ]
        self.assertEqual(len(theme_files), 1)
        with zipfile.ZipFile(os.path.join(self.repository.themes_dir, theme_files[0])) as archive:
            theme_payload = json.loads(archive.read("manifest.json").decode("utf-8"))
        self.assertEqual(
            set(theme_payload),
            {
                "format", "schema_version", "id", "name", "overrides",
                "assets", "workspace_scene", "surfaces", "components", "content",
            },
        )
        self.assertFalse(os.path.exists(os.path.join(self.repository.themes_dir, "default.json")))
        self.assertFalse(os.path.exists(os.path.join(self.repository.themes_dir, "default.cowork-theme")))

    def test_portable_json_dropped_into_theme_folder_is_discovered(self):
        os.makedirs(self.repository.themes_dir, exist_ok=True)
        path = os.path.join(self.repository.themes_dir, "portable.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "format": "cowork-theme",
                    "id": "portable",
                    "name": "Portable",
                    "overrides": {"font_scale": 1.1},
                },
                stream,
            )
        snapshot = self.repository.load()
        self.assertEqual(len(snapshot.themes), 1)
        self.assertEqual(snapshot.themes[0]["id"], "portable")
        self.assertEqual(snapshot.themes[0]["name"], "Portable")

        saved = self.repository.replace_state(
            themes=list(snapshot.themes),
            active_theme_id="portable",
            default_tokens=self.defaults,
            expected_revision=snapshot.revision,
            expected_theme_ids={"portable"},
        )
        self.assertEqual(saved.active_theme_id, "portable")
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.exists(self.repository.theme_path("portable")))

    def test_preview_patch_requires_current_revision_and_commit_binds_revision(self):
        preview = self.repository.write_preview(
            name="Patchable",
            overrides={"tokens": {"primary": "#3366cc"}},
            default_tokens=self.defaults,
        )
        patched = self.repository.patch_preview(
            preview_id=preview["preview_id"],
            preview_revision=preview["revision"],
            set_overrides={"tokens": {"bg_chat": "#101010"}},
            unset_tokens=["primary"],
            default_tokens=self.defaults,
        )
        self.assertEqual(patched["revision"], 2)
        self.assertEqual(patched["overrides"]["tokens"], {"bg_chat": "#101010"})
        with self.assertRaisesRegex(RuntimeError, "重新确认"):
            self.repository.commit_preview(
                preview_id=preview["preview_id"],
                preview_revision=1,
                activate=True,
                default_tokens=self.defaults,
            )

    def test_stale_settings_snapshot_cannot_overwrite_ai_theme(self):
        original = self.repository.load()
        created = self.repository.upsert_theme(
            name="AI Theme",
            overrides={"density": "compact"},
            default_tokens=self.defaults,
        )["theme"]
        with self.assertRaisesRegex(RuntimeError, "刷新"):
            self.repository.replace_state(
                themes=[],
                active_theme_id=DEFAULT_THEME_ID,
                default_tokens=self.defaults,
                expected_revision=original.revision,
                expected_theme_ids=set(),
            )
        self.assertEqual(self.repository.get_theme(created["id"])["name"], "AI Theme")


if __name__ == "__main__":
    unittest.main()
