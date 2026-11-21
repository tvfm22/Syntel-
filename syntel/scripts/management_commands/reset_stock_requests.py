#!/usr/bin/env python
"""
اسکریپت حذف و ایجاد مجدد StockRequest ها (نیازمند --confirm-destructive)
"""
import argparse
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')


def reset_stock_requests(confirm_destructive: bool = False):
    """حذف و ایجاد مجدد StockRequest ها در قالب تراکنش اتمیک"""
    if not confirm_destructive:
        raise RuntimeError("This script deletes all StockRequest rows; pass --confirm-destructive to proceed.")

    django.setup()
    from django.db import models, transaction
    from django.db.models import Count
    from purchases.models import PurchaseItem, StockRequest, User

    print("🔍 بررسی وضعیت فعلی...")
    existing_count = StockRequest.objects.count()
    print(f"📊 تعداد StockRequest های موجود: {existing_count}")

    items_count = PurchaseItem.objects.filter(supply_status='from_stock').count()
    print(f"📦 تعداد PurchaseItem با supply_status='from_stock': {items_count}")

    purchasing_user = User.objects.filter(role='purchasing').first()
    if not purchasing_user:
        print("❌ کاربر purchasing یافت نشد!")
        return

    with transaction.atomic():
        if existing_count > 0:
            print(f"\n🗑️  حذف {existing_count} StockRequest موجود...")
            StockRequest.objects.all().delete()
            print("✅ همه StockRequest ها حذف شدند")

        print(f"\n🔨 ایجاد مجدد StockRequest برای {items_count} آیتم...")
        items_without_stock_request = PurchaseItem.objects.filter(
            supply_status='from_stock'
        ).select_related('request__user', 'request__processed_by')

        created_count = 0
        for item in items_without_stock_request:
            requested_by = item.request.processed_by if item.request.processed_by else purchasing_user
            StockRequest.objects.create(
                purchase_item=item,
                requested_by=requested_by,
                status='pending',
                notes=f'ایجاد شده توسط اسکریپت reset برای آیتم #{item.id}'
            )
            created_count += 1

    print(f"\n✅ StockRequest های ایجاد شده: {created_count}")
    print(f"📊 مجموع StockRequest ها در دیتابیس: {StockRequest.objects.count()}")

    statuses = StockRequest.objects.values('status').annotate(count=models.Count('id'))
    for status in statuses:
        print(f"  - {status['status']}: {status['count']}")

    departments = StockRequest.objects.values(
        'purchase_item__request__user__role'
    ).annotate(count=Count('id')).order_by('-count')
    for dept in departments:
        print(f"  - {dept['purchase_item__request__user__role']}: {dept['count']}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Reset StockRequest records safely.')
    parser.add_argument(
        '--confirm-destructive',
        action='store_true',
        help='Required flag to confirm deleting and recreating all StockRequest rows.',
    )
    args = parser.parse_args()
    reset_stock_requests(confirm_destructive=args.confirm_destructive)
