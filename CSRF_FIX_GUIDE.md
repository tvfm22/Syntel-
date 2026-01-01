# 🔧 راهنمای رفع خطای CSRF 403

## ❌ خطا
```
ممنوع (403)
CSRF تأیید نشد. درخواست لغو شد.
CSRF token from POST incorrect.
```

## ✅ راه‌حل‌های انجام شده

### 1. تنظیمات CSRF در settings.py اضافه شد
```python
# CSRF Settings
CSRF_COOKIE_SECURE = False  # در production با HTTPS باید True باشد
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = [
    'http://localhost',
    'http://127.0.0.1',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]
```

### 2. ALLOWED_HOSTS بروزرسانی شد
```python
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '*']
```

## 🔍 بررسی‌های اضافی

### 1. پاک کردن Cache مرورگر
```
- Ctrl + Shift + Delete
- پاک کردن کامل کوکی‌ها و Cache
- بستن و باز کردن مرورگر
```

### 2. بررسی کوکی‌ها در مرورگر
```
1. F12 (Developer Tools)
2. Application/Storage -> Cookies
3. بررسی وجود: csrftoken
4. اگر نبود، Refresh کنید (F5)
```

### 3. تست با مرورگر Incognito
```
- مرورگر را در حالت Incognito/Private باز کنید
- دوباره لاگین کنید
```

### 4. ریستارت سرور Django
```bash
# خارج شوید از سرور (Ctrl+C)
# دوباره اجرا کنید:
python manage.py runserver
```

### 5. پاک کردن Session فایل‌ها
```bash
# پاک کردن session files
python manage.py clearsessions
```

## 🌐 برای محیط Production با Nginx

اگر از Nginx استفاده می‌کنید، این تنظیمات را اضافه کنید:

### settings.py
```python
# در production
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = [
    'https://your-domain.com',
    'https://www.your-domain.com',
]
```

### nginx.conf
```nginx
# اطمینان از ارسال صحیح headers
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

## 🧪 تست CSRF

اسکریپت تست:
```bash
python test_csrf.py
```

باید این خروجی را ببینید:
```
✅ صفحه لاگین با موفقیت بارگیری شد
✅ CSRF Cookie موجود است
✅ CSRF token در فرم موجود است
```

## 🐛 عیب‌یابی پیشرفته

### بررسی CSRF در HTML
```html
<!-- باید در فرم login.html موجود باشد -->
<form method="post">
    {% csrf_token %}
    <!-- ... -->
</form>
```

### بررسی Middleware
```python
# در settings.py باید موجود باشد:
MIDDLEWARE = [
    # ...
    'django.middleware.csrf.CsrfViewMiddleware',
    # ...
]
```

### بررسی Context Processor
```python
# در TEMPLATES settings باید موجود باشد:
'context_processors': [
    'django.template.context_processors.request',
    # ...
],
```

## 📝 لاگ‌های مفید

### فعال‌سازی لاگ CSRF در Django
```python
# در settings.py اضافه کنید:
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django.security.csrf': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}
```

## ✨ راه‌حل نهایی

اگر همه موارد بالا را انجام دادید و هنوز مشکل دارید:

### 1. ریستارت کامل
```bash
# 1. خروج از سرور
Ctrl + C

# 2. پاک کردن __pycache__
python -c "import pathlib, shutil; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]"

# 3. مایگریت
python manage.py migrate

# 4. اجرای سرور
python manage.py runserver
```

### 2. تست با curl
```bash
# دریافت CSRF token
curl -c cookies.txt http://localhost:8000/login/

# لاگین با token
curl -b cookies.txt -c cookies.txt -X POST http://localhost:8000/login/ \
  -d "username=admin&password=admin&csrfmiddlewaretoken=TOKEN"
```

### 3. حالت DEBUG
```python
# در settings.py
DEBUG = True  # اطمینان حاصل کنید True است
```

## 📞 پشتیبانی

اگر مشکل همچنان ادامه دارد:
1. خروجی کامل خطا را ذخیره کنید
2. نسخه Django را بررسی کنید: `python -m django --version`
3. تنظیمات MIDDLEWARE را بررسی کنید

---
**تاریخ بروزرسانی**: 2026/01/01  
**نسخه**: 1.0
