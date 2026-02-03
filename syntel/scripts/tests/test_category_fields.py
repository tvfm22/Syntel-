#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Category

# Check fields
fields = [f.name for f in Category._meta.get_fields()]
print("Category fields:", fields)

# Check if parent field exists
if 'parent' in fields:
    print("✓ Parent field exists in model")
    
    # Try to create a test category
    try:
        # Get or create a parent category
        parent_cat, _ = Category.objects.get_or_create(
            name="تره بار",
            defaults={'description': 'دسته‌بندی اصلی تره بار'}
        )
        print(f"✓ Parent category created/retrieved: {parent_cat}")
        
        # Try to create a subcategory
        sub_cat, created = Category.objects.get_or_create(
            name="هویج",
            parent=parent_cat,
            defaults={'description': 'هویج تازه'}
        )
        if created:
            print(f"✓ Subcategory created: {sub_cat}")
        else:
            print(f"✓ Subcategory already exists: {sub_cat}")
            
        print(f"Full path: {sub_cat.full_path}")
        
    except Exception as e:
        print(f"✗ Error creating categories: {e}")
else:
    print("✗ Parent field does NOT exist in model yet")
