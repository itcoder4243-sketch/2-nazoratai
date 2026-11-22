"""NazoratAI: onlayn nazorat ishi tizimi. FastAPI + SQLite, yengil, bitta fayl.

Copyright (c) 2026 SoliyevNurbek (Nurbek Soliyev). Barcha huquqlar himoyalangan.
Ruxsatsiz nusxalash, tarqatish yoki tijoriy foydalanish taqiqlanadi (LICENSE ga qarang).
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import queue
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).parent


def _load_dotenv(path):
    """.env faylidan KEY=VALUE qatorlarini o`qiydi (mavjud muhit o`zgaruvchilarini bosmaydi)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(BASE / ".env")
DB_PATH = os.environ.get("NAZORAT_DB") or str(BASE / "data" / "nazorat.db")
# HTTPS (domen) orqali ishlaganda 1: sessiya cookie faqat shifrlangan ulanishda yuboriladi
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
# server (Railway) UTC da ishlaydi: sahifa, Excel va jadvalda O'zbekiston vaqti (yozgi vaqt yo'q, +5)
TZ = timezone(timedelta(hours=float(os.environ.get("APP_TZ_OFFSET", "5"))))
BACKUP_DIR = Path(DB_PATH).parent / "backups"
BACKUP_KEEP = 14  # kunlik zaxiralardan nechtasi saqlanadi
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Nazorat ishi", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
app.add_middleware(GZipMiddleware, minimum_size=1200)  # tunnel/internet orqali sahifalar 5-10 barobar kichik


class StaticCache:
    """css fayllar ?v=N bilan versiyalangan: brauzer har sahifada qayta so`ramaydi.
    Toza ASGI (BaseHTTPMiddleware har so`rovga sezilarli kechikish qo`shadi)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/static/"):
            return await self.app(scope, receive, send)

        async def send2(msg):
            if msg["type"] == "http.response.start":
                msg["headers"] = list(msg.get("headers", [])) + [(b"cache-control", b"public, max-age=86400")]
            await send(msg)
        await self.app(scope, receive, send2)


app.add_middleware(StaticCache)

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SEC_HEADERS = [(b"x-frame-options", b"DENY"), (b"x-content-type-options", b"nosniff"),
               (b"referrer-policy", b"same-origin"), (b"content-security-policy", CSP.encode()),
               (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
               (b"cross-origin-opener-policy", b"same-origin")]
if COOKIE_SECURE:  # faqat HTTPS orqali ishlaganda
    SEC_HEADERS.append((b"strict-transport-security", b"max-age=31536000"))


def body_limit(path):
    """So'rov tanasining eng katta hajmi (bayt): javob saqlash ~100 KB, savol/talaba ro'yxati yuklash ~1 MB."""
    if path == "/login":
        return 16 * 1024
    if path.startswith("/api/"):
        return 256 * 1024
    return 2 * 1024 * 1024


class Security:
    """Xavfsizlik sarlavhalari, so'rov hajmi cheklovi va texnik *.up.railway.app domenidan asosiy domenga yo'naltirish."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        hdrs = dict(scope.get("headers") or [])
        host = hdrs.get(b"host", b"").decode("latin-1").split(":")[0]
        canon = os.environ.get("CANONICAL_HOST")
        if canon and host.endswith(".up.railway.app"):
            q = scope.get("query_string", b"").decode("latin-1")
            await Response(status_code=308, headers={"location": f"https://{canon}{path}" + (f"?{q}" if q else "")})(scope, receive, send)
            return
        limit = body_limit(path)
        try:
            clen = int(hdrs.get(b"content-length", b"0") or 0)
        except ValueError:
            clen = limit + 1
        if clen > limit:
            await Response("So'rov juda katta", status_code=413, headers={"connection": "close"})(scope, receive, send)
            return
        got = 0

        async def receive2():  # Content-Length ko'rsatilmagan (chunked) so'rovlar uchun ham
            nonlocal got
            msg = await receive()
            if msg["type"] == "http.request":
                got += len(msg.get("body", b""))
                if got > limit:
                    raise TooLarge()
            return msg

        started = False

        async def send2(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
                h = list(msg.get("headers", []))
                names = {k.lower() for k, _ in h}
                h += [x for x in SEC_HEADERS if x[0] not in names]
                if not path.startswith("/static/") and b"cache-control" not in names:
                    h.append((b"cache-control", b"no-store"))  # umumiy kompyuterda parol/natija keshda qolmasin
                msg["headers"] = h
            await send(msg)
        try:
            await self.app(scope, receive2, send2)
        except TooLarge:
            if not started:
                await Response("So'rov juda katta", status_code=413, headers={"connection": "close"})(scope, receive, send)


class TooLarge(Exception):
    pass


app.add_middleware(Security)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, role TEXT NOT NULL, name TEXT NOT NULL,
  login TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, pw_hash TEXT NOT NULL, pw_plain TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created INTEGER);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, text TEXT NOT NULL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL, started_at INTEGER, deadline INTEGER,
  submitted_at INTEGER, total REAL, comment TEXT, graded INTEGER DEFAULT 0, graded_at INTEGER);
CREATE TABLE IF NOT EXISTS attempt_q(
  id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL, question_id INTEGER NOT NULL, pos INTEGER,
  answer TEXT DEFAULT '', score REAL, comment TEXT);
CREATE TABLE IF NOT EXISTS violations(id INTEGER PRIMARY KEY, attempt_id INTEGER, ts INTEGER, kind TEXT);
"""
DEFAULTS = {"duration_min": "60", "q_count": "5", "max_per_q": "20", "penalty_points": "10", "max_strikes": "3",
            # natijalar ustoz e'lon qilgandan keyin ko'rinadi; imtihon oynasi: 0 = cheklovsiz (unix vaqt)
            "results_published": "0", "open_at": "0", "close_at": "0",
            # ustoz imtihonni to'xtatgan vaqt (0 = to'xtatilmagan)
            "stopped_at": "0"}
# oynadan chiqish/skrinshot urinishlari jarima oladi; nusxa olish urinishlari faqat jurnalga yoziladi
PENALTY_KINDS = {"tab_hidden", "window_blur", "exit_fullscreen", "printscreen", "screenshot_key", "print"}
STRIKE_GAP = 5  # soniya: bitta chiqish bir necha hodisa bersa, bitta jarima
# halollik belgilari: jarima emas, ustozga ko'rsatiladi (internet uzilishi ham shunday ko'rinishi mumkin)
FLAG_KINDS = {"no_signal": "Signal kelmadi", "hidden_unreported": "Yashirin, lekin chiqish xabari kelmadi",
              "fast_text": "Juda tez paydo bo'lgan matn"}
SIGNAL_GAP = 45      # soniya: sahifadan shuncha vaqt signal kelmasa belgilanadi (normal: har 10 s)
FAST_MIN, FAST_RATE = 200, 12  # shuncha belgidan ko'p va soniyasiga shundan tez paydo bo'lsa (inson yozishi ~1-6 belgi/s)
FLAG_SQL = ",".join(f"'{k}'" for k in FLAG_KINDS)
SIM_MIN, SIM_FLAG = 80, 0.6  # o'xshashlik: shundan uzun javoblar solishtiriladi, shundan yuqorisi belgilanadi
KIND_LABELS = {"tab_hidden": "Boshqa oyna/ilovaga o'tdi", "window_blur": "Oynadan chiqdi", "exit_fullscreen": "To'liq ekrandan chiqdi",
               "printscreen": "Skrinshot tugmasi", "screenshot_key": "Skrinshot tugmalari", "print": "Chop etishga urindi",
               "copy": "Nusxa olishga urindi", "cut": "Kesishga urindi", "paste": "Qo'yishga urindi", "paste_big": "Katta matn qo'yildi",
               "drop": "Matn sudrab tashlandi", "contextmenu": "Kontekst menyu", **FLAG_KINDS}


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL bilan xavfsiz; har yozuvda diskni kutmaydi (HDD da juda muhim)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-8000")
    # checkpoint (WAL -> asosiy fayl, diskka fsync) so`rov ichida emas, fon oqimida bajariladi
    conn.execute("PRAGMA wal_autocheckpoint=0")
    return conn


_pool = queue.LifoQueue()


def checkpointer():
    """Fon oqimi: har 2 soniyada PASSIVE checkpoint. Yozuvchilarni bloklamaydi, so`rovlar diskni kutmaydi."""
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
    while True:
        time.sleep(2)
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            pass


def init_db():
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")  # bir marta: bazaga yoziladi va saqlanib qoladi
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.executescript(SCHEMA)
    for ddl in ("ALTER TABLE violations ADD COLUMN penalty INTEGER DEFAULT 0",
                "ALTER TABLE attempts ADD COLUMN disq INTEGER DEFAULT 0",
                "ALTER TABLE attempts ADD COLUMN deduct REAL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN grp TEXT",
                "CREATE TABLE IF NOT EXISTS assigned(user_id INTEGER NOT NULL, question_id INTEGER NOT NULL, pos INTEGER NOT NULL, PRIMARY KEY(user_id, question_id))",
                # qayta topshirish: keyingi urinishdan ayriladigan chegirma va eski urinishlar tarixi
                "ALTER TABLE users ADD COLUMN carry REAL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN retakes INTEGER DEFAULT 0",
                "ALTER TABLE attempts ADD COLUMN carry REAL DEFAULT 0",
                "ALTER TABLE attempts ADD COLUMN try_no INTEGER DEFAULT 1",
                "CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, ts INTEGER, carry REAL, data TEXT)",
                # imtihon to'xtatilganda yozilayotgan ish qabul qilinmaydi; ustoz qayta ruxsat bersa, talaba yopiq vaqtda ham boshlaydi
                "ALTER TABLE attempts ADD COLUMN rejected INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN reopen INTEGER DEFAULT 0",
                # noto'g'ri kirishlar jurnali: barcha worker'lar uchun umumiy (login+IP va IP bo'yicha cheklov)
                "CREATE TABLE IF NOT EXISTS login_fails(k TEXT NOT NULL, ts INTEGER NOT NULL)",
                "CREATE INDEX IF NOT EXISTS ix_fails ON login_fails(k, ts)",
                "CREATE INDEX IF NOT EXISTS ix_sess_created ON sessions(created)",
                # halollik: buzilish ikkinchi kanal (saqlash) orqali ham keladi (cid = takrorlanmasin), server signalni kuzatadi,
                # javoblar yozilish tarixi (snaps) saqlanadi
                "ALTER TABLE violations ADD COLUMN cid TEXT",
                "ALTER TABLE violations ADD COLUMN info TEXT",
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_viol_cid ON violations(attempt_id, cid) WHERE cid IS NOT NULL",
                "ALTER TABLE attempts ADD COLUMN last_seen INTEGER",
                "CREATE TABLE IF NOT EXISTS snaps(attempt_id INTEGER NOT NULL, ts INTEGER NOT NULL, chars INTEGER NOT NULL)",
                "CREATE INDEX IF NOT EXISTS ix_snaps ON snaps(attempt_id, ts)",
                # indekslar: har saqlash/panel so`rovi butun jadvalni ko`rib chiqmasligi uchun
                "CREATE INDEX IF NOT EXISTS ix_aq_attempt ON attempt_q(attempt_id)",
                "CREATE INDEX IF NOT EXISTS ix_aq_question ON attempt_q(question_id)",
                "CREATE INDEX IF NOT EXISTS ix_viol_attempt ON violations(attempt_id, penalty, ts)",
                "CREATE INDEX IF NOT EXISTS ix_assigned_q ON assigned(question_id)",
                "CREATE INDEX IF NOT EXISTS ix_hist_user ON history(user_id)",
                "CREATE INDEX IF NOT EXISTS ix_sess_user ON sessions(user_id)",
                "CREATE INDEX IF NOT EXISTS ix_att_sub ON attempts(submitted_at)",
                "CREATE INDEX IF NOT EXISTS ix_att_start ON attempts(started_at)"):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    for k, v in DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    if not conn.execute("SELECT 1 FROM users WHERE role='teacher'").fetchone():
        pw = os.environ.get("TEACHER_PASSWORD") or secrets.token_urlsafe(8)
        add_user(conn, "teacher", "Ustoz", "ustoz", pw, keep_plain=False)
        if not os.environ.get("TEACHER_PASSWORD"):
            print(f"\n[!] Ustoz paroli yaratildi: login=ustoz parol={pw}\n")
    # eski bazalar: ochiq saqlangan o'quvchi parollari shifrlanadi (bir martalik)
    for r in conn.execute("SELECT id, pw_plain FROM users WHERE pw_plain IS NOT NULL AND pw_plain NOT LIKE 'enc1:%'").fetchall():
        conn.execute("UPDATE users SET pw_plain=? WHERE id=?", (seal_pw(r["pw_plain"]), r["id"]))
    conn.execute("ANALYZE")
    conn.close()
    threading.Thread(target=checkpointer, daemon=True).start()
    threading.Thread(target=backup_loop, daemon=True).start()


PBKDF2_ITERS = 10_000  # 100k bu kompyuterda ~200 ms/kirish edi: 200 kishi bir vaqtda kirsa 20+ soniya kutadi


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), PBKDF2_ITERS).hex()


DUMMY_SALT = secrets.token_hex(16)  # mavjud bo'lmagan login uchun ham xesh hisoblanadi: javob vaqti bir xil


def _pw_key():
    """O'quvchi parollarini shifrlash kaliti: PW_KEY muhit o'zgaruvchisi yoki baza yonidagi .pw_key fayli.
    Zaxira (.db) fayli kalitsiz qo'lga tushsa, parollar o'qilmaydi."""
    if os.environ.get("PW_KEY"):
        return hashlib.sha256(os.environ["PW_KEY"].encode()).digest()
    p = Path(DB_PATH).parent / ".pw_key"
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_hex(32).encode())
        os.close(fd)
    except FileExistsError:
        pass
    for _ in range(50):  # boshqa worker endigina yaratayotgan bo'lsa
        v = p.read_text().strip()
        if len(v) == 64:
            return bytes.fromhex(v)
        time.sleep(0.05)
    raise RuntimeError(".pw_key fayli buzilgan")


_PWK = _pw_key()


def _stream(nonce, n):
    out = b""
    for i in range(0, n, 32):
        out += hmac.new(_PWK, b"s" + nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
    return out[:n]


def seal_pw(pw):
    """Parolni shifrlaydi (HMAC-SHA256 oqimi + yaxlitlik belgisi)."""
    if pw is None:
        return None
    data, nonce = pw.encode(), secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(nonce, len(data))))
    tag = hmac.new(_PWK, b"t" + nonce + ct, hashlib.sha256).digest()[:16]
    return "enc1:" + base64.urlsafe_b64encode(nonce + tag + ct).decode()


def reveal_pw(v):
    if not v or not v.startswith("enc1:"):
        return v
    try:
        raw = base64.urlsafe_b64decode(v[5:])
        nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
        if not hmac.compare_digest(tag, hmac.new(_PWK, b"t" + nonce + ct, hashlib.sha256).digest()[:16]):
            return None
        return bytes(a ^ b for a, b in zip(ct, _stream(nonce, len(ct)))).decode()
    except Exception:
        return None


def add_user(conn, role, name, login, pw, keep_plain=True):
    salt = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO users(role,name,login,salt,pw_hash,pw_plain) VALUES(?,?,?,?,?,?)",
        (role, name, login, salt, hash_pw(pw, salt), seal_pw(pw) if keep_plain else None),
    )


def get_db():
    # ulanishlar qayta ishlatiladi (har so`rovda yangi ulanish + PRAGMA ochilmaydi)
    try:
        conn = _pool.get_nowait()
    except queue.Empty:
        conn = connect()
    try:
        yield conn
    finally:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if _pool.qsize() < 64:
                _pool.put(conn)
            else:
                conn.close()
        except sqlite3.Error:
            conn.close()


