import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]
os.environ["NAZORAT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["TEACHER_PASSWORD"] = "ustoz123"
import seed_demo  # noqa
from fastapi.testclient import TestClient
import app as A

def client():
    try:
        return TestClient(A.app, follow_redirects=True)
    except TypeError:
        return TestClient(A.app)  # eski Starlette: redirectlar sukut bo`yicha kuzatiladi
def login(c, u, p):
    r = c.post("/login", data={"login": u, "password": p}); assert r.status_code == 200; return r

seen = []
names = ["ali", "dilnoza", "jasur", "madina", "sardor", "nigora"]
pws = {"ali": "ali123", "dilnoza": "dil123", "jasur": "jas123", "madina": "mad123", "sardor": "sar123", "nigora": "nig123"}
for n in names:
    c = client(); login(c, n, pws[n])
    assert "Boshlash" in c.get("/s").text
    r = c.post("/s/start"); assert "Savol 1 / 5" in r.text and "wmc" in r.text
    conn = A.connect()
    att = conn.execute("SELECT a.id FROM attempts a JOIN users u ON u.id=a.user_id WHERE u.login=?", (n,)).fetchone()
    rows = conn.execute("SELECT id, question_id FROM attempt_q WHERE attempt_id=?", (att["id"],)).fetchall()
    seen += [r["question_id"] for r in rows]
    ans = {str(r["id"]): f"{n} javobi {r['id']}" for r in rows}
    assert c.post("/api/save", json={"answers": ans}).json()["ok"]
    assert c.post("/api/violation", json={"kind": "tab_hidden"}).json()["strikes"] == 1
    assert c.post("/api/save", json={"answers": ans, "final": True}).json()["submitted"]
    assert "yuborildi" in c.get("/s").text
    assert c.get("/t").url.path == "/s"  # talaba ustoz sahifasiga kira olmaydi
assert len(seen) == len(set(seen)), "takror savol!"
print("takrorlanmagan savollar:", len(seen))

t = client(); login(t, "ustoz", "ustoz123")
assert "Tekshirilmagan" in t.get("/t/results").text
aid = A.connect().execute("SELECT id FROM attempts LIMIT 1").fetchone()["id"]
page = t.get(f"/t/attempt/{aid}"); assert "tab_hidden" in page.text
conn = A.connect()
qs = conn.execute("SELECT id FROM attempt_q WHERE attempt_id=?", (aid,)).fetchall()
form = {"comment": "Yaxshi ish"}
for q in qs: form[f"score_{q['id']}"] = "17"; form[f"comment_{q['id']}"] = "Davom eting"
t.post(f"/t/attempt/{aid}/grade", data=form)
uid = conn.execute("SELECT user_id FROM attempts WHERE id=?", (aid,)).fetchone()["user_id"]
login_name = conn.execute("SELECT login FROM users WHERE id=?", (uid,)).fetchone()["login"]
s = client(); login(s, login_name, pws[login_name])
h = s.get("/s").text; assert "75.0 / 100" in h and "-10 ball" in h and "Davom eting" in h and "Yaxshi ish" in h
assert "Ali Valiyev" in t.get("/t/export.csv").text
print("HAMMASI OK; baho:", "5" in h)

# ---- jarima tizimi ----
A.STRIKE_GAP = 0
c = client(); login(c, "ali2" if False else "ustoz", "ustoz123")
conn = A.connect(); A.add_user(conn, "student", "Test Qoida", "qoida", "q123")
s = client(); login(s, "qoida", "q123"); s.post("/s/start")
r1 = s.post("/api/violation", json={"kind": "window_blur"}).json(); assert r1["strikes"] == 1 and not r1["disq"], r1
s.post("/api/violation", json={"kind": "paste"})  # jarimasiz, faqat jurnal
r2 = s.post("/api/violation", json={"kind": "printscreen"}).json(); assert r2["strikes"] == 2 and not r2["disq"], r2
r3 = s.post("/api/violation", json={"kind": "tab_hidden"}).json(); assert r3["disq"] and r3["submitted"], r3
assert "chetlashtirildingiz" in s.get("/s").text
aid = conn.execute("SELECT a.id FROM attempts a JOIN users u ON u.id=a.user_id WHERE u.login='qoida'").fetchone()["id"]
qs = conn.execute("SELECT id FROM attempt_q WHERE attempt_id=?", (aid,)).fetchall()
t = client(); login(t, "ustoz", "ustoz123")
assert "Chetlatilgan" in t.get("/t/results").text
t.post(f"/t/attempt/{aid}/grade", data={**{f"score_{q['id']}": "20" for q in qs}, "comment": "x"})
assert conn.execute("SELECT total FROM attempts WHERE id=?", (aid,)).fetchone()["total"] == 0
# 2 marta chiqqan, chetlatilmagan: 100 - 20 = 80
s2 = client(); A.add_user(conn, "student", "Ikki Marta", "ikki", "q123"); login(s2, "ikki", "q123"); s2.post("/s/start")
s2.post("/api/violation", json={"kind": "window_blur"}); s2.post("/api/violation", json={"kind": "tab_hidden"})
s2.post("/api/save", json={"answers": {}, "final": True})
aid2 = conn.execute("SELECT a.id FROM attempts a JOIN users u ON u.id=a.user_id WHERE u.login='ikki'").fetchone()["id"]
qs2 = conn.execute("SELECT id FROM attempt_q WHERE attempt_id=?", (aid2,)).fetchall()
t.post(f"/t/attempt/{aid2}/grade", data={**{f"score_{q['id']}": "20" for q in qs2}, "comment": "y"})
assert conn.execute("SELECT total FROM attempts WHERE id=?", (aid2,)).fetchone()["total"] == 80
assert "-20 ball" in s2.get("/s").text
print("JARIMA TIZIMI OK")


