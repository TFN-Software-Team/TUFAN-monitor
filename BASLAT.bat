@echo off
REM MON-15 (madde 88): tek tikla baslatma -- yazilimci olmayan bir ekip
REM uyesi komut satiri bilgisi olmadan sistemi ayaga kaldirabilsin.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ============================================================
    echo HATA: Python bulunamadi.
    echo Bu bilgisayarda Python 3 kurulu degil veya PATH'e eklenmemis.
    echo Kurulum icin: https://www.python.org/downloads/
    echo Kurulum sirasinda "Add python.exe to PATH" kutusunu isaretleyin.
    echo ============================================================
    pause
    exit /b 1
)

python monitor.py
pause