_settings = {"t": 0.0, "v": {}}


def _all_settings(conn):
    # sozlamalar har so`rovda 5-10 marta o`qiladi: xotirada 2 soniya saqlanadi (har worker o`zida), o`zgartirilganda darhol yangilanadi
    if time.time() - _settings["t"] > 2:
        _settings["v"] = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
        _settings["t"] = time.time()
    return _settings["v"]


def setting(conn, key):
    v = _all_settings(conn).get(key)
    return int(v) if v not in (None, "") else int(DEFAULTS[key])


def setting_s(conn, key, default=""):
    return _all_settings(conn).get(key) or default


def login_page(request, conn, error=None):
    return render(request, "login.html", error=error, title=setting_s(conn, "exam_title", "Nazorat ishi"),
                  minutes=setting(conn, "duration_min"), q_count=setting(conn, "q_count"),
                  penalty=setting(conn, "penalty_points"), max_strikes=setting(conn, "max_strikes"))


class Redir(Exception):
    pass


@app.exception_handler(Redir)
async def _redir(request, exc):
    return RedirectResponse("/", status_code=303)


SESSION_TTL = 12 * 3600  # cookie bilan bir xil: muddati o'tgan token serverda ham ishlamaydi


def current_user(request, conn):
    tok = request.cookies.get("sid")
    if not tok:
        return None
    return conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.created>?",
        (tok, int(time.time()) - SESSION_TTL)).fetchone()


def require(role):
    def dep(request: Request, conn=Depends(get_db)):
        u = current_user(request, conn)
        if not u or u["role"] != role:
            raise Redir()
        return u

    return dep


import starlette

_NEW_TR = tuple(int(x) for x in starlette.__version__.split(".")[:2]) >= (0, 29)


def nav_todo():
    try:
        conn = _pool.get_nowait()
    except queue.Empty:
        conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM attempts WHERE graded=0 AND rejected=0 AND "
                            "(submitted_at IS NOT NULL OR deadline < ?)", (int(time.time()),)).fetchone()[0]
    finally:
        if _pool.qsize() < 64:
            _pool.put(conn)
        else:
            conn.close()


def render(request, name, **ctx):
    u = ctx.get("user")
    if u is not None and u["role"] == "teacher" and "nav_todo" not in ctx:
        ctx["nav_todo"] = nav_todo()
    # Starlette >=0.29: (request, name, ctx); eski versiya (Windows 8.1 uchun): (name, ctx+request)
    if _NEW_TR:
        return templates.TemplateResponse(request, name, ctx)
    ctx["request"] = request
    return templates.TemplateResponse(name, ctx)


def grade_of(total, max_total):
    if not max_total:
        return "-"
    p = total / max_total * 100
    return 5 if p >= 86 else 4 if p >= 71 else 3 if p >= 56 else 2


templates.env.globals["grade_of"] = grade_of
def fmt_ts(t, f="%d.%m %H:%M"):
    return datetime.fromtimestamp(t, TZ).strftime(f) if t else "-"


