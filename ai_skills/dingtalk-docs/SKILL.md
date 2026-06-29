---
name: dingtalk-docs
description: Configuration preset for DingTalk document skills.
description_cn: 钉钉文档能力配置预设，用于保存并注入文档操作所需凭据。
license: Apache-2.0
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
security_level: medium
---

# DingTalk Docs

This bundled preset stores DingTalk application credentials for imported or script-based document skills.

## Usage
- Configure appKey, appSecret, and operatorId in the Cowork skill workbench.
- Script-based skills can read the configured values from the declared environment variables.
- Third-party skills that require their own config file should document that compatibility path explicitly.
