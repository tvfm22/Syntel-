# راهنمای عیب‌یابی سیستم Syntel

## مشکلات رایج و راه‌حل‌ها

### 1. مشکلات ورود به سیستم

#### مشکل: کاربر نمی‌تواند وارد شود
**علائم:**
- پیام "Invalid credentials"
- صفحه ورود مجدداً نمایش داده می‌شود

**راه‌حل‌ها:**
1. بررسی نام کاربری و رمز عبور
2. تأیید فعال بودن کاربر در پنل ادمین
3. بازنشانی رمز عبور توسط مدیر
4. بررسی Caps Lock و زبان صفحه‌کلید

#### مشکل: صفحه ورود بارگذاری نمی‌شود
**علائم:**
- خطای 500 یا صفحه سفید
- زمان بارگذاری طولانی

**راه‌حل‌ها:**
1. بررسی وضعیت سرور Django
2. بررسی لاگ‌های سرور
3. تأیید اتصال به پایگاه داده
4. بررسی فایل‌های static

### 2. مشکلات پایگاه داده

#### مشکل: اتصال به PostgreSQL ناموفق
**علائم:**
- خطای "connection refused"
- خطای "FATAL: password authentication failed"

**راه‌حل‌ها:**
```bash
# بررسی وضعیت PostgreSQL
sudo systemctl status postgresql

# تست اتصال
psql -U syntel_user -d syntel_db -h localhost

# بررسی تنظیمات در settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'syntel_db',
        'USER': 'syntel_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

#### مشکل: Migration error
**علائم:**
- خطا هنگام اجرای `python manage.py migrate`
- جدول وجود ندارد

**راه‌حل‌ها:**
```bash
# بررسی migrationهای اعمال شده
python manage.py showmigrations

# اعمال migrationهای باقی‌مانده
python manage.py migrate

# در صورت مشکل، fake initial migration
python manage.py migrate --fake-initial
```

### 3. مشکلات عملکرد

#### مشکل: سیستم کند است
**علائم:**
- زمان بارگذاری صفحات طولانی
- تأخیر در پاسخ‌ها

**راه‌حل‌ها:**
1. **بررسی منابع سیستم:**
   - مانیتورینگ CPU و حافظه
   - بررسی فضای دیسک

2. **بهینه‌سازی پایگاه داده:**
```sql
-- پاک‌سازی و بهینه‌سازی
VACUUM ANALYZE;

-- بررسی کوئری‌های کند
SELECT query, total_time, calls, mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;
```

3. **بهینه‌سازی Django:**
   - استفاده از select_related و prefetch_related
   - پیاده‌سازی caching
   - بهینه‌سازی کوئری‌ها

#### مشکل: مصرف بالای حافظه
**علائم:**
- سرور از حافظه خارج می‌شود
- عملکرد کند سیستم

**راه‌حل‌ها:**
1. افزایش RAM سرور
2. بهینه‌سازی کوئری‌های Django
3. پیاده‌سازی pagination
4. استفاده از database indexing

### 4. مشکلات فایل‌ها و رسانه

#### مشکل: فایل‌های static بارگذاری نمی‌شوند
**علائم:**
- CSS و JS بارگذاری نمی‌شوند
- صفحه بدون استایل نمایش داده می‌شود

**راه‌حل‌ها:**
```bash
# جمع‌آوری فایل‌های static
python manage.py collectstatic

# بررسی تنظیمات STATIC_ROOT و STATIC_URL
# در settings.py

# در تولید، تنظیمات Nginx
location /static/ {
    alias /path/to/static/files/;
}
```

#### مشکل: آپلود فایل‌ها ناموفق
**علائم:**
- خطا هنگام آپلود فایل
- فایل ذخیره نمی‌شود

**راه‌حل‌ها:**
1. بررسی دسترسی‌های فایل سیستم
2. تأیید تنظیمات MEDIA_ROOT
3. بررسی حجم فایل (محدودیت upload)
4. بررسی فضای دیسک

### 5. مشکلات ایمیل و اعلان‌ها

#### مشکل: ایمیل ارسال نمی‌شود
**علائم:**
- اعلان‌ها ارسال نمی‌شوند
- خطا در لاگ‌ها

**راه‌حل‌ها:**
```python
# تنظیمات ایمیل در settings.py
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@gmail.com'
EMAIL_HOST_PASSWORD = 'your-password'
```

### 6. مشکلات امنیتی

#### مشکل: CSRF token error
**علائم:**
- خطای "CSRF verification failed"
- فرم‌ها ارسال نمی‌شوند

**راه‌حل‌ها:**
1. بررسی تنظیمات CSRF در settings.py
2. تأیید وجود {% csrf_token %} در قالب‌ها
3. بررسی تنظیمات کوکی‌ها

#### مشکل: Session expired
**علائم:**
- کاربر به طور ناگهانی خارج می‌شود

**راه‌حل‌ها:**
```python
# تنظیمات session در settings.py
SESSION_COOKIE_AGE = 3600  # 1 hour
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_SECURE = True  # برای HTTPS
```

### 7. مشکلات پشتیبان‌گیری

#### مشکل: پشتیبان‌گیری ناموفق
**علائم:**
- خطا هنگام پشتیبان‌گیری
- فایل پشتیبان ایجاد نمی‌شود

**راه‌حل‌ها:**
```bash
# بررسی دسترسی به pg_dump
which pg_dump

