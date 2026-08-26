#!/usr/bin/env bash
# Set up every Lyra service on a fresh machine.
#   ./bootstrap.sh            # all services
#   ./bootstrap.sh lyra-voice # just one
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
PY="${PYTHON:-python3}"

SERVICES=(lyra-embodiment lyra-voice lyra-listen lyra-vision lyra-mcp)
# No args: full setup (core packages + every service).
# With args: only what was named, so a single service can be rebuilt cheaply.
if [[ $# -eq 0 ]]; then
  TARGETS=("${SERVICES[@]}"); DO_CORE=1
else
  TARGETS=("$@"); DO_CORE=0
  for t in "$@"; do [[ "$t" == lyra_ai || "$t" == lyra-memory ]] && DO_CORE=1; done
  TARGETS=($(printf '%s\n' "${TARGETS[@]}" | grep -vE '^(lyra_ai|lyra-memory)$' || true))
fi

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
if [[ "$DO_CORE" == 1 ]]; then
echo "==> lyra-memory (editable)"
"$PY" -m venv lyra-memory/venv
lyra-memory/venv/bin/pip install -q --upgrade pip
lyra-memory/venv/bin/pip install -q -e "lyra-memory[dev]"

echo "==> lyra_ai (editable, with lyra-memory)"
"$PY" -m venv lyra_ai/venv
lyra_ai/venv/bin/pip install -q --upgrade pip
lyra_ai/venv/bin/pip install -q -e "lyra_ai[dev]" -e lyra-memory
fi

for svc in ${TARGETS[@]+"${TARGETS[@]}"}; do
  [[ -d "$svc" ]] || { echo "!! no such service: $svc"; continue; }
  echo "==> $svc"
  "$PY" -m venv "$svc/venv"
  "$svc/venv/bin/pip" install -q --upgrade pip
  if [[ -f "$svc/requirements.txt" ]]; then
    # Relative-path deps (lyra-memory) resolve from the repo root, not the
    # service dir, so install them explicitly and filter them from the file.
    if grep -q '^lyra-memory' "$svc/requirements.txt"; then
      "$svc/venv/bin/pip" install -q -e "$ROOT/lyra-memory"
    fi
    grep -v '^lyra-memory' "$svc/requirements.txt" > "$svc/.req.tmp"
    "$svc/venv/bin/pip" install -q -r "$svc/.req.tmp"
    rm -f "$svc/.req.tmp"
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
