"""Excel hisobot, natijalarni e'lon qilish, imtihon vaqti, zaxira, ustoz paroli va telefon menyusi.
Ishga tushirish: python tests/test_admin_tools.py"""
import html, io, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _seed
A, rows = _seed.setup()
TPW = _seed.TEACHER_PW
from fastapi.testclient import TestClient
ok_all = True


def check(n, c, x=""):
    global ok_all; ok_all &= bool(c); print(("OK   " if c else "XATO ") + n + (f"  [{x}]" if x else ""))


def cl():
    return TestClient(A.app)


def db():
    return A.connect()


def local(ts):  # unix -> datetime-local (Toshkent)
    return A.fmt_ts(ts, "%Y-%m-%dT%H:%M")


T = cl(); T.post("/login", data={"login": "ustoz", "password": TPW})
S1 = cl(); S1.post("/login", data={"login": rows[0][2], "password": rows[0][3]})
S2 = cl(); S2.post("/login", data={"login": rows[1][2], "password": rows[1][3]})

# ---- imtihon vaqti ----
now = int(time.time())
r = T.post("/t/window", data={"open_at": local(now + 3600), "close_at": local(now + 7200)})
check("vaqt saqlandi", "Imtihon vaqti saqlandi" in html.unescape(r.text))
h = S1.get("/s").text
check("ochilishdan oldin: kutish va qolgan vaqt", 'class="wait"' in h and 'action="/s/start"' not in h)
S1.post("/s/start")
check("ochilishdan oldin boshlab bo'lmaydi", not db().execute("select 1 from attempts a join users u on u.id=a.user_id where u.login=?", (rows[0][2],)).fetchone())
r = T.post("/t/window", data={"open_at": local(now + 7200), "close_at": local(now + 3600)})
check("yopilish ochilishdan oldin bo'lsa rad etiladi", "keyin bo'lishi kerak" in html.unescape(r.text))
T.post("/t/window", data={"open_at": local(now - 600), "close_at": local(now + 1200)})
h = S1.get("/s").text
check("ochiq: Boshlash tugmasi va yopilish vaqti", 'action="/s/start"' in h and "da yopiladi" in h)
S1.post("/s/start")
a = db().execute("select a.* from attempts a join users u on u.id=a.user_id where u.login=?", (rows[0][2],)).fetchone()
check("ish vaqti yopilish vaqtidan oshmaydi", a and a["deadline"] <= A.parse_local(local(now + 1200)) + 60, a and a["deadline"] - now)
T.post("/t/window", data={"open_at": "", "close_at": local(now - 60)})
h = S2.get("/s").text
check("yopilgan: yangi ish boshlab bo'lmaydi", "yopilgan" in h and 'action="/s/start"' not in h)
r = T.post("/t/window", data={"clear": "1"})
check("cheklovni olib tashlash", "olib tashlandi" in html.unescape(r.text) and 'action="/s/start"' in S2.get("/s").text)

# ---- natijalarni e'lon qilish ----
db().execute("update attempts set submitted_at=?, deadline=? where id=?", (now, now, a["id"]))
aqs = db().execute("select id from attempt_q where attempt_id=?", (a["id"],)).fetchall()
T.post(f"/t/attempt/{a['id']}/grade", data={**{f"score_{q['id']}": "16" for q in aqs}, "comment": "Yaxshi ish"})
check("dashboard: natijalar yashirin eslatmasi", "Natijalar: yashirin" in html.unescape(T.get("/t").text))
h = html.unescape(S1.get("/s").text)
check("e'londan oldin o'quvchi ballni ko'rmaydi", "ustoz e'lon qilgandan keyin" in h and "Yaxshi ish" not in h)
r = T.post("/t/publish", data={"on": "1"}, headers={"referer": "http://testserver/t/settings"})
check("e'lon qilish va shu sahifaga qaytish", str(r.url).split("?")[0].endswith("/t/settings") and "e'lon qilindi" in html.unescape(r.text))
h = html.unescape(S1.get("/s").text)
check("e'londan keyin ball va izoh ko'rinadi", "Yaxshi ish" in h and "80" in h)
T.post("/t/publish", data={"on": "0"})
check("yashirish yana ishlaydi", "Yaxshi ish" not in html.unescape(S1.get("/s").text))

