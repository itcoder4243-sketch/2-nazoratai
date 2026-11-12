@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv\Scripts\python.exe ( echo Avval setup_win81.bat ni ishga tushiring. & pause & exit /b 1 )
echo.
echo === Nazorat ishi serveri ===
echo Talabalar shu manzillardan kiradi ^(bitta Wi-Fi yoki tarmoqda^):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo    http://%%a:8000
echo Ustoz paneli: shu manzilga kirib login "ustoz" bilan.
echo Birinchi ishga tushirishda ustoz paroli pastda bir marta chiqadi, yozib oling!
echo Oynani yopmang: yopsangiz server to'xtaydi.
echo.
.venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8000
pause
