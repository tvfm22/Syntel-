#!/usr/bin/env python
"""
Script to create a sample purchase request with multiple items for testing
"""
import os
import sys
import django

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(__file__))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User, PurchaseRequest, PurchaseItem

def create_sample_request():
    """Create a sample purchase request with items"""
    
    # Get or create a kitchen user
    kitchen_user = User.objects.filter(role='kitchen').first()
    if not kitchen_user:
        kitchen_user = User.objects.create_user(
            username='kitchen',
            password='kitchen123',
            first_name='سرآشپز',
            last_name='آشپزخانه',
            role='kitchen'
        )
        print("✓ Created kitchen user")
    
    # Create a purchase request
    request = PurchaseRequest.objects.create(
        user=kitchen_user,
        status='pending'
    )
    print(f"✓ Created purchase request #{request.id}")
    
    # Create sample items
    items = [
        {'name': 'برنج', 'quantity': 50, 'unit': 'کیلوگرم'},
        {'name': 'روغن مایع', 'quantity': 10, 'unit': 'لیتر'},
        {'name': 'گوشت مرغ', 'quantity': 30, 'unit': 'کیلوگرم'},
        {'name': 'پیاز', 'quantity': 15, 'unit': 'کیلوگرم'},
        {'name': 'گوجه فرنگی', 'quantity': 20, 'unit': 'کیلوگرم'},
        {'name': 'سیب زمینی', 'quantity': 25, 'unit': 'کیلوگرم'},
    ]
    
    for item_data in items:
        item = PurchaseItem.objects.create(
            request=request,
            name=item_data['name'],
            quantity=item_data['quantity'],
            unit=item_data['unit']
        )
        print(f"  ✓ Added item: {item.name} - {item.quantity} {item.unit}")
    
    print(f"\n✅ Sample request created successfully!")
    print(f"   Request ID: {request.id}")
    print(f"   User: {kitchen_user.username}")
    print(f"   Items: {request.items.count()}")
    print(f"\n💡 Login as 'purchasing' to process this request")

if __name__ == '__main__':
    create_sample_request()
