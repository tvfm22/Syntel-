# -*- coding: utf-8 -*-
import os, sys, django

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import InvoiceItem, Requester, Cafe
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum, Q, Value, DecimalField
from django.db.models.functions import Coalesce

print("Testing Analytics Query...")

# Get cafe
cafe = Cafe.objects.first()
print(f"Cafe: {cafe.code if cafe else 'None'}")

# Get time range
now = timezone.now()
month_ago = now - timedelta(days=30)

print(f"\nTime range: {month_ago.date()} to {now.date()}")

# Get requesters
requesters = Requester.objects.filter(cafe=cafe) if cafe else Requester.objects.all()
print(f"\nRequesters for cafe {cafe.code if cafe else 'all'}: {requesters.count()}")
for req in requesters:
    print(f"  - {req.name} ({req.get_name_display()})")

# Test InvoiceItem filtering
print(f"\n--- Testing InvoiceItem queries ---")

# Important statuses
INVOICE_STATUS_INCLUDED = ['sent', 'approved', 'partially_paid', 'paid', 'overdue']

items = InvoiceItem.objects.filter(
    invoice__created_at__gte=month_ago,
    invoice__status__in=INVOICE_STATUS_INCLUDED
)
if cafe:
    items = items.filter(invoice__cafe=cafe)

print(f"Total items (last month, important statuses): {items.count()}")

for req in requesters[:2]:
    req_items = items.filter(requester=req)
    count = req_items.count()
    total = req_items.aggregate(
        total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
    )['total'] or Decimal('0.00')
    
    print(f"\n  Requester: {req.get_name_display()}")
    print(f"    Items: {count}")
    print(f"    Total: {total:,.0f}")
    
    # Show sample items
    for item in req_items[:2]:
        print(f"      - {item.description}: {item.line_total:,.0f}")
