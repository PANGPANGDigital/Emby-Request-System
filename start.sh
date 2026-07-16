#!/usr/bin/env bash
set -euo pipefail

if [ ! -f .env ]; then
  if command -v python3 >/dev/null 2>&1; then
    python3 scripts/init_env.py
  elif command -v python >/dev/null 2>&1; then
    python scripts/init_env.py
  else
    echo "首次生成 .env 需要 Python 3；请安装 Python 3，或将 .env.example 复制为 .env 后手动填写配置。" >&2
    exit 1
  fi
fi

docker compose up -d --build
