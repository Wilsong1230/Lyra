#!/bin/bash
trap 'kill $(jobs -p) 2>/dev/null' EXIT
uvicorn server:app --host 0.0.0.0 --port 8000 &
echo "Lyra running at http://localhost:8000"
python mcp_server.py
