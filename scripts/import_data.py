"""Haqiqiy nazorat ishi ma'lumotlarini kiritadi: o'quvchilar (xlsx), savollar (docx).
usage: python scripts/import_data.py <nazorat_dir> <inputs_dir> <teacher_password> <out_dir>
Muhit o'zgaruvchisi PASSWORD_SALT majburiy (.env.example ga qarang): o'quvchi parollari login + shu tuzdan hosil qilinadi.
Eski (demo) o'quvchilar, savollar, urinishlar o'chiriladi (DB oldin nusxalanadi)."""
import collections, hashlib, itertools, json, random, shutil, sqlite3, sys, time, unicodedata, zipfile
import xml.etree.ElementTree as ET

APP_DIR, IN_DIR, TEACHER_PW, OUT = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
import os
SALT = os.environ.get("PASSWORD_SALT")
if not SALT:
    sys.exit("PASSWORD_SALT muhit o'zgaruvchisi o'rnatilmagan (.env.example ga qarang)")
sys.path.insert(0, APP_DIR)
import app as A  # noqa

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_students(p):
    z = zipfile.ZipFile(p)
    ss = ["".join(t.text or "" for t in si.iter(M + "t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si")]
    out, grp, seen = [], None, set()
    dup_skipped = 0
    for r in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(M + "row"):
        cells = {}
        for c in r.findall(M + "c"):
            col = "".join(ch for ch in c.get("r") if ch.isalpha())
            v = c.find(M + "v")
            if v is not None:
                cells[col] = ss[int(v.text)] if c.get("t") == "s" else v.text
        if "A" not in cells and "B" in cells:
            grp = cells["B"].strip()
        elif "B" in cells:
            name = " ".join(cells["B"].split())
            key = (grp, name)
            if name in seen:      # 306-guruh ro'yxati faylda ikki marta bor
                dup_skipped += 1
                continue
            seen.add(name)
            out.append((grp, name))
    return out, dup_skipped


def read_questions(p):
    root = ET.fromstring(zipfile.ZipFile(p).read("word/document.xml"))
    qs = []
    for para in root.iter(W + "p"):
        txt = "".join(el.text or "" if el.tag == W + "t" else ("\n" if el.tag == W + "br" else "") for el in para.iter() if el.tag in (W + "t", W + "br"))
        if txt.strip():
            qs.append(txt.strip())
    return qs


def ascii_(s):
    for ch in "‘’ʻʼ'`":
        s = s.replace(ch, "")
    s = s.replace("Ǵ", "G").replace("ǵ", "g")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


students, dup_skipped = read_students(IN_DIR + "/306.xlsx")
questions = read_questions(IN_DIR + "/savollar.docx")
print("o'quvchilar:", len(students), "(takror qator o'tkazib yuborildi:", dup_skipped, ") savollar:", len(questions))
dups = [q for q, k in collections.Counter(questions).items() if k > 1]
print("takroriy savol matnlari:", len(dups))

# --- login/parol ---
used, rows = set(), []
for grp, name in students:
    parts = name.split()
    sur, first = ascii_(parts[0]), ascii_(parts[1]) if len(parts) > 1 else "talaba"
    login = f"{sur}.{first}"
    if login in used and len(parts) > 2:
        login = f"{sur}.{first}.{ascii_(parts[2])[:1]}"
    k = 2
    base = login
    while login in used:
        login = f"{base}{k}"; k += 1
    used.add(login)
    code = int(hashlib.sha256((login + "|" + SALT).encode()).hexdigest(), 16) % 900 + 100
    rows.append((grp, name, login, f"{first}{code}"))

# --- savollarni biriktirish: guruh ichida iloji boricha takrorlanmasin, umumiy yuk teng ---
rng = random.Random(20261112)
N = len(questions)
glob = [0] * N
assign = {}
by_grp = collections.defaultdict(list)
for r in rows:
    by_grp[r[0]].append(r)
for g, lst in by_grp.items():
    rng.shuffle(lst)
    gu = [0] * N
    for r in lst:
        order = sorted(range(N), key=lambda q: (gu[q], glob[q], rng.random()))
        pick = order[:5]
        for q in pick:
            gu[q] += 1; glob[q] += 1
        rng.shuffle(pick)
        assign[r[2]] = pick

# --- DB ---
dbp = A.DB_PATH
shutil.copy(dbp, f"{dbp}.demo_backup_{int(time.time())}")
conn = A.connect()
conn.execute("BEGIN")
for t in ("violations", "attempt_q", "attempts", "assigned", "sessions", "questions"):
    conn.execute(f"DELETE FROM {t}")
conn.execute("DELETE FROM users WHERE role='student'")
conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('questions','attempts','attempt_q','violations','users')") if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone() else None
for i, q in enumerate(questions, 1):
    conn.execute("INSERT INTO questions(id,text) VALUES(?,?)", (i, q))
uid = {}
for grp, name, login, pw in rows:
    A.add_user(conn, "student", name, login, pw)
    u = conn.execute("SELECT id FROM users WHERE login=?", (login,)).fetchone()["id"]
    conn.execute("UPDATE users SET grp=? WHERE id=?", (grp, u))
    uid[login] = u
    for pos, q in enumerate(assign[login], 1):
        conn.execute("INSERT INTO assigned(user_id,question_id,pos) VALUES(?,?,?)", (u, q + 1, pos))
        conn.execute("UPDATE questions SET used=used+1 WHERE id=?", (q + 1,))
s = A.secrets.token_hex(16)
conn.execute("UPDATE users SET salt=?, pw_hash=? WHERE login='ustoz'", (s, A.hash_pw(TEACHER_PW, s)))
conn.execute("UPDATE settings SET value='60' WHERE key='duration_min'")
conn.execute("UPDATE settings SET value='5' WHERE key='q_count'")
conn.execute("COMMIT")

# --- statistika ---
mx_pair = 0
for g, lst in by_grp.items():
    for a, b in itertools.combinations([r[2] for r in lst], 2):
        mx_pair = max(mx_pair, len(set(assign[a]) & set(assign[b])))
print("guruhlar:", {g: len(l) for g, l in by_grp.items()})
print("bir savolni olgan o'quvchilar soni: min", min(glob), "maks", max(glob))
print("bir guruh ichida ikki o'quvchida umumiy savollar maks:", mx_pair)
import os
os.makedirs(OUT, exist_ok=True)
json.dump({"rows": rows, "assign": {k: [x + 1 for x in v] for k, v in assign.items()}}, open(OUT + "/state.json", "w", encoding="utf-8"), ensure_ascii=False)
print("ok")
