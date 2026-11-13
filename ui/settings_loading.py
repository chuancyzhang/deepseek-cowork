"""Settings navigation metadata is independent of constructed editors."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton


# title, searchable fields (advanced, hidden theme fields are intentionally absent).
PAGE_SPECS = {
    "appearance": ("外观与阅读", "工作台风格 界面字体 文字大小 界面密度 主题 AI 设计"),
    "preferences": ("回答偏好", "回答个性与偏好 交流 表达"),
    "models": ("模型与服务", "已连接的服务 新对话默认模型 API 密钥 模型"),
    "im": ("消息连接", "聊天软件 飞书 钉钉 企业微信 QQ 微信"),
    "account": ("企业账号", "企业登录 授权模型 账号"),
    "security": ("数据安全", "安全插件 隐私 凭证 令牌化"),
    "variables": ("变量与凭据", "变量 凭据 敏感值"),
    "storage": ("文件与存储", "默认工作区 聊天记录目录 独立聊天目录"),
    "experiments": ("实验功能", "长上下文 自动压缩 内置浏览器自动化"),
    "components": ("组件与依赖", "下载源 Python 工具包 Skill 依赖 后台任务"),
    "logs": ("运行日志", "查看 导出 日志 时间范围 来源"),
    "update": ("更新与关于", "应用更新 版本 安装包"),
    "mcp": ("MCP 服务", "工具 连接 服务"),
    "agents": ("专家", "角色 技能 提示词"),
    "projects": ("项目", "工作区"),
    "sessions": ("对话", "历史"),
    "memory": ("记忆", "全局长期摘要 当前工作区摘要 项目记忆"),
    "archive": ("归档", "已归档项目 已归档对话 恢复"),
}
PAGE_GROUPS = {
    "settings": [
        ("外观与阅读", "fa5s.palette", ("appearance",)),
        ("回答偏好", "fa5s.comment", ("preferences",)),
        ("模型与服务", "fa5s.brain", ("models",)),
        ("消息连接", "fa5s.comments", ("im",)),
        ("企业账号", "fa5s.user-circle", ("account",)),
        ("隐私与数据", "fa5s.shield-alt", ("security", "variables")),
        ("文件与存储", "fa5s.folder-open", ("storage",)),
        ("高级与维护", "fa5s.sliders-h", ("experiments", "components", "logs", "update")),
    ],
    "capabilities": [("MCP 服务", "fa5s.plug", ("mcp",)), ("专家", "fa5s.user-astronaut", ("agents",))],
    "projects": [("项目", "fa5s.folder-open", ("projects",)), ("对话", "fa5s.comments", ("sessions",)),
                 ("记忆", "fa5s.brain", ("memory",)), ("归档", "fa5s.archive", ("archive",))],
}
PAGE_FIELDS = {
    "appearance": {"appearance": None},
    "preferences": {"memory": ("soul",)},
    "memory": {"memory": ("global", "workspace", "workspace_dir")},
    "models": {"config": ("model_channels", "selected_model_id")},
    "agents": {"config": ("agent_profiles",)},
    "mcp": {"config": ("mcp_servers",)},
    "storage": {"config": ("default_workspace", "history_dir", "chat_workspace_root")},
    "security": {"config": ("data_security",)},
    "components": {"config": ("download_sources",)},
}


class LazySettingsPage(QWidget):
    """One explicit creation boundary; failures remain visible until retried."""

    def __init__(self, factory, parent=None, *, on_reuse=None):
        super().__init__(parent)
        self.factory = factory
        self.on_reuse = on_reuse
        self.page = None
        self.error = None
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)

    def ensure(self):
        if self.page is not None:
            if self.on_reuse is not None:
                self.on_reuse()
            return self.page
        if self.error is not None:
            return None
        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        try:
            page = self.factory(host)
            layout.addWidget(page)
        except Exception:
            host.hide()
            host.deleteLater()
            self.error = QWidget(self)
            error_box = QVBoxLayout(self.error)
            message = QLabel("此设置页加载失败，其他页面和已有输入已保留。请重试；仍失败可导出运行日志。", self.error)
            message.setWordWrap(True)
            error_box.addWidget(message)
            retry = QPushButton("重新加载", self.error)
            retry.clicked.connect(self.retry)
            error_box.addWidget(retry)
            self.box.addWidget(self.error)
            return None
        self.page = page
        self.box.addWidget(host)
        return page

    def retry(self):
        if self.error is not None:
            self.error.hide()
            self.error.deleteLater()
            self.error = None
        self.ensure()
