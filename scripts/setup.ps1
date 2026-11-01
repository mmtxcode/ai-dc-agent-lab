# setup.ps1 — one-time setup on the Windows jump host. Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

$ErrorActionPreference = "Stop"
Write-Host "== AI DC Agent Lab setup ==" -ForegroundColor Cyan

# 1. Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python 3.11+ is not on PATH. Install it, then re-run." }
python --version

# 2. Virtual environment + packages
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
Write-Host "Packages installed." -ForegroundColor Green

# 3. .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — fill in the CHANGE-ME values." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists — leaving it alone."
}

New-Item -ItemType Directory -Force -Path logs | Out-Null
Write-Host "`nNext: .\.venv\Scripts\python.exe scripts\check_lab.py" -ForegroundColor Cyan
