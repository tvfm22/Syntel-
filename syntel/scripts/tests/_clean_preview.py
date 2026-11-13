# -*- coding: utf-8 -*-
"""پاکسازی دادههای پیشنمایش PDF از دیتابیس توسعه"""
import sys, os
sys.path.insert(0, 'D:/Syntel/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
import django; django.setup()
from purchases.models import (Cafe, Product, Warehouse, StockItem,
                              PurchaseRequest, PurchaseItem, OfficerAssignment, StockTransaction)

cafe = Cafe.objects.filter(code='local').first()
OfficerAssignment.objects.filter(item__name__startswith='پیشنمایش').delete()
StockTransaction.objects.filter(description__icontains='پیشنمایش').delete()
PurchaseItem.objects.filter(name__startswith='پیشنمایش').delete()
PurchaseRequest.objects.filter(cafe=cafe, items__isnull=True).delete()
for p in Product.objects.filter(cafe=cafe, name__startswith='پیشنمایش'):
    StockItem.objects.filter(product=p).delete()
    p.delete()
Warehouse.objects.filter(code='PVW').delete()
print('preview data cleaned')
