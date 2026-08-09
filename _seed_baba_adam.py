# -*- coding: utf-8 -*-
"""Seed realistic demo data for cafe baba_adam:
officers (purchasing/warehouse/dual), requesters in different departments,
20 products, brands + warehouse stock for some of them, and 8 requests (~10 items each).

All seeded users share the password: Baba12345
"""
import os
import sys
import django

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'syntel'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import Client
from purchases.models import (
    Cafe, User, Department, Unit, Category, Product,
    ProductBrand, Warehouse, StockItem, PurchaseRequest,
)

PASSWORD = 'Baba12345'
baba = Cafe.objects.get(code='baba_adam')

# ─────────────────────────── ۱) بخش‌ها ───────────────────────────
dept_names = ['آشپزخانه', 'بار', 'تاسیسات', 'کانتر', 'صندوق']
depts = {}
for n in dept_names:
    d, _ = Department.objects.get_or_create(name=n, cafe=baba, defaults={'is_active': True})
    depts[n] = d
depts['قلیون بار'] = Department.objects.get(name='قلیون بار', cafe=baba)

# بخش کاربران درخواست‌دهندهٔ قدیمی که به بخش سراسری (cafe=None) وصل بودند → بخش کافه
fixed_depts = 0
for u in User.objects.filter(cafe=baba, role='requester'):
    if u.department and u.department.cafe_id is None:
        match = depts.get(u.department.name)
        if match:
            u.department = match
            u.save()
            fixed_depts += 1

# ─────────────────────────── ۲) واحدها و دسته‌ها (بازاستفاده) ───────────────────────────
units = {u.name: u for u in Unit.objects.all()}
cats = {c.name: c for c in Category.objects.filter(cafe=baba)}

# ─────────────────────────── ۳) کاربران ───────────────────────────
# (username, role, secondary_role, full_name, department or None)
users_def = [
    ('baba_po1',            'purchasing', None,        'علی رضایی',   None),  # مسئول خرید ۱
    ('baba_po2',            'purchasing', None,        'سارا محمدی',  None),  # مسئول خرید ۲
    ('baba_wo1',            'warehouse',  None,        'حسین کریمی',  None),  # مسئول انبار ۱
    ('baba_wo2',            'warehouse',  None,        'مریم احمدی',  None),  # مسئول انبار ۲
    ('baba_dual',           'purchasing', 'warehouse', 'رضا موسوی',   None),  # مسئول خرید و انبار
    ('baba_req_kitchen',    'requester',  None,        'امیر حسینی',  'آشپزخانه'),
    ('baba_req_facilities', 'requester',  None,        'جواد عظیمی',  'تاسیسات'),
    ('baba_req_counter',    'requester',  None,        'لیلا شریفی',  'کانتر'),
]

created_users = 0
for uname, role, sec, full_name, dept_name in users_def:
    u, created = User.objects.get_or_create(
        username=uname,
        defaults={'cafe': baba, 'role': role, 'secondary_role': sec,
                  'first_name': full_name.rsplit(' ', 1)[0],
                  'last_name': full_name.rsplit(' ', 1)[1] if ' ' in full_name else ''},
    )
    u.role = role
    u.secondary_role = sec
    u.cafe = baba
    u.department = depts[dept_name] if dept_name else None
    u.is_active = True
    u.set_password(PASSWORD)
    u.save()
    if created:
        created_users += 1

# کاربران درخواست‌دهندهٔ موجود
for uname in ('Mohammad', 'Mehrshad'):
    u = User.objects.get(username=uname)
    u.set_password(PASSWORD)
    u.save()

