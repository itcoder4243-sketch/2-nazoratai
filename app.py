"""Nazorat ishi tizimi: FastAPI + SQLite. Yengil, bitta fayl."""
import csv
import hashlib
import io
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
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
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Nazorat ishi", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

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
DEFAULTS = {"duration_min": "60", "q_count": "5", "max_per_q": "20", "penalty_points": "10", "max_strikes": "3"}
# oynadan chiqish/skrinshot urinishlari jarima oladi; nusxa olish urinishlari faqat jurnalga yoziladi
PENALTY_KINDS = {"tab_hidden", "window_blur", "exit_fullscreen", "printscreen", "screenshot_key", "print"}
STRIKE_GAP = 5  # soniya: bitta chiqish bir necha hodisa bersa, bitta jarima


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL bilan xavfsiz; har yozuvda diskni kutmaydi (HDD da juda muhim)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")  # bir marta: bazaga yoziladi va saqlanib qoladi
    conn.executescript(SCHEMA)
    for ddl in ("ALTER TABLE violations ADD COLUMN penalty INTEGER DEFAULT 0",
                "ALTER TABLE attempts ADD COLUMN disq INTEGER DEFAULT 0",
                "ALTER TABLE attempts ADD COLUMN deduct REAL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN grp TEXT",
                "CREATE TABLE IF NOT EXISTS assigned(user_id INTEGER NOT NULL, question_id INTEGER NOT NULL, pos INTEGER NOT NULL, PRIMARY KEY(user_id, question_id))"):
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
    conn.close()


PBKDF2_ITERS = 10_000  # 100k bu kompyuterda ~200 ms/kirish edi: 200 kishi bir vaqtda kirsa 20+ soniya kutadi


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), PBKDF2_ITERS).hex()


def add_user(conn, role, name, login, pw, keep_plain=True):
    salt = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO users(role,name,login,salt,pw_hash,pw_plain) VALUES(?,?,?,?,?,?)",
        (role, name, login, salt, hash_pw(pw, salt), pw if keep_plain else None),
    )


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def setting(conn, key):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return int(r["value"]) if r else int(DEFAULTS[key])


class Redir(Exception):
    pass


@app.exception_handler(Redir)
async def _redir(request, exc):
    return RedirectResponse("/", status_code=303)


def current_user(request, conn):
    tok = request.cookies.get("sid")
    if not tok:
        return None
    return conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (tok,)
    ).fetchone()


def require(role):
    def dep(request: Request, conn=Depends(get_db)):
        u = current_user(request, conn)
        if not u or u["role"] != role:
            raise Redir()
        return u

    return dep


import starlette

_NEW_TR = tuple(int(x) for x in starlette.__version__.split(".")[:2]) >= (0, 29)


def render(request, name, **ctx):
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
templates.env.filters["dt"] = lambda t: time.strftime("%d.%m %H:%M", time.localtime(t)) if t else "-"

# ---------------- umumiy ----------------
_fails = {}


@app.get("/")
def index(request: Request, conn=Depends(get_db)):
    u = current_user(request, conn)
    if u:
        return RedirectResponse("/t" if u["role"] == "teacher" else "/s", status_code=303)
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, login: str = Form(...), password: str = Form(...), conn=Depends(get_db)):
    # tunnel orqali hamma so`rov 127.0.0.1 dan keladi: haqiqiy IP sarlavhadan, limit esa akkaunt bo`yicha
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    now = time.time()
    key = login.strip().lower()
    recent = [t for t in _fails.get(key, []) if now - t < 300]
    if len(recent) >= 8:
        return render(request, "login.html", error="Juda ko'p urinish. 5 daqiqadan keyin qayta urining.")
    u = conn.execute("SELECT * FROM users WHERE login=?", (login.strip().lower(),)).fetchone()
    if not u or not secrets.compare_digest(u["pw_hash"], hash_pw(password, u["salt"])):
        recent.append(now)
        _fails[key] = recent
        return render(request, "login.html", error="Login yoki parol noto'g'ri")
    tok = secrets.token_urlsafe(32)
    conn.execute("BEGIN")
    conn.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))  # bitta qurilma
    conn.execute("INSERT INTO sessions(token,user_id,created) VALUES(?,?,?)", (tok, u["id"], int(now)))
    conn.execute("COMMIT")
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("sid", tok, httponly=True, samesite="lax", max_age=60 * 60 * 12)
    return r


