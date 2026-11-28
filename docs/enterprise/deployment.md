# DeepSeek Cowork 企业管理后台 · Linux 部署

文档适用版本：**5.4.2**

本指南在一台 Linux 服务器上部署管理后台、PostgreSQL 和业务文件卷。HTTPS 入口由 Caddy 提供。部署完成后，管理员在浏览器打开企业地址的 `/app`。

## 选择服务器架构

在目标服务器运行以下命令，确认 Docker 连接到这台机器上的 Linux 引擎，并已安装支持 `up --wait` 的 Compose 插件。

```bash
uname -m
docker info --format '{{.OSType}}/{{.Architecture}}'
docker compose version
```

| Linux 服务器 | Docker 平台 | 准备后台镜像的方式 |
| --- | --- | --- |
| x86_64（Intel、AMD） | `linux/amd64` | 可在服务器上原生构建，也可直接导入同版本交付包内的镜像。 |
| ARM64（`uname -m` 显示 `aarch64`） | `linux/arm64` | 在 ARM64 服务器上用随包源码或仓库的 `web/platform/` 原生构建。当前交付包的 `images.tar` 仅含 amd64 镜像。 |

## 准备目录与访问地址

将同版本仓库的整个 `web/platform/` 目录复制到服务器，例如 `/opt/cowork-platform/`。使用交付 ZIP 时，完整解压后进入包含 `compose.yaml`、`Dockerfile` 和 `.env.example` 的 `enterprise-docker/` 目录。以下命令均在该部署目录执行。

```bash
umask 077
test -e .env || cp .env.example .env
chmod 600 .env
```

编辑 `.env`，先填写下列配置。升级已有中心时沿用原配置。

| 配置项 | 填写内容 |
| --- | --- |
| `POSTGRES_PASSWORD` | 新库使用随机密码，例如用 `openssl rand -hex 32` 生成。 |
| `COWORK_DATABASE_URL` | 保留模板中的主机 `database`、端口 `5432`、用户及库名 `cowork`，将密码改为上面同一个值。密码如含特殊字符，须在此连接串中进行 URL 编码。 |
| `COWORK_PLATFORM_ID`、`COWORK_PLATFORM_NAME` | 分别填写固定平台标识和员工看到的企业名称。 |
| `COWORK_ENCRYPTION_KEY` | 在镜像就绪后生成并填入，用于加密模型和认证凭据。已有中心继续使用原密钥。 |

**域名与 HTTPS**　将域名解析到服务器，开放 TCP 443，将证书链与私钥分别放在 `certs/tls.crt`、`certs/tls.key`。管理员和员工的设备都需要信任签发机构。在 `.env` 中设置：

```dotenv
COMPOSE_FILE=compose.yaml
COWORK_HOST=cowork.example.internal
COWORK_PUBLIC_URL=https://cowork.example.internal
COWORK_ALLOW_PLATFORM_HTTP=0
```

**内网 IP 与 HTTP**　仅用于可信内网，账号和请求会通过明文 HTTP 传输。在下面的示例中，将占位符替换为绑定在服务器网卡上、员工可访问的 IPv4。选择并开放访问端口。此模式只启动后台和数据库。

```dotenv
COMPOSE_FILE=compose.yaml:compose.http.yaml
COWORK_HOST=<服务器内网IPv4>
COWORK_HTTP_PORT=8788
COWORK_ALLOW_PLATFORM_HTTP=1
```

HTTP 配置按上述 IP 和端口设置服务实际使用的 `COWORK_PUBLIC_URL`。两种模式下的登录回调和企业下载地址都以公共地址为准。

首次部署可沿用 `.env.example` 中的运行参数。需要调整时，`COWORK_SESSION_SECONDS` 控制会话期限，`COWORK_MAX_MODEL_CONCURRENCY` 控制模型并发，`COWORK_CLEANUP_SECONDS` 控制审计清理间隔。团队空间的单文件及总容量上限分别由 `COWORK_MAX_SPACE_FILE_BYTES`、`COWORK_MAX_SPACE_BYTES` 设置。

## 准备后台镜像

### x86_64 和 ARM64 原生构建

在目标服务器执行。同一份 Dockerfile 使用 `python:3.12-slim`，按当前 Docker 引擎的架构安装 `requirements.txt` 中固定版本的依赖。构建机需要能访问容器镜像源与 Python 包源。Compose 使用 `postgres:17`，HTTPS 模式另需 `caddy:2`。

```bash
unset DOCKER_DEFAULT_PLATFORM
docker compose config --quiet
docker compose build platform
docker compose pull --ignore-buildable
docker compose run --rm --no-deps --pull never platform uname -m
```

最后一条在后台容器中执行。x86_64 服务器应显示 `x86_64`，ARM64 应显示 `aarch64`。接着按“启动与验收”操作。

### x86_64 使用交付包离线部署

在 x86_64 服务器安装 `jq`，进入同版本交付包的解压目录。以下命令校验包内文件，核对两个镜像的 ID 和架构，并生成 Compose 使用的镜像配置。包中镜像的标签以 `deployment.json` 为准。

```bash
(
  set -eu
  sha256sum --check --quiet SHA256SUMS
  test "$(jq -r .target deployment.json)" = linux-amd64
  docker load -i images.tar
  for service in platform database; do
    image=$(jq -er --arg key "${service}_image" '.[$key]' deployment.json)
    id=$(jq -er --arg key "${service}_image_id" '.[$key]' deployment.json)
    actual=$(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$image")
    if [ "$actual" != "$id linux/amd64" ]; then
      echo "$service 镜像 ID 或架构与交付清单不符" >&2
      exit 1
    fi
  done
  jq '{services:{platform:{image:.platform_image},database:{image:.database_image}}}' deployment.json > compose.images.yaml
)
```

