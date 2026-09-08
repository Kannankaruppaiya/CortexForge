# CortexForge Development Launcher (PowerShell)
Write-Host "Starting CortexForge Development Environment..." -ForegroundColor Cyan

# 1. Check Python Virtualenv
if (-not (Test-Path ".venv")) {
    Write-Host "Virtual environment not found. Please run: python -m venv .venv; pip install -e '.[all]'" -ForegroundColor Red
    exit 1
}

# 2. Run Diagnostics
Write-Host "Running diagnostics..." -ForegroundColor Yellow
& .venv\Scripts\cortex.exe doctor

# 3. Launch Backend API in background or main process
Write-Host "`nLaunching CortexForge REST API on http://127.0.0.1:8000..." -ForegroundColor Green
& .venv\Scripts\cortex.exe serve --host 127.0.0.1 --port 8000
