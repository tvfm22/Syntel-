# MEMORY — پروژه Syntel (سیستم مدیریت خرید Kuker)

Django 5.0 با اپ واحد `purchases`. قالب‌ها در `syntel/frontend/templates`
(نه داخل اپ). محیط: `.venv/Scripts/python.exe`،
`DJANGO_SETTINGS_MODULE=syntel.settings`، سرور توسعه روی پورت ۸۰۰۰.

### ⚠️ مسیر `sys.path` برای اسکریپت مستقل
تنظیمات در `syntel/syntel/settings.py` است (پوشه تو در تو). `manage.py` مسیر
`D:\Syntel\syntel` را به `sys.path` اضافه می‌کند. در اسکریپت standalone:
```python
sys.path.insert(0, os.path.join(r'D:\Syntel', 'syntel'))  # نه فقط D:\Syntel
```
اشتباه در این مسیر → `ModuleNotFoundError: No module named 'syntel.settings'`.

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

### 4) حالت خالی کاذب با `{% if page_obj %}`
`page_obj` یک **صفحه Paginator** است نه لیست؛ شیء صفحه همیشه truthy است. پس
`{% if page_obj %}` هرگز false نمی‌شود و در جستجوی بی‌نتیجه، جدول/گرید خالی رندر
می‌شود به‌جای پیام «چیزی یافت نشد». **درست: `{% if page_obj.object_list %}`.**
(اصلاح‌شده در `stock/stock_list.html`؛ صفحات دیگر را هم بررسی کنید.)

### 5) هدر چسبان جدول
`position: sticky` روی `thead th` **بدون کانتینر اسکرول با ارتفاع محدود بی‌اثر است**.
هر دو با هم لازم‌اند: `overflow: auto; max-height: 66vh;` روی wrapper +
`position: sticky; top: 0;` روی `th`.

### 6) ارتفاع زیاد و ناهمگون ردیف‌های جدول
در جدول همه سلول‌های یک ردیف هم‌ارتفاع می‌شوند، پس **یک سلول با محتوای بلند
ارتفاع کل ردیف را بالا می‌برد**. تشخیص: ارتفاع ردیف‌ها را اندازه بگیرید و
بلندترین ردیف را سلول‌به‌سلول بررسی کنید. درمان: ستون محتوایی را پهن کنید،
`padding` سلول‌ها را کم کنید، و متن بلند را با
`display:-webkit-box; -webkit-line-clamp:2; overflow:hidden` کوتاه کنید (+ `title`).
(نمونه واقعی: ستون محصول ۱۵۱px → ۲۶۰px، ارتفاع ردیف ۹۹→۶۹px.)

### 7) تله تست: جستجوی نام کلاس در HTML رندرشده
`'stk-empty' in html` **گمراه‌کننده است** — نام کلاس در بلوک `<style>` هم هست و
همیشه True می‌شود. برای بررسی وجود عنصر در DOM از regex استفاده کنید:
`re.search(r'<div class="stk-empty"', html)`.

### 8) 🔴 قفل‌کردن ارتفاع صفحه برای اسکرول داخلی (تله flex)
هدف: جدول تا پایین ویوپورت ادامه داشته باشد و **خودِ صفحه اسکرول نشود**.
- ❌ `min-height: calc(100vh - Npx)` → چون سقف نمی‌گذارد، کارت تا اندازه محتوا
  رشد می‌کند و کل صفحه بلند و اسکرول‌بار می‌شود (تجربه‌شده: صفحه ۱۸٬۹۰۹px!).
- ✅ `height: calc(100vh - Npx)` + زنجیره کامل flex:
```css
.page   { display:flex; flex-direction:column; height:calc(100vh - 48px); }
.card   { flex:1 1 auto; min-height:0; display:flex; flex-direction:column; overflow:hidden; }
.scroller { flex:1 1 0; min-height:0; overflow:auto; }
```
- **`min-height: 0` روی هر flex-item حیاتی است** (پیش‌فرض `auto` اجازه رشد
  تا اندازه محتوا می‌دهد و اسکرول داخلی را از کار می‌اندازد).
- اجزای ثابت بالای صفحه (`page-header`, toolbar) → `flex-shrink: 0`.
- Npx = padding عمودی `main-content` (در این پروژه `var(--space-6)` = ۲۴px × ۲ = ۴۸).
- تأیید با `doc.scrollHeight === doc.clientHeight` (یعنی صفحه اسکرول ندارد).

## قواعد طراحی (تجربه‌شده)

- **سلسله‌مراتب داده در جدول:** داده اصلی سطر باید غالب باشد. عدد ثانویه
  (مثل «حداقل موجودی») را کوچک‌تر و کم‌رنگ‌تر کنید وگرنه چشم را پرت می‌کند
  (نمونه: `.stk-min` ۱۳px/600 خاکستری مقابل `.stk-qty` ۱۶px/800 سبز).
- **پرهیز از نوارهای تودرتو:** چند نوار مستقل با border/سایه/padding جدا روی هم
  = ظاهر به‌هم‌ریخته. یک `page-header` + یک تولبار واحد بهتر است؛ زیرعنوان را
  پویا کنید تا با فیلتر فعال تغییر کند.
- **تب سگمنتی** برای فیلتر انبار/وضعیت (به سبک `user-tabs`) بسیار جمع‌وجورتر از
  ردیف پیل‌های جداگانه است.

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
- برای خواندن نتیجه probe از `--dump-dom` استفاده کنید (نه اسکرین‌شات):
  ```bash
  msedge.exe --headless=new --disable-gpu --allow-file-access-from-files \
    --virtual-time-budget=12000 --dump-dom "file:///.../probe.html" \
    | sed -n '/<pre id="out">/,/<\/pre>/p'
  ```
- ⚠️ **تله اسکرین‌شات:** در پیش‌نمایش `file://` فونت‌آیکون (FontAwesome) لود
  نمی‌شود و گلیف‌ها مربع خالی/بریده دیده می‌شوند. **این باگ چیدمان نیست** —
  با `scrollWidth === clientWidth` روی خود المان تأیید کنید.

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
