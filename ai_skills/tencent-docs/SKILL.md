---
name: tencent-docs
description: Configuration preset for Tencent Docs document skills.
description_cn: 腾讯文档能力配置预设，用于保存并注入文档操作所需凭据。
license: Apache-2.0
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
security_level: medium
---

# Tencent Docs

This bundled preset stores Tencent Docs credentials for imported or script-based document skills.

## Usage
- Configure the required values in the Cowork skill workbench.
- Script-based skills can read the configured values from the declared environment variables.
- MCP servers should still be configured in the MCP settings area, including their own `env` and `headers`.
