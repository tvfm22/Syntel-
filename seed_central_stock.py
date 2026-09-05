# -*- coding: utf-8 -*-
"""Seed یک‌باره: انبار مرکزی + موجودی تصادفی ۱۰۰ تا ۳۰۰ برای همهٔ محصولات.

ایجاد «انبار مرکزی» برای هر کافهٔ فعال (اگر نبود) و سپس برای همهٔ محصولاتِ
فعال همان کافه، یک StockItem با مقدار تصادفی در بازهٔ [100, 300] در آن انبار.
اجرای مجدد idempotent است: انبار و موجودیِ موجود دست نمی‌خورند.
"""
import os
import random
import sys
from decimal import Decimal

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'syntel'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
os.environ.setdefault('DATA_PATH', os.path.join(BASE, 'data'))

import django  # noqa: E402

django.setup()

from purchases.models import Cafe, Product, StockItem, Warehouse  # noqa: E402

LOW, HIGH = 100, 300

for cafe in Cafe.objects.filter(is_active=True):
    warehouse, wh_created = Warehouse.objects.get_or_create(
        cafe=cafe, code='CENTRAL',
        defaults={
            'name': 'انبار مرکزی',
            'description': 'انبار مرکزی اصلی (seed خودکار)',
            'is_active': True,
            'is_supply_enabled': True,
        },
    )
    print(f'[{cafe.code}] انبار مرکزی: {"ساخته شد" if wh_created else "موجود بود"} #{warehouse.id}')

    products = Product.objects.filter(cafe=cafe, is_active=True)
    made, skipped = 0, 0
    for p in products:
        # بدون برند — برندهای این DB فعلاً خالی‌اند؛ unique_together (product, brand, warehouse)
        si, created = StockItem.objects.get_or_create(
            product=p, product_brand=None, warehouse=warehouse,
            defaults={
                'quantity': Decimal(random.randint(LOW, HIGH)),
                'unit': p.unit,
                'min_quantity': Decimal('10'),
            },
        )
        if created:
            made += 1
        else:
            skipped += 1
    print(f'[{cafe.code}] StockItem جدید: {made} | از قبل موجود: {skipped}')

print('DONE')
