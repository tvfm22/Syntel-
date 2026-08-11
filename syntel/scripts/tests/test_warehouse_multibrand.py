# -*- coding: utf-8 -*-
"""تست چندبرندی تأمین از انبار — اجرا با PYTHONIOENCODING=utf-8"""
import sys, os
sys.path.insert(0, 'D:/Syntel/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import datetime
from purchases.models import (
    Cafe, Unit, Product, ProductBrand, Warehouse, StockItem,
    StockTransaction, PurchaseRequest, PurchaseItem, OfficerAssignment,
)

User = get_user_model()
ok = lambda cond, msg: print(('PASS' if cond else 'FAIL') + ' - ' + msg)

# ---------- آماده‌سازی ----------
TEST_PRODUCT = 'شکر چندبرند تست'
TEST_PRODUCT_NB = 'قند بدون برند تست'
for pname in (TEST_PRODUCT, TEST_PRODUCT_NB):
    PurchaseItem.objects.filter(name=pname).delete()
    PurchaseRequest.objects.filter(items__name=pname).delete()
    OfficerAssignment.objects.filter(item__name=pname).delete()
    StockTransaction.objects.filter(description__icontains=pname).delete()
    StockItem.objects.filter(product__name=pname).delete()
    ProductBrand.objects.filter(product__name=pname).delete()
    Product.objects.filter(name=pname).delete()

cafe = Cafe.objects.filter(code='local').first()
if not cafe:
    cafe = Cafe.objects.create(name='کافه محلی', code='local')

unit_kg, _ = Unit.objects.get_or_create(name='کیلوگرم')
wh, _ = Warehouse.objects.get_or_create(cafe=cafe, code='TST-MB', defaults={'name': 'انبار تست چندبرند'})
prod, _ = Product.objects.get_or_create(name=TEST_PRODUCT, cafe=cafe, defaults={'unit': unit_kg})
brand_a, _ = ProductBrand.objects.get_or_create(product=prod, brand_name='برند آلفا')
brand_b, _ = ProductBrand.objects.get_or_create(product=prod, brand_name='برند بتا', defaults={'trade_unit': 'بسته'})
brand_c, _ = ProductBrand.objects.get_or_create(product=prod, brand_name='برند گاما', defaults={'trade_unit': 'کارتن', 'conversion_rate': Decimal('2')})

# موجودی‌ها: آلفا ۱۰ واحد پایه (بدون نرخ)، بتا ۱۰ (نرخ ۱)، گاما ۱۰ (نرخ ۲ → هر کارتن ۲ واحد)
si_a, _ = StockItem.objects.get_or_create(
    product=prod, product_brand=brand_a, warehouse=wh, defaults={'quantity': Decimal('10'), 'unit': unit_kg})
si_b, _ = StockItem.objects.get_or_create(
    product=prod, product_brand=brand_b, warehouse=wh, defaults={'quantity': Decimal('10'), 'unit': unit_kg})
si_c, _ = StockItem.objects.get_or_create(
    product=prod, product_brand=brand_c, warehouse=wh, defaults={'quantity': Decimal('10'), 'unit': unit_kg})
si_a.quantity = Decimal('10'); si_a.save()
si_b.quantity = Decimal('10'); si_b.save()
si_c.quantity = Decimal('10'); si_c.save()

officer, _ = User.objects.get_or_create(username='test_wh_mb')
officer.role = 'warehouse'; officer.cafe = cafe; officer.set_password('x'); officer.save()
requester, _ = User.objects.get_or_create(username='test_req_mb')
requester.role = 'kitchen'; requester.cafe = cafe; requester.set_password('x'); requester.save()

c = Client(); c.force_login(officer)
DASH = '/local/warehouse-officer/dashboard/'

def make_item():
    req = PurchaseRequest.objects.create(cafe=cafe, user=requester, status='pending')
    item = PurchaseItem.objects.create(request=req, product=prod, name=TEST_PRODUCT, quantity=10, unit='کیلوگرم')
    return req, item

def fresh_stock():
    si_a.quantity = Decimal('10'); si_b.quantity = Decimal('10'); si_c.quantity = Decimal('10')
    si_a.save(); si_b.save(); si_c.save()
    OfficerAssignment.objects.filter(item__name=TEST_PRODUCT).delete()
    StockTransaction.objects.filter(description__icontains=TEST_PRODUCT).delete()

# ---------- ۱) صفحه داشبورد: کارت کالا + گزینه‌های چند برند ----------
req1, item1 = make_item()
r = c.get(DASH)
ok(r.status_code == 200, f'داشبورد مسئول انبار باز می‌شود → {r.status_code}')
body = r.content.decode('utf-8', errors='replace')
ok(f'rows-{item1.id}' in body, 'کانتینر ردیف‌های پویا (rows-<id>) در صفحه است')
ok('افزودن برند دیگر' in body, 'دکمه «افزودن برند دیگر» در صفحه است')
ok(f'value="{si_a.id}"' in body and f'value="{si_b.id}"' in body and f'value="{si_c.id}"' in body,
   'هر سه برند در لیست انتخاب انبار/برند نمایش داده می‌شود')
ok('برند آلفا' in body and 'برند بتا' in body and 'برند گاما' in body,
   'گزینه‌ها فقط نام تجاری را نشان می‌دهند')
ok('انبار تست چندبرند' not in body, 'نام انبار در گزینه‌ها نمایش داده نمی‌شود (وقتی برند فقط در یک انبار است)')

# ---------- ۲) تأمین با دو برند (ردیف اول + ردیف اضافه) ----------
fresh_stock()
r = c.post(DASH, {'item_id': item1.id, 'action': 'fulfill',
                  'stock_item_id': si_a.id, 'qty': '4',
                  'stock_item_id_1': si_b.id, 'qty_1': '6'}, follow=True)
ok(r.status_code == 200, 'بعد از تأمین دوبرند ریدایرکت به داشبورد می‌شود')
si_a.refresh_from_db(); si_b.refresh_from_db()
ok(si_a.quantity == Decimal('6') and si_b.quantity == Decimal('4'),
   f'موجودی هر دو برند کسر شد: آلفا {si_a.quantity} (۶)، بتا {si_b.quantity} (۴)')
txs = StockTransaction.objects.filter(description__icontains=TEST_PRODUCT).order_by('id')
ok(txs.count() == 2, f'دو تراکنش StockTransaction ثبت شد → {txs.count()}')
assigns = OfficerAssignment.objects.filter(item=item1).order_by('id')
ok(assigns.count() == 2, f'دو تخصیص OfficerAssignment ساخته شد → {assigns.count()}')
ok(assigns[0].status == 'fulfilled' and assigns[0].stock_item_id == si_a.id
   and assigns[0].supplied_quantity == Decimal('4'),
   'تخصیص اول: fulfilled با انبار/برند آلفا و مقدار ۴')
ok(assigns[1].status == 'fulfilled' and assigns[1].stock_item_id == si_b.id
   and assigns[1].supplied_quantity == Decimal('6'),
   'تخصیص دوم (برند اضافه): fulfilled با انبار/برند بتا و مقدار ۶')
req1.refresh_from_db()
ok(req1.status == 'purchased', f'وضعیت درخواست همگام‌سازی شد → {req1.status}')
body = r.content.decode('utf-8', errors='replace')
ok('2 برند' in body, 'پیام موفقیت تعداد برندها را ذکر می‌کند')

# ---------- ۳) نرخ تبدیل: برند گاما (هر کارتن = ۲ واحد پایه) ----------
req2, item2 = make_item()
fresh_stock()
r = c.post(DASH, {'item_id': item2.id, 'action': 'fulfill',
                  'stock_item_id': si_c.id, 'qty': '3'}, follow=True)
si_c.refresh_from_db()
ok(si_c.quantity == Decimal('4'), f'نرخ تبدیل رعایت شد: ۳ کارتن × ۲ = ۶ واحد کسر → {si_c.quantity}')
tx = StockTransaction.objects.filter(description__icontains=TEST_PRODUCT).first()
ok(tx and tx.quantity == Decimal('6'), f'تراکنش با مقدار پایه ۶ ثبت شد → {tx.quantity if tx else "—"}')

# ---------- ۴) ردیف تکراری (همان برند دو بار): جمع > موجودی → خطا بدون کسر ----------
req3, item3 = make_item()
fresh_stock()
r = c.post(DASH, {'item_id': item3.id, 'action': 'fulfill',
                  'stock_item_id': si_a.id, 'qty': '6',
                  'stock_item_id_1': si_a.id, 'qty_1': '6'}, follow=True)
si_a.refresh_from_db()
ok(si_a.quantity == Decimal('10'), f'ردیف تکراری متوقف شد: موجودی آلفا دست‌نخورده → {si_a.quantity}')
ok(OfficerAssignment.objects.filter(item=item3).count() == 0, 'برای ردیف تکراری نامعتبر تخصیصی ساخته نشد')

# ---------- ۵) ردیف اضافه خالی (JS افزوده ولی پر نشده): نادیده گرفته شود ----------
req4, item4 = make_item()
fresh_stock()
r = c.post(DASH, {'item_id': item4.id, 'action': 'fulfill',
                  'stock_item_id': si_b.id, 'qty': '7',
                  'stock_item_id_1': '', 'qty_1': ''}, follow=True)
si_b.refresh_from_db()
ok(si_b.quantity == Decimal('3'), f'ردیف خالی نادیده گرفته شد، ردیف اول اعمال شد → {si_b.quantity}')
ok(OfficerAssignment.objects.filter(item=item4).count() == 1, 'فقط یک تخصیص برای ردیف معتبر ساخته شد')

# ---------- ۶) backward compat: فقط stock_item_id/qty (بدون JS) ----------
req5, item5 = make_item()
fresh_stock()
r = c.post(DASH, {'item_id': item5.id, 'action': 'fulfill',
                  'stock_item_id': si_a.id, 'qty': '5'}, follow=True)
si_a.refresh_from_db()
ok(si_a.quantity == Decimal('5'), f'تأمین تک‌ردیفه قدیمی همچنان کار می‌کند → {si_a.quantity}')

# ---------- ۷) موجودی ناکافی در یکی از ردیف‌ها: خطا و بدون کسر جزئی ----------
req6, item6 = make_item()
fresh_stock()
r = c.post(DASH, {'item_id': item6.id, 'action': 'fulfill',
                  'stock_item_id': si_a.id, 'qty': '4',
                  'stock_item_id_1': si_c.id, 'qty_1': '6'}, follow=True)  # ۶ کارتن × ۲ = ۱۲ > ۱۰
si_a.refresh_from_db(); si_c.refresh_from_db()
ok(si_a.quantity == Decimal('10') and si_c.quantity == Decimal('10'),
   f'خطای یکی از ردیف‌ها: هیچ کسری اعمال نشد → آلفا {si_a.quantity}، گاما {si_c.quantity}')
ok(OfficerAssignment.objects.filter(item=item6).count() == 0, 'با خطای اعتبارسنجی تخصیصی ساخته نشد')

# ---------- ۸) ماندن روی همان تاریخ بعد از ثبت تأمین ----------
today = datetime.now().date().isoformat()
req8, item8 = make_item()
fresh_stock()
r = c.post(DASH, {'item_id': item8.id, 'action': 'fulfill',
                  'stock_item_id': si_a.id, 'qty': '2',
                  'date': today}, follow=True)
final_url = r.redirect_chain[-1][0] if r.redirect_chain else ''
ok(f'date={today}' in final_url, f'بعد از ثبت تأمین، تاریخ همان روز حفظ می‌شود → {final_url}')

r = c.post(DASH, {'item_id': item8.id, 'action': 'fulfill',
                  'stock_item_id': si_a.id, 'qty': '999',
                  'date': today}, follow=True)
final_url = r.redirect_chain[-1][0] if r.redirect_chain else ''
ok(f'date={today}' in final_url, 'خطای اعتبارسنجی هم تاریخ را حفظ می‌کند')

r = c.post(DASH, {'item_id': item8.id, 'action': 'fulfill',
                  'stock_item_id': si_b.id, 'qty': '1'}, follow=True)
final_url = r.redirect_chain[-1][0] if r.redirect_chain else ''
ok(f'date={today}' in final_url, f'بدون ارسال تاریخ هم روی تاریخ امروز برمی‌گردد → {final_url}')

# ---------- ۹) چند انبار: برند در انبار دیگر هم نمایش و قابل انتخاب باشد ----------
wh2, _ = Warehouse.objects.get_or_create(cafe=cafe, code='TST-MB2', defaults={'name': 'انبار دوم تست'})
si_b2, _ = StockItem.objects.get_or_create(
    product=prod, product_brand=brand_b, warehouse=wh2, defaults={'quantity': Decimal('8'), 'unit': unit_kg})
si_b2.quantity = Decimal('8'); si_b2.save()
req9, item9 = make_item()
fresh_stock()
r = c.get(DASH)
body = r.content.decode('utf-8', errors='replace')
ok('برند بتا (انبار دوم تست)' in body, 'برند بتا در انبار دوم با نام انبار نمایش داده می‌شود')
ok(f'value="{si_b2.id}"' in body, 'موجودی انبار دوم قابل انتخاب است')
r = c.post(DASH, {'item_id': item9.id, 'action': 'fulfill',
                  'stock_item_id': si_b2.id, 'qty': '3'}, follow=True)
si_b2.refresh_from_db()
ok(si_b2.quantity == Decimal('5'), f'تأمین از انبار دوم انجام شد → {si_b2.quantity}')
assign = OfficerAssignment.objects.filter(item=item9).first()
ok(assign and assign.stock_item_id == si_b2.id, 'تخصیص به موجودی انبار دوم متصل شد')

# ---------- ۱۰) کالاهای ثبت‌شده: کمرنگ پایین صفحه + بازگشایی ----------
req10, item10 = make_item()
fresh_stock()
c.post(DASH, {'item_id': item10.id, 'action': 'fulfill',
              'stock_item_id': si_a.id, 'qty': '3'})
si_a.refresh_from_db()
ok(si_a.quantity == Decimal('7'), f'قبل از بازگشایی: تامین قبلی کسر شده → {si_a.quantity}')
r = c.get(DASH)
body = r.content.decode('utf-8', errors='replace')
ok('کالاهای ثبت‌شده این روز' in body, 'بخش «کالاهای ثبت‌شده» پایین صفحه هست')
ok('done-card' in body and 'تأمین شده' in body, 'کالای تامین‌شده کمرنگ با وضعیت «تأمین شده» نمایش داده می‌شود')
ok(f'reopen-form-{item10.id}' in body, 'فرم بازگشایی برای کالای ثبت‌شده هست')

# بازگشایی: موجودی برمی‌گردد و کالا دوباره در لیست فعال می‌آید
r = c.post(DASH, {'item_id': item10.id, 'action': 'reopen', 'date': today}, follow=True)
si_a.refresh_from_db()
ok(si_a.quantity == Decimal('10'), f'بعد از بازگشایی موجودی به انبار برگشت → {si_a.quantity}')
ok(OfficerAssignment.objects.filter(item=item10).count() == 0, 'تخصیص‌های قبلی حذف شدند')
ok(StockTransaction.objects.filter(
    reference_type='officer_assignment', reference_id=item10.id).count() == 0,
   'تراکنش‌های قبلی حذف شدند')
body = r.content.decode('utf-8', errors='replace')
ok(f'rows-{item10.id}' in body, 'کالای بازگشایی‌شده دوباره در لیست فعال ظاهر شد')

# عدم موجودی → کارت کمرنگ با وضعیت برگشت به مدیرعامل
req11, item11 = make_item()
fresh_stock()
c.post(DASH, {'item_id': item11.id, 'action': 'not_in_stock'})
r = c.get(DASH)
body = r.content.decode('utf-8', errors='replace')
ok('عدم موجودی — برگشت به مدیرعامل' in body, 'وضعیت «عدم موجودی — برگشت به مدیرعامل» نمایش داده می‌شود')

# ---------- ۱۱) محصول بدون نام تجاری: «بدون برند» دیفالت انتخاب شده ----------
prod_nb, _ = Product.objects.get_or_create(name=TEST_PRODUCT_NB, cafe=cafe, defaults={'unit': unit_kg})
si_nb, _ = StockItem.objects.get_or_create(
    product=prod_nb, product_brand=None, warehouse=wh, defaults={'quantity': Decimal('10'), 'unit': unit_kg})
si_nb.quantity = Decimal('10'); si_nb.save()
req_nb = PurchaseRequest.objects.create(cafe=cafe, user=requester, status='pending')
item_nb = PurchaseItem.objects.create(
    request=req_nb, product=prod_nb, name=TEST_PRODUCT_NB, quantity=5, unit='کیلوگرم')
r = c.get(DASH)
body = r.content.decode('utf-8', errors='replace')
ok(f'value="{si_nb.id}" selected' in body, 'گزینه «بدون برند» برای محصول بدون نام تجاری دیفالت انتخاب شده است')
ok('بدون برند' in body, 'برچسب «بدون برند» نمایش داده می‌شود')

# ---------- ۱۲) PDF «درخواست کالا از انبار» (راست به چپ + عنوان/تاریخ/امضا) ----------
admin_pdf, _ = User.objects.get_or_create(username='test_admin_pdf')
admin_pdf.role = 'admin'; admin_pdf.set_password('x'); admin_pdf.is_staff = True; admin_pdf.save()
c_pdf = Client(); c_pdf.force_login(admin_pdf)
req12, item12 = make_item()
fresh_stock()
c.post(DASH, {'item_id': item12.id, 'action': 'fulfill',
              'stock_item_id': si_a.id, 'qty': '2',
              'stock_item_id_1': si_b.id, 'qty_1': '3'})
r = c_pdf.get(f'/local/request-management/warehouse-pdf/?date={today}')
ok(r.status_code == 200, f'PDF «درخواست کالا از انبار» برای ادمین ساخته می‌شود → {r.status_code}')
ok(r['Content-Type'] == 'application/pdf', 'خروجی application/pdf است')
ok(r.content.startswith(b'%PDF'), 'خروجی فایل PDF معتبر است')
ok(len(r.content) > 1000, f'PDF خالی نیست ({len(r.content)} بایت)')
ok('attachment' in r['Content-Disposition'], 'PDF به صورت دانلود (attachment) ارسال می‌شود')
r = c_pdf.get(f'/baba_adam/request-management/warehouse-pdf/?date={today}')
ok(r.status_code == 200, 'PDF برای کافه دوم هم ساخته می‌شود')

# ---------- پاکسازی ----------
Warehouse.objects.filter(code='TST-MB2').delete()
for pname in (TEST_PRODUCT, TEST_PRODUCT_NB):
    OfficerAssignment.objects.filter(item__name=pname).delete()
    StockTransaction.objects.filter(description__icontains=pname).delete()
    PurchaseItem.objects.filter(name=pname).delete()
    PurchaseRequest.objects.filter(items__name=pname).delete()
    StockItem.objects.filter(product__name=pname).delete()
    ProductBrand.objects.filter(product__name=pname).delete()
    Product.objects.filter(name=pname).delete()

print('DONE')
