# -*- coding: utf-8 -*-
"""دیباگ بخش ۱۱: انتخاب دیفالت «بدون برند»"""
import sys, os
sys.path.insert(0, 'D:/Syntel/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
import django; django.setup()
from django.test import Client
from django.contrib.auth import get_user_model
from decimal import Decimal
from purchases.models import (Cafe, Unit, Product, ProductBrand, Warehouse, StockItem,
                              PurchaseRequest, PurchaseItem, OfficerAssignment, StockTransaction)

User = get_user_model()
pname = 'قند بدون برند تست'
OfficerAssignment.objects.filter(item__name=pname).delete()
StockTransaction.objects.filter(description__icontains=pname).delete()
PurchaseItem.objects.filter(name=pname).delete()
PurchaseRequest.objects.filter(items__name=pname).delete()
StockItem.objects.filter(product__name=pname).delete()
ProductBrand.objects.filter(product__name=pname).delete()
Product.objects.filter(name=pname).delete()

cafe = Cafe.objects.filter(code='local').first()
unit_kg, _ = Unit.objects.get_or_create(name='کیلوگرم')
wh, _ = Warehouse.objects.get_or_create(cafe=cafe, code='TST-MB')
prod_nb, _ = Product.objects.get_or_create(name=pname, cafe=cafe, defaults={'unit': unit_kg})
si_nb, _ = StockItem.objects.get_or_create(product=prod_nb, product_brand=None, warehouse=wh,
                                           defaults={'quantity': Decimal('10'), 'unit': unit_kg})
si_nb.quantity = Decimal('10'); si_nb.save()
requester, _ = User.objects.get_or_create(username='test_req_mb')
requester.role = 'kitchen'; requester.cafe = cafe; requester.save()
req = PurchaseRequest.objects.create(cafe=cafe, user=requester, status='pending')
item = PurchaseItem.objects.create(request=req, product=prod_nb, name=pname, quantity=5, unit='کیلوگرم')
officer, _ = User.objects.get_or_create(username='test_wh_mb')
officer.role = 'warehouse'; officer.cafe = cafe; officer.save()
c = Client(); c.force_login(officer)
body = c.get('/local/warehouse-officer/dashboard/').content.decode('utf-8', errors='replace')
print('selected check:', f'value="{si_nb.id}" selected' in body)
i = body.find('rows-%d' % item.id)
print(body[i:i+900])
