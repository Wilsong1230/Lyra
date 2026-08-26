#!/bin/bash
cd "$(dirname "$0")"
venv/bin/uvicorn vision_service:app --host 0.0.0.0 --port 8003
