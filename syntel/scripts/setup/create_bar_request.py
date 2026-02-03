#!/usr/bin/env python
"""
Script to create and approve bar department request
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

def create_bar_request():
    """Create and approve a bar department request"""
    
    # Get or create bar user
    bar_user = User.objects.filter(role='bar').first()
    
    if not bar_user:
        print("❌ No bar user found!")
        return
    
    # Create purchase request
    request = PurchaseRequest.objects.create(
        user=bar_user,
        status='pending'
    )
    
    items = [
        {'name': 'قهوه', 'quantity': 5, 'unit': 'کیلوگرم'},
        {'name': 'شیر', 'quantity': 20, 'unit': 'لیتر'},
        {'name': 'چای', 'quantity': 3, 'unit': 'کیلوگرم'},
        {'name': 'شکر', 'quantity': 10, 'unit': 'کیلوگرم'},
    ]
    
    for item_data in items:
        PurchaseItem.objects.create(
            request=request,
            name=item_data['name'],
            quantity=item_data['quantity'],
            unit=item_data['unit']
        )
    
    print(f"✅ Created bar request #{request.id} with {request.items.count()} items")
    
    # Approve it
    purchasing_user = User.objects.filter(role='purchasing').first()
    request.status = 'approved'
    request.approved_by = purchasing_user
    request.approved_at = timezone.now()
    request.save()
    
    # Set purchased quantities with variations
    items_list = list(request.items.all())
    items_list[0].purchased_quantity = items_list[0].quantity  # Full
    items_list[0].supply_status = 'purchased'
    items_list[0].save()
    
    items_list[1].purchased_quantity = 15  # Partial
    items_list[1].supply_status = 'purchased'
    items_list[1].save()
    
    items_list[2].purchased_quantity = items_list[2].quantity
    items_list[2].supply_status = 'from_stock'  # From stock
    items_list[2].save()
    
    items_list[3].purchased_quantity = 0
    items_list[3].supply_status = 'not_supplied'  # Not supplied
    items_list[3].save()
    
    print(f"✅ Approved bar request #{request.id}")
    print(f"📊 Total approved requests: {PurchaseRequest.objects.filter(status='approved').count()}")

if __name__ == '__main__':
    create_bar_request()
