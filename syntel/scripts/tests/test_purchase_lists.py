#!/usr/bin/env python
"""Test purchase lists by date view"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PurchaseRequest
from django.db.models import Count
from django.db.models.functions import TruncDate, Coalesce

# Test the query
reqs = PurchaseRequest.objects.filter(
    status='purchased'
).annotate(
    date=TruncDate(Coalesce('processed_at', 'created_at'))
).values('date', 'user__role').annotate(
    count=Count('id')
).order_by('-date', 'user__role')

req_list = list(reqs)
print(f'Found {len(req_list)} date-role groups')
print('\nFirst 10 groups:')
for r in req_list[:10]:
    print(f"Date: {r['date']}, Role: {r['user__role']}, Count: {r['count']}")

# Group by date
purchase_days = {}
for req in req_list:
    date_obj = req['date']
    if not date_obj:
        continue
    date_str = date_obj.strftime('%Y-%m-%d')
    role = req['user__role']
    
    if date_str not in purchase_days:
        purchase_days[date_str] = {
            'date_obj': date_obj,
            'departments': {}
        }
    
    role_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    
    purchase_days[date_str]['departments'][role] = {
        'name': role_names.get(role, role),
        'count': req['count']
    }

print(f'\nTotal dates with purchases: {len(purchase_days)}')
for date_str, date_info in list(purchase_days.items())[:5]:
    print(f"\nDate: {date_str}")
    for role, dept_info in date_info['departments'].items():
        print(f"  - {dept_info['name']}: {dept_info['count']} درخواست")
