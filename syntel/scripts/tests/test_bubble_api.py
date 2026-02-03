"""
Test script for bubble chart API
"""
import os
import sys
import django

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.views_analytics import (
    get_date_range, 
    _invoice_items_for_period, 
    calculate_growth_rate,
    get_color_by_growth
)
from purchases.models import Requester, Category, InvoiceItem
from django.db.models import Sum, Q, DecimalField, Value
from django.db.models.functions import Coalesce
from decimal import Decimal

def test_department_expenses_api(period='monthly'):
    """Test department expenses API logic"""
    print(f"\n{'='*50}")
    print(f"Testing Department Expenses API (period={period})")
    print('='*50)
    
    start, end, prev_start, prev_end = get_date_range(period)
    print(f"Current period: {start.date()} to {end.date()}")
    print(f"Previous period: {prev_start.date()} to {prev_end.date()}")
    
    requesters = Requester.objects.all().order_by('name')
    print(f"\nFound {requesters.count()} requesters")
    
    departments = []
    
    for req in requesters:
        current_items = _invoice_items_for_period(start=start, end=end)
        current = current_items.filter(
            Q(requester=req) | 
            Q(invoice__purchase_order__request__user__role=req.name)
        ).aggregate(
            total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
        )['total'] or Decimal('0.00')
        
        previous_items = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False)
        previous = previous_items.filter(
            Q(requester=req) | 
            Q(invoice__purchase_order__request__user__role=req.name)
        ).aggregate(
            total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
        )['total'] or Decimal('0.00')
        
        growth_rate = calculate_growth_rate(float(current), float(previous))
        color = get_color_by_growth(growth_rate)
        
        dept_data = {
            'id': req.id,
            'name': req.get_name_display(),
            'current_expense': float(current),
            'previous_expense': float(previous),
            'growth_rate': round(growth_rate, 2),
            'color': color
        }
        departments.append(dept_data)
        
        print(f"\n{req.get_name_display()} (id={req.id}):")
        print(f"  Current expense: {float(current):,.0f}")
        print(f"  Previous expense: {float(previous):,.0f}")
        print(f"  Growth rate: {growth_rate:.2f}%")
        print(f"  Color: {color}")
    
    response_data = {'departments': departments}
    print(f"\n{'='*50}")
    print("API Response would be:")
    print(response_data)
    return response_data


def test_items_per_requester():
    """Count items per requester"""
    print(f"\n{'='*50}")
    print("Items per Requester")
    print('='*50)
    
    for req in Requester.objects.all():
        count = InvoiceItem.objects.filter(requester=req).count()
        total = InvoiceItem.objects.filter(requester=req).aggregate(
            total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
        )['total']
        print(f"{req.get_name_display()}: {count} items, total: {total:,.0f}")


if __name__ == '__main__':
    test_items_per_requester()
    test_department_expenses_api('monthly')
