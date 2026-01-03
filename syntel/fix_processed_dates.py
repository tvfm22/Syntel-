#!/usr/bin/env python
"""Fix processed_at dates to match created_at for historical data"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PurchaseRequest

print("=== Fixing processed_at dates ===\n")

# Get all purchased requests
purchased = PurchaseRequest.objects.filter(status='purchased')

print(f"Total purchased requests to update: {purchased.count()}\n")

updated_count = 0
for req in purchased:
    # Set processed_at to created_at for historical accuracy
    old_processed = req.processed_at
    req.processed_at = req.created_at
    req.save(update_fields=['processed_at'])
    updated_count += 1
    if updated_count <= 5:
        print(f"  ID {req.id}: {old_processed.date() if old_processed else 'None'} → {req.created_at.date()}")

print(f"\n✓ Updated {updated_count} requests")
print("\nVerifying groups by date:")

from django.db.models import Count
from django.db.models.functions import TruncDate

dates = PurchaseRequest.objects.filter(
    status='purchased'
).values(
    'processed_at__date'
).annotate(
    count=Count('id')
).order_by('-processed_at__date')

for d in dates[:10]:
    print(f"  {d['processed_at__date']}: {d['count']} requests")
