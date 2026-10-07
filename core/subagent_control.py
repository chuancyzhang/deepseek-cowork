"""Host-only child controls. Never switch execution owners after a transport failure."""
from core.agent_manager import get_agent_manager_registry


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
            result = []
            owners = {((r.get("meta") or {}).get("execution_owner") or "local") for r in rows}
            for owner in sorted(owners):
                try:
                    result.extend(self._request_owner(owner, conversation_id, "list", {}))
                except Exception as exc:
                    for row in rows:
                        if ((row.get("meta") or {}).get("execution_owner") or "local") == owner:
                            result.append(dict(row, control_error="运行方不可用：" + str(exc)))
            return result
        row = self.storage.get_agent(data.get("agent_id", ""))
        if not row or row["conversation_id"] != conversation_id:
            raise ValueError("子任务不属于当前会话")
        owner = (row.get("meta") or {}).get("execution_owner") or "local"
        return self._request_owner(owner, conversation_id, operation, data)

    def _request_owner(self, owner, conversation_id, operation, data):
        if owner == "daemon":
            if self.daemon_client is None:
                raise RuntimeError("守护进程尚未连接")
            return self.daemon_client.subagent_control(conversation_id, operation, **data)
        if owner != "local":
            raise ValueError("未知执行进程")
        manager = get_agent_manager_registry().get_session_manager(
            conversation_id, self.storage, self.config)
        return dispatch_subagent(manager, operation, data)
