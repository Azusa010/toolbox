@echo off
cd /d "%~dp0"

py --version >nul 2>&1 || (echo Python not found. Please install from https://python.org && pause && exit /b)

py -c "import PIL,flask" 2>nul || py -m pip install Pillow Flask -q

py app.py
pause
