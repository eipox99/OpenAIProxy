#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
# Use --reload for development only; remove it for production
exec python -m uvicorn openproxy.main:app --host 0.0.0.0 --port 8000 ${RELOAD:---reload}
