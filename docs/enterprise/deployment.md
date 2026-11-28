# DeepSeek Cowork 企业管理后台 · Linux 部署指南

文档适用版本：**5.4.3**

本指南帮助运维人员在一台 Linux 服务器上通过 Docker Compose（或原生 Python 进程）部署 DeepSeek Cowork 企业管理后台、PostgreSQL 数据库与持久化业务文件卷。部署完成后，把企业地址下的 `/app` 交给平台管理员完成初始化即可。

## 1. 确认服务器架构与 Docker 环境

在目标 Linux 服务器上执行以下命令，确认 Docker 引擎正常运行且已安装支持 `up --wait` 的 Docker Compose v2 插件：

```bash
uname -m
docker info --format '{{.OSType}}/{{.Architecture}}'
docker compose version
```

| 服务器 CPU 架构 | Docker 平台标识 | 镜像准备方式 |
| --- | --- | --- |
| **x86_64**（Intel / AMD） | `linux/amd64` | 既可使用离线交付包直接导入预构建镜像，也可在服务器上原生构建 |
| **ARM64**（`aarch64`） | `linux/arm64` | 使用随包源码或仓库 `web/platform/` 目录在目标 ARM64 服务器上原生构建（当前离线交付包中的 `images.tar` 仅包含 `amd64` 镜像） |

## 2. 准备部署目录与环境变量

将同版本仓库的整个 `web/platform/` 目录复制到服务器（例如 `/opt/cowork-platform/`；若使用企业后台交付压缩包，完整解压后进入含 `compose.yaml`、`Dockerfile` 和 `.env.example` 的 `enterprise-docker/` 目录）。后续所有命令均在该部署目录下执行：

```bash
umask 077
test -e .env || cp .env.example .env
chmod 600 .env
```

编辑 `.env` 填写基础参数（升级已有平台时直接保留原 `.env` 即可）：

| 配置项 | 说明 |
| --- | --- |
| `POSTGRES_PASSWORD` | 数据库随机强密码（新部署可用 `openssl rand -hex 32` 生成） |
| `COWORK_DATABASE_URL` | 保持模板中的主机 `database`、端口 `5432`、库名与用户名 `cowork`，将密码替换为上方同一值（若密码含特殊字符需做 URL 编码） |
| `COWORK_PLATFORM_ID` | 平台唯一固定标识（选定后请勿随意更改，桌面客户端凭此识别所属企业） |
| `COWORK_PLATFORM_NAME` | 员工在桌面端与网页上看到的企业名称 |
| `COWORK_ENCRYPTION_KEY` | 用于加密数据库内模型密钥与认证凭据的主密钥（在第 4 步镜像就绪后生成填入；升级时严禁更换） |

接着按实际网络环境二选一配置访问入口（登录回调与客户端下载地址均以最终公共地址为准）：

- **方式 A：标准域名 + HTTPS（推荐生产环境使用，由内置 Caddy 提供 TLS）**
  将域名解析到服务器并开放 TCP `443` 端口，把证书链与私钥分别放入部署目录下的 `certs/tls.crt` 和 `certs/tls.key`（若使用企业内网自签 CA，需确保管理员与员工电脑均已信任该根证书），在 `.env` 中设置：

  ```dotenv
  COMPOSE_FILE=compose.yaml
  COWORK_HOST=cowork.example.internal
  COWORK_PUBLIC_URL=https://cowork.example.internal
  COWORK_ALLOW_PLATFORM_HTTP=0
  ```

- **方式 B：内网 IP + HTTP（仅限受信任的封闭内网环境）**
  注意明文 HTTP 会直接传输登录态与请求内容，切勿暴露在公网。开放选定的访问端口（如 `8788`），在 `.env` 中设置（此模式仅启动后台与数据库，无需启动 Caddy）：

  ```dotenv
  COMPOSE_FILE=compose.yaml:compose.http.yaml
  COWORK_HOST=<服务器内网IPv4>
  COWORK_HTTP_PORT=8788
  COWORK_ALLOW_PLATFORM_HTTP=1
  ```

> **可选容量与并发调优**：首次部署保持 `.env.example` 默认值即可；后续可按需调整 `COWORK_SESSION_SECONDS`（登录有效期）、`COWORK_MAX_MODEL_CONCURRENCY`（模型代理并发上限）、`COWORK_CLEANUP_SECONDS`（过期审计清理周期），以及团队空间单文件与总容量上限 `COWORK_MAX_SPACE_FILE_BYTES`、`COWORK_MAX_SPACE_BYTES`。

## 3. 准备容器镜像

### 路线一：在 x86_64 或 ARM64 服务器上原生构建

构建机需能访问基础容器镜像源（`python:3.12-slim`、`postgres:17`，HTTPS 模式另需 `caddy:2`）与 Python 包源：

```bash
unset DOCKER_DEFAULT_PLATFORM
docker compose config --quiet
docker compose build platform
docker compose pull --ignore-buildable
docker compose run --rm --no-deps --pull never platform uname -m
```

确认最后一行输出的架构与宿主机一致（`x86_64` 或 `aarch64`），即可进入第 4 步。

### 路线二：在 x86_64 服务器上使用离线交付包部署

服务器安装 `jq` 后进入离线包解压目录，执行以下脚本校验文件完整性、导入镜像并生成固定镜像映射 `compose.images.yaml`：

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