# ─────────────────────────── ۴) ۲۰ محصول جدید ───────────────────────────
# (name, category_name, unit_name)
products_def = [
    ('روغن سرخ کردنی', 'مواد اولیه', 'لیتر'),
    ('برنج ایرانی',     'مواد اولیه', 'کیلوگرم'),
    ('مرغ کامل',        'گوشت',      'کیلوگرم'),
    ('رب گوجه فرنگی',   'مواد اولیه', 'بسته'),
    ('ماست',            'مواد اولیه', 'کیلوگرم'),
    ('پنیر صبحانه',     'مواد اولیه', 'کیلوگرم'),
    ('تخم مرغ',         'مواد اولیه', 'عدد'),
    ('نان لواش',        'غذای اصلی', 'بسته'),
    ('سیب زمینی',       'سبزیجات',   'کیلوگرم'),
    ('ماکارونی',        'مواد اولیه', 'بسته'),
    ('شکر',             'مواد اولیه', 'کیلوگرم'),
    ('نمک',             'مواد اولیه', 'کیلوگرم'),
    ('فلفل سیاه',       'مواد اولیه', 'گرم'),
    ('زعفران',          'مواد اولیه', 'گرم'),
    ('یخ قالبی',        'نوشیدنی‌ها', 'کارتن'),
    ('نوشابه گازدار',   'نوشیدنی‌ها', 'بطری'),
    ('آب معدنی',        'نوشیدنی‌ها', 'بطری'),
    ('دستمال کاغذی',    'مواد اولیه', 'بسته'),
    ('مایع ظرفشویی',    'مواد اولیه', 'لیتر'),
    ('زغال قلیان',      'مواد اولیه', 'کیلوگرم'),
]

created_products = 0
products = {}
for name, cat_name, unit_name in products_def:
    p, created = Product.objects.get_or_create(
        name=name, cafe=baba,
        defaults={'unit': units[unit_name], 'category': cats.get(cat_name),
                  'usage_location': 'general', 'is_active': True},
    )
    products[name] = p
    if created:
        created_products += 1

# ─────────────────────────── ۵) برندها ───────────────────────────
# product → [(brand_name, trade_unit, conversion_rate)]
brands_def = {
    'روغن سرخ کردنی': [('دمس',   'قوطی', 5.0), ('لادن', 'قوطی', 5.0)],
    'برنج ایرانی':     [('عنبربو', 'کیسه', 10.0)],
    'رب گوجه فرنگی':   [('چین چین', 'قوطی', 0.8)],
    'ماست':            [('کاله',  'ظرف', 2.0)],
    'نوشابه گازدار':   [('کوکاکولا', 'بطری', 1.5), ('زمزم', 'بطری', 1.0)],
    'آب معدنی':        [('دماوند', 'بطری', 1.5)],
}
created_brands = 0
brands = {}  # (product_name, brand_name) → ProductBrand
for pname, bs in brands_def.items():
    for bname, tunit, rate in bs:
        b, created = ProductBrand.objects.get_or_create(
            product=products[pname], brand_name=bname,
            defaults={'trade_unit': tunit, 'conversion_rate': rate},
        )
        brands[(pname, bname)] = b
        if created:
            created_brands += 1

# ─────────────────────────── ۶) انبارها و موجودی ───────────────────────────
wh1, _ = Warehouse.objects.get_or_create(cafe=baba, code='WH1',
                                         defaults={'name': 'انبار مرکزی', 'is_active': True})
wh2, _ = Warehouse.objects.get_or_create(cafe=baba, code='WH2',
                                         defaults={'name': 'سردخانه', 'is_active': True})
whs = {'انبار مرکزی': wh1, 'سردخانه': wh2}

