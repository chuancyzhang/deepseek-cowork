# Cowork 企业平台

独立部署在 Linux 内网的企业服务，提供统一认证、成员和模型授权、Windows x64 客户端分发、近 30 天企业模型提问查询。桌面程序仍可暂不登录并使用个人模型。项目协作属于后续扩展，本服务不执行员工任务，也不同步完整对话。

服务为 FastAPI 单进程、PostgreSQL、同源静态页面及发布文件目录。整个 `web/platform/` 是独立部署单元，可单独复制并重命名为服务器上的任意目录，无需客户端源码或仓库外层文件。静态页面不引用 CDN。标准客户端由原有 Windows 构建链生成，Linux 中心只校验 ZIP 并加入公开连接文件，不执行上传的程序。

员工操作见[企业连接使用说明](../../docs/enterprise-platform.md)。服务结构：

- `server/`：认证、授权、模型网关、提问留存、发布状态机及命令入口。
- `ui/`：成员、模型、提问记录、设置与分发页面。
- `migrations/`：数据库版本约定；当前为初始结构版本 1。
- `compose.yaml`：PostgreSQL、应用与 HTTPS 反向代理。

## Linux 部署

将整个 `web/platform/` 文件夹复制到 Linux 服务器，例如 `/opt/cowork-platform/`。准备 Docker Compose、可达的认证与模型服务、企业域名以及受客户端信任的 HTTPS 证书。以下命令都在该目录内执行，不依赖上级目录。

```bash
cp .env.example .env
chmod 600 .env
mkdir -p certs
# 将企业签发的证书与私钥分别放到 certs/tls.crt、certs/tls.key
docker compose build platform
docker compose run --rm --no-deps platform python -m server generate-key
```

将生成的加密密钥填入 `.env`，并填写这些部署参数：

| 配置 | 作用 |
| --- | --- |
| `POSTGRES_PASSWORD`、`COWORK_DATABASE_URL` | 同一数据库密码；DSN 中的特殊字符必须 URL 编码 |
| `COWORK_HOST`、`COWORK_PUBLIC_URL` | 员工访问的域名及 HTTPS 基础地址 |
| `COWORK_PLATFORM_ID` | 固定平台标识；以后不得将同一数据库改绑其他标识 |
| `COWORK_PLATFORM_NAME` | 企业显示名称 |
| `COWORK_ENCRYPTION_KEY` | 中心模型密钥和认证凭据的 Fernet 加密密钥，必须独立保管 |
| `COWORK_BOOTSTRAP_PROVIDER`、`COWORK_BOOTSTRAP_SUBJECT` | 事先指定的首位管理员认证来源和稳定用户标识，不能使用“第一个登录的人” |
| `COWORK_LOGIN_PROVIDER` | 员工统一登录使用的认证来源 ID；仅一个启用来源时可省略，多个来源时必须明确指定 |
| `COWORK_SESSION_SECONDS` | 中心会话期限，默认 8 小时 |
| `COWORK_MAX_MODEL_CONCURRENCY` | 同时运行的模型请求上限，默认 64；达到上限明确返回忙碌状态 |
| `COWORK_CLEANUP_SECONDS` | 到期提问删除间隔，默认 60 秒，允许 1–300 秒 |

数据库与应用端口不映射到宿主机，仅反向代理开放 443。不要直接将应用 8080 端口暴露为公网入口。证书由企业统一管理；应用和 LDAP 均不会关闭证书校验。若必须使用内网 HTTP 模型端点，部署方可明确设置 `COWORK_ALLOW_MODEL_HTTP=1`，这不改变登录和客户端下载的 HTTPS 要求。

首次启动与数据库初始化：

```bash
docker compose up -d database
docker compose run --rm platform python -m server migrate
```

创建本地 `bootstrap-auth.json`，导入下面任一种认证配置。文件不得提交到仓库，权限设为 `600`。

```json
{
  "id": "company-sso",
  "name": "企业统一认证",
  "mode": "oidc",
  "enabled": true,
  "config": {
    "issuer": "https://identity.example.internal",
    "discovery_url": "https://identity.example.internal/.well-known/openid-configuration",
    "client_id": "cowork-platform",
    "pkce": "S256"
  },
  "secrets": {"client_secret": "由身份平台提供"}
}
```

```bash
chmod 600 bootstrap-auth.json
docker compose run --rm -T platform python -m server import-auth - < bootstrap-auth.json
sh start.sh
```

在身份平台注册准确回调地址：`https://企业域名/auth/callback/company-sso`。首位管理员完成认证后自动验证此来源，其余员工之后登录自动创建无模型权限的普通成员。管理员在“模型”中添加、测试、启用模型，再到“成员”分配。随后可移除 `.env` 中的两项 bootstrap 配置并重建应用容器，删除本次导入文件；后续认证设置通过管理页面维护。

