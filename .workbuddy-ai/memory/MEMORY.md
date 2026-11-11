# MEMORY — Syntel (خرید Kuker)

## پایه
Django 5.0.2، اپ `purchases`، قالبها `syntel/frontend/templates` (بر `purchases/templates` ارجح).
`.venv/Scripts/python.exe`، `DJANGO_SETTINGS_MODULE=syntel.settings`،
تست `manage.py test ... --settings=syntel.test_settings`.
تله sys.path: تنظیمات در `syntel/syntel/settings.py` است ← `sys.path.insert(0, r'D:\Syntel\syntel')`.

## چندکافه‌ای و URL
- `Cafe.code` (`baba_adam`/`local`) پیشوند مسیر است؛ همیشه `{% url ... cafe_code=cafe_code %}`.
- جستجو fail-closed: `get_scoped_object_or_404` + `CAFE_LOOKUP_PATHS` (مدل جدید ثبت شود وگرنه 404).
- بعد از هر تغییر `urls.py` تگهای `{% url %}` را بغربالید: الگوهای کافهمحور دو پارامترهاند و جاافتادنشان ۵۰۰ میدهد. (۲۰۲۶-۱۱-۰۹ رفع `voucher_create` ← `voucher_type=receipt`.)

## قالبها / CSS
- `dashboard_layout.html` اصلی، `base.html` adapter؛ بلوکها `title/extra_css/messages/content/extra_js`.
- کلاسهای آماده: `page-header`, `page-title`, `header-actions`, `btn*`, `card`, `data-table`, `badge-*`, `alert-*`, `empty-state`, `stat-card`. الگوی لیست `user_list.html`. توکنها `--accent-indigo:#8B5CF6`, `--radius-*`, `--space-*`, `--shadow-*`, فونت Vazir.
- تلهها: ①`extra_css` بیرون `<style>` است، خودتان `<style>` بگذارید؛ `{# #}` فقط تکخطی (چندخطی `{% comment %}`). ②`{% if page_obj %}` همیشه truthy ← `page_obj.object_list`. ③`sticky` روی `th` نیاز wrapper با `overflow:auto; max-height`؛ سلول بلند ردیف را بالا میبرد ← ستون پهن + `line-clamp:2`. ④اسکرول داخلی = `height` + زنجیره flex با `min-height:0`، نه `min-height:calc(...)`. ⑤`trim_decimal:N` بجای `floatformat`. ⑥فرم درونردیفی در لیست بزرگ ممنوع (۳۹۰ فرم = ۱MB) ← مودال مشترک با `data-*`. ⑦۹ قالب قدیمی BOM قبل `{% extends %}` دارند، بیدردسر. ⑧تست نام کلاس: `re.search(r'<div class="x"', html)` چون در `<style>` هم هست. ⑨برند فقط در `dashboard_layout` است (نوار موبایل + سایدبار)؛ لوگوی داخل `page-header` صفحات لیست در <1024px تکراری و بهمریخته میشود — `purchase_requests_list` و `user_list` مخفیش کردند (`req-brand`/`u-brand`). ⑩`.table-scroll` در `dashboard_layout` تعریف **نشده**؛ صفحهای که جدول دارد خودش `overflow-x:auto` + `min-width` بدهد وگرنه ستونها در موبایل بریده میشوند.

## تأیید بصری
`msedge --headless=new --disable-gpu --allow-file-access-from-files --virtual-time-budget=9000 --window-size=1600,1100 --screenshot=out.png file:///...`
HTML را با `Client.force_login` رندر کن، فقط `/static/` بازنویسی شود؛ `<head>`/`<main>` را با regex جدا نکن (`<style>` میشکند). در `file://` فونتآیکون و JS حجیم لود نمیشوند ← برای نمودار `http://127.0.0.1:<port>/static/...`. `runserver` حتماً `run_in_background=true`.

