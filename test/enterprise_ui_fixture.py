"""Disposable localhost UI fixture. Never imported by the production application."""
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from fastapi.responses import RedirectResponse
import httpx
import uvicorn
from web.platform.server.app import create_app
from web.platform.server.config import Settings
from web.platform.server.database import AuthSource, Member, Model, ModelGrant, Question


def main():
    with tempfile.TemporaryDirectory(prefix="cowork-enterprise-ui-") as directory:
        settings = Settings(database_url="sqlite:///" + str(Path(directory) / "fixture.sqlite3"), data_dir=directory,
                            platform_id="ui-fixture", platform_name="示例企业", public_url="http://localhost:8787",
                            encryption_key=Fernet.generate_key().decode(), development=True)
        app = create_app(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})))
        original = app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            async with original(application):
                with app.state.database.transaction() as db:
                    source = AuthSource(id="company", name="企业统一认证", mode="oidc", config={"issuer": "https://id.example.invalid", "discovery_url": "https://id.example.invalid/.well-known/openid-configuration", "client_id": "cowork"},
                                        secret=app.state.vault.seal({"client_secret": "fixture-only"}), enabled=True, revision=1, verified_revision=1)
                    admin = Member(id="fixture-admin", provider_id="company", subject="admin", name="王晨", email="admin@example.invalid", role="admin")
                    staff = Member(id="fixture-member", provider_id="company", subject="employee", name="李明", email="employee@example.invalid", role="member")
                    model = Model(id="fixture-model", name="企业通用模型", provider="openai", protocol="chat_completions", model_name="example-model",
                                  base_url="https://model.example.invalid/v1", secret=app.state.vault.seal({"api_key": "fixture-only"}), options={}, enabled=True, revision=1, tested_revision=1)
                    db.add_all([source, admin, staff, model])
                    db.flush()
                    db.add_all([ModelGrant(member_id=admin.id, model_id=model.id), ModelGrant(member_id=staff.id, model_id=model.id),
                                Question(id="fixture-question", member_id=staff.id, client_id="fixture-installation", submission_id="fixture-submission", model_id=model.id,
                                         body="请整理本周项目进展，列出已完成事项和待确认问题。", received_at=time.time(), expires_at=time.time() + 30 * 86400)])
                yield
        app.router.lifespan_context = lifespan
        @app.get("/qa/login")
        def login():
            session = app.state.auth.session("fixture-admin", "web")
            response = RedirectResponse("/app", status_code=303)
            response.set_cookie("cowork_session", session["access_token"], httponly=True, samesite="lax")
            response.set_cookie("cowork_csrf", session["csrf_token"], samesite="lax")
            return response
        uvicorn.run(app, host="127.0.0.1", port=8787, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
