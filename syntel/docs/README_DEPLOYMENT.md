# 🚀 Syntel - راهنمای سریع استقرار

سیستم مدیریت خرید و انبار Syntel

## نصب سریع با Docker

### 1. کلون پروژه
```bash
git clone https://github.com/tvfm22/Syntel-.git
cd Syntel-/syntel
```

### 2. تنظیمات محیطی
```bash
cp .env.example .env
# ویرایش .env و تکمیل متغیرها
```

### 3. اجرا
```bash
docker-compose up -d
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
```

### 4. دسترسی
- سایت: http://localhost
- ادمین: http://localhost/admin

## ویژگی‌های اصلی

✨ مدیریت کاربران با نقش‌های مختلف (آشپز، خریدار، مدیر)
📦 مدیریت محصولات و دسته‌بندی‌های سلسله‌مراتبی
🛒 ثبت و پیگیری درخواست‌های خرید
📋 صدور فاکتور و مدیریت پرداخت‌ها
📊 داشبورد آماری و گزارش‌گیری
💰 پیگیری تغییرات قیمت محصولات
🔔 سیستم اعلان‌ها
🔐 امنیت پیشرفته
📱 واسط کاربری فارسی و RTL

## تکنولوژی‌های استفاده شده

- **Backend**: Django 5.0.2, Django REST Framework
- **Database**: PostgreSQL 15
- **Cache**: Redis 7
- **Frontend**: Bootstrap 5, Select2, jQuery
- **Deployment**: Docker, Nginx, Gunicorn
- **Testing**: Pytest, Playwright

## مستندات کامل

- [راهنمای استقرار کامل](DEPLOYMENT_GUIDE.md)
- [مستندات فنی](TECHNICAL_DOCUMENTATION.md)
- [راهنمای کاربر](USER_MANUAL.md)
- [عیب‌یابی](TROUBLESHOOTING_GUIDE.md)

## نیازمندی‌های سیستم

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Docker & Docker Compose (برای deployment)

## مجوز

این پروژه تحت مجوز MIT منتشر شده است.

## پشتیبانی

برای گزارش مشکلات یا پیشنهادات، Issue جدید ایجاد کنید.