## منو / COGS
- مدلها: `KukerItem` (raw/prep/dish)، `KukerRecipeLine`، `CogsReport(Line)`، `MenuSection`، `KukerItemCostHistory`؛ `import_kuker_menu --cafe CODE`.
- همه کافهمحور؛ `KukerItem.key` داخل کافه یکتاست. `MenuGraph.from_db(cafe=...)` / `compute_cogs(sold, cafe=...)` — بی cafe یعنی همه رکوردها (فقط اسکریپت/تست).
- `unit_cost`=اعلامی اکسل؛ `computed_unit_cost`=از رسپی، مبنای تاریخچه/لیست/نمایش؛ هر دو memo با `_cache`.
- کوچکترین واحد: `normalize_to_smallest_unit` (÷۱۰۰۰) و `convert_quantity_to_smallest_unit` (سطر اول). `normalize_name` + `ITEM_ALIASES`/`RAW_ALIASES`.
- `product_sync.py` با گارد thread-local؛ `getattr(product,'cogs_item',None)` امن. `record_cost_snapshots_for_cafe` داخل `transaction.atomic()`.
- صفحات (`views_cogs.py`): `cogs_menu_list` `/<cafe>/cogs/`، `cogs_manage_items` `/items/`، `cogs_consumption_calc` `/consumption/`؛ `cogs_dashboard` alias. تب `cogs_nav.html` با `cogs_page`. بخشهای «آمادهسازی آشپزخانه»/«بار» همیشه هست.
- ویرایشگر رسپی **تکنسخه**: `partials/cogs_recipe_editor.html` + `cogs_recipe_editor_css.html`؛ API `CogsRecipe.open/close/onSaved`. «ساخت آیتم ← رسپی» = `?recipe=<id>` + `auto_open_item`.
- تاریخچه قیمت: مدل `ProductPriceEntry` (نه `PriceHistory`). `PRICE_UNIT_OPTIONS` = `kg/gram/liter/mil/piece/package`، `normalize_package_price`؛ اندپوینتهای `cogs_price_preview`، `cogs_price_entry`، `cogs_raw_material_create`، `cogs_menu_item_create`.
- نیمهآماده فقط دو بخش ثابت؛ غذای منو dish، ماده اولیه نمیشود؛ چرخه `_has_cycle`. `cogs_manage_items` نام غذا/نیمهآماده را در HTML نمیآورد (تست قدیمی).
- فرمول JS آینه `computed_unit_cost`: ماده با `fee`←`qty×fee`؛ بدون `fee`←تبدیل واحد×price؛ نیمهآماده←`qty×cost`؛ جمع÷`out_qty`.
- مصرف: یک ویو/پنج action (`save`/`preview`/`update`/`save_as_new`/`delete`)؛ GET با `qty__<id>` هم پیشنمایش میسازد. فیلتر `?cat=<id|none>&dept=<usage_location>` با آینهٔ کلاینت و `replaceState`. `compute_cogs` به سطرها `category_id/name/usage_location` میچسباند؛ `prep_rows` شامل `direct_lines` و `raw_lines`. 🔴 `split_fee=bool(cost) and sub_raw_cost>0` وگرنه COGS صفر. گروهبندی فروش = `MenuSection`.

## رسپی ↔ سفارش (سراسری)
- منبع حقیقت `purchases/services/recipe_gate.py`: کالای دستهٔ غذایی (`Category.requires_recipe` روی خود/والد) فقط وقتی دیده میشود که مادهٔ `KukerRecipeLine` همان کافه باشد؛ غیرغذایی/بیدسته آزاد.
- معافیت `PRODUCT_MANAGER_ROLES` (purchasing/warehouse/admin/manager/accounting/finance_manager/ceo/superuser) — گیت بیقید اعمال نشود (`product_form.html` هم `products_autocomplete` دارد).
- POST هر دو فرم و `edit_my_request` گیت دارند (`recipe_gate_error`؛ چون شناسه نمیفرستد ← `_recipe_gate_for_name`).
- `quick_add_product` همیشه دستهٔ «تحقیق و توسعه» همان کافه؛ آن دسته از `all_categories` فرم حذف است.
- `product_list`: نشانهای `در رسپی`/`بدون رسپی`/`بدون نیاز به رسپی` + `p-chip-rd`؛ فیلتر `?recipe=used|missing|free` (در حافظه). متن گزینهها عمداً با نشانها فرق دارد.

