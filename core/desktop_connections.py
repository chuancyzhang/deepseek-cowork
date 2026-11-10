"""Identity-scoped adapters for connected libraries and enterprise spaces."""
from pathlib import Path

from core.enterprise_client import EnterpriseClient, EnterpriseStore
from core.knowledge_library import KnowledgeStore
from core.knowledge_sources import MultiSourceKnowledgeService
from core.team_spaces import TeamSpaceClient


class Connections:
    methods = frozenset({"library.status", "library.select", "library.login", "library.authorize", "library.confirm",
        "library.logout", "library.catalog", "library.children", "library.search", "library.read", "library.reference",
        "library.upload", "library.uploads", "library.check_upload", "library.tenant",
        "enterprise.status", "enterprise.bind", "enterprise.providers", "enterprise.login", "enterprise.refresh", "enterprise.logout",
        "team.list", "team.detail", "team.collection", "team.object", "team.execute", "team.upload", "team.download", "team.conversation"})

    def __init__(self, service):
        self.s = service
        self.library = MultiSourceKnowledgeService(KnowledgeStore(service.config.data_dir))
        self.enterprise = EnterpriseClient(EnterpriseStore(service.config.data_dir))

    def dispatch(self, method, p):
        action = method.split(".")[1]
        if method.startswith("library."):
            if action == "status":
                return {"active": self.library.source, "sources": {name: provider.snapshot() for name, provider in self.library.providers.items()}}
            source = p.get("source") or self.library.source
            provider = self.library.provider(source)
            if action == "select":
                self.library.select_source(source)
                return {"saved": True}
            if action == "uploads":
                return self.library.store.uploads()
            if action == "authorize":
                return {"url": provider.start_authorization()}
            if action in {"login", "confirm", "logout", "tenant"}:
                def auth(cancelled, progress):
                    if action == "logout":
                        provider.logout()
                    elif action == "tenant":
                        provider.switch_tenant(p["tenant_id"])
                    elif action == "confirm":
                        provider.finish_authorization(confirmed=True, cancelled=cancelled.is_set)
                    elif source == "weknora":
                        provider.login(p["url"], p["email"], p["password"], cancelled=cancelled.is_set)
                    elif source == "lexiang":
                        provider.connect(p["company"], p["token"], cancelled=cancelled.is_set)
                    else:
                        raise ValueError("请使用浏览器授权连接腾讯文档。")
                    return provider.snapshot()
                return self.s.jobs.submit("资料库连接", auth)
            scope = provider.snapshot()
            if not scope or scope.get("unavailable"):
                raise ValueError("请先连接此资料来源。已选择的资料仍保留。")
            if action == "reference":
                ref = provider.reference(scope, p["collection"], p["title"], p.get("item", ""))
                self.library.store.visit(ref)
                return ref
            def operation(cancelled, progress):
                if action == "catalog":
                    return provider.catalog(scope)
                if action == "children":
                    if source == "weknora":
                        return {"files": provider.files(scope, p["collection"], p.get("page", 1), p.get("parent")), "folders": provider.folders(scope, p["collection"])}
                    return provider.children(scope, p["collection"], p.get("parent", ""), p.get("page", 1))
                if action == "search":
                    if source == "weknora":
                        return provider.tool(scope, "search", {"query": p["query"]}, cancelled=cancelled.is_set)
                    return provider.search(scope, p["query"], cancelled=cancelled.is_set)
                if action == "read":
                    if source == "weknora":
                        return {"html": provider.preview_html(scope, p["item"])}
                    return provider.read(scope, p["item"], p["collection"], cancelled=cancelled.is_set)
                if action == "upload":
                    return provider.upload(scope, p["path"], p["collection"], p.get("parent", ""))
                if action == "check_upload":
                    task = next((item for item in self.library.store.uploads() if item["id"] == p["id"]), None)
                    if not task or task.get("source", "weknora") != source:
                        raise ValueError("上传任务不属于当前资料来源。")
                    return provider.check_upload(task)
                raise ValueError("不支持的资料操作。")
            return self.s.jobs.submit("读取资料" if action != "upload" else "上传资料", operation)
        if method.startswith("enterprise."):
            if action == "status":
                return self.enterprise.store.read()
            def operation(cancelled, progress):
                if action == "bind":
                    self.enterprise.bind(p["connection"])
                elif action == "providers":
                    return self.enterprise.inspect(self.enterprise.store.connection())
                elif action == "login":
                    # Publish the public login URL; the authenticated host owns
                    # opening the system browser, never the worker thread.
                    self.enterprise.login(p.get("provider"), cancelled,
                        lambda url: progress("请在浏览器完成登录", url=url), username=p.get("username", ""), password=p.get("password", ""))
                elif action == "refresh":
                    self.enterprise.refresh()
                elif action == "logout":
                    self.enterprise.logout()
                return self.enterprise.store.read()  # never persist bearer tokens in job results
            return self.s.jobs.submit("企业连接", operation)
        client = TeamSpaceClient(self.enterprise.store)
        space = str(p.get("space_id") or "")
        def operation(cancelled, progress):
            if action == "list":
                return client.list(**p.get("filters", {}))
            if action == "detail":
                return client.detail(space)
            if action == "collection":
                if p["collection"] not in {"plans", "assets", "shares", "snapshots", "members", "events", "invites", "comments"}:
                    raise ValueError("未知的空间内容。")
                return client.collection(space, p["collection"], **p.get("filters", {}))
            if action == "object":
                return client.object(space, p["kind"], p["id"])
            if action == "execute":
                return client.execute(space, p["action"], p["payload"], operation_id=p["operation_id"])
            if action == "upload":
                frozen = client.freeze_file(p["path"], cancel=cancelled)
                return client.upload(space, frozen, operation_id=p["operation_id"], cancel=cancelled,
                    progress=lambda amount: progress("正在上传…", transferred=amount))
            if action == "download":
                return client.download(space, p["revision_id"], Path(p["path"]), cancel=cancelled)
            if action == "conversation":
                profile = self.s.config.get_model_profile(p["model_id"])
                if not profile:
                    raise ValueError("请选择当前可用模型。")
                client.policy(space, profile)
                detail = client.detail(space)
                if detail["state"] != "active":
                    raise ValueError("空间已归档，请先恢复空间。")
                if detail.get("description_revision") != p.get("description_revision"):
                    raise ValueError("空间说明已变化，请刷新后重新打开任务。输入已保留。")
                referenced = detail.get("skills") or []
                if referenced and not p.get("acknowledge_skills"):
                    raise ValueError("此空间引用了指定版本的技能，请核对本机技能或确认本次不使用这些引用。")
                binding = client.local_store.bind_workspace(client.identity, space, p["directory"],
                    [item["path"] for item in self.s.config.get_projects(include_hidden=True)])
                binding["task_context"] = {"space_name": detail["name"], "description": detail["description"],
                    "description_revision": detail["description_revision"], "inputs": [], "skill_references": referenced}
                client.session()
                conversation = self.s.dispatch("conversation.create", {"workspace_dir": binding["workspace_dir"]})
                cid = conversation["id"]
                self.s.storage.upsert_conversation(cid, title=p["title"])
                self.s.storage.bind_team_space(cid, binding)
                self.s.storage.update_conversation_meta(cid, {"workspace_source": "team_space", "selected_model_id": profile["id"]})
                self.s.store.put_state("draft:" + cid, {"text": p["prompt"], "attachments": []})
                return {"conversation_id": cid}
            raise ValueError("不支持的团队空间操作。")
        return self.s.jobs.submit("团队空间", operation)