首次配置完成后，日常启动只需进入部署目录运行 `sh start.sh`（等同于 `docker compose up -d --build --wait`）。命令等待应用健康检查，失败会返回非零状态。查看日志使用 `docker compose logs --tail=100 platform`；停止使用 `docker compose stop`，数据保存在命名卷中。程序代码升级后同样运行启动命令；不要使用删除数据卷的参数。

## OAuth2 兼容配置与 LDAP

管理页面“添加认证来源”默认提供兼容 OAuth2 预设，参数由表单与高级协议配置共同保存，不依赖外部附件。兼容预设的完整导入形态如下：

```json
{
  "id": "company-sso",
  "name": "企业统一认证",
  "mode": "oauth2",
  "enabled": true,
  "config": {
    "authorize_url": "https://identity.example.internal/idp/oauth2/authorize",
    "token_url": "https://identity.example.internal/idp/oauth2/getToken",
    "userinfo_url": "https://identity.example.internal/idp/oauth2/getUserInfo",
    "client_id": "cowork-platform",
    "pkce": "S256",
    "client_auth": "client_secret_post",
    "token_encoding": "form",
    "include_redirect_uri": false,
    "userinfo_method": "GET",
    "userinfo_token_delivery": "query",
    "userinfo_token_parameter": "access_token",
    "userinfo_client_id": true,
    "subject_token_path": "uid",
    "name_path": "displayName",
    "email_path": "mail",
    "error_field": "errcode",
    "success_values": []
  },
  "secrets": {"client_secret": "由身份平台提供"}
}
```

只有确认服务不支持 PKCE 时才改为 `disabled`；不会在失败后自动降级。可以配置不同端点、嵌套字段路径、Form/JSON 令牌请求、Basic/Post/None 客户端认证以及用户资料 GET/POST。`errcode` 等业务错误即使 HTTP 200 也会拒绝；两个响应都映射稳定身份时必须一致。上游刷新与全局退出未实现、默认未启用，退出仅注销 Cowork 会话。

LDAP 使用 `mode: "ldap"`，配置 `host`、`port`、`tls`（`ldaps` 或 `starttls`）、`base_dn`、`user_filter`（例如 `(uid={username})`）、`identity_attribute`（例如 `entryUUID`），可选 `bind_dn` 和 `ca_file`。查询账号密码放在 `secrets.bind_password`。`ca_file` 是容器内管理员挂载的 CA 文件路径。身份属性必须稳定，不能以登录名或 DN 变化伪装为同一成员。查询用户后再用其 DN 和用户密码绑定；不保存用户密码。

已有认证配置变更须保存草稿、完成测试登录后再启用。不同认证来源的相同用户标识不会合并。停用成员会撤销中心会话，不能通过重新登录再次加入。

## 子路径与原生进程部署

支持 `COWORK_PUBLIC_URL=https://企业域名/cowork`；回调地址和下载地址均带 `/cowork`。反向代理必须保留此前缀，或用 `handle_path /cowork/*` 去除后转交应用；仅选择一种代理方式并验证 `/cowork/api/v1/platform-info`、登录、静态资源和下载。不要将应用分别部署在无关域名。

不用 Docker 时，在独立 Python 3.12 环境中，从复制后的部署文件夹内运行：

```bash
python -m pip install -r requirements.txt
python -m server migrate
python -m server serve --host 127.0.0.1 --port 8080
```

设置相同的 `COWORK_*` 环境变量，额外指定绝对路径 `COWORK_DATA_DIR`。由 systemd 等进程管理器维护单进程服务，并在前方配置 HTTPS。命令入口关闭访问日志及 HTTP 客户端 INFO 日志，避免 OAuth query 中的令牌被写入日志。不要开启请求正文、SQL 参数、代理访问 URL 的调试日志。

## 客户端构建、发布与更新

