# Cowork 企业平台

文档适用版本：**5.4.0**

企业平台提供统一认证、成员与模型授权、客户端分发、企业提问查询和团队空间。桌面使用方法见[用户手册](../../docs/user-guide.md#企业账号与团队空间)。

`web/platform/` 是可独立复制的部署单元，由 FastAPI、PostgreSQL、同源静态页面和业务文件目录组成：

| 路径 | 职责 |
| --- | --- |
| `server/` | 认证、模型网关、提问记录、团队空间、分发及命令入口 |
| `ui/` | 平台管理页面 |
| `compose.yaml` | Linux 服务、数据库与 HTTPS 代理 |
| `compose.local.yaml` | Windows 本机 Docker 启动配置 |

## Windows 本机 Docker 启动

构建或导入 `cowork-enterprise:local` 镜像后，在此目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```

脚本首次生成 `.env.local`，以后复用原配置。打开 `http://localhost:8788/app`，按页面创建首位平台管理员。

停止与再次启动：

```powershell
docker compose --env-file .env.local -f compose.local.yaml stop
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```

服务端代码升级时，先构建镜像，再停止应用并完成业务备份，然后启动新服务：

```powershell
docker compose --env-file .env.local -f compose.local.yaml build platform
docker compose --env-file .env.local -f compose.local.yaml stop platform
docker compose --env-file .env.local -f compose.local.yaml up -d --no-deps --wait platform
```

## Linux 部署

将整个部署目录复制到服务器，例如 `/opt/cowork-platform/`。准备 Docker Compose、企业域名、认证和模型服务，以及受客户端信任的证书。以下命令均在部署目录执行：

```bash
cp .env.example .env
chmod 600 .env
mkdir -p certs
# 将证书与私钥放到 certs/tls.crt、certs/tls.key
docker compose build platform
docker compose run --rm --no-deps platform python -m server generate-key
```

把生成的加密密钥和部署参数填入 `.env`：

| 配置 | 含义 |
| --- | --- |
| `POSTGRES_PASSWORD`、`COWORK_DATABASE_URL` | 数据库密码和连接地址；DSN 中特殊字符使用 URL 编码 |
| `COWORK_HOST`、`COWORK_PUBLIC_URL` | 访问域名及公共基础地址 |
| `COWORK_PLATFORM_ID`、`COWORK_PLATFORM_NAME` | 固定平台标识与显示名称 |
| `COWORK_ENCRYPTION_KEY` | 模型密钥与认证凭据加密密钥 |
| `COWORK_LOGIN_PROVIDER` | 员工默认登录来源，可由网页配置覆盖 |
| `COWORK_SESSION_SECONDS` | 会话期限，默认 8 小时 |
| `COWORK_MAX_MODEL_CONCURRENCY` | 模型并发配置，默认 64 |
| `COWORK_CLEANUP_SECONDS` | 到期提问清理间隔，默认 60 秒 |
| `COWORK_MAX_SPACE_FILE_BYTES`、`COWORK_MAX_SPACE_BYTES` | 团队空间单文件及空间容量配置 |

启动服务：

```bash
sh start.sh
```

启动会检查数据库迁移。随后打开 `/app` 初始化管理员，配置认证、模型与成员授权。

## 初始化与成员管理

1. 首次打开网页，填写平台管理员账号、密码及确认密码。
2. 在“设置 → 添加认证来源”选择平台账号密码、OIDC、OAuth2 或 LDAP。
3. 外部认证保存草稿并完成测试登录后启用；平台账号密码可直接保存启用。
4. 在“客户端统一登录方式”选择员工入口。
5. 在“模型”保存连接参数、测试并启用，再到“成员”分配模型及默认模型。

平台管理员通过网页登录页的独立入口登录。成员页提供角色管理、模型分配和账号状态操作；提问记录页按成员、日期及关键词查询正文。

### 平台账号密码

选择“平台账号密码”认证来源后，进入“管理员工账号”，创建账号、姓名与初始密码，再分配模型。账号列表提供姓名修改、停用和重设密码。员工在桌面点击“登录企业账号”，在平台网页登录后回到客户端。

### OIDC 与命令行导入

网页初始化是常规入口。已有自动部署流程也可在空平台配置 `COWORK_BOOTSTRAP_PROVIDER`、`COWORK_BOOTSTRAP_SUBJECT`，导入认证来源，让对应企业账号完成首次管理员初始化。

创建权限为 `600` 的本地 `bootstrap-auth.json`，例如：

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

执行导入并启动：

```bash
docker compose up -d database
docker compose run --rm platform python -m server migrate
docker compose run --rm -T platform python -m server import-auth - < bootstrap-auth.json
sh start.sh
```

在身份平台注册回调地址 `https://企业域名/auth/callback/company-sso`。完成首次登录后，通过网页管理认证来源，移除初始化配置和导入文件。

### OAuth2 与 LDAP 参数

OAuth2 来源使用 `mode: "oauth2"`。在表单和高级配置中填写 `authorize_url`、`token_url`、`userinfo_url`、`client_id`、`pkce`，客户端密钥放入 secrets。

按身份服务契约设置客户端认证方式 `client_auth`、令牌请求编码 `token_encoding`、`include_redirect_uri`、用户信息请求方法及令牌传递位置。身份字段使用 `subject_token_path`、`name_path`、`email_path`，业务状态通过 `error_field`、`success_values` 映射。

LDAP 使用 `mode: "ldap"`，填写 `host`、`port`、`tls`（`ldaps` 或 `starttls`）、`base_dn`、`user_filter`（如 `(uid={username})`）、`identity_attribute`（如 `entryUUID`），按需配置 `bind_dn` 和容器内 `ca_file`。查询密码保存为 `secrets.bind_password`。

## 子路径与原生进程部署

子路径可配置为 `COWORK_PUBLIC_URL=https://企业域名/cowork`。代理统一处理此前缀，并检查对应的登录、静态资源和下载地址。

原生部署在独立 Python 3.12 环境执行：

```bash
python -m pip install -r requirements.txt
python -m server migrate
python -m server serve --host 127.0.0.1 --port 8080
```

配置相同的 `COWORK_*` 变量并指定绝对路径 `COWORK_DATA_DIR`。使用 systemd 等管理单进程服务，在前方配置 HTTPS 代理。

## 客户端分发

Windows 标准客户端按[构建说明](../../docs/technical-design.md#构建与发布)生成 ZIP 和 `latest-desktop-windows-x64.json` 更新清单（也可使用显式生成的审计报告）。平台管理员执行：

1. 在“设置 → 客户端分发”新建发布，选择“新版工作台（Electron）”，上传 ZIP 和对应更新清单。旧 Qt 包选择“旧版客户端（Qt）”并提供原审计报告。
2. 点击“校验并生成企业包”，等待可下载状态。
3. 下载核对后发布，并设置为推荐版本。
4. 员工下载并完整解压，启动后登录企业账号。

也可点击“从 GitHub 导入最新版”：中心读取正式 Release 的新版工作台清单，从固定版本标签下载标准 ZIP，验证并生成企业包。导入过程可取消，失败保留原因；完成后停在待发布状态，仍需管理员下载核验并发布推荐。

企业包在 EXE 同目录增加公开的 `enterprise_connection.json`，封装后重新计算大小和 SHA-256。推荐版本按客户端结构、系统和架构隔离；下载页默认提供新版工作台，旧客户端仍查询 Qt 推荐。在线客户端安装前会重新确认推荐记录有效；发布列表提供撤下、删除和恢复操作。

## 团队空间与数据库

团队空间提供计划、成员、资料、成果和动态，员工在桌面执行任务并选择发布内容，网页用于管理和阅读共享对象。操作见[用户手册](../../docs/user-guide.md#企业账号与团队空间)。

数据库结构由 `server/database.py` 和 `server/team_space_tables.py` 定义，`platform.schema_version` 记录已完成版本。结构版本 2 增加空间相关表；版本 3 为发布记录增加 `app_layout`，原记录默认 Qt，保留原发布状态和推荐。执行入口为：

```bash
docker compose run --rm platform python -m server migrate
```

文件保存在 `COWORK_DATA_DIR/team-space-files`，使用同一业务文件卷。平台信息接口 `/api/v1/platform-info` 提供能力声明。

## 日常运维

启动或更新代码后运行 `sh start.sh`，使用以下入口查看状态和日志：

```bash
docker compose ps
docker compose logs --tail=100 platform
docker compose stop
```

健康检查为 `/healthz`，平台信息为 `/api/v1/platform-info`。管理页面显示客户端处理状态和提问清理状态。

业务数据库备份使用排除提问正文的命令，并在停止写入期间备份业务文件卷，独立保管加密密钥：

```bash
docker compose exec -T database pg_dump -U cowork -d cowork -Fc --exclude-table-data=questions > cowork-business.dump
```

开发测试入口统一维护在[技术文档](../../docs/technical-design.md#开发与测试)。
