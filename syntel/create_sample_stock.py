#!/usr/bin/env python
"""
ایجاد داده‌های نمونه برای انبار
"""
import os
import sys
import django
from decimal import Decimal

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import (
    Product, Unit, Category, StockItem, StockTransaction,
    User
)
from django.utils import timezone

def create_sample_stock():
    """ایجاد موجودی‌های نمونه"""
    
    print("📦 ایجاد داده‌های نمونه برای انبار...")
    print("=" * 60)
    
    # دریافت یا ایجاد واحدها
    print("\n1️⃣ ایجاد واحدها...")
    kg_unit, _ = Unit.objects.get_or_create(name='کیلوگرم', defaults={'symbol': 'kg', 'is_active': True})
    liter_unit, _ = Unit.objects.get_or_create(name='لیتر', defaults={'symbol': 'L', 'is_active': True})
    piece_unit, _ = Unit.objects.get_or_create(name='عدد', defaults={'symbol': 'عدد', 'is_active': True})
    print(f"   ✅ واحدها ایجاد شدند")
    
    # دریافت یا ایجاد دسته‌بندی‌ها
    print("\n2️⃣ ایجاد دسته‌بندی‌ها...")
    food_cat, _ = Category.objects.get_or_create(name='مواد غذایی', defaults={'is_active': True})
    drink_cat, _ = Category.objects.get_or_create(name='نوشیدنی', defaults={'is_active': True})
    print(f"   ✅ دسته‌بندی‌ها ایجاد شدند")
    
    # محصولات نمونه
    print("\n3️⃣ ایجاد محصولات...")
    products_data = [
        {'name': 'برنج', 'category': food_cat, 'unit': kg_unit, 'quantity': 50, 'min_quantity': 10},
        {'name': 'روغن', 'category': food_cat, 'unit': liter_unit, 'quantity': 20, 'min_quantity': 5},
        {'name': 'شکر', 'category': food_cat, 'unit': kg_unit, 'quantity': 15, 'min_quantity': 10},
        {'name': 'نمک', 'category': food_cat, 'unit': kg_unit, 'quantity': 8, 'min_quantity': 5},
        {'name': 'چای', 'category': drink_cat, 'unit': kg_unit, 'quantity': 3, 'min_quantity': 5},  # موجودی کم
        {'name': 'قهوه', 'category': drink_cat, 'unit': kg_unit, 'quantity': 2, 'min_quantity': 4},  # موجودی کم
        {'name': 'شیر', 'category': drink_cat, 'unit': liter_unit, 'quantity': 30, 'min_quantity': 10},
        {'name': 'لیوان یکبار مصرف', 'category': drink_cat, 'unit': piece_unit, 'quantity': 500, 'min_quantity': 200},
    ]
    
    # دریافت کاربر admin برای ثبت تراکنش‌ها
    try:
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.filter(role='purchasing').first()
        if not admin_user:
            admin_user = User.objects.first()
    except:
        admin_user = None
    
    created_count = 0
    for prod_data in products_data:
        # ایجاد یا دریافت محصول
        product, created = Product.objects.get_or_create(
            name=prod_data['name'],
            defaults={
                'category': prod_data['category'],
                'unit': prod_data['unit'],
                'is_active': True
            }
        )
        
        if created:
            print(f"   ✅ محصول '{product.name}' ایجاد شد")
        else:
            print(f"   ℹ️  محصول '{product.name}' از قبل وجود داشت")
        
        # ایجاد موجودی انبار
        stock_item, stock_created = StockItem.objects.get_or_create(
            product=product,
            defaults={
                'quantity': Decimal(str(prod_data['quantity'])),
                'unit': prod_data['unit'],
                'min_quantity': Decimal(str(prod_data['min_quantity']))
            }
        )
        
        if stock_created:
            created_count += 1
            
            # ثبت تراکنش موجودی اولیه
            StockTransaction.objects.create(
                stock_item=stock_item,
                transaction_type='initial',
                quantity=Decimal(str(prod_data['quantity'])),
                unit=prod_data['unit'],
                reference_type='initial',
                description='ثبت موجودی اولیه نمونه',
                created_by=admin_user,
                previous_quantity=Decimal('0'),
                new_quantity=Decimal(str(prod_data['quantity']))
            )
            
            print(f"   📦 موجودی برای '{product.name}' ثبت شد: {prod_data['quantity']} {prod_data['unit'].name}")
        else:
            print(f"   ⚠️ موجودی '{product.name}' از قبل وجود داشت")
    
    print(f"\n✅ {created_count} موجودی جدید ایجاد شد")
    
    # خلاصه
    print("\n" + "=" * 60)
    print("📊 خلاصه:")
    print(f"   • کل موجودی‌ها: {StockItem.objects.count()}")
    print(f"   • موجودی‌های کم: {StockItem.objects.filter(quantity__lte=F('min_quantity')).count()}")
    print(f"   • کل تراکنش‌ها: {StockTransaction.objects.count()}")
    print("\n✅ داده‌های نمونه با موفقیت ایجاد شدند!")
    print("\n🌐 حالا می‌توانید داشبورد مالی را مشاهده کنید:")
    print("   http://localhost:8000/finance-dashboard/")
    print("=" * 60)

if __name__ == '__main__':
    from django.db.models import F
    create_sample_stock()
