"""One host-owned service for saved tasks, UI actions and scheduled execution."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import logging
import os
import threading
import time
import uuid

from core.favorites_manager import normalize_favorite, schedule_timezone

log = logging.getLogger("desktop.automation")
TASK_FIELDS = frozenset({"name", "description", "prompt", "skill_names", "execution_mode", "workspace_dir", "schedule"})
SCHEDULE_FIELDS = frozenset({"enabled", "timezone", "schedule_type", "time_of_day", "weekdays", "day_of_month",
    "interval_minutes", "one_time_at", "run_at", "delay_seconds", "cron_expression", "prompt_mode", "custom_prompt", "delivery"})
TRIGGER_FIELDS = frozenset({"timezone", "schedule_type", "time_of_day", "weekdays", "day_of_month",
    "interval_minutes", "one_time_at", "run_at", "delay_seconds", "cron_expression"})


class TaskConflict(ValueError):
    pass


def editable_task(value):
    """The UI sends a read model; only explicit editable fields cross the service boundary."""
    task = {key: copy.deepcopy(value[key]) for key in TASK_FIELDS if key in value}
    if isinstance(task.get("schedule"), dict):
        task["schedule"] = {key: value for key, value in task["schedule"].items() if key in SCHEDULE_FIELDS}
    return task


class TaskAutomationService:
    def __init__(self, host):
        self.host, self.config = host, host.config
        self.lock = threading.RLock()

    def _find(self, task_id):
        task = next((item for item in self.config.get_favorites() if item["id"] == task_id), None)
        if task is None:
            raise ValueError("任务不存在，请重新查询任务 ID。")
        return task

    def manager(self):
        return self.host.state.skill_catalog.snapshot().manager

    def issues(self, task, manager=None, run_context=None, *, include_delivery=True):
        problems = []
        if task.get("execution_mode") == "workspace" and not os.path.isdir(task.get("workspace_dir") or ""):
            problems.append({"field": "workspace_dir", "reason": "工作区不存在，请重新选择。"})
        manager = manager or self.manager()
        for name in task.get("skill_names") or []:
            state = manager.get_skill_readiness(name, run_context)
            if state["issues"]:
                problems.append({"field": "skill_names", "skill_name": name,
                                 "reason": state["message"], "issues": state["issues"]})
        delivery = ((task.get("schedule") or {}).get("delivery") or {}) if include_delivery else {}
        if delivery.get("enabled"):
            from core.favorite_delivery import FavoriteDeliveryService
            binding = FavoriteDeliveryService(self.host.storage).get_binding(delivery.get("binding_id"))
            if not binding or binding.get("status") != "active":
                problems.append({"field": "schedule.delivery", "reason": "消息接收目标尚未绑定或已失效，请在消息设置中完成绑定。"})
        return problems

    def validate(self, task, manager=None, run_context=None, *, include_delivery=True):
        problems = self.issues(task, manager, run_context, include_delivery=include_delivery)
        if problems:
            raise ValueError("；".join((p.get("skill_name", "") + "：" if p.get("skill_name") else "") + p["reason"] for p in problems))

    def describe(self, task):
        result = copy.deepcopy(task)
        result.pop("last_operation_id", None)
        result.pop("last_operation_hash", None)
        result["configuration_issues"] = self.issues(task)
        schedule = result.get("schedule")
        if schedule:
            zone = schedule_timezone(schedule)
            timestamp = schedule.get("next_run_at")
            schedule["next_run_iso"] = datetime.fromtimestamp(timestamp, zone).astimezone(zone).isoformat() if timestamp else ""
            schedule["timezone_label"] = schedule.get("timezone") if zone else "本机时区"
        return result

    def list(self, query="", schedule_status="all"):
        if schedule_status not in {"all", "enabled", "paused", "none"}:
            raise ValueError("计划筛选应为 all、enabled、paused 或 none。")
        with self.lock:
            items = self.config.get_favorites()
        query = str(query).strip().casefold()
        return [self.describe(item) for item in items if (not query or query in
            " ".join(str(item.get(key) or "") for key in ("name", "description", "prompt")).casefold())
            and (schedule_status == "all" or schedule_status == "none" and not item.get("schedule")
                 or schedule_status == "enabled" and (item.get("schedule") or {}).get("enabled")
                 or schedule_status == "paused" and item.get("schedule") and not item["schedule"].get("enabled"))]

    def get(self, task_id):
        with self.lock:
            task = self._find(task_id)
        return {"item": self.describe(task), "recent_runs": self.history(task_id, limit=5)}

    @staticmethod
    def _check_revision(task, expected_revision):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision != task["revision"]:
            raise TaskConflict("任务已变化或缺少版本，请重新读取后合并修改；当前输入尚未保存。")

    def _schedule(self, previous, changes, now):
        if changes is None:
            return None
        if not isinstance(changes, dict) or set(changes) - SCHEDULE_FIELDS:
            raise ValueError("计划包含未知字段或运行状态字段；请只提交可编辑的计划参数。")
        if "enabled" in changes and not isinstance(changes["enabled"], bool):
            raise ValueError("enabled 必须为布尔值。")
        for key in ("interval_minutes", "day_of_month", "delay_seconds", "one_time_at"):
            if key in changes and (isinstance(changes[key], bool) or not isinstance(changes[key], int)):
                raise ValueError(f"{key} 必须为整数。")
        for key, maximum in (("interval_minutes", 1440), ("day_of_month", 31)):
            if key in changes and not 1 <= changes[key] <= maximum:
                raise ValueError(f"{key} 必须在 1 到 {maximum} 之间。")
        for key in ("timezone", "schedule_type", "time_of_day", "cron_expression", "run_at", "prompt_mode", "custom_prompt"):
            if key in changes and not isinstance(changes[key], str):
                raise ValueError(f"{key} 必须为文本。")
        delivery = changes.get("delivery")
        if delivery is not None:
            if not isinstance(delivery, dict) or set(delivery) - {"enabled", "binding_id"}:
                raise ValueError("消息交付只接受 enabled 和 binding_id。")
            if "enabled" in delivery and not isinstance(delivery["enabled"], bool):
                raise ValueError("消息交付 enabled 必须为布尔值。")
            if "binding_id" in delivery and not isinstance(delivery["binding_id"], str):
                raise ValueError("消息交付 binding_id 必须为文本。")
        if "weekdays" in changes and (not isinstance(changes["weekdays"], list) or
            any(isinstance(day, bool) or not isinstance(day, int) for day in changes["weekdays"])):
            raise ValueError("weekdays 必须为星期整数列表。")
        result = copy.deepcopy(previous or {})
        changed = any(key in changes and changes[key] != result.get(key) for key in TRIGGER_FIELDS)
        if not previous:
            from tzlocal import get_localzone_name
            result.update(timezone=get_localzone_name(), enabled=False, created_at=now)
        result.update(copy.deepcopy(changes))
        if "delay_seconds" in changes and "run_at" in changes:
            raise ValueError("相对延时和绝对时间只能提供一个。")
        if ("delay_seconds" in changes or "run_at" in changes) and "one_time_at" in changes:
            raise ValueError("单次运行时间只能使用一种表示。")
        if "delay_seconds" in changes:
            delay = changes["delay_seconds"]
            if isinstance(delay, bool) or not isinstance(delay, int) or delay < 1:
                raise ValueError("延时必须是大于零的整数秒数。")
            result["one_time_at"] = now + delay
        if "run_at" in changes:
            instant = datetime.fromisoformat(str(changes["run_at"]).replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("单次运行时间必须包含时区偏移，例如 2026-12-01T09:00:00+08:00。")
            result["one_time_at"] = int(instant.timestamp())
        if "delay_seconds" in changes or "run_at" in changes:
            if result.get("schedule_type") not in {None, "once"}:
                raise ValueError("相对延时或绝对时间仅适用于单次计划。")
            result["schedule_type"] = "once"
        result.pop("run_at", None)
        result.pop("delay_seconds", None)
        kind = result.get("schedule_type")
        if not kind:
            raise ValueError("请选择计划类型。")
        required = {"daily": ["time_of_day"], "weekly": ["time_of_day", "weekdays"],
                    "monthly": ["time_of_day", "day_of_month"], "interval": ["interval_minutes"],
                    "once": ["one_time_at"], "cron": ["cron_expression"]}.get(kind, [])
        if any(result.get(key) in (None, "", []) for key in required):
            raise ValueError("计划缺少必要参数：" + "、".join(required))
        enabling = result.get("enabled") and not (previous or {}).get("enabled")
        if kind == "once" and (changed or enabling or not previous) and int(result.get("one_time_at") or 0) <= now:
            raise ValueError("单次执行时间已过去，请设置未来时间。")
        if changed or enabling or not previous:
            result.pop("next_run_at", None)
            if kind == "interval":
                result["interval_anchor_at"] = now
        result["updated_at"] = now
        return result

    def save(self, task, task_id="", expected_revision=None, *, manager=None, run_context=None, operation_id=""):
        if not isinstance(task, dict) or set(task) - TASK_FIELDS:
            raise ValueError("任务包含未知字段或运行状态字段。")
        for key in ("name", "description", "prompt", "execution_mode", "workspace_dir"):
            if key in task and not isinstance(task[key], str):
                raise ValueError(f"{key} 必须为文本。")
        if "skill_names" in task and (not isinstance(task["skill_names"], list) or
            any(not isinstance(name, str) or not name.strip() for name in task["skill_names"])):
            raise ValueError("skill_names 必须为非空技能标识组成的列表。")
        with self.lock:
            operation_key = "automation-save:" + operation_id if operation_id else ""
            fingerprint = hashlib.sha256(json.dumps([task, task_id, expected_revision], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            previous_operation = self.host.store.get_state(operation_key) if operation_key else None
            if previous_operation:
                if previous_operation["fingerprint"] != fingerprint:
                    raise TaskConflict("同一操作标识不能用于不同任务内容。")
                # The desired revision was already committed even if its tool receipt was lost.
                current = self._find(previous_operation["task_id"])
                if current.get("last_operation_id") == operation_id:
                    return self.describe(current)
                raise TaskConflict("该操作的任务已被再次修改，请读取当前版本。")
            generated_id = "fav-" + (uuid.uuid5(uuid.NAMESPACE_URL, "cowork-automation:" + operation_id).hex[:20]
                                      if operation_id else uuid.uuid4().hex[:12])
            candidate_id = task_id or generated_id
            old = next((item for item in self.config.get_favorites() if item["id"] == candidate_id), None)
            if old and operation_id and old.get("last_operation_id") == operation_id:
                if old.get("last_operation_hash") != fingerprint:
                    raise TaskConflict("同一操作标识不能用于不同任务内容。")
                return self.describe(old)
            if task_id and old is None:
                raise ValueError("任务不存在，请重新查询任务 ID。")
            if not task_id and old is not None:
                raise TaskConflict("该创建操作已完成且任务已变化，请重新查询。")
            if old:
                self._check_revision(old, expected_revision)
            now = int(time.time())
            updated = copy.deepcopy(old or {"id": generated_id, "created_at": now})
            updated.update(copy.deepcopy(task))
            if "schedule" in task:
                updated["schedule"] = self._schedule((old or {}).get("schedule"), task["schedule"], now)
            updated.update(updated_at=now, revision=(old or {}).get("revision", 0) + 1,
                           last_operation_id=operation_id, last_operation_hash=fingerprint)
            normalized = normalize_favorite(updated, now_ts=now)
            # Pausing/removing a broken plan remains possible without repairing its dependencies first.
            only_disable = bool(old and "schedule" in task and
                all(value == old.get(key) for key, value in task.items() if key != "schedule") and
                (task["schedule"] is None or task["schedule"].get("enabled") is False and
                 all(value == (old.get("schedule") or {}).get(key) for key, value in task["schedule"].items() if key != "enabled")))
            if not only_disable:
                self.validate(normalized, manager, run_context)
            with self.config.batch_save():
                self.config.upsert_favorite(normalized)
            if operation_key:
                self.host.store.put_state(operation_key, {"fingerprint": fingerprint, "task_id": normalized["id"]})
        log.info("automation_saved task=%s revision=%s", normalized["id"], normalized["revision"])
        self.host.publish({"event": "schedule_changed"})
        return self.describe(normalized)

    def set_schedule(self, task_id, expected_revision, action, schedule=None, **context):
        if action not in {"set", "enable", "pause", "remove"}:
            raise ValueError("计划操作应为 set、enable、pause 或 remove。")
        if action != "set" and schedule is not None:
            raise ValueError("只有 set 操作可以提供计划参数。")
        value = schedule if action == "set" else None if action == "remove" else {"enabled": action == "enable"}
        if action == "set" and not isinstance(value, dict):
            raise ValueError("设置计划时必须提供计划参数。")
        return self.save({"schedule": value}, task_id, expected_revision, **context)

    def delete(self, task_id, expected_revision):
        with self.lock, self.config.batch_save():
            task = self._find(task_id)
            self._check_revision(task, expected_revision)
            self.config.set_favorites([item for item in self.config.get_favorites() if item["id"] != task_id])
        log.info("automation_deleted task=%s history=preserved", task_id)
        self.host.publish({"event": "schedule_changed"})
        return {"deleted": True, "task_id": task_id, "history_preserved": True}

    def history(self, task_id="", limit=20, status_filter=""):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("记录数量必须是 1 到 100 的整数。")
        from core.interaction import interaction_service
        with self.lock:
            rows = self.config.get_favorite_run_history()
        result = []
        for row in rows:
            if task_id and row["favorite_id"] != task_id or status_filter and row["status"] != status_filter:
                continue
            if row["status"] == "running":
                pending = interaction_service.get_pending_request(row["session_id"])
                if pending:
                    row["waiting_for_input"] = True
            result.append(row)
            if len(result) >= limit:
                break
        from core.favorite_delivery import FavoriteDeliveryService
        jobs = {job["run_history_id"]: job for job in FavoriteDeliveryService(self.host.storage).get_jobs_for_runs([row["id"] for row in result])}
        for row in result:
            job = jobs.get(row["id"])
            if job:
                row.update(delivery_id=job["id"], delivery_status=job["status"], delivery_error=job.get("error") or "")
        return result

    def run(self, task_id, source="task", *, scheduled_at=0, operation_id="", manager=None, run_context=None):
        return self.host.scheduler.run(task_id, scheduled_at, source=source, operation_id=operation_id,
                                       manager=manager, run_context=run_context)
