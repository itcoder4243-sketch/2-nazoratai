@echo off
cd /d %~dp0
if not exist .venv (python -m venv .venv && .venv\Scripts\pip install fastapi uvicorn jinja2 python-multipart)
.venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8000
