param([string]$Config = "config.yaml")
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if (-not (Test-Path -LiteralPath $Config)) {
    Copy-Item -LiteralPath "config.example.yaml" -Destination $Config
    Write-Host "Created $Config. Open http://127.0.0.1:8765/ui and use Setup after the service starts."
}

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    $GeneratedToken = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    $EnvContent = (Get-Content -Raw -LiteralPath ".env").Replace("replace-with-a-long-random-token", $GeneratedToken)
    Set-Content -LiteralPath ".env" -Value $EnvContent -Encoding UTF8
    Write-Host "Created .env with a random message-ingest token."
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install -e .
}

$env:QQ_DAILY_CONFIG = (Resolve-Path -LiteralPath $Config).Path
& ".venv\Scripts\python.exe" -m app.main
