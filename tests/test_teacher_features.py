"""Ustoz funksiyalari: talabalar, savollar, chetlatish, qayta topshirish, vaqt qo'shish, CSV.
Ishga tushirish: python tests/test_teacher_features.py"""
import html, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _seed
A, rows = _seed.setup()
TPW = _seed.TEACHER_PW
from fastapi.testclient import TestClient
ok_all = True
def check(n, c, x=""):
    global ok_all; ok_all &= bool(c); print(("OK   " if c else "XATO ") + n + (f"  [{x}]" if x else ""))
def cl(): return TestClient(A.app, follow_redirects=True)
db = lambda: A.connect()
T = cl(); r = T.post("/login", data={"login": "ustoz", "password": TPW})
T.post("/t/publish", data={"on": "1"})  # natijalar o'quvchilarga ko'rinsin
check("ustoz kiradi", "Natijalar" in r.text)
for p in ["/t", "/t/students", "/t/questions", "/t/export.csv", "/t/students.csv", "/t/students.csv?grp=G1"]:
    r = T.get(p); check(f"GET {p} 200", r.status_code == 200, r.status_code)
check("natijalar: filtr va avto yangilash bor", 'data-filter="#res"' in T.get("/t/results").text and 'id="auto"' in T.get("/t/results").text)
c306 = T.get("/t/students.csv?grp=G1").text
check("guruh CSV faqat G1", all(l.startswith("G1;") for l in c306.strip().splitlines()[1:]) and len(c306.strip().splitlines()) > 1)

# --- talabalar ---
r = T.post("/t/students/one", data={"name": "TESTOV ALI VALI O‘G‘LI", "grp": "999", "login": "", "password": ""})
u = db().execute("select * from users where name='TESTOV ALI VALI O‘G‘LI'").fetchone()
check("bitta talaba qo'shildi, login familiya.ism, guruh", u and u["login"] == "testov.ali" and u["grp"] == "999", u and u["login"])
r = T.post("/t/students/one", data={"name": "BOSHQA", "login": "testov.ali"})
check("band login rad etiladi", "Login band" in r.text)
r = T.post("/t/students/add", data={"text": "AKA UKA\nOPA SINGIL; opa.s; parol1; 888\n\tbo'sh\nTESTOV ALI VALI", "grp": "777"})
a1 = db().execute("select * from users where name='AKA UKA'").fetchone(); a2 = db().execute("select * from users where login='opa.s'").fetchone()
check("ommaviy: guruh default 777, qatordagi 888, parol berilgan", a1["grp"] == "777" and a2["grp"] == "888" and a2["pw_plain"] == "parol1")
check("ommaviy: takror ism ikkinchi login oladi", db().execute("select count(*) from users where login like 'testov.ali%'").fetchone()[0] == 2)
uid = u["id"]
check("talaba sahifasi ochiladi", T.get(f"/t/students/{uid}").status_code == 200)
T.post(f"/t/students/{uid}/edit", data={"name": "TESTOV ALI", "grp": "998", "login": "testov.a"})
u = db().execute("select * from users where id=?", (uid,)).fetchone()
check("tahrirlash: ism/guruh/login", u["name"] == "TESTOV ALI" and u["grp"] == "998" and u["login"] == "testov.a")
r = T.post(f"/t/students/{uid}/edit", data={"name": "X", "grp": "", "login": "opa.s"}); check("tahrirda band login rad", "Login band" in r.text)
S = cl(); S.post("/login", data={"login": "testov.a", "password": u["pw_plain"]}); check("yangi talaba kiradi", "Boshlash" in S.get("/s").text)
r = T.post(f"/t/students/{uid}/password", data={"password": ""}); newpw = re.search(r"Yangi parol: ([^<&\s]+)", html.unescape(r.text)).group(1)
check("parol tiklandi va eski sessiya chiqarildi", "Boshlash" not in S.get("/s").text)
S = cl(); S.post("/login", data={"login": "testov.a", "password": newpw}); check("yangi parol bilan kiradi", "Boshlash" in S.get("/s").text)

# yangi talaba (biriktirilmagan) boshlaydi: 5 savol
r = S.post("/s/start"); ids = re.findall(r'data-id="(\d+)"', r.text); check("biriktirilmagan talabaga 5 savol", len(ids) == 5)

