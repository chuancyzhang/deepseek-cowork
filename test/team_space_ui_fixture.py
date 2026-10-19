"""Disposable browser fixture, isolated from every real enterprise/account."""
from contextlib import asynccontextmanager
from pathlib import Path
import hashlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from fastapi.responses import RedirectResponse
import httpx
import uvicorn
from web.platform.server.app import create_app
from web.platform.server.config import Settings
from web.platform.server.database import Member


def main():
    with tempfile.TemporaryDirectory(prefix="cowork-space-web-") as directory:
        settings = Settings(database_url="sqlite:///" + str(Path(directory) / "fixture.sqlite3"), data_dir=directory,
            platform_id="space-ui-fixture", platform_name="示例企业", public_url="http://127.0.0.1:8791",
            encryption_key=Fernet.generate_key().decode(), development=True)
        app = create_app(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
        original = app.router.lifespan_context
        people = {name: uuid.uuid4().hex for name in ("owner", "member", "admin")}
        @asynccontextmanager
        async def lifespan(application):
            async with original(application):
                with app.state.database.transaction() as db:
                    for kind, name in [("owner", "王晨"), ("member", "李明"), ("admin", "陈宁")]:
                        db.add(Member(id=people[kind], name=name, email=kind+"@example.invalid", provider_id="fixture", subject=kind,
                                      role="admin" if kind == "owner" else "member"))
                service = app.state.team_spaces
                def command(person, action, **payload):
                    return service.execute(people[person], app.state.space_id if hasattr(app.state, "space_id") else "", action,
                                           {**payload, "operation_id": uuid.uuid4().hex})
                app.state.space_id = command("owner", "space.create", name="客户方案协作", description="把客户需求、任务分工和可交付成果放在同一处。每位成员在自己的电脑执行，主动分享选定内容。" )["id"]
                for person in ("member", "admin"):
                    invite = command("owner", "invite.create", member_id=people[person], role="admin" if person == "admin" else "member")
                    command(person, "invite.respond", id=invite["id"], revision=1, response="accept")
                command("owner", "space.update", revision=1, allow_personal=True)
                content = "客户需求：整理目标用户、产品范围与验收要求。".encode("utf-8")
                upload = command("owner", "upload.prepare", filename="客户需求.txt", size=len(content), sha256=hashlib.sha256(content).hexdigest())
                async def chunks():
                    yield content
                await service.receive(people["owner"], app.state.space_id, upload["id"], chunks())
                asset = command("owner", "asset.publish", upload_id=upload["id"], description="客户提供的原始需求，计划使用固定版本。")
                refs = [{"asset_id": asset["id"], "revision_id": asset["revision_id"]}]
                plan = command("owner", "plan.create", title="整理客户需求与验收标准", description="根据共享需求形成清单，明确范围内外事项。", assignee_id=people["member"], priority="high", inputs=refs)
                command("member", "plan.status", id=plan["id"], revision=1, state="in_progress")
                command("owner", "plan.create", title="撰写方案初稿", description="基于需求清单形成方案，附上关键假设。", assignee_id=people["admin"])
                command("owner", "plan.create", title="检查交付范围", assignee_id=people["owner"])
                share = command("member", "share.publish", title="需求分析 · 第一轮", body="已整理三类核心需求，并标出两项待客户确认的问题。\n\n本次只发布选定的摘要与需求文件；完整工作对话保存在本机。", plan_id=plan["id"], assets=refs, source_state="stopped", source_ref="fixture-task")
                command("owner", "comment.create", target_kind="space", target_id=app.state.space_id, body="本周先完成需求澄清，再开始方案初稿。资料有变更时请发布新版本，保留旧计划的依据。")
                command("admin", "comment.create", target_kind="plan", target_id=plan["id"], body="请补充上线范围的例外情况，便于后续方案引用。")
                yield
        app.router.lifespan_context = lifespan
        @app.get("/qa/login/{person}")
        def login(person: str):
            session = app.state.auth.session(people[person], "web")
            response = RedirectResponse("/app/team-spaces", status_code=303)
            response.set_cookie("cowork_session", session["access_token"], httponly=True, samesite="lax")
            response.set_cookie("cowork_csrf", session["csrf_token"], samesite="lax")
            return response
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8791, access_log=False, log_level="warning"))
        @app.post("/qa/stop")
        def stop():
            server.should_exit = True
            return {"status": "stopping"}
        server.run()


if __name__ == "__main__":
    main()
