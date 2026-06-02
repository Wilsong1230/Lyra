#!/bin/bash
cd "$(dirname "$0")"
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000
echo "lyra-embodiment running at http://localhost:8000"
