"""Sinovlar uchun vaqtinchalik baza: 100 savol, 2 guruhda 20 ta to'qima o'quvchi (real ma'lumot emas)."""
import os
import random
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEACHER_PW = "ustoz123"  # faqat sinov bazasi uchun


def setup(n_students=20):
    os.environ["NAZORAT_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
    os.environ["TEACHER_PASSWORD"] = TEACHER_PW
    sys.path.insert(0, ROOT)
    import app as A
    rng = random.Random(1)
    conn = A.connect()
    conn.execute("BEGIN")
    for i in range(1, 101):
        conn.execute("INSERT INTO questions(id,text) VALUES(?,?)", (i, f"{i}-savol: mavzuni tushuntiring va misol keltiring."))
    rows = []
    for i in range(1, n_students + 1):
        grp, login, pw = f"G{(i - 1) // 10 + 1}", f"talaba{i:02d}", f"parol{i:02d}"
        name = f"TALABA{i:02d} SINOV O'G'LI"
        A.add_user(conn, "student", name, login, pw)
        uid = conn.execute("SELECT id FROM users WHERE login=?", (login,)).fetchone()["id"]
        conn.execute("UPDATE users SET grp=? WHERE id=?", (grp, uid))
        for pos, q in enumerate(rng.sample(range(1, 101), 5), 1):
            conn.execute("INSERT INTO assigned(user_id,question_id,pos) VALUES(?,?,?)", (uid, q, pos))
        rows.append((grp, name, login, pw))
    conn.execute("COMMIT")
    return A, rows
