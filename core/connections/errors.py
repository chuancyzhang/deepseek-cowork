"""Public connection errors contain no remote response or credential values."""


MESSAGES = {
    "invalid_template": "连接模板无效，请检查高级设置中的模板。",
    "template_conflict": "模板 ID 已存在，请明确选择更新或保留原模板。",
    "locked_template": "此模板由分发方管理，不能在本机覆盖。",
    "unsupported_adapter": "此连接的认证方式尚未安装或不受支持。",
    "dependency_missing": "此认证方式缺少依赖，请安装账号连接组件后重试。",
    "not_connected": "尚未连接，请在账号与连接中完成登录。",
    "authentication_required": "登录已失效，请使用原账号重新认证后继续。",
    "forbidden": "当前账号或能力没有此操作的访问权限。",
    "unavailable": "服务不可达，请检查网络与服务地址后重试。",
    "invalid_response": "认证服务返回的信息不完整或无法验证。",
    "identity_changed": "账号、组织或访问范围已变化，请明确重新绑定连接。",
    "target_changed": "目标地址与连接不一致，本次请求未发送。",
    "grant_required": "此能力尚未获得连接使用授权，请在账号与连接中授权。",
    "revoked": "连接已断开或授权已撤销，本次操作未执行。",
    "cancelled": "认证或任务已取消，已完成的内容已保留。",
    "outcome_unknown": "操作结果尚未确认，请核对远端结果后再继续。",
    "conflict": "连接状态已变化，请刷新页面后重试。",
    "unsupported_operation": "此连接不支持该操作，请使用服务提供的接入方式。",
}


class ConnectionError(RuntimeError):
    def __init__(self, code, *, status=0):
        self.code = code if code in MESSAGES else "invalid_response"
        self.status = status
        super().__init__(MESSAGES[self.code])

    def public(self):
        return {"ok": False, "code": self.code, "error": str(self)}
