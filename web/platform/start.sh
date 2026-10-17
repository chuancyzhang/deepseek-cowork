#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -f .env ]; then
    printf '%s\n' '缺少 .env。请先复制 .env.example 并按 README 完成首次配置。' >&2
    exit 1
fi
if [ ! -f certs/tls.crt ] || [ ! -f certs/tls.key ]; then
    printf '%s\n' '缺少 HTTPS 证书。请将证书和私钥放到 certs/tls.crt、certs/tls.key。' >&2
    exit 1
fi
docker compose up -d --build --wait
docker compose ps
