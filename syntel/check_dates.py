#!/usr/bin/env python
"""Check dates of purchased requests"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PurchaseRequest
from django.db.models import Count
from django.db.models.functions import TruncDate

print("=== Checking purchased requests dates ===\n")

# Get all purchased requests
purchased = PurchaseRequest.objects.filter(status='purchased').order_by('created_at')

print(f"Total purchased requests: {purchased.count()}\n")

# Group by created_at date
created_dates = purchased.values(
    'created_at__date'
).annotate(
    count=Count('id')
).order_by('-created_at__date')

print("Requests grouped by created_at date:")
for d in created_dates:
    print(f"  {d['created_at__date']}: {d['count']} requests")

# Group by processed_at date
processed_dates = purchased.exclude(
    processed_at__isnull=True
).values(
    'processed_at__date'
).annotate(
    count=Count('id')
).order_by('-processed_at__date')

print("\nRequests grouped by processed_at date:")
for d in processed_dates:
    print(f"  {d['processed_at__date']}: {d['count']} requests")

# Show sample requests
print("\nSample requests (first 10):")
for req in purchased[:10]:
    print(f"  ID: {req.id}, Created: {req.created_at.date()}, Processed: {req.processed_at.date() if req.processed_at else 'None'}, User: {req.user.role if req.user else 'None'}")
