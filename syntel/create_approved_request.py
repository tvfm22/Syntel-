#!/usr/bin/env python
"""
Script to create and approve a sample purchase request
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
from django.utils import timezone

def create_and_approve_request():
    """Create and approve a sample purchase request"""
    
    # Get kitchen user
    kitchen_user = User.objects.filter(role='kitchen').first()
    
    if not kitchen_user:
        print("❌ No kitchen user found!")
        return
    
    # Create purchase request
    request = PurchaseRequest.objects.create(
        user=kitchen_user,
        status='pending'
    )
    
    items = [
        {'name': 'شکر', 'quantity': 40, 'unit': 'کیلوگرم'},
        {'name': 'نمک', 'quantity': 10, 'unit': 'کیلوگرم'},
        {'name': 'رب گوجه', 'quantity': 5, 'unit': 'کیلوگرم'},
    ]
    
    for item_data in items:
        PurchaseItem.objects.create(
            request=request,
            name=item_data['name'],
            quantity=item_data['quantity'],
            unit=item_data['unit']
        )
    
    print(f"✅ Created request #{request.id} with {request.items.count()} items")
    
    # Approve it
    purchasing_user = User.objects.filter(role='purchasing').first()
    request.status = 'approved'
    request.approved_by = purchasing_user
    request.approved_at = timezone.now()
    request.save()
    
    # Set purchased quantities
    for item in request.items.all():
        item.purchased_quantity = item.quantity
        item.supply_status = 'purchased'
        item.save()
    
    print(f"✅ Approved request #{request.id}")
    print(f"📊 Total approved requests: {PurchaseRequest.objects.filter(status='approved').count()}")

if __name__ == '__main__':
    create_and_approve_request()
