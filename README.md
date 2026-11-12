# NazoratAI: onlayn nazorat ishi tizimi

Ustoz savollar bazasini yuklaydi, har bir o'quvchiga bazadan oldindan **5 ta savol** biriktiriladi, o'quvchi **60 daqiqa** ichida **ochiq javob** yozadi, ustoz esa har savolga ball va izoh qo'yadi. O'quvchi o'z bahosi va izohini ko'radi.

> **Muallif:** Nurbek Soliyev, AI va Full Stack dasturchi · [soliyevnurbek.uz](https://soliyevnurbek.uz) · [GitHub](https://github.com/SoliyevNurbek)

## Imkoniyatlar

- **Savollar:** bazadan tasodifiy, har o'quvchiga alohida to'plam; guruh ichida takrorlanish minimal.
- **Vaqt:** 60 daqiqalik taymer serverda hisoblanadi (brauzerda o'zgartirib bo'lmaydi). Vaqt tugasa ish avtomatik yuboriladi, javoblar har 10 soniyada saqlanadi.
- **Nazorat:** nusxa olish/qo'yish, o'ng tugma, belgilash va PrintScreen bloklanadi; ekranda o'quvchi ismi bilan suv belgisi; boshqa oynaga o'tish aniqlanadi.
  Oynadan chiqish: 1- va 2-marta jarima (sukut -10 ball), 3-martada nazoratdan chetlashtiriladi.
- **Ustoz paneli** (alohida, qorong'i dizayn): natijalar, savollar bazasi, o'quvchilar ro'yxati, har savolga ball va izoh, Excel (CSV) eksport, qayta topshirishga ruxsat.
- **O'quvchi oynasi:** oddiy o'quv uslubida; natijada ball, baho (2–5) va ustoz izohi.
- **Yengil:** FastAPI + SQLite + oddiy HTML/JS. 200 o'quvchi bir vaqtda sinovdan o'tgan (kirish ~0,3 s, saqlash ~0,09 s) kuchsiz kompyuterda ham (2 yadroli Pentium, HDD).

## Texnologiyalar

Python 3.8–3.12 · FastAPI · SQLite (WAL) · Jinja2 · Uvicorn

## O'rnatish va ishga tushirish

```bash
git clone https://github.com/SoliyevNurbek/nazoratai.git
cd nazoratai
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # Linux/macOS: cp .env.example .env  va qiymatlarni to'ldiring
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Brauzerda `http://localhost:8000` oching. Ustoz: login `ustoz`, paroli `.env` dagi `TEACHER_PASSWORD`
(bo'sh qoldirsangiz, birinchi ishga tushganda terminalda tasodifiy parol chiqadi).

**Windows 7/8.1:** `WINDOWS81_OQING.txt`, `setup_win81.bat`, `run_win81.bat` (eski kutubxonalar to'plami `requirements-win81.txt`).

## Haqiqiy ma'lumotlarni kiritish

1. Ustoz paneli → **Savollar**: har qatorga bitta savol; **Talabalar**: `Ism; login; parol` yoki faqat ism (login/parol avtomatik).
2. Katta ro'yxat (Excel/Word) uchun: `tools/import_data.py`. `.env` dagi `PASSWORD_SALT` ni to'ldiring.

## Xavfsizlik va maxfiylik

- Parollar, tokenlar va baza (o'quvchilar, javoblar) repoga **kiritilmaydi**: `.env`, `data/`, `*.db`, `*.xlsx`, `*.docx`, `*.csv` `.gitignore` da.
- Internetga chiqarishda (tunnel/hosting) ustoz parolini kuchli qiling va HTTPS ishlating.
- `seed_demo.py` va `smoke_test.py` dagi `ustoz123`, `ali123` kabi qiymatlar **faqat sinov uchun** to'qima ma'lumot.
- Brauzerda ekran suratini to'liq to'sib bo'lmaydi (masalan, boshqa telefon kamerasi bilan): shuning uchun suv belgisi va hodisalar jurnali ishlatiladi.

## Testlar

```bash
python smoke_test.py            # asosiy oqim, takrorlanmaslik, jarima va chetlatish
python tools/loadtest.py http://127.0.0.1:8000 <state.json> 200 90 <ustoz_paroli>   # yuklama sinovi
```

## Litsenziya

Litsenziya hali tanlanmagan. Foydalanish yoki tarqatishdan oldin muallif bilan kelishing.

© 2026 Nurbek Soliyev
