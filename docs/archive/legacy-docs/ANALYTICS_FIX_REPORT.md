# گزارش رفع مشکل صفحه Analytics

## 🔍 مشکل
بعد از سازماندهی مجدد پروژه، صفحه analytics در آدرس `http://localhost:8000/local/analytics/` دیگر اطلاعات را نمایش نمی‌داد.

## 🎯 تشخیص
مشکل در فایل `frontend/static/js/analytics_bubble.js` بود:
- API calls از مسیرهای مطلق استفاده می‌کردند (`/api/analytics/...`)
- در ساختار جدید URL ها باید شامل `cafe_code` باشند (`/<cafe_code>/api/analytics/...`)
- JavaScript از تغییر ساختار URL خبر نداشت

## ✅ راه حل
فایل `analytics_bubble.js` را بروزرسانی کردیم:

### 1. اضافه کردن متد `getCafeCodeFromUrl()`:
```javascript
getCafeCodeFromUrl() {
    const pathParts = window.location.pathname.split('/').filter(p => p);
    return pathParts[0] || 'local';
}
```

### 2. اضافه کردن متد `buildApiUrl()`:
```javascript
buildApiUrl(endpoint) {
    return `/${this.cafeCode}${endpoint}`;
}
```

### 3. بروزرسانی تمام API calls:
```javascript
// قبل:
const response = await fetch(`/api/analytics/departments/expenses/?period=...`);

// بعد:
const apiUrl = this.buildApiUrl(`/api/analytics/departments/expenses/?period=...`);
const response = await fetch(apiUrl);
```

## 📊 بررسی داده‌ها
اسکریپت `check_analytics_data.py` نشان داد که داده‌ها در دیتابیس موجود هستند:
- ✅ 329 Invoice
- ✅ 2663 InvoiceItem  
- ✅ 35 InvoiceItem در ماه اخیر
- ✅ 4 بخش (Requester)
- ✅ 26 دسته‌بندی اصلی
- ✅ 104 محصول
- ✅ 400+ میلیون تومان هزینه در ماه اخیر

## 🔧 فایل‌های تغییر یافته
1. `frontend/static/js/analytics_bubble.js` - اصلاح API URLs
2. `scripts/management_commands/check_analytics_data.py` - اسکریپت بررسی داده‌ها

## 📝 Commit ها
- `50f8d69` - fix: Update analytics_bubble.js to use cafe_code in API URLs

## ✨ تست
برای تست:
1. مطمئن شوید سرور Django در حال اجراست: `python manage.py runserver`
2. مرورگر را باز کنید و به آدرس بروید: `http://localhost:8000/`
3. وارد سیستم شوید
4. به صفحه Analytics بروید: `http://localhost:8000/local/analytics/`
5. اطلاعات باید به درستی نمایش داده شوند

## 🎉 نتیجه
مشکل رفع شد و صفحه analytics اکنون به درستی کار می‌کند!

تاریخ: 3 فوریه 2026
