# start-lyra.ps1 — bring up the Lyra mind on this machine.
#
# This is the house machine's entrypoint: it starts the LLM backend if it is not
# already running, then runs the cognitive runtime (perception loop + dreaming
# loop). It does NOT start the CLI — that is a separate process and, per known
# defect 6, a separate mind. See PICKUP.md.
#
#   .\start-lyra.ps1            # run the mind
#   .\start-lyra.ps1 -Chat      # run the conversational CLI instead
param(
    [switch]$Chat,
    [string]$Model = "llama3.2:3b"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot "lyra_ai\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No venv at lyra_ai\.venv. Build it first:`n  cd lyra_ai`n  python -m venv .venv`n  .venv\Scripts\python.exe -m pip install -e ../lyra-memory -e `".[dev]`" httpx"
}

# Ollama is the only backend that needs no credential. Start it if it is down.
$ollamaUp = $false
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 -UseBasicParsing | Out-Null
    $ollamaUp = $true
} catch {
    $ollamaUp = $false
}

if (-not $ollamaUp) {
    $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $ollama) {
        Write-Host "[start-lyra] starting ollama..." -NoNewline
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        foreach ($i in 1..15) {
            Start-Sleep -Milliseconds 400
            try {
                Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 -UseBasicParsing | Out-Null
                $ollamaUp = $true
                break
            } catch { }
        }
        Write-Host $(if ($ollamaUp) { " up." } else { " did not come up." })
    } else {
        Write-Warning "Ollama not found at $ollama - set an API key backend instead."
    }
}

if ($Chat) {
    # Pass the tag explicitly: OllamaBackend.default_model is "llama3.2", which
    # resolves to llama3.2:latest and is not pulled here.
    & (Join-Path $PSScriptRoot "lyra_ai\.venv\Scripts\lyra.exe") --backend ollama --model $Model
} else {
    Set-Location (Join-Path $PSScriptRoot "lyra_ai")
    & $python -m lyra_core
}
