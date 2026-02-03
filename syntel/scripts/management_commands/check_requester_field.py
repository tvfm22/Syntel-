# -*- coding: utf-8 -*-
import os
import sys
import django

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import InvoiceItem, Requester
from django.utils import timezone
from datetime import timedelta

print("Checking InvoiceItem requester field...")

# Total items
total = InvoiceItem.objects.count()
print(f"Total InvoiceItems: {total}")

# Items with requester
with_requester = InvoiceItem.objects.filter(requester__isnull=False).count()
print(f"InvoiceItems with requester: {with_requester}")

# Items without requester
without_requester = InvoiceItem.objects.filter(requester__isnull=True).count()
print(f"InvoiceItems without requester: {without_requester}")

# Recent items
month_ago = timezone.now() - timedelta(days=30)
recent_total = InvoiceItem.objects.filter(invoice__created_at__gte=month_ago).count()
recent_with_req = InvoiceItem.objects.filter(
    invoice__created_at__gte=month_ago,
    requester__isnull=False
).count()
print(f"\nRecent (last month):")
print(f"  Total: {recent_total}")
print(f"  With requester: {recent_with_req}")
print(f"  Without requester: {recent_total - recent_with_req}")

# Check if we can get requester from purchase chain
print(f"\nChecking purchase chain...")
items_with_order = InvoiceItem.objects.filter(
    invoice__purchase_order__isnull=False
).count()
print(f"InvoiceItems with purchase_order: {items_with_order}")

# Sample check
print(f"\nSample InvoiceItems:")
for item in InvoiceItem.objects.all()[:5]:
    po = item.invoice.purchase_order if item.invoice and item.invoice.purchase_order else None
    req = po.request if po and hasattr(po, 'request') else None
    user = req.user if req and hasattr(req, 'user') else None
    
    print(f"  Item {item.id}:")
    print(f"    Direct requester: {item.requester}")
    print(f"    PO: {po}")
    print(f"    Request: {req}")
    print(f"    User: {user}")
    if user:
        print(f"    User role: {user.role if hasattr(user, 'role') else 'N/A'}")
