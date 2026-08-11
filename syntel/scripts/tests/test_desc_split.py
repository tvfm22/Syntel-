# -*- coding: utf-8 -*-
"""تست ادغام کالاهای مشترک بین بخش‌ها (داشبورد مدیرعامل)

- صفحه مدیرعامل (all_departments_requests): کالایی که چند بخش درخواست‌اش را کرده‌اند
  در یک ردیف نمایش داده می‌شود؛ در همان ردیف تگ هر بخش (نام بخش + مقدار) و مجموع
  کل کالا (جمع کل) حساب می‌شود. توضیحات متفاوت دیگر ردیف‌ها را جدا نمی‌کند.
- برای هر بخش فقط یک «ویرایش» و یک «حذف» وجود دارد (نه به ازای هر قلم):
  - ویرایش در همان صفحه پنل باز می‌کند و با وارد کردن مقدار جدید و تأیید،
    قلم‌های بخش به یک قلم با مقدار جدید تبدیل می‌شوند.
  - حذف، همه قلم‌های ارسال‌نشده همان بخش را حذف می‌کند.
- لیست خرید مسئول خرید (purchase_officer_dashboard): اقلام همنام با توضیحات متفاوت
  همچنان به‌عنوان گزینه‌های جدا خریداری می‌شوند (هر گزینه جدا).

اجرا: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe syntel/scripts/tests/test_desc_split.py
"""
import sys, os, re
sys.path.insert(0, 'D:/Syntel/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from purchases.models import Cafe, Department, Product, PurchaseRequest, PurchaseItem, OfficerAssignment

User = get_user_model()
ok = lambda cond, msg: print(('PASS' if cond else 'FAIL') + ' - ' + msg)

# ---------- پاک‌سازی داده تست ----------
USERNAMES = ['test_desc_ceo', 'test_desc_kitchen', 'test_desc_bar', 'test_desc_officer']
OfficerAssignment.objects.filter(item__request__user__username__in=USERNAMES).delete()
PurchaseRequest.objects.filter(user__username__in=USERNAMES).delete()
User.objects.filter(username__in=USERNAMES).delete()
Product.objects.filter(name='شکر تست').delete()

cafe = Cafe.objects.filter(code='local').first() or Cafe.objects.create(name='کافه محلی', code='local')
dept_kitchen = Department.objects.filter(name='آشپزخانه', cafe=cafe).first() or Department.objects.create(name='آشپزخانه', cafe=cafe)
dept_bar = Department.objects.filter(name='بار', cafe=cafe).first() or Department.objects.create(name='بار', cafe=cafe)

ceo = User.objects.create_user(username='test_desc_ceo', password='x', role='ceo', cafe=cafe)
kitchen = User.objects.create_user(username='test_desc_kitchen', password='x', role='requester', cafe=cafe, department=dept_kitchen)
bar = User.objects.create_user(username='test_desc_bar', password='x', role='requester', cafe=cafe, department=dept_bar)
officer = User.objects.create_user(username='test_desc_officer', password='x', role='purchasing', cafe=cafe)

product = Product.objects.create(name='شکر تست', cafe=cafe)

def add_item(req, qty, desc):
    return PurchaseItem.objects.create(
        request=req, product=product, name=product.name, quantity=qty, unit='کیلو', description=desc,
    )

def make_request(user):
    return PurchaseRequest.objects.create(user=user, cafe=cafe, status='pending')

# ── سناریو ──
# آشپزخانه: «شکر» ×۵ و ×۲ (توضیح «پنج کیلویی») + ×۶ بدون توضیح → تگ ۱۳
# بار:       «شکر» ×۳ (توضیح «یک کیلویی») + ×۴ بدون توضیح → تگ ۷
# همه در یک ردیف: جمع کل ۲۰
r1 = make_request(kitchen); i1 = add_item(r1, 5, 'پنج کیلویی')
r2 = make_request(kitchen); i2 = add_item(r2, 2, 'پنج کیلویی')
r3 = make_request(bar);    i3 = add_item(r3, 3, 'یک کیلویی')
r4 = make_request(bar);    i4 = add_item(r4, 4, '')
r5 = make_request(kitchen); i5 = add_item(r5, 6, '')

all_ids = {str(x.id) for x in (i1, i2, i3, i4, i5)}
kitchen_ids = ','.join(str(x.id) for x in (i1, i2, i5))   # آشپزخانه در ردیف: i1,i2,i5
bar_ids = ','.join(str(x.id) for x in (i3, i4))           # بار: i3,i4

c = Client(); c.force_login(ceo)
URL = '/local/all-departments-requests/?type=purchase'

# ---------- ۱) صفحه مدیرعامل: یک ردیف ادغام‌شده با تگ بخش و جمع کل ----------
r = c.get(URL)
ok(r.status_code == 200, f'مدیرعامل: صفحه درخواست کالا → {r.status_code}')
body = r.content.decode('utf-8')

trs = re.findall(r'<tr class="([^"]*)">(.*?)</tr>', body, re.S)
my_trs = [t for cls, t in trs if 'شکر تست' in t]
ok(len(my_trs) == 1, f'همه درخواست‌های «شکر تست» (با هر توضیحی) در یک ردیف → {len(my_trs)}')
t = my_trs[0]

def row_qty(tt):
    m = re.search(r'class="qty-cell">([^<]*)<', tt)
    return m.group(1) if m else '?'

def row_section_tags(tt):
    return re.findall(r'class="section-dept">([^<]*)<', tt), \
           re.findall(r'class="section-qty">([^<]*)<', tt)

ok(row_qty(t) == '20 کیلو', f'جمع کل ردیف = ۲۰ کیلو (۵+۲+۳+۴+۶) → {row_qty(t)}')
depts, qtys = row_section_tags(t)
ok(depts == ['آشپزخانه', 'بار'], f'تگ بخش‌های ردیف → {depts}')
ok(qtys == ['13 کیلو', '7 کیلو'], f'مقدار هر تگ: آشپزخانه ۱۳ و بار ۷ → {qtys}')

# یک باکس انتخاب برای کل کالا (نه یکی برای هر بخش) — همه قلم‌های همه بخش‌ها با هم
chk_m = re.search(r'class="item-chk" value="([^"]*)"', t)
chk_ids = set(chk_m.group(1).split(',')) if chk_m else set()
ok(t.count('class="item-chk"') == 1, 'فقط یک باکس انتخاب برای کل کالا (به ازای هر بخش باکس جدا نیست)')
ok(chk_ids == all_ids, f'باکس انتخاب همه قلم‌های هر دو بخش را با هم دارد → {sorted(chk_ids) if chk_m else "—"}')

# به ازای هر بخش فقط یک «ویرایش» و یک «حذف» (نه برای هر قلم)
ok(t.count('value="delete_section"') == 2, f'فقط یک دکمه حذف برای هر بخش (۲ بخش) → {t.count("value=\"delete_section\"")}')
ok(t.count('class="mini-link ml-edit"') == 2, f'فقط یک دکمه ویرایش برای هر بخش (۲ بخش) → {t.count("class=\"mini-link ml-edit\"")}')
ok(t.count('class="inline-edit-form"') == 2, 'پنل ویرایش درجا برای هر بخش هست')
ok('value="13"' in t and 'value="7"' in t, 'مقدار فعلی هر بخش در پنل ویرایش از قبل پر شده')

# ---------- ۲) ویرایش درجا: مقدار آشپزخانه به ۹ تغییر می‌کند ----------
r = c.post(URL, {'action': 'edit_section', 'item_ids': kitchen_ids, 'new_qty': '9'})
ok(r.status_code == 302, f'ویرایش مقدار بخش → ریدایرکت {r.status_code}')

body2 = c.get(URL).content.decode('utf-8')
trs2 = re.findall(r'<tr class="([^"]*)">(.*?)</tr>', body2, re.S)
t2 = [x for cls, x in trs2 if 'شکر تست' in x][0]
ok(row_qty(t2) == '16 کیلو', f'جمع کل بعد از ویرایش = ۱۶ کیلو (۹+۳+۴) → {row_qty(t2)}')
d2, q2 = row_section_tags(t2)
ok(d2 == ['آشپزخانه', 'بار'] and q2 == ['9 کیلو', '7 کیلو'],
   f'تگ‌ها بعد از ویرایش: آشپزخانه ۹ و بار ۷ → {q2}')
left = PurchaseItem.objects.filter(product=product).count()
ok(left == 3, f'قلم‌های بخش ادغام‌شده: سه قلم مانده (۹+۳+۴) → {left}')
ok(not PurchaseRequest.objects.filter(id=r2.id).exists() and not PurchaseRequest.objects.filter(id=r5.id).exists(),
   'درخواست‌های خالی بعد از ادغام حذف شدند')

# ---------- ۳) حذف بخش: کل بار حذف می‌شود ----------
r = c.post(URL, {'action': 'delete_section', 'item_ids': bar_ids})
ok(r.status_code == 302, f'حذف بخش بار → ریدایرکت {r.status_code}')

body3 = c.get(URL).content.decode('utf-8')
trs3 = re.findall(r'<tr class="([^"]*)">(.*?)</tr>', body3, re.S)
t3 = [x for cls, x in trs3 if 'شکر تست' in x][0]
ok(row_qty(t3) == '9 کیلو', f'جمع کل بعد از حذف بار = ۹ کیلو → {row_qty(t3)}')
d3, q3 = row_section_tags(t3)
ok(d3 == ['آشپزخانه'] and q3 == ['9 کیلو'], f'فقط تگ آشپزخانه مانده → {d3} {q3}')
left = PurchaseItem.objects.filter(product=product).count()
ok(left == 1, f'فقط قلم آشپزخانه (۹ کیلو) مانده → {left}')

# ---------- ۴) ارسال: ردیف کامل کمرنگ و باکس غیرفعال می‌شود ----------
r = c.post(URL, {'action': 'send', 'item_ids': str(i1.id), 'officer_user_id': str(officer.id)})
ok(r.status_code == 302, f'ارسال قلم باقی‌مانده → ریدایرکت {r.status_code}')
body4 = c.get(URL).content.decode('utf-8')
faded = [x for cls, x in re.findall(r'<tr class="([^"]*)">(.*?)</tr>', body4, re.S)
         if 'شکر تست' in x and 'row-faded' in cls]
ok(len(faded) == 1, 'بعد از ارسال همه قلم‌ها ردیف کمرنگ می‌شود')
ok('checked disabled' in (faded[0] if faded else ''), 'باکس ردیف ارسال‌شده کامل غیرفعال است')

# ---------- ۵) لیست خرید مسئول: گزینه با مقدار ویرایش‌شده ----------
co = Client(); co.force_login(officer)
r = co.get('/local/purchase-officer/dashboard/')
ok(r.status_code == 200, f'مسئول خرید: داشبورد → {r.status_code}')
ob = r.content.decode('utf-8')
cards = re.findall(r'class="row-name">(شکر تست)</div>\s*<div class="row-qty">جمع: ([^<]*)<', ob)
ok(cards == [('شکر تست', '9 کیلو')], f'کارت مسئول خرید: شکر ۹ کیلو → {cards}')
ok(ob.count('📝 پنج کیلویی') == 1, 'توضیح قلم ارسال‌شده در لیست مسئول هست')

print('--- پایان تست ---')
