"""Halollik nazorati: ikkinchi buzilish kanali, signal uzilishi, yashirin holat, tez paydo bo'lgan matn, o'xshashlik.
Ishga tushirish: python tests/test_integrity.py"""
import html, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _seed
A, rows = _seed.setup()
TPW = _seed.TEACHER_PW
from fastapi.testclient import TestClient
ok_all = True


def check(n, c, x=""):
    global ok_all; ok_all &= bool(c); print(("OK   " if c else "XATO ") + n + (f"  [{x}]" if x else ""))


def db():
    return A.connect()


def student(i):
    c = TestClient(A.app); c.post("/login", data={"login": rows[i][2], "password": rows[i][3]}); c.post("/s/start")
    a = db().execute("select a.* from attempts a join users u on u.id=a.user_id where u.login=?", (rows[i][2],)).fetchone()
    qs = [r["id"] for r in db().execute("select id from attempt_q where attempt_id=? order by pos", (a["id"],))]
    return c, a["id"], qs


def kinds(aid):
    return [r["kind"] for r in db().execute("select kind from violations where attempt_id=? order by id", (aid,))]


T = TestClient(A.app); T.post("/login", data={"login": "ustoz", "password": TPW})

# ---- ikkinchi kanal: /api/violation to'sib qo'yilsa ham saqlash orqali yetadi ----
S, aid, qs = student(0)
r = S.post("/api/save", json={"answers": {}, "v": [{"id": "e1", "kind": "tab_hidden"}], "vis": "v", "a": 1}).json()
check("saqlash ichidagi buzilish qayd etiladi va jarima bo'ladi", kinds(aid) == ["tab_hidden"] and r["strikes"] == 1 and r["vdone"] == ["e1"], r)
S.post("/api/violation", json={"id": "e1", "kind": "tab_hidden"})
S.post("/api/save", json={"answers": {}, "v": [{"id": "e1", "kind": "tab_hidden"}]})
check("bir xil hodisa ikki kanaldan kelsa bir marta yoziladi", kinds(aid) == ["tab_hidden"])
S.post("/api/save", json={"answers": {}, "v": [{"id": "x", "kind": "fast_text"}]})
check("talaba server belgilarini o'zi qo'ya olmaydi", "fast_text" not in kinds(aid))

# ---- uchinchi jarima saqlash orqali kelsa ham chetlatiladi ----
db().execute("update violations set ts=ts-100 where attempt_id=?", (aid,))
S.post("/api/save", json={"answers": {}, "v": [{"id": "e2", "kind": "window_blur"}]})
db().execute("update violations set ts=ts-100 where attempt_id=?", (aid,))
r = S.post("/api/save", json={"answers": {str(qs[0]): "matn"}, "v": [{"id": "e3", "kind": "exit_fullscreen"}]}).json()
d = db().execute("select disq, submitted_at from attempts where id=?", (aid,)).fetchone()
check("3-jarima saqlash kanali orqali ham chetlatadi", r["submitted"] and d["disq"] == 1 and d["submitted_at"])

# ---- signal uzilishi va yashirin holat ----
S, aid, qs = student(1)
S.post("/api/save", json={"answers": {}})
db().execute("update attempts set last_seen=last_seen-120 where id=?", (aid,))
S.post("/api/save", json={"answers": {}})
f = db().execute("select kind, penalty, info from violations where attempt_id=?", (aid,)).fetchall()
check("120 s signal kelmasa belgi qo'yiladi (jarimasiz)", [(x["kind"], x["penalty"]) for x in f] == [("no_signal", 0)] and "soniya" in f[0]["info"], [dict(x) for x in f])
S.post("/api/save", json={"answers": {}, "vis": "h", "a": 1})
S.post("/api/save", json={"answers": {}, "vis": "h", "a": 1})
check("yashirin, lekin chiqish xabari yo'q: bitta belgi (daqiqasiga ko'pi bilan bir)", kinds(aid).count("hidden_unreported") == 1, kinds(aid))
S3, aid3, _ = student(2)
S3.post("/api/save", json={"answers": {}, "v": [{"id": "h1", "kind": "tab_hidden"}], "vis": "h", "a": 1})
check("chiqish xabari bilan kelgan yashirin holat belgilanmaydi", kinds(aid3) == ["tab_hidden"], kinds(aid3))
S4, aid4, _ = student(3)
S4.post("/api/save", json={"answers": {}, "vis": "h", "a": 0})
check("to'liq ekranga kirmagan (a=0) holatda belgi yo'q", kinds(aid4) == [])