# تست پشتیبان‌گیری دستی
pg_dump -U syntel_user -h localhost syntel_db > test_backup.sql

# بررسی فضای دیسک
df -h

# بررسی تنظیمات PostgreSQL
# فایل pg_hba.conf برای دسترسی کاربر
```

#### مشکل: بازیابی پشتیبان ناموفق
**علائم:**
- خطا هنگام بازیابی
- داده‌ها کامل بازیابی نمی‌شوند

**راه‌حل‌ها:**
```bash
# تست بازیابی
psql -U syntel_user -d syntel_db < backup_file.sql

# بررسی یکپارچگی فایل پشتیبان
head -n 20 backup_file.sql

# بازیابی در پایگاه داده خالی
createdb syntel_db_restore
psql -U syntel_user -d syntel_db_restore < backup_file.sql
```

### 8. مشکلات مانیتورینگ

#### مشکل: داده‌های مانیتورینگ نمایش داده نمی‌شوند
**علائم:**
- نمودارها خالی هستند
- اطلاعات سیستم نمایش داده نمی‌شود

**راه‌حل‌ها:**
1. **نصب psutil:**
```bash
pip install psutil
```

2. **بررسی دسترسی سیستم:**
```python
import psutil
print(psutil.cpu_percent())
print(psutil.virtual_memory())
```

3. **بررسی API مانیتورینگ:**
   - دسترسی به `/system/monitoring/api/`
   - بررسی پاسخ JSON

### 9. مشکلات گزارشات

#### مشکل: گزارشات PDF ایجاد نمی‌شوند
**علائم:**
- خطا هنگام تولید PDF
- فایل PDF خالی است

**راه‌حل‌ها:**
1. **نصب ReportLab:**
```bash
pip install reportlab
```

2. **بررسی فونت‌ها:**
```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
```

3. **بررسی دسترسی فایل:**
   - دسترسی نوشتن در دایرکتوری temp
   - فضای دیسک کافی

#### مشکل: گزارشات Excel ایجاد نمی‌شوند
**علائم:**
- خطا هنگام تولید Excel

**راه‌حل‌ها:**
```bash
pip install openpyxl
```

### 10. مشکلات شبکه و اتصال

#### مشکل: اتصال به سرور قطع می‌شود
**علائم:**
- timeout error
- connection refused

**راه‌حل‌ها:**
1. **بررسی firewall:**
```bash
# Linux
sudo ufw status
sudo ufw allow 8000

# Windows Firewall
# Control Panel > System and Security > Windows Defender Firewall
```

2. **بررسی تنظیمات شبکه:**
   - ALLOWED_HOSTS در settings.py
   - تنظیمات proxy

## ابزارهای عیب‌یابی

### لاگ‌های Django
```python
# settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'logs/django.log',
        },
    },
    'root': {
        'handlers': ['file'],
        'level': 'DEBUG',
    },
}
```

### مانیتورینگ پایگاه داده
```sql
-- اتصالات فعال
SELECT * FROM pg_stat_activity;

-- اندازه جداول
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- کوئری‌های کند
SELECT query, total_time, calls, mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;
```

### تست عملکرد
```bash
# تست بارگذاری
ab -n 100 -c 10 http://localhost:8000/

# تست حافظه
python -c "import psutil; print(psutil.virtual_memory())"

# تست پایگاه داده
python manage.py dbshell
SELECT count(*) FROM purchases_purchaserequest;
```

## تماس با پشتیبانی

### اطلاعات لازم برای گزارش مشکل
1. **توضیح مشکل:** چه اتفاقی افتاده؟
2. **مراحل بازتولید:** چگونه مشکل را ایجاد کنیم؟
3. **پیام خطا:** متن کامل خطا
4. **محیط سیستم:** OS، Python version، Django version
5. **لاگ‌ها:** لاگ‌های مربوط به مشکل
6. **زمان وقوع:** چه زمانی مشکل رخ داده؟

### کانال‌های پشتیبانی
- **ایمیل:** support@syntel.com
- **تلفن:** 021-12345678
- **تیکت سیستم:** ایجاد تیکت در پنل پشتیبانی
- **فوریت:** برای مشکلات حیاتی از تلفن استفاده کنید

---

*این راهنما برای عیب‌یابی مشکلات رایج سیستم Syntel تهیه شده است. برای مشکلات پیچیده‌تر با تیم پشتیبانی تماس بگیرید.*