# --- savol soni o'zgarsa ---
T.post("/t/settings", data={"duration_min": 60, "q_count": 6, "max_per_q": 20, "penalty_points": 10, "max_strikes": 3})
g, n, lg, pw = rows[0]; S2 = cl(); S2.post("/login", data={"login": lg, "password": pw})
r = S2.post("/s/start"); ids2 = re.findall(r'data-id="(\d+)"', r.text)
check("q_count=6 bo'lsa ham boshlaydi (5 biriktirilgan + 1)", len(ids2) == 6, len(ids2))
T.post("/t/settings", data={"duration_min": 60, "q_count": 4, "max_per_q": 20, "penalty_points": 10, "max_strikes": 3})
g, n, lg3, pw3 = rows[1]; S3 = cl(); S3.post("/login", data={"login": lg3, "password": pw3})
check("q_count=4 bo'lsa 4 savol", len(re.findall(r'data-id="(\d+)"', S3.post("/s/start").text)) == 4)
T.post("/t/settings", data={"duration_min": 60, "q_count": 5, "max_per_q": 20, "penalty_points": 10, "max_strikes": 3})

# --- savollar ---
nq = db().execute("select count(*) from questions").fetchone()[0]
r = T.post("/t/questions/import", data={"text": "YANGI SAVOL BIR\nYANGI SAVOL BIR\n" + db().execute("select text from questions where id=1").fetchone()[0]})
check("takror savollar qo'shilmaydi", db().execute("select count(*) from questions").fetchone()[0] == nq + 1 and "2 ta takror" in html.unescape(r.text))
T.post("/t/questions/edit/1", data={"text": "TAHRIRLANGAN 1"})
check("savol tahrirlandi", db().execute("select text from questions where id=1").fetchone()[0] == "TAHRIRLANGAN 1")
# biriktirilgan, lekin hech kim ochmagan savol
q = db().execute("select question_id, count(*) c from assigned where question_id not in (select question_id from attempt_q) group by question_id limit 1").fetchone()
qid = q["question_id"]; who = [r[0] for r in db().execute("select user_id from assigned where question_id=?", (qid,))]
T.post(f"/t/questions/delete/{qid}")
check("biriktirilgan savol o'chdi", not db().execute("select 1 from questions where id=?", (qid,)).fetchone())
check("o'sha talabalarda yana 5 ta har xil savol", all(db().execute("select count(distinct question_id) from assigned where user_id=?", (w,)).fetchone()[0] == 5 for w in who), len(who))
given = db().execute("select question_id from attempt_q limit 1").fetchone()[0]
r = T.post(f"/t/questions/delete/{given}"); check("javob yozilgan savol o'chmaydi", db().execute("select 1 from questions where id=?", (given,)).fetchone() and "o'chirib bo'lmaydi" in html.unescape(r.text))

# --- chetlatish, qayta ochish, qayta topshirish ---
g, n, lg4, pw4 = rows[5]; S4 = cl(); S4.post("/login", data={"login": lg4, "password": pw4})
r = S4.post("/s/start"); ids4 = re.findall(r'data-id="(\d+)"', r.text)
S4.post("/api/save", json={"answers": {i: "javob" for i in ids4}})
for _ in range(3):
    j = S4.post("/api/violation", json={"kind": "tab_hidden"}).json()
    con = db(); con.execute("update violations set ts=ts-10"); con.close()
check("3 marta chiqib chetlatildi", j["disq"])
aid = db().execute("select a.id from attempts a join users u on u.id=a.user_id where u.login=?", (lg4,)).fetchone()[0]
check("urinish sahifasi (chetlatilgan) ochiladi", T.get(f"/t/attempt/{aid}").status_code == 200)
T.post(f"/t/attempt/{aid}/extend", data={"minutes": 5})
a = db().execute("select * from attempts where id=?", (aid,)).fetchone()
check("qayta ochildi: disq=0, yozmoqda, 2 ogohlantirish", a["disq"] == 0 and a["submitted_at"] is None and A.strike_count(db(), aid) == 2)
check("talaba ishga qaytadi, javoblar saqlangan", "javob" in S4.get("/s/exam").text)
con = db(); con.execute("update violations set ts=ts-10"); con.close()
check("keyingi chiqishda yana chetlatiladi", S4.post("/api/violation", json={"kind": "window_blur"}).json()["disq"])
old_q = {r[0] for r in db().execute("select question_id from attempt_q where attempt_id=?", (aid,))}
r = T.get(f"/t/attempt/{aid}"); sug = re.search(r'name="carry" value="([\d.]+)"', r.text).group(1)
check("taklif chegirma = 10 x 3 = 30", sug == "30", sug)
T.post(f"/t/attempt/{aid}/retake", data={"carry": "30", "new_q": "1"})
uu = db().execute("select * from users where login=?", (lg4,)).fetchone()
check("qayta topshirish: carry=30, retakes=1, tarixda 1 yozuv", uu["carry"] == 30 and uu["retakes"] == 1 and db().execute("select count(*) from history where user_id=?", (uu["id"],)).fetchone()[0] == 1)
new_asg = {r[0] for r in db().execute("select question_id from assigned where user_id=?", (uu["id"],))}
check("yangi 5 savol, eskilaridan hech biri yo'q", len(new_asg) == 5 and not (new_asg & old_q))
check("talabaga chegirma ogohlantirishi ko'rinadi", "30 ball" in S4.get("/s").text and "Boshlash" in S4.get("/s").text)
r = S4.post("/s/start"); ids5 = re.findall(r'data-id="(\d+)"', r.text)
a2 = db().execute("select * from attempts where user_id=?", (uu["id"],)).fetchone()
check("2-urinish: try_no=2, carry=30", a2["try_no"] == 2 and a2["carry"] == 30)
S4.post("/api/save", json={"answers": {i: "x" for i in ids5}, "final": True})
qs = db().execute("select id from attempt_q where attempt_id=?", (a2["id"],)).fetchall()
T.post(f"/t/attempt/{a2['id']}/grade", data={**{f"score_{q['id']}": "18" for q in qs}, "comment": "ok"})
check("yakuniy = 90 - 30 = 60", db().execute("select total from attempts where id=?", (a2["id"],)).fetchone()[0] == 60)
h = S4.get("/s").text; check("talaba 60 va chegirmani ko'radi", "60.0 / 100" in h and "-30 ball" in h)
r = T.get(f"/t/attempt/{a2['id']}"); check("ustoz tarixni ko'radi", "Oldingi urinishlar" in r.text and "1-urinish" in r.text)
r = T.post(f"/t/attempt/{a2['id']}/extend", data={"minutes": 5}); check("baholangan ishni qayta ochib bo'lmaydi", "Baholangan ishni" in html.unescape(r.text))

