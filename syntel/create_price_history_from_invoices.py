import os
import django
from decimal import Decimal

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Invoice, InvoiceItem, PriceHistory
from django.db.models import Max

def create_price_history_from_invoices():
    """ایجاد تاریخچه قیمت از فاکتورهای موجود"""
    
    print("🔄 شروع ایجاد تاریخچه قیمت از فاکتورها...")
    print("=" * 60)
    
    # دریافت همه فاکتورها
    invoices = Invoice.objects.all().order_by('invoice_date')
    
    if not invoices.exists():
        print("❌ هیچ فاکتوری یافت نشد!")
        return
    
    print(f"✓ تعداد فاکتورها: {invoices.count()}")
    
    total_created = 0
    total_updated = 0
    
    # پاک کردن تاریخچه قبلی (اختیاری)
    # PriceHistory.objects.all().delete()
    # print("⚠ تاریخچه قبلی پاک شد")
    
    for invoice in invoices:
        invoice_items = invoice.invoice_items.all()
        
        for item in invoice_items:
            if not item.product:
                continue
            
            # بررسی آخرین قیمت محصول
            last_price_history = PriceHistory.objects.filter(
                product=item.product
            ).order_by('-date').first()
            
            # محاسبه درصد تغییر
            percent_change = Decimal('0')
            if last_price_history and last_price_history.price > 0:
                price_diff = item.unit_price - last_price_history.price
                percent_change = (price_diff / last_price_history.price) * Decimal('100')
            
            # ایجاد یا بروزرسانی رکورد تاریخچه قیمت
            price_history, created = PriceHistory.objects.get_or_create(
                product=item.product,
                invoice=invoice,
                date=invoice.invoice_date,
                defaults={
                    'price': item.unit_price,
                    'supplier': invoice.supplier,
                    'percent_change': percent_change
                }
            )
            
            if not created:
                # بروزرسانی اگر قبلا وجود داشته
                price_history.price = item.unit_price
                price_history.supplier = invoice.supplier
                price_history.percent_change = percent_change
                price_history.save()
                total_updated += 1
            else:
                total_created += 1
            
            if (total_created + total_updated) % 10 == 0:
                print(f"   پردازش شد: {total_created + total_updated} رکورد...")
    
    print("=" * 60)
    print(f"✅ عملیات تکمیل شد!")
    print(f"   📊 رکوردهای جدید: {total_created}")
    print(f"   🔄 رکوردهای بروزرسانی شده: {total_updated}")
    print(f"   📈 مجموع: {total_created + total_updated}")
    
    # نمایش آمار
    print("\n📊 آمار تاریخچه قیمت:")
    total_histories = PriceHistory.objects.count()
    unique_products = PriceHistory.objects.values('product').distinct().count()
    
    print(f"   کل رکوردها: {total_histories}")
    print(f"   محصولات منحصر به فرد: {unique_products}")
    
    # نمایش محصولات با بیشترین افزایش قیمت
    high_increase = PriceHistory.objects.filter(
        percent_change__gt=0
    ).order_by('-percent_change')[:5]
    
    if high_increase:
        print("\n🔥 5 محصول با بیشترین افزایش قیمت:")
        for ph in high_increase:
            print(f"   • {ph.product.name}: +{ph.percent_change:.1f}%")
    
    # نمایش محصولات با بیشترین کاهش قیمت
    high_decrease = PriceHistory.objects.filter(
        percent_change__lt=0
    ).order_by('percent_change')[:5]
    
    if high_decrease:
        print("\n📉 5 محصول با بیشترین کاهش قیمت:")
        for ph in high_decrease:
            print(f"   • {ph.product.name}: {ph.percent_change:.1f}%")

if __name__ == '__main__':
    print("🚀 ایجاد تاریخچه قیمت از فاکتورها\n")
    create_price_history_from_invoices()
    print("\n✅ عملیات به پایان رسید!")
