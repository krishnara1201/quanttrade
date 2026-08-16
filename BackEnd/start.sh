#!/bin/sh
set -e
uv run alembic upgrade head
exec uv run uvicorn app:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*'
