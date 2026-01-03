#!/usr/bin/env python
"""
اسکریپت حذف و ایجاد مجدد StockRequest ها
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PurchaseItem, StockRequest, User
from django.db.models import Q

def reset_stock_requests():
    """حذف و ایجاد مجدد StockRequest ها"""
    print("🔍 بررسی وضعیت فعلی...")
    
    # شمارش StockRequest های موجود
    existing_count = StockRequest.objects.count()
    print(f"📊 تعداد StockRequest های موجود: {existing_count}")
    
    # شمارش PurchaseItem های with supply_status='from_stock'
    items_count = PurchaseItem.objects.filter(supply_status='from_stock').count()
    print(f"📦 تعداد PurchaseItem با supply_status='from_stock': {items_count}")
    
    # حذف تمام StockRequest های موجود
    if existing_count > 0:
        print(f"\n🗑️  حذف {existing_count} StockRequest موجود...")
        StockRequest.objects.all().delete()
        print("✅ همه StockRequest ها حذف شدند")
    
    # ایجاد مجدد StockRequest ها
    print(f"\n🔨 ایجاد مجدد StockRequest برای {items_count} آیتم...")
    
    items_without_stock_request = PurchaseItem.objects.filter(
        supply_status='from_stock'
    ).select_related('request__user', 'request__processed_by')
    
    # یافتن کاربر purchasing
    purchasing_user = User.objects.filter(role='purchasing').first()
    if not purchasing_user:
        print("❌ کاربر purchasing یافت نشد!")
        return
    
    created_count = 0
    errors = 0
    
    for item in items_without_stock_request:
        try:
            # استفاده از کاربری که درخواست را پردازش کرده
            requested_by = item.request.processed_by if item.request.processed_by else purchasing_user
            
            stock_request = StockRequest.objects.create(
                purchase_item=item,
                requested_by=requested_by,
                status='pending',
                notes=f'ایجاد شده توسط اسکریپت reset برای آیتم #{item.id}'
            )
            created_count += 1
            
            if created_count <= 10 or created_count % 50 == 0:
                print(f"  ✅ StockRequest #{stock_request.id} برای '{item.name}' (آیتم #{item.id})")
                
        except Exception as e:
            errors += 1
            print(f"  ❌ خطا برای آیتم #{item.id}: {str(e)}")
    
    print(f"\n{'='*60}")
    print(f"✅ StockRequest های ایجاد شده: {created_count}")
    print(f"❌ خطاها: {errors}")
    print(f"📊 مجموع StockRequest ها در دیتابیس: {StockRequest.objects.count()}")
    print(f"{'='*60}")
    
    # نمایش آمار وضعیت‌ها
    print(f"\n📈 آمار وضعیت StockRequest ها:")
    statuses = StockRequest.objects.values('status').annotate(count=models.Count('id'))
    for status in statuses:
        print(f"  - {status['status']}: {status['count']}")
    
    # نمایش آمار بخش‌ها
    print(f"\n🏢 آمار بخش‌های درخواست‌دهنده:")
    from django.db.models import Count
    departments = StockRequest.objects.values(
        'purchase_item__request__user__role'
    ).annotate(
        count=Count('id')
    ).order_by('-count')
    
    for dept in departments:
        role = dept['purchase_item__request__user__role']
        count = dept['count']
        print(f"  - {role}: {count}")

if __name__ == '__main__':
    from django.db import models
    reset_stock_requests()
