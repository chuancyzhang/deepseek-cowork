"""Shared automation service, real persistent storage, deterministic execution receipts."""
import copy
from datetime import datetime
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.chat_storage import ChatStorage
from core.desktop_config import DesktopConfigManager
from core.desktop_scheduler import DesktopScheduler
from core.desktop_store import DesktopStore
from core.favorites_manager import compute_next_run_at, normalize_favorite
from core.task_automation import TaskAutomationService, TaskConflict, editable_task

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / ".tmp" / "task-automation"
QA.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("automation_skill_test", ROOT / "skills/task-automation/impl.py")
skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skill)


class Host:
    def __init__(self, root):
        with patch("core.config_manager.get_app_data_dir", return_value=str(root)), patch("core.config_manager.get_base_dir", return_value=str(root)):
            self.config = DesktopConfigManager(migrate_legacy=False)
        self.storage = ChatStorage(str(root / "history.sqlite"))
        self.store = DesktopStore(self.storage)
        self.runs, self.sent, self.events, self.unavailable = {}, [], [], set()
        self.store.get_run = lambda run_id: self.runs.get(run_id)
        self.closing, self.on_send, self.lose_receipt, self.fail_send = False, None, False, False
        self.manager = SimpleNamespace(
            get_skill_readiness=lambda name, _ctx=None: {"issues": ["disabled"] if name in self.unavailable else [], "message": "能力未开启。"},
            get_full_skill_prompt=lambda name: "instructions:" + name)
        self.state = SimpleNamespace(skill_catalog=SimpleNamespace(snapshot=lambda: SimpleNamespace(manager=self.manager)))
        self.scheduler = DesktopScheduler(self)
        self.automations = TaskAutomationService(self)
        self.scheduler.lock = self.automations.lock
        self.publish = self.events.append

    def dispatch(self, method, p):
        if method == "conversation.create":
            cid = uuid.uuid4().hex
            self.storage.upsert_conversation(cid, title="新对话")
            return {"id": cid}
        if method == "conversation.send":
            if self.fail_send:
                raise RuntimeError("提交被拒绝")
            self.sent.append(copy.deepcopy(p))
            self.runs[p["request_id"]] = {"id": p["request_id"], "conversation_id": p["conversation_id"], "status": "queued"}
            if self.on_send:
                self.on_send()
            if self.lose_receipt:
                raise TimeoutError("接收后回执丢失")
            return {"accepted": True, "run": self.runs[p["request_id"]]}
        raise AssertionError(method)


