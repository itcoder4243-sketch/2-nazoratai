"""Kirish sahifasi, imtihon ekrani, jarayon paneli va tez baholash oqimi.
Ishga tushirish: python tests/test_ui_flow.py"""
import html, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _seed
A, rows = _seed.setup()
TPW = _seed.TEACHER_PW
from fastapi.testclient import TestClient
ok_all = True
def check(n, c, x=""):
    global ok_all; ok_all &= bool(c); print(("OK   " if c else "XATO ") + n + (f"  [{x}]" if x else ""))
cl = lambda: TestClient(A.app, follow_redirects=True); db = lambda: A.connect()
P = cl(); r = P.get("/")
check("kirish: standart nom, 60 daqiqa, 5 savol, qoida", "Nazorat ishi" in r.text and "60 daqiqa" in r.text and "5 ta savol" in r.text and "−10 ball" in r.text)
r = P.post("/login", data={"login": "x", "password": "y"}); check("xato parolda ham kirish sahifasi to'liq", "Login yoki parol" in html.unescape(r.text) and "60 daqiqa" in r.text)
T = cl(); T.post("/login", data={"login": "ustoz", "password": TPW})
T.post("/t/settings", data={"duration_min": 60, "q_count": 5, "max_per_q": 20, "penalty_points": 10, "max_strikes": 3, "exam_title": "Pedagogika — nazorat ishi"})
check("nazorat nomi saqlandi va ko'rinadi", "Pedagogika — nazorat ishi" in cl().get("/").text and "Pedagogika" in T.get("/t/settings").text)
check("sozlamalar o'zgarmadi", A.setting(db(), "duration_min") == 60 and A.setting(db(), "q_count") == 5)
# 3 talaba: biri yozadi, biri yuboradi, biri chetlatiladi
S = []
for k in range(3):
    c = cl(); c.post("/login", data={"login": rows[k][2], "password": rows[k][3]}); r = c.post("/s/start"); S.append((c, r))
r = S[0][1]
check("imtihon: navigatsiya 5 tugma, taymer, hisoblagich", len(re.findall(r'class="qn"', r.text)) == 5 and 'id="spill"' in r.text and "0 / 3" in r.text and 'id="tbar"' in r.text)
check("imtihon: himoya kodi joyida", all(k in r.text for k in ["visibilitychange", "requestFullscreen", "insertFromPaste", "printscreen", "/api/violation"]))
check("imtihon: guruh ko'rinadi", rows[0][0] + "-guruh" in r.text)
ids = re.findall(r'data-id="(\d+)"', S[1][1].text)
S[1][0].post("/api/save", json={"answers": {i: "javob" for i in ids}, "final": True})
for _ in range(3):
    S[2][0].post("/api/violation", json={"kind": "tab_hidden"}); con = db(); con.execute("update violations set ts=ts-10"); con.close()
h = T.get("/t").text
check("jarayon chizig'i va legenda", 'class="prog"' in h and 'data-state-pick="Yozmoqda"' in h)
check("hodisalar: boshladi, yubordi, chetlatildi", "boshladi" in h and "ishni yubordi" in h and "chetlatildi (3/3)" in h)
a1 = db().execute("select a.id from attempts a join users u on u.id=a.user_id where u.login=?", (rows[1][2],)).fetchone()[0]
a2 = db().execute("select a.id from attempts a join users u on u.id=a.user_id where u.login=?", (rows[2][2],)).fetchone()[0]
r = T.get(f"/t/attempt/{a1}")
check("tez baholash tugmalari 0/5/10/15/20", all(f'data-v="{v}"' in r.text for v in (0, 5, 10, 15, 20)) and "Saqlash va keyingisi" in r.text and "Tekshirilmagan: 2" in r.text)
qs = db().execute("select id from attempt_q where attempt_id=?", (a1,)).fetchall()
r = T.post(f"/t/attempt/{a1}/grade", data={**{f"score_{q['id']}": "15" for q in qs}, "comment": "", "next": "1"})
check("saqlash va keyingisi: keyingi tekshirilmagan ish ochildi", str(r.url).split("?")[0].endswith(f"/t/attempt/{a2}"), r.url)
check("ball saqlandi 75", db().execute("select total from attempts where id=?", (a1,)).fetchone()[0] == 75)
qs = db().execute("select id from attempt_q where attempt_id=?", (a2,)).fetchall()
r = T.post(f"/t/attempt/{a2}/grade", data={**{f"score_{q['id']}": "20" for q in qs}, "next": "1"})
check("oxirgisidan keyin natijalarga qaytadi", str(r.url).split("?")[0].endswith("/t/review") and "qolmadi" in html.unescape(r.text))
check("chetlatilgan baholansa 0", db().execute("select total from attempts where id=?", (a2,)).fetchone()[0] == 0)
check("oddiy Saqlash natijalarga qaytaradi", str(T.post(f"/t/attempt/{a1}/grade", data={f"score_{q['id']}": "10" for q in db().execute("select id from attempt_q where attempt_id=?", (a1,)).fetchall()}).url).split("?")[0].endswith("/t/review"))
print("\nNATIJA:", "HAMMASI TO'G'RI" if ok_all else "XATOLAR BOR")
