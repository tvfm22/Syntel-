#!/usr/bin/env python
"""
ایجاد داده‌های نمونه برای کافه بابا آدم
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Category, Product, Supplier, Unit, Cafe
from decimal import Decimal

baba_adam = Cafe.objects.get(code='baba_adam')

# ایجاد دسته‌بندی‌های پایه برای بابا آدم
categories_data = [
    ('نوشیدنی‌ها', None),
    ('غذای اصلی', None),
    ('مواد اولیه', None),
    ('دسر', None),
    ('قهوه', 'نوشیدنی‌ها'),
    ('چای', 'نوشیدنی‌ها'),
    ('گوشت', 'مواد اولیه'),
    ('سبزیجات', 'مواد اولیه'),
]

created_categories = {}
for name, parent_name in categories_data:
    parent = created_categories.get(parent_name) if parent_name else None
    cat, created = Category.objects.get_or_create(
        name=name,
        cafe=baba_adam,
        defaults={'parent': parent}
    )
    created_categories[name] = cat
    if created:
        print(f'✅ دسته‌بندی "{name}" ایجاد شد')

# واحدها
unit_kg, _ = Unit.objects.get_or_create(name='کیلوگرم', defaults={'symbol': 'kg', 'cafe': baba_adam})
unit_liter, _ = Unit.objects.get_or_create(name='لیتر', defaults={'symbol': 'L', 'cafe': baba_adam})
unit_piece, _ = Unit.objects.get_or_create(name='عدد', defaults={'symbol': 'عدد', 'cafe': baba_adam})

# ایجاد محصولات نمونه برای بابا آدم
products_data = [
    ('قهوه ترک', 'قهوه', unit_kg),
    ('چای لاهیجان', 'چای', unit_kg),
    ('گوشت گوسفندی', 'گوشت', unit_kg),
    ('گوجه فرنگی', 'سبزیجات', unit_kg),
    ('پیاز', 'سبزیجات', unit_kg),
]

for name, cat_name, unit in products_data:
    cat = created_categories.get(cat_name)
    prod, created = Product.objects.get_or_create(
        name=name,
        cafe=baba_adam,
        defaults={'category': cat, 'unit': unit}
    )
    if created:
        print(f'✅ محصول "{name}" ایجاد شد')

# ایجاد تأمین‌کنندگان نمونه برای بابا آدم
suppliers_data = [
    ('تأمین‌کننده گوشت بابا آدم', 'butcher'),
    ('سبزی‌فروشی مرکزی', 'grocery'),
    ('پخش قهوه و چای', 'wholesale'),
]

for name, stype in suppliers_data:
    sup, created = Supplier.objects.get_or_create(
        name=name,
        cafe=baba_adam,
        defaults={'supplier_type': stype}
    )
    if created:
        print(f'✅ تأمین‌کننده "{name}" ایجاد شد')

print()
print('=== خلاصه داده‌ها ===')
from collections import Counter
print(f'Categories: {dict(Counter(Category.objects.values_list("cafe__code", flat=True)))}')
print(f'Products: {dict(Counter(Product.objects.values_list("cafe__code", flat=True)))}')
print(f'Suppliers: {dict(Counter(Supplier.objects.values_list("cafe__code", flat=True)))}')
