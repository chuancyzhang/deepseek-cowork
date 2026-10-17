"""Cowork platform HTTP application. Start via the documented server module."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import html
import json
import logging
from pathlib import Path
import time
import uuid
from urllib.parse import parse_qs

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx
from sqlalchemy import delete, select, update

from .auth import Authentication, oauth_compatibility_preset
from .config import Settings
from .database import (AuditEvent, AuthSource, ClientRelease, Database, LoginSession, LoginTransaction,
                       Member, Model, Platform, Question)
from .management import Management, member_view, model_view
from .releases import Releases, public_release
from .security import PlatformError, Vault, digest

log = logging.getLogger(__name__)
UI = Path(__file__).resolve().parent.parent / "ui"
PROTOCOL_PATHS = {"chat_completions": "chat/completions", "responses": "responses", "anthropic": "v1/messages"}


async def read_json(request, maximum=1024 * 1024):
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise PlatformError("json_required", "此接口需要 JSON 请求。", 415)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > maximum:
            raise PlatformError("request_too_large", "请求内容超过允许大小。", 413)
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError()
        return payload
    except (ValueError, UnicodeDecodeError):
        raise PlatformError("invalid_json", "请求内容不是有效的 JSON 对象。") from None


def create_app(settings=None, *, transport=None):
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = Database(settings)
    vault = Vault(settings.encryption_key)
    management = Management(settings, database, vault)
    releases = Releases(settings, database)
    cleanup_state = {"last_success_at": None, "deleted": 0, "error": ""}
    jobs = set()

    def cleanup():
        now = time.time()
        with database.transaction() as db:
            count = db.execute(delete(Question).where(Question.expires_at <= now)).rowcount
            db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
            db.execute(delete(LoginTransaction).where(LoginTransaction.expires_at <= now))
            if count:
                database.audit(db, "questions.expired", target=str(count))
        cleanup_state.update(last_success_at=now, deleted=count, error="")
        return count

    async def cleanup_loop():
        while True:
            await asyncio.sleep(settings.cleanup_seconds)
            try:
                await asyncio.to_thread(cleanup)
            except Exception as exc:
                cleanup_state["error"] = "到期清理未完成，后台将重试。"
                log.error("questions.cleanup_failed reason=%s", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(app):
        database.migrate(settings)
        # Interrupted processing cannot be inferred complete from temporary files.
        with database.transaction() as db:
            db.execute(update(ClientRelease).where(ClientRelease.state == "processing")
                       .values(state="failed", error="服务中断，处理未完成；可重新处理完整源文件。"))
            db.execute(update(ClientRelease).where(ClientRelease.state == "draft", ClientRelease.stage == "receive")
                       .values(state="failed", error="上传中断，请重新上传。"))
        cleanup()
        async with httpx.AsyncClient(transport=transport, follow_redirects=False,
                timeout=httpx.Timeout(connect=15, read=settings.model_read_timeout, write=60, pool=15),
                limits=httpx.Limits(max_connections=settings.max_model_concurrency + 20, max_keepalive_connections=20)) as client:
            app.state.http = client
            app.state.auth = Authentication(settings, database, vault, client)
            app.state.model_slots = asyncio.Semaphore(settings.max_model_concurrency)
            cleaner = asyncio.create_task(cleanup_loop())
            try:
                yield
            finally:
                cleaner.cancel()
                await asyncio.gather(cleaner, return_exceptions=True)
                with database.transaction() as db:
                    db.execute(update(ClientRelease).where(ClientRelease.state == "processing")
                               .values(state="failed", error="服务正在停止，处理未完成。"))
                if jobs:
                    await asyncio.gather(*jobs, return_exceptions=True)
        database.close()

    app = FastAPI(title="Cowork 企业平台", version="1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None, root_path=settings.path_prefix)
    app.state.settings, app.state.database, app.state.vault = settings, database, vault
    app.state.management, app.state.releases, app.state.cleanup = management, releases, cleanup

    def principal(request, *, admin=False):
        authorization = request.headers.get("authorization", "")
        bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        # Anthropic SDK uses x-api-key for the Cowork session on the local gateway.
        bearer = bearer or (request.headers.get("x-api-key", "") if "/gateway/models/" in request.url.path else "")
        raw = bearer or request.cookies.get("cowork_session", "")
        user = management.principal(raw, cookie=not bool(bearer),
                                    csrf=request.headers.get("x-csrf-token"), writing=request.method not in {"GET", "HEAD"})
        return management.administrator(user) if admin else user

    def set_session(response, session):
        options = dict(secure=settings.secure_cookie, samesite="lax", path=settings.path_prefix or "/",
                       max_age=settings.session_seconds)
        response.set_cookie("cowork_session", session["access_token"], httponly=True, **options)
        response.set_cookie("cowork_csrf", session["csrf_token"], httponly=False, **options)
        return response

    @app.exception_handler(PlatformError)
    async def platform_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "type": exc.code, "message": exc.message},
                             "request_id": getattr(request.state, "request_id", "")}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.error("request.failed request=%s reason=%s", getattr(request.state, "request_id", ""), type(exc).__name__)
        return JSONResponse({"error": {"code": "server_error", "message": "服务器未完成此操作；内容未被确认保存，请重试或联系管理员。"},
                             "request_id": getattr(request.state, "request_id", "")}, status_code=500)

    @app.middleware("http")
    async def security_headers(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != settings.origin:
            return JSONResponse({"error": {"code": "origin_rejected", "message": "请求来源不匹配，请从平台页面重新操作。"}}, status_code=403)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health():
        with database.transaction() as db:
            db.get(Platform, 1)
        return {"status": "ok"}

    @app.get("/api/v1/platform-info")
    def platform_info():
        with database.transaction() as db:
            platform = db.get(Platform, 1)
            return {"platform_id": platform.platform_id, "platform_name": platform.name,
                    "config_version": platform.config_version, "supported_api_versions": [1],
                    "question_retention_days": 30, "question_scope": "enterprise_models_only"}

    @app.get("/api/v1/auth/providers")
    def providers():
        return {"items": app.state.auth.public_sources()}

    @app.post("/api/v1/auth/transactions")
    async def begin_login(request: Request):
        payload = await read_json(request, 8192)
        test_actor = principal(request, admin=True)["id"] if payload.get("test") else ""
        return await app.state.auth.begin(payload.get("provider_id"), payload.get("mode", "web"),
                                          payload.get("code_challenge", ""), test_actor=test_actor)

    @app.get("/auth/start/{transaction_id}")
    def launch_login(transaction_id: str):
        url, browser_token = app.state.auth.launch(transaction_id)
        response = RedirectResponse(url, status_code=302)
        response.set_cookie("cowork_login_" + transaction_id, browser_token, secure=settings.secure_cookie,
                            httponly=True, samesite="lax", path=(settings.path_prefix or "") + "/auth", max_age=600)
        return response

    @app.get("/auth/callback/{provider_id}")
    async def login_callback(provider_id: str, request: Request):
        try:
            result = await app.state.auth.callback(provider_id, request.query_params.get("state", ""),
                                                   request.query_params.get("code", ""), request.cookies,
                                                   upstream_error="error" in request.query_params)
        except PlatformError as exc:
            return HTMLResponse("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>登录未完成</title>"
                "<h1>登录未完成</h1><p>" + html.escape(exc.message) + "</p><p>客户端输入和本机历史仍保留。"
                "桌面用户请返回 Cowork 重新登录。</p><a href='" + html.escape(settings.public_url + "/app", quote=True) + "'>返回企业平台</a></html>", status_code=exc.status)
        return login_completed(result)

    def login_completed(result):
        if result["mode"] == "web":
            response = set_session(RedirectResponse(settings.public_url + "/app", status_code=303), result)
        else:
            message = "认证配置已验证，可返回管理页面启用。" if result["mode"] == "test" else "登录已完成，请返回 Cowork 客户端。"
            response = HTMLResponse("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>登录完成</title><p>" + message + "</p></html>")
        response.delete_cookie("cowork_login_" + result["transaction_id"], path=(settings.path_prefix or "") + "/auth")
        return response

    def directory_form(transaction_id, cookies, *, username="", error="", status=200):
        transaction = app.state.auth.directory_transaction(transaction_id, cookies)
        action = html.escape(settings.public_url + "/auth/directory/" + transaction_id, quote=True)
        stylesheet = html.escape(settings.path_prefix + "/assets/platform.css", quote=True)
        return HTMLResponse("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>企业账号登录</title><link rel='stylesheet' href='" + stylesheet + "'><main><section class='login'><h1>登录"
            + html.escape(settings.platform_name) + "</h1><p>请输入企业账号密码，登录后返回 Cowork。</p>"
            + ("<p role='alert'>" + html.escape(error) + "</p>" if error else "")
            + "<form class='form' method='post' action='" + action + "'><input type='hidden' name='state' value='"
            + html.escape(transaction["state"], quote=True) + "'><label>账号<input name='username' autocomplete='username' required maxlength='512' value='"
            + html.escape(username, quote=True) + "'></label><label>密码<input name='password' type='password' autocomplete='current-password' required maxlength='4096'></label>"
            "<button class='primary'>登录</button></form></section></main></html>", status_code=status)

    @app.get("/auth/directory/{transaction_id}")
    def directory_login_page(transaction_id: str, request: Request):
        return directory_form(transaction_id, request.cookies)

    @app.post("/auth/directory/{transaction_id}")
    async def directory_login_submit(transaction_id: str, request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            raise PlatformError("form_required", "请通过登录页面提交。", 415)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 32768:
                raise PlatformError("request_too_large", "登录内容过长。", 413)
        try:
            fields = parse_qs(data.decode("utf-8"), max_num_fields=3)
            if any(len(v) != 1 for v in fields.values()):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            raise PlatformError("invalid_login", "登录表单无效，请重新打开。") from None
        transaction = app.state.auth.directory_transaction(transaction_id, request.cookies)
        username = fields.get("username", [""])[0]
        if fields.get("state", [""])[0] != transaction["state"]:
            raise PlatformError("login_binding_failed", "登录表单已失效，请在原窗口重试。", 403)
        try:
            result = await app.state.auth.callback(transaction["provider_id"], fields.get("state", [""])[0], "", request.cookies,
                                                   credentials=(username, fields.get("password", [""])[0]))
        except PlatformError as exc:
            if exc.code in {"ldap_failed", "invalid_credentials"}:
                try:
                    return directory_form(transaction_id, request.cookies, username=username, error=exc.message, status=exc.status)
                except PlatformError:
                    return HTMLResponse("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>登录未完成</title>"
                        "<h1>登录未完成</h1><p>本次登录尝试已结束。请返回客户端重新登录，或联系管理员检查目录服务。</p></html>", status_code=401)
            raise
        return login_completed(result)

    @app.post("/api/v1/auth/transactions/{transaction_id}/status")
    async def login_status(transaction_id: str, request: Request):
        payload = await read_json(request, 8192)
        return app.state.auth.desktop_status(transaction_id, payload.get("verifier", ""))

    @app.post("/api/v1/auth/transactions/{transaction_id}/exchange")
    async def exchange(transaction_id: str, request: Request):
        payload = await read_json(request, 8192)
        return app.state.auth.desktop_status(transaction_id, payload.get("verifier", ""), handoff=payload.get("handoff_code", ""))

    @app.post("/api/v1/auth/transactions/{transaction_id}/cancel")
    async def cancel_login(transaction_id: str, request: Request):
        payload = await read_json(request, 8192)
        return app.state.auth.desktop_status(transaction_id, payload.get("verifier", ""), cancel=True)

    @app.post("/api/v1/auth/ldap")
    async def ldap_login(request: Request):
        payload = await read_json(request, 16384)
        kind = payload.get("mode", "web")
        if kind not in {"web", "desktop"}:
            raise PlatformError("invalid_login", "登录方式无效。")
        actor = principal(request, admin=True)["id"] if payload.get("test") else ""
        result = await app.state.auth.ldap_login(payload.get("provider_id"), payload.get("username", ""),
                                                payload.get("password", ""), kind=kind, test_actor=actor)
        if actor:
            return result
        return set_session(JSONResponse({"status": "completed"}), result) if kind == "web" else result

    @app.get("/api/v1/me")
    def me(request: Request):
        user = principal(request)
        return {"member": user, "models": management.models(user["id"]), "platform_id": settings.platform_id}

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        user = principal(request)
        authorization = request.headers.get("authorization", "")
        raw = authorization[7:] if authorization.lower().startswith("bearer ") else request.cookies.get("cowork_session", "")
        with database.transaction() as db:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(raw)))
            database.audit(db, "logout.completed", member_id=user["id"])
        response = JSONResponse({"status": "logged_out", "upstream_logout": "not_enabled"})
        response.delete_cookie("cowork_session", path=settings.path_prefix or "/")
        response.delete_cookie("cowork_csrf", path=settings.path_prefix or "/")
        return response

    @app.get("/api/v1/admin/members")
    def members(request: Request):
        principal(request, admin=True)
        return {"items": management.members()}

    @app.put("/api/v1/admin/members/{member_id}")
    async def update_member(member_id: str, request: Request):
        actor = principal(request, admin=True)
        return management.update_member(member_id, await read_json(request, 32768), actor["id"])

    @app.get("/api/v1/admin/models")
    def models(request: Request):
        principal(request, admin=True)
        return {"items": management.models()}

    @app.post("/api/v1/admin/models")
    async def create_model(request: Request):
        actor = principal(request, admin=True)
        return management.save_model(await read_json(request, 65536), actor["id"])

    @app.put("/api/v1/admin/models/{model_id}")
    async def update_model(model_id: str, request: Request):
        actor = principal(request, admin=True)
        return management.save_model(await read_json(request, 65536), actor["id"], model_id)

    def upstream_parts(model):
        headers = {"Content-Type": "application/json"}
        if model["protocol"] == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            if model["options"].get("anthropic_auth") == "bearer":
                headers["Authorization"] = "Bearer " + model["api_key"].removeprefix("Bearer ")
            else:
                headers["x-api-key"] = model["api_key"]
        else:
            headers["Authorization"] = "Bearer " + model["api_key"].removeprefix("Bearer ")
        base = model["base_url"].rstrip("/")
        path = PROTOCOL_PATHS[model["protocol"]]
        if model["protocol"] == "anthropic" and base.endswith("/v1"):
            path = "messages"
        return base + "/" + path, headers

    @app.post("/api/v1/admin/models/{model_id}/test")
    async def test_model(model_id: str, request: Request):
        actor = principal(request, admin=True)
        with database.transaction() as db:
            model = db.get(Model, model_id)
            if not model:
                raise PlatformError("not_found", "模型不存在。", 404)
            config = {**model_view(model, admin=True), "api_key": vault.open(model.secret)["api_key"]}
        body = {"model": config["model_name"], "stream": False}
        if config["protocol"] == "responses":
            body.update(input="Reply with OK.", max_output_tokens=32)
        else:
            body.update(messages=[{"role": "user", "content": "Reply with OK."}], max_tokens=32)
        address, headers = upstream_parts(config)
        started = time.monotonic()
        try:
            response = await app.state.http.post(address, json=body, headers=headers, timeout=60)
            data = response.json()
            field = {"responses": "output", "anthropic": "content", "chat_completions": "choices"}[config["protocol"]]
            if response.status_code != 200 or not isinstance(data, dict) or data.get("error") or field not in data:
                raise ValueError()
        except (httpx.HTTPError, ValueError):
            with database.transaction() as db:
                database.audit(db, "model.test_failed", member_id=actor["id"], target=model_id, outcome="upstream_failed")
            raise PlatformError("model_test_failed", "模型实际调用未通过，请核对地址、协议、标识和密钥。", 502) from None
        with database.transaction() as db:
            changed = db.execute(update(Model).where(Model.id == model_id, Model.revision == config["revision"])
                                 .values(tested_revision=config["revision"])).rowcount
            if not changed:
                raise PlatformError("model_changed", "测试期间模型配置已改变，请重新测试。", 409)
            database.audit(db, "model.tested", member_id=actor["id"], target=model_id)
        return {"tested": True, "latency_seconds": round(time.monotonic() - started, 3)}

    @app.post("/api/v1/gateway/models/{model_id}/{path:path}")
    async def proxy(model_id: str, path: str, request: Request):
        actor = principal(request)
        model = management.authorized_model(actor["id"], model_id)
        if path != PROTOCOL_PATHS[model["protocol"]]:
            raise PlatformError("protocol_mismatch", "请求协议与企业模型配置不匹配。", 400)
        body = await read_json(request, settings.max_request_bytes)
        metadata = body.pop("_cowork", None)
        if not isinstance(metadata, dict) or metadata.get("model_revision") != model["revision"]:
            raise PlatformError("model_changed", "企业模型配置已更新，请刷新企业连接后重新发起本轮。", 409)
        if body.get("model") != model["model_name"]:
            raise PlatformError("model_mismatch", "请求模型与授权模型不一致，请刷新企业模型列表。", 409)
        if not isinstance(body.get("stream", False), bool):
            raise PlatformError("invalid_stream", "流式参数无效。")
        # No transformations of messages, system prefix, tools or option ordering.
        try:
            await asyncio.wait_for(app.state.model_slots.acquire(), timeout=0.05)
        except asyncio.TimeoutError:
            raise PlatformError("platform_busy", "企业模型当前繁忙，请稍后重试。", 429) from None
        upstream = None
        try:
            await asyncio.to_thread(management.record_questions, actor["id"], model_id, metadata)
            address, headers = upstream_parts(model)
            for key in ("anthropic-version", "anthropic-beta"):
                if model["protocol"] == "anthropic" and key in request.headers:
                    headers[key] = request.headers[key]
            headers["X-Request-ID"] = request.state.request_id
            outgoing = app.state.http.build_request("POST", address, json=body, headers=headers)
            log.info("model.start request=%s member=%s model=%s", request.state.request_id, actor["id"], model_id)
            upstream = await app.state.http.send(outgoing, stream=True)
            if upstream.status_code < 200 or upstream.status_code >= 300:
                status = upstream.status_code
                # Do not expose a vendor error which may quote credentials or user input.
                raise PlatformError("model_upstream_error", "企业模型服务拒绝或未完成请求，请联系管理员或稍后重试。",
                                    status if status in {400, 401, 403, 408, 409, 429, 500, 502, 503, 504} else 502)
        except BaseException as exc:
            if upstream is not None:
                await upstream.aclose()
            app.state.model_slots.release()
            log.warning("model.failed request=%s reason=%s", request.state.request_id, type(exc).__name__)
            if isinstance(exc, httpx.HTTPError):
                raise PlatformError("model_unavailable", "无法连接企业模型服务，输入已保留，请稍后重试。", 502) from None
            raise

        async def stream_body():
            outcome = "completed"
            try:
                async for block in upstream.aiter_bytes():
                    yield block
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception:
                outcome = "stream_failed"
                raise
            finally:
                await upstream.aclose()
                app.state.model_slots.release()
                log.info("model.%s request=%s member=%s model=%s", outcome, request.state.request_id, actor["id"], model_id)
        headers = {"X-Request-ID": request.state.request_id, "X-Accel-Buffering": "no"}
        if upstream.headers.get("x-request-id"):
            headers["X-Upstream-Request-ID"] = upstream.headers["x-request-id"][:256]
        return StreamingResponse(stream_body(), status_code=upstream.status_code,
                                 media_type=upstream.headers.get("content-type", "application/json"), headers=headers)

    @app.get("/api/v1/admin/questions")
    def questions(request: Request, member_id: str = "", keyword: str = "", since: float | None = None,
                  until: float | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        actor = principal(request, admin=True)
        result = management.questions(member_id=member_id, keyword=keyword, since=since, until=until, offset=offset, limit=limit)
        with database.transaction() as db:
            database.audit(db, "questions.queried", member_id=actor["id"])
        return result

    @app.get("/api/v1/admin/questions/{question_id}")
    def question(question_id: str, request: Request):
        actor = principal(request, admin=True)
        result = management.question(question_id)
        with database.transaction() as db:
            database.audit(db, "question.read", member_id=actor["id"], target=question_id)
        return result

    @app.get("/api/v1/admin/settings")
    def admin_settings(request: Request):
        principal(request, admin=True)
        with database.transaction() as db:
            sources = [{"id": s.id, "name": s.name, "mode": s.mode, "config": s.config, "enabled": s.enabled,
                        "revision": s.revision, "verified": s.verified_revision == s.revision,
                        "has_secret": bool(vault.open(s.secret))}
                       for s in db.scalars(select(AuthSource).order_by(AuthSource.name))]
            events = [{"id": e.id, "action": e.action, "target": e.target, "outcome": e.outcome, "created_at": e.created_at}
                      for e in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100))]
        return {"sources": sources, "cleanup": cleanup_state, "events": events,
                "platform_id": settings.platform_id, "public_url": settings.public_url,
                "session_seconds": settings.session_seconds, "refresh": "not_enabled", "global_logout": "not_enabled"}

    @app.get("/api/v1/admin/auth-preset")
    def auth_preset(request: Request):
        principal(request, admin=True)
        return {"mode": "oauth2", "config": oauth_compatibility_preset("https://identity.example.com")}

    @app.put("/api/v1/admin/auth-sources/{source_id}")
    async def save_source(source_id: str, request: Request):
        actor = principal(request, admin=True)
        payload = await read_json(request, 65536)
        payload["id"] = source_id
        return {"id": app.state.auth.save_source(payload, actor=actor["id"])}

    @app.get("/api/v1/admin/client-releases")
    def release_list(request: Request):
        principal(request, admin=True)
        with database.transaction() as db:
            return {"items": [public_release(r, admin=True) for r in db.scalars(select(ClientRelease).order_by(ClientRelease.created_at.desc()))],
                    "catalog_revision": db.get(Platform, 1).catalog_revision}

    @app.post("/api/v1/admin/client-releases")
    async def release_create(request: Request):
        actor = principal(request, admin=True)
        return releases.create(await read_json(request, 32768), actor["id"])

    @app.get("/api/v1/admin/client-releases/{release_id}")
    def release_details(release_id: str, request: Request):
        principal(request, admin=True)
        with database.transaction() as db:
            item = db.get(ClientRelease, release_id)
            if not item:
                raise PlatformError("not_found", "发布记录不存在。", 404)
            return public_release(item, admin=True)

    @app.put("/api/v1/admin/client-releases/{release_id}/source")
    async def release_upload(release_id: str, request: Request):
        actor = principal(request, admin=True)
        # Report is a separate administrator JSON request, not a large header.
        with database.transaction() as db:
            item = db.get(ClientRelease, release_id)
            if not item or not item.report:
                raise PlatformError("report_required", "请先保存检查报告。")
            report = item.report
        return await releases.upload(release_id, request.stream(), report, actor["id"])

    @app.put("/api/v1/admin/client-releases/{release_id}/report")
    async def release_report(release_id: str, request: Request):
        principal(request, admin=True)
        report = await read_json(request, 1024 * 1024)
        with database.transaction() as db:
            item = db.scalar(select(ClientRelease).where(ClientRelease.id == release_id).with_for_update())
            if not item or item.state not in {"draft", "failed"} or (item.stage == "receive" and item.state != "failed"):
                raise PlatformError("release_busy", "此发布记录当前不能更换报告。", 409)
            item.report = report
        return {"saved": True}

    @app.post("/api/v1/admin/client-releases/{release_id}/prepare", status_code=202)
    async def release_prepare_async(release_id: str, request: Request):
        actor = principal(request, admin=True)
        generation = releases.prepare(release_id, actor["id"])
        task = asyncio.create_task(asyncio.to_thread(releases.process, release_id, generation))
        jobs.add(task)
        task.add_done_callback(jobs.discard)
        return {"state": "processing"}

    @app.post("/api/v1/admin/client-releases/{release_id}/cancel")
    def release_cancel(release_id: str, request: Request):
        actor = principal(request, admin=True)
        return releases.cancel(release_id, actor["id"])

    @app.post("/api/v1/admin/client-releases/{release_id}/{action}")
    async def release_mutate(release_id: str, action: str, request: Request):
        actor = principal(request, admin=True)
        return releases.mutate(release_id, action, await read_json(request, 16384), actor["id"])

    def download(release_id, *, preview=False):
        with database.transaction() as db:
            item = db.get(ClientRelease, release_id)
            if not item or item.state not in ({"ready", "published"} if preview else {"published"}):
                raise PlatformError("release_unavailable", "此版本尚未发布或已撤下。", 404)
            path = releases.download_path(item)
            if not path.is_file() or path.stat().st_size != item.output_bytes:
                raise PlatformError("release_file_missing", "发布文件不完整，请联系管理员。", 503)
            return FileResponse(path, media_type="application/zip", filename=f"deepseek-cowork-v{item.app_version}.zip",
                                headers={"X-Content-SHA256": item.output_sha256})

    @app.get("/api/v1/admin/client-releases/{release_id}/download")
    def preview_download(release_id: str, request: Request):
        principal(request, admin=True)
        return download(release_id, preview=True)

    @app.get("/api/v1/client-releases/recommended")
    def recommended(target_os: str = "windows", arch: str = "x64"):
        with database.transaction() as db:
            item = db.scalar(select(ClientRelease).where(ClientRelease.state == "published", ClientRelease.recommended,
                                                        ClientRelease.target_os == target_os, ClientRelease.arch == arch))
            return {"release": public_release(item) if item else None}

    @app.get("/api/v1/client-releases/{release_id}/download")
    def public_download(release_id: str):
        return download(release_id)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/app", response_class=HTMLResponse)
    @app.get("/app/{route:path}", response_class=HTMLResponse)
    @app.get("/downloads", response_class=HTMLResponse)
    def page():
        return (UI / "index.html").read_text(encoding="utf-8").replace("__BASE__", html.escape(settings.path_prefix, quote=True))

    app.mount("/assets", StaticFiles(directory=str(UI), check_dir=False), name="assets")
    return app
