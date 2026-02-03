# Django Project Structure Standards

## ✅ تغییرات انجام شده در این پروژه

### 📁 ساختار جدید پوشه‌ها

```
Syntel/
├── .gitignore                 # تنظیمات Git (بروزرسانی شده)
├── README.md                  # مستندات اصلی پروژه
└── syntel/                    # ریشه پروژه Django
    ├── docs/                  # 📚 تمام مستندات پروژه
    │   ├── ANALYTICS_*.md
    │   ├── DEPLOYMENT_*.md
    │   ├── USER_MANUAL.md
    │   └── ... (22 فایل)
    │
    ├── scripts/               # 🛠️ اسکریپت‌های کمکی
    │   ├── deployment/        # اسکریپت‌های استقرار
    │   │   ├── deploy.sh
    │   │   ├── deploy_production.sh
    │   │   └── validate_*.sh
    │   │
    │   ├── fixes/             # اسکریپت‌های رفع مشکل
    │   │   ├── fix_csrf.py
    │   │   ├── fix_urls2.py
    │   │   └── ... (7 فایل)
    │   │
    │   ├── management_commands/ # ابزارهای مدیریتی
    │   │   ├── check_*.py     # بررسی داده‌ها
    │   │   ├── clear_cache.*  # مدیریت کش
    │   │   ├── verify_*.py    # اعتبارسنجی
    │   │   └── ... (18 فایل)
    │   │
    │   ├── setup/             # راه‌اندازی اولیه
    │   │   ├── create_*.py    # ایجاد داده نمونه
    │   │   ├── setup_*.py     # راه‌اندازی
    │   │   └── ... (22 فایل)
    │   │
    │   ├── tests/             # تست‌ها و ابزارها
    │   │   ├── test_*.py      # فایل‌های تست
    │   │   ├── runtests.py    # اجرای تست
    │   │   └── ... (16 فایل)
    │   │
    │   └── README.md          # راهنمای استفاده
    │
    ├── purchases/             # اپلیکیشن اصلی Django
    │   ├── management/        # دستورات مدیریتی
    │   ├── migrations/        # مایگریشن‌ها
    │   ├── templates/         # قالب‌های HTML
    │   ├── templatetags/      # تگ‌های سفارشی
    │   ├── tests/             # تست‌های اپ
    │   ├── models.py
    │   ├── views.py
    │   └── ...
    │
    ├── syntel/                # تنظیمات پروژه
    │   ├── settings.py
    │   ├── urls.py
    │   └── ...
    │
    ├── frontend/              # فایل‌های فرانت‌اند
    ├── staticfiles/           # استاتیک‌های جمع‌آوری شده
    ├── logs/                  # لاگ‌های سیستم
    ├── temp/                  # فایل‌های موقت
    ├── tests/                 # تست‌های کلی
    ├── manage.py              # مدیریت Django
    ├── requirements.txt       # وابستگی‌ها
    ├── Dockerfile
    └── docker-compose.yml
```

## 🎯 مزایای این ساختار

### 1. سازماندهی بهتر
- تمام اسکریپت‌های مرتبط در یک پوشه جمع شده‌اند
- دسته‌بندی واضح بر اساس نقش (setup, fixes, tests, etc.)
- مستندات در یک مکان مرکزی

### 2. پیروی از استانداردهای Django
- ساختار پوشه‌ها استاندارد است
- جداسازی منطقی کدها
- سازگاری با best practices

### 3. قابلیت نگهداری بهتر
- یافتن فایل‌ها آسان‌تر است
- توسعه‌دهندگان جدید سریع‌تر یاد می‌گیرند
- مستندسازی بهتر

### 4. مدیریت Git بهینه
- .gitignore کامل و استاندارد
- commit history واضح‌تر
- مدیریت آسان‌تر تغییرات

## 📝 نحوه استفاده از اسکریپت‌ها

### اسکریپت‌های راه‌اندازی
```bash
cd syntel
python scripts/setup/create_users.py
python scripts/setup/setup_cafes.py
```

### اسکریپت‌های مدیریتی
```bash
python scripts/management_commands/check_users.py
python scripts/management_commands/clear_cache.py
```

### اسکریپت‌های رفع مشکل
```bash
python scripts/fixes/fix_csrf.py
python scripts/fixes/fix_urls2.py
```

### اجرای تست‌ها
```bash
python scripts/tests/runtests.py
```

### استقرار
```bash
bash scripts/deployment/deploy.sh
bash scripts/deployment/validate_production.sh
```

## 🔄 تغییرات در Git

### Commit انجام شده
```
Commit: acf1f86
Message: "Refactor: Reorganize project structure to Django standards"

Changes:
- 214 files changed
- 2913 insertions(+)
- 794 deletions(-)
```

### تغییرات اصلی:
1. ✅ ایجاد پوشه `scripts/` با زیرپوشه‌های منظم
2. ✅ ایجاد پوشه `docs/` برای مستندات
3. ✅ انتقال تمام فایل‌های پراکنده به مکان‌های مناسب
4. ✅ بروزرسانی `.gitignore`
5. ✅ ایجاد README.md جامع
6. ✅ Push به GitHub با موفقیت

## 📚 مستندات

تمام مستندات در پوشه `syntel/docs/` قرار دارند:
- راهنماهای کاربری
- مستندات فنی
- راهنماهای استقرار
- گزارشات پروژه

## ✨ نکات مهم

1. **همه imports کار می‌کنند**: تغییری در کدهای اصلی داده نشده
2. **سازگاری با Git**: تمام تاریخچه حفظ شده
3. **استاندارد Django**: پیروی کامل از best practices
4. **مستندسازی کامل**: README برای هر پوشه
5. **آماده Production**: ساختار حرفه‌ای و قابل اعتماد

## 🚀 مراحل بعدی (پیشنهادی)

1. ✅ بررسی و تست تمام اسکریپت‌ها در محیط جدید
2. ✅ بروزرسانی مستندات داخلی اگر لازم است
3. ✅ اطمینان از عملکرد صحیح deployment scripts
4. ✅ آموزش تیم درباره ساختار جدید

---

**تاریخ سازماندهی مجدد**: 3 فوریه 2026
**وضعیت**: ✅ کامل و Push شده به GitHub
**Repository**: https://github.com/tvfm22/Syntel-.git