# (product, brand|None, warehouse, qty به واحد پایه)
stock_def = [
    ('روغن سرخ کردنی', 'دمس',      'انبار مرکزی', 125.0),   # ۲۵ قوطی × ۵ لیتر
    ('روغن سرخ کردنی', 'لادن',     'انبار مرکزی', 60.0),    # ۱۲ قوطی × ۵ لیتر
    ('برنج ایرانی',    'عنبربو',   'انبار مرکزی', 300.0),   # ۳۰ کیسه × ۱۰ کیلو
    ('رب گوجه فرنگی',  'چین چین',  'انبار مرکزی', 16.0),    # ۲۰ قوطی × ۰٫۸
    ('ماست',           'کاله',     'سردخانه',    20.0),     # ۱۰ ظرف × ۲ کیلو
    ('تخم مرغ',        None,       'سردخانه',    240.0),    # ۲۴۰ عدد
    ('مرغ کامل',       None,       'سردخانه',    150.0),    # ۱۵۰ کیلوگرم
    ('نوشابه گازدار',  'کوکاکولا', 'انبار مرکزی', 45.0),    # ۳۰ بطری × ۱٫۵
    ('نوشابه گازدار',  'زمزم',     'انبار مرکزی', 20.0),    # ۲۰ بطری × ۱
    ('آب معدنی',       'دماوند',   'انبار مرکزی', 90.0),    # ۶۰ بطری × ۱٫۵
    ('سیب زمینی',      None,       'انبار مرکزی', 200.0),
    ('یخ قالبی',       None,       'سردخانه',    50.0),     # ۵۰ کارتن
    ('شکر',            None,       'انبار مرکزی', 100.0),
]
created_stock = 0
for pname, bname, wh_name, qty in stock_def:
    si, created = StockItem.objects.get_or_create(
        product=products[pname],
        product_brand=brands.get((pname, bname)),
        warehouse=whs[wh_name],
        defaults={'quantity': qty, 'unit': products[pname].unit},
    )
    if created:
        created_stock += 1

# ─────────────────────────── ۷) درخواست‌ها ───────────────────────────
# هر درخواست: (requester_username, [(product_name, qty, desc|None), ...])
requests_def = [
    ('Mehrshad', [   # قلیون بار — درخواست ۱
        ('تنباکو', 500, 'بوی تنباکو - پرتقال'),
        ('زغال قلیان', 20, None),
        ('یخ قالبی', 10, None),
        ('نوشابه گازدار', 24, None),
        ('آب معدنی', 36, None),
        ('دستمال کاغذی', 5, None),
        ('مایع ظرفشویی', 2, None),
        ('نمک', 1, None),
        ('ماست', 5, None),
        ('رب گوجه فرنگی', 2, None),
    ]),
    ('Mehrshad', [   # قلیون بار — درخواست ۲
        ('تنباکو', 300, 'بوی توت فرنگی'),
        ('زغال قلیان', 15, None),
        ('یخ قالبی', 8, None),
        ('نوشابه گازدار', 12, None),
        ('آب معدنی', 24, None),
        ('شکر', 5, None),
        ('چای لاهیجان', 2, None),
        ('قهوه ترک', 1, None),
    ]),
    ('Mohammad', [   # بار — درخواست ۱
        ('نوشابه گازدار', 48, None),
        ('آب معدنی', 48, None),
        ('یخ قالبی', 15, None),
        ('شکر', 10, None),
        ('چای لاهیجان', 1, None),
        ('قهوه ترک', 1, None),
        ('پنیر صبحانه', 3, None),
        ('ماست', 10, None),
        ('تخم مرغ', 30, None),
        ('نان لواش', 10, None),
    ]),
    ('Mohammad', [   # بار — درخواست ۲
        ('نوشابه گازدار', 24, None),
        ('آب معدنی', 24, None),
        ('یخ قالبی', 10, None),
        ('نمک', 1, None),
        ('شکر', 5, None),
        ('دستمال کاغذی', 4, None),
        ('مایع ظرفشویی', 2, None),
        ('فلفل سیاه', 200, None),
        ('زعفران', 50, None),
    ]),
    ('baba_req_kitchen', [   # آشپزخانه — درخواست ۱
        ('روغن سرخ کردنی', 15, None),
        ('برنج ایرانی', 20, None),
        ('مرغ کامل', 25, None),
        ('رب گوجه فرنگی', 5, None),
        ('سیب زمینی', 30, None),
        ('پیاز', 15, None),
        ('گوجه فرنگی', 10, 'ترجیحاً تازه باشد'),
        ('ماکارونی', 8, None),
        ('ماست', 10, None),
        ('پنیر صبحانه', 4, None),
    ]),
    ('baba_req_kitchen', [   # آشپزخانه — درخواست ۲
        ('روغن سرخ کردنی', 10, None),
        ('برنج ایرانی', 15, None),
        ('تخم مرغ', 60, None),
        ('مرغ کامل', 20, None),
        ('سیب زمینی', 20, None),
        ('نمک', 3, None),
        ('فلفل سیاه', 150, None),
        ('زعفران', 25, None),
        ('نان لواش', 12, None),
        ('دستمال کاغذی', 6, None),
    ]),
    ('baba_req_facilities', [   # تاسیسات
        ('مایع ظرفشویی', 4, None),
        ('دستمال کاغذی', 10, None),
        ('شکر', 2, None),
        ('چای لاهیجان', 1, None),
        ('نمک', 1, None),
        ('ماکارونی', 3, None),
        ('روغن سرخ کردنی', 5, None),
        ('برنج ایرانی', 5, None),
        ('رب گوجه فرنگی', 2, None),
        ('تخم مرغ', 20, None),
    ]),
    ('baba_req_counter', [   # کانتر
        ('دستمال کاغذی', 6, None),
        ('مایع ظرفشویی', 2, None),
        ('نوشابه گازدار', 24, None),
        ('آب معدنی', 36, None),
        ('نان لواش', 8, None),
        ('یخ قالبی', 5, None),
        ('شکر', 3, None),
        ('چای لاهیجان', 1, None),
        ('قهوه ترک', 1, None),
        ('ماست', 4, None),
    ]),
]

