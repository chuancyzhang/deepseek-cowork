# 5.2.5 文档同步与截图清理记录

记录日期：2026-09-24。范围为源码基线文档和文档工具，不修改应用版本或运行时行为。

## 同步结果

- 当前态文档统一到源码 5.2.5，补齐 5.2.3–5.2.5 版本说明及专题导航。
- 用户指南的 27 张图片保持原文件；设置、资料库、能力和主题入口复用同场景图片。
- 账号连接、数据安全、独有主题及 SkillHub 详情图保留；详情图仅作场景示例，不代表重新完成当前版本缩放验收。
- 逐项删除 80 张已替代且无有效引用的图片，共 8,965,011 字节（约 8.55 MiB）。未清空目录，未修改应用图标、主题资源或第三方资产。
- 全仓引用扫描包含被 Git 忽略的本地开发记录；排除 Git 内部数据、虚拟环境、依赖、构建输出及本次临时文件。生成脚本中的输出文件名与读取输入分开核对。
- UI 截图脚本默认输出改为 `.tmp/documentation-qa/`，附件测试输入改用用户指南已有图片。

## 删除清单

以下为删除记录，代码形式的旧路径不应恢复为图片链接。旧连续编号操作图由新版用户指南覆盖；管理和 SkillHub 网格图由统一入口图替代；主题重复入口及错配的交互示例图已撤下。

| 原路径 | 处理原因 |
| --- | --- |
| `images/user-guide/s01-download-release.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s02-extract-zip.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s03-launch-application.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s03b-deepseek-quickstart.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s04-home-and-settings.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s05-model-services.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s05c-model-batch-capabilities.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s05d-model-fetch-loading.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s05e-model-fetch-error.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s06-add-model.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s07-model-configuration.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s08-model-and-reasoning.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s09-interface-overview.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s10-direct-chat.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s11-complete-conversation.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s12-add-project.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s13-project-picker.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s14-project-workspace.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s15-composer-add-menu.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s15a-grill-mode-armed.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s16-image-preview.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s16-pasted-image.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s17-thinking-and-tools.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s18-guidance-applied.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s18-guidance-running.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s19-edit-history-message.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s19a-question-navigator.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s20-task-observability.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s21-deliverables-list.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22-deliverable-preview.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22a-deliverable-edit-1x.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22a-deliverable-edit-1_25x.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22a-deliverable-edit-1_5x.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22a-deliverable-edit.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22b-deliverable-docx-edit-1x.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22b-deliverable-docx-edit-1_25x.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22b-deliverable-docx-edit-1_5x.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s22b-deliverable-docx-edit.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s23-generate-office-draft-action.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s23-generate-office-draft.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s24-ppt-agent.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s25-generate-from-html.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s26-generated-file-path.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s26-generated-file.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s27-powerpoint-edit.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s29b-wecom-capability.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s30-specified-capability.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s31-agent-picker.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s31-agent-settings.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s32-skill-capture-small.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s32-skill-capture.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s33-automation-center.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s33-skill-capture-background.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s34-automation-editor.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s34-skill-capture-ready.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s35-personality-memory.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s36-update.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s37-history-on-demand.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s39-ai-theme-package-home.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s40-enterprise-messages.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s40-favorites-library.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s40b-favorites-history.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s41-favorite-editor.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s41-wechat-scan.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s42-favorite-schedule.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/s43-favorite-message-delivery.png` | 旧版操作流程，已采用用户指南基准 |
| `images/user-guide/management-builtin-small.png` | 管理入口与步骤改用指南及正文 |
| `images/user-guide/management-mcp-edit-small.png` | 管理入口与步骤改用指南及正文 |
| `images/user-guide/management-agents-small.png` | 管理入口与步骤改用指南及正文 |
| `images/user-guide/management-sidebar.png` | 管理入口与步骤改用指南及正文 |
| `images/user-guide/skillhub-store-1.25.png` | 旧导航与加号安装按钮已过时 |
| `images/user-guide/skillhub-store-1.5.png` | 旧导航与加号安装按钮已过时 |
| `images/user-guide/skillhub-store-one.png` | 旧导航与加号安装按钮已过时 |
| `images/user-guide/skillhub-store-small.png` | 旧导航与加号安装按钮已过时 |
| `images/user-guide/skillhub-store-three.png` | 旧导航与加号安装按钮已过时 |
| `images/user-guide/skillhub-store.png` | 旧导航与加号安装按钮已过时 |
| `images/ai-theme-visualize/01-97792428.png` | 重复入口或与筛选／滑块说明不匹配 |
| `images/ai-theme-visualize/07-cec41a63.png` | 重复入口或与筛选／滑块说明不匹配 |
| `images/ai-theme-visualize/10-63de1211.png` | 重复入口或与筛选／滑块说明不匹配 |
| `images/ai-theme-visualize/11-39f6ca39.png` | 重复入口或与筛选／滑块说明不匹配 |

## 保留边界

- 本地开发经验仍引用以下四张历史图，保留原文件：
  - `images/user-guide/s05b-model-import.png`
  - `images/user-guide/s28-capability-center.png`
  - `images/user-guide/s29-capability-settings.png`
  - `images/user-guide/s38-component-status-cache.png`
- WeKnora 专题旧图退出当前操作说明；资料库验证脚本相关场景和其他独有 QA 图片保留，不把未引用一概视为过时。
- 既有历史发布说明、文章和源码测试记录不反向改写为本次验证。

## 本次验证

- `scripts/check_docs.py`：通过，版本来自 `core/app_version.py`，不再写死版本或截图总数。
- `test/test_documentation.py`：8 项测试及 6 项子测试通过，覆盖当前声明、历史引用、失效链接、缺图、场景完整性和资产保护。
- `scripts/build_documentation_docx.py --help`：通过，未生成 Word 文件。
- `git diff --check`：通过。
- 对照图片联系表核对用户指南、重复图和独有专题图；未重新运行应用 UI、真实服务、Windows 缩放或打包验收。
