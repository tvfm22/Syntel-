# راهنمای دیپلوی پروژه Syntel
# آی‌پی سرور: 45.159.150.199

## پیش‌نیازها

قبل از شروع دیپلوی، مطمئن شوید که موارد زیر نصب شده‌اند:

```bash
# نصب Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# نصب Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# بررسی نصب
docker --version
docker-compose --version
```

## مراحل دیپلوی

### 1. آماده‌سازی سرور

```bash
# اتصال به سرور
ssh root@45.159.150.199

# ایجاد دایرکتوری پروژه
mkdir -p /opt/syntel
cd /opt/syntel
```

### 2. کپی فایل‌های پروژه

```bash
# از طریق Git
git clone <repository-url> .

# یا از طریق SCP
scp -r /path/to/project/* root@45.159.150.199:/opt/syntel/
```

### 3. پیکربندی متغیرهای محیطی

فایل `.env` را بررسی کنید و در صورت نیاز تغییر دهید:

```bash
cd /opt/syntel
nano .env
```

موارد مهم برای بررسی:
- `DJANGO_SECRET_KEY`: کلید امنیتی منحصر به فرد
- `DB_PASSWORD`: رمز عبور پایگاه داده
- `EMAIL_*`: تنظیمات ایمیل

### 4. اجرای دیپلوی

```bash
cd /opt/syntel
./deploy_production.sh
```

این اسکریپت به صورت خودکار:
- ✅ Docker containerها را متوقف می‌کند
- ✅ پشتیبان‌گیری از دیتابیس را انجام می‌دهد
- ✅ تصاویر Docker را می‌سازد
- ✅ Migration ها را اجرا می‌کند
- ✅ فایل‌های استاتیک را جمع‌آوری می‌کند
- ✅ سرویس‌ها را راه‌اندازی می‌کند
- ✅ وضعیت سلامت را بررسی می‌کند

### 5. دسترسی به سیستم

پس از اتمام دیپلوی:

🌐 **آدرس وب**: http://45.159.150.199

👤 **اطلاعات ورود پیش‌فرض**:
- نام کاربری: `admin`
- رمز عبور: `admin123`

⚠️ **مهم**: بلافاصله رمز عبور پیش‌فرض را تغییر دهید!

## دستورات مفید

### مشاهده لاگ‌ها
```bash
# لاگ همه سرویس‌ها
docker-compose logs -f

# لاگ یک سرویس خاص
docker-compose logs -f web
docker-compose logs -f nginx
docker-compose logs -f db
```

### ری‌استارت سرویس‌ها
```bash
# ری‌استارت همه
docker-compose restart

# ری‌استارت یک سرویس
docker-compose restart web
```

### بررسی وضعیت
```bash
# وضعیت containerها
docker-compose ps

# بررسی سلامت سیستم
./health_check.sh
```

### پشتیبان‌گیری
```bash
# ایجاد بکاپ
./backup.sh

# بازیابی از بکاپ
./restore.sh ./backups/syntel_backup_YYYYMMDD_HHMMSS.sql.gz
```

### دیپلوی سریع (برای آپدیت‌های کوچک)
```bash
./quick_deploy.sh
```

### دسترسی به دیتابیس
```bash
# Shell دیتابیس
docker exec -it syntel_db psql -U syntel_user -d syntel_prod

# بررسی اندازه دیتابیس
docker exec syntel_db psql -U syntel_user -d syntel_prod -c "SELECT pg_size_pretty(pg_database_size('syntel_prod'));"
```

### دسترسی به Django Shell
```bash
docker-compose exec web python manage.py shell
```

### ایجاد کاربر جدید
```bash
docker-compose exec web python manage.py createsuperuser
```

## نظارت و مدیریت

### بررسی منابع سیستم
```bash
# استفاده از CPU و RAM
docker stats

# فضای دیسک
df -h

# لاگ‌های سیستم
journalctl -u docker -f
```

