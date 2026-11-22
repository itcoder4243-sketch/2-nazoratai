"""Xavfsizlik: sarlavhalar, so'rov hajmi, login cheklovi (login+IP), sessiya muddati, POST chiqish, parol shifrlash.
Ishga tushirish: python tests/test_security.py"""
import html, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _seed
A, rows = _seed.setup()
TPW = _seed.TEACHER_PW
from fastapi.testclient import TestClient
ok_all = True


def check(n, c, x=""):
    global ok_all; ok_all &= bool(c); print(("OK   " if c else "XATO ") + n + (f"  [{x}]" if x else ""))


def cl(ip="1.1.1.1", host="testserver"):
    return TestClient(A.app, client=(ip, 50000), base_url=f"http://{host}")


def db():
    return A.connect()


def bad_login(c, login, n=1):
    for _ in range(n):
        r = c.post("/login", data={"login": login, "password": "xato-parol"})
    return html.unescape(r.text)


# ---- sarlavhalar ----
h = cl().get("/").headers
need = {"x-frame-options": "DENY", "x-content-type-options": "nosniff", "referrer-policy": "same-origin"}
check("xavfsizlik sarlavhalari", all(h.get(k) == v for k, v in need.items()) and "frame-ancestors 'none'" in h.get("content-security-policy", ""), dict(h))
check("sahifalar keshlanmaydi, static keshlanadi", h.get("cache-control") == "no-store" and
      "max-age" in cl().get("/static/style.css").headers.get("cache-control", ""))

# ---- so'rov hajmi ----
S = cl("2.2.2.2"); S.post("/login", data={"login": rows[0][2], "password": rows[0][3]}); S.post("/s/start")
r = S.post("/api/save", json={"answers": {str(i): "A" * 6000 for i in range(60)}})  # ~360 KB
check("katta /api/save rad etiladi (413)", r.status_code == 413, r.status_code)
r = S.post("/api/save", json={"answers": {"1": "B" * 6000}})
check("oddiy /api/save ishlaydi", r.status_code == 200 and r.json().get("ok"))
r = cl().post("/login", data={"login": "x", "password": "y" * 20000})
check("katta login so'rovi rad etiladi", r.status_code == 413, r.status_code)


def chunks():
    for _ in range(40):
        yield b"x" * 10000


r = S.post("/api/save", content=chunks(), headers={"content-type": "application/json"})
check("Content-Length'siz katta so'rov ham to'xtatiladi", r.status_code in (413, 400), r.status_code)

# ---- login cheklovi ----
victim = rows[1]
att = cl("6.6.6.6")
msg = bad_login(att, victim[2], 9)
check("hujumchi IP 8 xatodan keyin bloklanadi", "Juda ko'p" in msg)
r = att.post("/login", data={"login": victim[2], "password": victim[3]})
check("bloklangan IP to'g'ri parol bilan ham kira olmaydi (taxmin to'xtaydi)", "Juda ko'p" in html.unescape(r.text))
r = cl("7.7.7.7").post("/login", data={"login": victim[2], "password": victim[3]})
check("haqiqiy egasi boshqa IP dan bemalol kiradi", str(r.url).endswith("/s"))
r = cl("1.2.3.4").post("/login", data={"login": "ustoz", "password": TPW})
bad_login(cl("6.6.6.6"), "ustoz", 20)
r = cl("1.2.3.4").post("/login", data={"login": "ustoz", "password": TPW})
check("ustozni begona IP dan bloklab bo'lmaydi", str(r.url).endswith("/t"))
spr = cl("8.8.8.8")
for i in range(A.FAIL_PER_IP):
    bad_login(spr, f"yoq_login_{i}")
check("bitta IP dan ko'p loginga taxmin (spraying) bloklanadi", "Juda ko'p" in bad_login(spr, rows[5][2]))
r = cl("9.9.9.9", host="nazorat.kodx.uz").post("/login", data={"login": "x", "password": "y"},
                                                   headers={"cf-connecting-ip": "5.5.5.5", "x-forwarded-for": "5.5.5.5"})
