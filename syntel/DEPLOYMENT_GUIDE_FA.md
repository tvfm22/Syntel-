# 🚀 راهنمای نصب و راه‌اندازی سایت Syntel
## IP سرور: 45.159.150.199

این فایل راهنمای کامل نصب و راه‌اندازی سیستم مدیریت مالی Syntel را توضیح می‌دهد.

---

## ✅ پیش‌نیازها

قبل از شروع، اطمینان حاصل کنید که:
- سرور Ubuntu/Debian دارید
- دسترسی root به سرور دارید
- پورت‌های 80 و 8000 باز هستند

---

## 📋 نصب سریع (یک دستوری)

به عنوان کاربر root این دستور را اجرا کنید:

```bash
cd /opt/project/Syntel-/syntel
sudo ./deploy_server.sh
```

این اسکریپت تمام کارهای زیر را به طور خودکار انجام می‌دهد:
1. ✅ نصب وابستگی‌ها (Python, Nginx, PostgreSQL, ...)
2. ✅ ساخت محیط مجازی Python
3. ✅ نصب پکیج‌های Python
4. ✅ تنظیم متغیرهای محیطی
5. ✅ اجرای migrations دیتابیس
6. ✅ جمع‌آوری فایل‌های استاتیک
7. ✅ ساخت superuser (admin/admin123)
8. ✅ پیکربندی Nginx
9. ✅ پیکربندی سرویس Systemd
10. ✅ راه‌اندازی سرویس‌ها

---

## 🔧 نصب دستی (مرحله به مرحله)

اگر می‌خواهید به صورت دستی نصب کنید:

### 1. نصب وابستگی‌های سیستم

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv nginx postgresql postgresql-contrib
```

### 2. ساخت محیط مجازی

```bash
cd /opt/project/Syntel-/syntel
python3 -m venv venv
source venv/bin/activate
```

### 3. نصب پکیج‌های Python

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. تنظیم فایل محیطی

فایل `.env.production` را ویرایش کنید:

```bash
nano .env.production
```

مقادیر مهم:
```
SECRET_KEY=your-secret-key-here
DEBUG=False
ALLOWED_HOSTS=45.159.150.199,localhost
DATABASE_URL=sqlite:///$(pwd)/db.sqlite3
```

### 5. ساخت دایرکتوری‌های لازم

```bash
mkdir -p logs media staticfiles
chmod 755 logs media staticfiles
```

### 6. اجرای Migrations و Collectstatic

```bash
export DJANGO_SETTINGS_MODULE=syntel.settings_production
python manage.py migrate
python manage.py collectstatic --noinput
```

### 7. ساخت Superuser

```bash
python manage.py createsuperuser
# یا اجرای این دستور برای ساخت خودکار:
python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.create_superuser('admin', 'admin@syntel.com', 'admin123') if not User.objects.filter(username='admin').exists() else None"
```

### 8. پیکربندی Nginx

```bash
sudo cp nginx_server.conf /etc/nginx/sites-available/syntel
sudo ln -sf /etc/nginx/sites-available/syntel /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

### 9. پیکربندی Systemd Service

```bash
sudo cp syntel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable syntel
sudo systemctl start syntel
```

### 10. راه‌اندازی Nginx

```bash
sudo systemctl restart nginx
```

---

## 🔍 بررسی وضعیت

برای بررسی وضعیت سرویس‌ها:

```bash
./check_deployment.sh
```

یا به صورت دستی:

```bash
# بررسی وضعیت سرویس Syntel
sudo systemctl status syntel

# بررسی وضعیت Nginx
sudo systemctl status nginx

# مشاهده لاگ‌ها
sudo journalctl -u syntel -f
tail -f /var/log/nginx/syntel-error.log
```

---

## 🌐 دسترسی به سایت

پس از نصب موفق:

- **سایت اصلی**: http://45.159.150.199
- **پنل مدیریت**: http://45.159.150.199/admin
- **اطلاعات ورود پیش‌فرض**: 
  - نام کاربری: `admin`
  - رمز عبور: `admin123`

⚠️ **مهم**: حتماً رمز عبور admin را تغییر دهید!

---