class TestTaskAutomation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=QA)
        self.host = Host(Path(self.temp.name))
        self.service = self.host.automations
        self.context = {"task_automation_service": self.service, "skill_manager": self.host.manager, "run_context": {"mode": "execution"}}

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **values):
        return self.service.save({"name": "日报", "prompt": "普通任务正文", **values})

    def scheduled(self, **values):
        return self.create(schedule={"schedule_type": "once", "delay_seconds": 10, "enabled": True, "timezone": "Asia/Shanghai", **values})

    def due(self, task):
        raw = self.host.config.get_favorite(task["id"])
        raw["schedule"]["next_run_at"] = 1000
        self.host.config.upsert_favorite(raw)

    def test_tool_starts_real_conversation_and_replay_does_not_submit_twice(self):
        task = self.create()
        context = {**self.context, "logical_execution_id": "same-operation"}
        first = skill.run_automation(task["id"], _context=context)
        second = skill.run_automation(task["id"], _context=context)
        self.assertEqual(first["status"], "queued")
        self.assertTrue(first["run_id"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertTrue(self.host.storage.has_conversation(first["conversation_id"]))
        self.assertEqual(len(self.host.sent), 1)
        self.assertNotIn("client_action", first)
        self.assertEqual(skill.run_automation("missing")["status"], "unavailable")

    def test_task_and_schedule_prompts_are_separate(self):
        task = self.scheduled(prompt_mode="custom", custom_prompt="计划正文")
        first = self.service.run(task["id"])
        self.assertEqual(self.host.sent[-1]["text"], "普通任务正文")
        self.host.runs[first["run_id"]]["status"] = "completed"
        self.host.scheduler.reconcile()
        self.service.run(task["id"], source="schedule")
        self.assertEqual(self.host.sent[-1]["text"], "计划正文")
        self.assertEqual(self.host.config.get_favorite(task["id"])["schedule"]["next_run_at"], task["schedule"]["next_run_at"])

    def test_delivery_is_schedule_only_and_history_reads_actual_delivery_state(self):
        from core.favorite_delivery import FavoriteDeliveryService
        delivery = FavoriteDeliveryService(self.host.storage)
        task = self.scheduled()
        request = delivery.create_binding_request(task["id"])
        binding = delivery.claim_binding_request(request["request_id"], "feishu",
            {"target_type": "chat_id", "target_value": "isolated-fixture", "display_name": "隔离目标"})
        task = self.service.save({"schedule": {"delivery": {"enabled": True, "binding_id": binding["id"]}}}, task["id"], task["revision"])
        manual = self.service.run(task["id"])
        self.host.runs[manual["run_id"]]["status"] = "completed"
        self.host.scheduler.reconcile()
        self.assertEqual(delivery.get_jobs_for_runs([manual["history_id"]]), [])
        scheduled = self.service.run(task["id"], source="schedule")
        self.host.runs[scheduled["run_id"]]["status"] = "completed"
        self.host.scheduler.reconcile()
        jobs = delivery.get_jobs_for_runs([scheduled["history_id"]])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "pending")
        # A real gateway's subsequent receipt is the source of delivery status.
        with self.host.storage._connect() as db:
            db.execute("UPDATE favorite_delivery_jobs SET status='completed' WHERE id=?", (jobs[0]["id"],))
        row = next(row for row in self.service.history(task["id"]) if row["id"] == scheduled["history_id"])
        self.assertEqual(row["delivery_status"], "completed")
        self.host.scheduler.reconcile()
        self.assertEqual(len(delivery.get_jobs_for_runs([scheduled["history_id"]])), 1)
        delivery.unbind(binding["id"])
        with self.assertRaisesRegex(ValueError, "失效"):
            self.service.run(task["id"], source="schedule")
        self.assertFalse(self.service.set_schedule(task["id"], task["revision"], "pause")["schedule"]["enabled"])

    def test_terminal_claim_recovers_history_after_projection_failure(self):
        task = self.create()
        receipt = self.service.run(task["id"])
        self.host.runs[receipt["run_id"]]["status"] = "completed"
        with patch.object(self.host.scheduler, "record", side_effect=OSError("history write interrupted")):
            with self.assertRaises(OSError):
                self.host.scheduler.reconcile()
        self.assertEqual(self.service.history(task["id"])[0]["status"], "running")
        self.host.scheduler.reconcile()
        self.assertEqual(self.service.history(task["id"])[0]["status"], "completed")
        self.assertEqual(len(self.host.sent), 1)

    def test_ability_only_opens_draft_and_requires_a_prompt_for_schedule(self):
        task = self.create(prompt="", skill_names=["data-analysis"])
        result = self.service.run(task["id"])
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual(result["run_id"], "")
        self.assertFalse(self.host.sent)
        self.assertEqual(self.host.storage.get_conversation_meta(result["conversation_id"])["selected_skill_names"], ["data-analysis"])
        with self.assertRaisesRegex(ValueError, "专用提示词"):
            self.service.set_schedule(task["id"], task["revision"], "set", {"schedule_type": "daily", "time_of_day": "09:00"})

    def test_same_name_and_revision_conflict_never_overwrite(self):
        first, second = self.create(), self.create()
        self.assertNotEqual(first["id"], second["id"])
        self.service.save({"prompt": "用户新编辑"}, first["id"], first["revision"])
        with self.assertRaises(TaskConflict):
            self.service.save({"prompt": "过期编辑"}, first["id"], first["revision"])
        self.assertEqual(self.service.get(first["id"])["item"]["prompt"], "用户新编辑")

    def test_save_replay_does_not_recalculate_relative_time(self):
        payload = {"name": "稍后检查", "prompt": "检查", "schedule": {"schedule_type": "once", "delay_seconds": 600}}
        first = self.service.save(payload, operation_id="create-once")
        second = self.service.save(payload, operation_id="create-once")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["schedule"]["next_run_at"], second["schedule"]["next_run_at"])
        self.assertEqual(len(self.service.list()), 1)
        with self.assertRaises(TaskConflict):
            self.service.save({**payload, "prompt": "不同内容"}, operation_id="create-once")

    def test_lost_save_receipt_recovers_committed_task(self):
        payload = {"name": "已保存", "prompt": "执行"}
        with patch.object(self.host.store, "put_state", side_effect=OSError("receipt disk full")):
            with self.assertRaises(OSError):
                self.service.save(payload, operation_id="lost-save")
        self.assertEqual(self.service.save(payload, operation_id="lost-save")["revision"], 1)
        self.assertEqual(len(self.service.list()), 1)

    def test_disk_failure_keeps_original_input_and_no_false_success(self):
        task = self.create()
        with patch.object(self.host.config, "_write_config_or_raise", side_effect=OSError("disk full")):
            result = skill.save_automation({"prompt": "保存失败"}, task["id"], task["revision"], _context=self.context)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.service.get(task["id"])["item"]["prompt"], task["prompt"])

    def test_readonly_fields_and_time_inputs_are_validated(self):
        with self.assertRaisesRegex(ValueError, "运行状态"):
            self.create(schedule={"schedule_type": "daily", "time_of_day": "09:00", "next_run_at": 1})
        with patch("core.task_automation.time.time", return_value=1000):
            task = self.scheduled()
        self.assertEqual(task["schedule"]["next_run_at"], 1010)
        self.assertTrue(task["schedule"]["next_run_iso"].endswith("+08:00"))
        self.assertNotIn("next_run_at", editable_task(task)["schedule"])
        for changes in [{"interval_minutes": 0}, {"day_of_month": False}, {"enabled": "true"}, {"timezone": []}, {"delivery": {"enabled": "false"}}, {"delivery": {"target": "guessed"}}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.scheduled(**changes)
        for schedule in [{"schedule_type": "once", "run_at": "2020-01-01T00:00:00+08:00"}, {"schedule_type": "once", "run_at": "2040-01-01T00:00:00"}, {"schedule_type": "daily"}, {"schedule_type": "weekly", "time_of_day": "09:00", "weekdays": []}]:
            with self.subTest(schedule=schedule), self.assertRaises(ValueError):
                self.create(schedule=schedule)

    def test_broken_references_are_visible_but_pause_is_allowed(self):
        result = skill.save_automation({"name": "检查", "prompt": "检查", "execution_mode": "workspace", "workspace_dir": self.temp.name + "/missing"}, _context=self.context)
        self.assertIn("工作区不存在", result["error"])
        task = self.scheduled()
        task = self.service.save({"skill_names": ["data-analysis"]}, task["id"], task["revision"])
        self.host.unavailable.add("data-analysis")
        self.assertTrue(self.service.get(task["id"])["item"]["configuration_issues"])
        with self.assertRaisesRegex(ValueError, "未开启"):
            self.service.run(task["id"])
        self.assertFalse(self.service.set_schedule(task["id"], task["revision"], "pause")["schedule"]["enabled"])

    def test_lost_start_receipt_uses_acceptance_without_repeating(self):
        task = self.create()
        self.host.lose_receipt = True
        result = self.service.run(task["id"], operation_id="lost-start")
        self.assertEqual(result["status"], "queued")
        self.service.run(task["id"], operation_id="lost-start")
        self.assertEqual(len(self.host.sent), 1)

    def test_start_rejection_retains_error_history(self):
        task = self.create()
        self.host.fail_send = True
        self.assertEqual(self.service.run(task["id"])["status"], "error")
        self.assertIn("提交被拒绝", self.service.history(task["id"])[0]["error"])

    def test_busy_trigger_has_durable_skipped_record(self):
        task = self.scheduled()
        self.service.run(task["id"])
        self.due(task)
        with patch("core.desktop_scheduler.time.time", return_value=1001):
            self.host.scheduler.tick()
        self.assertEqual({row["status"] for row in self.service.history(task["id"])}, {"running", "skipped"})
        self.assertFalse(self.host.config.get_favorite(task["id"])["schedule"]["enabled"])

    def test_trigger_errors_and_missed_runs_do_not_execute(self):
        task = self.scheduled()
        self.due(task)
        raw = self.host.config.get_favorite(task["id"])
        raw["skill_names"] = ["gone"]
        self.host.config.upsert_favorite(raw)
        self.host.unavailable.add("gone")
        with patch("core.desktop_scheduler.time.time", return_value=1001):
            self.host.scheduler.tick()
        self.assertEqual(self.service.history(task["id"])[0]["status"], "error")
        other = self.scheduled()
        self.due(other)
        with patch("core.desktop_scheduler.time.time", return_value=2000):
            self.host.scheduler.tick()
            self.host.scheduler.tick()
        self.assertEqual(len(self.service.history(other["id"])), 1)
        self.assertEqual(self.service.history(other["id"])[0]["status"], "missed")
        self.assertFalse(self.host.sent)

    def test_tick_does_not_overwrite_an_edit_during_submission(self):
        task = self.scheduled()
        self.due(task)
        self.host.on_send = lambda: self.service.save({"prompt": "发送期间的新修改", "schedule": {"enabled": False}}, task["id"], task["revision"])
        with patch("core.desktop_scheduler.time.time", return_value=1001):
            self.host.scheduler.tick()
        current = self.service.get(task["id"])["item"]
        self.assertEqual(current["prompt"], "发送期间的新修改")
        self.assertFalse(current["schedule"]["enabled"])

    def test_restart_reconciles_uncertain_claim_without_repeating(self):
        task = self.create()
        result = self.service.run(task["id"], operation_id="restart")
        self.host.runs.clear()
        self.host.scheduler = DesktopScheduler(self.host)
        self.host.scheduler.lock = self.service.lock
        self.host.scheduler.reconcile()
        replay = self.service.run(task["id"], operation_id="restart")
        self.assertEqual(replay["status"], "interrupted")
        self.assertEqual(replay["conversation_id"], result["conversation_id"])
        self.assertEqual(len(self.host.sent), 1)

    def test_delete_preserves_running_conversation_and_history(self):
        task = self.create()
        run = self.service.run(task["id"])
        skill.delete_automation(task["id"], task["revision"], _context=self.context)
        self.assertFalse(self.service.list())
        self.assertEqual(len(self.service.history(task["id"])), 1)
        self.assertTrue(self.host.storage.has_conversation(run["conversation_id"]))
        self.assertEqual(self.host.runs[run["run_id"]]["status"], "queued")

    def test_legacy_ids_skill_selection_and_next_time_survive(self):
        task = normalize_favorite({"id": "legacy", "name": "旧任务", "prompt": "运行", "skill_names": ["favorites-manager"], "schedule": {"schedule_type": "daily", "next_run_at": 1000}})
        self.assertEqual(task["id"], "legacy")
        self.assertEqual(task["skill_names"], ["task-automation"])
        self.assertEqual(task["schedule"]["timezone"], "local")
        self.assertEqual(task["schedule"]["next_run_at"], 1000)


class TestScheduleTimezone(unittest.TestCase):
    def test_dst_gap_is_skipped_and_repeated_time_executes_once(self):
        zone = ZoneInfo("America/New_York")
        before_gap = int(datetime(2026, 3, 8, 0, tzinfo=zone).timestamp())
        for schedule in [{"schedule_type": "daily", "time_of_day": "02:30"}, {"schedule_type": "cron", "cron_expression": "30 2 * * *"}]:
            with self.subTest(schedule=schedule), self.assertLogs("desktop.scheduler", level="WARNING"):
                result = compute_next_run_at({**schedule, "timezone": str(zone)}, now_ts=before_gap)
            self.assertEqual(datetime.fromtimestamp(result, zone).isoformat(), "2026-03-09T02:30:00-04:00")
        first_fold = int(datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0).timestamp())
        for schedule in [{"schedule_type": "daily", "time_of_day": "01:30"}, {"schedule_type": "cron", "cron_expression": "30 1 * * *"}]:
            result = compute_next_run_at({**schedule, "timezone": str(zone)}, now_ts=first_fold)
            self.assertEqual(datetime.fromtimestamp(result, zone).isoformat(), "2026-11-02T01:30:00-05:00")


if __name__ == "__main__":
    unittest.main()