check("soxta IP sarlavhasi hisobga olinmaydi", db().execute("select count(*) from login_fails where k like '5.5.5.5|%'").fetchone()[0] == 0)
T = cl("1.2.3.4"); T.post("/login", data={"login": "ustoz", "password": TPW})
uid = db().execute("select id from users where login=?", (victim[2],)).fetchone()[0]
T.post(f"/t/students/{uid}/password", data={"password": "yangiParol77"})
r = att.post("/login", data={"login": victim[2], "password": "yangiParol77"})
check("ustoz parolni yangilasa blok olib tashlanadi", str(r.url).endswith("/s"))

# ---- vaqt bo'yicha login aniqlash ----
def med(login):
    v = []
    for _ in range(7):
        t = time.perf_counter(); cl("3.3.3.3").post("/login", data={"login": login, "password": "zz"}); v.append(time.perf_counter() - t)
    return sorted(v)[3] * 1000
a, b = med("mavjud_emas_login"), med(rows[7][2])
check("mavjud/mavjud emas login javob vaqti yaqin", abs(a - b) < max(a, b) * 0.5 + 3, f"{a:.1f} / {b:.1f} ms")

# ---- sessiya ----
S2 = cl("4.4.4.4"); S2.post("/login", data={"login": rows[8][2], "password": rows[8][3]})
db().execute("update sessions set created=created-? where user_id=(select id from users where login=?)", (A.SESSION_TTL + 5, rows[8][2]))
check("muddati o'tgan sessiya ishlamaydi", S2.get("/s", follow_redirects=False).status_code == 303)
S3 = cl("4.4.4.5"); S3.post("/login", data={"login": rows[9][2], "password": rows[9][3]})
check("GET /logout chiqarib yubormaydi", S3.get("/logout").url.path in ("/s", "/") and S3.get("/s", follow_redirects=False).status_code == 200)
S3.post("/logout")
check("POST /logout chiqaradi", S3.get("/s", follow_redirects=False).status_code == 303)

# ---- parol shifrlash ----
raw = db().execute("select pw_plain from users where login=?", (rows[10][2],)).fetchone()[0]
check("bazada parol ochiq emas", raw.startswith("enc1:") and rows[10][3] not in raw)
check("ustoz sahifasida parol ko'rinadi", rows[10][3] in T.get("/t/students").text)
check("CSV da parol ko'rinadi", rows[10][3] in T.get("/t/students.csv").text)
tam = raw[:-2] + ("A" if raw[-2] != "A" else "B") + raw[-1]
check("o'zgartirilgan shifr qabul qilinmaydi", A.reveal_pw(tam) is None)
db().execute("update users set pw_plain=? where login=?", ("ochiqParol1", rows[11][2]))
A.init_db()
check("eski ochiq parollar ishga tushishda shifrlanadi", A.reveal_pw(db().execute("select pw_plain from users where login=?", (rows[11][2],)).fetchone()[0]) == "ochiqParol1"
      and db().execute("select count(*) from users where pw_plain not like 'enc1:%'").fetchone()[0] == 0)

# ---- texnik domen ----
os.environ["CANONICAL_HOST"] = "nazorat.kodx.uz"
r = cl(host="abc.up.railway.app").get("/t/results?x=1", follow_redirects=False)
check("*.up.railway.app asosiy domenga yo'naltiriladi", r.status_code == 308 and r.headers["location"] == "https://nazorat.kodx.uz/t/results?x=1", r.headers.get("location"))
check("asosiy domen odatdagidek ishlaydi", cl(host="nazorat.kodx.uz").get("/").status_code == 200)

print("\nNATIJA:", "HAMMASI TO'G'RI" if ok_all else "XATOLAR BOR")
sys.exit(0 if ok_all else 1)
