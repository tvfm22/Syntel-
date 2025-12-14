# مستندات فنی سیستم Syntel

## نمای کلی معماری

سیستم Syntel یک برنامه وب Django است که برای مدیریت فرآیند خرید و پرداخت سازمان‌ها طراحی شده است.

### تکنولوژی‌ها
- **Backend**: Django 5.2.8
- **Database**: PostgreSQL
- **Frontend**: HTML5, CSS3, Bootstrap 4, JavaScript
- **Charts**: Chart.js
- **PDF Generation**: ReportLab
- **Excel Export**: openpyxl

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
│   └── admin.py          # تنظیمات ادمین
├── frontend/              # قالب‌ها و فایل‌های استاتیک
│   └── templates/        # قالب‌های HTML
└── static/               # فایل‌های استاتیک
```

## مدل‌های داده

### مدل‌های اصلی

#### User (کاربر)
```python
class User(AbstractUser):
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    # نقش‌های: kitchen, bar, counter, purchasing, manager
```

#### PurchaseRequest (درخواست خرید)
```python
class PurchaseRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
```

#### PurchaseItem (اقلام خرید)
```python
class PurchaseItem(models.Model):
    request = models.ForeignKey(PurchaseRequest, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    purchased_quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True)
```

#### Product (محصول)
```python
class Product(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE)
    min_stock = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    is_active = models.BooleanField(default=True)
```

#### Supplier (تأمین‌کننده)
```python
class Supplier(models.Model):
    name = models.CharField(max_length=100, unique=True)
    contact_person = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    rating = models.DecimalField(max_digits=3, decimal_places=1, null=True)
```

#### Invoice (فاکتور)
```python
class Invoice(models.Model):
    order = models.OneToOneField(PurchaseOrder, on_delete=models.CASCADE)
    invoice_number = models.CharField(max_length=50, unique=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField()
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES)
```

#### Payment (پرداخت)
```python
class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    payment_location = models.ForeignKey(PaymentLocation, on_delete=models.CASCADE)
    reference_number = models.CharField(max_length=100, blank=True)
```

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
- `POST /purchase-orders/<id>/send/` - ارسال سفارش

### Invoice Management
- `GET /invoices/` - لیست فاکتورها
- `POST /invoices/create/<order_id>/` - ایجاد فاکتور
- `POST /invoices/<id>/approve/` - تأیید فاکتور

### Payment Management
- `GET /payments/` - لیست پرداخت‌ها
- `POST /payments/create/` - ایجاد پرداخت

### Backup & Monitoring
- `GET /backup/` - صفحه پشتیبان‌گیری
- `POST /backup/database/` - پشتیبان‌گیری از پایگاه داده
- `GET /system/monitoring/` - داشبورد مانیتورینگ
- `GET /system/monitoring/api/` - API مانیتورینگ

## تنظیمات پایگاه داده

### PostgreSQL Configuration
```sql
-- ایجاد پایگاه داده
CREATE DATABASE syntel_db;

-- ایجاد کاربر
CREATE USER syntel_user WITH PASSWORD 'your_password';

-- اعطای دسترسی‌ها
GRANT ALL PRIVILEGES ON DATABASE syntel_db TO syntel_user;

-- فعال کردن extensionها
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

### Django Settings
```python
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

## نصب و راه‌اندازی

### پیش‌نیازها
- Python 3.8+
- PostgreSQL 12+
- pip
- virtualenv

### مراحل نصب
```bash
# کلون کردن پروژه
git clone <repository-url>
cd syntel

# ایجاد محیط مجازی
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# نصب وابستگی‌ها
pip install -r requirements.txt

# تنظیمات پایگاه داده
createdb syntel_db
python manage.py migrate

# ایجاد کاربر مدیر
python manage.py createsuperuser

# اجرای سرور
python manage.py runserver
```

## پشتیبان‌گیری و بازیابی

### پشتیبان‌گیری خودکار
```bash
# پشتیبان‌گیری روزانه از پایگاه داده
pg_dump -U syntel_user -h localhost syntel_db > backup_$(date +%Y%m%d_%H%M%S).sql

# پشتیبان‌گیری از فایل‌های رسانه
tar -czf media_backup_$(date +%Y%m%d).tar.gz media/
```

### بازیابی
```bash
# بازیابی پایگاه داده
psql -U syntel_user -h localhost syntel_db < backup_file.sql

