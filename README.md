# 🏢 Syntel - سیستم مدیریت خرید و انبار

[![Django](https://img.shields.io/badge/Django-5.0.2-green.svg)](https://www.djangoproject.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-blue.svg)](https://postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

سیستم جامع مدیریت خرید، انبار، فاکتور و مالی با رابط کاربری فارسی - آماده برای استقرار روی سرور

## � ساختار پروژه

```
Syntel/
├── syntel/                    # Django Project Root
│   ├── docs/                  # مستندات کامل پروژه
│   ├── frontend/              # فایل‌های استاتیک فرانت‌اند
│   ├── logs/                  # لاگ‌های سیستم
│   ├── purchases/             # اپلیکیشن اصلی
│   │   ├── management/        # دستورات مدیریتی Django
│   │   ├── migrations/        # مایگریشن‌های دیتابیس
│   │   ├── templates/         # قالب‌های HTML
│   │   ├── templatetags/      # تگ‌های سفارشی
│   │   └── tests/             # تست‌های اپلیکیشن
│   ├── scripts/               # اسکریپت‌های کمکی
│   │   ├── deployment/        # اسکریپت‌های استقرار
│   │   ├── fixes/             # اسکریپت‌های رفع مشکل
│   │   ├── management_commands/ # ابزارهای مدیریتی
│   │   ├── setup/             # اسکریپت‌های راه‌اندازی اولیه
│   │   └── tests/             # ابزارهای تست
│   ├── staticfiles/           # فایل‌های استاتیک جمع‌آوری شده
│   ├── syntel/                # تنظیمات Django
│   ├── temp/                  # فایل‌های موقت
│   ├── tests/                 # تست‌های کل پروژه
│   ├── manage.py              # اسکریپت مدیریت Django
│   ├── requirements.txt       # وابستگی‌های Python
│   ├── Dockerfile             # تنظیمات Docker
│   └── docker-compose.yml     # تنظیمات Docker Compose
└── README.md                  # این فایل
```

## 🚀 نصب سریع

### با Docker (توصیه شده برای Production)

```bash
# کلون پروژه
git clone https://github.com/tvfm22/Syntel-.git
cd Syntel-/syntel

# تنظیمات محیطی
cp .env.example .env
nano .env  # ویرایش و تکمیل متغیرها

# استقرار خودکار
chmod +x scripts/deployment/deploy.sh
bash scripts/deployment/deploy.sh
```

سایت روی http://localhost در دسترس خواهد بود.

### نصب دستی (Development)

```bash
cd syntel
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

تمام مستندات پروژه در پوشه `syntel/docs/` قرار دارد:

- [راهنمای کاربر](syntel/docs/USER_MANUAL.md) - راهنمای استفاده برای کاربران
- [راهنمای مدیر](syntel/docs/MANAGER_MANUAL.md) - راهنمای مدیریت سیستم
- [مستندات فنی](syntel/docs/TECHNICAL_DOCUMENTATION.md) - مستندات توسعه‌دهنده
- [راهنمای استقرار](syntel/docs/DEPLOYMENT_GUIDE.md) - نحوه استقرار روی سرور
- [راهنمای عیب‌یابی](syntel/docs/TROUBLESHOOTING_GUIDE.md) - رفع مشکلات رایج
- [راهنمای سریع](syntel/docs/QUICK_START_GUIDE.md) - شروع سریع

## 🛠️ اسکریپت‌های کمکی

اسکریپت‌های کمکی در پوشه `syntel/scripts/` سازماندهی شده‌اند:

### راه‌اندازی اولیه (`scripts/setup/`)
```bash
python scripts/setup/create_users.py          # ایجاد کاربران نمونه
python scripts/setup/setup_cafes.py           # راه‌اندازی کافه‌ها
```

### مدیریت (`scripts/management_commands/`)
```bash
python scripts/management_commands/check_users.py      # بررسی کاربران
python scripts/management_commands/clear_cache.py      # پاک کردن کش
```

### رفع مشکل (`scripts/fixes/`)
```bash
python scripts/fixes/fix_csrf.py              # رفع مشکل CSRF
python scripts/fixes/fix_urls2.py             # تصحیح URLها
```

### استقرار (`scripts/deployment/`)
```bash
bash scripts/deployment/deploy.sh             # استقرار روی سرور
bash scripts/deployment/validate_production.sh # اعتبارسنجی Production
```
- بازیابی سیستم در صورت مشکل

### 📈 مانیتورینگ سیستم
- نظارت بر منابع سیستم (CPU، حافظه، دیسک)
- مانیتورینگ پایگاه داده
- هشدارهای خودکار سیستم
- داشبورد عملکرد real-time

## پیش‌نیازها

- Python 3.8 یا بالاتر
- PostgreSQL 12 یا بالاتر
- pip (مدیریت بسته Python)
- Git

## نصب و راه‌اندازی

### 1. کلون کردن پروژه
```bash
git clone <repository-url>
cd syntel
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

### 4. تنظیمات پایگاه داده
```bash
# ایجاد پایگاه داده PostgreSQL
createdb syntel_db

# یا از طریق SQL
psql -U postgres
CREATE DATABASE syntel_db;
CREATE USER syntel_user WITH PASSWORD 'password123';
GRANT ALL PRIVILEGES ON DATABASE syntel_db TO syntel_user;
\q
```

### 5. تنظیمات Django
فایل `syntel/settings.py` را ویرایش کنید و تنظیمات پایگاه داده را وارد کنید:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'syntel_db',
        'USER': 'syntel_user',
        'PASSWORD': 'password123',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### 6. اجرای Migrationها
```bash
python manage.py migrate
```

### 7. ایجاد کاربر مدیر
```bash
python manage.py createsuperuser
```

### 8. اجرای سرور توسعه
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

## ساختار پروژه

```
syntel/
├── syntel/                 # تنظیمات اصلی Django
│   ├── settings.py        # تنظیمات Django
│   ├── urls.py           # URLهای اصلی
│   ├── wsgi.py           # تنظیمات WSGI
│   └── asgi.py           # تنظیمات ASGI
├── purchases/             # اپلیکیشن اصلی
│   ├── models.py         # مدل‌های داده
│   ├── views.py          # منطق برنامه
│   ├── urls.py           # URLهای اپلیکیشن
│   ├── forms.py          # فرم‌های Django
│   ├── admin.py          # تنظیمات ادمین Django
│   └── migrations/       # Migrationهای پایگاه داده
├── frontend/              # قالب‌ها و فایل‌های استاتیک
│   └── templates/        # قالب‌های HTML
├── static/               # فایل‌های استاتیک (CSS, JS, images)
├── media/                # فایل‌های آپلود شده توسط کاربران
└── docs/                 # مستندات پروژه
```

## مستندات

### راهنمای کاربری
- [راهنمای کامل کاربر](USER_MANUAL.md) - راهنمای استفاده از سیستم
- [راهنمای مدیر سیستم](MANAGER_MANUAL.md) - راهنمای مخصوص مدیران
- [راهنمای شروع سریع](QUICK_START_GUIDE.md) - راه‌اندازی سریع سیستم
- [راهنمای عیب‌یابی](TROUBLESHOOTING_GUIDE.md) - حل مشکلات رایج

### مستندات فنی
- [مستندات فنی](TECHNICAL_DOCUMENTATION.md) - معماری، API، و تنظیمات فنی
- [راهنمای مانیتورینگ سیستم](SYSTEM_MONITORING_README.md) - مستندات مانیتورینگ

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
cd syntel

# ایجاد branch توسعه
git checkout -b feature/new-feature

# نصب وابستگی‌های توسعه
pip install -r requirements-dev.txt
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
        alias /path/to/media/files/;
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