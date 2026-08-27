# start.ps1 — Windows equivalent of start.sh. Runs the memory system standalone
# (dreaming loop only). The full mind is `python -m lyra_core`, which boots
# perception AND dreaming together; use this only to run memory by itself.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# bootstrap.sh gives this service its own venv, so prefer a local one; fall back
# to the lyra_ai venv, which is the single shared interpreter the standalone
# black-box layout produced. Either directory name may be in use, so probe all
# four before building one, matching start.sh.
$python = $null
foreach ($dir in @("venv", ".venv", "..\lyra_ai\venv", "..\lyra_ai\.venv")) {
    $candidate = Join-Path $PSScriptRoot "$dir\Scripts\python.exe"
    if (Test-Path $candidate) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    python -m venv venv
    & ".\venv\Scripts\python.exe" -m pip install -e ".[dev]"
    $python = ".\venv\Scripts\python.exe"
}

& $python -m lyra_memory
