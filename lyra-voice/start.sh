#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001