校验成功后，在 `.env` 的 `COMPOSE_FILE` 行末追加 `:compose.images.yaml`。例如内网 HTTP 使用 `COMPOSE_FILE=compose.yaml:compose.http.yaml:compose.images.yaml`。

HTTPS 还需单独准备 Caddy。可在有网络的机器执行 `docker pull --platform linux/amd64 caddy:2` 和 `docker save -o caddy-amd64.tar caddy:2`，把归档传到目标服务器，执行 `docker load -i caddy-amd64.tar`。内网 HTTP 可直接进入下一步。

## 启动与验收

首次安装先执行以下命令，将输出完整填入 `.env` 中的 `COWORK_ENCRYPTION_KEY` 并保存。

```bash
docker compose run --rm --no-deps --pull never platform python -m server generate-key
```

两种架构在镜像就绪后使用相同命令。应用启动时会连接数据库并执行迁移。

```bash
docker compose config --quiet
docker compose up -d --no-build --pull never --wait
docker compose ps
```

从管理员的机器访问实际地址。将下例域名替换为企业域名；内网 HTTP 使用 `http://<服务器内网IPv4>:<访问端口>`。使用企业内部 CA 时，先在访问机器上信任该 CA。

```bash
PUBLIC_URL=https://cowork.example.internal
curl --fail --show-error --silent "$PUBLIC_URL/healthz"
```

健康接口应返回 `"status":"ok"` 和数据库结构版本。打开同一地址的 `/app`，创建超级管理员，再配置企业登录方式、部门与模型授权。配置步骤见[管理员指南](admin-guide.md#初始化与成员管理)。

## 子路径与原生进程部署

子路径可配置为 `COWORK_PUBLIC_URL=https://企业域名/cowork`。代理统一处理此前缀，并检查对应的登录、静态资源和下载地址。

直接运行 Python 进程时，先准备 PostgreSQL 及可用的加密密钥，将 `COWORK_DATABASE_URL` 指向进程可达的数据库主机，设置可写的绝对路径 `COWORK_DATA_DIR`，并将实际 `COWORK_*` 配置导入进程环境。在独立 Python 3.12 环境执行：

```bash
python -m pip install -r requirements.txt
python -m server migrate
python -m server serve --host localhost --port 8080
```

使用 systemd 等管理单进程服务，在前方配置 HTTPS 代理。

## 命令行导入认证来源

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
docker compose up -d --no-build --pull never --wait database
docker compose run --rm --no-deps --pull never platform python -m server migrate
docker compose run --rm --no-deps --pull never -T platform python -m server import-auth - < bootstrap-auth.json
docker compose up -d --no-build --pull never --wait
```

在身份平台注册回调地址 `https://企业域名/auth/callback/company-sso`。完成首次登录后可移除导入文件。如果没有通过网页创建本地超级管理员，保留 `COWORK_BOOTSTRAP_PROVIDER` 与 `COWORK_BOOTSTRAP_SUBJECT`，用它们指向已启用的企业管理员。平台通过该账号授予或收回管理员角色，其他管理员可以继续管理普通资源。

## 数据库与业务文件

团队空间在 Cowork 桌面端提供计划、成员、资料、成果和动态。员工在桌面执行任务并选择发布内容。操作见[员工指南](employee-guide.md#共同完成任务)。

`platform.schema_version` 记录已完成的数据库迁移。结构版本 6 增加部门层级、员工归属及模型和能力的部门授权，保留原有个人授权。结构版本 5 增加企业能力及聊天审计表，保留成员、模型、团队空间、业务文件和 Electron 发布记录。结构版本 4 开始只支持 Electron 分发，旧 Qt 记录保留但停止分发。执行入口为：

```bash
docker compose run --rm platform python -m server migrate
```

文件保存在 `COWORK_DATA_DIR/team-space-files`，使用同一业务文件卷。平台信息接口 `/api/v1/platform-info` 提供能力声明。

## 日常运维

所有运维命令均在原部署目录执行，Compose 会读取 `.env` 的 `COMPOSE_FILE`。用以下命令查看服务和最近的启动日志。

```bash
docker compose ps
docker compose logs --tail=100 platform database
```

**备份。** 停止后台写入，保留数据库运行，导出业务库。以下备份按审计保留策略排除聊天请求快照与历史提问正文。同步备份挂载到 `/data` 的 `releases` 卷，并单独保管 `.env` 中的原加密密钥及 HTTPS 证书。

```bash
docker compose stop platform
docker compose exec -T database pg_dump -U cowork -d cowork -Fc --exclude-table-data=questions --exclude-table-data=chat_audit_requests > cowork-business.dump
```

业务备份完成后，执行 `docker compose up -d --no-build --pull never --wait` 恢复服务。

**升级。** 备份后，在同一部署目录更新后台源码并执行 `docker compose build platform`；使用 x86_64 离线包则按上文核验新包并重新生成 `compose.images.yaml`。保留原 `.env`、证书、Compose 项目名及卷名，再运行“启动与验收”中的启动和健康检查命令。若需回退数据库迁移，同时恢复升级前的数据库、业务文件和匹配的后台版本。
