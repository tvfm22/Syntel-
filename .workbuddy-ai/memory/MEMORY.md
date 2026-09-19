# MEMORY — پروژه Syntel (سیستم مدیریت خرید Kuker)

Django 5.0 با اپ واحد `purchases`. قالب‌ها در `syntel/frontend/templates`
(نه داخل اپ). محیط: `.venv/Scripts/python.exe`،
`DJANGO_SETTINGS_MODULE=syntel.settings`، سرور توسعه روی پورت ۸۰۰۰.

## معماری و قراردادها

### چند‌کافه‌ای (multi-cafe)
- `Cafe.code` مثل `baba_adam` و `local`. **کافه یک اپ نیست** — پیشوند مسیر است.
- تمام URLها داخل کافه با prefix `<cafe_code>/` هستند و ویوها پارامتر را با
  wrapper `cafe_view` حذف می‌کنند. پس همیشه `{% url ... cafe_code=cafe_code %}`.

### قالب‌ها
- `dashboard_layout.html` ← قالب اصلی (سایدبار، هدر، دیزاین‌توکن‌ها، CSS مشترک).
- `base.html` ← فقط adapter نازک به `dashboard_layout.html`.
- پیام‌ها (`messages`) به‌صورت سراسری در layout رندر می‌شوند — در قالب صفحه
  تکرار نکنید.
- بلوک‌ها: `title`, `extra_css`, `messages`, `content`, `extra_js`.

### دیزاین سیستم (متغیرهای CSS در `dashboard_layout.html`)
- رنگ اصلی: `--accent-indigo: #8B5CF6` (بنفش)، `--primary-dark: #7C3AED`.
- شعاع: `--radius-xs/sm/md/lg/xl/2xl/3xl` (۶ تا ۲۴px).
- فاصله: `--space-1..12` (۴ تا ۴۸px). انیمیشن: `--transition-fast/base/slow`.
- فونت: `'Vazir'`. سایه: `--shadow-sm/md/lg/xl`.
- کلاس‌های مشترک آماده: `page-header`, `page-title`, `header-actions`,
  `btn` + `btn-primary/success/danger/warning/info/secondary`, `logout-btn`,
  `card`/`card-body`/`card-title`, `filters-container`, `filters-grid`,
  `table-card`/`table-header`/`table-title`/`data-table`, `badge-*`, `alert-*`,
  `empty-state`, `form-group`, `stat-card`.
- **الگوی مرجع برای صفحه لیست:** `user_list.html` (تب سگمنتی، search-box،
  جدول data-table، act-chip، role-badge، status-pill).

## 🔴 باگ‌ها و تله‌های شناخته‌شده

### 1) تودرتویی عنصر `<style>` (اصلاح‌شده ۲۰۲۶-۰۹-۱۸)
`{% block extra_css %}` قبلاً **داخل** عنصر `<style>` لایوت بود. قالب‌های صفحه
`<style>` خودشان را می‌آوردند → دو عنصر تودرتو → HTML parser قانون پایه صفحه را
نادیده می‌گرفت (فقط media queryها پارس می‌شدند). ۳۳ قالب تحت تأثیر بودند.
اکنون بلوک بیرون از `<style>` است. **اگر عنصری stylesheet اضافه می‌کنید، خودتان
`<style>` را در `extra_css` بگذارید.**

### 2) کامنت چندخطی در جنگو
`{# ... #}` فقط **تک‌خطی** است. برای چندخطی از
`{% comment %}...{% endcomment %}` استفاده کنید وگرنه متن در صفحه چاپ می‌شود.

### 3) ۹ قالب با خطای کامپایل مستقل (از قبل موجود)
BOM (`EF BB BF`) در ابتدای فایل که قبل از `{% extends %}` می‌آید، و فیلترهای
ثبت‌نشده (`intcomma`, `split`, `notification_type_color`). در رندر واقعی سایت
کار می‌کنند ولی `get_template()` مستقیم خطا می‌دهد.

## روش کار (workflow)

### تأیید بصری در مرورگر headless
```bash
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
  --headless=new --disable-gpu --allow-file-access-from-files \
  --virtual-time-budget=9000 --window-size=1600,1100 \
  --screenshot="out.png" "file:///path.html"
```
- برای اندازه‌گیری واقعی چیدمان، `getComputedStyle` را در یک iframe probe
  اجرا کنید (نمونه: `temp/probe*.html`). **به اسکرین‌شات تنها بسنده نکنید.**

### ساخت پیش‌نمایش مستقل (روش امن)
1. HTML رندرشده را با `Client.force_login` بگیرید.
2. فقط `/static/...` را به `http://localhost:8000/static/...` بازنویسی کنید.
3. **هرگز `<head>`/`<main>` را با regex جدا/بازچین نکنید** — عنصر `<style>` را
   می‌شکند و نتیجه کاملاً گمراه‌کننده می‌شود.

### تست رندر
```python
from django.test import Client
from django.contrib.auth import get_user_model
U = get_user_model(); u = U.objects.filter(username='admin').first()
c = Client(); c.force_login(u)
r = c.get('/baba_adam/finance-dashboard/')   # → 200
```
کاربر `admin` سوپرادمین است و همه صفحات را می‌بیند.
