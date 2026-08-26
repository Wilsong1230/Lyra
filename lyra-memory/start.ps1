# start.ps1 — Windows equivalent of start.sh. Runs the memory system standalone
# (dreaming loop only). The full mind is `python -m lyra_core`, which boots
# perception AND dreaming together; use this only to run memory by itself.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Prefer the shared lyra_ai venv — that is the layout README step 2 produces and
# the one that is actually built on this machine. Fall back to a local venv so
# this script still works from a bare checkout, matching start.sh.
$shared = Join-Path $PSScriptRoot "..\lyra_ai\.venv\Scripts\python.exe"
if (Test-Path $shared) {
    $python = $shared
} else {
    if (-not (Test-Path "venv")) {
        python -m venv venv
        & ".\venv\Scripts\python.exe" -m pip install -e ".[dev]"
    }
    $python = ".\venv\Scripts\python.exe"
}

& $python -m lyra_memory
