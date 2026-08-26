#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
trap 'kill $(jobs -p) 2>/dev/null' EXIT
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8002 &
venv/bin/uvicorn ambient_service:app --host 0.0.0.0 --port 8004 &
venv/bin/uvicorn wakeword_service:app --host 0.0.0.0 --port 8005
