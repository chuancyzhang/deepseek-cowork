# sub2api / New API 与模型服务验收

验证日期：2026-10-29 至 2026-10-30。此次改动采用服务总览、页内接入和统一保存，沿用现有主题、模型渠道及会话模型绑定。

## 实现与契约

- `core/llm/gateway_accounts.py` 负责站点识别、登录、TOTP、密钥读取/创建及按密钥发现模型；登录状态只存在于本次接入的独立 HTTP 会话。New API 区分 Cookie 与 Access Token 登录、遮罩密钥与专用完整密钥接口，支持 RSA-OAEP/SHA-256 和长密码 v2 加密。
- `core/llm/gateway_catalog.py` 只映射明确声明。sub2api 能力清单仅补充 `/v1/models` 已返回的模型，不扩大密钥可用范围；能力清单读取失败会提示并保留模型列表。分组权限不等同于模型能力；推理上限不扩展为支持档位；独立图片接口不等同于 Responses 图片工具。
- `ui/gateway_setup.py` 和 `ui/model_services.py` 提供站点识别、账号登录或手动密钥、密钥多选、独立创建步骤、模型选择、部分失败重试及服务详情。后台结果绑定接入代次；取消或销毁后的结果不能进入新流程。临时 HTTP 会话在线程结束后释放，包括线程已经返回、GUI 完成信号尚未处理的窗口。
- 持久化只新增白名单来源与能力字段，不保存密码、验证码、Cookie、管理端 Token 或上游提示词。已有模型 ID、人工配置和默认模型保留。手动修改账号连接的地址或密钥会解除原账号来源绑定，作为独立服务保存。
- 创建请求不自动重试。名称附加短标识，响应丢失后只核对同名远端记录；本地放弃不会删除远端密钥。新旧配置使用同一个“保存更改”，失败保留草稿。

上游接口核对依据：[sub2api 密钥](https://github.com/Wei-Shaw/sub2api/blob/main/frontend/src/api/keys.ts)、[sub2api 登录](https://github.com/Wei-Shaw/sub2api/blob/main/frontend/src/api/auth.ts)、[sub2api 模型路由](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/server/routes/gateway.go)、[sub2api 能力清单](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/service/group_codex_models_manifest.go)、[New API 鉴权](https://github.com/QuantumNous/new-api/blob/main/docs/authentication.md)、[New API 密钥](https://github.com/QuantumNous/new-api/blob/main/controller/token.go)、[New API 模型](https://github.com/QuantumNous/new-api/blob/main/controller/model.go)。这些链接为上游可变分支，兼容性由本地接口样本测试固定。

## 自动化验证

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pytest -q test/test_gateway_setup.py test/test_gateway_accounts.py test/test_model_configuration_flow.py test/test_model_catalog_and_onboarding.py test/test_default_model_settings.py test/test_llm_factory.py test/test_worker_lifecycle.py --tb=short --disable-warnings
```

结果：**120 项测试、12 个子测试通过**。覆盖两种站点、新旧鉴权、密码加密、TOTP、分页、完整密钥读取、空/失效密钥、创建响应丢失后的核对、多密钥导入与去重、部分失败及重试、取消与迟到结果、来源持久化、人工覆盖、默认模型、保存失败恢复、会话绑定和退出线程生命周期。测试使用 `httpx.MockTransport` 与隔离临时配置，没有真实账号或生成类网络请求。

## 界面验收

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/render_model_configuration.py
$env:QT_SCALE_FACTOR='1.5'
.venv/Scripts/python.exe -X utf8 -B scripts/render_model_configuration.py
Remove-Item Env:QT_SCALE_FACTOR

$env:QT_QPA_PLATFORM='windows'
$env:COWORK_MODEL_SCREENSHOTS='D:\code\cowork\docs\images\model-configuration\native'
.venv/Scripts/python.exe -X utf8 -B scripts/render_model_configuration.py
$env:QT_SCALE_FACTOR='1.5'
.venv/Scripts/python.exe -X utf8 -B scripts/render_model_configuration.py
Remove-Item Env:QT_SCALE_FACTOR, Env:QT_QPA_PLATFORM, Env:COWORK_MODEL_SCREENSHOTS
```

Windows 原生窗口读取到 **144 DPI（系统 150%）**；Qt 缩放为 1 时设备像素比 1.5，叠加 `QT_SCALE_FACTOR=1.5` 后为 2.25。没有修改系统缩放。离屏与原生均覆盖浅色、深色、小窗口、模型编辑与返回、主题候选取消、保存栏、创建与重试入口、控件生命周期。截图脚本实际走过识别、登录、创建、部分失败、重试、导入和保存；没有非预期顶层控件、水平溢出、主题候选持久化或会话串用。原生窗口可能受屏幕可用尺寸限制，实际逻辑尺寸记录在 JSON 中。

| 状态 | 截图 |
| --- | --- |
| 服务总览 | [查看](images/model-configuration/services-overview.png) |
| 识别后登录 | [查看](images/model-configuration/service-sign-in.png) |
| 创建密钥与额度 | [查看](images/model-configuration/service-key-create.png) |
| 多密钥模型选择 | [查看](images/model-configuration/service-model-selection.png) |
| 部分失败恢复 | [查看](images/model-configuration/service-partial-failure.png) |
| 原生高 DPI 创建入口 | [查看](images/model-configuration/native/service-key-create-bottom.png) |
| 原生深色、高 DPI | [查看](images/model-configuration/native/service-partial-failure-dark-150.png) |
| 原生测量记录 | [系统缩放](images/model-configuration/native/verification.json) · [叠加 Qt 缩放](images/model-configuration/native/verification-150.json) |

## 边界

没有使用真实 sub2api / New API 账号做端到端测试，也没有验证第三方站点的私有改版、真实验证码/SSO 或真实模型调用。遇到网页验证或不支持的登录方式，用户可前往站点并手动填写 API Key。当前不支持的原生协议保留可见说明，不能直接导入为对话模型。

应用运行时的系统提示词、工具定义、历史消息组织与缓存参数未改动；已有模型的 prompt prefix 保持不变。此次没有修改 `APP_VERSION`，也没有提交、推送或发布。