### پاک‌سازی منابع Docker
```bash
# حذف تصاویر استفاده نشده
docker image prune -a

# حذف volumeهای استفاده نشده
docker volume prune

# پاک‌سازی کامل
docker system prune -a --volumes
```

## عیب‌یابی

### سرویس web بالا نمی‌آید
```bash
# بررسی لاگ‌ها
docker-compose logs web

# ری‌بیلد و استارت
docker-compose build web
docker-compose up -d web
```

### خطای اتصال به دیتابیس
```bash
# بررسی وضعیت دیتابیس
docker-compose ps db

# بررسی لاگ دیتابیس
docker-compose logs db

# ری‌استارت دیتابیس
docker-compose restart db
```

### مشکل در فایل‌های استاتیک
```bash
# جمع‌آوری مجدد
docker-compose exec web python manage.py collectstatic --noinput --clear

# ری‌استارت nginx
docker-compose restart nginx
```

### خطای Migration
```bash
# بررسی وضعیت migrationها
docker-compose exec web python manage.py showmigrations

# اجرای مجدد
docker-compose exec web python manage.py migrate
```

## امنیت

### تغییر رمزهای عبور
```bash
# تغییر رمز ادمین Django
docker-compose exec web python manage.py changepassword admin

# تغییر SECRET_KEY
# فایل .env را ویرایش کنید و سپس:
docker-compose restart web
```

### فعال‌سازی HTTPS (اختیاری)
```bash
# نصب Certbot
sudo apt install certbot python3-certbot-nginx

# دریافت گواهی SSL
sudo certbot --nginx -d yourdomain.com

# تنظیم تمدید خودکار
sudo certbot renew --dry-run
```

### بررسی امنیتی
```bash
# بررسی پورت‌های باز
sudo netstat -tulpn

# بررسی فایروال
sudo ufw status

# فعال‌سازی فایروال
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

## بکاپ خودکار

### تنظیم Cron Job برای بکاپ روزانه
```bash
# ویرایش crontab
crontab -e

# اضافه کردن این خط برای بکاپ روزانه در ساعت 2 صبح
0 2 * * * cd /opt/syntel && ./backup.sh >> /var/log/syntel_backup.log 2>&1
```

## بروزرسانی

### آپدیت کد
```bash
cd /opt/syntel
git pull origin main
./quick_deploy.sh
```

### آپدیت Docker Images
```bash
docker-compose pull
docker-compose up -d
```

## ساختار فایل‌ها

```
/opt/syntel/
├── .env                      # متغیرهای محیطی (حساس!)
├── docker-compose.yml        # تنظیمات Docker
├── Dockerfile                # تصویر Docker
├── nginx.conf                # تنظیمات Nginx
├── requirements.txt          # وابستگی‌های Python
├── deploy_production.sh      # اسکریپت دیپلوی کامل
├── quick_deploy.sh           # دیپلوی سریع
├── backup.sh                # پشتیبان‌گیری
├── restore.sh               # بازیابی
├── health_check.sh          # بررسی سلامت
├── backups/                 # پشتیبان‌های دیتابیس
├── logs/                    # فایل‌های لاگ
├── media/                   # فایل‌های آپلود شده
└── staticfiles/             # فایل‌های استاتیک
```

## پشتیبانی

در صورت بروز مشکل:
1. لاگ‌ها را بررسی کنید: `docker-compose logs`
2. وضعیت سلامت را چک کنید: `./health_check.sh`
3. اسناد عیب‌یابی را مطالعه کنید

## نکات مهم

⚠️ **هشدارهای امنیتی**:
- رمزهای پیش‌فرض را تغییر دهید
- فایل `.env` را در Git commit نکنید
- بکاپ منظم بگیرید
- لاگ‌ها را بررسی کنید

✅ **بهترین شیوه‌ها**:
- بکاپ قبل از هر آپدیت
- تست در محیط staging
- نظارت بر منابع سیستم
- بروزرسانی منظم امنیتی
