# Upstream Source

- Project: Tencent BrowserSkill
- Repository: https://github.com/Tencent/BrowserSkill
- Adapted workflow source: `crates/bsk-cli/skill/SKILL.md`
- Supported CLI release: `0.5.0`
- Supported Chrome/Edge extension release: `0.5.0`
- Bundled artifact manifest: `resources/browser_skill/bundle.json`
- License: MIT

Cowork keeps the upstream session, Agent Window, observe/snapshot, tab borrowing, human
handoff, and safety model while routing commands through the parameterized
`browser_skill_cli` tool instead of a general-purpose shell.
