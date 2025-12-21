# راهنمای صفحه موبایل برای کاربران دپارتمان‌ها

## تغییرات انجام شده

### ۱. صفحه Mobile Dashboard جدید

یک صفحه ساده و mobile-friendly برای کاربران دپارتمان‌ها ایجاد شد که شامل:

#### ویژگی‌های صفحه:
- ✅ دیزاین مینیمال و تمیز
- ✅ بهینه‌سازی برای موبایل (Mobile-First Design)
- ✅ دو گزینه اصلی:
  - **ثبت درخواست خرید** - لینک مستقیم به فرم درخواست خرید
  - **درخواست‌های من** - لینک به لیست درخواست‌ها
- ✅ دکمه خروج از حساب کاربری
- ✅ انیمیشن‌های نرم و جذاب
- ✅ Touch-friendly buttons
- ✅ رنگ‌بندی gradient زیبا
- ✅ Responsive design (موبایل، تبلت، دسکتاپ)

#### کاربرانی که به این صفحه منتقل می‌شوند:
- آشپزخانه (kitchen)
- بار (bar)
- کانتر (counter)
- صندوق (cashier)
- تاسیسات (facilities)

### ۲. تغییرات در Login Flow

پس از لاگین موفق:
- اگر نقش کاربر یکی از 5 نقش بالا باشد → انتقال به `/mobile-dashboard/`
- اگر نقش کاربر مدیر یا خرید باشد → انتقال به `/dashboard/` (داشبورد اصلی)

### ۳. فایل‌های اضافه شده

```
frontend/templates/mobile_dashboard.html    - Template صفحه موبایل
test_mobile_dashboard.py                     - اسکریپت تست
```

### ۴. فایل‌های تغییر یافته

```
purchases/views.py       - اضافه کردن mobile_dashboard view و تغییر login_view
purchases/urls.py        - اضافه کردن URL pattern جدید
```

## نحوه استفاده

### ورود به سیستم

1. به آدرس `http://localhost:8000/login/` بروید
2. با یکی از کاربران دپارتمان وارد شوید:
   - kitchen / kitchen123
   - bar / bar123
   - counter / counter123
   - cashier / cashier123
   - facilities / facilities123

3. پس از ورود، به صورت خودکار به صفحه موبایل منتقل می‌شوید

### صفحه Mobile Dashboard

صفحه شامل:
- **هدر**: نام کاربر و نقش او
- **دو دکمه بزرگ**:
  1. ثبت درخواست خرید (با آیکون 📝)
  2. درخواست‌های من (با آیکون 📋)
- **دکمه خروج**: در پایین صفحه

## ویژگی‌های Mobile-Friendly

### 1. Viewport Optimization
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
```

### 2. Touch-Optimized
- دکمه‌ها حداقل 44x44 پیکسل (استاندارد Apple)
- فاصله کافی بین دکمه‌ها (20px gap)
- غیرفعال کردن text selection
- حذف tap highlight color

### 3. Responsive Breakpoints

#### موبایل (< 480px):
- فونت‌ها کوچک‌تر
- padding کمتر
- دکمه‌ها فشرده‌تر

#### Landscape Mode:
- تنظیمات ویژه برای حالت افقی
- ارتفاع کمتر برای المان‌ها

### 4. Animations
- انیمیشن fade-in برای بارگذاری
- انیمیشن hover/active برای دکمه‌ها
- Smooth transitions

## تست کردن

### 1. تست با مرورگر موبایل
در Chrome DevTools:
1. F12 را فشار دهید
2. Toggle device toolbar (Ctrl+Shift+M)
3. یک دستگاه موبایل انتخاب کنید
4. به `http://localhost:8000` بروید و لاگین کنید

### 2. تست با موبایل واقعی
1. سرور را روی 0.0.0.0 اجرا کنید:
   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```
2. IP کامپیوتر را پیدا کنید
3. از موبایل به `http://[IP]:8000` بروید

### 3. تست خودکار
```bash
python test_mobile_dashboard.py
```

## دسترسی مستقیم

کاربران همچنین می‌توانند مستقیماً به صفحه موبایل بروند:
```
http://localhost:8000/mobile-dashboard/
```

**نکته**: فقط کاربران با نقش‌های kitchen, bar, counter, cashier, facilities دسترسی دارند.

## Troubleshooting

### مشکل: صفحه به درستی نمایش داده نمی‌شود
- Cache مرورگر را پاک کنید (Ctrl+F5)
- مطمئن شوید سرور در حال اجرا است

### مشکل: دسترسی رد شد (403)
- مطمئن شوید با کاربر مناسب وارد شده‌اید
- بررسی کنید که نقش کاربر صحیح است

### مشکل: redirect اتفاق نمی‌افتد
- logout کنید و دوباره login کنید
- بررسی کنید که تغییرات در `views.py` اعمال شده باشد

## نکات مهم

1. **امنیت**: فقط کاربران authenticate شده می‌توانند به صفحه دسترسی داشته باشند
2. **Performance**: صفحه بسیار سبک است (بدون dependency خارجی)
3. **SEO**: برای استفاده داخلی طراحی شده (نیازی به SEO نیست)
4. **Browser Support**: تمام مرورگرهای مدرن (Chrome, Safari, Firefox, Edge)

## Screenshots (توضیحات ظاهری)

### صفحه اصلی:
- Background: Gradient بنفش زیبا
- Header: نام کاربر با فونت بزرگ و واضح
- Buttons: سفید با سایه، آیکون emoji
- Footer: دکمه logout شفاف

### حالت Active:
- دکمه‌ها کمی بالا می‌آیند (translateY)
- سایه بیشتر می‌شود
- Background pattern ظاهر می‌شود

## کد نمونه - دسترسی از کد

```python
from django.shortcuts import redirect

def some_view(request):
    # Redirect department users to mobile dashboard
    if request.user.role in ['kitchen', 'bar', 'counter', 'cashier', 'facilities']:
        return redirect('mobile_dashboard')
    return redirect('dashboard')
```

## آینده (پیشنهادات بهبود)

1. ✨ اضافه کردن نوتیفیکیشن‌ها به صفحه
2. 📊 نمایش آمار سریع (تعداد درخواست‌ها)
3. 🔔 لیست اعلان‌های جدید
4. 🌙 حالت Dark Mode
5. 🌐 PWA Support (نصب به عنوان اپلیکیشن)
6. 📱 Push Notifications
7. 🔄 Pull to Refresh
8. 💾 Offline Support

## پشتیبانی

در صورت بروز مشکل:
1. لاگ‌های سرور را بررسی کنید
2. Console مرورگر را چک کنید (F12)
3. مطمئن شوید migrations اجرا شده‌اند
4. بررسی کنید که template file وجود دارد

---

**تاریخ ایجاد**: 2025-12-21  
**نسخه**: 1.0  
**وضعیت**: ✅ آماده به کار
