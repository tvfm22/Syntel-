# راهنمای دیپلوی در Liara

## مشکل و راه حل

مشکل: فایل‌های پروژه Django در پوشه `syntel/` قرار دارند، اما Liara از روت ریپازیتوری شروع به بیلد می‌کند.

**راه حل اعمال شده:**
- Dockerfile جدید در روت ریپازیتوری ساخته شد
- فایل `.dockerignore` برای روت ایجاد شد
- فایل `liara.json` برای پیکربندی Liara ساخته شد

## مراحل دیپلوی

### ۱. Push کردن فایل‌های جدید

```bash
git add Dockerfile .dockerignore liara.json
git commit -m "Add Liara deployment configuration"
git push origin main
```

### ۲. تنظیم متغیرهای محیطی در Liara

در پنل Liara، به بخش **تنظیمات > متغیرهای محیطی** بروید و موارد زیر را اضافه کنید:

#### الزامی:
```
DJANGO_SECRET_KEY=یک-کلید-امنیتی-حداقل-۵۰-کاراکتر
DJANGO_ALLOWED_HOSTS=your-app.liara.run
DJANGO_DEBUG=False
```

#### دیتابیس (اگر از دیتابیس PostgreSQL لیارا استفاده می‌کنید):
```
DB_NAME=نام-دیتابیس
DB_USER=یوزرنیم-دیتابیس
DB_PASSWORD=پسورد-دیتابیس
DB_HOST=آدرس-دیتابیس.liara.cloud
DB_PORT=5432
```

#### اختیاری (ایمیل):
```
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=noreply@yourdomain.com
```

### ۳. ایجاد دیسک‌ها در Liara

در پنل Liara:
1. به بخش **دیسک‌ها** بروید
2. دو دیسک زیر را ایجاد کنید:
   - نام: `media` - مسیر: `/app/media`
   - نام: `staticfiles` - مسیر: `/app/staticfiles`

### ۴. دیپلوی مجدد

پس از push کردن تغییرات، Liara به صورت خودکار دیپلوی را شروع می‌کند.

اگر خودکار شروع نشد:
- در پنل Liara، روی دکمه **استقرار مجدد** کلیک کنید

## بررسی دیپلوی

بعد از اتمام دیپلوی:

1. **بررسی لاگ‌ها:**
   - در پنل Liara به بخش **لاگ‌ها** بروید
   - مطمئن شوید خطایی وجود ندارد

2. **اجرای Migration:**
   در ترمینال Liara (Console):
   ```bash
   python manage.py migrate
   ```

3. **ساخت Superuser:**
   ```bash
   python manage.py createsuperuser
   ```

4. **بررسی سایت:**
   - به آدرس `https://your-app.liara.run` بروید
   - پنل ادمین: `https://your-app.liara.run/admin`

## عیب‌یابی

### خطای Static Files
اگر فایل‌های استاتیک لود نمی‌شوند:
```bash
python manage.py collectstatic --noinput
```

### خطای Database
مطمئن شوید:
- دیتابیس PostgreSQL در Liara ساخته شده
- متغیرهای محیطی دیتابیس درست تنظیم شده‌اند

### بررسی Health Check
```bash
curl https://your-app.liara.run/health/
```

## نکات مهم

1. **امنیت:**
   - حتماً `DJANGO_SECRET_KEY` را تغییر دهید
   - `DJANGO_DEBUG=False` تنظیم شود
   - `DJANGO_ALLOWED_HOSTS` را با دامنه خود تنظیم کنید

2. **دیتابیس:**
   - از دیتابیس PostgreSQL Liara استفاده کنید
   - SQLite برای production مناسب نیست

3. **فایل‌های رسانه:**
   - حتماً دیسک `media` را بسازید
   - برای ذخیره‌سازی بهتر می‌توانید از Object Storage استفاده کنید

4. **Backup:**
   - به صورت منظم از دیتابیس backup بگیرید

## دستورات مفید

### ورود به Console Liara:
```bash
liara shell
```

### مشاهده لاگ‌های زنده:
```bash
liara logs --follow
```

### دیپلوی از CLI:
```bash
liara deploy
```

## پشتیبانی

اگر مشکلی پیش آمد:
- لاگ‌های Liara را بررسی کنید
- با پشتیبانی Liara در تماس باشید
- مستندات Liara: https://docs.liara.ir
