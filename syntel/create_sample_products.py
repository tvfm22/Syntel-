#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Category, Product, Unit

# Get categories
carrot_cat = Category.objects.filter(name='هویج').first()
tomato_cat = Category.objects.filter(name='گوجه').first()
milk_cat = Category.objects.filter(name='شیر').first()

# Get or create unit
kg_unit, _ = Unit.objects.get_or_create(
    name='کیلوگرم',
    defaults={'symbol': 'کیلو', 'is_active': True}
)

liter_unit, _ = Unit.objects.get_or_create(
    name='لیتر',
    defaults={'symbol': 'لیتر', 'is_active': True}
)

# Create products with categories
products_data = [
    {'name': 'هویج تازه', 'category': carrot_cat, 'unit': kg_unit},
    {'name': 'گوجه فرنگی', 'category': tomato_cat, 'unit': kg_unit},
    {'name': 'شیر کم چرب', 'category': milk_cat, 'unit': liter_unit},
    {'name': 'شیر پر چرب', 'category': milk_cat, 'unit': liter_unit},
]

print("Creating sample products with categories...")
for prod_data in products_data:
    prod, created = Product.objects.get_or_create(
        name=prod_data['name'],
        defaults={
            'category': prod_data['category'],
            'unit': prod_data['unit'],
            'is_active': True
        }
    )
    if created:
        print(f"✓ Created: {prod.name} - Category: {prod.category.full_path if prod.category else 'N/A'}")
    else:
        # Update category if product exists
        prod.category = prod_data['category']
        prod.save()
        print(f"  Updated: {prod.name} - Category: {prod.category.full_path if prod.category else 'N/A'}")

print("\nAll products with categories:")
products = Product.objects.filter(is_active=True).select_related('category', 'category__parent')
for prod in products[:10]:
    cat_path = prod.category.full_path if prod.category else 'بدون دسته‌بندی'
    print(f"  - {prod.name}: {cat_path}")

print("\nDone!")