# بازیابی فایل‌های رسانه
tar -xzf media_backup.tar.gz
```

## مانیتورینگ و نگهداری

### لاگ‌ها
- Django logs: `logs/django.log`
- PostgreSQL logs: `/var/log/postgresql/`
- Application logs: تنظیم شده در `settings.py`

### معیارهای کلیدی
- Response time < 2 seconds
- CPU usage < 80%
- Memory usage < 85%
- Database connections < 100

### نگهداری منظم
```bash
# پاک‌سازی sessionهای منقضی
python manage.py clearsessions

# بهینه‌سازی پایگاه داده
python manage.py dbshell
VACUUM ANALYZE;

# پشتیبان‌گیری
python manage.py backup_database
```

## امنیت

### تنظیمات امنیتی
```python
# settings.py
SECRET_KEY = 'your-secret-key-here'
DEBUG = False
ALLOWED_HOSTS = ['your-domain.com']

# HTTPS settings
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Session security
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

### دسترسی‌ها
- Role-based access control (RBAC)
- Object-level permissions
- Audit logging برای تغییرات حساس

## عیب‌یابی

### مشکلات رایج

#### Database Connection Error
```
Error: connection to server failed
```
**راه‌حل:**
- بررسی وضعیت PostgreSQL service
- تأیید تنظیمات DATABASES در settings.py
- بررسی firewall و network connectivity

#### Static Files Not Loading
```
404 error for static files
```
**راه‌حل:**
- اجرای `python manage.py collectstatic`
- بررسی تنظیمات STATIC_URL و STATIC_ROOT
- تأیید دسترسی‌های فایل سیستم

#### Memory Issues
```
Out of memory error
```
**راه‌حل:**
- افزایش RAM سرور
- بهینه‌سازی کوئری‌های Django
- پیاده‌سازی caching

### لاگ‌های خطا
```python
# settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'ERROR',
            'class': 'logging.FileHandler',
            'filename': 'logs/django.log',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'ERROR',
            'propagate': True,
        },
    },
}
```

## بهینه‌سازی عملکرد

### Database Optimization
```sql
-- ایجاد ایندکس‌ها
CREATE INDEX idx_purchase_request_status ON purchases_purchaserequest(status);
CREATE INDEX idx_purchase_request_created_at ON purchases_purchaserequest(created_at);
CREATE INDEX idx_payment_date ON purchases_payment(payment_date);

-- کوئری‌های بهینه
SELECT * FROM purchases_purchaserequest
WHERE created_at >= %s
ORDER BY created_at DESC
LIMIT 10;
```

### Caching
```python
# settings.py
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/',
    }
}

# استفاده در views
from django.core.cache import cache

def dashboard(request):
    data = cache.get('dashboard_data')
    if not data:
        data = expensive_calculation()
        cache.set('dashboard_data', data, 300)  # 5 minutes
    return render(request, 'dashboard.html', data)
```

### CDN و Static Files
- استفاده از CloudFront یا CDN مشابه
- Gzip compression فعال
- Minification فایل‌های static

## استقرار (Deployment)

### Production Setup
```bash
# نصب وابستگی‌ها
sudo apt update
sudo apt install postgresql nginx redis-server

# تنظیمات Gunicorn
pip install gunicorn
gunicorn syntel.wsgi:application --bind 0.0.0.0:8000

# تنظیمات Nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /static/ {
        alias /path/to/static/files/;
    }
}
```

### Docker Deployment
```dockerfile
FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput

CMD ["gunicorn", "syntel.wsgi:application", "--bind", "0.0.0.0:8000"]
```

## پشتیبانی و نگهداری

### پشتیبان‌گیری منظم
- روزانه: پشتیبان‌گیری کامل
- هفتگی: پشتیبان‌گیری incremental
- ماهانه: آرشیو کامل

### مانیتورینگ مداوم
- استفاده از ابزارهای مانیتورینگ (Nagios, Zabbix)
- تنظیم alertها برای معیارهای حیاتی
- گزارش‌گیری منظم از عملکرد

### به‌روزرسانی‌ها
- تست به‌روزرسانی‌ها در محیط staging
- پشتیبان‌گیری قبل از deployment
- rollback plan برای مشکلات

---

*این مستندات برای توسعه‌دهندگان و مدیران سیستم Syntel تهیه شده است.*