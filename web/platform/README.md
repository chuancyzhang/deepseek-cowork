# Cowork 企业平台

独立部署在 Linux 内网的企业服务，提供统一认证、成员和模型授权、Windows x64 客户端分发、近 30 天企业模型提问查询，以及团队空间的计划、共享资产和成果。桌面程序仍可暂不登录并使用个人模型。空间任务在员工电脑执行，中心不执行任务，也不同步完整私人对话。

服务为 FastAPI 单进程、PostgreSQL、同源静态页面及发布文件目录。整个 `web/platform/` 是独立部署单元，可单独复制并重命名为服务器上的任意目录，无需客户端源码或仓库外层文件。静态页面不引用 CDN。标准客户端由原有 Windows 构建链生成，Linux 中心只校验 ZIP 并加入公开连接文件，不执行上传的程序。

员工操作见[企业连接使用说明](../../docs/enterprise-platform.md)。服务结构：

- `server/`：认证、授权、模型网关、提问留存、发布状态机及命令入口。
- `ui/`：成员、模型、提问记录、设置与分发页面。
- `migrations/`：逐版迁移约定；当前为结构版本 2，独立增加团队空间表。
- `compose.yaml`：PostgreSQL、应用与 HTTPS 反向代理。

## Windows 本机 Docker 启动

本机配置使用 `compose.local.yaml`，仅映射 `127.0.0.1:8788`，数据库不对宿主机开放端口。数据库和发布文件使用独立命名卷；该入口运行真实中心服务，不注入演示管理员。

首次已构建或导入 `cowork-enterprise:local` 镜像后，在此文件夹执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1`。脚本首次生成 `.env.local` 的随机数据库密码和加密密钥，以后保留原配置；请保管此文件，不要在已有数据卷时删除重建。访问 `http://localhost:8788/app`。首次打开网页会进入初始化页面：直接创建首位独立管理员账号，然后在“设置 → 添加认证来源”配置企业认证。

停止：`docker compose --env-file .env.local -f compose.local.yaml stop`。重新启动：再次运行启动脚本。导出的镜像包不包含本机凭据或数据库；镜像导入可用 `docker load -i images.tar`。

## 首次网页初始化与管理员登录

1. 启动容器后打开 `/app`，空平台自动显示“初始化企业平台”。
2. 在网页填写管理员账号、密码及确认密码，直接创建首位管理员。账号为 3–64 位字母、数字、点、横线或下划线；密码至少 12 个字符。
3. 创建后直接进入“设置”，点击“添加认证来源”，选择平台账号密码后直接保存并启用；OAuth2、OIDC、LDAP 则填写参数、保存草稿、完成测试登录后启用。
4. 员工使用企业认证登录，默认是普通成员；首位管理员在“成员 → 管理与分配”授予或收回管理员角色。其他管理员不能转授管理员角色。
5. 管理员使用登录页的“平台管理员登录”。尚无启用的企业认证时，网页直接显示管理员登录。

首次创建只允许成功一次，并发提交只有一个能成功。初始化完成状态持久保存，重启、账号停用或移除不会重新开放；已有成员或认证来源的平台也不开放首次创建。首位管理员负责后续管理员授权，不能停用或降级。旧式 bootstrap 配置仅用于没有成员且未初始化的实例，之后不会再自动授予管理员角色。

管理员密码按随机盐和 scrypt 派生存储，不能读取原密码。此管理员入口仅提供网页会话，不作为员工桌面认证来源。无需初始化码；旧版本遗留的 `setup-code` 文件不再读取或生效。管理员登录连续 5 次错误后暂停尝试 5 分钟，失败输入保留。当前版本没有网页找回或修改管理员密码入口，请妥善保管创建的密码。

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
| `COWORK_BOOTSTRAP_PROVIDER`、`COWORK_BOOTSTRAP_SUBJECT` | 仅旧式命令行初始化使用；网页创建独立管理员时留空 |
| `COWORK_LOGIN_PROVIDER` | 员工登录入口的部署默认值；网页“客户端统一登录方式”保存后优先使用网页配置。仅一个启用来源时可省略 |
| `COWORK_SESSION_SECONDS` | 中心会话期限，默认 8 小时 |
| `COWORK_MAX_MODEL_CONCURRENCY` | 同时运行的模型请求上限，默认 64；达到上限明确返回忙碌状态 |
| `COWORK_CLEANUP_SECONDS` | 到期提问删除间隔，默认 60 秒，允许 1–300 秒 |

数据库与应用端口不映射到宿主机，仅反向代理开放 443。不要直接将应用 8080 端口暴露为公网入口。证书由企业统一管理；应用和 LDAP 均不会关闭证书校验。若必须使用内网 HTTP 模型端点，部署方可明确设置 `COWORK_ALLOW_MODEL_HTTP=1`，此选项控制上游模型连接，与客户端企业连接支持 HTTP / HTTPS 是独立配置。

