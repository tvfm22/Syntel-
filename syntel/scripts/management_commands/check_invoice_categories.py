#!/usr/bin/env python
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from purchases.models import InvoiceItem, Product, Category

print("Invoice Items (first 20):")
items = InvoiceItem.objects.select_related('product__category', 'requester')[:20]
for i in items:
    prod_name = i.product.name if i.product else "N/A"
    cat_name = i.product.category.name if i.product and i.product.category else "N/A"
    parent = i.product.category.parent.name if i.product and i.product.category and i.product.category.parent else "None"
    print(f"  {i.id}: {prod_name} -> Cat: {cat_name} (Parent: {parent})")

print("\n\nProducts with subcategories (showing category hierarchy):")
products_with_subcats = Product.objects.filter(category__parent__isnull=False).select_related('category__parent')[:30]
for p in products_with_subcats:
    print(f"  {p.id}: {p.name} -> {p.category.parent.name} > {p.category.name}")

print("\n\nProducts in main categories (no parent):")
products_main = Product.objects.filter(category__parent__isnull=True).select_related('category')[:20]
for p in products_main:
    print(f"  {p.id}: {p.name} -> {p.category.name}")