# --- vaqt qo'shish + avtomatik yuborish himoyasi ---
g, n, lg6, pw6 = rows[7]; S6 = cl(); S6.post("/login", data={"login": lg6, "password": pw6}); S6.post("/s/start")
aid6 = db().execute("select a.id from attempts a join users u on u.id=a.user_id where u.login=?", (lg6,)).fetchone()[0]
d0 = db().execute("select deadline from attempts where id=?", (aid6,)).fetchone()[0]
T.post(f"/t/attempt/{aid6}/extend", data={"minutes": 10})
check("vaqt +10 daqiqa", db().execute("select deadline from attempts where id=?", (aid6,)).fetchone()[0] == d0 + 600)
j = S6.post("/api/save", json={"answers": {}, "final": True, "auto": True}).json()
check("taymer avtomatik yuborsa-yu vaqt bor: yuborilmaydi, remaining qaytadi", not j["submitted"] and j["remaining"] > 3000, j)
j = S6.post("/api/save", json={"answers": {}, "final": True}).json(); check("qo'lda yuborish ishlaydi", j["submitted"])

# --- ommaviy qayta topshirish ---
for k in (10, 11):
    g, n, lgk, pwk = rows[k]; Sk = cl(); Sk.post("/login", data={"login": lgk, "password": pwk}); Sk.post("/s/start")
    for _ in range(3):
        Sk.post("/api/violation", json={"kind": "tab_hidden"}); con = db(); con.execute("update violations set ts=ts-10"); con.close()
nd = db().execute("select count(*) from attempts where disq=1").fetchone()[0]
check("natijalarda chetlatilganlar bloki", "Hammasiga qayta topshirishga ruxsat" in T.get("/t/results").text)
r = T.post("/t/retake-disq", data={"carry": "", "new_q": "1", "grp": ""})
check("hammasiga ruxsat: chetlatilganlar qolmadi", db().execute("select count(*) from attempts where disq=1").fetchone()[0] == 0 and f"{nd} ta" in html.unescape(r.text), nd)
check("default chegirma 30", all(db().execute("select carry from users where login=?", (rows[k][2],)).fetchone()[0] == 30 for k in (10, 11)))

csvt = T.get("/t/export.csv").text
check("CSV: yangi ustunlar va 60 ball", "Qayta topshirish chegirmasi" in csvt and ";60;" in csvt)
check("eski reset tugmasi ishlaydi (chegirmasiz)", T.post(f"/t/attempt/{aid6}/reset").status_code == 200 and db().execute("select carry from users where login=?", (lg6,)).fetchone()[0] == 0)
T.post(f"/t/students/delete/{uid}"); check("talaba o'chirildi (tarixi bilan)", not db().execute("select 1 from users where id=?", (uid,)).fetchone())
for p in ["/t", "/t/students", "/t/questions"]:
    check(f"yakuniy GET {p}", T.get(p).status_code == 200)
print("\nNATIJA:", "HAMMASI TO'G'RI" if ok_all else "XATOLAR BOR")
