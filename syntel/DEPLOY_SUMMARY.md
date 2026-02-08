# 📦 فایل‌های آماده شده برای دیپلوی

## ✅ فایل‌های ایجاد شده:

### اسکریپت‌های اصلی
- ✅ `install_and_deploy.sh` - نصب و دیپلوی کامل (یک کلیک!)
- ✅ `deploy_production.sh` - دیپلوی production
- ✅ `quick_deploy.sh` - آپدیت سریع
- ✅ `server_setup.sh` - نصب اولیه سرور
- ✅ `backup.sh` - پشتیبان‌گیری خودکار
- ✅ `restore.sh` - بازیابی از بکاپ
- ✅ `health_check.sh` - بررسی سلامت سیستم

### مستندات
- ✅ `DEPLOYMENT.md` - راهنمای کامل دیپلوی
- ✅ `QUICKSTART.md` - راهنمای سریع شروع
- ✅ این فایل - خلاصه و دستورات

### فایل‌های پیکربندی
- ✅ `.env` - تنظیمات محیطی (IP: 45.159.150.199)
- ✅ `docker-compose.yml` - تنظیمات Docker
- ✅ `nginx.conf` - تنظیمات Nginx
- ✅ `Dockerfile` - تصویر Docker

---

## 🚀 دیپلوی در 3 گام

### گام 1: دسترسی به سرور
```bash
# اتصال به سرور
ssh root@45.159.150.199

# رفتن به پوشه پروژه
cd /opt/project/Syntel-/syntel
```

### گام 2: اجرای دیپلوی یک‌کلیکه
```bash
sudo ./install_and_deploy.sh
```

این اسکریپت به صورت خودکار:
- Docker را نصب می‌کند (در صورت نیاز)
- Docker Compose را نصب می‌کند
- Firewall را تنظیم می‌کند
- دیتابیس را راه‌اندازی می‌کند
- Migration ها را اجرا می‌کند
- Static files را جمع‌آوری می‌کند
- کاربر admin را ایجاد می‌کند
- تمام سرویس‌ها را start می‌کند

**زمان تقریبی:** 5-10 دقیقه

### گام 3: دسترسی به سیستم
🌐 آدرس: http://45.159.150.199
👤 نام کاربری: admin
🔑 رمز عبور: admin123

---

## 📋 دستورات مفید

### مدیریت سرویس‌ها
```bash
# مشاهده وضعیت
docker-compose ps

# مشاهده لاگ‌ها
docker-compose logs -f

# ری‌استارت
docker-compose restart

# توقف
docker-compose down

# شروع مجدد
docker-compose up -d
```

### پشتیبان‌گیری و بازیابی
```bash
# ایجاد بکاپ
./backup.sh

# بازیابی
./restore.sh ./backups/syntel_backup_YYYYMMDD_HHMMSS.sql.gz
```

### بررسی سلامت
```bash
# چک کامل سیستم
./health_check.sh

# لاگ یک سرویس خاص
docker-compose logs -f web
docker-compose logs -f db
docker-compose logs -f nginx
```

### مدیریت دیتابیس
```bash
# دسترسی به shell دیتابیس
docker exec -it syntel_db psql -U syntel_user -d syntel_prod

# بررسی اندازه دیتابیس
docker exec syntel_db psql -U syntel_user -d syntel_prod -c "SELECT pg_size_pretty(pg_database_size('syntel_prod'));"
```

### مدیریت Django
```bash
# دسترسی به Django shell
docker-compose exec web python manage.py shell

# ایجاد کاربر جدید
docker-compose exec web python manage.py createsuperuser

# تغییر رمز عبور
docker-compose exec web python manage.py changepassword admin

# اجرای migration
docker-compose exec web python manage.py migrate
```

---

## 🔍 عیب‌یابی

### مشکل در اتصال به وب
```bash
# بررسی لاگ‌های nginx
docker-compose logs nginx

# بررسی لاگ‌های web
docker-compose logs web

# ری‌استارت سرویس‌ها
docker-compose restart nginx web
```

### مشکل در دیتابیس
```bash
# بررسی وضعیت دیتابیس
docker-compose logs db

# ری‌استارت دیتابیس
docker-compose restart db

# تست اتصال
docker exec syntel_db pg_isready -U syntel_user -d syntel_prod
```

### مشکل در static files
```bash
# جمع‌آوری مجدد
docker-compose exec web python manage.py collectstatic --noinput --clear

# ری‌استارت nginx
docker-compose restart nginx
```

### دیپلوی مجدد از صفر
```bash
# توقف و حذف همه چیز
docker-compose down -v

# پاک‌سازی Docker
docker system prune -af

# دیپلوی مجدد
./install_and_deploy.sh
```

---

## ⚠️ نکات امنیتی مهم

### بلافاصله بعد از دیپلوی:

1. **تغییر رمز عبور ادمین**
```bash
docker-compose exec web python manage.py changepassword admin
```

2. **تغییر SECRET_KEY**
```bash
# ایجاد کلید جدید
python3 -c "import secrets; print(secrets.token_urlsafe(50))"

# فایل .env را ویرایش کنید
nano .env
# DJANGO_SECRET_KEY را با کلید جدید جایگزین کنید

# ری‌استارت
docker-compose restart web
```

3. **تغییر رمز دیتابیس** (اختیاری)
```bash
nano .env
# DB_PASSWORD را تغییر دهید

# ری‌بیلد و ری‌استارت
docker-compose down
docker-compose up -d
```

---

## 📊 نظارت بر سیستم

### بررسی منابع
```bash
# استفاده از CPU و RAM
docker stats

# فضای دیسک
df -h

# لاگ‌های سیستم
journalctl -u docker -f
```

### بکاپ خودکار روزانه
```bash
# تنظیم cron job
crontab -e

# اضافه کنید:
0 2 * * * cd /opt/project/Syntel-/syntel && ./backup.sh >> /var/log/syntel_backup.log 2>&1
```

---

## 🔄 بروزرسانی

### آپدیت کد
```bash
# از Git
git pull origin main
./quick_deploy.sh

# یا دستی
docker-compose restart web
```

### آپدیت Docker Images
```bash
docker-compose pull
docker-compose up -d
```

---

## 📞 کمک و پشتیبانی

### مستندات
- [QUICKSTART.md](QUICKSTART.md) - شروع سریع
- [DEPLOYMENT.md](DEPLOYMENT.md) - راهنمای کامل
- [../TROUBLESHOOTING_GUIDE.md](../TROUBLESHOOTING_GUIDE.md) - رفع مشکلات

### دستورات کاربردی
```bash
# خلاصه وضعیت
./health_check.sh

# لاگ‌های اخیر
docker-compose logs --tail=100

# ری‌استارت کامل
docker-compose restart
```

---

## ✅ چک‌لیست نهایی

قبل از استفاده production:

- [ ] سیستم با `./health_check.sh` چک شده
- [ ] رمز admin تغییر یافته
- [ ] SECRET_KEY منحصر به فرد است
- [ ] بکاپ خودکار تنظیم شده
- [ ] فایروال فعال است
- [ ] SSL برای HTTPS تنظیم شده (اختیاری)
- [ ] ایمیل‌ها تست شده
- [ ] کاربران اولیه ایجاد شده
- [ ] داده‌های تستی وارد شده

---

**تاریخ:** 8 فوریه 2026
**نسخه:** 1.0
**IP سرور:** 45.159.150.199

**✨ موفق باشید!**
