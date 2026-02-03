#!/usr/bin/env python
"""Create sample purchase requests for Baba Adam cafe"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PurchaseRequest, PurchaseItem, Product, User, Cafe

def main():
    try:
        baba_cafe = Cafe.objects.get(code='baba_adam')
    except Cafe.DoesNotExist:
        print("Baba Adam cafe not found!")
        return

    # Get baba adam users
    try:
        baba_kitchen = User.objects.get(username='baba_kitchen')
        baba_bar = User.objects.get(username='baba_bar')
    except User.DoesNotExist as e:
        print(f"User not found: {e}")
        return

    # Get products for baba adam
    products = Product.objects.filter(cafe=baba_cafe)
    if not products.exists():
        print("No products for Baba Adam cafe!")
        return

    # Create some purchase requests
    print("\nCreating purchase requests for Baba Adam...")
    
    # Kitchen request
    pr1, created = PurchaseRequest.objects.get_or_create(
        user=baba_kitchen,
        cafe=baba_cafe,
        status='pending'
    )
    if created:
        print(f"Created kitchen request: {pr1.id}")
        # Add items
        for i, product in enumerate(products[:2]):
            PurchaseItem.objects.create(
                request=pr1,
                product=product,
                name=product.name,
                quantity=5 + i,
                unit=product.unit.name if product.unit else 'کیلو'
            )
        print(f"  Added {min(2, products.count())} items")
    else:
        print(f"Kitchen request already exists: {pr1.id}")

    # Bar request
    pr2, created = PurchaseRequest.objects.get_or_create(
        user=baba_bar,
        cafe=baba_cafe,
        status='pending'
    )
    if created:
        print(f"Created bar request: {pr2.id}")
        for i, product in enumerate(products[1:3]):
            PurchaseItem.objects.create(
                request=pr2,
                product=product,
                name=product.name,
                quantity=3 + i,
                unit=product.unit.name if product.unit else 'کیلو'
            )
        print(f"  Added items")
    else:
        print(f"Bar request already exists: {pr2.id}")

    # Print summary
    print("\n=== Purchase Requests Summary ===")
    for cafe in Cafe.objects.all():
        count = PurchaseRequest.objects.filter(cafe=cafe).count()
        print(f"{cafe.name}: {count} requests")

    print("\nDone!")

if __name__ == '__main__':
    main()
