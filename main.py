"""Platformalar uchun kirish nuqtasi (Railway, Render va h.k.: `uvicorn main:app`).

Baza papkasida restore.db bo'lsa, ilova yuklanishidan oldin uni asosiy baza o'rniga qo'yadi
(eski baza nusxa sifatida saqlanadi). Bir nechta worker bo'lganda fayl qulfi bilan navbatma-navbat bajariladi.
Copyright (c) 2026 SoliyevNurbek. Barcha huquqlar himoyalangan.
"""
import os
import shutil
import time
from pathlib import Path

DB = Path(os.environ.get("NAZORAT_DB") or Path(__file__).resolve().parent / "data" / "nazorat.db")
DB.parent.mkdir(parents=True, exist_ok=True)


def _restore():
    src = DB.parent / "restore.db"
    if not src.exists():
        return
    if DB.exists():
        shutil.copy2(DB, DB.parent / f"nazorat_oldingi_{time.strftime('%Y%m%d_%H%M%S')}.db")
    for ext in ("-wal", "-shm"):
        Path(str(DB) + ext).unlink(missing_ok=True)
    os.replace(src, DB)
    print("[nazorat] restore.db bazaga o'rnatildi", flush=True)


# Qulf ilova yuklanguncha ushlanadi: bo'sh bazani (jadvallar, ustoz) faqat bitta worker yaratadi.
try:
    import fcntl
except ImportError:  # Windows: bitta jarayon
    fcntl = None
with open(DB.parent / ".restore.lock", "w") as lock:
    if fcntl:
        fcntl.flock(lock, fcntl.LOCK_EX)
    _restore()
    from app import app  # noqa: E402,F401
