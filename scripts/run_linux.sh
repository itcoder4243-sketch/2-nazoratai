#!/usr/bin/env bash
# Linux/macOS da tez ishga tushirish (sinov uchun). Serverda deploy/nazoratai.service dan foydalaning.
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
exec .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4 --timeout-keep-alive 30