@app.get("/logout")
def logout(request: Request, conn=Depends(get_db)):
    tok = request.cookies.get("sid")
    if tok:
        conn.execute("DELETE FROM sessions WHERE token=?", (tok,))
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie("sid")
    return r


# ---------------- talaba ----------------
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
    if att and att["graded"]:
        items = conn.execute(
            "SELECT aq.*, q.text FROM attempt_q aq JOIN questions q ON q.id=aq.question_id "
            "WHERE attempt_id=? ORDER BY pos", (att["id"],)).fetchall()
    return render(request, "s_home.html", user=u, att=att, items=items, mx=mx,
                  q_count=setting(conn, "q_count"), minutes=setting(conn, "duration_min"),
                  now=int(time.time()))


@app.post("/s/start")
def student_start(u=Depends(require("student")), conn=Depends(get_db)):
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not conn.execute("SELECT 1 FROM attempts WHERE user_id=?", (u["id"],)).fetchone():
            n = setting(conn, "q_count")
            # eng kam ishlatilgan savollardan tasodifiy: takrorlanish minimal
            rows = conn.execute("SELECT question_id AS id FROM assigned WHERE user_id=? ORDER BY pos", (u["id"],)).fetchall()
            if not rows:  # oldindan biriktirilmagan bo`lsa: eng kam ishlatilganlardan tasodifiy
                rows = conn.execute("SELECT id FROM questions ORDER BY used ASC, RANDOM() LIMIT ?", (n,)).fetchall()
            if len(rows) < n:
                conn.execute("ROLLBACK")
                return RedirectResponse("/s", status_code=303)
            now = int(time.time())
            cur = conn.execute(
                "INSERT INTO attempts(user_id,started_at,deadline) VALUES(?,?,?)",
                (u["id"], now, now + setting(conn, "duration_min") * 60))
            aid = cur.lastrowid
            for i, r in enumerate(rows, 1):
                conn.execute("INSERT INTO attempt_q(attempt_id,question_id,pos) VALUES(?,?,?)", (aid, r["id"], i))
                conn.execute("UPDATE questions SET used=used+1 WHERE id=?", (r["id"],))
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
                  remaining=max(0, att["deadline"] - int(time.time())),
                  strikes=strike_count(conn, att["id"]), max_strikes=setting(conn, "max_strikes"),
                  penalty=setting(conn, "penalty_points"))


