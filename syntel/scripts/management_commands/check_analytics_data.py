#!/usr/bin/env python
"""
بررسی داده‌های Analytics در دیتابیس
"""
import os
import sys
import django

# Setup Django - add parent directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import InvoiceItem, Invoice, Requester, Category, Product
from django.utils import timezone
from datetime import timedelta

print("=" * 60)
print("بررسی داده‌های Analytics")
print("=" * 60)

# بررسی Invoice ها
invoices = Invoice.objects.all()
print(f"\n📄 تعداد کل Invoice: {invoices.count()}")

# Invoice های با وضعیت های مهم
important_statuses = ['sent', 'approved', 'partially_paid', 'paid', 'overdue']
important_invoices = Invoice.objects.filter(status__in=important_statuses)
print(f"📊 Invoice های با وضعیت مهم: {important_invoices.count()}")

# InvoiceItem ها
invoice_items = InvoiceItem.objects.all()
print(f"\n📦 تعداد کل InvoiceItem: {invoice_items.count()}")

# InvoiceItem های ماه اخیر
month_ago = timezone.now() - timedelta(days=30)
recent_items = InvoiceItem.objects.filter(
    invoice__created_at__gte=month_ago,
    invoice__status__in=important_statuses
)
print(f"🕐 InvoiceItem های ماه اخیر: {recent_items.count()}")

# بررسی Requester (دپارتمان)
requesters = Requester.objects.all()
print(f"\n👥 تعداد Requester (بخش‌ها): {requesters.count()}")
for req in requesters[:5]:
    print(f"   - {req.name}")

# بررسی Category
categories = Category.objects.filter(parent__isnull=True)
print(f"\n📂 تعداد Category اصلی: {categories.count()}")
for cat in categories[:5]:
    subcats = Category.objects.filter(parent=cat).count()
    print(f"   - {cat.name} ({subcats} زیر دسته)")

# بررسی Product
products = Product.objects.all()
print(f"\n🏷️  تعداد Product: {products.count()}")

# محاسبه مجموع هزینه‌ها
if recent_items.exists():
    from django.db.models import Sum, F
    total_expense = recent_items.aggregate(
        total=Sum(F('quantity') * F('unit_price'))
    )['total']
    print(f"\n💰 مجموع هزینه‌های ماه اخیر: {total_expense:,.0f} تومان" if total_expense else "\n💰 مجموع هزینه‌ها: 0 تومان")

# بررسی InvoiceItem بر اساس دپارتمان
print(f"\n🔍 InvoiceItem های ماه اخیر بر اساس دپارتمان:")
from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Coalesce

dept_expenses = recent_items.values(
    'invoice__requester__name',
    'invoice__requester__role'
).annotate(
    total=Sum(F('quantity') * F('unit_price'), output_field=DecimalField())
).order_by('-total')

for dept in dept_expenses[:10]:
    print(f"   - {dept['invoice__requester__name']}: {dept['total']:,.0f} تومان")

print("\n" + "=" * 60)
print("✅ بررسی کامل شد!")
print("=" * 60)
