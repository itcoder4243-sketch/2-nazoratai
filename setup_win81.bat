@echo off
chcp 65001 >nul
cd /d %~dp0
echo === Nazorat ishi: o'rnatish (Windows 8.1) ===
where python >nul 2>nul
if errorlevel 1 (
  echo [XATO] Python topilmadi. Python 3.11.9 ni o'rnating: https://www.python.org/downloads/release/python-3119/
  echo        O'rnatishda "Add python.exe to PATH" belgisini qo'ying.
  pause & exit /b 1
)
python -c "import sys; sys.exit(0 if (3,8)<=sys.version_info[:2]<=(3,12) else 1)"
if errorlevel 1 (
  echo [XATO] Python versiyasi mos emas. Windows 8.1 uchun Python 3.8 dan 3.12 gacha kerak ^(tavsiya: 3.11.9^).
  python --version
  pause & exit /b 1
)
if not exist .venv ( python -m venv .venv )
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements-win81.txt
if errorlevel 1 ( echo [XATO] Kutubxonalarni o'rnatib bo'lmadi. Internetni tekshiring. & pause & exit /b 1 )
echo.
echo Tayyor. Endi run_win81.bat ni ikki marta bosing.
pause
