# 🏢 Syntel - سیستم مدیریت خرید و انبار

[![Django](https://img.shields.io/badge/Django-5.0.2-green.svg)](https://www.djangoproject.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-blue.svg)](https://sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

سیستم جامع مدیریت خرید، انبار، فاکتور و مالی با رابط کاربری فارسی - آماده برای استقرار روی سرور

## ساختار پروژه

```
Syntel/
├── manage.py                  # نقطه ورود Django
├── requirements.txt           # وابستگی‌های Python
├── Dockerfile                 # ساخت ایمیج Production
├── entrypoint.sh              # اجرای migration و collectstatic
├── liara.json                 # تنظیمات استقرار Liara
├── README.md                  # این فایل
├── docs/                      # مستندات اصلی و آرشیو
├── reference/                 # فایل‌های مرجع (اکسل منو و ورودی‌های قدیمی)
├── data/                      # داده‌های قابل نوشتن: db, uploads, logs, cache, tmp, sessions, backups
├── syntel/
│   ├── syntel/                # تنظیمات Django
│   ├── purchases/             # اپلیکیشن اصلی
│   ├── frontend/              # قالب‌ها و فایل‌های استاتیک
│   ├── scripts/               # اسکریپت‌های کمکی
│   ├── deploy/                # فایل‌های استقرار قدیمی و آرشیو
│   ├── staticfiles/           # خروجی collectstatic
│   └── gunicorn_config.py     # تنظیمات Gunicorn
└── .venv/                     # محیط مجازی محلی (خارج از گیت)
```

## 🚀 نصب سریع

### با Docker (Production)

استقرار فعلی روی Liara با `Dockerfile` ریشه پروژه انجام می‌شود. راهنمای کامل در
[`docs/LIARA_DEPLOYMENT_GUIDE.md`](docs/LIARA_DEPLOYMENT_GUIDE.md) است.

### نصب دستی (Development)

```bash
# از ریشه پروژه
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## ویژگی‌ها

### 🛒 مدیریت خرید
- ثبت درخواست‌های خرید توسط بخش‌های مختلف
- تأیید و مدیریت درخواست‌ها توسط واحد خرید
- ایجاد و پیگیری سفارش‌های خرید
- مدیریت تأمین‌کنندگان و ارزیابی عملکرد

### 📄 مدیریت فاکتور و پرداخت
- ایجاد و تأیید فاکتورها
- ثبت و پیگیری پرداخت‌ها
- پشتیبانی از روش‌های پرداخت مختلف
- گزارشات مالی جامع

### 👥 مدیریت کاربران
- نقش‌های کاربری مختلف (آشپزخانه، بار، کانتر، خرید، مدیر)
- کنترل دسترسی مبتنی بر نقش
- مدیریت کاربران و رمز عبور

### 📊 گزارشات و تحلیل‌ها
- گزارشات خرید و مالی
- تحلیل عملکرد تأمین‌کنندگان
- گزارشات موجودی و مصرف
- نمودارهای تحلیلی تعاملی

### 💾 پشتیبان‌گیری و بازیابی
- پشتیبان‌گیری کامل از پایگاه داده
- صدور و وارد کردن داده‌ها (JSON/CSV)
- ایجاد اسکریپت‌های انتقال داده

## 📚 مستندات

تمام مستندات پروژه در پوشه `docs/` قرار دارد:

- [راهنمای کاربر](docs/USER_MANUAL.md) - راهنمای استفاده برای کاربران
- [راهنمای مدیر](docs/MANAGER_MANUAL.md) - راهنمای مدیریت سیستم
- [مستندات فنی](docs/TECHNICAL_DOCUMENTATION.md) - مستندات توسعه‌دهنده
- [راهنمای استقرار Liara](docs/LIARA_DEPLOYMENT_GUIDE.md) - نحوه استقرار روی Liara
- [راهنمای عیب‌یابی](docs/TROUBLESHOOTING_GUIDE.md) - رفع مشکلات رایج
- [راهنمای سریع](docs/QUICK_START_GUIDE.md) - شروع سریع
- [مدیریت انبار](docs/STOCK_MANAGEMENT_GUIDE.md) - راهنمای انبار و موجودی

## 🛠️ اسکریپت‌های کمکی

اسکریپت‌های کمکی در پوشه `syntel/scripts/` سازماندهی شده‌اند:

### راه‌اندازی اولیه (`syntel/scripts/setup/`)
```bash
python syntel/scripts/setup/create_users.py          # ایجاد کاربران نمونه
python syntel/scripts/setup/setup_cafes.py           # راه‌اندازی کافه‌ها
```

### مدیریت (`syntel/scripts/management_commands/`)
```bash
python syntel/scripts/management_commands/check_users.py      # بررسی کاربران
python syntel/scripts/management_commands/clear_cache.py      # پاک کردن کش
```

### رفع مشکل (`syntel/scripts/fixes/`)
```bash
python syntel/scripts/fixes/fix_csrf.py              # رفع مشکل CSRF
python syntel/scripts/fixes/fix_urls2.py             # تصحیح URLها
```

### استقرار (`syntel/scripts/deployment/`)
```bash
bash syntel/scripts/deployment/deploy.sh             # استقرار روی سرور
bash syntel/scripts/deployment/validate_production.sh # اعتبارسنجی Production
```

### 📈 مانیتورینگ سیستم
- نظارت بر منابع سیستم (CPU، حافظه، دیسک)
- مانیتورینگ پایگاه داده
- هشدارهای خودکار سیستم
- داشبورد عملکرد real-time

## پیش‌نیازها

- Python 3.11 یا بالاتر
- SQLite (پیش‌فرض پروژه؛ نیازی به نصب جداگانه ندارد)
- pip (مدیریت بسته Python)
- Git

## نصب و راه‌اندازی

### 1. کلون کردن پروژه
```bash
git clone <repository-url>
cd Syntel
```

### 2. ایجاد محیط مجازی
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python -m venv venv
source venv/bin/activate
```

### 3. نصب وابستگی‌ها
```bash
pip install -r requirements.txt
```

### 4. تنظیمات محیطی
```bash
cp .env.example .env
# سپس DJANGO_SECRET_KEY و DATA_PATH را در .env تنظیم کنید
```

دیتابیس پیش‌فرض SQLite است و در `data/db/db.sqlite3` ساخته می‌شود.

### 5. اجرای Migrationها
```bash
python manage.py migrate
```

### 6. ایجاد کاربر مدیر
```bash
python manage.py createsuperuser
```

### 7. اجرای سرور توسعه
```bash
python manage.py runserver
```

### 9. دسترسی به سیستم
- آدرس: http://127.0.0.1:8000
- پنل ادمین: http://127.0.0.1:8000/admin/

## تنظیمات اولیه

### افزودن کاربران پایه
1. وارد پنل ادمین شوید
2. کاربران جدید اضافه کنید با نقش‌های:
   - `kitchen`: آشپزخانه
   - `bar`: بار
   - `counter`: کانتر
   - `purchasing`: خرید
   - `manager`: مدیر

### تنظیمات پایه سیستم
1. **دسته‌بندی محصولات**: افزودن دسته‌های اصلی
2. **واحدهای اندازه‌گیری**: تعریف واحدهای مورد استفاده
3. **روش‌های پرداخت**: تنظیم روش‌های پرداخت
4. **تأمین‌کنندگان**: افزودن تأمین‌کنندگان اولیه

## ساختار پوشه‌های اصلی

```
Syntel/
├── syntel/syntel/          # تنظیمات Django
├── syntel/purchases/       # اپلیکیشن اصلی
├── syntel/frontend/        # قالب‌ها و فایل‌های استاتیک
├── syntel/scripts/         # اسکریپت‌های کمکی
├── syntel/deploy/          # آرشیو استقرار قدیمی
├── reference/              # اکسل منو و ورودی‌های مرجع
├── data/                   # دیتابیس، آپلودها، لاگ‌ها و پشتیبان‌ها
└── docs/                   # مستندات و آرشیو
```

## مستندات

### راهنمای کاربری
- [راهنمای کامل کاربر](docs/USER_MANUAL.md) - راهنمای استفاده از سیستم
- [راهنمای مدیر سیستم](docs/MANAGER_MANUAL.md) - راهنمای مخصوص مدیران
- [راهنمای شروع سریع](docs/QUICK_START_GUIDE.md) - راه‌اندازی سریع سیستم
- [راهنمای عیب‌یابی](docs/TROUBLESHOOTING_GUIDE.md) - حل مشکلات رایج

### مستندات فنی
- [مستندات فنی](docs/TECHNICAL_DOCUMENTATION.md) - معماری، API، و تنظیمات فنی
- [راهنمای مانیتورینگ سیستم](docs/SYSTEM_MONITORING_README.md) - مستندات مانیتورینگ
- [استقرار روی Liara](docs/LIARA_DEPLOYMENT_GUIDE.md) - راهنمای Production

## نقش‌های کاربری

| نقش | دسترسی‌ها |
|-----|-----------|
| **آشپزخانه** | ثبت درخواست خرید مواد اولیه، مشاهده وضعیت درخواست‌ها |
| **بار** | ثبت درخواست خرید نوشیدنی‌ها، مشاهده وضعیت درخواست‌ها |
| **کانتر** | ثبت درخواست خرید اقلام متفرقه، مشاهده وضعیت درخواست‌ها |
| **خرید** | تأیید درخواست‌ها، ایجاد سفارش‌ها، مدیریت تأمین‌کنندگان |
| **مدیر** | دسترسی کامل، تأیید فاکتورها، گزارشات، پشتیبان‌گیری، مانیتورینگ |

## API Endpoints

### Authentication
- `POST /login/` - ورود کاربر
- `POST /logout/` - خروج کاربر

### Purchase Management
- `GET /purchase-request/` - فرم درخواست خرید
- `GET /purchase-requests/` - لیست درخواست‌ها
- `POST /purchase-request/<id>/update/` - به‌روزرسانی درخواست

### Order Management
- `GET /purchase-orders/` - لیست سفارش‌ها
- `POST /purchase-orders/create/<request_id>/` - ایجاد سفارش

### Invoice & Payment
- `GET /invoices/` - لیست فاکتورها
- `POST /invoices/create/<order_id>/` - ایجاد فاکتور
- `GET /payments/` - لیست پرداخت‌ها

### Backup & Monitoring
- `GET /backup/` - صفحه پشتیبان‌گیری
- `POST /backup/database/` - پشتیبان‌گیری از پایگاه داده
- `GET /system/monitoring/` - داشبورد مانیتورینگ

## تکنولوژی‌های استفاده شده

### Backend
- **Django 5.2.8**: فریمورک وب Python
- **PostgreSQL**: پایگاه داده رابطه‌ای
- **Django REST Framework**: API development (اختیاری)

### Frontend
- **HTML5/CSS3**: ساختار و استایل صفحات
- **Bootstrap 4**: فریمورک CSS responsive
- **JavaScript/jQuery**: تعاملات سمت کلاینت
- **Chart.js**: نمودارهای تعاملی

### ابزارهای جانبی
- **ReportLab**: تولید فایل‌های PDF
- **openpyxl**: کار با فایل‌های Excel
- **psutil**: مانیتورینگ منابع سیستم
- **Pillow**: پردازش تصاویر

## امنیت

### ویژگی‌های امنیتی پیاده‌سازی شده
- **Authentication**: سیستم ورود و خروج امن
- **Authorization**: کنترل دسترسی مبتنی بر نقش (RBAC)
- **CSRF Protection**: محافظت در برابر حملات CSRF
- **SQL Injection Protection**: استفاده از ORM Django
- **XSS Protection**: sanitization ورودی‌ها
- **Session Security**: مدیریت امن sessionها

### تنظیمات امنیتی تولید
```python
# settings.py برای محیط تولید
DEBUG = False
SECRET_KEY = 'production-secret-key'
ALLOWED_HOSTS = ['your-domain.com']
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

## پشتیبان‌گیری

### پشتیبان‌گیری خودکار
```bash
# پشتیبان‌گیری روزانه از پایگاه داده
pg_dump -U syntel_user -h localhost syntel_db > backup_$(date +%Y%m%d_%H%M%S).sql

# پشتیبان‌گیری از طریق Django management command
python manage.py backup_database
```

### بازیابی
```bash
psql -U syntel_user -h localhost syntel_db < backup_file.sql
```

## مانیتورینگ

### معیارهای مانیتورینگ
- استفاده از CPU و حافظه
- اتصالات فعال پایگاه داده
- عملکرد کوئری‌های SQL
- تعداد درخواست‌های روزانه
- نرخ تأیید و تکمیل سفارش‌ها

### ابزارهای مانیتورینگ
- داشبورد مانیتورینگ داخلی سیستم
- لاگ‌های Django
- مانیتورینگ PostgreSQL
- هشدارهای خودکار

## توسعه و مشارکت

### راه‌اندازی محیط توسعه
```bash
# کلون کردن پروژه
git clone <repository-url>
cd Syntel

# ایجاد branch توسعه
git checkout -b feature/new-feature

# نصب وابستگی‌ها
pip install -r requirements.txt
```

### اجرای تست‌ها
```bash
# اجرای تمام تست‌ها
python manage.py test

# اجرای تست‌های خاص
python manage.py test purchases.tests.TestPurchaseRequest

# بررسی پوشش کد
coverage run manage.py test
coverage report
```

### استایل کد
```bash
# بررسی PEP 8
flake8 purchases/

# مرتب‌سازی importها
isort purchases/

# فرمت کد
black purchases/
```

## استقرار (Deployment)

### تنظیمات تولید
1. **وب سرور**: Gunicorn + Nginx
2. **پایگاه داده**: PostgreSQL با تنظیمات بهینه
3. **فایل‌های استاتیک**: CDN یا Nginx serving
4. **SSL**: گواهی SSL معتبر
5. **مانیتورینگ**: ابزارهای مانیتورینگ سرور

### نمونه تنظیمات Nginx
```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL configuration
    ssl_certificate /path/to/ssl/cert.pem;
    ssl_certificate_key /path/to/ssl/private.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /path/to/static/files/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    location /media/ {
        alias /app/data/uploads/;   # آپلودها زیر DATA_PATH/uploads (پیش‌فرض /app/data)
        expires 1M;
        add_header Cache-Control "public";
    }
}
```

## پشتیبانی

### کانال‌های پشتیبانی
- **مستندات**: فایل‌های README و راهنماها
- **Issues**: گزارش مشکلات در GitHub
- **ایمیل**: support@syntel.com
- **تلفن**: 021-12345678

### اطلاعات نسخه
- **نسخه فعلی**: 1.0.0
- **تاریخ انتشار**: November 2025
- **Django compatibility**: 5.2.x
- **Python compatibility**: 3.8+

## مجوز

این پروژه تحت مجوز MIT منتشر شده است. برای اطلاعات بیشتر فایل LICENSE را مشاهده کنید.

## مشارکت‌کنندگان

- تیم توسعه Syntel
- مشارکت‌کنندگان open source

---

**Syntel** - راهکاری هوشمند برای مدیریت خرید و مالی سازمان‌ها 🚀