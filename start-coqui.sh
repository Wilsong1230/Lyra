#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
TTS_ENGINE=coqui venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001
