@echo off
cd /d %~dp0..
if not exist .venv (python -m venv .venv && .venv\Scripts\pip install -r requirements.txt)
.venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4 --timeout-keep-alive 30
