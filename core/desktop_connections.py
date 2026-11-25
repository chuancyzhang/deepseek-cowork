"""Identity-scoped adapters for connected libraries and enterprise spaces."""
from pathlib import Path

from core.enterprise_client import EnterpriseClient, EnterpriseStore
from core.knowledge_library import KnowledgeStore
from core.knowledge_sources import MultiSourceKnowledgeService
from core.team_spaces import TeamSpaceClient


class Connections:
    methods = frozenset({"library.status", "library.select", "library.login", "library.authorize", "library.confirm",
        "library.logout", "library.catalog", "library.children", "library.search", "library.read", "library.read_result", "library.reference", "library.references",
        "library.upload", "library.uploads", "library.check_upload", "library.tenant", "library.import", "library.mkdir", "library.folder",
        "library.rename", "library.trash", "library.trash_list", "library.restore", "library.locate",
        "library.create_file", "library.paste", "library.edit_open", "library.edit_save", "library.create_manual",
        "enterprise.status", "enterprise.capabilities", "enterprise.capabilityCredential", "enterprise.bind", "enterprise.providers", "enterprise.login", "enterprise.refresh", "enterprise.logout",
        "team.list", "team.detail", "team.collection", "team.object", "team.execute", "team.upload", "team.download", "team.conversation"})

    def __init__(self, service):
        self.s = service
        self.library = MultiSourceKnowledgeService(KnowledgeStore(service.config.data_dir))
        from core.local_library import LocalLibrary
        self.local = LocalLibrary(service)
        self.enterprise = EnterpriseClient(EnterpriseStore(service.config.data_dir))
        from core.enterprise_chat_audit import start_sync
        start_sync(self.enterprise.store)

    def dispatch(self, method, p):
        action = method.split(".")[1]
        if method.startswith("library."):
            if action == "status":
                return {"active": self.library.source, "sources": {name: provider.snapshot() for name, provider in self.library.providers.items()}}
            source = p.get("source") or self.library.source
            entries = p["items"] if action == "references" else [p]
            if not isinstance(entries, list) or not entries or any(not isinstance(item, dict) for item in entries):
                raise ValueError("请选择可引用的资料。")
            if source == "local":
                if action == "catalog":
                    return {"mine": self.local.projects(), "public": self.local.public()}
                if action == "folder":
                    return self.local.folder(p)
                if action == "locate":
                    _, path = self.local.checked(p["collection"], p["item"])
                    return {"path": str(path)}
                if action == "mkdir":
                    return self.local.mkdir(p)
                if action == "create_file":
                    return self.local.create_file(p)
                if action == "paste":
                    return self.s.jobs.submit("粘贴公共资料", lambda cancelled, progress: self.local.paste(p, cancelled, progress))
                if action == "edit_open":
                    return self.local.edit_open(p)
                if action == "edit_save":
                    return self.local.edit_save(p)
                if action == "rename" and p.get("collection") == "public":
                    return self.local.rename(p)
                if action == "trash" and p.get("collection") == "public":
                    return self.s.jobs.submit("移到资料库回收站", lambda cancelled, progress: self.local.trash(p, cancelled, progress))
                if action == "trash_list":
                    return self.local.trash_list()
                if action == "restore":
                    return self.s.jobs.submit("还原公共资料", lambda cancelled, progress: self.local.restore(p, cancelled, progress))
                if action == "import":
                    return self.s.jobs.submit("添加公共资料", lambda cancelled, progress: self.local.import_files(p, cancelled, progress))
                if action in {"children", "search"}:
                    return self.s.jobs.submit("读取本地资料", lambda cancelled, progress: self.local.files(p, cancelled, progress))
                if action == "read":
                    return self.local.read(p)
                if action in {"reference", "references"}:
                    refs = [{"source": "local", **self.local.describe(entry)} for entry in entries]
                    return refs[0] if action == "reference" else refs
                raise ValueError("本地资料使用项目目录，无需连接或上传。")
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
            if action == "read_result":
                from core.knowledge_library import same_identity
                job = self.s.jobs.get(str(p["job_id"]))
                result = job.get("result") or {}
                if (job["status"] != "completed" or result.get("kind") != "library_preview"
                        or not same_identity(result.get("scope", {}), scope)
                        or result.get("scope", {}).get("generation") != scope.get("generation")):
                    raise ValueError("预览尚未完成或资料库账号已改变，请重新打开文件。")
                return {key: result[key] for key in ("path", "name", "fingerprint", "payload")}
            if action in {"reference", "references"}:
                refs = [provider.reference(scope, entry["collection"], entry["title"], entry.get("item", "")) for entry in entries]
                for ref in refs:
                    self.library.store.visit(ref)
                return refs[0] if action == "reference" else refs
            def operation(cancelled, progress):
                if action == "catalog":
                    if source != "weknora" and p.get("refresh"):
                        provider.clear_cache()
                    return provider.catalog(scope)
                if action == "children":
                    if source == "weknora":
                        result = provider.children(scope, p["collection"], p.get("parent", ""), p.get("page", 1))
                        url = provider.reference(scope, p["collection"], "")["url"]
                        return {**result, "items": [{**item, "knowledge_base_id": p["collection"], "url": url} for item in result["items"]]}
                    return provider.children(scope, p["collection"], p.get("parent", ""), p.get("page", 1))
                if action == "search":
                    if source == "weknora":
                        args = {"query": p["query"]}
                        if p.get("collection"):
                            args["kb_ids"] = [p["collection"]]
                        matches = provider.tool(scope, "search", args, cancelled=cancelled.is_set)["results"]
                        # Search is ranked per chunk; picking or opening acts on the source document.
                        documents = {}
                        for item in matches:
                            identifier = item["knowledge_id"]
                            title = item.get("knowledge_title") or item.get("title") or item.get("file_name") or identifier
                            base = item["knowledge_base_id"]
                            documents.setdefault((base, identifier), {**provider.reference(scope, base, title, identifier), "id": identifier})
                        return list(documents.values())
                    return provider.search(scope, p["query"], cancelled=cancelled.is_set)
                if action == "read":
                    from core.library_preview import weknora_preview, document_content, check_cancelled
                    if source == "weknora":
                        return weknora_preview(provider, scope, p["item"], self.s.config.data_dir, cancelled, progress)
                    value = provider.read(scope, p["item"], p.get("collection", ""), cancelled=cancelled.is_set)
                    check_cancelled(cancelled)
                    content = document_content(value)
                    if not content:
                        raise ValueError("平台尚未返回可显示的正文，请在原平台核对文件内容或解析状态。")
                    return {"text": content, "format": "markdown", "url": value.get("url", "")}
                if source == "weknora" and action == "edit_open":
                    item = provider.manual_document(scope, p["collection"], p["item"], cancelled.is_set)
                    return {"id": item["id"], "name": item["title"], "path": item["id"],
                            "fingerprint": str(item["metadata"].get("version")),
                            "payload": {"kind": "text", "content": item["metadata"]["content"]}}
                if source == "weknora" and action in {"create_manual", "edit_save"}:
                    return provider.save_manual_document(scope, p, cancelled.is_set)
                if action == "upload":
                    return provider.upload(scope, p["path"], p["collection"], p.get("parent", ""))
                if action == "check_upload":
                    task = next((item for item in self.library.store.uploads() if item["id"] == p["id"]), None)
                    if not task or task.get("source", "weknora") != source:
                        raise ValueError("上传任务不属于当前资料来源。")
                    return provider.check_upload(task)
                raise ValueError("不支持的资料操作。")
            label = "上传资料" if action == "upload" else "保存资料" if action in {"create_manual", "edit_save"} else "读取资料"
            return self.s.jobs.submit(label, operation)
        if method.startswith("enterprise."):
            if action == "status":
                from core.enterprise_chat_audit import pending_status
                state = self.enterprise.store.connection_status()
                return {**state, "chat_audit_sync": pending_status(self.enterprise.store, state)} if state.get("member") else state
            if action == "capabilities":
                from core.enterprise_capabilities import EnterpriseCapabilities
                return EnterpriseCapabilities(self.enterprise).inventory()
            if action == "capabilityCredential":
                from core.enterprise_capabilities import EnterpriseCapabilities
                result = EnterpriseCapabilities(self.enterprise).set_credential(p.get("selection_key"), p.get("value"))
                self.s.publish({"event": "enterprise_changed"})
                return result
            def operation(cancelled, progress):
                if action == "bind":
                    self.enterprise.bind(p.get("connection"), use_package=p.get("use_package") is True,
                                         confirm_switch=p.get("confirm_switch") is True)
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
                    try:
                        self.enterprise.logout()
                    finally:
                        self.s.publish({"event": "enterprise_changed"})
                        self.s.publish({"event": "settings_changed"})
                if action in {"bind", "login", "refresh"}:
                    self.s.publish({"event": "enterprise_changed"})
                    self.s.publish({"event": "settings_changed"})
                return self.enterprise.store.connection_status()  # never persist bearer tokens in job results
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
                    progress=lambda amount, total: progress("正在上传…", transferred=amount, total=total))
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
                from core.sidebar_projects import project_paths
                binding = client.local_store.bind_workspace(client.identity, space, p["directory"],
                    [folder for project in self.s.config.get_projects() for folder in project_paths(project)])
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
