param(
    [switch]$Force,
    [switch]$Setup
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$deployScript = Join-Path $root "scripts\vps-deploy.py"

python -c "import paramiko" 2>$null
if ($LASTEXITCODE -ne 0) {
    python -m pip install -q paramiko
}

$args = @($deployScript)
if ($Force) { $args += "--force" }
if ($Setup) { $args += "--setup" }

python @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Deploy finished: https://scada.khze.org" -ForegroundColor Green
Write-Host "Note: only /var/www/scada + scada.service + scada.khze.org nginx vhost are changed." -ForegroundColor DarkGray