校验通过后，在 `.env` 的 `COMPOSE_FILE` 末尾追加 `:compose.images.yaml`（例如 HTTPS 模式为 `COMPOSE_FILE=compose.yaml:compose.images.yaml`，内网 HTTP 模式为 `COMPOSE_FILE=compose.yaml:compose.http.yaml:compose.images.yaml`）。若采用 HTTPS 模式，还需在联网机器拉取导出 `caddy:2`（`docker pull --platform linux/amd64 caddy:2 && docker save -o caddy-amd64.tar caddy:2`）并拷入服务器 `docker load -i caddy-amd64.tar`。

## 4. 启动与健康检查验收

**首次全新安装时**，先生成加密主密钥并完整填入 `.env` 的 `COWORK_ENCRYPTION_KEY`：

```bash
docker compose run --rm --no-deps --pull never platform python -m server generate-key
```

随后启动全部服务（启动时会自动连接数据库并按序完成表结构迁移）：

```bash
docker compose config --quiet
docker compose up -d --no-build --pull never --wait
docker compose ps
```

在管理员电脑或服务器上请求健康检查接口（将地址替换为你的实际 HTTPS 域名或 `http://<内网IP>:<端口>`）：

```bash
PUBLIC_URL=https://cowork.example.internal
curl --fail --show-error --silent "$PUBLIC_URL/healthz"
```

返回包含 `"status":"ok"` 及当前数据库结构版本号即代表部署成功。现在可以将 `$PUBLIC_URL/app` 交给管理员，按[管理员指南](admin-guide.md#初始化与成员管理)完成首次初始化。

## 进阶部署方式

### 挂载反向代理子路径或直接运行原生进程

- **子路径代理**：支持将公共地址设为带前缀路径（如 `COWORK_PUBLIC_URL=https://example.internal/cowork`），由前置反向代理统一转发该前缀并验证登录回调、静态资源与 `/downloads` 下载页可达。
- **无容器原生运行**：准备好 PostgreSQL、可写的绝对路径 `COWORK_DATA_DIR` 及全套 `COWORK_*` 环境变量后，在独立的 Python 3.12 虚拟环境中执行：

  ```bash
  python -m pip install -r requirements.txt
  python -m server migrate
  python -m server serve --host localhost --port 8080
  ```

  生产环境请通过 `systemd` 托管进程，并在前方配置 HTTPS 反向代理。

### 命令行导入认证来源

除网页引导初始化外，自动化运维流水线也可以在空库状态下配置 `COWORK_BOOTSTRAP_PROVIDER` 与 `COWORK_BOOTSTRAP_SUBJECT`，预先导入 SSO 认证配置，让指定的企业管理员账号首次登录即获得管理权限。

先创建权限为 `600` 的 `bootstrap-auth.json`：

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

执行迁移与导入并启动服务：

```bash
docker compose up -d --no-build --pull never --wait database
docker compose run --rm --no-deps --pull never platform python -m server migrate
docker compose run --rm --no-deps --pull never -T platform python -m server import-auth - < bootstrap-auth.json
docker compose up -d --no-build --pull never --wait
```

在身份平台侧将回调地址注册为 `https://企业域名/auth/callback/company-sso`（首次登录成功后即可删除本地的 `bootstrap-auth.json`；若未创建本地超级管理员，请保留 `.env` 中的 `COWORK_BOOTSTRAP_PROVIDER` 与 `COWORK_BOOTSTRAP_SUBJECT`）。

## 数据存储与迁移说明

- **数据库迁移记录**：`platform.schema_version` 记录当前已完成的数据库结构版本（版本 4 切换为纯 Electron 客户端分发，版本 5 新增企业能力与聊天审计表，版本 6 新增部门树与资源分级授权并兼容保留原个人授权）。手动执行迁移命令为 `docker compose run --rm platform python -m server migrate`。
- **业务文件卷**：团队空间的共享文件存放在 `COWORK_DATA_DIR/team-space-files`，客户端发布包存放在 `releases` 目录，均位于持久化数据卷中（员工桌面协作方式见[员工指南](employee-guide.md#共同完成任务)）。

## 日常运维

所有运维命令均在原部署目录下执行：

```bash
docker compose ps
docker compose logs --tail=100 platform database
```

- **安全备份**：为保证数据一致性，备份前先短暂停止 `platform` 容器写入，导出 PostgreSQL 业务库（下列命令默认排除临时提问表与 30 天滚动的聊天请求审计快照），同时备份挂载到 `/data` 的持久化业务文件卷，并将 `.env`（尤其是 `COWORK_ENCRYPTION_KEY`）与 `certs/` 证书妥善异地保管：

  ```bash
  docker compose stop platform
  docker compose exec -T database pg_dump -U cowork -d cowork -Fc --exclude-table-data=questions --exclude-table-data=chat_audit_requests > cowork-business.dump
  docker compose up -d --no-build --pull never --wait
  ```

- **平滑升级与回滚**：先按上一步完成完整备份；随后在原部署目录更新同版本代码重新 `docker compose build platform`（x86_64 离线包则按第 3 步核验新包并更新 `compose.images.yaml`），保持原有的 `.env`、证书、Compose 项目名和卷名不变，执行第 4 步的 `up -d --wait` 与 `/healthz` 检查即可自动完成数据库平滑升级。万一需要回退版本，须同时恢复升级前备份的数据库、业务文件卷以及对应旧版镜像。
