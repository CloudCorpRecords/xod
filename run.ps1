# Xod from source on Windows: the same as run.sh. Sets up .venv the first time (Python 3.10 or newer; 3.12
# recommended), then serves http://localhost:8000.
#   .\run.ps1
# If PowerShell refuses to run scripts: powershell -ExecutionPolicy Bypass -File .\run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "========================================================"
Write-Host " Starting Xod"
Write-Host "========================================================"

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $found = $null
    foreach ($c in @(@{ Exe = "py"; Args = @("-3.12") }, @{ Exe = "py"; Args = @("-3.13") }, @{ Exe = "py"; Args = @("-3.11") },
                     @{ Exe = "py"; Args = @("-3.10") }, @{ Exe = "python"; Args = @() })) {
        try {
            & $c.Exe @($c.Args) -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $found = $c; break }
        } catch {}
    }
    if (-not $found) {
        Write-Host "Xod needs Python 3.10 or newer. Get it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
        exit 1
    }
    Write-Host "Setting up Xod's Python environment with $($found.Exe) $($found.Args -join ' ') (one time, a few minutes)..."
    & $found.Exe @($found.Args) -m venv .venv
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

# A model on this computer (Xod also finds these by itself once it's running).
foreach ($s in @(@{ Name = "Ollama"; Url = "http://localhost:11434/api/version" }, @{ Name = "LM Studio"; Url = "http://localhost:1234/v1/models" })) {
    try { Invoke-WebRequest $s.Url -UseBasicParsing -TimeoutSec 2 | Out-Null; Write-Host "Found $($s.Name) running." } catch {}
}

Write-Host "========================================================"
Write-Host " Web UI: http://localhost:8000"
Write-Host "========================================================"
$env:PYTHONUTF8 = "1"
# No --reload here: on Windows its restart can hang and keep serving the old code (restart by hand instead).
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
