import os
import sys
import json
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.interaction import impl as interaction_impl


class TestInteractionSkill(unittest.TestCase):
    def test_publish_feishu_artifact_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "sample.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("demo")
            result = interaction_impl.publish_feishu_artifact(
                items=[{"path": file_path, "caption": "demo file"}],
                audience="feishu",
                tool_summary="artifact ready",
            )
            payload = json.loads(result)
            self.assertEqual(payload.get("source_tool"), "publish_feishu_artifact")
            parts = payload.get("content_parts") or []
            file_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "file"]
            self.assertTrue(file_parts)
            self.assertEqual(file_parts[0].get("artifact_source"), "publish_feishu_artifact")

    def test_publish_feishu_artifact_missing_file_error(self):
        result = interaction_impl.publish_feishu_artifact(items=[{"path": "Z:/not-found.bin"}], audience="feishu")
        self.assertIn("file not found", result.lower())


if __name__ == "__main__":
    unittest.main()
