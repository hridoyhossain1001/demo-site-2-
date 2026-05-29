# Buykori AdSync — Deployment Guide (DigitalOcean Droplet)

এই গাইডে **DigitalOcean Droplet**-এ Buykori AdSync সার্ভার deploy করার সম্পূর্ণ পদ্ধতি দেওয়া আছে।

---

## প্রয়োজনীয় জিনিস

- DigitalOcean Droplet (Ubuntu 22.04/24.04 LTS, ন্যূনতম 2GB RAM)
- একটি Domain (যেমন: `api.buykori.app`) — Droplet IP-তে A record point করা
- Git repository access

---

## প্রজেক্ট ফাইল কাঠামো

```
├── app/                     # FastAPI application
├── migrations/              # Alembic DB migrations
├── deploy/
│   ├── nginx.conf           # Nginx configuration template
│   ├── supervisor.conf      # Supervisor process manager config
│   ├── deploy.sh            # Auto-deployment script
│   └── setup.sh             # First-time server setup script
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development-only dependencies (pytest etc.)
└── .env                     # লোকাল টেস্টের জন্য (সার্ভারে .env আলাদা)
```

---

## ধাপ ১ — Droplet-এ প্রথমবার Setup

SSH দিয়ে Droplet-এ ঢুকুন এবং setup script চালান:

```bash
ssh root@YOUR_DROPLET_IP

# প্রজেক্ট clone করুন
git clone https://github.com/YOUR_USERNAME/buykori-adsync.git /var/www/buykori-adsync
cd /var/www/buykori-adsync

# Setup script চালান (Python, PostgreSQL, Redis, Nginx, Supervisor install/configure করবে)
chmod +x deploy/setup.sh
sudo bash deploy/setup.sh
```

---

## ধাপ ২ — Environment Variables সেট করুন

```bash
sudo nano /var/www/buykori-adsync/.env
```

নিচের variables সেট করুন:

```env
DATABASE_URL=postgresql+asyncpg://buykori:YOUR_DB_PASSWORD@localhost:5432/buykori_adsync
REDIS_URL=redis://localhost:6379/0

ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-strong-password-here
ADMIN_API_KEY=your-long-random-admin-api-key-64-chars

ENCRYPTION_KEY=your-generated-fernet-key-here

ALLOWED_HOSTS=localhost,127.0.0.1,YOUR_DOMAIN,www.YOUR_DOMAIN,api.YOUR_DOMAIN

ENABLE_DOCS=false
ENABLE_DEBUG=false
ENABLE_CREATE_ALL=false

EVENT_INGEST_MODE=redis_stream
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
```

ENCRYPTION_KEY তৈরি করতে:
```bash
cd /var/www/buykori-adsync
source venv/bin/activate
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## ধাপ ৩ — Database Setup

```bash
# Manual path only: setup.sh fresh server-এ DB user/database/tables তৈরি করে
# এবং Alembic head stamp করে। Existing deploy/update হলে শুধু migration চালান।

# PostgreSQL user ও database তৈরি
sudo -u postgres psql -c "CREATE USER buykori WITH PASSWORD 'YOUR_DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE buykori_adsync OWNER buykori;"

# Existing database/update path
cd /var/www/buykori-adsync
source venv/bin/activate
alembic upgrade head
```

---

## ধাপ ৪ — Nginx Configure করুন

```bash
# Domain replace করুন
sudo sed 's/DOMAIN_PLACEHOLDER/api.buykori.app/g' \
    /var/www/buykori-adsync/deploy/nginx.conf \
    > /etc/nginx/sites-available/buykori-adsync

sudo ln -sf /etc/nginx/sites-available/buykori-adsync /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# SSL Certificate (Let's Encrypt)
sudo certbot --nginx -d api.buykori.app -d client.buykori.app -d admin.buykori.app
```

---

## ধাপ ৫ — Supervisor দিয়ে Process চালু করুন

```bash
# Supervisor config কপি করুন
sudo cp /var/www/buykori-adsync/deploy/supervisor.conf /etc/supervisor/conf.d/buykori-adsync.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start buykori-web buykori-worker
```

---

## ধাপ ৬ — Deploy যাচাই করুন

```bash
# Status চেক
sudo supervisorctl status

# Health check
curl https://api.buykori.app/status

# Logs দেখুন
sudo tail -f /var/log/supervisor/buykori-web.out.log
sudo tail -f /var/log/supervisor/buykori-worker.out.log
```

**সফল হলে:**
- **Health Check:** `https://api.buykori.app/status`
- **Admin Panel:** `https://api.buykori.app/api/v1/admin`
- **API Docs (dev only):** `https://api.buykori.app/docs` (ENABLE_DOCS=true হলে)
- **Client Portal:** `https://client.buykori.app`

---

## ধাপ ৭ — Code Update করুন

নতুন কোড push করার পর Droplet-এ:

```bash
cd /var/www/buykori-adsync
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --quiet

# Migration থাকলে
alembic upgrade head

# Restart করুন
sudo supervisorctl restart buykori-web buykori-worker
```

অথবা deploy script ব্যবহার করুন:

```bash
bash deploy/deploy.sh
```

Local workspace theke fast hotfix deploy korte changed-file helper chalan:

```powershell
python deploy\changed_deploy.py --base origin/main
```

If SSH key auth is not configured yet, set `DO_SSH_PASSWORD` for that command only.
Preview the planned upload/delete list first:

```powershell
python deploy\changed_deploy.py --base origin/main --dry-run
```

Use `--skip-migrations` or `--skip-restart` only when you are sure that step is not needed.

### Pending deploy hardening

The Droplet already has a non-root `buykori` user that owns `/var/www/buykori-adsync`.
Next security step is to move local deploys from `root` to `buykori`:

- add this PC's public SSH key to `/home/buykori/.ssh/authorized_keys`
- grant `buykori` limited passwordless sudo only for `supervisorctl status` and restarting `buykori-web` / `buykori-worker:*`
- set `DO_SSH_USER=buykori` for `deploy/changed_deploy.py`

Do this only after explicit approval because it is a persistent production access-control change.

---

## দৈনন্দিন ব্যবস্থাপনা

```bash
# Restart
sudo supervisorctl restart buykori-web
sudo supervisorctl restart buykori-worker

# Logs
sudo tail -f /var/log/supervisor/buykori-web.out.log

# Status
sudo supervisorctl status

# Database check
psql -U buykori -d buykori_adsync -c "SELECT COUNT(*) FROM clients;"

# Migration
source /var/www/buykori-adsync/venv/bin/activate && alembic upgrade head
```

---

## প্রথম Admin Account তৈরি করুন

1. `https://api.YOUR_DOMAIN/api/v1/admin` এ যান
2. Admin username ও password দিয়ে login করুন (`.env`-এ দেওয়া)
3. নতুন Client তৈরি করুন — API Key পাবেন
4. WordPress plugin-এ API Key বসান

---

## Troubleshooting

**502 Bad Gateway?**
```bash
sudo supervisorctl status buykori-web
sudo tail -20 /var/log/supervisor/buykori-web.err.log
```

**Database connection error?**
```bash
# .env-এ DATABASE_URL সঠিক আছে কিনা চেক করুন
cd /var/www/buykori-adsync && source venv/bin/activate
python -c "import asyncio; from app.database import engine; from sqlalchemy import text; asyncio.run(engine.connect())"
```

**Permission denied?**
```bash
sudo chown -R buykori:buykori /var/www/buykori-adsync
```
