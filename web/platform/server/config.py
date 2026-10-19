"""Explicit deployment settings. No embedded credentials or production defaults."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


def service_url(value: str, *, allow_local_http=False, allow_http=False) -> str:
    value = str(value).strip().rstrip("/")
    parts = urlsplit(value)
    if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
            or "\\" in value or any(ord(c) < 32 for c in value)
            or any(p in {".", ".."} for p in parts.path.split("/"))):
        raise ValueError("服务地址必须是无凭据、查询参数或片段的绝对地址。")
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and (allow_http or (allow_local_http and local))):
        raise ValueError("服务地址必须使用 HTTPS；仅显式开发模式允许本机 HTTP。")
    # Force validation of malformed ports now, before any network operation.
    _ = parts.port
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    platform_id: str
    platform_name: str
    public_url: str
    encryption_key: str
    bootstrap_provider: str = ""
    bootstrap_subject: str = ""
    login_provider: str = ""
    development: bool = False
    session_seconds: int = 8 * 60 * 60
    max_model_concurrency: int = 64
    max_request_bytes: int = 32 * 1024 * 1024
    max_archive_bytes: int = 512 * 1024 * 1024
    max_unpacked_bytes: int = 2 * 1024 * 1024 * 1024
    cleanup_seconds: int = 60
    model_read_timeout: int = 660
    allow_model_http: bool = False
    max_space_file_bytes: int = 128 * 1024 * 1024
    max_space_bytes: int = 5 * 1024 * 1024 * 1024

    def __post_init__(self):
        object.__setattr__(self, "data_dir", Path(self.data_dir).resolve())
        object.__setattr__(self, "public_url", service_url(self.public_url, allow_local_http=self.development))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.platform_id):
            raise ValueError("platform_id 格式无效。")
        if not 1 <= len(self.platform_name.strip()) <= 80 or any(ord(c) < 32 for c in self.platform_name):
            raise ValueError("企业名称必须为 1–80 个非控制字符。")
        if not self.database_url.startswith("postgresql+psycopg://"):
            if not (self.development and self.database_url.startswith("sqlite")):
                raise ValueError("正式部署必须配置 PostgreSQL；SQLite 仅用于显式开发与契约测试。")
        if not 300 <= self.session_seconds <= 30 * 86400:
            raise ValueError("平台会话期限须在 5 分钟至 30 天之间。")
        if self.max_model_concurrency < 1 or not 1 <= self.cleanup_seconds <= 300:
            raise ValueError("并发上限或清理周期无效。")
        if not 1 <= self.max_space_file_bytes <= self.max_space_bytes:
            raise ValueError("团队空间单文件上限必须为正数且不超过空间容量。")
        from cryptography.fernet import Fernet
        Fernet(self.encryption_key.encode("ascii"))

    @property
    def path_prefix(self):
        return urlsplit(self.public_url).path

    @property
    def origin(self):
        p = urlsplit(self.public_url)
        return f"{p.scheme}://{p.netloc}"

    @property
    def secure_cookie(self):
        return self.public_url.startswith("https://")

    @classmethod
    def from_env(cls):
        required = ["DATABASE_URL", "DATA_DIR", "PLATFORM_ID", "PLATFORM_NAME", "PUBLIC_URL", "ENCRYPTION_KEY"]
        values = {name: os.environ.get("COWORK_" + name, "") for name in required}
        missing = ["COWORK_" + name for name, value in values.items() if not value]
        if missing:
            raise ValueError("缺少部署配置：" + ", ".join(missing))
        return cls(
            database_url=values["DATABASE_URL"], data_dir=Path(values["DATA_DIR"]),
            platform_id=values["PLATFORM_ID"], platform_name=values["PLATFORM_NAME"],
            public_url=values["PUBLIC_URL"], encryption_key=values["ENCRYPTION_KEY"],
            bootstrap_provider=os.environ.get("COWORK_BOOTSTRAP_PROVIDER", ""),
            bootstrap_subject=os.environ.get("COWORK_BOOTSTRAP_SUBJECT", ""),
            login_provider=os.environ.get("COWORK_LOGIN_PROVIDER", ""),
            development=os.environ.get("COWORK_DEVELOPMENT") == "1",
            session_seconds=int(os.environ.get("COWORK_SESSION_SECONDS", 28800)),
            max_model_concurrency=int(os.environ.get("COWORK_MAX_MODEL_CONCURRENCY", 64)),
            cleanup_seconds=int(os.environ.get("COWORK_CLEANUP_SECONDS", 60)),
            allow_model_http=os.environ.get("COWORK_ALLOW_MODEL_HTTP") == "1",
            max_space_file_bytes=int(os.environ.get("COWORK_MAX_SPACE_FILE_BYTES", 128 * 1024 * 1024)),
            max_space_bytes=int(os.environ.get("COWORK_MAX_SPACE_BYTES", 5 * 1024 * 1024 * 1024)),
        )
