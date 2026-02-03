# -*- coding: utf-8 -*-
import os, sys, django

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import InvoiceItem, Requester, Cafe, Invoice
from django.utils import timezone
from datetime import timedelta

print("Checking Cafe and Data Distribution...")

# All cafes
cafes = Cafe.objects.all()
print(f"\nTotal Cafes: {cafes.count()}")
for cafe in cafes:
    print(f"  - {cafe.code} ({cafe.name})")

# Requesters by cafe
print(f"\n--- Requesters by Cafe ---")
for cafe in cafes:
    reqs = Requester.objects.filter(cafe=cafe)
    print(f"{cafe.code}: {reqs.count()} requesters")
    for req in reqs:
        print(f"  - {req.get_name_display()}")

# Also check requesters without cafe
no_cafe_reqs = Requester.objects.filter(cafe__isnull=True)
print(f"\nRequesters without cafe: {no_cafe_reqs.count()}")
for req in no_cafe_reqs:
    print(f"  - {req.get_name_display()}")

# Check Invoices by cafe
print(f"\n--- Invoices by Cafe ---")
for cafe in cafes:
    inv_count = Invoice.objects.filter(cafe=cafe).count()
    print(f"{cafe.code}: {inv_count} invoices")

no_cafe_inv = Invoice.objects.filter(cafe__isnull=True).count()
print(f"Invoices without cafe: {no_cafe_inv}")

# Check recent data
month_ago = timezone.now() - timedelta(days=30)
print(f"\n--- Recent Invoices (last 30 days) ---")
recent_invoices = Invoice.objects.filter(created_at__gte=month_ago)
print(f"Total: {recent_invoices.count()}")

for cafe in cafes:
    count = recent_invoices.filter(cafe=cafe).count()
    if count > 0:
        print(f"  {cafe.code}: {count}")

no_cafe = recent_invoices.filter(cafe__isnull=True).count()
if no_cafe > 0:
    print(f"  No cafe: {no_cafe}")

# Check invoice statuses
print(f"\n--- Invoice Statuses (last 30 days) ---")
statuses = recent_invoices.values_list('status', flat=True).distinct()
for status in statuses:
    count = recent_invoices.filter(status=status).count()
    print(f"  {status}: {count}")
