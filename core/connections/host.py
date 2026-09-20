"""The narrow API available to explicitly integrated host capabilities."""
from .broker import ConnectionBroker, requirement_list, task_identity
from .errors import ConnectionError


class CapabilityConnections:
    def __init__(self, capability, requirements, authorization, *, data_dir=None):
        self.capability = capability
        self.requirements = {x["id"]: x for x in requirement_list(requirements)}
        self.authorization = authorization
        self.broker = ConnectionBroker(data_dir=data_dir)

    def request(self, requirement_id, operation, method, url, *, resources=(), **kwargs):
        declaration = self.requirements.get(requirement_id)
        if not declaration or operation not in declaration["operations"] or self.authorization is None:
            raise ConnectionError("forbidden")
        if declaration.get("resources") and (not resources or not set(resources) <= set(declaration["resources"])):
            raise ConnectionError("forbidden")
        self.authorization.check()
        if not self.broker.store.selection(self.capability, requirement_id) and not self.broker.store.binding(task_identity(self.authorization), self.capability, requirement_id):
            from ..interaction import interaction_service
            # This is reached only by an explicitly integrated host request, never by old skills.
            for _ in range(2):
                choices = [{"label": c["name"] + " · " + (c.get("profile", {}).get("label") or c["identity"]["subject"]), "value": c["id"]}
                           for c in self.broker.store.list() if c["state"] == "ready" and c["template"]["service"] == declaration["service"]]
                choices.extend([{"label": "在设置中添加连接后重新选择", "value": "manage"}, {"label": "取消", "value": "cancel"}])
                reply = interaction_service.create_request(self.authorization.session_id, "choice",
                    "请选择本次使用的服务连接。也可先到设置 → 账号与连接添加并登录，然后返回重新选择。",
                    title="选择服务连接", options=choices, timeout_seconds=86400, source_tool="connection_selection",
                    metadata={"run_id": self.authorization.run_id, "host_only": True},
                    abort_check=self.authorization._cancelled.is_set, require_receiver=True)
                self.authorization.check()
                picked = reply.get("selected_options", [])
                if reply.get("status") != "completed" or len(picked) != 1 or picked[0] == "cancel":
                    raise ConnectionError("cancelled")
                if picked[0] == "manage":
                    continue
                if picked[0] not in {c["value"] for c in choices[:-2]}:
                    raise ConnectionError("forbidden")
                item = self.broker.store.get(picked[0])
                if item["template"]["service"] != declaration["service"]:
                    raise ConnectionError("target_changed")
                # Binding asks for the separate capability grant before any external request.
                bound = self.broker.binding(task_identity(self.authorization), self.capability, requirement_id,
                    connection_id=picked[0], operations=[operation], resources=resources, service=declaration["service"])
                self.broker.store.select(self.capability, requirement_id, picked[0])
                break
        binding = self.broker.binding(task_identity(self.authorization), self.capability, requirement_id,
                                      operations=[operation], resources=resources, service=declaration["service"])
        return self.broker.request(binding, operation, method, url, resources=resources,
                                   cancelled=self.authorization._cancelled.is_set, **kwargs)
