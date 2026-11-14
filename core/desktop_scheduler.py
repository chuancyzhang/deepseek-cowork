"""Native persistent schedule runner with a durable claim before execution."""
import json
import logging
import threading
import time
import uuid

from core.favorites_manager import advance_favorite_schedule, favorite_effective_prompt, make_favorite_run_record, FAVORITE_RUN_GRACE_SECONDS

log = logging.getLogger("desktop.scheduler")


class DesktopScheduler:
    def __init__(self, service):
        self.s = service
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread = threading.Thread(target=self.loop, name="desktop-scheduler", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(10)

    def record(self, claim):
        history = self.s.config.get_favorite_run_history()
        self.s.config.set_favorite_run_history([claim["record"], *[item for item in history if item["id"] != claim["record"]["id"]]])

    def run(self, favorite_id, scheduled_at=0):
        with self.lock:
            if self.s.closing:
                raise ValueError("后台正在退出，任务尚未启动。")
            favorite = self.s.config.get_favorite(favorite_id)
            if not favorite:
                raise ValueError("常用任务不存在。")
            key = f"schedule:{favorite_id}:{scheduled_at or uuid.uuid4().hex}"
            previous = self.s.store.get_state(key)
            if previous:
                return {"conversation_id": previous["record"]["session_id"], "claimed": True}
            busy = [item for item in self.s.config.get_favorite_run_history() if item["favorite_id"] == favorite_id and item["status"] == "running"]
            if busy:
                raise ValueError("这个常用任务仍在运行，请先查看运行结果。")
            conversation = self.s.dispatch("conversation.create", {"workspace_dir": favorite.get("workspace_dir") if favorite.get("execution_mode") == "workspace" else None})
            cid = conversation["id"]
            self.s.storage.upsert_conversation(cid, title=favorite["name"])
            record = make_favorite_run_record(favorite, trigger_source="scheduler" if scheduled_at else "manual", session_id=cid, scheduled_at=scheduled_at)
            claim = {"favorite": favorite, "record": record, "run_id": uuid.uuid4().hex, "done": False}
            # Once claimed, restart only reconciles receipts. It never repeats
            # tools whose external effect cannot be proven absent after a crash.
            self.s.store.put_state(key, claim)
            self.record(claim)
            try:
                self.s.dispatch("conversation.send", {"conversation_id": cid, "request_id": claim["run_id"], "message_id": uuid.uuid4().hex,
                    "text": favorite_effective_prompt(favorite), "attachments": [], "model_id": self.s.config.get_selected_model_id(),
                    "preferences": {"selected_skill_names": favorite.get("skill_names") or [], "mode": "work"}})
            except Exception as exc:
                log.exception("favorite_start_failed favorite=%s run=%s", favorite_id, claim["run_id"])
                record.update(status="error", error=str(exc), finished_at=int(time.time()))
                claim["done"] = True
                self.s.store.put_state(key, claim)
                self.record(claim)
                raise
            self.s.publish({"event": "conversation_changed", "conversation_id": cid})
            return {"conversation_id": cid, "run_id": claim["run_id"]}

    def reconcile(self):
        with self.s.storage._connect() as db:
            claims = [(row[0], json.loads(row[1])) for row in db.execute("SELECT key,value FROM desktop_view_state WHERE key LIKE 'schedule:%'")]
        for key, claim in claims:
            if claim["done"]:
                continue
            run = self.s.store.get_run(claim["run_id"])
            if run and run["status"] in {"queued", "running", "stopping"}:
                continue
            status = {"failed": "error", "completed": "completed"}.get((run or {}).get("status"), "interrupted")
            record = claim["record"]
            record.update(status=status, error=(run or {}).get("error") or ("上次启动被中断，请核对对话后手动运行。" if not run else ""), finished_at=int(time.time()))
            claim["done"] = True
            # Persist terminal state before delivery: an uncertain delivery is
            # visible and must not be silently enqueued a second time.
            self.s.store.put_state(key, claim)
            delivery = (claim["favorite"].get("schedule") or {}).get("delivery") or {}
            if delivery.get("enabled"):
                try:
                    from core.favorite_delivery import FavoriteDeliveryService
                    messages = self.s.storage.get_messages(record["session_id"])
                    output = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "assistant" and not m.get("tool_calls")), "")
                    job = FavoriteDeliveryService(self.s.storage).enqueue_delivery(run_history_id=record["id"], favorite_id=record["favorite_id"],
                        session_id=record["session_id"], binding_id=delivery["binding_id"], favorite_name=record["favorite_name"], terminal_status=status,
                        content=output, error=record["error"])
                    record.update(delivery_id=job["id"], delivery_status=job["status"])
                except Exception as exc:
                    record.update(delivery_status="failed", delivery_error=str(exc))
                    log.exception("favorite_delivery_failed run=%s", claim["run_id"])
            self.s.store.put_state(key, claim)
            self.record(claim)
            self.s.publish({"event": "schedule_changed"})

    def tick(self):
        with self.lock:
            self.reconcile()
            now = int(time.time())
            for favorite in self.s.config.get_favorites():
                schedule = favorite.get("schedule") or {}
                due = int(schedule.get("next_run_at") or 0)
                if not schedule.get("enabled") or not due or due > now:
                    continue
                if due + FAVORITE_RUN_GRACE_SECONDS < now:
                    self.s.config.append_favorite_run_history(make_favorite_run_record(favorite, status="missed", scheduled_at=due,
                        summary="后台未运行，已跳过错过的计划；可手动运行。"))
                    schedule["last_missed_at"] = due
                else:
                    try:
                        self.run(favorite["id"], due)
                        schedule["last_run_at"] = now
                    except Exception as exc:
                        log.exception("favorite_trigger_failed favorite=%s", favorite["id"])
                        self.s.publish({"event": "service_error", "error": str(exc)})
                if schedule.get("schedule_type") == "once":
                    schedule["enabled"] = False
                else:
                    favorite = advance_favorite_schedule(favorite, now_ts=now, after_ts=now)
                self.s.config.upsert_favorite(favorite)

    def loop(self):
        while not self.stop_event.wait(5):
            if self.s.closing:
                continue
            try:
                self.tick()
            except Exception as exc:
                log.exception("schedule_tick_failed")
                self.s.publish({"event": "service_error", "error": "定时任务检查失败：" + str(exc)})
