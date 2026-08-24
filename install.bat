@echo off
echo ============================================
echo  GST Invoice Extractor - Installer
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.8+ from python.org
    pause
    exit /b 1
)
echo [OK] Python found.

:: Install packages
echo.
echo Installing Python packages...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Package installation failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  IMPORTANT: Tesseract OCR (for scanned PDFs)
echo ============================================
echo  If you have scanned/image PDFs, install Tesseract:
echo  1. Download from: https://github.com/UB-Mannheim/tesseract/wiki
echo  2. Install to default location: C:\Program Files\Tesseract-OCR\
echo  3. During install, select "Add to PATH"
echo.
echo  If all your PDFs are digital (not scanned), skip Tesseract.
echo ============================================
echo.
echo Installation complete! Run main.py to start.
echo.
pause
