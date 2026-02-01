# EditBot Installation Script
# Installs all dependencies with pip cache on D drive

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "EditBot Installation Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Set pip to cache in current directory (not C drive)
$env:PIP_CACHE_DIR = "D:\Video Editing Project\editbot\.pip_cache"
Write-Host "Pip cache directory: $env:PIP_CACHE_DIR" -ForegroundColor Gray

# Navigate to project
Set-Location "D:\Video Editing Project\editbot"

# Activate venv
Write-Host "`n[1/6] Activating virtual environment..." -ForegroundColor Yellow
& ".\.venv\Scripts\Activate.ps1"

# Upgrade pip
Write-Host "`n[2/6] Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# Install PyTorch with CUDA (for RTX 3050)
Write-Host "`n[3/6] Installing PyTorch with CUDA support..." -ForegroundColor Yellow
Write-Host "This may take a few minutes..." -ForegroundColor Gray
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install core dependencies
Write-Host "`n[4/6] Installing core dependencies..." -ForegroundColor Yellow
pip install pydantic python-dotenv ollama ffmpeg-python colorama tqdm requests

# Install OpenAI Whisper (for basic transcription)
Write-Host "`n[5/6] Installing Whisper..." -ForegroundColor Yellow
pip install openai-whisper

# Install faster-whisper (optimized, less VRAM)
Write-Host "`n[6/6] Installing faster-whisper..." -ForegroundColor Yellow
pip install faster-whisper

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "Installation Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "`nSupported languages: en, es, fr, de, it, pt, ru, ja, ko, zh, ar, hi, nl, pl, tr, vi, th, id, ms, fil" -ForegroundColor Cyan
Write-Host "`nTo test, run: python test_system.py" -ForegroundColor Cyan
