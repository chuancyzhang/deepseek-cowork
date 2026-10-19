"""Team-space server contracts: shared facts, isolation and recoverable writes."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import select

from web.platform.server.app import create_app
from web.platform.server.config import Settings
from web.platform.server.database import Member, Model, ModelGrant, Platform, Question
from web.platform.server.team_space_tables import SpaceActivity, SpaceInvite, SpaceSnapshot, SpaceUpload


class TeamSpaceTests(unittest.TestCase):
    def database_url(self):
        return "sqlite:///" + str(Path(self.temp.name) / "db.sqlite3")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outgoing = []
        def upstream(request):
            self.outgoing.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        self.settings = Settings(database_url=self.database_url(),
            data_dir=Path(self.temp.name), platform_id="test", platform_name="测试企业",
            public_url="http://localhost", encryption_key=Fernet.generate_key().decode(), development=True,
            max_space_file_bytes=1024, max_space_bytes=2048)
        self.app = create_app(self.settings, transport=httpx.MockTransport(upstream))
        self.client = self.enterContext(TestClient(self.app, base_url="http://localhost"))
        self.db = self.app.state.database
        with self.db.transaction() as db:
            for person in ("owner", "admin", "member", "outsider", "platform"):
                db.add(Member(id=person, name=person, provider_id="test", subject=person,
                    role="admin" if person == "platform" else "member"))
        self.tokens = {person: self.app.state.auth.session(person, "desktop")["access_token"]
            for person in ("owner", "admin", "member", "outsider", "platform")}
        self.space = self.command("owner", "space.create", name="方案", description="三人协作")["id"]

    def request(self, person, method, path, **kwargs):
        return self.client.request(method, "/api/v1" + path,
            headers={"Authorization": "Bearer " + self.tokens[person]}, **kwargs)

    def command(self, person, action, *, expected=200, space=None, **payload):
        payload.setdefault("operation_id", uuid.uuid4().hex)
        path = "/team-spaces" if action == "space.create" else f"/team-spaces/{space or self.space}/actions/{action}"
        response = self.request(person, "POST", path, json=payload)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def join(self, person="member", role="member"):
        invitation = self.command("owner", "invite.create", member_id=person, role=role)
        self.command(person, "invite.respond", id=invitation["id"], revision=1, response="accept")

    def get(self, person="owner", suffix=""):
        response = self.request(person, "GET", "/team-spaces/" + self.space + suffix)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def upload(self, content=b"shared", person="owner", filename="input.txt"):
        item = self.command(person, "upload.prepare", filename=filename, size=len(content), sha256=hashlib.sha256(content).hexdigest())
        response = self.request(person, "PUT", f"/team-spaces/{self.space}/uploads/{item['id']}/content", content=content)
        self.assertEqual(response.status_code, 200, response.text)
        return item["id"]

    def asset(self, content=b"shared", **kwargs):
        return self.command("owner", "asset.publish", upload_id=self.upload(content), **kwargs)

    def publish(self, **kwargs):
        defaults = dict(title="交付成果", body="摘要", assets=[], uploads=[], source_state="completed")
        defaults.update(kwargs)
        return self.command("owner", "share.publish", **defaults)

    def test_expired_upload_retries_and_restore_verifies_published_digest(self):
        service = self.app.state.team_spaces
        published = self.asset(b"keep")
        pending = self.upload(b"retry")
        with self.db.transaction() as db:
            db.get(SpaceUpload, pending).updated_at = time.time() - 90000
        self.assertEqual(service.cleanup_uploads(), 1)
        self.assertFalse(service.file_path(pending).exists())
        result = self.request("owner", "PUT", f"/team-spaces/{self.space}/uploads/{pending}/content", content=b"retry")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(service.verify_storage(), {"checked": 1, "failures": []})
        with self.db.transaction() as db:
            upload = db.scalar(select(SpaceUpload).where(SpaceUpload.state == "published"))
            service.file_path(upload.id).write_bytes(b"oops")
        self.assertEqual(service.verify_storage()["failures"][0]["reason"], "digest_mismatch")
        self.assertTrue(self.get(suffix="/objects/asset/" + published["id"])["revisions"])

    def test_withdrawn_fixed_inputs_block_subsequent_model_calls(self):
        self.command("owner", "space.update", revision=1, allow_personal=True)
        asset = self.asset()
        policy = {"source": "personal", "assets": [{"asset_id": asset["id"], "revision_id": asset["revision_id"]}]}
        endpoint = f"/team-spaces/{self.space}/model-policy"
        self.assertEqual(self.request("owner", "POST", endpoint, json=policy).status_code, 200)
        self.command("owner", "asset.withdraw", id=asset["id"], revision=1)
        self.assertEqual(self.request("owner", "POST", endpoint, json=policy).status_code, 409)

    def test_invalid_enums_and_reserved_filenames_are_rejected_without_write(self):
        self.command("owner", "space.update", revision=1, model_mode=[], expected=400)
        self.command("owner", "plan.create", title="无效优先级", priority=[], expected=400)
        self.command("owner", "share.publish", title="无效状态", body="草稿", source_state={}, expected=400)
        self.command("owner", "invite.create", member_id=[], expected=400)
        self.command("owner", "upload.prepare", filename="CON.txt", size=0, sha256=hashlib.sha256(b"").hexdigest(), expected=400)
        self.assertEqual(self.get(suffix="/plans")["items"], [])

    def test_asset_rename_collision_does_not_overwrite_other_file(self):
        first = self.asset(b"first")
        second = self.command("owner", "asset.publish", upload_id=self.upload(b"second", filename="second.txt"))
        self.command("owner", "asset.update", id=second["id"], revision=1, name="input.txt", expected=409)
        self.assertEqual(self.get(suffix="/objects/asset/" + second["id"])["name"], "second.txt")
        self.assertEqual(self.request("owner", "GET", f"/team-spaces/{self.space}/files/{first['revision_id']}").content, b"first")

    def test_complete_collaboration_with_manual_completion(self):
        self.join()
        asset = self.asset()
        refs = [{"asset_id": asset["id"], "revision_id": asset["revision_id"]}]
        plan = self.command("owner", "plan.create", title="方案初稿", assignee_id="member", inputs=refs)
        self.command("member", "plan.status", id=plan["id"], revision=1, state="in_progress")
        share = self.publish(plan_id=plan["id"], assets=refs)
        result = self.command("member", "plan.status", id=plan["id"], revision=2, state="completed",
            completion="方案已完成", outcomes=[{"share_id": share["id"], "snapshot_id": share["snapshot_id"]}])
        self.assertEqual(result["revision"], 3)
        self.command("member", "comment.create", target_kind="plan", target_id=plan["id"], body="有遗漏，请重新打开。")
        self.command("owner", "plan.status", id=plan["id"], revision=3, state="in_progress", reason="补充输入")
        activities = self.get(suffix="/activity")["items"]
        self.assertTrue(any(e["details"].get("previous", {}).get("completion") == "方案已完成" for e in activities))
        self.assertEqual(self.get("member", f"/objects/plan/{plan['id']}")["state"], "in_progress")

    def test_nonmembers_cannot_infer_shared_or_private_objects(self):
        asset = self.asset()
        for path in ("", "/plans", "/activity", "/assets", "/files/" + asset["revision_id"], "/objects/asset/" + asset["id"]):
            self.assertEqual(self.request("outsider", "GET", "/team-spaces/" + self.space + path).status_code, 403)
        self.assertEqual(self.request("outsider", "GET", "/team-spaces").json()["items"], [])
        self.assertEqual(self.request("platform", "GET", "/team-spaces/" + self.space).status_code, 403)
        self.assertNotIn("private_tasks", self.get())

    def test_owner_only_personal_models_and_dynamic_revocation(self):
        self.join("admin", "admin")
        self.join()
        self.command("admin", "space.update", revision=1, allow_personal=True, expected=403)
        self.command("owner", "space.update", revision=1, allow_personal=True)
        path = f"/team-spaces/{self.space}/model-policy"
        self.assertEqual(self.request("member", "POST", path, json={"source": "personal"}).status_code, 200)
        self.command("owner", "space.update", revision=2, allow_personal=False)
        self.assertEqual(self.request("member", "POST", path, json={"source": "personal"}).status_code, 403)
        self.assertEqual(self.request("member", "POST", path, json={"source": "personal", "messages": []}).status_code, 400)
        self.command("member", "plan.create", title="仍可人工协作")

    def test_invite_identity_revoke_expiry_and_duplicate_accept(self):
        invite = self.command("owner", "invite.create", member_id="member")
        self.command("outsider", "invite.respond", id=invite["id"], revision=1, response="accept", expected=403)
        self.command("owner", "invite.revoke", id=invite["id"], revision=1)
        self.command("member", "invite.respond", id=invite["id"], revision=1, response="accept", expected=409)
        invite = self.command("owner", "invite.create", member_id="member")
        with self.db.transaction() as db:
            db.get(SpaceInvite, invite["id"]).expires_at = time.time() - 1
        self.command("member", "invite.respond", id=invite["id"], revision=1, response="accept", expected=409)
        invite = self.command("owner", "invite.create", member_id="member")
        op = uuid.uuid4().hex
        args = dict(id=invite["id"], revision=1, response="accept", operation_id=op)
        first = self.command("member", "invite.respond", **args)
        self.assertEqual(first, self.command("member", "invite.respond", **args))
        self.assertEqual(len([m for m in self.get()["members"] if m["id"] == "member"]), 1)

    def test_idempotency_and_optimistic_concurrent_edits(self):
        self.join()
        operation = uuid.uuid4().hex
        plan = self.command("owner", "plan.create", title="唯一", operation_id=operation)
        self.assertEqual(plan, self.command("owner", "plan.create", title="唯一", operation_id=operation))
        self.command("owner", "plan.create", title="不同内容", operation_id=operation, expected=409)
        self.assertEqual(self.request("owner", "GET", "/team-space-operations/" + operation).json()["result"], plan)
        def edit(title):
            return self.request("owner", "POST", f"/team-spaces/{self.space}/actions/plan.update",
                json={"operation_id": uuid.uuid4().hex, "id": plan["id"], "revision": 1, "title": title}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(edit, ["A", "B"]))
        self.assertEqual(sorted(statuses), [200, 409])
        with self.db.transaction() as db:
            events = list(db.scalars(select(SpaceActivity).where(SpaceActivity.kind == "plan.create")))
        self.assertEqual(len(events), 1)

    def test_upload_validation_quota_cancel_and_no_partial_publish(self):
        item = self.command("owner", "upload.prepare", filename="a.txt", size=3, sha256=hashlib.sha256(b"abc").hexdigest())
        self.command("owner", "asset.publish", upload_id=item["id"], expected=409)
        response = self.request("owner", "PUT", f"/team-spaces/{self.space}/uploads/{item['id']}/content", content=b"bad")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.get(suffix="/assets")["items"], [])
        response = self.request("owner", "PUT", f"/team-spaces/{self.space}/uploads/{item['id']}/content", content=b"abc")
        self.assertEqual(response.status_code, 200)
        self.command("owner", "upload.cancel", id=item["id"])
        self.command("owner", "asset.publish", upload_id=item["id"], expected=409)
        self.upload(b"a" * 1024)
        self.upload(b"b" * 1024)
        self.command("owner", "upload.prepare", filename="over.txt", size=1, sha256=hashlib.sha256(b"x").hexdigest(), expected=413)
        self.command("owner", "upload.prepare", filename="../escape", size=0, sha256=hashlib.sha256(b"").hexdigest(), expected=400)

    def test_asset_revisions_and_snapshot_are_immutable(self):
        asset = self.asset(b"first")
        share = self.publish(assets=[{"asset_id": asset["id"], "revision_id": asset["revision_id"]}])
        self.command("owner", "asset.publish", upload_id=self.upload(b"second"), asset_id=asset["id"], revision=1)
        response = self.request("owner", "GET", f"/team-spaces/{self.space}/files/{asset['revision_id']}")
        self.assertEqual(response.content, b"first")
        original = self.get(suffix=f"/objects/snapshot/{share['snapshot_id']}")
        self.publish(id=share["id"], revision=1, title="更新", body="第二版")
        self.assertEqual(self.get(suffix=f"/objects/snapshot/{share['snapshot_id']}"), original)
        self.command("owner", "asset.withdraw", id=asset["id"], revision=2)
        self.assertEqual(self.request("owner", "GET", f"/team-spaces/{self.space}/files/{asset['revision_id']}").status_code, 410)

    def test_withdrawal_checks_other_references_before_removing_attachments(self):
        share = self.publish(uploads=[self.upload(b"result")])
        snapshot = self.get(suffix=f"/objects/snapshot/{share['snapshot_id']}")
        refs = snapshot["assets"]
        self.command("owner", "plan.create", title="依赖成果文件", inputs=refs)
        preview = self.get(suffix=f"/shares/{share['id']}/withdrawal-preview")
        self.assertTrue(preview["attachments"][0]["retained"])
        result = self.command("owner", "share.withdraw", id=share["id"], revision=1)
        self.assertEqual(result["retained_assets"], [refs[0]["asset_id"]])
        self.assertEqual(self.request("owner", "GET", f"/team-spaces/{self.space}/objects/snapshot/{share['snapshot_id']}").status_code, 410)
        self.assertEqual(self.request("owner", "GET", f"/team-spaces/{self.space}/files/{refs[0]['revision_id']}").status_code, 200)
        another = self.publish(uploads=[self.upload(b"private result", filename="other.txt")])
        ref = self.get(suffix=f"/objects/snapshot/{another['snapshot_id']}")["assets"][0]
        self.command("owner", "share.withdraw", id=another["id"], revision=1)
        self.assertEqual(self.request("owner", "GET", f"/team-spaces/{self.space}/files/{ref['revision_id']}").status_code, 410)

    def test_archive_retains_reading_and_policy_but_blocks_new_shared_writes(self):
        asset = self.asset()
        self.command("owner", "space.update", revision=1, allow_personal=True)
        self.command("owner", "space.archive", revision=2)
        self.command("owner", "plan.create", title="不能新建", expected=409)
        self.assertEqual(self.request("owner", "POST", f"/team-spaces/{self.space}/model-policy", json={"source": "personal"}).status_code, 200)
        self.assertEqual(self.request("owner", "GET", f"/team-spaces/{self.space}/files/{asset['revision_id']}").status_code, 200)
        self.command("owner", "space.restore", revision=3)
        self.command("owner", "plan.create", title="恢复后可新建")

    def test_removed_member_cannot_use_cached_model_permission(self):
        self.join()
        self.command("owner", "space.update", revision=1, allow_personal=True)
        self.command("owner", "member.remove", member_id="member", revision=1)
        self.assertEqual(self.request("member", "POST", f"/team-spaces/{self.space}/model-policy", json={"source": "personal"}).status_code, 403)
        self.command("member", "plan.create", title="不能写入", expected=403)

    def test_objects_cannot_cross_space_boundaries(self):
        asset = self.asset()
        other = self.command("owner", "space.create", name="其他")["id"]
        self.command("owner", "plan.create", space=other, title="不能引用", inputs=[{"asset_id": asset["id"], "revision_id": asset["revision_id"]}], expected=404)

    def test_gateway_rechecks_space_policy_and_keeps_prompt_unchanged(self):
        with self.db.transaction() as db:
            db.add(Model(id="model", name="公司模型", provider="openai", protocol="chat_completions",
                model_name="test-model", base_url="http://localhost/v1", secret=self.app.state.vault.seal({"api_key": "secret"}), enabled=True))
            db.add(ModelGrant(member_id="owner", model_id="model"))
        messages = [{"role": "system", "content": "stable"}, {"role": "user", "content": "new"}]
        payload = {"model": "test-model", "messages": messages, "_cowork": {"client_id": "client01234567890",
            "run_id": "run", "model_revision": 1, "questions": [], "team_space": {"space_id": self.space}}}
        response = self.request("owner", "POST", "/gateway/models/model/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.outgoing[0]["messages"], messages)
        self.assertNotIn("_cowork", self.outgoing[0])
        self.command("owner", "space.update", revision=1, model_mode="selected", model_ids=[])
        self.assertEqual(self.request("owner", "POST", "/gateway/models/model/chat/completions", json=payload).status_code, 403)
        del payload["_cowork"]["team_space"]
        self.assertEqual(self.request("owner", "POST", "/gateway/models/model/chat/completions", json=payload).status_code, 200)
        with self.db.transaction() as db:
            self.assertEqual(list(db.scalars(select(Question))), [])

    def test_disabled_owner_recovery_does_not_grant_content_access(self):
        self.join()
        with self.db.transaction() as db:
            db.get(Member, "owner").enabled = False
        listing = self.request("platform", "GET", "/admin/members/owner/owned-spaces")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertNotIn("description", listing.json()["items"][0])
        response = self.request("platform", "POST", f"/admin/team-spaces/{self.space}/recover-owner", json={
            "operation_id": uuid.uuid4().hex, "revision": 1, "member_id": "member", "reason": "原负责人已离职"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get("member")["owner_id"], "member")
        self.assertEqual(self.request("platform", "GET", "/team-spaces/" + self.space).status_code, 403)

    def test_migration_and_capability_advertisement(self):
        with self.db.transaction() as db:
            self.assertEqual(db.get(Platform, 1).schema_version, 2)
            db.get(Platform, 1).schema_version = 1
        self.db.migrate(self.settings)
        self.assertEqual(self.get()["name"], "方案")
        self.assertEqual(self.client.get("/api/v1/platform-info").json()["team_spaces"]["version"], 1)
        with self.db.transaction() as db:
            db.get(Platform, 1).schema_version = 99
        with self.assertRaisesRegex(ValueError, "结构版本"):
            self.db.migrate(self.settings)
        with self.db.transaction() as db:
            db.get(Platform, 1).schema_version = 2


if __name__ == "__main__":
    unittest.main()