def parse_local(v):
    """<input type=datetime-local> qiymati (Toshkent vaqti) -> unix vaqt; bo'sh bo'lsa 0."""
    v = (v or "").strip()
    if not v:
        return 0
    return int(datetime.strptime(v[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ).timestamp())


templates.env.filters["dt"] = lambda t: fmt_ts(t)
templates.env.filters["tm"] = lambda t: fmt_ts(t, "%H:%M")
templates.env.filters["dtl"] = lambda t: fmt_ts(t, "%Y-%m-%dT%H:%M") if t else ""
templates.env.filters["dtf"] = lambda t: fmt_ts(t, "%d.%m.%Y %H:%M")
templates.env.filters["pw"] = lambda v: reveal_pw(v) or ""
templates.env.filters["num"] =lambda x: ("%g" % round(x or 0, 1))


def back(url, msg=None):
    # xabar so'rov qismiga, "#bo'lim" esa oxiriga: /t/settings?msg=...#window
    path, _, frag = url.partition("#")
    if msg:
        path += ("&" if "?" in path else "?") + "msg=" + quote(msg)
    return RedirectResponse(path + ("#" + frag if frag else ""), status_code=303)


def slug(s):
    for ch in "‘’ʻʼ'`":
        s = s.replace(ch, "")
    s = s.replace("Ǵ", "G").replace("ǵ", "g")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


def gen_pw(name):
    parts = name.split()
    first = slug(parts[1]) if len(parts) > 1 else slug(parts[0]) if parts else ""
    return f"{first or 'talaba'}{secrets.randbelow(900) + 100}"


def pick_questions(conn, n, avoid=(), hard=()):
    """Eng kam ishlatilgan savollardan n ta: avval `avoid` va `hard` dan tashqari, yetmasa `avoid` dan ham."""
    out = []
    for excl in (set(avoid) | set(hard), set(hard)):
        if len(out) >= n:
            break
        ex = excl | set(out)
        where = f"WHERE id NOT IN ({','.join('?' * len(ex))})" if ex else ""
        out += [r["id"] for r in conn.execute(
            f"SELECT id FROM questions {where} ORDER BY used ASC, RANDOM() LIMIT ?",
            (*ex, n - len(out)))]
    return out

# ---------------- umumiy ----------------
FAIL_WINDOW = 300     # soniya
FAIL_PER_LOGIN = 8    # bitta IP dan bitta loginga: shundan keyin shu IP uchun 5 daqiqa blok
FAIL_PER_IP = 100     # bitta IP dan barcha loginlarga (sinf bitta Wi-Fi'da bo'lishi mumkin)


def client_ip(request):
    # Railway edge haqiqiy IP ni o'zi qo'yadi (mijoz yuborgan X-Forwarded-For almashtiriladi)
    return request.client.host if request.client else "?"


def login_blocked(conn, ip, key):
    since = int(time.time()) - FAIL_WINDOW
    n_login = conn.execute("SELECT COUNT(*) FROM login_fails WHERE k=? AND ts>?", (f"{ip}|{key}", since)).fetchone()[0]
    n_ip = conn.execute("SELECT COUNT(*) FROM login_fails WHERE k=? AND ts>?", (f"{ip}|*", since)).fetchone()[0]
    return n_login >= FAIL_PER_LOGIN or n_ip >= FAIL_PER_IP


def login_failed(conn, ip, key):
    now = int(time.time())
    conn.executemany("INSERT INTO login_fails(k,ts) VALUES(?,?)", [(f"{ip}|{key}", now), (f"{ip}|*", now)])
    if secrets.randbelow(50) == 0:  # eski yozuvlar vaqti-vaqti bilan tozalanadi
        conn.execute("DELETE FROM login_fails WHERE ts<?", (now - 3600,))


def clear_fails(conn, key):
    conn.execute("DELETE FROM login_fails WHERE k LIKE ? ESCAPE '\\'",
                 ("%|" + key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"),))


@app.get("/")
def index(request: Request, conn=Depends(get_db)):
    u = current_user(request, conn)
    if u:
        return RedirectResponse("/t" if u["role"] == "teacher" else "/s", status_code=303)
    return login_page(request, conn)


@app.post("/login")
def login(request: Request, login: str = Form(...), password: str = Form(...), conn=Depends(get_db)):
    # cheklov login+IP bo'yicha: begona odam noto'g'ri parol terib, haqiqiy egasini (boshqa IP dan) bloklay olmaydi
    ip = client_ip(request)
    now = time.time()
    key = login.strip().lower()[:80]
    if login_blocked(conn, ip, key):
        return login_page(request, conn, "Juda ko'p urinish. 5 daqiqadan keyin qayta urining.")
    u = conn.execute("SELECT * FROM users WHERE login=?", (key,)).fetchone()
    ok = secrets.compare_digest(u["pw_hash"], hash_pw(password, u["salt"])) if u else \
        (hash_pw(password, DUMMY_SALT) and False)  # login yo'q bo'lsa ham xesh: javob vaqti bir xil
    if not ok:
        login_failed(conn, ip, key)
        return login_page(request, conn, "Login yoki parol noto'g'ri")
    tok = secrets.token_urlsafe(32)
    conn.execute("BEGIN")
    conn.execute("DELETE FROM sessions WHERE user_id=? OR created<?", (u["id"], int(now) - SESSION_TTL))  # bitta qurilma
    conn.execute("INSERT INTO sessions(token,user_id,created) VALUES(?,?,?)", (tok, u["id"], int(now)))
    conn.execute("DELETE FROM login_fails WHERE k=?", (f"{ip}|{key}",))
    conn.execute("COMMIT")
    r = RedirectResponse("/t" if u["role"] == "teacher" else "/s", status_code=303)  # "/" orqali ortiqcha aylanmasiz
    r.set_cookie("sid", tok, httponly=True, samesite="lax", max_age=60 * 60 * 12, secure=COOKIE_SECURE)
    return r


@app.get("/logout")
def logout_get():
    # chiqish faqat POST orqali (boshqa sayt havola/rasm bilan chiqarib yubora olmaydi)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, conn=Depends(get_db)):
    tok = request.cookies.get("sid")
    if tok:
        conn.execute("DELETE FROM sessions WHERE token=?", (tok,))
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie("sid")
    return r


# ---------------- talaba ----------------
def exam_window(conn, now=None):
    """Imtihon oynasi holati: ('stopped'|'before'|'open'|'closed', open_at, close_at, stopped_at). 0 = chegara yo'q."""
    now = now or time.time()
    o, c, s = setting(conn, "open_at"), setting(conn, "close_at"), setting(conn, "stopped_at")
    st = "stopped" if s else "before" if o and now < o else "closed" if c and now >= c else "open"
    return {"state": st, "open_at": o, "close_at": c, "stopped_at": s}


def finalize_if_expired(conn, att):
    if att and att["submitted_at"] is None and time.time() > att["deadline"]:
        conn.execute("UPDATE attempts SET submitted_at=? WHERE id=?", (att["deadline"], att["id"]))
        return conn.execute("SELECT * FROM attempts WHERE id=?", (att["id"],)).fetchone()
    return att


def get_attempt(conn, uid):
    att = conn.execute("SELECT * FROM attempts WHERE user_id=?", (uid,)).fetchone()
    return finalize_if_expired(conn, att)


@app.get("/s")
def student_home(request: Request, u=Depends(require("student")), conn=Depends(get_db)):
    att = get_attempt(conn, u["id"])
    items, mx = [], setting(conn, "max_per_q")
    if att and att["graded"] and setting(conn, "results_published"):
        items = conn.execute(
            "SELECT aq.*, q.text FROM attempt_q aq JOIN questions q ON q.id=aq.question_id "
            "WHERE attempt_id=? ORDER BY pos", (att["id"],)).fetchall()
    return render(request, "s_home.html", user=u, att=att, items=items, mx=mx,
                  q_count=setting(conn, "q_count"), minutes=setting(conn, "duration_min"),
                  now=int(time.time()), win=exam_window(conn), published=setting(conn, "results_published"))


@app.post("/s/start")
def student_start(u=Depends(require("student")), conn=Depends(get_db)):
    win = exam_window(conn)
    if win["state"] != "open" and not u["reopen"] and \
            not conn.execute("SELECT 1 FROM attempts WHERE user_id=?", (u["id"],)).fetchone():
        return RedirectResponse("/s", status_code=303)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not conn.execute("SELECT 1 FROM attempts WHERE user_id=?", (u["id"],)).fetchone():
            n = setting(conn, "q_count")
            # oldindan biriktirilganlar; savollar soni o`zgargan bo`lsa kesiladi yoki eng kam ishlatilganlar bilan to`ldiriladi
            ids = [r["question_id"] for r in conn.execute(
                "SELECT question_id FROM assigned WHERE user_id=? ORDER BY pos", (u["id"],))][:n]
            if len(ids) < n:
                ids += pick_questions(conn, n - len(ids), hard=ids)
            if len(ids) < n:
                conn.execute("ROLLBACK")
                return RedirectResponse("/s", status_code=303)
            now = int(time.time())
            deadline = now + setting(conn, "duration_min") * 60
            if win["close_at"] > now:  # yopilish vaqtidan oshmaydi (ustoz yopilgandan keyin ruxsat bergan bo'lsa, to'liq vaqt)
                deadline = min(deadline, win["close_at"])
            cur = conn.execute(
                "INSERT INTO attempts(user_id,started_at,deadline,carry,try_no) VALUES(?,?,?,?,?)",
                (u["id"], now, deadline, u["carry"] or 0, (u["retakes"] or 0) + 1))
            conn.execute("UPDATE users SET reopen=0 WHERE id=?", (u["id"],))
            aid = cur.lastrowid
            for i, qid in enumerate(ids, 1):
                conn.execute("INSERT INTO attempt_q(attempt_id,question_id,pos) VALUES(?,?,?)", (aid, qid, i))
                conn.execute("UPDATE questions SET used=used+1 WHERE id=?", (qid,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return RedirectResponse("/s/exam", status_code=303)


@app.get("/s/exam")
def student_exam(request: Request, u=Depends(require("student")), conn=Depends(get_db)):
    att = get_attempt(conn, u["id"])
    if not att or att["submitted_at"] is not None:
        return RedirectResponse("/s", status_code=303)
    items = conn.execute(
        "SELECT aq.id, aq.pos, aq.answer, q.text FROM attempt_q aq JOIN questions q ON q.id=aq.question_id "
        "WHERE attempt_id=? ORDER BY pos", (att["id"],)).fetchall()
    return render(request, "s_exam.html", user=u, att=att, items=items,
                  remaining=max(0, att["deadline"] - int(time.time())), total=att["deadline"] - att["started_at"],
                  strikes=strike_count(conn, att["id"]), max_strikes=setting(conn, "max_strikes"),
                  penalty=setting(conn, "penalty_points"))


@app.post("/api/save")
def api_save(data: dict = Body(...), u=Depends(require("student")), conn=Depends(get_db)):
    # sync def: bloklovchi SQLite ishi hodisalar sikli (event loop) emas, thread pool da bajariladi
    att = get_attempt(conn, u["id"])
    if not att or att["submitted_at"] is not None:
        return JSONResponse({"ok": False, "submitted": True})
    answers = [(str(v)[:6000], int(k)) for k, v in (data.get("answers") or {}).items() if str(k).isdigit()]
    events = [e for e in (data.get("v") or [])[:20] if isinstance(e, dict)]
    now = int(time.time())
    disq = False
    conn.execute("BEGIN")
    try:
        # signal: sahifa har 10 s da saqlash yuboradi; uzoq tanaffus ustozga belgilanadi
        if att["last_seen"] and now - att["last_seen"] > SIGNAL_GAP:
            add_flag(conn, att["id"], now, "no_signal", f"{now - att['last_seen']} soniya signal yo'q")
        conn.execute("UPDATE attempts SET last_seen=? WHERE id=?", (now, att["id"]))
        # buzilishlar ikkinchi kanal orqali ham keladi: /api/violation to'sib qo'yilsa ham yetib boradi
        for e in events:
            disq = record_violation(conn, att, e.get("kind"), str(e.get("id", ""))[:40] or None)["disq"] or disq
        # sahifa "yashirin" deyapti, lekin so'nggi daqiqada chiqish xabari yo'q: buzilish kodi o'chirilgan bo'lishi mumkin
        if data.get("vis") == "h" and data.get("a") and not conn.execute(
                "SELECT 1 FROM violations WHERE attempt_id=? AND ts>=? AND kind IN ('tab_hidden','window_blur','exit_fullscreen','hidden_unreported')",
                (att["id"], now - 60)).fetchone():
            add_flag(conn, att["id"], now, "hidden_unreported", "sahifa yashirin holatda yozildi")
        if answers and not disq:
            old = {r["id"]: (r["pos"], r["n"]) for r in conn.execute(
                "SELECT id, pos, LENGTH(answer) n FROM attempt_q WHERE attempt_id=?", (att["id"],))}
            # bir xil matn qayta yozilmaydi (IS NOT): diskka yozuv faqat haqiqiy o`zgarishda
            changed = sum(conn.execute("UPDATE attempt_q SET answer=? WHERE id=? AND attempt_id=? AND answer IS NOT ?",
                                       (a, i, att["id"], a)).rowcount for a, i in answers)
            if changed:
                last = conn.execute("SELECT MAX(ts) FROM snaps WHERE attempt_id=?", (att["id"],)).fetchone()[0] or att["started_at"]
                dt = max(1, now - last)
                for a, i in answers:
                    if i in old and len(a) - (old[i][1] or 0) > FAST_MIN and (len(a) - (old[i][1] or 0)) / dt > FAST_RATE:
                        add_flag(conn, att["id"], now, "fast_text", f"{old[i][0]}-savol: +{len(a) - (old[i][1] or 0)} belgi {dt} soniyada")
                total = conn.execute("SELECT SUM(LENGTH(answer)) FROM attempt_q WHERE attempt_id=?", (att["id"],)).fetchone()[0] or 0
                conn.execute("INSERT INTO snaps(attempt_id,ts,chars) VALUES(?,?,?)", (att["id"], now, total))
        # taymer tugadi deb kelgan avtomatik yuborish: ustoz vaqt qo`shgan bo`lsa yubormaymiz
        final = not disq and bool(data.get("final")) and not (data.get("auto") and att["deadline"] > time.time() + 3)
        if final:
            # ustoz shu orada imtihonni to'xtatgan bo'lsa, ish qabul qilinmagan holicha qoladi
            conn.execute("UPDATE attempts SET submitted_at=? WHERE id=? AND submitted_at IS NULL", (now, att["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if final or disq:
        return JSONResponse({"ok": True, "submitted": True, "vdone": [str(e.get("id")) for e in events]})
    return JSONResponse({"ok": True, "submitted": False, "remaining": max(0, att["deadline"] - now),
                         "strikes": strike_count(conn, att["id"]), "vdone": [str(e.get("id")) for e in events]})


def add_flag(conn, aid, ts, kind, info):
    conn.execute("INSERT INTO violations(attempt_id,ts,kind,penalty,info) VALUES(?,?,?,0,?)", (aid, ts, kind, info))


def record_violation(conn, att, kind, cid=None):
    """Talaba sahifasidan kelgan buzilish (ikki kanaldan biri orqali, cid bilan takrorlanmaydi)."""
    kind = re.sub(r"[^a-z_]", "", str(kind or "")[:30]) or "other"
    if kind in FLAG_KINDS:  # bu belgilarni faqat server qo'yadi
        kind = "other"
    if cid and conn.execute("SELECT 1 FROM violations WHERE attempt_id=? AND cid=?", (att["id"], cid)).fetchone():
        return {"strike": False, "disq": False, "dup": True}
    now = int(time.time())
    pen = 0
    if kind in PENALTY_KINDS:
        last = conn.execute("SELECT MAX(ts) m FROM violations WHERE attempt_id=? AND penalty=1",
                            (att["id"],)).fetchone()["m"]
        pen = 1 if (last is None or now - last >= STRIKE_GAP) else 0
    conn.execute("INSERT OR IGNORE INTO violations(attempt_id,ts,kind,penalty,cid) VALUES(?,?,?,?,?)", (att["id"], now, kind, pen, cid))
    disq = bool(pen and strike_count(conn, att["id"]) >= setting(conn, "max_strikes"))
    if disq:
        conn.execute("UPDATE attempts SET submitted_at=?, disq=1 WHERE id=? AND submitted_at IS NULL", (now, att["id"]))
    return {"strike": bool(pen), "disq": disq}


@app.post("/api/violation")
def api_violation(data: dict = Body(...), u=Depends(require("student")), conn=Depends(get_db)):
    att = get_attempt(conn, u["id"])
    if not att or att["submitted_at"] is not None:
        return JSONResponse({"submitted": True})
    r = record_violation(conn, att, data.get("kind"), str(data.get("id", ""))[:40] or None)
    strikes = strike_count(conn, att["id"])
    return JSONResponse({"strike": r["strike"], "strikes": strikes, "max": setting(conn, "max_strikes"),
                         "penalty": setting(conn, "penalty_points"), "disq": r["disq"], "submitted": r["disq"]})


def strike_count(conn, aid):
    return conn.execute("SELECT COUNT(*) c FROM violations WHERE attempt_id=? AND penalty=1", (aid,)).fetchone()["c"]


# ---------------- ustoz ----------------
def state_of(r):
    return ("Kirmagan" if not r["aid"] else "Yozmoqda" if r["submitted_at"] is None
            else "Qabul qilinmagan" if r["rejected"] else "Baholangan" if r["graded"]
            else "Chetlatilgan" if r["disq"] else "Tekshirilmagan")


STATES = ("Kirmagan", "Yozmoqda", "Tekshirilmagan", "Chetlatilgan", "Baholangan", "Qabul qilinmagan")
SUBMITTED = ("Tekshirilmagan", "Chetlatilgan", "Baholangan")  # qabul qilingan ishlar


def load_cfg(conn):
    cfg = {k: setting(conn, k) for k in DEFAULTS}
    cfg["exam_title"] = setting_s(conn, "exam_title")
    return cfg


def results_data(conn):
    """Har bir talabaning joriy holati (vaqti o'tgan ishlar yopiladi), holatlar soni va umumiy statistika."""
    rows = conn.execute(
        "SELECT u.id uid, u.name, u.login, u.grp, u.carry ucarry, a.id aid, a.started_at, a.deadline, a.submitted_at, "
        "a.total, a.graded, a.graded_at, a.disq, a.carry, a.try_no, a.rejected, "
        "(SELECT COUNT(*) FROM violations v WHERE v.attempt_id=a.id AND v.penalty=1) viol, "
        f"(SELECT COUNT(*) FROM violations v WHERE v.attempt_id=a.id AND v.kind IN ({FLAG_SQL})) flags "
        "FROM users u LEFT JOIN attempts a ON a.user_id=u.id WHERE u.role='student' ORDER BY u.grp, u.name").fetchall()
    rows = [dict(r) for r in rows]
    now = time.time()
    for r in rows:
        if r["aid"] and r["submitted_at"] is None and now > r["deadline"]:
            conn.execute("UPDATE attempts SET submitted_at=? WHERE id=?", (r["deadline"], r["aid"]))
            r["submitted_at"] = r["deadline"]
        r["state"] = state_of(r)
        r["left"] = max(0, int((r["deadline"] - now) // 60)) if r["state"] == "Yozmoqda" else None
    cnt = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    graded = [r["total"] for r in rows if r["graded"]]
    stats = {
        "students": len(rows),
        "questions": conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"],
        "pending": cnt["Tekshirilmagan"] + cnt["Chetlatilgan"],
        "graded": cnt["Baholangan"], "cnt": cnt, "rejected": cnt["Qabul qilinmagan"],
        "submitted": cnt["Tekshirilmagan"] + cnt["Chetlatilgan"] + cnt["Baholangan"],
        "avg": sum(graded) / len(graded) if graded else None,
    }
    return rows, stats


@app.get("/t")
def teacher_dashboard(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    rows, stats = results_data(conn)
    cfg = load_cfg(conn)
    mx = cfg["max_per_q"] * cfg["q_count"]
    graded = [r for r in rows if r["graded"]]
    for r in graded:
        r["grade"] = grade_of(r["total"], mx)
    top = sorted(graded, key=lambda r: (-r["total"], r["name"]))[:10]
    low = sorted(graded, key=lambda r: (r["total"], r["name"]))[:10]
    # gistogramma: 10 ta teng oraliq, oxirgisi maksimal ballni ham o'z ichiga oladi
    step = mx / 10 if mx else 10
    hist = []
    for i in range(10):
        lo, hi = i * step, (i + 1) * step
        n = sum(1 for r in graded if lo <= r["total"] < hi or (i == 9 and r["total"] >= hi))
        hist.append({"lo": lo, "hi": hi, "n": n})
    # y o'qi uchun butun, teng qadamli belgilar (masalan 0, 4, 8, 12, 16)
    ystep = max(1, -(-max([h["n"] for h in hist] + [1]) // 4))
    hmax = ystep * 4
    grades = {g: sum(1 for r in graded if r["grade"] == g) for g in (5, 4, 3, 2)}
    totals = sorted(r["total"] for r in graded)
    k = len(totals)
    summary = {
        "median": (totals[k // 2] if k % 2 else (totals[k // 2 - 1] + totals[k // 2]) / 2) if k else None,
        "max": totals[-1] if k else None, "min": totals[0] if k else None,
        "pass": round(sum(1 for r in graded if r["grade"] >= 3) / k * 100) if k else None,
    }
    groups = []
    for g in sorted({r["grp"] or "—" for r in rows}, key=lambda g: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", g)]):
        gr = [r for r in rows if (r["grp"] or "—") == g]
        gg = [r["total"] for r in gr if r["graded"]]
        groups.append({"grp": g, "n": len(gr), "sub": sum(1 for r in gr if r["state"] in SUBMITTED),
                       "graded": len(gg), "avg": sum(gg) / len(gg) if gg else None, "max": max(gg) if gg else None})
    return render(request, "t_dash.html", user=u, stats=stats, cfg=cfg, mx_total=mx, top=top, low=low,
                  hist=hist, hmax=hmax, grades=grades, summary=summary, groups=groups,
                  events=recent_events(conn), now_s=fmt_ts(time.time(), "%H:%M:%S"), win=exam_window(conn))


@app.get("/t/results")
def teacher_results(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    rows, stats = results_data(conn)
    cfg = load_cfg(conn)
    disq_n = conn.execute("SELECT COUNT(*) c FROM attempts WHERE disq=1 AND rejected=0").fetchone()["c"]
    groups = sorted({r["grp"] for r in rows if r["grp"]})
    rejected = [r for r in rows if r["state"] == "Qabul qilinmagan"]
    return render(request, "t_results.html", user=u, rows=rows, stats=stats, cfg=cfg, groups=groups,
                  mx_total=cfg["max_per_q"] * cfg["q_count"], disq_n=disq_n, rejected=rejected,
                  states=STATES, win=exam_window(conn))


@app.get("/t/review")
def teacher_review(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    rows, stats = results_data(conn)
    cfg = load_cfg(conn)
    queue_ = sorted((r for r in rows if r["state"] in ("Tekshirilmagan", "Chetlatilgan")),
                    key=lambda r: (r["grp"] or "", r["name"]))
    done = sorted((r for r in rows if r["graded"]), key=lambda r: -(r["graded_at"] or 0))[:10]
    writing = [r for r in rows if r["state"] == "Yozmoqda"]
    groups = sorted({r["grp"] for r in queue_ if r["grp"]})
    return render(request, "t_review.html", user=u, queue=queue_, done=done, writing=writing, stats=stats,
                  groups=groups, mx_total=cfg["max_per_q"] * cfg["q_count"], first=next_ungraded(conn))


def recent_events(conn, limit=12):
    """So`nggi hodisalar: jarimali chiqishlar, chetlatish, yuborish, boshlash."""
    ms = setting(conn, "max_strikes")
    ev = []
    for r in conn.execute(
            "SELECT v.ts, v.kind, a.id aid, u.name, (SELECT COUNT(*) FROM violations w WHERE w.attempt_id=v.attempt_id "
            "AND w.penalty=1 AND w.ts<=v.ts) n FROM violations v JOIN attempts a ON a.id=v.attempt_id "
            "JOIN users u ON u.id=a.user_id WHERE v.penalty=1 ORDER BY v.ts DESC LIMIT ?", (limit,)):
        ev.append((r["ts"], "ban" if r["n"] >= ms else "warn", r["aid"],
                   f"{r['name']} " + (f"chetlatildi ({r['n']}/{ms})" if r["n"] >= ms else f"oynadan chiqdi ({r['n']}/{ms})")))
    for r in conn.execute("SELECT a.id aid, a.submitted_at ts, u.name FROM attempts a JOIN users u ON u.id=a.user_id "
                          "WHERE a.submitted_at IS NOT NULL AND a.disq=0 AND a.rejected=0 ORDER BY a.submitted_at DESC LIMIT ?", (limit,)):
        ev.append((r["ts"], "sent", r["aid"], f"{r['name']} ishni yubordi"))
    for r in conn.execute("SELECT a.id aid, a.submitted_at ts, u.name FROM attempts a JOIN users u ON u.id=a.user_id "
                          "WHERE a.rejected=1 ORDER BY a.submitted_at DESC LIMIT ?", (limit,)):
        ev.append((r["ts"] + 0.5, "ban", r["aid"], f"{r['name']}: ish qabul qilinmadi (imtihon to'xtatildi)"))
    for r in conn.execute("SELECT a.id aid, a.started_at ts, u.name FROM attempts a JOIN users u ON u.id=a.user_id "
                          "ORDER BY a.started_at DESC LIMIT ?", (limit,)):
        ev.append((r["ts"], "start", r["aid"], f"{r['name']} boshladi"))
    ev.sort(key=lambda e: e[0], reverse=True)
    return [dict(ts=e[0], kind=e[1], aid=e[2], text=e[3]) for e in ev[:limit]]


@app.get("/t/settings")
def teacher_settings_page(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    cfg = load_cfg(conn)
    return render(request, "t_settings.html", user=u, cfg=cfg, win=exam_window(conn), backups=list_backups(),
                  now_dtl=fmt_ts(time.time(), "%Y-%m-%dT%H:%M"),
                  writing_n=conn.execute("SELECT COUNT(*) c FROM attempts WHERE submitted_at IS NULL AND deadline>?",
                                         (int(time.time()),)).fetchone()["c"])


@app.post("/t/settings")
def teacher_settings(u=Depends(require("teacher")), conn=Depends(get_db),
                     duration_min: int = Form(60), q_count: int = Form(5),
                     max_per_q: int = Form(20), penalty_points: int = Form(10),
                     max_strikes: int = Form(3), exam_title: str = Form("")):
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('exam_title',?)", (" ".join(exam_title.split())[:120],))
    for k, v in dict(duration_min=max(1, duration_min), q_count=max(1, q_count), max_per_q=max(1, max_per_q),
                     penalty_points=penalty_points, max_strikes=max(1, max_strikes)).items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (str(max(0, v)), k))
    _settings["t"] = 0
    return back("/t/settings", "Sozlamalar saqlandi")


# ---- savollar ----
@app.get("/t/questions")
def teacher_questions(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    qs = conn.execute(
        "SELECT q.*, (SELECT COUNT(*) FROM assigned a WHERE a.question_id=q.id) asg, "
        "(SELECT COUNT(*) FROM attempt_q aq WHERE aq.question_id=q.id) given FROM questions q ORDER BY q.id").fetchall()
    return render(request, "t_questions.html", user=u, qs=qs)


@app.post("/t/questions/import")
def teacher_q_import(u=Depends(require("teacher")), conn=Depends(get_db), text: str = Form(...)):
    have = {" ".join(r["text"].split()).lower() for r in conn.execute("SELECT text FROM questions")}
    added = dup = 0
    for line in text.splitlines():
        line = line.strip()
        key = " ".join(line.split()).lower()
        if not line:
            continue
        if key in have:
            dup += 1
            continue
        have.add(key)
        conn.execute("INSERT INTO questions(text) VALUES(?)", (line[:2000],))
        added += 1
    return back("/t/questions", f"{added} ta savol qo'shildi" + (f", {dup} ta takror o'tkazib yuborildi" if dup else ""))


@app.post("/t/questions/edit/{qid}")
def teacher_q_edit(qid: int, u=Depends(require("teacher")), conn=Depends(get_db), text: str = Form(...)):
    text = text.strip()
    if text:
        conn.execute("UPDATE questions SET text=? WHERE id=?", (text[:2000], qid))
    return RedirectResponse(f"/t/questions?msg={quote(f'{qid}-savol yangilandi')}#q{qid}", status_code=303)


@app.post("/t/questions/delete/{qid}")
def teacher_q_delete(qid: int, u=Depends(require("teacher")), conn=Depends(get_db)):
    if conn.execute("SELECT 1 FROM attempt_q WHERE question_id=?", (qid,)).fetchone():
        return back("/t/questions", "Bu savolga talaba javob yozgan, o'chirib bo'lmaydi (tahrirlash mumkin)")
    conn.execute("BEGIN IMMEDIATE")
    try:
        moved = 0
        # biriktirilgan talabalarga o`rniga boshqa (ularda yo`q) savol
        for a in conn.execute("SELECT user_id, pos FROM assigned WHERE question_id=?", (qid,)).fetchall():
            mine = [r["question_id"] for r in conn.execute("SELECT question_id FROM assigned WHERE user_id=?", (a["user_id"],))]
            new = pick_questions(conn, 1, hard=set(mine) | {qid})
            conn.execute("DELETE FROM assigned WHERE user_id=? AND question_id=?", (a["user_id"], qid))
            if new:
                conn.execute("INSERT INTO assigned(user_id,question_id,pos) VALUES(?,?,?)", (a["user_id"], new[0], a["pos"]))
                conn.execute("UPDATE questions SET used=used+1 WHERE id=?", (new[0],))
                moved += 1
        conn.execute("DELETE FROM questions WHERE id=?", (qid,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return back("/t/questions", f"Savol o'chirildi" + (f"; {moved} talabaga o'rniga boshqa savol berildi" if moved else ""))


# ---- talabalar ----
def make_login(conn, name):
    parts = name.split()
    base = ".".join(p for p in (slug(parts[0]) if parts else "", slug(parts[1]) if len(parts) > 1 else "") if p) or "talaba"
    login, i = base, 1
    while conn.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
        i += 1
        login = f"{base}{i}"
    return login


def create_student(conn, name, grp=None, login=None, pw=None):
    name = " ".join(name.split())
    login = (login or "").strip().lower() or make_login(conn, name)
    if not re.fullmatch(r"[a-z0-9._-]{2,60}", login):
        return None, f"Login noto'g'ri: {login} (faqat lotin harf, raqam, . _ -)"
    if conn.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
        return None, f"Login band: {login}"
    pw = (pw or "").strip() or gen_pw(name)
    add_user(conn, "student", name, login, pw)
    conn.execute("UPDATE users SET grp=? WHERE login=?", ((grp or "").strip() or None, login))
    return login, None


@app.get("/t/students")
def teacher_students(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    st = conn.execute(
        "SELECT u.*, a.id aid, a.submitted_at, a.graded, a.disq, a.rejected FROM users u LEFT JOIN attempts a ON a.user_id=u.id "
        "WHERE u.role='student' ORDER BY u.grp, u.name").fetchall()
    st = [dict(s, state=state_of(s)) for s in st]
    groups = sorted({s["grp"] for s in st if s["grp"]})
    return render(request, "t_students.html", user=u, st=st, groups=groups)


@app.post("/t/students/one")
def teacher_student_one(u=Depends(require("teacher")), conn=Depends(get_db), name: str = Form(...),
                        grp: str = Form(""), login: str = Form(""), password: str = Form("")):
    lg, err = create_student(conn, name, grp, login, password)
    return back("/t/students", err or f"Qo'shildi: {name} — login {lg}")


@app.post("/t/students/add")
def teacher_students_add(u=Depends(require("teacher")), conn=Depends(get_db), text: str = Form(...), grp: str = Form("")):
    """Har qator: Ism; login; parol; guruh  (Excel'dan nusxa bo`lsa tab bilan ham bo`ladi). Faqat ism ham yetadi."""
    added, errs = 0, []
    for line in text.splitlines():
        parts = [p.strip() for p in re.split(r"[;\t]", line)]
        if not parts or not parts[0]:
            continue
        g = parts[3] if len(parts) > 3 and parts[3] else grp
        lg, err = create_student(conn, parts[0], g, parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")
        if err:
            errs.append(err)
        else:
            added += 1
    return back("/t/students", f"{added} ta talaba qo'shildi" + (f"; {len(errs)} ta xato: " + "; ".join(errs[:5]) if errs else ""))


@app.get("/t/students/{uid}")
def teacher_student(uid: int, request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    s = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (uid,)).fetchone()
    if not s:
        return back("/t/students")
    att = conn.execute("SELECT * FROM attempts WHERE user_id=?", (uid,)).fetchone()
    asg = conn.execute("SELECT a.pos, q.id, q.text FROM assigned a JOIN questions q ON q.id=a.question_id "
                       "WHERE a.user_id=? ORDER BY a.pos", (uid,)).fetchall()
    hist = [dict(h, d=json.loads(h["data"])) for h in
            conn.execute("SELECT * FROM history WHERE user_id=? ORDER BY ts", (uid,))]
    return render(request, "t_student.html", user=u, s=s, att=att, asg=asg, hist=hist)


@app.post("/t/students/{uid}/edit")
def teacher_student_edit(uid: int, u=Depends(require("teacher")), conn=Depends(get_db), name: str = Form(...),
                         grp: str = Form(""), login: str = Form(...)):
    login = login.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{2,60}", login):
        return back(f"/t/students/{uid}", "Login noto'g'ri (faqat lotin harf, raqam, . _ -)")
    if conn.execute("SELECT 1 FROM users WHERE login=? AND id<>?", (login, uid)).fetchone():
        return back(f"/t/students/{uid}", f"Login band: {login}")
    conn.execute("UPDATE users SET name=?, grp=?, login=? WHERE id=? AND role='student'",
                 (" ".join(name.split()), grp.strip() or None, login, uid))
    return back(f"/t/students/{uid}", "Saqlandi")


@app.post("/t/students/{uid}/password")
def teacher_student_pw(uid: int, u=Depends(require("teacher")), conn=Depends(get_db), password: str = Form("")):
    s = conn.execute("SELECT * FROM users WHERE id=? AND role='student'", (uid,)).fetchone()
    if not s:
        return back("/t/students")
    pw = password.strip() or gen_pw(s["name"])
    salt = secrets.token_hex(16)
    conn.execute("UPDATE users SET salt=?, pw_hash=?, pw_plain=? WHERE id=?", (salt, hash_pw(pw, salt), seal_pw(pw), uid))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    clear_fails(conn, s["login"])
    return back(f"/t/students/{uid}", f"Yangi parol: {pw}")


@app.post("/t/students/delete/{uid}")
def teacher_student_delete(uid: int, u=Depends(require("teacher")), conn=Depends(get_db)):
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    att = conn.execute("SELECT id FROM attempts WHERE user_id=?", (uid,)).fetchone()
    if att:
        conn.execute("DELETE FROM violations WHERE attempt_id=?", (att["id"],))
        conn.execute("DELETE FROM snaps WHERE attempt_id=?", (att["id"],))
        conn.execute("DELETE FROM attempt_q WHERE attempt_id=?", (att["id"],))
        conn.execute("DELETE FROM attempts WHERE id=?", (att["id"],))
    conn.execute("DELETE FROM assigned WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM history WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=? AND role='student'", (uid,))
    return back("/t/students", "Talaba o'chirildi")


@app.get("/t/students.csv")
def teacher_students_csv(grp: str = "", u=Depends(require("teacher")), conn=Depends(get_db)):
    q = "SELECT grp, name, login, pw_plain FROM users WHERE role='student'" + (" AND grp=?" if grp else "") + " ORDER BY grp, name"
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Guruh", "F.I.Sh.", "Login", "Parol"])
    for r in conn.execute(q, (grp,) if grp else ()):
        w.writerow([r["grp"] or "", r["name"], r["login"], reveal_pw(r["pw_plain"]) or ""])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=login_parollar{('_' + slug(grp)) if grp else ''}.csv"})


# ---- urinish: tekshirish, qayta topshirish, vaqt ----
@app.get("/t/attempt/{aid}")
def teacher_attempt(aid: int, request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    q = "SELECT a.*, u.name, u.login, u.grp FROM attempts a JOIN users u ON u.id=a.user_id WHERE a.id=?"
    att = conn.execute(q, (aid,)).fetchone()
    if not att:
        return back("/t/review", "Urinish topilmadi (qayta topshirishga ruxsat berilgan bo'lishi mumkin)")
    finalize_if_expired(conn, att)
    att = conn.execute(q, (aid,)).fetchone()
    items = conn.execute(
        "SELECT aq.*, q.text FROM attempt_q aq JOIN questions q ON q.id=aq.question_id WHERE attempt_id=? ORDER BY pos",
        (aid,)).fetchall()
    viol = conn.execute("SELECT * FROM violations WHERE attempt_id=? ORDER BY ts", (aid,)).fetchall()
    strikes = strike_count(conn, aid)
    sims = {it["id"]: similar_answers(conn, it) for it in items}
    hist = [dict(h, d=json.loads(h["data"])) for h in
            conn.execute("SELECT * FROM history WHERE user_id=? ORDER BY ts", (att["user_id"],))]
    return render(request, "t_attempt.html", user=u, att=att, items=items, viol=viol, mx=setting(conn, "max_per_q"),
                  strikes=strikes, deduct=calc_deduct(conn, att, strikes), hist=hist,
                  suggest=default_carry(conn, att), left=max(0, int((att["deadline"] - time.time()) // 60)),
                  todo=conn.execute("SELECT COUNT(*) c FROM attempts WHERE graded=0 AND rejected=0 AND submitted_at IS NOT NULL").fetchone()["c"],
                  carry=att["carry"] or 0, dur=setting(conn, "duration_min"), labels=KIND_LABELS,
                  flags=[v for v in viol if v["kind"] in FLAG_KINDS], sims=sims, chart=typing_chart(conn, att, viol))


def shingles(text):
    w = re.findall(r"\w+", (text or "").lower())
    return {" ".join(w[i:i + 3]) for i in range(len(w) - 2)}


def similar_answers(conn, it):
    """Shu savolga boshqa talabalar yozgan javoblardan eng o'xshashlari (so'z 3-gramma Jaccard)."""
    if len((it["answer"] or "").strip()) < SIM_MIN:
        return []
    mine, out = shingles(it["answer"]), []
    for r in conn.execute("SELECT aq.answer, u.name, a.id aid FROM attempt_q aq JOIN attempts a ON a.id=aq.attempt_id "
                          "JOIN users u ON u.id=a.user_id WHERE aq.question_id=? AND aq.attempt_id<>? AND LENGTH(aq.answer)>=?",
                          (it["question_id"], it["attempt_id"], SIM_MIN)):
        other = shingles(r["answer"])
        if mine and other:
            j = len(mine & other) / len(mine | other)
            if j >= SIM_FLAG:
                out.append({"name": r["name"], "aid": r["aid"], "pct": round(j * 100)})
    return sorted(out, key=lambda x: -x["pct"])[:5]


def typing_chart(conn, att, viol):
    """Javoblar hajmi vaqt bo'yicha (SVG nuqtalari): tabiiy yozish asta o'sadi, ko'chirilgan matn sakraydi."""
    snaps = conn.execute("SELECT ts, chars FROM snaps WHERE attempt_id=? ORDER BY ts", (att["id"],)).fetchall()
    if not snaps:
        return None
    t0 = att["started_at"]
    t1 = max(att["submitted_at"] or att["deadline"], snaps[-1]["ts"], t0 + 60)
    top = max(max(r["chars"] for r in snaps), 1)
    W, H = 600, 120
    X = lambda t: round(min(max(t - t0, 0), t1 - t0) / (t1 - t0) * W, 1)
    Y = lambda c: round(H - c / top * (H - 6), 1)
    pts = [(0, H)] + [(X(r["ts"]), Y(r["chars"])) for r in snaps]
    marks = [{"x": X(v["ts"]), "fast": v["kind"] == "fast_text"} for v in viol
             if v["penalty"] == 1 or v["kind"] == "fast_text"]
    return {"W": W, "H": H, "pts": " ".join(f"{x},{y}" for x, y in pts), "marks": marks, "top": top,
            "mins": round((t1 - t0) / 60)}


def calc_deduct(conn, att, strikes):
    """Jarima: har bir ogohlantirish uchun penalty_points; chetlatilsa jami 0 (alohida qayd etiladi)."""
    ms, pp = setting(conn, "max_strikes"), setting(conn, "penalty_points")
    return pp * min(strikes, ms - 1)


def default_carry(conn, att):
    """Qayta topshirishda taklif qilinadigan chegirma: oldingi chegirma + jarima x ogohlantirishlar."""
    return (att["carry"] or 0) + setting(conn, "penalty_points") * strike_count(conn, att["id"])


@app.post("/t/attempt/{aid}/grade")
async def teacher_grade(aid: int, request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    form = await request.form()
    att = conn.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if not att:
        return back("/t/results", "Urinish topilmadi")
    if att["rejected"]:
        return back(f"/t/attempt/{aid}", "Bu ish qabul qilinmagan: baholab bo'lmaydi. Qayta topshirishga ruxsat bering yoki ishni qayta oching.")
    mx = setting(conn, "max_per_q")
    total = 0.0
    for r in conn.execute("SELECT id FROM attempt_q WHERE attempt_id=?", (aid,)).fetchall():
        try:
            sc = float(str(form.get(f"score_{r['id']}", "0")).replace(",", "."))
        except ValueError:
            sc = 0.0
        sc = max(0.0, min(float(mx), sc))
        total += sc
        conn.execute("UPDATE attempt_q SET score=?, comment=? WHERE id=?",
                     (sc, str(form.get(f"comment_{r['id']}", ""))[:1500], r["id"]))
    deduct = calc_deduct(conn, att, strike_count(conn, aid))
    final = 0.0 if att["disq"] else max(0.0, total - deduct - (att["carry"] or 0))
    conn.execute("UPDATE attempts SET total=?, deduct=?, comment=?, graded=1, graded_at=? WHERE id=?",
                 (final, deduct, str(form.get("comment", ""))[:2000], int(time.time()), aid))
    if form.get("next"):
        nxt = next_ungraded(conn, aid)
        if nxt:
            return back(f"/t/attempt/{nxt}", "Baholandi. Keyingi ish ochildi")
        return back("/t/review", "Baholandi. Tekshirilmagan ish qolmadi")
    return back("/t/review", "Baholandi")


def next_ungraded(conn, aid=None):
    """Tekshirilmagan (yuborilgan, baholanmagan) keyingi ish: guruh va ism tartibida, joriydan keyin."""
    rows = [r["id"] for r in conn.execute(
        "SELECT a.id FROM attempts a JOIN users u ON u.id=a.user_id WHERE a.graded=0 AND a.rejected=0 AND "
        "(a.submitted_at IS NOT NULL OR a.deadline < ?) ORDER BY u.grp, u.name", (int(time.time()),))]
    if aid in rows:
        i = rows.index(aid)
        rows = rows[i + 1:] + rows[:i]
    return rows[0] if rows else None


def do_retake(conn, aid, carry=None, new_q=True):
    """Eski urinishni tarixga ko`chiradi va talabaga qayta topshirishga ruxsat beradi.
    carry: keyingi natijadan ayriladigan ball (None = default_carry)."""
    att = conn.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if not att:
        return None
    uid = att["user_id"]
    items = [dict(r) for r in conn.execute(
        "SELECT aq.question_id, aq.pos, aq.answer, aq.score, aq.comment, q.text FROM attempt_q aq "
        "JOIN questions q ON q.id=aq.question_id WHERE attempt_id=? ORDER BY pos", (aid,))]
    snap = dict(att, strikes=strike_count(conn, aid), items=items,
                violations=[dict(r) for r in conn.execute("SELECT ts, kind, penalty FROM violations WHERE attempt_id=? ORDER BY ts", (aid,))])
    carry = default_carry(conn, att) if carry is None else max(0.0, carry)
    conn.execute("INSERT INTO history(user_id,ts,carry,data) VALUES(?,?,?,?)",
                 (uid, int(time.time()), carry, json.dumps(snap, ensure_ascii=False)))
    if new_q:
        seen = {i["question_id"] for i in items}
        for h in conn.execute("SELECT data FROM history WHERE user_id=?", (uid,)):
            seen |= {i["question_id"] for i in json.loads(h["data"]).get("items", [])}
        new = pick_questions(conn, setting(conn, "q_count"), avoid=seen)
        conn.execute("DELETE FROM assigned WHERE user_id=?", (uid,))
        for pos, qid in enumerate(new, 1):
            conn.execute("INSERT INTO assigned(user_id,question_id,pos) VALUES(?,?,?)", (uid, qid, pos))
    elif not conn.execute("SELECT 1 FROM assigned WHERE user_id=?", (uid,)).fetchone():
        for i in items:
            conn.execute("INSERT INTO assigned(user_id,question_id,pos) VALUES(?,?,?)", (uid, i["question_id"], i["pos"]))
    conn.execute("DELETE FROM violations WHERE attempt_id=?", (aid,))
    conn.execute("DELETE FROM snaps WHERE attempt_id=?", (aid,))
    conn.execute("DELETE FROM attempt_q WHERE attempt_id=?", (aid,))
    conn.execute("DELETE FROM attempts WHERE id=?", (aid,))
    # ustoz ruxsati: imtihon to'xtatilgan yoki yopilgan bo'lsa ham shu talaba boshlay oladi
    conn.execute("UPDATE users SET carry=?, retakes=COALESCE(retakes,0)+1, reopen=1 WHERE id=?", (carry, uid))
    return uid


def parse_carry(v):
    v = str(v or "").strip().replace(",", ".")
    try:
        return float(v) if v else None
    except ValueError:
        return None


@app.post("/t/attempt/{aid}/retake")
def teacher_retake(aid: int, u=Depends(require("teacher")), conn=Depends(get_db),
                   carry: str = Form(""), new_q: str = Form("")):
    conn.execute("BEGIN IMMEDIATE")
    try:
        uid = do_retake(conn, aid, parse_carry(carry), bool(new_q))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if not uid:
        return back("/t/results", "Urinish topilmadi")
    c = conn.execute("SELECT name, carry FROM users WHERE id=?", (uid,)).fetchone()
    return back("/t/results", f"{c['name']}: qayta topshirishga ruxsat berildi, chegirma -{c['carry']:g} ball")


@app.post("/t/attempt/{aid}/reset")
def teacher_reset(aid: int, u=Depends(require("teacher")), conn=Depends(get_db)):
    """Eski tugma: chegirmasiz, o'sha savollar bilan qayta topshirish."""
    return teacher_retake(aid, u, conn, carry="0", new_q="")


def retake_many(conn, where, grp, carry, new_q):
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT a.id FROM attempts a JOIN users u ON u.id=a.user_id WHERE " + where + (" AND u.grp=?" if grp else ""),
            (grp,) if grp else ())]
        for aid in ids:
            do_retake(conn, aid, carry, new_q)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(ids)


@app.post("/t/retake-disq")
def teacher_retake_disq(u=Depends(require("teacher")), conn=Depends(get_db),
                        carry: str = Form(""), new_q: str = Form(""), grp: str = Form("")):
    n = retake_many(conn, "a.disq=1 AND a.rejected=0", grp, parse_carry(carry), bool(new_q))
    return back("/t/results", f"{n} ta chetlatilgan talabaga qayta topshirishga ruxsat berildi")


@app.post("/t/retake-rejected")
def teacher_retake_rejected(u=Depends(require("teacher")), conn=Depends(get_db),
                            carry: str = Form("0"), new_q: str = Form(""), grp: str = Form("")):
    """Imtihon to'xtatilganda qabul qilinmagan ishlar: talabalar qaytadan topshiradi (sukut bo'yicha chegirmasiz)."""
    n = retake_many(conn, "a.rejected=1", grp, parse_carry(carry) or 0.0, bool(new_q))
    return back("/t/results#rejected", f"{n} ta talabaga nazorat ishi qayta berildi: ular «Boshlash» ni bosib qaytadan yozadi")


@app.post("/t/stop")
def teacher_stop(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    """Imtihonni to'xtatish: shu paytgacha yuborilgan ishlar qabul qilinadi, hali yozayotganlarniki qabul qilinmaydi."""
    now = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        # vaqti allaqachon tugagan ishlar o'z vaqtida yuborilgan hisoblanadi
        conn.execute("UPDATE attempts SET submitted_at=deadline WHERE submitted_at IS NULL AND deadline<=?", (now,))
        n = conn.execute("UPDATE attempts SET submitted_at=?, rejected=1 WHERE submitted_at IS NULL", (now,)).rowcount
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('stopped_at',?)", (str(now),))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    _settings["t"] = 0
    to = urlparse(request.headers.get("referer") or "").path
    return back(to if to.startswith("/t") else "/t",
                f"Imtihon to'xtatildi. {n} ta yozilayotgan ish qabul qilinmadi" if n else
                "Imtihon to'xtatildi. Yozilayotgan ish yo'q edi")


@app.post("/t/resume")
def teacher_resume(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('stopped_at','0')")
    _settings["t"] = 0
    to = urlparse(request.headers.get("referer") or "").path
    return back(to if to.startswith("/t") else "/t", "Imtihon qayta ochildi: kirmagan talabalar boshlay oladi")


@app.post("/t/attempt/{aid}/extend")
def teacher_extend(aid: int, u=Depends(require("teacher")), conn=Depends(get_db), minutes: int = Form(10)):
    """Vaqt qo`shish; yuborilgan yoki chetlatilgan (baholanmagan) ishni qayta ochish."""
    att = conn.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if not att:
        return back("/t/results", "Urinish topilmadi")
    if att["graded"]:
        return back(f"/t/attempt/{aid}", "Baholangan ishni qayta ochib bo'lmaydi. Qayta topshirishdan foydalaning.")
    minutes = max(1, min(600, minutes))
    now = int(time.time())
    deadline = max(att["deadline"], now) + minutes * 60
    if att["disq"]:
        # chetlatishni bekor qilish: oxirgi jarimalar "kechirildi" (penalty=2), keyingi chiqishda yana chetlatiladi
        extra = strike_count(conn, aid) - (setting(conn, "max_strikes") - 1)
        if extra > 0:
            conn.execute("UPDATE violations SET penalty=2 WHERE id IN (SELECT id FROM violations WHERE attempt_id=? "
                         "AND penalty=1 ORDER BY ts DESC LIMIT ?)", (aid, extra))
    conn.execute("UPDATE attempts SET deadline=?, submitted_at=NULL, disq=0, rejected=0 WHERE id=?", (deadline, aid))
    msg = f"+{minutes} daqiqa qo'shildi" + (" va ish qayta ochildi" if att["submitted_at"] is not None else "")
    return back(f"/t/attempt/{aid}", msg)


@app.get("/t/export.csv")
def teacher_export(u=Depends(require("teacher")), conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT u.grp, u.name, u.login, a.submitted_at, a.total, a.comment, a.graded, a.disq, a.deduct, a.carry, a.try_no, a.rejected, "
        "(SELECT COUNT(*) FROM violations v WHERE v.attempt_id=a.id AND v.penalty=1) viol "
        "FROM users u LEFT JOIN attempts a ON a.user_id=u.id WHERE u.role='student' ORDER BY u.grp, u.name").fetchall()
    mx = setting(conn, "max_per_q") * setting(conn, "q_count")
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Guruh", "Ism", "Login", "Holat", "Urinish", "Ball", f"Maks({mx})", "Baho", "Ogohlantirish",
                "Jarima", "Qayta topshirish chegirmasi", "Izoh"])
    for r in rows:
        st = ("Kirmagan" if r["try_no"] is None else "Yozmoqda" if r["submitted_at"] is None else
              "Qabul qilinmagan" if r["rejected"] else "Chetlatilgan" if r["disq"] else
              "Baholangan" if r["graded"] else "Tekshirilmagan")
        w.writerow([r["grp"] or "", r["name"], r["login"], st, r["try_no"] or "",
                    ("%g" % r["total"]) if r["graded"] else "", mx,
                    grade_of(r["total"], mx) if r["graded"] else "", r["viol"] if r["try_no"] else "",
                    ("%g" % r["deduct"]) if r["graded"] and r["deduct"] else "",
                    ("%g" % r["carry"]) if r["carry"] else "", r["comment"] or ""])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=natijalar.csv"})



@app.post("/t/publish")
def teacher_publish(request: Request, u=Depends(require("teacher")), conn=Depends(get_db), on: int = Form(...)):
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('results_published',?)", ("1" if on else "0",))
    _settings["t"] = 0
    to = urlparse(request.headers.get("referer") or "").path  # tugma bosilgan sahifaga qaytadi
    return back(to if to.startswith("/t") else "/t",
                "Natijalar e'lon qilindi: o'quvchilar ball va izohlarni ko'radi" if on else "Natijalar o'quvchilardan yashirildi")


@app.post("/t/window")
def teacher_window(u=Depends(require("teacher")), conn=Depends(get_db),
                   open_at: str = Form(""), close_at: str = Form(""), clear: str = Form("")):
    try:
        o, c = (0, 0) if clear else (parse_local(open_at), parse_local(close_at))
    except ValueError:
        return back("/t/settings#window", "Sana yoki vaqt noto'g'ri kiritildi")
    if o and c and c <= o:
        return back("/t/settings#window", "Yopilish vaqti ochilish vaqtidan keyin bo'lishi kerak")
    conn.execute("BEGIN")
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('open_at',?)", (str(o),))
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('close_at',?)", (str(c),))
    cut = 0
    if c:  # yozayotganlarning vaqti ham yopilish vaqtidan oshmaydi
        cut = conn.execute("UPDATE attempts SET deadline=? WHERE submitted_at IS NULL AND deadline>?", (c, c)).rowcount
    conn.execute("COMMIT")
    _settings["t"] = 0
    if clear:
        return back("/t/settings#window", "Vaqt cheklovi olib tashlandi: imtihon doim ochiq")
    msg = "Imtihon vaqti saqlandi" + (f": {fmt_ts(o, '%d.%m %H:%M')} da ochiladi" if o else "") + \
          (f", {fmt_ts(c, '%d.%m %H:%M')} da yopiladi" if c else "") + (f". {cut} ta yozayotgan talaba vaqti qisqardi" if cut else "")
    return back("/t/settings#window", msg)


@app.post("/t/password")
def teacher_password(request: Request, u=Depends(require("teacher")), conn=Depends(get_db),
                     current: str = Form(...), new: str = Form(...), confirm: str = Form(...)):
    if not secrets.compare_digest(u["pw_hash"], hash_pw(current, u["salt"])):
        return back("/t/settings#security", "Joriy parol noto'g'ri")
    if new != confirm:
        return back("/t/settings#security", "Yangi parol va takrori mos emas")
    if len(new) < 8 or new.isdigit() or new.isalpha():
        return back("/t/settings#security", "Parol kamida 8 belgi bo'lsin va harf hamda raqamdan iborat bo'lsin")
    salt = secrets.token_hex(16)
    conn.execute("BEGIN")
    conn.execute("UPDATE users SET salt=?, pw_hash=?, pw_plain=NULL WHERE id=?", (salt, hash_pw(new, salt), u["id"]))
    # boshqa qurilmalardagi ustoz sessiyalari yopiladi, joriy sessiya qoladi
    conn.execute("DELETE FROM sessions WHERE user_id=? AND token<>?", (u["id"], request.cookies.get("sid", "")))
    conn.execute("COMMIT")
    return back("/t/settings#security", "Parol o'zgartirildi. Boshqa qurilmalardagi ustoz sessiyalari yopildi")


# ---- zaxira ----
_BK_NAME = re.compile(r"^nazorat_(kunlik|qolda)_[0-9_-]+\.db$")


def make_backup(kind="qolda"):
    """SQLite backup API: ishlayotgan bazaning izchil nusxasi (yozuvchilarni to'xtatmaydi)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = fmt_ts(time.time(), "%Y-%m-%d" if kind == "kunlik" else "%Y-%m-%d_%H-%M-%S")
    dst = BACKUP_DIR / f"nazorat_{kind}_{stamp}.db"
    tmp = dst.with_suffix(".tmp")
    src = sqlite3.connect(DB_PATH, timeout=30)
    out = sqlite3.connect(str(tmp))
    try:
        src.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")
    finally:
        out.close()
        src.close()
    os.replace(tmp, dst)
    for k, keep in (("kunlik", BACKUP_KEEP), ("qolda", 10)):
        for old in sorted(BACKUP_DIR.glob(f"nazorat_{k}_*.db"))[:-keep]:
            old.unlink(missing_ok=True)
    return dst.name


def list_backups():
    if not BACKUP_DIR.is_dir():
        return []
    files = [f for f in BACKUP_DIR.iterdir() if _BK_NAME.match(f.name)]
    return [{"name": f.name, "kb": max(1, f.stat().st_size // 1024), "ts": int(f.stat().st_mtime),
             "kind": "Kunlik" if "_kunlik_" in f.name else "Qo'lda"}
            for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)]


def backup_loop():
    """Har kuni bitta avtomatik zaxira. Bir nechta worker bo'lsa fayl qulfi bilan faqat bittasi yozadi."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    time.sleep(20)
    while True:
        try:
            if not (BACKUP_DIR / f"nazorat_kunlik_{fmt_ts(time.time(), '%Y-%m-%d')}.db").exists():
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                with open(BACKUP_DIR / ".lock", "w") as lock:
                    if fcntl:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                    if not (BACKUP_DIR / f"nazorat_kunlik_{fmt_ts(time.time(), '%Y-%m-%d')}.db").exists():
                        print("[nazorat] kunlik zaxira:", make_backup("kunlik"), flush=True)
        except Exception as e:  # zaxira xatosi ilovani to'xtatmasin
            print("[nazorat] zaxira xatosi:", e, flush=True)
        time.sleep(600)


@app.post("/t/backup")
def teacher_backup(u=Depends(require("teacher"))):
    name = make_backup("qolda")
    return back("/t/settings#backup", f"Zaxira olindi: {name}")


@app.get("/t/backup/{name}")
def teacher_backup_file(name: str, u=Depends(require("teacher"))):
    f = BACKUP_DIR / name
    if not _BK_NAME.match(name) or not f.is_file():
        return back("/t/settings#backup", "Zaxira fayli topilmadi")
    return FileResponse(f, media_type="application/octet-stream", filename=name)


# ---- Excel hisobot ----
@app.get("/t/export.xlsx")
def teacher_export_xlsx(u=Depends(require("teacher")), conn=Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    rows, stats = results_data(conn)
    cfg = load_cfg(conn)
    qn, mq = cfg["q_count"], cfg["max_per_q"]
    mx = qn * mq
    scores = {}
    for r in conn.execute("SELECT attempt_id, pos, score FROM attempt_q"):
        scores.setdefault(r["attempt_id"], {})[r["pos"]] = r["score"]
    extra = {r["id"]: r for r in conn.execute("SELECT id, deduct, comment FROM attempts")}

    title = cfg["exam_title"] or "Nazorat ishi"
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    head_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="C9D1DD")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    gfill = {5: "D1FAE5", 4: "DBEAFE", 3: "FEF3C7", 2: "FEE2E2"}
    sfill = {"Baholangan": "D1FAE5", "Tekshirilmagan": "FEF3C7", "Chetlatilgan": "FEE2E2", "Yozmoqda": "E0E7FF",
             "Qabul qilinmagan": "FECACA"}

    def table(ws, top, headers, data, widths):
        ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=14)
        ws.cell(row=2, column=1, value=f"Hisobot: {fmt_ts(time.time(), '%d.%m.%Y %H:%M')} · maks. ball {mx}").font = Font(color="6B7280")
        for j, h in enumerate(headers, 1):
            c = ws.cell(row=top, column=j, value=h)
            c.fill, c.font, c.border = head_fill, head_font, box
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[top].height = 32
        for i, row in enumerate(data, top + 1):
            for j, v in enumerate(row, 1):
                c = ws.cell(row=i, column=j, value=v)
                c.border = box
                c.alignment = Alignment(vertical="center", wrap_text=isinstance(v, str) and len(v) > 40)
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = ws.cell(row=top + 1, column=1)
        if data:
            ws.auto_filter.ref = f"A{top}:{get_column_letter(len(headers))}{top + len(data)}"

    wb = Workbook()
    ws = wb.active
    ws.title = "Natijalar"
    heads = ["№", "Guruh", "F.I.Sh.", "Login", "Holat", "Urinish"] + [f"{k}-savol" for k in range(1, qn + 1)] + \
            ["Jarima", "Chegirma", f"Yakuniy ball ({mx})", "Baho", "Ogohlantirish", "Boshladi", "Yubordi", "Ustoz izohi"]
    data = []
    for n, r in enumerate(rows, 1):
        sc = scores.get(r["aid"], {})
        ex = extra.get(r["aid"])
        g = grade_of(r["total"], mx) if r["graded"] else None
        data.append([n, r["grp"] or "", r["name"], r["login"], r["state"], r["try_no"] or None]
                    + [sc.get(k) if r["graded"] else None for k in range(1, qn + 1)]
                    + [(ex["deduct"] or None) if ex and r["graded"] else None, r["carry"] or None,
                       r["total"] if r["graded"] else None, g, r["viol"] if r["aid"] else None,
                       fmt_ts(r["started_at"], "%d.%m.%Y %H:%M") if r["started_at"] else None,
                       fmt_ts(r["submitted_at"], "%d.%m.%Y %H:%M") if r["submitted_at"] else None,
                       (ex["comment"] or None) if ex else None])
    table(ws, 4, heads, data, [5, 9, 34, 22, 15, 8] + [8] * qn + [8, 9, 12, 7, 12, 16, 16, 40])
    gcol, tcol, scol = 6 + qn + 4, 6 + qn + 3, 5
    for i, row in enumerate(data, 5):
        st = row[4]
        if st in sfill:
            ws.cell(row=i, column=scol).fill = PatternFill("solid", fgColor=sfill[st])
        g = row[gcol - 1]
        if g:
            c = ws.cell(row=i, column=gcol)
            c.fill, c.font = PatternFill("solid", fgColor=gfill[g]), Font(bold=True)
            c.alignment = Alignment(horizontal="center")
        ws.cell(row=i, column=tcol).font = Font(bold=True)
        for j in range(7, 7 + qn + 3):
            ws.cell(row=i, column=j).number_format = "0.##"

    # guruhlar
    wg = wb.create_sheet("Guruhlar")
    gdata = []
    for grp in sorted({r["grp"] or "—" for r in rows}, key=lambda g: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", g)]):
        gr = [r for r in rows if (r["grp"] or "—") == grp]
        tt = [r["total"] for r in gr if r["graded"]]
        gg = [grade_of(t, mx) for t in tt]
        gdata.append([grp, len(gr), sum(1 for r in gr if r["state"] in SUBMITTED), len(tt),
                      round(sum(tt) / len(tt), 1) if tt else None, max(tt) if tt else None, min(tt) if tt else None,
                      gg.count(5), gg.count(4), gg.count(3), gg.count(2),
                      round(sum(1 for g in gg if g >= 3) / len(gg) * 100) if gg else None])
    table(wg, 4, ["Guruh", "Talabalar", "Topshirdi", "Baholandi", "O'rtacha", "Eng yuqori", "Eng past",
                  "5", "4", "3", "2", "O'zlashtirish %"], gdata, [12, 11, 11, 11, 10, 11, 10, 6, 6, 6, 6, 15])

    # baholar va taqsimot (diagramma bilan)
    wd = wb.create_sheet("Taqsimot")
    tt = [r["total"] for r in rows if r["graded"]]
    step = mx / 10 if mx else 10
    bins = []
    for i in range(10):
        lo, hi = i * step, (i + 1) * step
        bins.append([f"{lo:g}–{hi:g}", sum(1 for t in tt if lo <= t < hi or (i == 9 and t >= hi))])
    table(wd, 4, ["Ball oralig'i", "Talabalar soni"], bins, [16, 16])
    gl = [["5 (a'lo)", 0], ["4 (yaxshi)", 0], ["3 (qoniqarli)", 0], ["2 (qoniqarsiz)", 0]]
    for t in tt:
        gl[5 - grade_of(t, mx)][1] += 1
    for j, h in enumerate(["Baho", "Soni"], 4):
        c = wd.cell(row=4, column=j, value=h)
        c.fill, c.font, c.border = head_fill, head_font, box
    for i, (k, v) in enumerate(gl, 5):
        wd.cell(row=i, column=4, value=k).border = box
        wd.cell(row=i, column=5, value=v).border = box
    wd.column_dimensions["D"].width, wd.column_dimensions["E"].width = 16, 10
    if tt:
        ch = BarChart()
        ch.title, ch.y_axis.title, ch.x_axis.title = "Ballar taqsimoti", "Talabalar", "Ball"
        ch.add_data(Reference(wd, min_col=2, min_row=4, max_row=14), titles_from_data=True)
        ch.set_categories(Reference(wd, min_col=1, min_row=5, max_row=14))
        ch.legend = None
        ch.height, ch.width = 8, 16
        wd.add_chart(ch, "G4")

    buf = io.BytesIO()
    wb.save(buf)
    fname = f"natijalar_{fmt_ts(time.time(), '%Y-%m-%d_%H-%M')}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


init_db()
