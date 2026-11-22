# Serverga o'rnatish (Ubuntu 22.04/24.04, domen + HTTPS)

## 1. Kod va muhit
```bash
sudo apt update && sudo apt install -y python3-venv nginx certbot python3-certbot-nginx git
sudo git clone https://github.com/SoliyevNurbek/nazoratai.git /opt/nazoratai
cd /opt/nazoratai
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env      # COOKIE_SECURE=1 qiling (HTTPS uchun)
```

## 2. Real baza (savollar + o'quvchilar) — GitHub orqali EMAS
Baza (`data/nazorat.db`) maxfiy: o'quvchilar ismi, parollari va javoblari bor, shuning uchun repoda yo'q.
O'z kompyuteringizdan serverga xavfsiz nusxalang (server ishlamay turganda):
```bash
scp data/nazorat.db user@SERVER_IP:/tmp/nazorat.db
ssh user@SERVER_IP "sudo mkdir -p /opt/nazoratai/data && sudo mv /tmp/nazorat.db /opt/nazoratai/data/ && sudo chown -R www-data:www-data /opt/nazoratai/data && sudo chmod 700 /opt/nazoratai/data"
```
Bo'sh bazadan boshlash ham mumkin: ustoz paneliga kirib savollar va o'quvchilarni qo'shasiz.

## 3. Xizmat (systemd)
```bash
sudo cp deploy/nazoratai.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now nazoratai
sudo systemctl status nazoratai          # active (running) bo'lishi kerak
```

## 4. Nginx + domen + SSL
DNS: domeningizning `A` yozuvini server IP manziliga yo'naltiring.
```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/nazoratai
sudo sed -i 's/DOMEN.UZ/sizning-domen.uz/g' /etc/nginx/sites-available/nazoratai
sudo ln -s /etc/nginx/sites-available/nazoratai /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d sizning-domen.uz -d www.sizning-domen.uz
```

## 5. Yangilash va zaxira
```bash
cd /opt/nazoratai && sudo git pull && sudo systemctl restart nazoratai
sudo sqlite3 /opt/nazoratai/data/nazorat.db ".backup /root/nazorat_$(date +%F_%H%M).db"
```
Imtihon kuni ertalab va tugagandan keyin zaxira oling.

## Railway orqali (eng oson)
1. Railway'da GitHub reposini ulang. Ishga tushirish buyrug'i kerak emas: Railway `uvicorn main:app` ni o'zi ishlatadi.
2. **Volume** qo'shing, mount path: `/data`.
3. O'zgaruvchilar: `NAZORAT_DB=/data/nazorat.db`, `COOKIE_SECURE=1`, `WEB_CONCURRENCY=4`, `FORWARDED_ALLOW_IPS=*`, `TEACHER_PASSWORD=...`
4. Real bazani yuklash: `railway volume files upload data/nazorat.db /restore.db`, keyin `railway redeploy`.
   `main.py` ishga tushishda `restore.db` ni asosiy baza o'rniga qo'yadi va eskisini `nazorat_oldingi_*.db` nomi bilan saqlab qo'yadi.
5. Zaxira olish: `railway volume files download /nazorat.db ./zaxira.db` (agar `nazorat.db-wal` bo'lsa, uni ham yuklab oling).
6. Domen: `railway domain sizning-domen.uz`. Chiqqan CNAME va TXT yozuvlarini DNS panelingizga kiriting.