created_requests = 0
for uname, items in requests_def:
    requester = User.objects.get(username=uname)
    req = PurchaseRequest.objects.create(user=requester, cafe=baba, status='pending_manager_approval')
    for pname, qty, desc in items:
        p = products.get(pname) or Product.objects.get(name=pname, cafe=baba)
        req.items.create(product=p, name=p.name, quantity=qty,
                         unit=p.unit.name if p.unit else '', description=desc or '')
    created_requests += 1

# ─────────────────────────── ۸) بررسی صفحات ───────────────────────────
admin = User.objects.filter(username='admin').first()
c = Client()
c.force_login(admin)
r1 = c.get('/baba_adam/all-departments-requests/')
r2 = c.get('/baba_adam/warehouse-officer/dashboard/')
r3 = c.get('/baba_adam/purchase-officer/dashboard/')

# ─────────────────────────── خلاصه ───────────────────────────
print('=== خلاصه سید دیتا (بابا آدم) ===')
print('بخش‌ها:', [d.name for d in Department.objects.filter(cafe=baba)])
print('کاربران جدید:', created_users, '| رفع بخش کاربران قدیمی:', fixed_depts)
print('مسئول خرید:', [u.username for u in User.objects.filter(cafe=baba, role='purchasing')])
print('مسئول انبار:', [u.username for u in User.objects.filter(cafe=baba, role='warehouse')])
print('مسئول خرید+انبار:', [u.username for u in User.objects.filter(cafe=baba, secondary_role='warehouse')])
print('درخواست‌دهنده:', [(u.username, u.department.name if u.department else None)
                          for u in User.objects.filter(cafe=baba, role='requester')])
print('محصولات جدید:', created_products, '| کل محصولات کافه:', Product.objects.filter(cafe=baba).count())
print('برندهای جدید:', created_brands)
print('موجودی انبار جدید:', created_stock)
print('درخواست‌های جدید:', created_requests)
for u in User.objects.filter(cafe=baba, role='requester'):
    n = u.requests.count()
    items = sum(r.items.count() for r in u.requests.all())
    print(f'  {u.username} ({u.department.name if u.department else "-"}): {n} درخواست، {items} قلم')
print('صفحات (لاگین ادمین):',
      'CEO:', r1.status_code, '| انبار:', r2.status_code, '| خرید:', r3.status_code)
print('رمز همه کاربران سیدشده:', PASSWORD)
