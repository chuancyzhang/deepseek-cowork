import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.remote_skill_installer import (
    RemoteSkillInstallService,
    _request_urls,
    _redact_untrusted_text,
    run_remote_skill_installer_agent,
    validate_public_https_url,
)


ENTRY_TEXT = """# Wind installer
必需 skill:
- wind-find-finance-skill
- wind-mcp-skill
GitHub:
npx skills add Wind-Information-Co-Ltd/wind-skills --skill wind-mcp-skill -y
"""


class FakeRunner:
    def analyze_entry(self, _entry_url, _entry_text, _request):
        return {
            "repository_candidates": [
                {
                    "url": "https://github.com/Wind-Information-Co-Ltd/wind-skills.git",
                    "evidence": {"line_start": 6, "line_end": 6},
                }
            ],
            "required_skills": [
                {
                    "name": "wind-find-finance-skill",
                    "path_hint": "skills/wind-find-finance-skill",
                    "confidence": "high",
                    "evidence": {"line_start": 3, "line_end": 3},
                },
                {
                    "name": "wind-mcp-skill",
                    "path_hint": "skills/wind-mcp-skill",
                    "confidence": "high",
                    "evidence": {"line_start": 4, "line_end": 4},
                },
            ],
            "ambiguities": [],
            "risks": [],
        }

    def analyze_package(self, _package_payload):
        return {
            "config_candidates": [
                {
                    "skill_name": "wind-mcp-skill",
                    "name": "WIND_API_KEY",
                    "label": "Wind API Key",
                    "kind": "secret",
                    "required": True,
                    "env": "WIND_API_KEY",
                    "help": "仅在运行时注入。",
                    "placeholder": "请输入 Wind API Key",
                    "default": "",
                    "options": [],
                    "action_label": "获取 API Key",
                    "action_url": "https://aifinmarket.wind.com.cn/#/user/overview",
                    "confidence": "high",
                    "evidence": [{"file": "scripts/cli.mjs", "line": 1}],
                }
            ],
            "risks": ["network"],
            "ambiguities": [],
        }


class LowConfidenceRunner(FakeRunner):
    def analyze_package(self, package_payload):
        payload = super().analyze_package(package_payload)
        payload["config_candidates"][0]["confidence"] = "low"
        return payload


class NoisyOutOfScopeRunner(FakeRunner):
    def analyze_entry(self, entry_url, entry_text, request):
        payload = super().analyze_entry(entry_url, entry_text, request)
        payload["ambiguities"] = [
            "入口没有提供 commit SHA。",
            "入口没有提供 Skill 在仓库中的实际目录。",
            "用户没有选择项目或全局安装范围。",
        ]
        return payload

    def analyze_package(self, package_payload):
        payload = super().analyze_package(package_payload)
        payload["ambiguities"] = ["模型不确定是否应生成 needs_confirmation。"]
        return payload


class ConfigStub:
    def __init__(self):
        self.enabled = {}

    def is_skill_enabled(self, name, default=True):
        return self.enabled.get(name, default)

    def set_skill_enabled(self, name, enabled):
        self.enabled[name] = bool(enabled)


