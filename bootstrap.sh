#!/usr/bin/env bash
# Set up every Lyra service on a fresh machine.
#   ./bootstrap.sh            # all services
#   ./bootstrap.sh lyra-voice # just one
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
PY="${PYTHON:-python3}"

SERVICES=(lyra-embodiment lyra-voice lyra-listen lyra-vision lyra-mcp)
TARGETS=("${@:-${SERVICES[@]}}")

command -v "$PY" >/dev/null || { echo "error: $PY not found"; exit 1; }
echo "==> Using $("$PY" --version)"

# .env from template on first run
if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "==> Created .env from .env.example — fill in your API keys before running Lyra."
fi
if [[ ! -f litellm_config.yaml && -f litellm_config.example.yaml ]]; then
  cp litellm_config.example.yaml litellm_config.yaml
  echo "==> Created litellm_config.yaml from example."
fi

# Core packages first — services and the CLI depend on them.
echo "==> lyra-memory (editable)"
"$PY" -m venv lyra-memory/venv
lyra-memory/venv/bin/pip install -q --upgrade pip
lyra-memory/venv/bin/pip install -q -e lyra-memory

echo "==> lyra_ai (editable, with lyra-memory)"
"$PY" -m venv lyra_ai/venv
lyra_ai/venv/bin/pip install -q --upgrade pip
lyra_ai/venv/bin/pip install -q -e "lyra_ai[dev]" -e lyra-memory

for svc in "${TARGETS[@]}"; do
  [[ -d "$svc" ]] || { echo "!! no such service: $svc"; continue; }
  echo "==> $svc"
  "$PY" -m venv "$svc/venv"
  "$svc/venv/bin/pip" install -q --upgrade pip
  if [[ -f "$svc/requirements.txt" ]]; then
    # lyra-mcp depends on lyra-memory by relative path; install it explicitly.
    "$svc/venv/bin/pip" install -q -e "$ROOT/lyra-memory"
    grep -v '^lyra-memory' "$svc/requirements.txt" > /tmp/req-$$.txt || true
    "$svc/venv/bin/pip" install -q -r /tmp/req-$$.txt
    rm -f /tmp/req-$$.txt
  fi
done

cat <<'DONE'

==> Bootstrap complete.

Next:
  1. Edit .env and add your API keys (see .env.example for the list).
  2. Start the services you need:
       ./lyra-embodiment/start.sh   # :8000  avatar
       ./lyra-voice/start.sh        # :8001  TTS
       ./lyra-listen/start.sh       # :8002  STT
       ./lyra-vision/start.sh       # :8003  vision
  3. Run the assistant:
       lyra_ai/venv/bin/lyra
DONE
