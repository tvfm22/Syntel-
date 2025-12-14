#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Category, Product

# Create some sample categories
categories_data = [
    {
        'name': 'تره بار',
        'description': 'میوه و سبزیجات تازه',
        'subcategories': [
            {'name': 'هویج', 'description': 'هویج تازه'},
            {'name': 'گوجه', 'description': 'گوجه فرنگی'},
            {'name': 'خیار', 'description': 'خیار تازه'},
            {'name': 'سیب', 'description': 'سیب تازه'},
        ]
    },
    {
        'name': 'لبنیات',
        'description': 'محصولات لبنی',
        'subcategories': [
            {'name': 'شیر', 'description': 'شیر پاستوریزه'},
            {'name': 'ماست', 'description': 'ماست'},
            {'name': 'پنیر', 'description': 'انواع پنیر'},
        ]
    },
    {
        'name': 'گوشت و پروتئین',
        'description': 'گوشت قرمز و سفید',
        'subcategories': [
            {'name': 'مرغ', 'description': 'مرغ تازه'},
            {'name': 'گوشت گاو', 'description': 'گوشت گاو'},
            {'name': 'ماهی', 'description': 'ماهی تازه'},
        ]
    },
]

print("Creating sample categories...")
for cat_data in categories_data:
    parent_cat, created = Category.objects.get_or_create(
        name=cat_data['name'],
        parent=None,
        defaults={'description': cat_data['description']}
    )
    if created:
        print(f"✓ Created parent category: {parent_cat.name}")
    else:
        print(f"  Parent category exists: {parent_cat.name}")
    
    for subcat_data in cat_data.get('subcategories', []):
        sub_cat, created = Category.objects.get_or_create(
            name=subcat_data['name'],
            parent=parent_cat,
            defaults={'description': subcat_data['description']}
        )
        if created:
            print(f"  ✓ Created subcategory: {sub_cat.full_path}")
        else:
            print(f"    Subcategory exists: {sub_cat.full_path}")

print("\nAll categories:")
all_cats = Category.objects.all().select_related('parent')
for cat in all_cats:
    indent = "  " if cat.parent else ""
    print(f"{indent}{cat.full_path}")

print("\nDone!")
