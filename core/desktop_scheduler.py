"""Native persistent schedule runner with a durable claim before execution."""
import json
import hashlib
import logging
import threading
import time
import uuid

from core.favorites_manager import advance_favorite_schedule, favorite_effective_prompt, make_favorite_run_record, normalize_favorite_run_record, FAVORITE_RUN_GRACE_SECONDS

log = logging.getLogger("desktop.scheduler")


class DesktopScheduler:
    def __init__(self, service):
        self.s = service
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.starting = set()
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

    def receipt(self, claim):
        record = claim["record"]
        return {"status": claim.get("submission_status") or record["status"],
                "conversation_id": record["session_id"], "run_id": claim.get("run_id") or "",
                "history_id": record["id"], "error": record.get("error") or ""}

    def run(self, favorite_id, scheduled_at=0, *, source="task", operation_id="", manager=None, run_context=None):
        if source not in {"task", "schedule"}:
            raise ValueError("运行来源必须为 task 或 schedule。")
        source = "schedule" if scheduled_at else source
        with self.lock:
            if self.s.closing:
                raise ValueError("后台正在退出，任务尚未启动。")
            key = f"schedule:{favorite_id}:{scheduled_at or operation_id or uuid.uuid4().hex}"
            previous = self.s.store.get_state(key)
            if previous:
                if previous.get("source", source) != source:
                    raise ValueError("同一运行标识不能用于不同的运行来源。")
                return {**self.receipt(previous), "claimed": True}
            favorite = self.s.automations._find(favorite_id)
            schedule = favorite.get("schedule") or {}
            if scheduled_at and (not schedule.get("enabled") or schedule.get("next_run_at") != scheduled_at):
                return {"status": "superseded", "task_id": favorite_id}
            if source == "schedule" and not schedule:
                raise ValueError("任务没有定时计划。")
            prompt = favorite_effective_prompt(favorite) if source == "schedule" else favorite.get("prompt") or ""
            snapshot_manager = self.s.automations.manager()
            self.s.automations.validate(favorite, snapshot_manager, include_delivery=source == "schedule")
            if manager is not None:
                self.s.automations.validate(favorite, manager, run_context, include_delivery=source == "schedule")
            busy = [item for item in self.s.config.get_favorite_run_history() if item["favorite_id"] == favorite_id and item["status"] == "running"]
            if busy:
                if scheduled_at:
                    record = make_favorite_run_record(favorite, status="skipped", scheduled_at=scheduled_at,
                        summary="上一轮仍在运行，已跳过本次计划。")
                    claim = {"favorite": favorite, "record": record, "run_id": "", "done": True,
                             "source": source, "submission_status": "skipped"}
                    self.s.store.put_state(key, claim)
                    self.record(claim)
                    return self.receipt(claim)
                raise ValueError("这个常用任务仍在运行，请先查看运行结果。")
            record = make_favorite_run_record(favorite, trigger_source="scheduler" if scheduled_at else source,
                                               scheduled_at=scheduled_at)
            run_id = uuid.uuid4().hex if prompt else ""
            model_id = self.s.config.get_selected_model_id()
            record.update(run_id=run_id, task_revision=favorite["revision"], model_id=model_id,
                skill_versions={name: hashlib.sha256(snapshot_manager.get_full_skill_prompt(name).encode()).hexdigest()
                                for name in favorite.get("skill_names") or []})
            claim = {"favorite": favorite, "record": record, "run_id": run_id, "done": False,
                     "source": source, "submission_status": "preparing"}
            # Once claimed, restart only reconciles receipts. It never repeats
            # tools whose external effect cannot be proven absent after a crash.
            self.s.store.put_state(key, claim)
            self.record(claim)
            self.starting.add(key)
        try:
            conversation = self.s.dispatch("conversation.create", {"workspace_dir": favorite.get("workspace_dir") if favorite.get("execution_mode") == "workspace" else None})
            cid = conversation["id"]
            self.s.storage.upsert_conversation(cid, title=favorite["name"])
            self.s.storage.update_conversation_meta(cid, {"selected_skill_names": favorite.get("skill_names") or [],
                                                       "automation_task_id": favorite_id, "automation_task_revision": favorite["revision"]})
            with self.lock:
                record["session_id"] = cid
                self.s.store.put_state(key, claim)
                self.record(claim)
            if prompt:
                result = self.s.dispatch("conversation.send", {"conversation_id": cid, "request_id": run_id, "message_id": uuid.uuid4().hex,
                    "text": prompt, "attachments": [], "model_id": model_id,
                    "preferences": {"selected_skill_names": favorite.get("skill_names") or [], "mode": "work"}})
                if not result.get("accepted"):
                    raise RuntimeError("后台未确认接收任务，请查看运行记录；不会自动重复提交。")
                claim["submission_status"] = (result.get("run") or {}).get("status") or "queued"
            else:
                claim.update(done=True, submission_status="draft_ready")
                record.update(status="completed", summary="已创建能力组合会话，等待输入任务内容。", finished_at=int(time.time()))
            log.info("automation_started task=%s run=%s conversation=%s status=%s", favorite_id, run_id, cid, claim["submission_status"])
        except Exception as exc:
            log.exception("favorite_start_failed favorite=%s run=%s", favorite_id, run_id)
            accepted = self.s.store.get_run(run_id) if run_id else None
            if accepted:
                # The send receipt was lost, but the durable run proves acceptance.
                claim["submission_status"] = accepted["status"]
            else:
                record.update(status="error", error=str(exc), finished_at=int(time.time()))
                claim.update(done=True, submission_status="error")
        finally:
            with self.lock:
                self.s.store.put_state(key, claim)
                self.record(claim)
                self.starting.discard(key)
        self.s.publish({"event": "conversation_changed", "conversation_id": record["session_id"]})
        return self.receipt(claim)

    def reconcile(self):
        with self.s.storage._connect() as db:
            claims = [(row[0], json.loads(row[1])) for row in db.execute("SELECT key,value FROM desktop_view_state WHERE key LIKE 'schedule:%'")]
        history = {item["id"]: item for item in self.s.config.get_favorite_run_history()}
        for key, claim in claims:
            if key in self.starting:
                continue
            if claim["done"]:
                # Recover the history projection after a crash between claim and config writes.
                # Do not resurrect records already removed by the history retention limit.
                previous = history.get(claim["record"]["id"])
                if previous is not None and previous != normalize_favorite_run_record(claim["record"]):
                    self.record(claim)
                    log.info("automation_history_recovered task=%s run=%s", claim["record"]["favorite_id"], claim.get("run_id"))
                    self.s.publish({"event": "schedule_changed"})
                continue
            run = self.s.store.get_run(claim["run_id"])
            if run and run["status"] in {"queued", "running", "stopping"}:
                continue
            status = {"failed": "error", "completed": "completed"}.get((run or {}).get("status"), "interrupted")
            record = claim["record"]
            record.update(status=status, error=(run or {}).get("error") or ("上次启动被中断，请核对对话后手动运行。" if not run else ""), finished_at=int(time.time()))
            claim["submission_status"] = status
            claim["done"] = True
            delivery = ((claim["favorite"].get("schedule") or {}).get("delivery") or {}) if claim.get("source", "schedule") == "schedule" else {}
            if delivery.get("enabled"):
                record.update(delivery_status="unknown", delivery_error="发送结果尚未确认，请核对消息记录。")
            # Persist terminal state before delivery: an uncertain delivery is
            # visible and must not be silently enqueued a second time.
            self.s.store.put_state(key, claim)
            self.record(claim)
            if delivery.get("enabled"):
                try:
                    from core.favorite_delivery import FavoriteDeliveryService
                    messages = self.s.storage.get_messages(record["session_id"])
                    output = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "assistant" and not m.get("tool_calls")), "")
                    job = FavoriteDeliveryService(self.s.storage).enqueue_delivery(run_history_id=record["id"], favorite_id=record["favorite_id"],
                        session_id=record["session_id"], binding_id=delivery["binding_id"], favorite_name=record["favorite_name"], terminal_status=status,
                        content=output, error=record["error"])
                    record.update(delivery_id=job["id"], delivery_status=job["status"], delivery_error="")
                except Exception as exc:
                    record.update(delivery_status="failed", delivery_error=str(exc))
                    log.exception("favorite_delivery_failed run=%s", claim["run_id"])
            self.s.store.put_state(key, claim)
            self.record(claim)
            log.info("automation_finished task=%s run=%s status=%s delivery=%s", record["favorite_id"], claim["run_id"], status, record.get("delivery_status") or "none")
            self.s.publish({"event": "schedule_changed"})

    def tick(self):
        with self.lock:
            self.reconcile()
            favorites = self.s.config.get_favorites()
        now = int(time.time())
        for candidate in favorites:
            with self.lock:
                favorite = next((item for item in self.s.config.get_favorites() if item["id"] == candidate["id"]), None)
                if favorite is None:
                    continue
                schedule = favorite.get("schedule") or {}
                due = int(schedule.get("next_run_at") or 0)
                if not schedule.get("enabled") or not due or due > now:
                    continue
                revision = favorite["revision"]
                key = f"schedule:{favorite['id']}:{due}"
                if due + FAVORITE_RUN_GRACE_SECONDS < now and not self.s.store.get_state(key):
                    record = make_favorite_run_record(favorite, status="missed", scheduled_at=due,
                        summary="后台未运行，已跳过错过的计划；可手动运行。")
                    claim = {"favorite": favorite, "record": record, "run_id": "", "done": True, "submission_status": "missed"}
                    self.s.store.put_state(key, claim)
                    self.record(claim)
                    schedule["last_missed_at"] = due
                    missed = True
                else:
                    missed = False
            if not missed:
                try:
                    result = self.s.automations.run(favorite["id"], scheduled_at=due)
                    if result["status"] == "superseded":
                        continue
                except Exception as exc:
                    log.exception("favorite_trigger_failed favorite=%s", favorite["id"])
                    with self.lock:
                        if not self.s.store.get_state(key):
                            record = make_favorite_run_record(favorite, status="error", scheduled_at=due, error=str(exc))
                            claim = {"favorite": favorite, "record": record, "done": True, "run_id": "", "submission_status": "error"}
                            self.s.store.put_state(key, claim)
                            self.record(claim)
            with self.lock:
                current = next((item for item in self.s.config.get_favorites() if item["id"] == favorite["id"]), None)
                if not current or current["revision"] != revision or (current.get("schedule") or {}).get("next_run_at") != due:
                    continue
                if missed:
                    current["schedule"]["last_missed_at"] = due
                elif (self.s.store.get_state(key) or {}).get("run_id"):
                    current["schedule"]["last_run_at"] = now
                if current["schedule"].get("schedule_type") == "once":
                    current["schedule"]["enabled"] = False
                else:
                    current = advance_favorite_schedule(current, now_ts=now, after_ts=now)
                self.s.config.upsert_favorite(current)
            self.s.publish({"event": "schedule_changed"})

    def loop(self):
        while not self.stop_event.wait(5):
            if self.s.closing:
                continue
            try:
                self.tick()
            except Exception as exc:
                log.exception("schedule_tick_failed")
                self.s.publish({"event": "service_error", "error": "定时任务检查失败：" + str(exc)})
