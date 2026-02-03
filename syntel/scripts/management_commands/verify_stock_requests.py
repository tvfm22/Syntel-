#!/usr/bin/env python
"""
اسکریپت بررسی نهایی StockRequest ها
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PurchaseItem, StockRequest
from django.db.models import Count

print("=" * 70)
print("📊 گزارش نهایی StockRequest ها")
print("=" * 70)

# شمارش کل
total_stock_requests = StockRequest.objects.count()
print(f"\n✅ مجموع StockRequest ها: {total_stock_requests}")

# آمار وضعیت
print(f"\n📈 آمار وضعیت:")
statuses = StockRequest.objects.values('status').annotate(count=Count('id')).order_by('-count')
for status in statuses:
    print(f"  - {status['status']}: {status['count']}")

# آمار بخش‌ها
print(f"\n🏢 آمار بخش‌های درخواست‌دهنده:")
departments = StockRequest.objects.values(
    'purchase_item__request__user__role'
).annotate(
    count=Count('id')
).order_by('-count')

for dept in departments:
    role = dept['purchase_item__request__user__role']
    count = dept['count']
    print(f"  - {role}: {count}")

# آمار PurchaseItem ها
print(f"\n📦 آمار PurchaseItem ها:")
total_from_stock = PurchaseItem.objects.filter(supply_status='from_stock').count()
items_with_request = PurchaseItem.objects.filter(
    supply_status='from_stock',
    stock_requests__isnull=False
).distinct().count()

print(f"  - PurchaseItem با supply_status='from_stock': {total_from_stock}")
print(f"  - PurchaseItem دارای StockRequest: {items_with_request}")
print(f"  - PurchaseItem بدون StockRequest: {total_from_stock - items_with_request}")

# نمونه StockRequest های اخیر
print(f"\n📋 نمونه StockRequest های اخیر (5 مورد اول):")
recent_requests = StockRequest.objects.select_related(
    'purchase_item__request__user',
    'requested_by'
).order_by('-id')[:5]

for req in recent_requests:
    print(f"  ✅ ID:{req.id} | آیتم:'{req.purchase_item.name}' | بخش:{req.purchase_item.request.user.role} | وضعیت:{req.status}")

print("\n" + "=" * 70)
print("✅ بررسی کامل شد!")
print("=" * 70)
