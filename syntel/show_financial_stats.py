"""
بررسی و نمایش آمار کامل داده‌های مالی
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import *
from django.db.models import Sum, Count, Avg
from django.utils import timezone
from datetime import timedelta

print("=" * 70)
print("📊 آمار کامل داده‌های مالی سیستم Syntel")
print("=" * 70)

# فاکتورها
print("\n🧾 فاکتورها:")
total_invoices = Invoice.objects.count()
cash_invoices = Invoice.objects.filter(supplier__isnull=True).count()
supplier_invoices = Invoice.objects.filter(supplier__isnull=False).count()
total_amount = Invoice.objects.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
cash_amount = Invoice.objects.filter(supplier__isnull=True).aggregate(Sum('total_amount'))['total_amount__sum'] or 0
supplier_amount = Invoice.objects.filter(supplier__isnull=False).aggregate(Sum('total_amount'))['total_amount__sum'] or 0

print(f"  تعداد کل: {total_invoices}")
print(f"  نقدی: {cash_invoices} ({cash_amount:,.0f} ریال)")
print(f"  تامین‌کننده: {supplier_invoices} ({supplier_amount:,.0f} ریال)")
print(f"  مجموع: {total_amount:,.0f} ریال")

# وضعیت پرداخت
paid = Invoice.objects.filter(payment_status='paid').count()
unpaid = Invoice.objects.filter(payment_status='unpaid').count()
partial = Invoice.objects.filter(payment_status='partial').count()
print(f"\n💳 وضعیت پرداخت:")
print(f"  پرداخت شده: {paid}")
print(f"  پرداخت نشده: {unpaid}")
print(f"  پرداخت جزئی: {partial}")

# درخواست‌ها
print("\n📝 درخواست‌های خرید:")
total_requests = PurchaseRequest.objects.count()
pending = PurchaseRequest.objects.filter(status='pending').count()
purchased = PurchaseRequest.objects.filter(status='purchased').count()
partial_req = PurchaseRequest.objects.filter(status='partial').count()
print(f"  تعداد کل: {total_requests}")
print(f"  در انتظار: {pending}")
print(f"  خریداری شده: {purchased}")
print(f"  جزئی: {partial_req}")

# آیتم‌های درخواست
total_items = PurchaseItem.objects.count()
from_stock = PurchaseItem.objects.filter(supply_status='from_stock').count()
purchased_items = PurchaseItem.objects.filter(supply_status='purchased').count()
pending_items = PurchaseItem.objects.filter(supply_status='pending').count()
print(f"\n📦 آیتم‌های درخواست:")
print(f"  تعداد کل: {total_items}")
print(f"  تامین از انبار: {from_stock}")
print(f"  خریداری شده: {purchased_items}")
print(f"  در انتظار: {pending_items}")

# تاریخچه قیمت
print("\n📈 تاریخچه قیمت:")
ph_count = PriceHistory.objects.count()
products_with_history = PriceHistory.objects.values('product').distinct().count()
print(f"  تعداد رکورد: {ph_count}")
print(f"  محصولات با تاریخچه: {products_with_history}")

increased = PriceHistory.objects.filter(percent_change__gt=0).count()
decreased = PriceHistory.objects.filter(percent_change__lt=0).count()
avg_change = PriceHistory.objects.filter(percent_change__isnull=False).aggregate(Avg('percent_change'))['percent_change__avg']
print(f"  افزایش قیمت: {increased} مورد")
print(f"  کاهش قیمت: {decreased} مورد")
if avg_change:
    print(f"  میانگین تغییر: {avg_change:.2f}%")

# پرداخت‌ها
print("\n💰 پرداخت‌ها:")
payment_count = Payment.objects.count()
payment_total = Payment.objects.filter(payment_status='completed').aggregate(Sum('amount'))['amount__sum'] or 0
print(f"  تعداد: {payment_count}")
print(f"  مجموع: {payment_total:,.0f} ریال")

# محصولات پرمصرف
print("\n🔥 10 محصول پرمصرف:")
top_products = InvoiceItem.objects.values('product__name').annotate(
    count=Count('id'),
    total=Sum('line_total')
).order_by('-count')[:10]

for i, p in enumerate(top_products, 1):
    if p['product__name']:
        print(f"  {i}. {p['product__name']}: {p['count']} بار - {p['total']:,.0f} ریال")

# تامین‌کنندگان پرفعالیت
print("\n🏢 5 تامین‌کننده برتر:")
top_suppliers = Invoice.objects.filter(supplier__isnull=False).values(
    'supplier__name'
).annotate(
    count=Count('id'),
    total=Sum('total_amount')
).order_by('-count')[:5]

for i, s in enumerate(top_suppliers, 1):
    print(f"  {i}. {s['supplier__name']}: {s['count']} فاکتور - {s['total']:,.0f} ریال")

# بازه زمانی
print("\n📅 بازه زمانی:")
oldest = Invoice.objects.order_by('invoice_date').first()
newest = Invoice.objects.order_by('-invoice_date').first()
if oldest and newest:
    print(f"  اولین فاکتور: {oldest.invoice_date}")
    print(f"  آخرین فاکتور: {newest.invoice_date}")
    days = (newest.invoice_date - oldest.invoice_date).days
    print(f"  مدت زمان: {days} روز")

print("\n" + "=" * 70)
print("✅ داده‌ها برای نمودارهای آمار و تحلیل آماده است:")
print("  ✓ نمودار خرید نقدی vs تامین‌کنندگان")
print("  ✓ نمودار وضعیت پرداخت")
print("  ✓ نمودار روند قیمت محصولات")
print("  ✓ نمودار پرمصرف‌ترین محصولات")
print("  ✓ نمودار عملکرد تامین‌کنندگان")
print("  ✓ نمودار تامین از انبار vs خرید")
print("=" * 70)
