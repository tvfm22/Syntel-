# تغییرات تسک: برند محصول و عمر مفید (Shelf Life)

**تاریخ:** 2026-08-12
**قاعده کلی:** توسعهٔ سیستم موجود، نه بازنویسی — بدون از دست رفتن داده‌ها و بدون شکستن رفتار قبلی.

---

## ۱. مدل‌ها (`syntel/purchases/models.py`)

| مدل | تغییر |
|---|---|
| `Product` | فیلد جدید `shelf_life_days` — عدد صحیح (PositiveInteger)، اختیاری (خالی = ندارد) |
| `PurchaseItem` | فیلد جدید `product_brand` (FK به `ProductBrand`، `null=True`) |
| `WarehouseRequestItem` | فیلد جدید `product_brand` (FK به `ProductBrand`، `null=True`) |

**مایگریشن:** `syntel/purchases/migrations/0072_product_shelf_life_days_purchaseitem_product_brand_and_more.py`
— ساخته و روی دیتابیس dev اعمال شده. رکوردهای موجود دست‌نخورده ماندند.

---

## ۲. `syntel/purchases/views.py`

1. **`search_products_autocomplete`** — محصولاتِ دارای برند، به ازای هر برندِ فعال یک نتیجه برمی‌گردانند با:
   - `label = "نام - برند"` (مثلاً «روغن - دمس»)
   - `product_brand_id`، `brand`، واحد تجاری برند
   - محصولات بدون برند مثل قبل ساده می‌مانند.

2. **`check_product_availability`** (view جدید) — پارامترهای `product_id`، `brand_id`، `qty`:
   - جمع موجودی از `StockItem`ها بر اساس (محصول، برند) در انبارهای کافه
   - تبدیل به واحد تجاری با `conversion_rate`
   - اگر برند انتخابی موجودی کافی نداشت → لیست **برندهای جایگزینِ دارای موجودی** (پیشنهاد، نه مسدودسازی)
   - برند نامرتبط به محصول → 404

3. **`purchase_request_form`** (POST) — خواندن `item_brand_id` به‌همراه `item_product_id`؛ برندی که به محصول تعلق ندارد ساکت حذف می‌شود (هیچ محصول جدیدی ساخته نمی‌شود).

4. **`warehouse_request_form`** (POST) — همان الگو برای `WarehouseRequestItem`.

5. **مسیرهای تبدیل** (`process_warehouse_request`، `_process_available_products_post`) — انتقال `product_brand` از آیتم درخواست به آیتم‌های جدید، تا برند در کل جریان حفظ شود.

6. **`product_create` / `product_update`** — ذخیره/پاک‌کردن `shelf_life_days` (هیچ مقداری = پاک‌کردن).

---

## ۳. `syntel/purchases/urls.py`

Route جدید:

```
path('api/products/check-availability/', cv(views.check_product_availability), name='check_product_availability')
```

---

## ۴. تمپلیت‌ها

### `frontend/templates/product_form.html`
- ورودی «عمر مفید (روز)» در بخش پایه محصول (هم ایجاد، هم ویرایش) — placeholder «مثال: 180»

### `frontend/templates/product_list.html`
- نمایش «۱۸۰ روز» یا «—» در کارت محصول

### `frontend/templates/purchase_request_form.html` و `warehouse_request_form.html` (یکسان)
- دراپ‌داون برنددار («روغن – دمس») + چیپ برند کنار فیلد انتخاب
- قبل از افزودن به لیست: درخواست به `check-availability` → خط موجودی رنگی (سبز/زرد/قرمز)
- برند انتخابی موجود نیست ولی برند دیگری موجود است → دکمه «استفاده از برند دیگر» (کلیک یک‌باره، بررسی مجدد)
- فیلدهای مخفی `item_product_id` / `item_brand_id` در submit

### `purchase_request_detail.html` / `purchasing_requests_list.html` / `warehouse_officer_dashboard.html`
- نمایش «نام - برند» در آیتم‌ها با گارد — محصول بدون برند مثل قبل نمایش داده می‌شود.

---

## ۵. تأیید صحت

- ✅ `manage.py check` — بدون خطا
- ✅ `makemigrations --check --dry-run` — «No changes detected»
- ✅ ۶ تست اسکرچ (autocomplete برنددار، موجودی/جایگزین/404، ثبت برند در هر دو فرم، عمر مفید create/update/clear، رندر صفحات) — همه سبز، سپس حذف شدند

### نکته مهم — شکست‌های از پیش موجود
~۸۳ شکست در `manage.py test purchases` **از قبل وجود داشتند** و ربطی به این تسک ندارند:
- `NoReverseMatch` — تست‌ها بدون `cafe_code` reverse می‌کنند (بازآرایی multi-cafe)
- انتظارات قدیمی لاگین (302 و متن خطا)
- `test_data_integrity` — تکراری‌بودن نام محصول با `cafe=NULL` در SQLite

---

## ۶. وضعیت

- کامیت نشده است.
- در `git status` علاوه بر فایل‌های کد، فایل‌های runtime (لاگ و session files در `data/`) هم دیده می‌شوند که مربوط به اجرای تست‌ها هستند، نه ویژگی.
