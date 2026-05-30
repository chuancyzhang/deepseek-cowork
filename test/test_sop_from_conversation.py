import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sop_from_conversation import (
    DEFAULT_SOP_NAME,
    build_sop_generation_messages,
    normalize_sop_draft,
)


class TestSopFromConversation(unittest.TestCase):
    def test_normalize_sop_draft_filters_empty_steps(self):
        draft = normalize_sop_draft(
            {
                "name": "文章整理 SOP",
                "description": "整理文章并输出结构化结果",
                "triggers": "文章整理, SOP",
                "steps": [
                    {"title": "阅读原文", "instructions": "提取核心事实"},
                    {"title": "", "instructions": "", "success_criteria": ""},
                    {
                        "title": "输出 SOP",
                        "instructions": "整理步骤",
                        "success_criteria": "用户能直接复用",
                        "allow_skip": True,
                    },
                ],
            }
        )

        self.assertEqual(draft["name"], "文章整理 SOP")
        self.assertEqual(draft["advance_mode"], "manual")
        self.assertEqual(draft["triggers"], ["文章整理", "SOP"])
        self.assertEqual(len(draft["steps"]), 2)
        self.assertEqual(draft["steps"][1]["title"], "输出 SOP")
        self.assertTrue(draft["steps"][1]["allow_skip"])
        self.assertEqual(draft["steps"][0]["advance_mode"], "inherit")

    def test_normalize_sop_draft_uses_fallback_name(self):
        draft = normalize_sop_draft(
            {
                "description": "依据有限的对话生成",
                "steps": [{"instructions": "完成当前对话中明确的工作"}],
            },
            fallback_title="当前会话流程",
        )

        self.assertEqual(draft["name"], "当前会话流程")
        self.assertEqual(draft["steps"][0]["title"], "步骤 1")

    def test_normalize_sop_draft_falls_back_to_default_name(self):
        draft = normalize_sop_draft({"steps": [{"title": "确认目标"}]})

        self.assertEqual(draft["name"], DEFAULT_SOP_NAME)

    def test_revision_prompt_includes_previous_draft_and_feedback(self):
        previous = {
            "name": "旧 SOP",
            "steps": [{"title": "旧步骤", "instructions": "旧指令"}],
        }
        messages = build_sop_generation_messages(
            "## user\n请整理流程",
            fallback_title="流程",
            previous_draft=previous,
            revision_feedback="步骤要更口语化",
        )
        user_prompt = messages[-1]["content"]

        self.assertIn("需要修订的上一版 SOP 草稿", user_prompt)
        self.assertIn("旧 SOP", user_prompt)
        self.assertIn("步骤要更口语化", user_prompt)
        self.assertIn("不要把创建过程拆成逐步确认", user_prompt)
        self.assertIn('"advance_mode": "manual"', user_prompt)


if __name__ == "__main__":
    unittest.main()
