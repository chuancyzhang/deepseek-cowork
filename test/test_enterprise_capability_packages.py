"""Enterprise ZIP lifecycle through the admin API, including atomic bundle imports."""

from io import BytesIO
import json
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from web.platform.server.app import create_app
from web.platform.server.capability_packages import MAX_ZIP_BYTES
from web.platform.server.config import Settings
from web.platform.server.database import Member


API = "/api/v1/admin/capabilities"
SKILL = "---\nname: 对账流程\ndescription: 复核经营数据\n---\n按订单核对应收。"


def archive(entries):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as zipped:
        for path, data in entries:
            zipped.writestr(path, data)
    return output.getvalue()


def expert_files(prompt="生成分析报告。", skill=SKILL):
    return [("expert.json", json.dumps({"name": "经营分析助手", "description": "经营复核"})),
            ("prompt.md", prompt), ("skills/ledger-check/SKILL.md", skill),
            ("skills/ledger-check/references/指标.md", "计算收入与成本。")]


@pytest.fixture
def platform(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'platform.sqlite3'}", data_dir=tmp_path,
                        platform_id="zip-test", platform_name="ZIP 验收", public_url="http://localhost",
                        encryption_key=Fernet.generate_key().decode(), development=True)
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        with app.state.database.transaction() as db:
            admin = Member(provider_id="test", subject="admin", name="管理员", role="admin")
            member = Member(provider_id="test", subject="member", name="员工", role="member")
            db.add_all([admin, member])
        headers = [{"Authorization": "Bearer " + app.state.auth.session(row.id, "desktop")["access_token"]}
                   for row in (admin, member)]
        yield client, *headers


def upload(client, auth, kind, content, *, item=None, revision=None):
    endpoint = f"{API}/zip/{kind}"
    headers = {**auth, "Content-Type": "application/zip"}
    if item:
        return client.put(f"{endpoint}/{item['id']}", headers=headers, content=content,
                          params={"revision": item["revision"] if revision is None else revision})
    return client.post(endpoint, headers=headers, content=content)


def action(client, admin, item, name, status=200, **fields):
    response = client.post(f"{API}/{item['id']}/{name}", headers=admin,
                           json={"revision": item["revision"], **fields})
    assert response.status_code == status, response.text
    return response.json()


def test_templates_and_single_desktop_skill_zip_stage_draft(platform):
    client, admin, member = platform
    for kind in ("skill", "expert"):
        url = f"{API}/zip-template/{kind}"
        assert client.get(url, headers=member).status_code == 403
        template = client.get(url, headers=admin)
        assert template.status_code == 200
        assert f"cowork-{kind}-template.zip" in template.headers["Content-Disposition"]
        assert upload(client, member, kind, template.content).status_code == 403
        staged = upload(client, admin, kind, template.content)
        assert staged.status_code == 201, staged.text
        assert staged.json()["version"] == 0
    assert client.get(f"{API}/zip-template/mcp", headers=admin).status_code == 404
    folded = SKILL.replace("description: 复核经营数据", "description: >-\n  复核经营数据。\n  包含订单与应收。")
    package = archive([("ledger-check/SKILL.md", folded), ("ledger-check/skill.json", '{"kind":"knowledge"}'),
                       ("ledger-check/scripts/test.py", "raise RuntimeError('must not execute')"),
                       ("ledger-check/README.md", "说明")])
    item = upload(client, admin, "skill", package)
    assert item.status_code == 201, item.text
    saved = client.get(f"{API}/{item.json()['id']}", headers=admin).json()
    assert saved["name"] == "对账流程" and not saved["tested"]
    assert saved["description"] == "复核经营数据。 包含订单与应收。"
    assert saved["config"]["instructions"] == "按订单核对应收。"
    assert saved["config"]["files"] == {"references/README.md": "说明", "scripts/test.py": "raise RuntimeError('must not execute')"}
    action(client, admin, saved, "test")


def test_expert_bundle_keeps_skill_identity_and_published_versions(platform):
    client, admin, member = platform
    response = upload(client, admin, "expert", archive(("ledger-expert/" + path, content)
                                                      for path, content in expert_files()))
    assert response.status_code == 201, response.text
    expert = response.json()
    [skill_ref] = expert["bundled_skills"]
    skill_path = f"{API}/{skill_ref['id']}"
    skill = client.get(skill_path, headers=admin).json()
    expert_config = client.get(f"{API}/{expert['id']}", headers=admin).json()["config"]
    assert expert_config["skill_ids"] == [skill["id"]]
    assert expert_config["instructions"] == "生成分析报告。"
    action(client, admin, expert, "test", status=400)
    skill = action(client, admin, action(client, admin, skill, "test"), "publish")
    expert = action(client, admin, action(client, admin, expert, "test"), "publish")
    expert = action(client, admin, expert, "grant", all_members=True)
    assert not client.get("/api/v1/capabilities", headers=member).json()["items"][0]["available"]
    action(client, admin, skill, "grant", all_members=True)

    replacement = archive(expert_files("生成新版分析。", SKILL.replace("应收", "应付")))
    conflict = upload(client, admin, "expert", replacement, item=expert, revision=expert["revision"] - 1)
    assert conflict.status_code == 409, conflict.text
    assert client.get(skill_path, headers=admin).json()["config"]["instructions"] == "按订单核对应收。"
    updated = upload(client, admin, "expert", replacement, item=expert)
    assert updated.status_code == 200, updated.text
    [updated_skill] = updated.json()["bundled_skills"]
    assert updated_skill["id"] == skill["id"]
    assert client.get(skill_path, headers=admin).json()["config"]["instructions"] == "按订单核对应付。"
    visible = {item["kind"]: item for item in client.get("/api/v1/capabilities", headers=member).json()["items"]}
    assert visible["expert"]["config"]["instructions"] == "生成分析报告。"
    assert visible["skill"]["config"]["instructions"] == "按订单核对应收。"


def test_invalid_archive_or_child_definition_leaves_no_drafts(platform):
    client, admin, _ = platform
    symlink = ZipInfo("references/link.md")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    for entry, status in (("../outside.md", 400), ("references/DATA.md", 400),
                          ("references/../SKILL.md", 400), (symlink, 400), ("assets/private.pem", 400),
                          ("assets/CONIN$", 400), ("unknown.py", 400)):
        parts = [("SKILL.md", SKILL), ("references/data.md", "测试背景资料"), (entry, "拒绝此条目")]
        assert upload(client, admin, "skill", archive(parts)).status_code == status
    overlarge = archive([("SKILL.md", SKILL), ("references/huge.txt", "x" * MAX_ZIP_BYTES)])
    assert upload(client, admin, "skill", overlarge).status_code == 413
    malformed_child = expert_files() + [("skills/zz-invalid/SKILL.md", SKILL.replace("对账流程", "名称" * 75))]
    assert upload(client, admin, "expert", archive(malformed_child)).status_code == 400
    no_skill = upload(client, admin, "expert", archive(expert_files()[:2]))
    assert no_skill.status_code == 400 and "至少一个 Skill" in no_skill.json()["error"]["message"]
    assert client.get(API, headers=admin).json() == {"items": []}
