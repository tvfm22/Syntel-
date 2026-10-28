# راه حل مشکل Analytics - داده‌ها برنگشتند

## 🔍 مشکل واقعی
بعد از بررسی دقیق، مشکل یافت شد:

### ✅ API کار می‌کند!
```
Status: 200
Departments: 4
  - انبار: 313,673,697 Toman (Growth: 25.62%)
```

### ❌ اما داده‌ها فقط برای کافه `local` هستند!

## 📊 توزیع داده‌ها

### کافه‌های موجود:
1. **local** (کافه لوکال)
   - ✅ 329 Invoice
   - ✅ 85 Invoice در ماه اخیر
   - ✅ 4 Requester (انبار، آشپزخانه، بار، تاسیسات)
   - ✅ 313+ میلیون تومان داده

2. **baba_adam** (کافه رستوران بابا آدم)
   - ❌ 0 Invoice
   - ❌ 0 Requester
   - ❌ هیچ داده‌ای وجود ندارد!

## 🎯 راه حل

### گزینه 1: استفاده از URL صحیح
مطمئن شوید که از URL صحیح استفاده می‌کنید:

```
❌ اشتباه: http://localhost:8000/baba_adam/analytics/
✅ صحیح:   http://localhost:8000/local/analytics/
```

### گزینه 2: تغییر کافه فعال
1. به صفحه انتخاب کافه بروید: `http://localhost:8000/select-cafe/`
2. کافه `local` را انتخاب کنید
3. سپس به analytics بروید

### گزینه 3: ایجاد داده برای کافه baba_adam
اگر می‌خواهید داده برای `baba_adam` داشته باشید:

```bash
cd d:\Syntel\syntel
# اجرای اسکریپت ایجاد داده برای baba_adam
python scripts/setup/setup_baba_adam_data.py
```

## 🧪 تست و بررسی

### بررسی کافه‌ها و داده‌ها:
```bash
python scripts/management_commands/check_cafe_data.py
```

### تست API:
```bash
python scripts/tests/test_local_analytics.py
```

### بررسی داده‌های analytics:
```bash
python scripts/management_commands/check_analytics_data.py
```

## 📝 خلاصه

**مشکل**: داده‌ها برای کافه `baba_adam` وجود ندارد، فقط برای `local` هست.

**راه حل سریع**: 
```
http://localhost:8000/local/analytics/
```

**نتیجه تست**:
- ✅ JavaScript کار می‌کند
- ✅ API کار می‌کند  
- ✅ داده‌ها در دیتابیس هستند (برای local)
- ✅ 313 میلیون تومان داده برای بخش انبار

## 🚀 مراحل نهایی

1. مرورگر را باز کنید
2. به `http://localhost:8000/` بروید
3. لاگین کنید
4. به `http://localhost:8000/local/analytics/` بروید
5. داده‌ها باید نمایش داده شوند! 🎉

تاریخ: 3 فوریه 2026