## 🔄 دستورات مدیریتی

### راه‌اندازی مجدد سرویس

```bash
sudo systemctl restart syntel
sudo systemctl restart nginx
```

### توقف سرویس

```bash
sudo systemctl stop syntel
sudo systemctl stop nginx
```

### مشاهده لاگ‌ها

```bash
# لاگ‌های Gunicorn
sudo journalctl -u syntel -f

# لاگ‌های Nginx
tail -f /var/log/nginx/syntel-access.log
tail -f /var/log/nginx/syntel-error.log

# لاگ‌های Django
tail -f /opt/project/Syntel-/syntel/logs/gunicorn-error.log
```

### به‌روزرسانی کد

```bash
cd /opt/project/Syntel-/syntel
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart syntel
```

---

## 🛡️ امنیت

### تغییر رمز عبور Admin

```bash
cd /opt/project/Syntel-/syntel
source venv/bin/activate
python manage.py changepassword admin
```

### تنظیم فایروال

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

---

## 🔧 عیب‌یابی

### سرویس شروع نمی‌شود

```bash
# بررسی لاگ‌های systemd
sudo journalctl -u syntel -xe

# بررسی تنظیمات gunicorn
sudo systemctl cat syntel
```

### Nginx خطا می‌دهد

```bash
# بررسی تنظیمات nginx
sudo nginx -t

# بررسی لاگ‌های nginx
sudo tail -f /var/log/nginx/error.log
```

### خطای 502 Bad Gateway

این به معنی این است که Nginx نمی‌تواند به Gunicorn متصل شود:

```bash
# بررسی که Gunicorn در حال اجرا است
sudo systemctl status syntel

# بررسی که پورت 8000 باز است
sudo netstat -tuln | grep 8000

# راه‌اندازی مجدد
sudo systemctl restart syntel
```

### خطای Static Files

```bash
cd /opt/project/Syntel-/syntel
source venv/bin/activate
python manage.py collectstatic --noinput
sudo systemctl restart nginx
```

---

## 📊 نظارت و Performance

### بررسی استفاده از منابع

```bash
# استفاده CPU و RAM
htop

# فضای دیسک
df -h

# وضعیت پردازش‌ها
ps aux | grep gunicorn
```

### تنظیم Workers

در فایل `gunicorn_config.py`:

```python
workers = 5  # تعداد workerها را بر اساس CPU تنظیم کنید
```

فرمول پیشنهادی: `workers = (2 × تعداد هسته‌های CPU) + 1`

---

## 📁 ساختار فایل‌های مهم

```
/opt/project/Syntel-/syntel/
├── manage.py               # Django management
├── requirements.txt        # Python dependencies
├── .env.production        # تنظیمات production
├── gunicorn_config.py     # تنظیمات Gunicorn
├── nginx_server.conf      # تنظیمات Nginx
├── syntel.service         # Systemd service
├── deploy_server.sh       # اسکریپت deploy
├── check_deployment.sh    # بررسی وضعیت
├── db.sqlite3            # دیتابیس
├── logs/                 # فایل‌های لاگ
├── media/                # فایل‌های آپلود شده
├── staticfiles/          # فایل‌های استاتیک
└── venv/                 # محیط مجازی Python
```

---

## 🆘 پشتیبانی

در صورت بروز مشکل:

1. لاگ‌ها را بررسی کنید
2. از اسکریپت `check_deployment.sh` استفاده کنید
3. تنظیمات `.env.production` را بررسی کنید
4. مطمئن شوید تمام سرویس‌ها در حال اجرا هستند

---

## ✅ چک‌لیست نهایی

- [ ] تمام سرویس‌ها در حال اجرا هستند (`systemctl status`)
- [ ] سایت از طریق IP قابل دسترسی است
- [ ] پنل ادمین کار می‌کند
- [ ] فایل‌های استاتیک لود می‌شوند
- [ ] رمز عبور admin تغییر کرده است
- [ ] فایروال پیکربندی شده است
- [ ] بک‌آپ منظم راه‌اندازی شده است

---

**تاریخ آخرین به‌روزرسانی**: 2026-02-08
**نسخه**: 1.0.0
