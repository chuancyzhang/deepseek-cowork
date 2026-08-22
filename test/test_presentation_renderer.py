import os
import tempfile
import unittest
from unittest.mock import patch

from core.ppt_agent import (
    PPT_AGENT_OUTPUT_PPTX,
    build_ppt_agent_prompt,
    normalize_ppt_agent_output_format,
)
from core.presentation_renderer import (
    RENDERER_NONE,
    RENDERER_POWERPOINT,
    RENDERER_WPS,
    detect_presentation_renderer,
    detect_presentation_renderers,
    export_presentation_pngs,
    file_sha256,
)


class PresentationRendererTests(unittest.TestCase):
    def test_direct_pptx_prompt_is_template_driven_without_html_skills(self):
        result = build_ppt_agent_prompt(
            "生成一份季度汇报",
            template_file=r"D:\templates\brand.pptx",
            output_format=PPT_AGENT_OUTPUT_PPTX,
            template_screenshots=[r"D:\preview\slide-001.png"],
            renderer=RENDERER_POWERPOINT,
        )

        prompt = result["prompt"]
        self.assertEqual(result["output_format"], PPT_AGENT_OUTPUT_PPTX)
        self.assertIn("python-pptx", prompt)
        self.assertIn("OOXML", prompt)
        self.assertIn("克隆", prompt)
        self.assertIn(r"D:\templates\brand.pptx", prompt)
        self.assertIn(r"D:\preview\slide-001.png", prompt)
        self.assertIn("不要先生成 HTML", prompt)

    def test_unknown_output_format_defaults_to_pptx(self):
        self.assertEqual(normalize_ppt_agent_output_format(""), PPT_AGENT_OUTPUT_PPTX)

    def test_renderer_detection_prioritizes_powerpoint_then_wps(self):
        def probe(prog_id, timeout=12):
            del timeout
            if prog_id == "PowerPoint.Application":
                return True
            if prog_id == "KWPP.Application":
                return True
            raise RuntimeError("not registered")

        with patch("core.presentation_renderer._probe_prog_id", side_effect=probe):
            result = detect_presentation_renderers()
            selected = detect_presentation_renderer()

        self.assertEqual(
            [item["renderer"] for item in result["available"]],
            [RENDERER_POWERPOINT, RENDERER_WPS],
        )
        self.assertEqual(selected["renderer"], RENDERER_POWERPOINT)

    def test_renderer_detection_reports_none_when_automation_is_unavailable(self):
        with patch(
            "core.presentation_renderer._probe_prog_id",
            side_effect=RuntimeError("not registered"),
        ):
            selected = detect_presentation_renderer()

        self.assertEqual(selected["renderer"], RENDERER_NONE)
        self.assertFalse(selected["available"])
        self.assertIn("PowerPoint.Application", selected["errors"])

    def test_hash_tracks_template_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "template.pptx")
            with open(path, "wb") as handle:
                handle.write(b"first")
            first = file_sha256(path)
            with open(path, "wb") as handle:
                handle.write(b"second")
            second = file_sha256(path)

        self.assertNotEqual(first, second)

    def test_export_rejects_renderer_prog_id_mismatch_before_com(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "template.pptx")
            with open(source, "wb") as handle:
                handle.write(b"pptx")
            with self.assertRaisesRegex(ValueError, "ProgID"):
                export_presentation_pngs(
                    source,
                    os.path.join(temp_dir, "slides"),
                    renderer=RENDERER_WPS,
                    prog_id="PowerPoint.Application",
                )


if __name__ == "__main__":
    unittest.main()