## قفل «تایید دریافت قبل از درخواست جدید»
- منبع حقیقت `purchases/services/receipt_gate.py`: `OfficerAssignment` با `target_role='warehouse'` + `status='fulfilled'` روی اقلام درخواستِ خود کاربر، منهای `PurchaseItem.received_confirmed_at`؛ همان مبنای صفحهٔ «کالاهای دریافتی من» و نشان داشبورد. `cafe` محدوده را به کافهٔ جاری میبندد.
- `RECEIPT_GATE_ROLES` فقط نقشهای بخش/درخواستدهنده؛ سوپرادمین و نقشهای ستادی معافاند. `receipt_gate_context` → بنر مشترک `partials/receipt_gate_banner.html` + دکمهٔ ارسال `disabled`.
- قفل سمت سرور در POST هر دو فرم (`purchase_request` / `warehouse_request_form`) و صفحهٔ ویرایش (`edit_my_request` — فقط ردیف تازه؛ ویرایش ردیف موجود آزاد میماند)؛ ردیفهای تایپشده حفظ میشوند. بنر مشترک `partials/receipt_gate_banner.html` در هر سه‌جا نمایش داده میشود.
- 🔴 حفظ متن آزمونها: زیرنویس داشبورد موبایل باید هم «N قلم در انتظار تایید دریافت» و هم «مشاهده و تایید کالاهای تحویل‌شده» (با نیمفاصله) را داشته باشد؛ حذف/بینیمفاصله کردنشان دو تست قدیمی را قرمز میکند.

## تست / بدهی
- `WarehouseRequest.status` میتواند `partial`؛ `OfficerAssignment.status` میتواند `reopened`.
- بدهی قدیمی باقیمانده: `test_analytics_bubble` (۹ خطا: `PurchaseRequest` فیلد `requester`/`description` ندارد)، `test_data_integrity` (unique با cafe=NULL در SQLite).
- ✅ رفع شد (۲۰۲۶-۱۱-۱۱): `test_purchase_item_form_invalid` — `PurchaseItemForm.quantity` حالا `MinValueValidator(0)` + `min="0" step="any"` دارد؛ مقدار منفی دیگر قبول نمیشود.
- ✅ رفع شد (۲۰۲۶-۱۱-۱۱): `test_my_requests_day_groups` بعد از نیمهشب قرمز میشد چون ویو `datetime.now().date()` (محلی) را با `created_at.date()` (UTC) مقایسه میکرد. ویو `timezone.localdate()`/`timezone.localtime()` گرفت و تست هم `django.utils.timezone.now` را فریز میکند تا مرز نیمهشب وسط تست بیتأثیر شود. تست `test_requests_appear_on_registration_day_not_delivery_day` هم `now-3h` را به نقطهای بین نیمهشب و الان تغییر داد (قبل از ساعت ۳ بامداد به دیروز میافتاد).
- تست وابسته به ساعت: هر تستی که «امروز» میسازد، نباید بازهٔ ساعتی ثابت از `timezone.now()` کم کند.
- 🔴 `USE_TZ=True` و `TIME_ZONE=Asia/Tehran`؛ `created_at` در DBـه UTC است. فیلترهای `jalali_date`/`jalali_datetime`/`jalali_time` در `custom_filters.py` با `_as_local()` زمان آگاه را به محلی تبدیل میکنند (ورودی naive/date دستنخورده میماند) — وگرنه ساعتها ۳:۳۰ عقب نشان داده میشوند.
- دستنخورده: ویو `order` میفرستد ولی قالب `purchase_order` میخواهد. (بارگذاری فیلترهای `purchase_order_detail` در ۲۰۲۶-۱۱-۱۱ تعمیر شد.)

## شماره درخواست (از ۲۰۲۶-۱۱-۱۱)
- بدون هشتک، عدد لاتین ساده از ۱۰۰۰۰۰: `purchases/numbering.py` → `request_number(pk)` = `str(100000+pk-1)`؛ **رشته** برگردانده شود تا `localize` ارقام/جداکننده نزند. مدلها property `request_number` دارند؛ قالبها `{{ req.request_number }}` و برای id خام `|request_number`.
- شماره فقط نمایشی است و از `id` مشتق میشود (بدون مایگریشن/بکفیل)؛ URLها، `name="request_id"` و آرگومانهای JS همچنان `id` خاماند. هشتک فاکتور/حواله/گزارش COGS/قلم کالا عمداً ماند.
- نکتهٔ قالب: در `purchase_order_form` حتماً `{% extends %}` خط اول باشد وگرنه «must be the first tag»؛ فیلتر `replace` در Django وجود ندارد.
