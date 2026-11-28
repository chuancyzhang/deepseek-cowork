"""Build a self-contained enterprise deployment from an audited source snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.app_version import APP_VERSION

PLATFORM = ROOT / "web/platform"
ENTERPRISE_DOCS = ROOT / "docs/enterprise"
FILES = ("Dockerfile", "Dockerfile.dockerignore", "requirements.txt", "compose.local.yaml",
         "compose.yaml", "compose.http.yaml", "Caddyfile", "start-local.ps1", "start.sh", ".env.example", "README.md")
BUNDLE_GUIDES = {"deployment.md": "DEPLOYMENT.md", "admin-guide.md": "ADMIN_GUIDE.md"}
GENERATED = {"images.tar", "deployment.json", "SHA256SUMS", *BUNDLE_GUIDES.values()}
MARKDOWN_LINK = re.compile(r'(?P<image>!?)\[(?P<label>[^\]\r\n]*)\]\((?P<target><[^>\r\n]+>|[^()\s]+)(?P<title> +"[^"\r\n]*")?\)')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_source(source, destination):
    """An allowlist excludes deployment credentials, volumes and Python caches."""
    paths = [source / name for name in FILES]
    paths += sorted((source / "server").glob("*.py"))
    paths += sorted(path for path in (source / "ui").iterdir() if path.suffix in {".js", ".css", ".html"})
    hashes = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"部署源码不是常规文件：{path}")
        name = path.relative_to(source).as_posix()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[name] = sha256(target)
    return hashes


def source_digest(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_bundle_guides(source_files):
    """Keep links that are usable when the deployment package stands alone."""
    guides = {ENTERPRISE_DOCS / source: name for source, name in BUNDLE_GUIDES.items()}
    for path in guides:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"企业交付指南不是常规文件：{path}")
    packaged_links = {(PLATFORM / name).resolve(): name for name in source_files}
    packaged_links.update({path.resolve(): name for path, name in guides.items()})

    def render(path):
        def rewrite(match):
            target = urlsplit(match["target"].strip("<>"))
            if target.scheme or target.netloc or not target.path:
                return match[0]
            bundled = packaged_links.get((path.parent / unquote(target.path)).resolve())
            if bundled:
                link = urlunsplit(("", "", bundled, target.query, target.fragment))
                return f'{match["image"]}[{match["label"]}]({link}{match["title"] or ""})'
            return "" if match["image"] else f'{match["label"]}（仓库文档）'

        return MARKDOWN_LINK.sub(rewrite, path.read_text(encoding="utf-8"))

    return {name: render(path) for path, name in guides.items()}


def docker(*args, capture=False):
    result = subprocess.run(["docker", *args], check=True, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE if capture else None,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    return result.stdout.strip() if capture else ""


def inspect_image(tag):
    value = json.loads(docker("image", "inspect", tag, capture=True))[0]
    if (value["Os"], value["Architecture"]) != ("linux", "amd64"):
        raise ValueError(f"镜像不是 linux/amd64：{tag}")
    return value


def audit_image(tag, hashes, digest):
    value = inspect_image(tag)
    labels = value["Config"].get("Labels") or {}
    if labels.get("io.cowork.source-sha256") != digest or labels.get("org.opencontainers.image.version") != APP_VERSION:
        raise ValueError("镜像与本次源码或版本不一致。")
    inputs = {name: checksum for name, checksum in hashes.items()
              if name.startswith(("server/", "ui/")) or name == "requirements.txt"}
    code = """
