# NazoratAI — onlayn nazorat ishi tizimi

Ustoz savollar bazasini yuritadi. Har bir o'quvchiga bazadan alohida **5 ta savol** biriktiriladi. O'quvchi **60 daqiqa** ichida ochiq javob yozadi, ustoz har savolga ball va izoh qo'yadi (jami 100 ball). Telefon va kompyuterda ishlaydi.

> **Muallif:** SoliyevNurbek (Nurbek Soliyev) · [GitHub](https://github.com/SoliyevNurbek) · © 2026, barcha huquqlar himoyalangan ([LICENSE](LICENSE))

## Imkoniyatlar

**O'quvchi**
- Taymer serverda hisoblanadi; vaqt tugasa ish avtomatik yuboriladi.
- Javoblar avtomatik saqlanadi (faqat o'zgargan javoblar yuboriladi).
- Natija: ball, baho va ustoz izohi.

**Ko'chirishga qarshi nazorat**
- Nusxa olish, qo'yish, o'ng tugma va belgilash bloklanadi.
- Ekranda ism bilan suv belgisi chiqadi.
- Oynadan chiqish: 1- va 2-marta −10 ball, 3-martada o'quvchi chetlashtiriladi.

**Ustoz paneli**
- Dashboard: asosiy ko'rsatkichlar, imtihon jarayoni, ballar gistogrammasi, baholar taqsimoti, TOP-10, eng past 10, guruhlar kesimi.
- Tekshirish: baholanmagan ishlar navbati va «Saqlash va keyingisi» oqimi.
- Excel hisobot (.xlsx): natijalar (har savol bali bilan), guruhlar kesimi, taqsimot diagrammasi.
- Natijalarni e'lon qilish: o'quvchilar ball va izohni faqat ustoz e'lon qilgandan keyin ko'radi.
- Imtihon vaqti: belgilangan vaqtda avtomatik ochiladi va yopiladi (Toshkent vaqti).
- Zaxira: har kuni avtomatik zaxira (14 kun saqlanadi), qo'lda zaxira olish va yuklab olish.
- Ustoz parolini panelning o'zidan o'zgartirish.
- Telefon: menyu pastki chap tugma orqali chap tomondan chiqadi.
- Natijalar: filtrlar, avtomatik yangilanish, qolgan vaqt, kengaytirilgan CSV.
- O'quvchilar: qo'shish, tahrirlash, ommaviy yuklash, parolni tiklash, guruhlar, CSV.
- Savollar: qo'shish, tahrirlash, qidirish, takrorlarni aniqlash, xavfsiz o'chirish.
- Qayta topshirish: chegirma ball bilan, yangi yoki o'sha savollar bilan; chetlashtirilganlarga ommaviy ruxsat.
- Vaqt qo'shish va ishni qayta ochish; har bir urinish tarixi saqlanadi.

**Tezlik**
- 4 ta uvicorn worker, SQLite WAL va ulanishlar puli.
- Fon checkpoint, indekslar, GZip va statik kesh.
- 500 ta o'quvchi bir vaqtda lokal sinovdan xatosiz o'tgan.

## Loyiha tuzilishi

```
app.py              # butun ilova (FastAPI)
templates/, static/ # sahifalar va uslublar
scripts/            # ishga tushirish, import, demo ma'lumot
tests/              # avtomatik sinovlar (vaqtinchalik bazada)
deploy/             # systemd, nginx va serverga o'rnatish qo'llanmasi
docs/               # qo'shimcha hujjatlar
data/               # baza (git'ga KIRMAYDI)
```

## Lokal ishga tushirish

```bash
git clone https://github.com/SoliyevNurbek/nazoratai.git
cd nazoratai
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # Linux/macOS: cp .env.example .env
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

Brauzerda `http://localhost:8000` ni oching. Ustoz logini `ustoz`, paroli esa `.env` dagi `TEACHER_PASSWORD`. Agar u bo'sh bo'lsa, tasodifiy parol terminalga chiqariladi.

Tayyor skriptlar: `scripts/run_windows.bat`, `scripts/run_linux.sh`.

## Serverga o'rnatish (domen + HTTPS)

To'liq qo'llanma: [deploy/README.md](deploy/README.md).

## Sinovlar

```bash
pip install -r requirements-dev.txt
python tests/smoke_test.py
python tests/test_teacher_features.py
python tests/test_ui_flow.py
python tests/test_admin_tools.py
```

Sinovlar vaqtinchalik bazada, to'qima ma'lumotlar bilan ishlaydi, real bazaga tegmaydi.

## Xavfsizlik

- Real baza, o'quvchilar ro'yxati, parollar, savollar fayllari va `.env` git'ga yuklanmaydi (`.gitignore`).
- Parollar bazada xeshlangan holda saqlanadi.
- HTTPS orqali ishlatganda `.env` da `COOKIE_SECURE=1` qiling.
