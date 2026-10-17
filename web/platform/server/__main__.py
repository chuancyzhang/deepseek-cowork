"""Deployment and bootstrap commands for the independent enterprise service."""
import argparse
import json
import logging
from pathlib import Path
import sys

from .config import Settings
from .database import Database
from .security import Vault


def main():
    parser = argparse.ArgumentParser(description="Cowork 企业平台")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    sub.add_parser("migrate")
    sub.add_parser("generate-key")
    auth = sub.add_parser("import-auth")
    auth.add_argument("file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for logger in ("httpx", "httpcore"):
        logging.getLogger(logger).setLevel(logging.WARNING)
    if args.command == "generate-key":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return
    settings = Settings.from_env()
    if args.command == "serve":
        import uvicorn
        from .app import create_app
        # One process owns release jobs and cleanup. Async model streams share it.
        # Access logs are disabled: OAuth query parameters can contain credentials.
        uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1,
                    access_log=False, proxy_headers=False)
        return
    db = Database(settings)
    try:
        db.migrate(settings)
        if args.command == "import-auth":
            if not settings.bootstrap_provider or not settings.bootstrap_subject:
                parser.error("首次导入前必须配置预先指定的管理员 provider 与稳定 subject。")
            from .auth import Authentication
            raw = sys.stdin.read(65537) if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
            if len(raw.encode("utf-8")) > 65536:
                parser.error("认证配置超过 64 KiB。")
            payload = json.loads(raw)
            Authentication(settings, db, Vault(settings.encryption_key), None).save_source(payload, bootstrap=True)
            print("认证来源已导入。仅预先指定的管理员可完成首次认证；测试成功后其他成员才可加入。")
        else:
            print("数据库结构版本 1 已就绪。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