import hashlib,json,pathlib
expected=json.loads(__import__('sys').argv[1])
actual={name:hashlib.sha256(pathlib.Path('/app',name).read_bytes()).hexdigest() for name in expected}
assert actual==expected, '镜像源码与打包快照不一致'
from server.database import SCHEMA_VERSION
from server.releases import DESKTOP_REQUIRED_FILES
assert 'desktop-release.json' in DESKTOP_REQUIRED_FILES
print(f'ENTERPRISE_IMAGE_AUDIT_OK:{SCHEMA_VERSION}')
"""
    output = docker("run", "--rm", "--network", "none", tag, "python", "-c", code,
                    json.dumps(inputs), capture=True)
    marker, _, schema = output.partition(":")
    if marker != "ENTERPRISE_IMAGE_AUDIT_OK" or not schema.isdecimal():
        raise ValueError("企业镜像校验未完成。")
    return value["Id"], int(schema)


def validate_destination(destination):
    """Never replace a deployment that contains local credentials or business data."""
    if not destination.exists():
        return
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("交付目录必须是常规目录。")
    allowed = set(FILES) | GENERATED
    for path in destination.rglob("*"):
        name = path.relative_to(destination).as_posix()
        if path.is_symlink() or (path.is_file() and name not in allowed
                                and not (path.parent == destination / "server" and path.suffix == ".py")
                                and not (path.parent == destination / "ui" and path.suffix in {".js", ".css", ".html"})):
            raise ValueError(f"交付目录含本机配置或未知内容，不能覆盖：{name}")
        if path.is_dir() and name not in {"server", "ui"}:
            raise ValueError(f"交付目录含非构建目录，不能覆盖：{name}")


def package(destination, archive):
    destination, archive = destination.resolve(), archive.resolve()
    if destination == PLATFORM.resolve() or destination in PLATFORM.resolve().parents:
        raise ValueError("不能将源码目录作为交付目录。")
    if archive == destination or destination in archive.parents:
        raise ValueError("ZIP 必须放在交付目录之外。")
    validate_destination(destination)
    docker("info", "--format", "{{.OSType}}", capture=True)
    temporary = ROOT / ".tmp/enterprise-package"
    temporary.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary) as folder:
        work = Path(folder)
        bundle = work / "enterprise-docker"
        bundle.mkdir()
        hashes = copy_source(PLATFORM, bundle)
        guides = read_bundle_guides(hashes)
        digest = source_digest(hashes)
        tag = f"cowork-enterprise:{APP_VERSION}-{digest[:12]}"
        print(f"构建企业中心 {APP_VERSION} source={digest}", flush=True)
        docker("build", "--platform", "linux/amd64", "--tag", tag,
               "--build-arg", f"COWORK_BUILD_VERSION={APP_VERSION}",
               "--build-arg", f"COWORK_BUILD_SOURCE={digest}", str(bundle))
        image_id, database_schema = audit_image(tag, hashes, digest)
        # An existing database image is deliberately reused; packaging never upgrades PostgreSQL.
        database = inspect_image("postgres:17")
        database_tag = "cowork-postgres:17-" + database["Id"].split(":")[1][:12]
        docker("tag", database["Id"], database_tag)
        docker("save", "--output", str(bundle / "images.tar"), tag, database_tag)
        for name, content in guides.items():
            (bundle / name).write_text(content, encoding="utf-8")
            hashes[name] = sha256(bundle / name)
        (bundle / "README.md").write_text(f"""# Cowork 企业中心离线部署包

版本：{APP_VERSION}。在 Linux 服务器上完整解压，进入含 `compose.yaml` 的目录。

- **x86_64**：可校验并导入 `images.tar` 中的后台与 PostgreSQL 镜像。离线 HTTPS 部署另需准备同架构的 `caddy:2`。
- **ARM64**：在目标服务器使用随包源码和 Docker Compose 原生构建。构建时需要可访问的镜像源和 Python 包源。

在[部署指南](DEPLOYMENT.md)选择服务器架构和访问方式，配置 `.env`，启动后检查企业地址下的 `/healthz`。升级前备份数据库与业务文件，保留原加密密钥和 Compose 项目名。

管理员从 `/app` 初始化平台，配置部门、认证和资源授权。操作见[管理员使用手册](ADMIN_GUIDE.md)。
""", encoding="utf-8")
        hashes["README.md"] = sha256(bundle / "README.md")
        hashes["images.tar"] = sha256(bundle / "images.tar")
        manifest = {"schema_version": 1, "version": APP_VERSION, "source_sha256": digest,
                    "target": "linux-amd64", "database_schema": database_schema,
                    "platform_image": tag, "platform_image_id": image_id,
                    "database_image": database_tag, "database_image_id": database["Id"], "files": hashes}
        (bundle / "deployment.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        checksums = {**hashes, "deployment.json": sha256(bundle / "deployment.json")}
        (bundle / "SHA256SUMS").write_text("".join(f"{value}  {name}\n" for name, value in sorted(checksums.items())), encoding="utf-8")
        packed = work / archive.name
        with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    output.write(path, "enterprise-docker/" + path.relative_to(bundle).as_posix())
        with zipfile.ZipFile(packed) as check:
            if check.testzip() is not None:
                raise ValueError("交付 ZIP 完整性校验失败。")
        validate_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        archive.parent.mkdir(parents=True, exist_ok=True)
        backup = work / "previous-output"
        if destination.exists():
            destination.replace(backup)
        try:
            bundle.replace(destination)
            packed.replace(archive)
        except BaseException:
            if destination.exists():
                destination.replace(work / "failed-output")
            if backup.exists():
                backup.replace(destination)
            raise
        print(f"企业部署包：{archive}\nSHA-256：{sha256(archive)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/enterprise-docker")
    parser.add_argument("--archive", type=Path, default=ROOT / "dist/cowork-enterprise-docker.zip")
    args = parser.parse_args()
    package(args.output_dir, args.archive)


if __name__ == "__main__":
    main()
