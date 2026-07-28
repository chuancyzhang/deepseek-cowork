---
name: wind-alice
description: 调用 Wind Alice Agent 执行其金融研究工作流，并将流式结果和附件安全保存到当前 Cowork 工作区。
---

# Wind Alice

## 何时使用

当用户明确要求 Alice，或所选子 Skill 的执行入口为 `wind_alice` 时使用。将用户原始问题
原样传入；指定 Alice Skill 时可使用中文名、英文名或上游支持的别名。

## Cowork 执行

通过根插件的 `wind_alice` 入口运行：

```text
wind_alice --prompt "<用户原始问题>" [--skill "<Alice Skill 名称>"]
wind_alice list-skills
```

配置仅由能力中心临时注入：

- `WIND_API_KEY`：调用与附件下载所需。
- `WIND_ALICE_API_URL`：可选，默认使用插件声明的 Wind 官方 Alice 地址。

不得扫描用户目录或源码目录中的配置，不得把 Key 写入文件，也不得启动浏览器或更新
Skill。缺少配置时直接返回认证错误和能力中心配置指引。

## 文件与工作区

- 必须存在 `COWORK_WORKSPACE_DIR`。
- Alice 附件统一写入
  `COWORK_WORKSPACE_DIR/wind-aifinmarket/alice-downloads/`。
- 目录创建、权限或下载失败时返回具体根因，不回退到当前目录、用户目录或源码目录。
- 同名文件追加序号，不覆盖已有文件。

## 交付

1. 等待流式任务结束，不并行重复提交同一问题。
2. 使用 Alice 返回的 `agentResult.value` 作为主要正文，保留本地附件路径。
3. 不输出 API Key、Bearer Token、完整敏感请求或配置内容。
4. 标注 Wind Alice 为实际来源，并说明查询时间、限制和金融免责声明。

## 金融安全

Alice 的交易、止损、止盈、仓位和提醒类结果只作为分析与计划；不得真实下单、修改账户
或创建外部提醒。输出不构成投资建议，使用者应独立核验数据和适用法规。

