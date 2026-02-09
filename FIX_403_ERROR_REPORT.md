# گزارش رفع خطای 403 و "نقش تایید نشد"

## خلاصه مشکل
همه کاربران هنگام ورود و navigateکردن بین صفحات، خطای **403 Forbidden** و **"نقش تایید نشد"** را دریافت می‌کردند.

## علت اصلی مشکل
مشکل از دو منبع ناشی می‌شد:

### 1. کاربران عادی بدون کافه
تعدادی کاربر با نقش‌های عادی (kitchen, bar, counter, cashier, facilities, purchasing) در دیتابیس `cafe=None` داشتند. این کاربران شامل:
- kitchen_user
- bar_user  
- counter_user
- cashier_user
- facilities_user
- purchasing_user

### 2. رفتار نادرست Middleware
`CafeMiddleware` هنگام مواجهه با کاربران عادی بدون کافه، خطای `Http404` را raise می‌کرد که توسط Django به خطای 403 Forbidden تبدیل می‌شد.

## تغییرات اعمال شده

### 1. اصلاح Middleware (`purchases/middleware.py`)

**قبل از تغییر:**
```python
if not request.user.cafe:
    # کاربر عادی بدون کافه - خطا
    raise Http404("کافه برای کاربر تعریف نشده است...")
```

**بعد از تغییر:**
```python
if not request.user.cafe:
    # کاربر عادی بدون کافه - logout و هدایت به لاگین
    from django.contrib.auth import logout
    logout(request)
    messages.error(request, "کافه برای کاربر شما تعریف نشده است...")
    return redirect('login')
```

**دلیل تغییر:** به جای raise کردن exception که به 403 تبدیل می‌شود، کاربر را logout می‌کنیم و با پیام واضح به صفحه login هدایت می‌کنیم.

### 2. اصلاح Login View (`purchases/views.py`)

**قبل از تغییر:**
```python
if user and user.is_active:
    login(request, user)  # لاگین قبل از چک کردن کافه
    
    if user.needs_cafe_selection:
        return redirect("select_cafe")
    
    if not user.cafe:
        logout(request)  # بعداً logout می‌کنیم
        return render(...)
```

**بعد از تغییر:**
```python
if user and user.is_active:
    # کاربران ادمین
    if user.needs_cafe_selection:
        login(request, user)
        return redirect("select_cafe")
    
    # کاربران عادی - چک کافه قبل از لاگین
    if not user.cafe:
        return render(...)  # بدون لاگین، فقط نمایش خطا
    
    # لاگین فقط اگر همه چیز OK باشه
    login(request, user)
```

**دلیل تغییر:** کاربران فقط زمانی login می‌شوند که همه شرایط (داشتن کافه یا admin بودن) برقرار باشد. این از ایجاد session ناقص جلوگیری می‌کند.

### 3. اضافه کردن Import مفقود شده

```python
from django.urls import reverse
```

این import برای استفاده از تابع `reverse()` در برخی viewها لازم بود و مفقود شده بود.

### 4. اختصاص کافه به کاربران

6 کاربر عادی که `cafe=None` داشتند، به کافه لوکال اختصاص داده شدند:

```python
# اجرا شد:
users_without_cafe = User.objects.filter(cafe__isnull=True).exclude(
    role__in=['manager', 'ceo', 'finance_manager', 'accounting']
)
for user in users_without_cafe:
    user.cafe = local_cafe
    user.save()
```

## نتیجه

✅ **همه کاربران عادی اکنون کافه دارند**  
✅ **Middleware به درستی کاربران بدون کافه را handle می‌کند**  
✅ **Login view قبل از لاگین کردن، کافه را چک می‌کند**  
✅ **خطاهای 403 و "نقش تایید نشد" برطرف شده‌اند**

## فایل‌های تغییر یافته

1. [purchases/middleware.py](syntel/purchases/middleware.py) - اصلاح منطق رفتار با کاربران بدون کافه
2. [purchases/views.py](syntel/purchases/views.py) - اصلاح login view و اضافه کردن import
3. داده‌های دیتابیس - اختصاص کافه به 6 کاربر

## تست و بررسی

برای تست می‌توانید:

```bash
# بررسی تنظیمات کاربران
python test_403_fix.py

# یا manual test:
# 1. به http://localhost:8000/login بروید
# 2. با هر کدام از کاربران زیر لاگین کنید:
#    - kitchen_user / password123
#    - bar_user / password123
#    - purchasing / password123
# 3. بین صفحات navigate کنید
# 4. هیچ خطای 403 نباید ببینید
```

## نکات مهم برای آینده

1. **هنگام ایجاد کاربر جدید:** حتماً `cafe` را set کنید (برای کاربران عادی)
2. **کاربران ادمین:** می‌توانند `cafe=None` داشته باشند (طبیعی است)
3. **اگر کاربری کافه نداشته باشد:** به صفحه login با پیام خطا هدایت می‌شود

## ابزارهای کمکی ایجاد شده

1. **fix_users_without_cafe.py** - اسکریپت اختصاص کافه به کاربران بدون کافه
2. **test_403_fix.py** - اسکریپت تست و بررسی تنظیمات کاربران

---
تاریخ: 8 فوریه 2026  
وضعیت: ✅ حل شد
