# Changelog

تمام تغییرات مهم در این پروژه در این فایل مستند می‌شود.

## [1.0.0] - 2025-12-14

### ✨ Added
- سیستم دسته‌بندی سلسله‌مراتبی محصولات
- Autocomplete با Select2 برای جستجوی محصولات و دسته‌بندی‌ها
- API endpoints برای autocomplete
- داشبورد تغییرات قیمت محصولات
- سیستم مدیریت کاربران با نقش‌های مختلف
- مدیریت درخواست‌های خرید
- سیستم فاکتور و پرداخت
- گزارش‌گیری و خروجی Excel/PDF
- سیستم اعلان‌ها
- Docker support کامل
- مستندات جامع deployment

### 🔧 Changed
- بهبود UI/UX فرم ثبت فاکتور
- بهینه‌سازی queryهای دیتابیس
- افزودن RTL support کامل

### 🐛 Fixed
- رفع مشکل migration در SQLite
- اصلاح خطای فیلد symbol در Unit model
- رفع مشکل jQuery برای Select2

### 🔐 Security
- افزودن CSRF protection
- محافظت در برابر SQL Injection
- محافظت در برابر XSS
- Rate limiting

### 📦 Dependencies
- Django 5.0.2
- Django REST Framework 3.15.1
- PostgreSQL 15
- Redis 7
- Select2 4.1.0
- Bootstrap 5.3.0

## [0.9.0] - قبل از 2025-12-14

### ✨ Added
- راه‌اندازی اولیه پروژه
- مدل‌های پایه دیتابیس
- سیستم احراز هویت
- پنل مدیریت Django

---

فرمت بر اساس [Keep a Changelog](https://keepachangelog.com/fa/1.0.0/)
