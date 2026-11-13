"""Expert continuity, main-message attachments and guarded final previews."""
import copy
import unittest


from core.conversation_agents import agent_role_context, builtin_agent_profiles, extract_agent_mentions, message_agent_identity
from core.conversation_integrity import canonical_ledger_message


class AgentPolicyTests(unittest.TestCase):
    def test_registry_is_independent_and_resolves_collisions(self):
        custom = [{"id": "a", "name": "HTML创作"}, {"id": "b", "name": "HTML创作（内置）"}]
        original = copy.deepcopy(custom)
        profiles = builtin_agent_profiles(custom)
        self.assertEqual(profiles[0]["name"], "HTML创作（内置 2）")
        profiles[0]["skill_names"].append("changed")
        self.assertEqual(builtin_agent_profiles()[0]["skill_names"], [])
        self.assertEqual(custom, original)

    def test_mentions_preserve_paragraphs_code_and_email(self):
        profile = builtin_agent_profiles()[0]
        body = "第一段\n\n- a  b\n- c\n```html\n<p>@HTML创作</p>\n```\n`@HTML创作` a@HTML创作"
        found, cleaned = extract_agent_mentions("@HTML创作 " + body, [profile])
        self.assertEqual(found, [profile])
        self.assertEqual(cleaned, body)

    def test_runtime_role_reset_and_output_path(self):
        profile = builtin_agent_profiles()[0]
        self.assertEqual(agent_role_context(None, None), {})
        self.assertIn("普通助手", agent_role_context({}, None)["agent_system_prompt"])
        context = agent_role_context(profile, profile, workspace="D:/work", message_id="a" * 32)
        self.assertIn("html-aaaaaaaaaaaa.html", context["agent_system_prompt"])
        self.assertEqual(context["agent_summon_source"], "conversation")
        with self.assertRaises(ValueError):
            agent_role_context(profile, None)

    def test_history_names_are_frozen_and_guidance_does_not_change_identity(self):
        old = {"id": "a", "name": "旧名字"}
        messages = [{"id": "u", "role": "user", "meta": {"conversation_agent": old, "request_id": "r"}},
                    {"id": "g", "role": "user", "meta": {"same_turn_guidance": True}},
                    {"id": "context", "role": "user", "meta": {"kind": "runtime_context", "request_id": "r"}},
                    {"id": "a", "role": "assistant", "meta": {"request_id": "r"}}]
        self.assertEqual(message_agent_identity(messages, messages[-1]), old)
        messages[-1]["meta"]["conversation_agent"] = {}
        self.assertEqual(message_agent_identity(messages, messages[-1]), {})

    def test_reply_identity_is_only_a_ui_projection(self):
        message = {"id": "a", "role": "assistant", "content": "结果"}
        projected = copy.deepcopy(message)
        projected["meta"] = {"ui_conversation_agent": {"id": "expert", "name": "专家"}}
        self.assertEqual(canonical_ledger_message(message), canonical_ledger_message(projected))






if __name__ == "__main__":
    unittest.main()
