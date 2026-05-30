import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sop_manager import (
    SOP_ADVANCE_MODE_AUTO,
    SOP_ADVANCE_MODE_MANUAL,
    SOP_RUN_STATUS_ACTIVE,
    SOP_RUN_STATUS_AWAITING_CONFIRMATION,
    SOP_RUN_STATUS_COMPLETED,
    SOP_STEP_STATUS_AWAITING_CONFIRMATION,
    SOP_STEP_STATUS_COMPLETED,
    SOP_STEP_STATUS_PENDING,
    SOP_STEP_STATUS_SKIPPED,
    append_step_output,
    build_sop_prompt_fragment,
    build_step_execution_request,
    complete_current_step,
    confirm_current_step,
    create_sop_run,
    default_sop_templates,
    get_current_step,
    mark_step_awaiting_confirmation,
    mark_step_running,
    normalize_sop_run,
    normalize_sop_templates,
    resolve_step_advance_mode,
    rerun_current_step,
    skip_current_step,
)


class TestSopManager(unittest.TestCase):
    def test_default_templates_include_placeholder(self):
        templates = default_sop_templates()
        self.assertTrue(templates)
        self.assertEqual(templates[0]["id"], "office-file-first-placeholder")

    def test_normalize_sop_templates_deduplicates_ids_and_filters_empty_steps(self):
        templates = normalize_sop_templates(
            [
                {
                    "id": "office",
                    "name": "Office",
                    "steps": [{"title": "A"}, {"title": "", "instructions": "", "success_criteria": ""}],
                },
                {
                    "id": "office",
                    "name": "Office Copy",
                    "steps": [{"title": "B"}],
                },
            ]
        )
        self.assertEqual(len(templates), 2)
        self.assertEqual(len(templates[0]["steps"]), 1)
        self.assertEqual(templates[0]["id"], "office")
        self.assertEqual(templates[1]["id"], "office-2")

    def test_sop_run_lifecycle_confirm_rerun_and_skip(self):
        run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "advance_mode": "manual",
                "description": "demo",
                "steps": [
                    {"title": "Step 1", "instructions": "Do 1", "success_criteria": "Done 1"},
                    {"title": "Step 2", "instructions": "Do 2", "success_criteria": "Done 2", "allow_skip": True},
                ],
            }
        )
        self.assertEqual(run["status"], SOP_RUN_STATUS_ACTIVE)
        self.assertEqual(get_current_step(run)["status"], SOP_STEP_STATUS_PENDING)

        run = mark_step_running(run, {"started_at": 1})
        self.assertEqual(get_current_step(run)["status"], "running")

        run = mark_step_awaiting_confirmation(run, {"finished_at": 2})
        self.assertEqual(run["status"], SOP_RUN_STATUS_AWAITING_CONFIRMATION)
        self.assertEqual(get_current_step(run)["status"], SOP_STEP_STATUS_AWAITING_CONFIRMATION)
        self.assertEqual(get_current_step(run)["last_execution"]["started_at"], 1)
        self.assertEqual(get_current_step(run)["last_execution"]["finished_at"], 2)

        rerun = rerun_current_step(run, "again")
        self.assertEqual(rerun["status"], SOP_RUN_STATUS_ACTIVE)
        self.assertEqual(get_current_step(rerun)["status"], SOP_STEP_STATUS_PENDING)

        run = mark_step_awaiting_confirmation(rerun, {"finished_at": 3})
        run = confirm_current_step(run, "ok")
        self.assertEqual(run["status"], SOP_RUN_STATUS_ACTIVE)
        self.assertEqual(run["current_step_index"], 1)
        self.assertEqual(run["steps"][0]["status"], SOP_STEP_STATUS_COMPLETED)

        run = mark_step_awaiting_confirmation(run, {"finished_at": 4})
        run = skip_current_step(run, "n/a")
        self.assertEqual(run["status"], SOP_RUN_STATUS_COMPLETED)
        self.assertEqual(run["steps"][1]["status"], SOP_STEP_STATUS_SKIPPED)

    def test_resolve_step_advance_mode_respects_template_and_step_override(self):
        run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "advance_mode": "auto",
                "steps": [
                    {"title": "Step 1", "advance_mode": "inherit"},
                    {"title": "Step 2", "advance_mode": "manual"},
                ],
            }
        )
        self.assertEqual(resolve_step_advance_mode(run), SOP_ADVANCE_MODE_AUTO)
        run = complete_current_step(run, auto_advanced=True)
        self.assertEqual(run["current_step_index"], 1)
        self.assertEqual(resolve_step_advance_mode(run), SOP_ADVANCE_MODE_MANUAL)

    def test_build_step_execution_request_uses_original_goal_and_prior_outputs(self):
        run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "advance_mode": "manual",
                "steps": [
                    {"title": "Step 1", "instructions": "Do 1"},
                    {"title": "Step 2", "instructions": "Do 2", "advance_mode": "auto"},
                ],
            }
        )
        run["original_user_request"] = "整理本周日报"
        run = append_step_output(run, content="第一步已完成", executor="main_agent")
        run = complete_current_step(run, auto_advanced=True)
        payload = build_step_execution_request(run)
        self.assertIn("整理本周日报", payload["content"])
        self.assertIn("第一步已完成", payload["content"])
        self.assertIn("Step 2", payload["display_content"])

    def test_prompt_fragment_mentions_current_step_and_strict_rules(self):
        run = normalize_sop_run(
            {
                "template_id": "office",
                "template_name": "Office",
                "template_description": "Handle office tasks",
                "template_advance_mode": "manual",
                "current_step_index": 0,
                "status": SOP_RUN_STATUS_ACTIVE,
                "steps": [
                    {
                        "title": "Confirm scope",
                        "instructions": "Ask only scope questions",
                        "success_criteria": "Scope is clear",
                        "advance_mode": "inherit",
                        "status": SOP_STEP_STATUS_PENDING,
                    }
                ],
            }
        )

        fragment = build_sop_prompt_fragment(run)
        self.assertIn("当前 SOP: Office", fragment)
        self.assertIn("当前步骤: 1/1 - Confirm scope", fragment)
        self.assertIn("本轮只允许完成当前步骤", fragment)
        self.assertIn("人工确认", fragment)


if __name__ == "__main__":
    unittest.main()
