"""Imtihonni to'xtatish, qabul qilinmaganlar ro'yxati, qayta berish va tugmalar oldidan tasdiqlash.
Ishga tushirish: python tests/test_stop_exam.py"""
import html, io, os, re, sys, time
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


def att_of(login):
    return db().execute("select a.* from attempts a join users u on u.id=a.user_id where u.login=?", (login,)).fetchone()


def txt(r):
    return html.unescape(r.text)


T = cl(); T.post("/login", data={"login": "ustoz", "password": TPW})
S = []
for r in rows[:4]:
    c = cl(); c.post("/login", data={"login": r[2], "password": r[3]}); S.append(c)
L = [r[2] for r in rows[:4]]
N = [r[1] for r in rows[:4]]

# ---- sukut: vaqt cheklovi yo'q, boshlagandan 60 daqiqa ----
check("sukut: imtihon ochiq, cheklovsiz", A.exam_window(db())["state"] == "open")
S[0].post("/s/start"); S[1].post("/s/start")
a0 = att_of(L[0])
check("boshlagandan 60 daqiqa sanaladi", a0 and a0["deadline"] - a0["started_at"] == 3600, a0 and a0["deadline"] - a0["started_at"])
aq = db().execute("select id from attempt_q where attempt_id=?", (a0["id"],)).fetchall()
S[0].post("/api/save", json={"answers": {str(aq[0]["id"]): "yarim javob"}})
a1 = att_of(L[1])
aq1 = db().execute("select id from attempt_q where attempt_id=?", (a1["id"],)).fetchall()
S[1].post("/api/save", json={"answers": {str(q["id"]): "tayyor javob" for q in aq1}, "final": True})
check("ikkinchi talaba o'z vaqtida yubordi", att_of(L[1])["submitted_at"] is not None)

# ---- to'xtatish ----
h = txt(T.get("/t"))
check("dashboard: to'xtatish tugmasi va tasdiqlash matni", 'action="/t/stop"' in h and "Hozir yozayotgan 1 ta talabaning ishi QABUL QILINMAYDI" in h)
r = T.post("/t/stop", headers={"referer": "http://testserver/t"})
check("to'xtatildi: 1 ta ish qabul qilinmadi", "1 ta yozilayotgan ish qabul qilinmadi" in txt(r), txt(r)[:0])
a0 = att_of(L[0])
check("yozayotgan ish: qabul qilinmagan", a0["rejected"] == 1 and a0["submitted_at"] is not None)
check("yuborilgan ish: qabul qilingan", att_of(L[1])["rejected"] == 0)
check("javoblar o'chmagan (ustoz ko'radi)", db().execute("select answer from attempt_q where id=?", (aq[0]["id"],)).fetchone()[0] == "yarim javob")
r = S[0].post("/api/save", json={"answers": {str(aq[0]["id"]): "kech javob"}, "final": True}).json()
check("to'xtatilgandan keyin saqlash/yuborish qabul qilinmaydi", r.get("submitted") and
      db().execute("select answer from attempt_q where id=?", (aq[0]["id"],)).fetchone()[0] == "yarim javob" and att_of(L[0])["rejected"] == 1)
check("talaba: «ishingiz qabul qilinmadi»", "Ishingiz qabul qilinmadi" in txt(S[0].get("/s")))
h = txt(S[2].get("/s"))
check("kirmagan talaba: to'xtatilgan, Boshlash yo'q", "ustoz tomonidan to'xtatilgan" in h and 'action="/s/start"' not in h)
S[2].post("/s/start")
check("to'xtatilganda boshlab bo'lmaydi", att_of(L[2]) is None)