在 Windows 按[现有构建说明](../../README_CN.md#构建发行包)运行 PyInstaller 和 `scripts/package_release.py`。新标准包必须包含 `_internal/enterprise_capabilities.json`；报告包含 `supported_connection_schemas` 和 `supported_platform_api_versions`。中心会同时检查报告、SHA-256、大小、实际 ZIP 结构与能力声明，拒绝旧构建、用户数据、既有企业种子、路径穿越、重复文件和链接。

管理员在“设置 → 客户端分发”新建发布，上传 ZIP 和对应报告，点击“校验并生成企业包”，下载核验后发布并设置推荐。上传、封装、可发布、已发布分别呈现；后台处理成功之前不开放下载。撤下当前推荐时明确选择替代版本或空状态，旧链接也会拒绝新下载。

企业包仅新增 EXE 同目录的 `enterprise_connection.json`，原标准文件逐个复核摘要不变。六字段公开配置仅包含 schema/config 版本、平台标识、名称、HTTPS 地址和 enterprise 渠道，不含任何账号或密钥。

员工下载后完整解压；“设置 → 账号与连接”只需点击“登录企业账号”，在企业认证网页输入账号密码或扫码；二维码由身份平台提供。LDAP 也在中心网页登录页输入账号密码，不在桌面暴露来源选择与协议配置。管理员可以给普通版本的 EXE 同目录放置公开连接文件，重启后出现同一登录入口。

已有确认连接优先于包内默认配置；配置不同时可明确确认“使用安装包中的企业连接”。同平台更低版本、同版本异值会拒绝，不会暗中迁移域名。企业更新入口打开中心下载页，仍用现有本地 ZIP 更新器；安装普通包、其他平台或不同地址的包会在生成更新计划之前阻止。初期不启用中心自动增量更新。

## 数据、备份与恢复

提问正文只在 `questions` 表保存，从中心首次接收开始保留 30×24 小时；查询立即过滤到期记录，后台物理删除，启动先执行清理。搜索不建额外副本，不提供批量导出。按成员、客户端实例、提交标识去重；重试不延长到期时间。模型完整上下文仅用于代理转发，不写入提问表、操作日志或历史备份。

业务配置备份示例：

```bash
docker compose exec -T database pg_dump -U cowork -d cowork -Fc --exclude-table-data=questions > cowork-business.dump
```

另行备份发布文件卷及独立保管的加密密钥。禁止给此数据库卷配置包含提问正文的历史快照、全量物理备份或长期 WAL 归档；否则无法兑现 30 天正文删除规则。备份 PostgreSQL 业务表时必须排除 `questions` 数据，保留表结构即可。备份恢复应进入独立测试实例，核对平台标识和加密密钥，再按逐表恢复流程切换服务；不要把其他企业的数据导入现有实例。恢复后先检查提问表为空、模型密钥可解密、发布文件摘要与数据库一致，再开放访问。

服务启动会将中断的上传/封装标记为失败。完整源包可重新处理，半成品不公开；已有已发布版本和推荐记录保留。磁盘不足、数据库写入失败或到期清理失败不会伪装为成功。管理页展示最近清理状态；日志用 request/member/model/release ID 定位阶段，避免记录问题正文和搜索词。

## 验证与当前边界

以下开发回归命令在完整仓库根目录执行；复制到服务器的独立目录无需包含桌面测试代码。

```bash
python -m pytest -q test/test_enterprise_deployment.py test/test_enterprise_platform.py test/test_enterprise_client.py test/test_updater.py test/test_update_coordinator.py test/test_package_release.py test/test_connections_ui.py test/test_conversation_integrity.py test/test_llm_factory.py
```

`COWORK_TEST_DATABASE_URL=postgresql+psycopg://...` 可让平台契约测试使用 PostgreSQL。测试为每个用例创建独立 `cowork_test_<uuid>` schema，结束后仅删除该 schema；请使用专门测试数据库及具备建 schema 权限的测试账号。

本次聚焦回归通过 112 项测试、34 个子测试，覆盖独立文件夹启动、单一浏览器登录入口、OIDC 签名/nonce、另一组 OAuth 字段映射、LDAP 网页及适配契约、真实 OpenAI SDK 经中心流式调用、权限与凭据隔离、提问去重/删除、客户端包封装/撤回和个人使用。独立目录测试只复制中心目录到临时位置并清空 `PYTHONPATH`，完成迁移、应用启动及页面/接口读取，未借用客户端仓库路径。

补充的管理页面路由、离开守卫、样式与资料库界面回归 34 项通过；桌面截图检查了 100%/150% 缩放和 460px 窄窗口。

并发测试使用本机 SQLite 与模拟上游：32 路流式请求同时运行，另取消 8 路，40 条提问各保存一次，连接名额全部归还。它验证并发行为，不代表实际 Linux 容量。

扩展检查中的模型目录/首次配置测试有 10 项既有失败；在未修改的 HEAD 隔离副本复现相同结果，原因是旧 `deepseek-v4-*` 预期与当前推荐模型配置不一致。本次未修改这部分模型推荐规则，不宣称全仓回归通过。

真实企业 SSO/LDAP、实际模型端点、干净 Windows 机器首次安装和 Linux/PostgreSQL 压测仍需部署联调。本机 Docker Desktop 因残留 `sailor-ingest.sock` 启动失败，运行期容器验证未完成；Compose 配置已静态校验。没有生成或发布正式企业发行包。

UI 验收夹具可用 `python test/enterprise_ui_fixture.py` 启动；仅绑定本机 8787 端口，数据位于临时目录，`/qa/login` 仅存在于此测试脚本，生产应用没有该入口。测试完成须停止夹具。
