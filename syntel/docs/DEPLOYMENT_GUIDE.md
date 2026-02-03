# راهنمای استقرار پروژه Syntel

## پیش‌نیازها

- Docker و Docker Compose نصب شده باشد
- دامنه (Domain) آماده برای سرور
- دسترسی SSH به سرور
- حداقل 2GB RAM و 20GB فضای دیسک

## مراحل استقرار

### 1. کلون کردن پروژه

```bash
git clone https://github.com/tvfm22/Syntel-.git
cd Syntel-/syntel
```

### 2. تنظیمات محیطی

کپی کردن فایل نمونه و تکمیل متغیرها:

```bash
cp .env.example .env
nano .env
```

متغیرهای مهم:
- `DJANGO_SECRET_KEY`: کلید امنیتی Django (حداقل 50 کاراکتر تصادفی)
- `DJANGO_ALLOWED_HOSTS`: دامنه‌های مجاز (مثال: yourdomain.com,www.yourdomain.com)
- `DB_PASSWORD`: رمز عبور قوی برای دیتابیس
- `EMAIL_*`: تنظیمات ایمیل برای ارسال اعلان‌ها

### 3. ساخت و اجرای کانتینرها

```bash
docker-compose up -d --build
```

### 4. اجرای Migration ها

```bash
docker-compose exec web python manage.py migrate
```

### 5. ایجاد Superuser

```bash
docker-compose exec web python manage.py createsuperuser
```

### 6. جمع‌آوری فایل‌های Static

```bash
docker-compose exec web python manage.py collectstatic --noinput
```

### 7. بررسی وضعیت

```bash
docker-compose ps
docker-compose logs web
```

## دسترسی به برنامه

- آدرس سایت: `http://your-domain.com`
- پنل ادمین: `http://your-domain.com/admin/`

## پشتیبان‌گیری

### پشتیبان دیتابیس

```bash
docker-compose exec db pg_dump -U syntel_user syntel_prod > backup_$(date +%Y%m%d_%H%M%S).sql
```

### بازیابی دیتابیس

```bash
cat backup_file.sql | docker-compose exec -T db psql -U syntel_user syntel_prod
```

## به‌روزرسانی

```bash
git pull origin main
docker-compose down
docker-compose up -d --build
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py collectstatic --noinput
```

## Monitoring

### لاگ‌ها

```bash
# همه لاگ‌ها
docker-compose logs -f

# فقط لاگ web
docker-compose logs -f web

# فقط لاگ database
docker-compose logs -f db
```

### بررسی منابع

```bash
docker stats
```

## عیب‌یابی

### مشکل اتصال به دیتابیس

```bash
docker-compose exec db psql -U syntel_user -d syntel_prod
```

### ری‌استارت سرویس‌ها

```bash
docker-compose restart web
docker-compose restart db
docker-compose restart redis
```

### پاک کردن کامل و شروع مجدد

```bash
docker-compose down -v
docker-compose up -d --build
```

## امنیت

✅ اطمینان از تنظیم `DJANGO_DEBUG=False` در production
✅ استفاده از HTTPS (راه‌اندازی SSL/TLS)
✅ تغییر `SECRET_KEY` به یک مقدار تصادفی و امن
✅ استفاده از رمز عبور قوی برای دیتابیس
✅ محدود کردن دسترسی به پورت‌ها از طریق Firewall
✅ به‌روزرسانی منظم Docker Images و Dependencies

## پشتیبانی

در صورت بروز مشکل، لاگ‌ها را بررسی کنید و با تیم توسعه تماس بگیرید.