首次启动（自动迁移数据库），然后按上文完成网页初始化：

```bash
sh start.sh
```

### 可选：沿用企业账号作为首位管理员

已有自动部署流程可继续使用下述命令行导入方式；该方式与首次网页创建管理员二选一。须预先填写两项 `COWORK_BOOTSTRAP_*`。

数据库初始化：

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

## 平台账号密码（数据库认证）

不依赖外部身份平台，员工账号与密码校验值存储在本平台的 PostgreSQL 中。客户端仍使用“登录企业账号”入口，在平台网页输入账号密码，完成后自动回到客户端。

1. “设置 → 添加认证来源”选择“平台账号密码”，填写名称和来源 ID，点击“保存并启用”；无需填写外部数据库或协议参数。
2. 点击“管理员工账号 → 创建员工账号”，填写账号、成员姓名和初始密码（至少 12 个字符）。账号名称创建后不可更改；相同来源不能重名，不同来源的同名账号属于不同成员。
3. 平台账号密码无需测试登录；可以先启用来源再创建账号。已有草稿直接点击“启用”即可。
4. 有多个启用来源时，在“设置 → 客户端统一登录方式”选择该来源并保存。只有一个来源时自动使用它。
5. 在账号列表的“模型与权限”或“成员”页分配模型。新建账号默认是普通成员，管理员角色仍只能由首位管理员授权。

账号列表可修改姓名、停用账号、重设密码。密码使用随机盐与 scrypt 保存校验值，接口不返回密码、哈希或盐。重设密码会撤销已有平台会话及未完成的登录交接；停用账号也会拒绝新登录。登录连续 5 次失败会暂停该账号 5 分钟，管理员重设密码可清除锁定。管理员角色的数据库账号只能由首位管理员管理其密码和状态，避免通过重设密码绕过管理员授权。

数据库账号由管理员创建，不开放员工自助注册。忘记密码由管理员在账号页面重设，当前没有员工自助修改密码入口。登录协议与桌面交接保持兼容，不改变模型请求前缀、个人配置或本机对话。

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

OAuth2、OIDC、LDAP 配置变更须保存草稿、完成测试登录后再启用；平台账号密码无需测试，保存配置保留当前启用状态。不同认证来源的相同用户标识不会合并。停用成员会撤销中心会话，不能通过重新登录再次加入。

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

企业包仅新增 EXE 同目录的 `enterprise_connection.json`，原标准文件逐个复核摘要不变。六字段公开配置仅包含 schema/config 版本、平台标识、名称、HTTP 或 HTTPS 地址和 enterprise 渠道，不含任何账号或密钥。

员工下载后完整解压；“设置 → 账号与连接”只需点击“登录企业账号”，在企业认证网页输入账号密码或扫码；二维码由身份平台提供。LDAP 也在中心网页登录页输入账号密码，不在桌面暴露来源选择与协议配置。管理员可以给普通版本的 EXE 同目录放置公开连接文件，重启后出现同一登录入口。

已有确认连接优先于包内默认配置；配置不同时可明确确认“使用安装包中的企业连接”。同平台更低版本、同版本异值会拒绝，不会暗中迁移域名。企业更新入口打开中心下载页，仍用现有本地 ZIP 更新器；安装普通包、其他平台或不同地址的包会在生成更新计划之前阻止。初期不启用中心自动增量更新。

## 团队空间

员工在桌面“团队空间”完成邀请、计划、留言、资产和成果发布；网页提供空间管理、只读协作内容与文件下载。操作说明见[团队空间指南](../../docs/team-spaces.md)。服务不保存私人任务目录或完整对话，也不执行员工任务。

升级前停止应用写入并备份业务库与文件卷。运行 `python -m server migrate` 完成结构版本 1 → 2，启动也会检查迁移；原账号、模型授权和客户端发布记录保留。版本 2 的新增表与回滚边界见[迁移说明](migrations/README.md)。

- `COWORK_MAX_SPACE_FILE_BYTES`：单文件字节上限，默认 `134217728`（128 MiB）。
- `COWORK_MAX_SPACE_BYTES`：单空间已发布版本及有效上传预留总容量，默认 `5368709120`（5 GiB）；旧修订和撤下资产仍计入保留容量。
- 文件位于 `COWORK_DATA_DIR/team-space-files`，沿用 Compose 的 `/data` 业务文件卷，但不通过公开发行下载接口提供访问。
- 上传先校验大小与 SHA-256，再事务发布；超过 24 小时仍未发布的临时字节清理，原操作可重新上传。共享对象与已发布修订没有 30 天自动清理。
- 所有者停用后的恢复入口位于管理员“成员 → 成员详情 → 恢复空间所有权”。仅提供必要标识和已有有效成员，不开放共享正文。

