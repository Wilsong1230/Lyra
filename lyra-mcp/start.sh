#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export EMBODIMENT_URL="${EMBODIMENT_URL:-http://localhost:8000}"
export VOICE_URL="${VOICE_URL:-http://localhost:8001}"
export LISTEN_URL="${LISTEN_URL:-http://localhost:8002}"
venv/bin/python mcp_server.py
