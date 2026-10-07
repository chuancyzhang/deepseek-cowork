"""Host-only child controls. Never switch execution owners after a transport failure."""
import os
from core.agent_manager import get_agent_manager_registry
from core.runtime_journal import RuntimeJournal


def dispatch_subagent(manager, operation, data):
    if operation == "list":
        return manager.list_agent_summaries()
    target = str(data.get("agent_id") or "")
    with manager._lock:
        manager._resolve(target, exact=True)
    if operation == "history":
        return manager.history(target, data.get("before"), data.get("limit", 50))
    if operation == "events":
        return manager.events(target, data.get("run_id", ""), data.get("after", 0))
    if operation == "send":
        if not data.get("message_id"):
            raise ValueError("缺少消息 ID")
        with manager._lock:
            record = manager._resolve(target, exact=True)
            manager._refresh_queue(record)
            resume = record.paused and not record.pending_inputs and record.worker is None
            result = manager.send_input(target, data.get("content"), data["message_id"], source="human")
            if resume:
                manager.resume_agent(target)
            return result
    if operation == "interrupt":
        if not data.get("run_id"):
            raise ValueError("缺少运行 ID")
        return manager.interrupt_agent(target, data["run_id"])
    if operation == "resume":
        return manager.resume_agent(target)
    raise ValueError("不支持的子任务操作")


class SubagentControl:
    def __init__(self, storage, config, daemon_client=None):
        self.storage, self.config, self.daemon_client = storage, config, daemon_client

    def request(self, conversation_id, operation, **data):
        rows = self.storage.list_agents(conversation_id)
        if operation == "list":
            result = [dict(r, read_only=True, control_error="旧版记录缺少运行归属，历史只读。可在主对话创建新的子任务。")
                      for r in rows if not (r.get("meta") or {}).get("execution_owner")]
            owners = {(r.get("meta") or {}).get("execution_owner") for r in rows} - {None, ""}
            for owner in sorted(owners):
                try:
                    result.extend(s for s in self._request_owner(owner, conversation_id, "list", {})
                                  if (s.get("meta") or {}).get("execution_owner") == owner)
                except Exception as exc:
                    for row in rows:
                        if (row.get("meta") or {}).get("execution_owner") == owner:
                            result.append(dict(row, control_error="运行方不可用：" + str(exc)))
            return result
        row = self.storage.get_agent(data.get("agent_id", ""))
        if not row or row["conversation_id"] != conversation_id:
            raise ValueError("子任务不属于当前会话")
        owner = (row.get("meta") or {}).get("execution_owner")
        if not owner:
            if operation == "history":
                return self._saved_history(row, data, "旧版记录缺少运行归属，历史只读。")
            if operation == "events":
                return {"events": [], "summary": row}
            raise ValueError("旧版记录缺少运行归属，请在主对话创建新的子任务")
        try:
            return self._request_owner(owner, conversation_id, operation, data)
        except (OSError, ConnectionError, TimeoutError) as exc:
            if operation == "history":
                return self._saved_history(row, data, "运行方不可用，仅展示已保存历史：" + str(exc))
            raise

    def _saved_history(self, row, data, reason):
        page = self.storage.agent_history_page(row["id"], data.get("before"), data.get("limit", 50))
        journal = RuntimeJournal(os.path.dirname(self.storage.db_path))
        namespace = "subagent:" + row["conversation_id"] + ":" + row["id"]
        activity = []
        for message in page["messages"]:
            if not message["id"].endswith(":assistant"):
                continue
            run_id = message["id"].split(":")[0]
            content, reasoning = "", ""
            for event in journal.read_events(namespace, run_id):
                payload = event.get("payload") or {}
                if payload.get("event_type") in {"tool_call", "tool_result"}:
                    activity.append(event)
                if payload.get("status") == "content":
                    content += payload.get("content_delta", "")
                elif payload.get("status") == "content_snapshot":
                    content = payload.get("content", "")
                elif payload.get("status") == "thinking":
                    reasoning += payload.get("reasoning_delta", "")
            # Committed final messages win; journal only fills uncommitted placeholders.
            if not message.get("content"):
                message["content"] = content
            if not message.get("reasoning_content"):
                message["reasoning_content"] = reasoning
        page.update(summary=dict(row, read_only=True, control_error=reason),
                    inbox=self.storage.agent_inbox(row["id"]), activity=activity)
        return page

    def _request_owner(self, owner, conversation_id, operation, data):
        if owner == "daemon":
            if self.daemon_client is None:
                raise ConnectionError("守护进程尚未连接")
            return self.daemon_client.subagent_control(conversation_id, operation, **data)
        if owner != "local":
            raise ValueError("未知执行进程")
        manager = get_agent_manager_registry().get_session_manager(
            conversation_id, self.storage, self.config)
        return dispatch_subagent(manager, operation, data)
