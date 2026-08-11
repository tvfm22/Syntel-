# -*- coding: utf-8 -*-
"""ساخت یک PDF نمونه از درخواست کالا از انبار برای بازبینی بصری"""
import sys, os
sys.path.insert(0, 'D:/Syntel/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from decimal import Decimal
from purchases.models import (
    Cafe, Unit, Product, ProductBrand, Warehouse, StockItem,
    PurchaseRequest, PurchaseItem, OfficerAssignment, StockTransaction,
)

User = get_user_model()
cafe = Cafe.objects.filter(code='local').first()
unit_kg, _ = Unit.objects.get_or_create(name='کیلوگرم')
wh, _ = Warehouse.objects.get_or_create(cafe=cafe, code='PVW', defaults={'name': 'انبار پیشنمایش'})

# پاکسازی دادههای پیشنمایش قبلی (اجرای مکرر نباید ردیفهای PDF را جمع کند)
OfficerAssignment.objects.filter(item__name__startswith='پیشنمایش').delete()
StockTransaction.objects.filter(description__icontains='پیشنمایش').delete()
PurchaseItem.objects.filter(name__startswith='پیشنمایش').delete()
PurchaseRequest.objects.filter(cafe=cafe, items__isnull=True).delete()
for p in Product.objects.filter(cafe=cafe, name__startswith='پیشنمایش'):
    StockItem.objects.filter(product=p).delete()
    ProductBrand.objects.filter(product=p).delete()
    p.delete()

names = ['شکر', 'روغن', 'برنج', 'چای', 'قهوه', 'ماکارونی', 'رب گوجه', 'پنیر', 'کره', 'تخممرغ', 'نمک', 'ادویه']
products = []
for n in names:
    p, _ = Product.objects.get_or_create(name=f'پیشنمایش {n}', cafe=cafe, defaults={'unit': unit_kg})
    products.append(p)

# برندها: محصول ۲ (روغن) سه برند، بقیه تکبرند
for idx, p in enumerate(products):
    if idx == 1:
        for bname in ['برند دمس', 'برند لادن', 'برند غنچه']:
            b, _ = ProductBrand.objects.get_or_create(product=p, brand_name=bname)
            si, _ = StockItem.objects.get_or_create(
                product=p, product_brand=b, warehouse=wh, defaults={'quantity': Decimal('50'), 'unit': unit_kg})
            si.quantity = Decimal('50'); si.save()
    else:
        b, _ = ProductBrand.objects.get_or_create(product=p, brand_name=f'برند {n}')
        si, _ = StockItem.objects.get_or_create(
            product=p, product_brand=b, warehouse=wh, defaults={'quantity': Decimal('30'), 'unit': unit_kg})
        si.quantity = Decimal('30'); si.save()

req = PurchaseRequest.objects.create(cafe=cafe, user=User.objects.first(), status='pending')
from purchases.models import OfficerAssignment, StockTransaction
officer, _ = User.objects.get_or_create(username='test_wh_pv')
officer.role = 'warehouse'; officer.cafe = cafe; officer.save()

for idx, p in enumerate(products):
    it = PurchaseItem.objects.create(request=req, product=p, name=p.name, quantity=10, unit='کیلوگرم')
    si = StockItem.objects.filter(product=p, warehouse=wh).first()
    if not si:
        continue
    brand = si.product_brand
    # تأمین: روغن از ۳ برند (۲+۳+۵)، بقیه از یک برند
    if idx == 1:
        for i, si2 in enumerate(StockItem.objects.filter(product=p, warehouse=wh)):
            a = OfficerAssignment.objects.create(
                item=it, target_role='warehouse', status='fulfilled',
                assigned_to=officer, assigned_by=officer, stock_item=si2,
                supplied_quantity=Decimal(str([2, 3, 5][i])))
            StockTransaction.objects.create(
                stock_item=si2, transaction_type='out', quantity=Decimal(str([2, 3, 5][i])),
                unit=unit_kg, reference_type='officer_assignment', reference_id=it.id,
                description=f'تامین - {it.name}',
                previous_quantity=Decimal('50'), new_quantity=Decimal('50'))
    else:
        a = OfficerAssignment.objects.create(
            item=it, target_role='warehouse', status='fulfilled',
            assigned_to=officer, assigned_by=officer, stock_item=si,
            supplied_quantity=Decimal('10'))
        StockTransaction.objects.create(
            stock_item=si, transaction_type='out', quantity=Decimal('10'),
            unit=unit_kg, reference_type='officer_assignment', reference_id=it.id,
            description=f'تامین - {it.name}',
            previous_quantity=Decimal('30'), new_quantity=Decimal('30'))

admin, _ = User.objects.get_or_create(username='test_admin_pv')
admin.role = 'admin'; admin.is_staff = True; admin.save()
c = Client(); c.force_login(admin)
r = c.get('/local/request-management/warehouse-pdf/?date=2026-08-11')
with open(r'D:\Syntel\preview_warehouse.pdf', 'wb') as f:
    f.write(r.content)
print('saved', len(r.content), 'bytes →', r['Content-Disposition'])