# ---- ustoz ko'rinishi ----
h = txt(T.get("/t/results"))
rej = h.split('id="rejected"')[1].split("</table>")[0]
check("Natijalar: qabul qilinmaganlar ro'yxati", N[0] in rej and N[1] not in rej)
check("holat filtri: Qabul qilinmagan", 'value="Qabul qilinmagan"' in h)
h = txt(T.get("/t/review"))
check("tekshirish navbatida qabul qilinmagan yo'q", N[1] in h and N[0] not in h.split("So'nggi baholanganlar")[0])
check("nav: tekshirilmagan soni faqat qabul qilinganlar", A.nav_todo() == 1, A.nav_todo())
h = txt(T.get("/t"))
check("dashboard: to'xtatilgan holat va qayta ochish", "Imtihon: to'xtatilgan" in h and 'action="/t/resume"' in h and "Qabul qilinmagan ishlar: 1 ta" in h)
h = txt(T.get(f"/t/attempt/{a0['id']}"))
check("ish sahifasi: qabul qilinmagan, baholash formasi yo'q", "Qabul qilinmagan" in h and "/grade" not in h and "yarim javob" in h)
r = T.post(f"/t/attempt/{a0['id']}/grade", data={f"score_{q['id']}": "20" for q in aq})
check("qabul qilinmagan ishni baholab bo'lmaydi", "baholab bo'lmaydi" in txt(r) and not att_of(L[0])["graded"])
from openpyxl import load_workbook
wb = load_workbook(io.BytesIO(T.get("/t/export.xlsx").content))
st = {row[3]: row[4] for row in wb["Natijalar"].iter_rows(min_row=5, values_only=True)}
check("Excel: holat «Qabul qilinmagan»", st.get(L[0]) == "Qabul qilinmagan" and st.get(L[1]) == "Tekshirilmagan", (st.get(L[0]), st.get(L[1])))

# ---- qayta berish ----
r = T.post("/t/retake-rejected", data={"carry": "0", "grp": ""})
check("qabul qilinmaganlarga qayta berildi", "1 ta talabaga nazorat ishi qayta berildi" in txt(r))
check("ro'yxat bo'shadi, tarixda saqlandi", att_of(L[0]) is None and
      db().execute("select count(*) from history h join users u on u.id=h.user_id where u.login=?", (L[0],)).fetchone()[0] == 1)
h = txt(S[0].get("/s"))
check("talaba: qayta berildi, Boshlash bor (imtihon to'xtatilgan bo'lsa ham)", "qayta berdi" in h and 'action="/s/start"' in h)
S[0].post("/s/start")
a0 = att_of(L[0])
check("qayta boshladi: yana 60 daqiqa, chegirmasiz", a0 and a0["deadline"] - a0["started_at"] == 3600 and not a0["carry"] and a0["try_no"] == 2)
check("ruxsat bir martalik", db().execute("select reopen from users where login=?", (L[0],)).fetchone()[0] == 0)
check("boshqa kirmagan talaba hali ham boshlay olmaydi", 'action="/s/start"' not in S[2].get("/s").text)

# ---- qayta ochish ----
r = T.post("/t/resume")
check("imtihon qayta ochildi", "qayta ochildi" in txt(r) and A.exam_window(db())["state"] == "open")
S[2].post("/s/start")
check("qayta ochilgach kirmagan talaba boshlaydi", att_of(L[2]) is not None)
# yozayotgan ishni ustoz qayta ochishi (qabul qilinmagan -> davom)
T.post("/t/stop")
a2 = att_of(L[2])
check("ikkinchi to'xtatish: yozayotganlar qabul qilinmadi", a2["rejected"] == 1 and att_of(L[0])["rejected"] == 1)
T.post(f"/t/attempt/{a2['id']}/extend", data={"minutes": "15"})
a2 = att_of(L[2])
check("ishni qayta ochish: qabul qilinmaganlikdan chiqadi", a2["rejected"] == 0 and a2["submitted_at"] is None)
T.post("/t/resume")

# ---- tasdiqlash oynasi: ustoz sahifalaridagi har bir amal formasi ----
sid = db().execute("select id from users where login=?", (L[3],)).fetchone()[0]
pages = ["/t", "/t/results", "/t/review", "/t/settings", "/t/students", f"/t/students/{sid}", "/t/questions",
         f"/t/attempt/{att_of(L[1])['id']}", f"/t/attempt/{a2['id']}"]
missing = []
for p in pages:
    h = T.get(p).text
    for f in re.findall(r"<form\b[^>]*>", h):
        if 'method="post"' in f and "data-c-title" not in f:
            missing.append((p, f[:80]))
    if "onsubmit=\"return confirm" in h or "return confirm(" in h:
        missing.append((p, "eski confirm()"))
check("har bir POST forma tasdiqlash matniga ega", not missing, missing[:3])
h = T.get("/t").text
check("tasdiqlash oynasi: Davom et va Orqaga tugmalari", "Davom et" in h and "Orqaga" in h and "cfm-ok" in h and "cfm-bad" in h)
check("chiqish havolasi ham tasdiqlanadi", re.search(r'action="/logout"[^>]*data-c-title', h))

print("\nNATIJA:", "HAMMASI TO'G'RI" if ok_all else "XATOLAR BOR")
sys.exit(0 if ok_all else 1)
