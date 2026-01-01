#!/usr/bin/env python
"""
بررسی داده‌های انبار
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import StockItem, StockTransaction, StockRequest

def check_stock_data():
    """بررسی وجود داده‌های انبار"""
    
    print("📦 بررسی داده‌های انبار...")
    print("=" * 60)
    
    # تعداد موجودی‌ها
    stock_count = StockItem.objects.count()
    print(f"\n📊 تعداد کل موجودی‌ها: {stock_count}")
    
    if stock_count > 0:
        print("\n📋 لیست موجودی‌ها:")
        for item in StockItem.objects.select_related('product', 'unit').all()[:10]:
            print(f"   • {item.product.name}: {item.quantity} {item.unit.name}")
    else:
        print("   ⚠️ هیچ موجودی‌ای ثبت نشده است")
    
    # تعداد تراکنش‌ها
    trans_count = StockTransaction.objects.count()
    print(f"\n📊 تعداد کل تراکنش‌ها: {trans_count}")
    
    if trans_count > 0:
        print("\n📋 آخرین تراکنش‌ها:")
        for trans in StockTransaction.objects.select_related('stock_item__product', 'created_by').order_by('-created_at')[:5]:
            print(f"   • {trans.stock_item.product.name}: {trans.get_transaction_type_display()} - {trans.quantity}")
    else:
        print("   ⚠️ هیچ تراکنشی ثبت نشده است")
    
    # تعداد درخواست‌ها
    request_count = StockRequest.objects.count()
    print(f"\n📊 تعداد درخواست‌های تامین از انبار: {request_count}")
    
    if request_count > 0:
        print("\n📋 درخواست‌های تامین:")
        for req in StockRequest.objects.select_related('purchase_item').all()[:5]:
            print(f"   • {req.purchase_item.name}: {req.get_status_display()}")
    else:
        print("   ⚠️ هیچ درخواستی ثبت نشده است")
    
    print("\n" + "=" * 60)
    
    if stock_count == 0:
        print("\n💡 برای ثبت موجودی اولیه:")
        print("   1. به آدرس زیر بروید:")
        print("      http://localhost:8000/stock/initial-register/")
        print("   2. یا از داشبورد مالی روی 'ثبت موجودی جدید' کلیک کنید")
    else:
        print("\n✅ داده‌های انبار موجود است و باید در داشبورد نمایش داده شود")

if __name__ == '__main__':
    check_stock_data()
