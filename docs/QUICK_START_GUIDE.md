# راهنمای شروع سریع Syntel

## نصب و راه‌اندازی سریع

### مرحله 1: پیش‌نیازها
```bash
# نصب Python 3.8+
python --version

# دیتابیس پیش‌فرض SQLite است و نصب جداگانه لازم ندارد.
```

### مرحله 2: کلون کردن پروژه
```bash
git clone <repository-url>
cd Syntel
```

### مرحله 3: تنظیم محیط مجازی
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python -m venv venv
source venv/bin/activate
```

### مرحله 4: نصب وابستگی‌ها
```bash
pip install -r requirements.txt
```

### مرحله 5: تنظیمات محیطی و دیتابیس
```bash
cp .env.example .env
# DJANGO_SECRET_KEY و DATA_PATH را در .env تنظیم کنید

# دیتابیس پیش‌فرض SQLite است و در data/db/db.sqlite3 ساخته می‌شود.
python manage.py migrate

# ایجاد کاربر مدیر
python manage.py createsuperuser
```

### مرحله 7: اجرای سرور
```bash
python manage.py runserver
```

### مرحله 8: دسترسی به سیستم
- آدرس: http://127.0.0.1:8000
- ورود با نام کاربری و رمز عبور مدیر

## تنظیمات اولیه

### افزودن کاربران پایه
1. وارد پنل ادمین شوید: http://127.0.0.1:8000/admin/
2. کاربران جدید اضافه کنید:
   - آشپزخانه: نقش `kitchen`
   - بار: نقش `bar`
   - کانتر: نقش `counter`
   - خرید: نقش `purchasing`
   - مدیر: نقش `manager`

### تنظیمات پایه
1. **دسته‌بندی محصولات**: افزودن دسته‌های اصلی
2. **واحدهای اندازه‌گیری**: تعریف واحدهای مورد استفاده
3. **روش‌های پرداخت**: تنظیم روش‌های پرداخت
4. **تأمین‌کنندگان**: افزودن تأمین‌کنندگان اولیه

## تست سیستم

### ایجاد درخواست خرید نمونه
1. با کاربر kitchen/bar/counter وارد شوید
2. به بخش "درخواست خرید" بروید
3. محصول نمونه اضافه کنید
4. درخواست را ثبت کنید

### تأیید درخواست
1. با کاربر purchasing وارد شوید
2. درخواست را تأیید کنید
3. سفارش خرید ایجاد کنید

### ایجاد فاکتور و پرداخت
1. وضعیت سفارش را به "دریافت شده" تغییر دهید
2. فاکتور ایجاد کنید
3. با کاربر manager فاکتور را تأیید کنید
4. پرداخت ثبت کنید

## پشتیبان‌گیری اولیه

### پشتیبان‌گیری از داده‌ها
```bash
# پشتیبان‌گیری از پایگاه داده
python manage.py backup_database

# یا از طریق رابط کاربری
# وارد بخش "پشتیبان‌گیری" شوید
# بر روی "پشتیبان‌گیری پایگاه داده" کلیک کنید
```

## مانیتورینگ سیستم

### بررسی عملکرد
- به بخش "مانیتورینگ سیستم" بروید
- منابع سیستم را بررسی کنید
- هشدارهای سیستم را مشاهده کنید

## عیب‌یابی سریع

### مشکل: سرور اجرا نمی‌شود
```bash
# بررسی پورت 8000
netstat -an | find "8000"

# یا
python manage.py runserver --noreload
```

### مشکل: اتصال به پایگاه داده
```bash
# بررسی وضعیت PostgreSQL
# Windows: services.msc
# Linux: sudo systemctl status postgresql

# تست اتصال
psql -U syntel_user -d syntel_db -h localhost
```

### مشکل: فایل‌های static بارگذاری نمی‌شوند
```bash
python manage.py collectstatic
```

### مشکل: migration error
```bash
python manage.py showmigrations
python manage.py migrate --fake-initial
```

## تنظیمات تولید (Production)

### 1. تنظیمات امنیتی
```python
# settings.py
DEBUG = False
SECRET_KEY = 'your-production-secret-key'
ALLOWED_HOSTS = ['your-domain.com']
```

### 2. وب سرور
```bash
# نصب Gunicorn
pip install gunicorn

# اجرای با Gunicorn
gunicorn syntel.wsgi:application --bind 0.0.0.0:8000
```

### 3. Nginx (اختیاری)
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /static/ {
        alias /path/to/your/static/files/;
    }
}
```

### 4. SSL Certificate
```bash
# استفاده از Let's Encrypt
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## پشتیبانی

### منابع آموزشی
- [راهنمای کاربری کامل](USER_MANUAL.md)
- [راهنمای مدیر سیستم](MANAGER_MANUAL.md)
- [مستندات فنی](TECHNICAL_DOCUMENTATION.md)

### تماس با پشتیبانی
- ایمیل: support@syntel.com
- تلفن: 021-12345678
- وب‌سایت: https://syntel.com/support

---

*برای شروع سریع، این راهنما را دنبال کنید. در صورت مشکل، با تیم پشتیبانی تماس بگیرید.*