# ---- Excel ----
r = T.get("/t/export.xlsx")
check("Excel yuklanadi", r.status_code == 200 and "spreadsheetml" in r.headers.get("content-type", ""))
from openpyxl import load_workbook
wb = load_workbook(io.BytesIO(r.content))
check("Excel varaqlari", wb.sheetnames == ["Natijalar", "Guruhlar", "Taqsimot"], wb.sheetnames)
ws = wb["Natijalar"]
hdr = [c.value for c in ws[4]]
row = next(r for r in ws.iter_rows(min_row=5, values_only=True) if r[3] == rows[0][2])
check("Excel: savol ballari, yakuniy ball va baho", row[6] == 16 and row[hdr.index("Yakuniy ball (100)")] == 80 and row[hdr.index("Baho")] == 4, row)
check("Excel: barcha talabalar", ws.max_row - 4 == len(rows))
check("Excel: diagramma bor", len(wb["Taqsimot"]._charts) == 1)
check("Excel: student kira olmaydi", S1.get("/t/export.xlsx", follow_redirects=False).status_code == 303)

# ---- zaxira ----
r = T.post("/t/backup")
check("qo'lda zaxira", "Zaxira olindi" in html.unescape(r.text))
name = A.list_backups()[0]["name"]
r = T.get(f"/t/backup/{name}")
check("zaxirani yuklab olish (SQLite fayl)", r.status_code == 200 and r.content[:15] == b"SQLite format 3")
bad = [T.get(u) for u in ("/t/backup/..%2F..%2Fapp.py", "/t/backup/app.py", "/t/backup/nazorat.db")]
check("zaxira: noto'g'ri nom rad etiladi", all(b"SQLite" not in x.content[:16] and b"import" not in x.content[:200] for x in bad))
check("zaxira: talabaga yopiq", S1.get(f"/t/backup/{name}", follow_redirects=False).status_code == 303)
A.make_backup("kunlik")
check("kunlik zaxira ro'yxatda", any(b["kind"] == "Kunlik" for b in A.list_backups()))

# ---- ustoz paroli ----
T2 = cl(); T2.post("/login", data={"login": "ustoz", "password": TPW})  # login bitta qurilmani qoldiradi: T chiqib ketadi
r = T2.post("/t/password", data={"current": "xato", "new": "yangiParol1", "confirm": "yangiParol1"})
check("noto'g'ri joriy parol rad etiladi", "Joriy parol noto'g'ri" in html.unescape(r.text))
r = T2.post("/t/password", data={"current": TPW, "new": "qisqa1", "confirm": "qisqa1"})
check("qisqa parol rad etiladi", "kamida 8" in html.unescape(r.text))
r = T2.post("/t/password", data={"current": TPW, "new": "yangiParol1", "confirm": "yangiParol2"})
check("takror mos emas rad etiladi", "mos emas" in html.unescape(r.text))
r = T2.post("/t/password", data={"current": TPW, "new": "yangiParol1", "confirm": "yangiParol1"})
check("parol o'zgardi, sessiya saqlandi", "Parol o'zgartirildi" in html.unescape(r.text) and T2.get("/t").url.path == "/t")
check("eski parol bilan kirib bo'lmaydi", "noto'g'ri" in html.unescape(cl().post("/login", data={"login": "ustoz", "password": TPW}).text))
check("yangi parol bilan kiriladi", cl().post("/login", data={"login": "ustoz", "password": "yangiParol1"}).url.path == "/t")

# ---- telefon menyusi va vaqt ----
T3 = cl(); T3.post("/login", data={"login": "ustoz", "password": "yangiParol1"})
h = T3.get("/t/settings").text
check("telefon menyusi tugmasi va menyu", 'id="navbtn"' in h and 'id="side"' in h and 'id="scrim"' in h)
check("sozlamalar bo'limlari", all(f'id="{x}"' in h for x in ("exam", "window", "publish", "backup", "security")))
check("vaqt Toshkentda (UTC+5)", A.fmt_ts(3600, "%H:%M") == "06:00")

print("\nNATIJA:", "HAMMASI TO'G'RI" if ok_all else "XATOLAR BOR")
sys.exit(0 if ok_all else 1)
