import os
import django
import random
from datetime import datetime, timedelta
from decimal import Decimal

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Invoice, InvoiceItem, Product, Supplier, Requester, User
from django.utils import timezone

def create_sample_invoices():
    """ایجاد 20 فاکتور نمونه با تاریخ‌های مختلف و محصولات مشترک"""
    
    # دریافت یا ایجاد کاربر
    try:
        user = User.objects.filter(role='purchasing').first()
        if not user:
            user = User.objects.first()
        if not user:
            print("❌ هیچ کاربری یافت نشد. لطفا ابتدا کاربر ایجاد کنید.")
            return
    except Exception as e:
        print(f"❌ خطا در دریافت کاربر: {e}")
        return

    # دریافت محصولات موجود
    products = list(Product.objects.filter(is_active=True))
    if len(products) < 8:
        print(f"❌ تعداد محصولات کافی نیست. محصولات موجود: {len(products)}")
        print("لطفا حداقل 8 محصول در سیستم ایجاد کنید.")
        return
    
    print(f"✓ تعداد محصولات موجود: {len(products)}")

    # دریافت فروشندگان موجود
    suppliers = list(Supplier.objects.filter(status='active'))
    if len(suppliers) < 3:
        print(f"⚠ تعداد فروشندگان کم است ({len(suppliers)}). در صورت نیاز فروشنده‌های بیشتری ایجاد کنید.")
        if len(suppliers) == 0:
            print("❌ هیچ فروشنده‌ای یافت نشد.")
            return
    
    print(f"✓ تعداد فروشندگان موجود: {len(suppliers)}")

    # دریافت یا ایجاد درخواست‌کنندگان
    requesters = list(Requester.objects.all())
    if len(requesters) == 0:
        print("⚠ درخواست‌کننده‌ای یافت نشد، ایجاد می‌شود...")
        for req_type in ['kitchen', 'bar', 'facilities']:
            Requester.objects.get_or_create(name=req_type)
        requesters = list(Requester.objects.all())
    
    # انتخاب محصولات مشترک (60% از محصولات)
    total_products = min(len(products), 15)  # حداکثر 15 محصول استفاده می‌کنیم
    common_products_count = max(int(total_products * 0.6), 5)  # حداقل 5 محصول مشترک
    common_products = random.sample(products[:total_products], common_products_count)
    remaining_products = [p for p in products[:total_products] if p not in common_products]
    
    print(f"✓ محصولات مشترک: {common_products_count} محصول")
    print(f"✓ محصولات متغیر: {len(remaining_products)} محصول")

    # تاریخ شروع (3 ماه پیش)
    start_date = datetime.now() - timedelta(days=90)
    
    created_invoices = []
    
    print("\n🔄 شروع ایجاد فاکتورها...")
    print("=" * 60)
    
    for i in range(1, 21):
        try:
            # انتخاب فروشنده تصادفی
            supplier = random.choice(suppliers)
            
            # تولید تاریخ تصادفی
            random_days = random.randint(0, 90)
            invoice_date = start_date + timedelta(days=random_days)
            due_date = invoice_date + timedelta(days=random.randint(7, 30))
            
            # شماره فاکتور یکتا
            invoice_number = f"INV-{invoice_date.strftime('%Y%m%d')}-{i:03d}"
            
            # چک کردن وجود فاکتور با این شماره
            if Invoice.objects.filter(invoice_number=invoice_number).exists():
                invoice_number = f"INV-{invoice_date.strftime('%Y%m%d')}-{i:03d}-{random.randint(100,999)}"
            
            # ایجاد فاکتور
            invoice = Invoice.objects.create(
                invoice_number=invoice_number,
                supplier=supplier,
                store_name=supplier.name,
                invoice_date=invoice_date.date(),
                due_date=due_date.date(),
                tax_rate=Decimal('9.0'),  # 9% مالیات
                discount_rate=Decimal('0.0'),
                shipping_cost=Decimal(random.randint(50000, 200000)),
                status=random.choice(['draft', 'sent', 'approved']),
                payment_status='unpaid',
                created_by=user,
                notes=f'فاکتور نمونه شماره {i}'
            )
            
            # انتخاب 8-12 محصول برای فاکتور (60% مشترک + بقیه تصادفی)
            num_items = random.randint(8, 12)
            num_common = max(int(num_items * 0.6), 5)  # 60% از محصولات مشترک
            num_random = num_items - num_common
            
            # انتخاب محصولات برای این فاکتور
            invoice_products = random.sample(common_products, min(num_common, len(common_products)))
            if num_random > 0 and remaining_products:
                invoice_products += random.sample(remaining_products, min(num_random, len(remaining_products)))
            
            # اگر تعداد کافی نبود، از همه محصولات استفاده کن
            if len(invoice_products) < num_items:
                additional_needed = num_items - len(invoice_products)
                available = [p for p in products if p not in invoice_products]
                if available:
                    invoice_products += random.sample(available, min(additional_needed, len(available)))
            
            subtotal = Decimal('0')
            
            # ایجاد آیتم‌های فاکتور
            for product in invoice_products:
                quantity = Decimal(str(random.randint(1, 20)))
                unit_price = Decimal(str(random.randint(10000, 500000)))
                
                base_total = quantity * unit_price
                tax_amount = base_total * (invoice.tax_rate / Decimal('100'))
                line_total = base_total + tax_amount
                
                requester = random.choice(requesters) if requesters else None
                
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=product,
                    description=product.name,
                    quantity=quantity,
                    unit_price=unit_price,
                    tax_amount=tax_amount,
                    discount_amount=Decimal('0'),
                    line_total=line_total,
                    requester=requester
                )
                
                subtotal += base_total
            
            # محاسبه مجموع فاکتور
            tax_amount = subtotal * (invoice.tax_rate / Decimal('100'))
            total_amount = subtotal + tax_amount + invoice.shipping_cost
            
            invoice.subtotal = subtotal
            invoice.tax_amount = tax_amount
            invoice.total_amount = total_amount
            invoice.discount_amount = Decimal('0')
            invoice.cash_discount = Decimal('0')
            invoice.save()
            
            created_invoices.append(invoice)
            
            print(f"✓ فاکتور {i:2d}: {invoice_number} | {supplier.name[:30]:30s} | "
                  f"{invoice_date.strftime('%Y-%m-%d')} | {len(invoice_products):2d} محصول | "
                  f"{int(total_amount):,} ریال")
            
        except Exception as e:
            print(f"❌ خطا در ایجاد فاکتور {i}: {e}")
            continue
    
    print("=" * 60)
    print(f"\n✅ تعداد {len(created_invoices)} فاکتور با موفقیت ایجاد شد!")
    print(f"📊 محصولات مشترک: {[p.name for p in common_products[:5]]}...")
    print(f"📅 بازه تاریخی: {start_date.strftime('%Y-%m-%d')} تا {datetime.now().strftime('%Y-%m-%d')}")
    print(f"🏪 فروشندگان استفاده شده: {len(set([inv.supplier.name for inv in created_invoices]))}")

if __name__ == '__main__':
    print("🚀 شروع ایجاد فاکتورهای نمونه...\n")
    create_sample_invoices()
    print("\n✅ عملیات به پایان رسید!")