class TestRemoteSkillInstaller(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.fixture_repo = os.path.join(self.temp_dir, "fixture-repo")
        self.events = []
        self.config = ConfigStub()
        self.context = {
            "session_id": "session-a",
            "skill_change_publisher": self.events.append,
            "config_manager": self.config,
        }
        self._write_fixture_skill(
            "wind-find-finance-skill",
            "---\nname: wind-find-finance-skill\ndescription: Find finance skills.\n---\n# Finder\n",
            "",
        )
        self._write_fixture_skill(
            "wind-mcp-skill",
            "---\nname: wind-mcp-skill\ndescription: Query Wind data.\n---\n# Wind MCP\n",
            (
                "const key = process.env.WIND_API_KEY;\n"
                "const portal = 'https://aifinmarket.wind.com.cn/#/user/overview';\n"
                "fetch('https://mcp.wind.com.cn/data');\n"
            ),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_fixture_skill(self, name, skill_md, script):
        skill_dir = os.path.join(self.fixture_repo, "skills", name)
        os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write(skill_md)
        if script:
            with open(os.path.join(skill_dir, "scripts", "cli.mjs"), "w", encoding="utf-8") as handle:
                handle.write(script)

    def _clone_fixture(self, _service, _candidates, destination):
        shutil.copytree(self.fixture_repo, destination)
        return (
            "https://github.com/Wind-Information-Co-Ltd/wind-skills.git",
            "a" * 40,
            [],
        )

    def _inspect(self):
        service = RemoteSkillInstallService(
            self.temp_dir,
            context=self.context,
            runner=FakeRunner(),
        )
        with patch(
            "core.remote_skill_installer.fetch_markdown_entry",
            return_value={
                "url": "https://aifinmarket.wind.com.cn/skill.md",
                "text": ENTRY_TEXT,
            },
        ), patch.object(
            service,
            "_clone_candidates",
            side_effect=lambda candidates, destination: self._clone_fixture(service, candidates, destination),
        ):
            result = service.inspect(
                "阅读 https://aifinmarket.wind.com.cn/skill.md 安装万得金融能力"
            )
        return service, result

    def test_inspect_returns_fixed_preview_and_generated_secret_field(self):
        _service, result = self._inspect()
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(
            result["preview"]["skills"],
            ["wind-find-finance-skill", "wind-mcp-skill"],
        )
        field = result["preview"]["config_fields"]["wind-mcp-skill"][0]
        self.assertEqual(field["env"], "WIND_API_KEY")
        self.assertEqual(field["kind"], "secret")
        self.assertTrue(field["required"])
        self.assertNotIn("evidence", field)
        plan_path = os.path.join(
            self.temp_dir,
            "skill_install_plans",
            result["continuation_id"],
            "plan.json",
        )
        with open(plan_path, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
        self.assertEqual(plan["commit"], "a" * 40)
        self.assertFalse(plan["consumed"])

    def test_kernel_resolves_commit_and_paths_instead_of_blocking_on_agent_noise(self):
        service = RemoteSkillInstallService(
            self.temp_dir,
            context=self.context,
            runner=NoisyOutOfScopeRunner(),
        )
        with patch(
            "core.remote_skill_installer.fetch_markdown_entry",
            return_value={
                "url": "https://aifinmarket.wind.com.cn/skill.md",
                "text": ENTRY_TEXT,
            },
        ), patch.object(
            service,
            "_clone_candidates",
            side_effect=lambda candidates, destination: self._clone_fixture(service, candidates, destination),
        ):
            result = service.inspect(
                "阅读 https://aifinmarket.wind.com.cn/skill.md 安装万得金融能力"
            )

        self.assertEqual(result["status"], "needs_confirmation")
        self.assertRegex(result["continuation_id"], r"^install_[a-f0-9]{32}$")
        self.assertEqual(result["preview"]["source"]["commit"], "a" * 40)
        self.assertEqual(
            result["preview"]["skills"],
            ["wind-find-finance-skill", "wind-mcp-skill"],
        )

    def test_confirm_installs_same_snapshot_and_publishes_once(self):
        service, preview = self._inspect()
        result = service.install(preview["continuation_id"])
        self.assertEqual(result["status"], "installed")
        self.assertEqual(len(self.events), 1)
        self.assertEqual(
            self.events[0]["skill_names"],
            ["wind-find-finance-skill", "wind-mcp-skill"],
        )
        target = os.path.join(self.temp_dir, "ai_skills", "wind-mcp-skill")
        with open(os.path.join(target, "skill.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["config_fields"][0]["env"], "WIND_API_KEY")
        self.assertEqual(payload["remote_source"]["commit"], "a" * 40)
        self.assertEqual(
            result["configuration_targets"],
            [{"skill_name": "wind-mcp-skill", "missing_required": ["WIND_API_KEY"]}],
        )
        with self.assertRaisesRegex(ValueError, "已经使用"):
            service.install(preview["continuation_id"])

    def test_plan_is_bound_to_session_and_detects_snapshot_tampering(self):
        service, preview = self._inspect()
        other = RemoteSkillInstallService(
            self.temp_dir,
            context={**self.context, "session_id": "session-b"},
            runner=FakeRunner(),
        )
        with self.assertRaisesRegex(ValueError, "不属于当前会话"):
            other.install(preview["continuation_id"])

        source_script = os.path.join(
            self.temp_dir,
            "skill_install_plans",
            preview["continuation_id"],
            "source",
            "skills",
            "wind-mcp-skill",
            "scripts",
            "cli.mjs",
        )
        with open(source_script, "a", encoding="utf-8") as handle:
            handle.write("// tampered\n")
        with self.assertRaisesRegex(ValueError, "快照已发生变化"):
            service.install(preview["continuation_id"])

    def test_expired_plan_cannot_install(self):
        service, preview = self._inspect()
        plan_path = os.path.join(
            self.temp_dir,
            "skill_install_plans",
            preview["continuation_id"],
            "plan.json",
        )
        with open(plan_path, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
        plan["expires_at"] = 1
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump(plan, handle)
        with self.assertRaisesRegex(ValueError, "已过期"):
            service.install(preview["continuation_id"])

    def test_public_state_machine_requires_a_valid_continuation(self):
        result = run_remote_skill_installer_agent(
            continuation_id="install_" + "f" * 32,
            decision="confirm",
            app_data_dir=self.temp_dir,
            context=self.context,
            runner=FakeRunner(),
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("不存在或已过期", result["error"])

        invalid_decision = run_remote_skill_installer_agent(
            request="https://example.com/skill.md",
            decision="yes",
            app_data_dir=self.temp_dir,
            context=self.context,
            runner=FakeRunner(),
        )
        self.assertEqual(invalid_decision["status"], "error")
        self.assertIn("decision", invalid_decision["error"])

    def test_initial_request_url_stops_before_chinese_explanation(self):
        urls = _request_urls(
            "请安装：https://aifinmarket.wind.com.cn/skill.md。用户已选择官方 GitHub"
        )
        self.assertEqual(urls, ["https://aifinmarket.wind.com.cn/skill.md"])

    def test_rephrased_inspection_loop_is_stopped_after_two_attempts(self):
        prior_calls = []
        for index in range(3):
            prior_calls.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "type": "function",
                            "function": {
                                "name": "remote_skill_installer_agent",
                                "arguments": json.dumps(
                                    {
                                        "request": (
                                            "阅读 https://aifinmarket.wind.com.cn/skill.md "
                                            f"安装万得金融能力，第 {index + 1} 次改写"
                                        ),
                                        "continuation_id": "",
                                        "decision": "",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            )
        result = run_remote_skill_installer_agent(
            request="阅读 https://aifinmarket.wind.com.cn/skill.md 安装万得金融能力",
            app_data_dir=self.temp_dir,
            context={
                **self.context,
                "current_messages_snapshot": [
                    {"role": "user", "content": "安装万得金融能力"},
                    *prior_calls,
                ],
            },
            runner=FakeRunner(),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "inspection_retry_limit")

    def test_existing_target_blocks_entire_install(self):
        service, preview = self._inspect()
        existing = os.path.join(self.temp_dir, "ai_skills", "wind-find-finance-skill")
        os.makedirs(existing)
        with self.assertRaisesRegex(ValueError, "不会覆盖"):
            service.install(preview["continuation_id"])
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "ai_skills", "wind-mcp-skill")))
        self.assertEqual(self.events, [])

    def test_publish_failure_rolls_back_all_installed_skills(self):
        service, preview = self._inspect()
        service.context["skill_change_publisher"] = (
            lambda _event: (_ for _ in ()).throw(RuntimeError("catalog refresh failed"))
        )
        with self.assertRaisesRegex(RuntimeError, "catalog refresh failed"):
            service.install(preview["continuation_id"])
        self.assertFalse(
            os.path.exists(os.path.join(self.temp_dir, "ai_skills", "wind-find-finance-skill"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.temp_dir, "ai_skills", "wind-mcp-skill"))
        )

    def test_private_urls_and_secret_literals_are_rejected_or_redacted(self):
        with self.assertRaisesRegex(ValueError, "非公网"):
            validate_public_https_url("https://127.0.0.1/skill.md")
        with self.assertRaisesRegex(ValueError, "用户名或密码"):
            validate_public_https_url("https://user:secret@example.com/skill.md")
        redacted = _redact_untrusted_text(
            "api_key=super-secret\nAuthorization: Bearer abcdefghijklmnop"
        )
        self.assertNotIn("super-secret", redacted)
        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertIn("<redacted-secret>", redacted)

    def test_low_confidence_config_is_previewed_but_not_installed_by_default(self):
        service = RemoteSkillInstallService(
            self.temp_dir,
            context=self.context,
            runner=LowConfidenceRunner(),
        )
        with patch(
            "core.remote_skill_installer.fetch_markdown_entry",
            return_value={
                "url": "https://aifinmarket.wind.com.cn/skill.md",
                "text": ENTRY_TEXT,
            },
        ), patch.object(
            service,
            "_clone_candidates",
            side_effect=lambda candidates, destination: self._clone_fixture(service, candidates, destination),
        ):
            result = service.inspect(
                "阅读 https://aifinmarket.wind.com.cn/skill.md 安装万得金融能力"
            )
        self.assertNotIn("wind-mcp-skill", result["preview"]["config_fields"])
        self.assertEqual(
            result["preview"]["low_confidence_config_fields"]["wind-mcp-skill"][0]["env"],
            "WIND_API_KEY",
        )


if __name__ == "__main__":
    unittest.main()
