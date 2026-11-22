"""Demo ma'lumot: 150 ta savol, 6 ta talaba, ustoz paroli ustoz123 (faqat demo uchun!)."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TEACHER_PASSWORD", "ustoz123")
import app

C = ["Algoritm", "O'zgaruvchi", "Funksiya", "For sikli", "Shart operatori (if)", "Ro'yxat (list)", "Lug'at (dict)",
     "Massiv", "Rekursiya", "Obyekt", "Sinf (class)", "Fayl bilan ishlash", "Ma'lumotlar bazasi", "SQL", "Internet",
     "IP-manzil", "Brauzer", "HTTP protokoli", "HTML", "CSS", "JavaScript", "Git", "Operatsion tizim", "Protsessor",
     "Operativ xotira (RAM)", "Kesh xotira", "Shifrlash", "Kuchli parol", "Fishing (phishing)", "Antivirus",
     "Bulutli texnologiyalar", "Sun'iy intellekt", "Mashinali o'qitish", "API", "Server", "Klient", "Domen nomi",
     "Hosting", "Kompilyator", "Interpretator", "Debugging (xatolarni topish)", "Bit va bayt", "Ikkilik sanoq tizimi",
     "Mantiqiy operatorlar", "Qidiruv algoritmlari", "Saralash algoritmlari", "Stek", "Navbat", "Daraxt", "Graf"]
P = ["{} nima? Qisqacha tushuntiring.",
     "{} ning vazifasi va qo'llanilish sohalarini yozing.",
     "{} ga o'zingizdan misol keltiring va izohlang."]

conn = app.connect()
if not conn.execute("SELECT 1 FROM questions").fetchone():
    for c in C:
        for p in P:
            conn.execute("INSERT INTO questions(text) VALUES(?)", (p.format(c),))
for name, login, pw in [("Ali Valiyev", "ali", "ali123"), ("Dilnoza Karimova", "dilnoza", "dil123"),
                        ("Jasur Toshmatov", "jasur", "jas123"), ("Madina Ergasheva", "madina", "mad123"),
                        ("Sardor Qodirov", "sardor", "sar123"), ("Nigora Yusupova", "nigora", "nig123")]:
    try:
        app.add_user(conn, "student", name, login, pw)
    except Exception:
        pass
print("Demo tayyor: ustoz/ustoz123, talabalar: ali/ali123 ...")