# ---- yozish tarixi va tez matn ----
S, aid, qs = student(4)
db().execute("update attempts set started_at=started_at-600 where id=?", (aid,))
S.post("/api/save", json={"answers": {str(qs[0]): "Bu mening javobim. " * 3}})
check("normal yozish: belgi yo'q, tarixga yoziladi", kinds(aid) == [] and db().execute("select count(*) from snaps where attempt_id=?", (aid,)).fetchone()[0] == 1)
S.post("/api/save", json={"answers": {str(qs[0]): "Bu mening javobim. " * 3 + "x" * 900}})
f = db().execute("select kind, penalty, info from violations where attempt_id=?", (aid,)).fetchone()
check("bir zumda +900 belgi: «tez paydo bo'lgan matn» belgisi", f and f["kind"] == "fast_text" and f["penalty"] == 0 and "+900" in f["info"], f and dict(f))
S.post("/api/save", json={"answers": {str(qs[0]): "Bu mening javobim. " * 3 + "x" * 900}})
check("o'zgarishsiz saqlash tarixga yozilmaydi", db().execute("select count(*) from snaps where attempt_id=?", (aid,)).fetchone()[0] == 2)

# ---- o'xshashlik ----
S5, aid5, qs5 = student(5)
same = db().execute("select question_id from attempt_q where id=?", (qs[1],)).fetchone()[0]
db().execute("update attempt_q set question_id=? where id=?", (same, qs5[0]))
txt = "Operatsion tizim kompyuter resurslarini boshqaradi va dasturlar uchun muhit yaratadi, jarayonlar xotira fayllar hamda qurilmalarni nazorat qiladi"
S.post("/api/save", json={"answers": {str(qs[1]): txt}})
S5.post("/api/save", json={"answers": {str(qs5[0]): txt + " deb o'ylayman"}})
p = html.unescape(T.get(f"/t/attempt/{aid}").text)
check("ustoz sahifasida o'xshash javob ko'rsatiladi", "boshqa talabalarnikiga o'xshash" in p and rows[5][1] in p)
check("ustoz sahifasida yozish grafigi va belgilar", "<svg class=\"tl\"" in p and "Juda tez paydo bo'lgan matn" in p and "+900 belgi" in p)
p = html.unescape(T.get(f"/t/attempt/{aid4}").text)
check("belgisiz talabada «belgi yo'q»", "belgi yo'q" in p)
check("natijalar jadvalida belgi soni", "⚑" in T.get("/t/results").text)

# ---- imtihon sahifasining skripti sintaktik to'g'ri (bitta apostrof butun himoyani o'chirib qo'yishi mumkin) ----
import re, shutil, subprocess, tempfile
S6, _, _ = student(6)
js = "\n".join(re.findall(r"<script>(.*?)</script>", S6.get("/s/exam").text, re.S))
if shutil.which("node"):
    fn = os.path.join(tempfile.mkdtemp(), "exam.js"); open(fn, "w", encoding="utf-8").write(js)
    r = subprocess.run(["node", "--check", fn], capture_output=True, text=True)
    check("imtihon sahifasi skriptida sintaksis xatosi yo'q", r.returncode == 0, r.stderr[:300])
check("skriptda ikkinchi kanal va ekran qulfi bor", "wakeLock" in js and "vdone" in js)

# ---- qayta topshirish tarixni tozalaydi ----
T.post(f"/t/attempt/{aid}/retake", data={"carry": "0"})
check("qayta topshirishda yozish tarixi o'chadi", db().execute("select count(*) from snaps where attempt_id=?", (aid,)).fetchone()[0] == 0)

print("\nNATIJA:", "HAMMASI TO'G'RI" if ok_all else "XATOLAR BOR")
sys.exit(0 if ok_all else 1)
