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
    [string]$Model = "llama3:latest"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# A shell started before OPENROUTER_API_KEY was set carries a stale environment
# block, and so does every process it launches - including a new TAB of an
# already-running terminal. Dreaming then posts "Bearer " and OpenRouter answers
# "missing authentication header", which reads like a Lyra bug and is not one.
# Read the persisted value straight from the user environment when the session
# does not have it. Nothing is printed and nothing is written to disk.
foreach ($name in @("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "CEREBRAS_API_KEY")) {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        $persisted = [Environment]::GetEnvironmentVariable($name, "User")
        if ($persisted) {
            Set-Item -Path "env:$name" -Value $persisted
            Write-Host "[start-lyra] $name loaded from user environment"
        }
    }
}

# bootstrap.sh builds lyra_ai\venv; the standalone black-box layout this script
# came from built lyra_ai\.venv by hand. Accept either, and remember which one
# so the -Chat branch below launches from the same interpreter.
$venvDir = $null
foreach ($candidate in @("venv", ".venv")) {
    if (Test-Path (Join-Path $PSScriptRoot "lyra_ai\$candidate\Scripts\python.exe")) {
        $venvDir = $candidate
        break
    }
}
if (-not $venvDir) {
    Write-Error "No venv under lyra_ai. Build it first:`n  ./bootstrap.sh    # from Git Bash; builds every service"
}
$python = Join-Path $PSScriptRoot "lyra_ai\$venvDir\Scripts\python.exe"

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
    # CP-A: the CLI is a client of the daemon and takes no backend arguments.
    # Start the mind first (this script without -Chat), then attach.
    & (Join-Path $PSScriptRoot "lyra_ai\$venvDir\Scripts\lyra.exe")
} else {
    # Pass the tag explicitly: OllamaBackend.default_model is "llama3.2", which
    # resolves to llama3.2:latest and is not pulled here. llama3:latest rather
    # than llama3.2:3b because the 3B cannot hold LAYER1_FACTS - it denies being
    # Lyra and denies having internal states, both of which the prompt forbids.
    Set-Location (Join-Path $PSScriptRoot "lyra_ai")
    & $python -m lyra_core --backend ollama --model $Model
}
