---
name: feishu-docs
description: Configuration preset for Feishu document skills.
description_cn: 飞书文档能力配置预设，用于保存并注入文档操作所需凭据。
license: Apache-2.0
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
security_level: medium
---

# Feishu Docs

This bundled preset stores Feishu application credentials for imported or script-based document skills.

## Usage
- Configure App ID and App Secret in the Cowork skill workbench.
- Script-based skills can read the configured values from the declared environment variables.
- Tenant or user access tokens can be supplied when a skill expects pre-issued tokens.