空间接口能力由 `/api/v1/platform-info` 的 `team_spaces` 声明。旧中心或不兼容中心下，空间任务显示不可继续，普通个人路径不新增中心依赖。已绑定的空间任务不能使用不支持空间约束的旧客户端继续执行，升级或回退时须保持客户端和中心兼容。

## 数据、备份与恢复

提问正文只在 `questions` 表保存，从中心首次接收开始保留 30×24 小时；查询立即过滤到期记录，后台物理删除，启动先执行清理。搜索不建额外副本，不提供批量导出。按成员、客户端实例、提交标识去重；重试不延长到期时间。模型完整上下文仅用于代理转发，不写入提问表、操作日志或历史备份。

业务配置备份示例：

```bash
docker compose exec -T database pg_dump -U cowork -d cowork -Fc --exclude-table-data=questions > cowork-business.dump
```

另行备份整个业务文件卷（包括发布文件和 `team-space-files`）及独立保管的加密密钥。数据库与文件备份期间停止应用写入，确保引用对应同一时点。禁止给此数据库卷配置包含提问正文的历史快照、全量物理备份或长期 WAL 归档；否则无法兑现 30 天正文删除规则。备份 PostgreSQL 业务表时必须排除 `questions` 数据，保留表结构即可。备份恢复应进入独立测试实例，核对平台标识和加密密钥，再按逐表恢复流程切换服务；不要把其他企业的数据导入现有实例。恢复后先检查提问表为空、模型密钥可解密、发布文件摘要与数据库一致，再执行 `python -m server verify-space-files` 核对全部已发布空间文件大小及 SHA-256；输出 `failures` 为空且退出码为 0 后开放访问。

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

真实企业 SSO/LDAP、实际模型端点、干净 Windows 机器首次安装和 Linux/PostgreSQL 压测仍需部署联调。2026-10-17 在本机 Docker Desktop 的 Linux 容器中完成应用镜像构建及 PostgreSQL 17 启动，两项容器健康检查通过，`/healthz`、`/api/v1/platform-info`、`/api/v1/auth/providers` 和 `/app` 均返回 200。已导出本机启动镜像包；未生成或发布正式 Windows 企业客户端发行包。

数据库账号及原有企业流程的聚焦回归为 `python -m pytest -q test/test_enterprise_database_auth.py test/test_enterprise_setup.py test/test_enterprise_platform.py test/test_enterprise_client.py test/test_enterprise_deployment.py`，55 项测试及 3 个子测试通过，包含无账号时直接启用来源、原生表单来源校验、现有桌面客户端完成账号密码浏览器登录并取得模型授权。Docker PostgreSQL 独立 schema 另行验证并发同名创建只成功一次、认证测试/启用、桌面交接和重设密码撤销令牌。

UI 验收夹具可用 `python test/enterprise_ui_fixture.py` 启动；仅绑定本机 8787 端口（使用 `http://127.0.0.1:8787/qa/login`，与 localhost 服务隔离 Cookie），数据位于临时目录，`/qa/login` 仅存在于此测试脚本，生产应用没有该入口。测试完成须停止夹具。

认证来源列表提供“删除”操作，并在执行前确认。仅可删除已停用且未关联成员的来源；正在进行的登录需先结束或等待过期。若来源被指定为客户端统一登录入口，先更换入口。已关联成员的来源可停用，保留员工账号、授权与历史记录。

标准包仅允许 `_internal/config.json` 中包含空 API Key、默认 `https://api.deepseek.com` 地址和空 `disabled_skills` 列表；本地打包与平台使用同一内容校验规则。其他路径的配置文件、未知字段、重复字段、凭据或自定义值均拒绝分发。

客户端发布列表支持可恢复删除：草稿、失败、可发布或已撤下记录可确认删除，默认列表将隐藏该记录；在“显示已删除”中可恢复。安装包文件保留，恢复不自动发布或设为推荐。已发布版本先撤下；上传或生成中的记录先取消或等待完成。此操作不释放磁盘空间。

客户端企业连接接受 HTTP 和 HTTPS 地址，本机、内网和域名地址均可使用；地址不能包含凭据、查询参数或片段。本机 localhost、127.0.0.1、::1 请求直连，不经系统代理。旧客户端将 HTTP 连接显示为“企业连接无法读取”时，需要重新构建标准客户端并由平台重新生成企业包；仅修改服务端或重复下载旧包不会更新 EXE 中的校验逻辑。
