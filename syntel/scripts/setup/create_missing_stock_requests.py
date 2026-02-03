#!/usr/bin/env python
"""
اسکریپت ایجاد StockRequest برای PurchaseItem های فاقد StockRequest
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PurchaseItem, StockRequest, User
from django.db.models import Count

def create_missing_stock_requests():
    """ایجاد StockRequest برای آیتم‌های بدون StockRequest"""
    print("🔍 جستجوی PurchaseItem های بدون StockRequest...")
    
    # یافتن آیتم‌هایی که supply_status='from_stock' دارند اما StockRequest ندارند
    items_without_request = PurchaseItem.objects.filter(
        supply_status='from_stock'
    ).exclude(
        id__in=StockRequest.objects.values_list('purchase_item_id', flat=True)
    ).select_related('request__user', 'request__processed_by')
    
    total = items_without_request.count()
    print(f"📦 تعداد آیتم‌های بدون StockRequest: {total}")
    
    if total == 0:
        print("✅ همه آیتم‌ها StockRequest دارند!")
        return
    
    # یافتن کاربر purchasing
    purchasing_user = User.objects.filter(role='purchasing').first()
    if not purchasing_user:
        print("❌ کاربر purchasing یافت نشد!")
        return
    
    print(f"\n🔨 شروع ایجاد {total} StockRequest...\n")
    
    created = 0
    errors = 0
    
    for item in items_without_request:
        try:
            requested_by = item.request.processed_by if item.request.processed_by else purchasing_user
            
            stock_request = StockRequest.objects.create(
                purchase_item=item,
                requested_by=requested_by,
                status='pending',
                notes=f'ایجاد شده برای آیتم #{item.id}'
            )
            created += 1
            
            if created <= 10 or created % 25 == 0:
                print(f"  ✅ #{stock_request.id}: '{item.name}' (آیتم #{item.id}) - بخش: {item.request.user.role}")
                
        except Exception as e:
            errors += 1
            print(f"  ❌ خطا برای آیتم #{item.id}: {str(e)}")
    
    print(f"\n{'='*70}")
    print(f"✅ StockRequest های ایجاد شده: {created}")
    print(f"❌ خطاها: {errors}")
    print(f"📊 مجموع StockRequest ها: {StockRequest.objects.count()}")
    print(f"{'='*70}\n")
    
    # آمار نهایی
    print("📈 آمار نهایی:")
    statuses = StockRequest.objects.values('status').annotate(count=Count('id'))
    for s in statuses:
        print(f"  - {s['status']}: {s['count']}")

if __name__ == '__main__':
    create_missing_stock_requests()