@app.post("/api/save")
def api_save(data: dict = Body(...), u=Depends(require("student")), conn=Depends(get_db)):
    # sync def: bloklovchi SQLite ishi hodisalar sikli (event loop) emas, thread pool da bajariladi
    att = get_attempt(conn, u["id"])
    if not att or att["submitted_at"] is not None:
        return JSONResponse({"ok": False, "submitted": True})
    conn.execute("BEGIN")
    try:
        for k, v in (data.get("answers") or {}).items():
            if str(k).isdigit():
                conn.execute("UPDATE attempt_q SET answer=? WHERE id=? AND attempt_id=?",
                             (str(v)[:6000], int(k), att["id"]))
        if data.get("final"):
            conn.execute("UPDATE attempts SET submitted_at=? WHERE id=?", (int(time.time()), att["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if data.get("final"):
        return JSONResponse({"ok": True, "submitted": True})
    return JSONResponse({"ok": True, "submitted": False, "remaining": max(0, att["deadline"] - int(time.time()))})


@app.post("/api/violation")
def api_violation(data: dict = Body(...), u=Depends(require("student")), conn=Depends(get_db)):
    att = get_attempt(conn, u["id"])
    if not att or att["submitted_at"] is not None:
        return JSONResponse({"submitted": True})
    kind = re.sub(r"[^a-z_]", "", str(data.get("kind", ""))[:30]) or "other"
    now = int(time.time())
    pen = 0
    if kind in PENALTY_KINDS:
        last = conn.execute("SELECT MAX(ts) m FROM violations WHERE attempt_id=? AND penalty=1",
                            (att["id"],)).fetchone()["m"]
        pen = 1 if (last is None or now - last >= STRIKE_GAP) else 0
    conn.execute("INSERT INTO violations(attempt_id,ts,kind,penalty) VALUES(?,?,?,?)", (att["id"], now, kind, pen))
    strikes = strike_count(conn, att["id"])
    ms, pp = setting(conn, "max_strikes"), setting(conn, "penalty_points")
    disq = bool(pen and strikes >= ms)
    if disq:
        conn.execute("UPDATE attempts SET submitted_at=?, disq=1 WHERE id=?", (now, att["id"]))
    return JSONResponse({"strike": bool(pen), "strikes": strikes, "max": ms, "penalty": pp,
                         "disq": disq, "submitted": disq})


def strike_count(conn, aid):
    return conn.execute("SELECT COUNT(*) c FROM violations WHERE attempt_id=? AND penalty=1", (aid,)).fetchone()["c"]


# ---------------- ustoz ----------------
@app.get("/t")
def teacher_home(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT u.id uid, u.name, u.login, a.id aid, a.started_at, a.deadline, a.submitted_at, a.total, a.graded, a.disq, "
        "(SELECT COUNT(*) FROM violations v WHERE v.attempt_id=a.id AND v.penalty=1) viol "
        "FROM users u LEFT JOIN attempts a ON a.user_id=u.id WHERE u.role='student' ORDER BY u.name").fetchall()
    rows = [dict(r) for r in rows]
    now = time.time()
    for r in rows:
        if r["aid"] and r["submitted_at"] is None and now > r["deadline"]:
            conn.execute("UPDATE attempts SET submitted_at=? WHERE id=?", (r["deadline"], r["aid"]))
            r["submitted_at"] = r["deadline"]
        r["state"] = ("Kirmagan" if not r["aid"] else "Yozmoqda" if r["submitted_at"] is None
                      else "Baholangan" if r["graded"] else "Chetlatilgan" if r["disq"] else "Tekshirilmagan")
    stats = {
        "students": len(rows),
        "questions": conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"],
        "pending": sum(1 for r in rows if r["state"] in ("Tekshirilmagan", "Chetlatilgan")),
        "graded": sum(1 for r in rows if r["state"] == "Baholangan"),
    }
    cfg = {k: setting(conn, k) for k in DEFAULTS}
    return render(request, "t_home.html", user=u, rows=rows, stats=stats, cfg=cfg,
                  mx_total=cfg["max_per_q"] * cfg["q_count"])


@app.post("/t/settings")
def teacher_settings(u=Depends(require("teacher")), conn=Depends(get_db),
                     duration_min: int = Form(60), q_count: int = Form(5),
                     max_per_q: int = Form(20), penalty_points: int = Form(10),
                     max_strikes: int = Form(3)):
    for k, v in dict(duration_min=duration_min, q_count=q_count, max_per_q=max_per_q,
                     penalty_points=penalty_points, max_strikes=max(1, max_strikes)).items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (str(max(0, v)), k))
    return RedirectResponse("/t", status_code=303)


@app.get("/t/questions")
def teacher_questions(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    qs = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    return render(request, "t_questions.html", user=u, qs=qs)


@app.post("/t/questions/import")
def teacher_q_import(u=Depends(require("teacher")), conn=Depends(get_db), text: str = Form(...)):
    for line in text.splitlines():
        line = line.strip()
        if line:
            conn.execute("INSERT INTO questions(text) VALUES(?)", (line[:2000],))
    return RedirectResponse("/t/questions", status_code=303)


@app.post("/t/questions/delete/{qid}")
def teacher_q_delete(qid: int, u=Depends(require("teacher")), conn=Depends(get_db)):
    if not conn.execute("SELECT 1 FROM attempt_q WHERE question_id=?", (qid,)).fetchone():
        conn.execute("DELETE FROM questions WHERE id=?", (qid,))
    return RedirectResponse("/t/questions", status_code=303)


def make_login(conn, name):
    base = re.sub(r"[^a-z0-9]", "", name.lower().replace("'", "").replace("ʻ", "").split()[0]) or "talaba"
    login, i = base, 1
    while conn.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
        i += 1
        login = f"{base}{i}"
    return login


@app.get("/t/students")
def teacher_students(request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    st = conn.execute("SELECT * FROM users WHERE role='student' ORDER BY name").fetchall()
    return render(request, "t_students.html", user=u, st=st)


@app.post("/t/students/add")
def teacher_students_add(u=Depends(require("teacher")), conn=Depends(get_db), text: str = Form(...)):
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if not parts[0]:
            continue
        name = parts[0]
        login = parts[1].lower() if len(parts) > 1 and parts[1] else make_login(conn, name)
        pw = parts[2] if len(parts) > 2 and parts[2] else "".join(secrets.choice(alphabet) for _ in range(6))
        try:
            add_user(conn, "student", name, login, pw)
        except sqlite3.IntegrityError:
            pass
    return RedirectResponse("/t/students", status_code=303)


@app.post("/t/students/delete/{uid}")
def teacher_student_delete(uid: int, u=Depends(require("teacher")), conn=Depends(get_db)):
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    att = conn.execute("SELECT id FROM attempts WHERE user_id=?", (uid,)).fetchone()
    if att:
        conn.execute("DELETE FROM violations WHERE attempt_id=?", (att["id"],))
        conn.execute("DELETE FROM attempt_q WHERE attempt_id=?", (att["id"],))
        conn.execute("DELETE FROM attempts WHERE id=?", (att["id"],))
    conn.execute("DELETE FROM users WHERE id=? AND role='student'", (uid,))
    return RedirectResponse("/t/students", status_code=303)


@app.get("/t/attempt/{aid}")
def teacher_attempt(aid: int, request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    att = conn.execute("SELECT a.*, u.name, u.login FROM attempts a JOIN users u ON u.id=a.user_id WHERE a.id=?",
                       (aid,)).fetchone()
    if not att:
        return RedirectResponse("/t", status_code=303)
    att = finalize_if_expired(conn, att) and conn.execute(
        "SELECT a.*, u.name, u.login FROM attempts a JOIN users u ON u.id=a.user_id WHERE a.id=?", (aid,)).fetchone()
    items = conn.execute(
        "SELECT aq.*, q.text FROM attempt_q aq JOIN questions q ON q.id=aq.question_id WHERE attempt_id=? ORDER BY pos",
        (aid,)).fetchall()
    viol = conn.execute("SELECT * FROM violations WHERE attempt_id=? ORDER BY ts", (aid,)).fetchall()
    strikes = strike_count(conn, aid)
    return render(request, "t_attempt.html", user=u, att=att, items=items, viol=viol, mx=setting(conn, "max_per_q"),
                  strikes=strikes, deduct=calc_deduct(conn, att, strikes))


def calc_deduct(conn, att, strikes):
    """Jarima: har bir ogohlantirish uchun penalty_points; chetlatilsa jami 0 (alohida qayd etiladi)."""
    ms, pp = setting(conn, "max_strikes"), setting(conn, "penalty_points")
    return pp * min(strikes, ms - 1)


@app.post("/t/attempt/{aid}/grade")
async def teacher_grade(aid: int, request: Request, u=Depends(require("teacher")), conn=Depends(get_db)):
    form = await request.form()
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
    att = conn.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    deduct = calc_deduct(conn, att, strike_count(conn, aid))
    final = 0.0 if att["disq"] else max(0.0, total - deduct)
    conn.execute("UPDATE attempts SET total=?, deduct=?, comment=?, graded=1, graded_at=? WHERE id=?",
                 (final, deduct, str(form.get("comment", ""))[:2000], int(time.time()), aid))
    return RedirectResponse("/t", status_code=303)


@app.post("/t/attempt/{aid}/reset")
def teacher_reset(aid: int, u=Depends(require("teacher")), conn=Depends(get_db)):
    """Qayta topshirishga ruxsat: urinishni o'chiradi, savollar 'used' hisobi kamaytiriladi."""
    for r in conn.execute("SELECT question_id FROM attempt_q WHERE attempt_id=?", (aid,)).fetchall():
        conn.execute("UPDATE questions SET used=MAX(0,used-1) WHERE id=?", (r["question_id"],))
    conn.execute("DELETE FROM violations WHERE attempt_id=?", (aid,))
    conn.execute("DELETE FROM attempt_q WHERE attempt_id=?", (aid,))
    conn.execute("DELETE FROM attempts WHERE id=?", (aid,))
    return RedirectResponse("/t", status_code=303)


@app.get("/t/export.csv")
def teacher_export(u=Depends(require("teacher")), conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT u.name, u.login, a.total, a.comment, a.graded, "
        "(SELECT COUNT(*) FROM violations v WHERE v.attempt_id=a.id AND v.penalty=1) viol "
        "FROM users u JOIN attempts a ON a.user_id=u.id ORDER BY u.name").fetchall()
    mx = setting(conn, "max_per_q") * setting(conn, "q_count")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Ism", "Login", "Ball", f"Maks({mx})", "Baho", "Izoh", "Jarima hodisalari"])
    for r in rows:
        w.writerow([r["name"], r["login"], r["total"] if r["graded"] else "", mx,
                    grade_of(r["total"], mx) if r["graded"] else "", r["comment"] or "", r["viol"]])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=natijalar.csv"})


init_db()
