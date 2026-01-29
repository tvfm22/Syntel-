import os
import django
import sys
from pathlib import Path

# تنظیم Django
sys.path.append(str(Path(__file__).resolve().parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import (
    Supplier, Invoice, InvoiceItem, Product, 
    PriceHistory, User
)
from django.utils import timezone
from datetime import timedelta, date
import random
from decimal import Decimal

def get_or_create_cash_suppliers():
    """ایجاد یا دریافت فروشگاه‌های نقدی"""
    print("\n🏪 در حال ایجاد/دریافت فروشگاه‌های نقدی...")
    
    cash_suppliers_data = [
        {
            'name': 'فروشگاه زنجیره‌ای شهروند',
            'supplier_code': 'CASH001',
            'phone': '021-11223344',
            'address': 'تهران، میدان ونک',
            'is_cash_store': True,
        },
        {
            'name': 'هایپرمارکت اتکا',
            'supplier_code': 'CASH002',
            'phone': '021-22334455',
            'address': 'تهران، سعادت‌آباد',
            'is_cash_store': True,
        },
        {
            'name': 'فروشگاه رفاه',
            'supplier_code': 'CASH003',
            'phone': '021-33445566',
            'address': 'تهران، جنت‌آباد',
            'is_cash_store': True,
        },
        {
            'name': 'هایپراستار',
            'supplier_code': 'CASH004',
            'phone': '021-44556677',
            'address': 'تهران، پونک',
            'is_cash_store': True,
        },
        {
            'name': 'فروشگاه اسنپ مارکت',
            'supplier_code': 'CASH005',
            'phone': '021-55667788',
            'address': 'تهران، نیاوران',
            'is_cash_store': True,
        },
    ]
    
    cash_suppliers = []
    for data in cash_suppliers_data:
        supplier, created = Supplier.objects.get_or_create(
            supplier_code=data['supplier_code'],
            defaults={
                'name': data['name'],
                'phone': data['phone'],
                'address': data['address'],
                'status': 'active',
                'supplier_type': 'other'  # برای فروشگاه‌های نقدی از 'other' استفاده می‌کنیم
            }
        )
        # علامت‌گذاری به عنوان فروشگاه نقدی (با نوت یا فیلد دیگر)
        if supplier.notes != 'فروشگاه نقدی':
            supplier.notes = 'فروشگاه نقدی'
            supplier.save()
        
        if created:
            print(f"  ✓ {supplier.name} ساخته شد")
        else:
            print(f"  → {supplier.name} قبلاً وجود داشت")
        cash_suppliers.append(supplier)
    
    return cash_suppliers

def get_all_suppliers():
    """دریافت همه تامین‌کنندگان"""
    return list(Supplier.objects.filter(status='active'))

def get_all_products():
    """دریافت همه محصولات"""
    return list(Product.objects.all())

def create_invoices_for_two_months(suppliers, cash_suppliers, products):
    """ایجاد فاکتورها برای دو ماه آینده"""
    print("\n🧾 در حال ایجاد فاکتورها برای دو ماه آینده...")
    
    # دریافت کاربر
    try:
        user = User.objects.filter(role='manager').first()
        if not user:
            user = User.objects.filter(is_superuser=True).first()
        if not user:
            user = User.objects.first()
    except:
        print("  ✗ کاربری یافت نشد!")
        return []
    
    if not products:
        print("  ✗ محصولی یافت نشد!")
        return []
    
    invoices = []
    
    # تاریخ شروع: امروز
    start_date = timezone.now().date()
    
    # تعداد فاکتورها در دو ماه (60 روز)
    # هر تامین‌کننده حداقل 4 فاکتور در ماه = 8 فاکتور در 2 ماه
    all_suppliers = suppliers + cash_suppliers
    
    invoice_counter = Invoice.objects.count() + 1
    
    invoice_statuses = ['draft', 'sent', 'approved', 'paid']
    payment_statuses = ['unpaid', 'partial', 'paid']
    
    # قیمت‌های پایه محصولات
    product_base_prices = {}
    for product in products:
        # دریافت آخرین قیمت از PriceHistory
        latest_price = PriceHistory.objects.filter(product=product).order_by('-date').first()
        if latest_price:
            product_base_prices[product.id] = float(latest_price.price)
        else:
            # قیمت پیش‌فرض تصادفی
            product_base_prices[product.id] = random.uniform(10000, 500000)
    
    print(f"\n  📅 ایجاد فاکتورها از تاریخ {start_date} به مدت 60 روز...")
    
    # برای هر تامین‌کننده، 8 فاکتور ایجاد می‌کنیم (در طول 60 روز)
    for supplier in all_suppliers:
        is_cash = supplier in cash_suppliers
        supplier_type = "نقدی" if is_cash else "عادی"
        
        print(f"\n  📦 در حال ایجاد فاکتورها برای {supplier.name} ({supplier_type})...")
        
        # 8 فاکتور در 60 روز = هر 7-8 روز یک فاکتور
        for invoice_num in range(8):
            # محاسبه تاریخ فاکتور
            days_offset = invoice_num * 7 + random.randint(0, 3)
            if days_offset >= 60:
                days_offset = 59
            
            invoice_date = start_date + timedelta(days=days_offset)
            
            # وضعیت تصادفی
            status = random.choice(invoice_statuses)
            payment_status = random.choice(payment_statuses)
            
            # ساخت فاکتور
            invoice = Invoice.objects.create(
                invoice_number=f'INV-2026-{invoice_counter:05d}',
                supplier=supplier,
                invoice_date=invoice_date,
                due_date=invoice_date + timedelta(days=30),
                status=status,
                payment_status=payment_status,
                tax_rate=Decimal('9'),
                discount_amount=Decimal(str(random.randint(0, 300000))),
                notes=f'فاکتور {supplier_type} - {supplier.name}',
                created_by=user,
            )
            invoice_counter += 1
            
            # انتخاب 8 تا 15 محصول تصادفی
            num_items = random.randint(8, 15)
            selected_products = random.sample(products, min(num_items, len(products)))
            
            # ایجاد آیتم‌های فاکتور
            for product in selected_products:
                quantity = Decimal(str(random.randint(3, 40)))
                
                # قیمت با تغییرات تصادفی
                base_price = product_base_prices.get(product.id, 50000)
                # تغییر قیمت بین -5% تا +10%
                price_change = random.uniform(-0.05, 0.10)
                unit_price = Decimal(str(base_price * (1 + price_change)))
                
                line_total = quantity * unit_price
                
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=product,
                    description=f'{product.name}',
                    quantity=quantity,
                    unit_price=unit_price,
                    line_total=line_total,
                    notes=f'تاریخ: {invoice_date.strftime("%Y-%m-%d")}'
                )
                
                # ذخیره تاریخچه قیمت
                # بررسی آخرین قیمت
                last_price_obj = PriceHistory.objects.filter(
                    product=product,
                    supplier=supplier
                ).order_by('-date').first()
                
                if last_price_obj:
                    previous_price = float(last_price_obj.price)
                else:
                    previous_price = base_price
                
                # محاسبه درصد تغییر
                if previous_price > 0:
                    percent_change = ((float(unit_price) - previous_price) / previous_price) * 100
                else:
                    percent_change = 0
                
                # ذخیره تاریخچه
                PriceHistory.objects.create(
                    product=product,
                    supplier=supplier,
                    price=unit_price,
                    date=invoice_date,
                    percent_change=Decimal(str(percent_change))
                )
            
            # محاسبه مجموع فاکتور
            invoice.save()
            
            invoices.append(invoice)
            print(f"    ✓ {invoice.invoice_number} - تاریخ: {invoice_date} - {num_items} قلم - {invoice.total_amount:,.0f} ریال")
    
    return invoices

def add_supplier_type_field():
    """به‌روزرسانی نوت فروشگاه‌های نقدی"""
    print("\n🔧 بررسی و به‌روزرسانی فروشگاه‌های نقدی...")
    try:
        cash_codes = ['CASH001', 'CASH002', 'CASH003', 'CASH004', 'CASH005']
        updated = Supplier.objects.filter(supplier_code__in=cash_codes).update(
            notes='فروشگاه نقدی'
        )
        print(f"  ✓ {updated} فروشگاه نقدی به‌روز شد")
    except Exception as e:
        print(f"  ⚠ خطا: {e}")

def main():
    print("=" * 80)
    print("🚀 ایجاد داده‌های دو ماه آینده برای سیستم Syntel")
    print("=" * 80)
    
    try:
        # اضافه کردن فیلد supplier_type
        add_supplier_type_field()
        
        # دریافت/ایجاد فروشگاه‌های نقدی
        cash_suppliers = get_or_create_cash_suppliers()
        
        # دریافت همه تامین‌کنندگان دیگر
        all_suppliers = get_all_suppliers()
        regular_suppliers = [s for s in all_suppliers if s not in cash_suppliers]
        
        print(f"\n📊 تامین‌کنندگان:")
        print(f"  • تامین‌کنندگان عادی: {len(regular_suppliers)}")
        print(f"  • فروشگاه‌های نقدی: {len(cash_suppliers)}")
        
        # دریافت همه محصولات
        products = get_all_products()
        print(f"  • محصولات: {len(products)}")
        
        if not products:
            print("\n⚠ هیچ محصولی یافت نشد! لطفاً ابتدا محصولات را ایجاد کنید.")
            return
        
        # ایجاد فاکتورها
        invoices = create_invoices_for_two_months(regular_suppliers, cash_suppliers, products)
        
        print("\n" + "=" * 80)
        print("✅ عملیات با موفقیت انجام شد!")
        print("=" * 80)
        print(f"\n📊 خلاصه:")
        print(f"  • تعداد کل تامین‌کنندگان: {len(all_suppliers)}")
        print(f"  • تعداد فاکتورهای ایجاد شده: {len(invoices)}")
        
        # آمار فاکتورها
        if invoices:
            total_items = sum(inv.invoice_items.count() for inv in invoices)
            total_amount = sum(inv.total_amount or 0 for inv in invoices)
            
            # فاکتورهای نقدی
            cash_invoices = [inv for inv in invoices if inv.supplier in cash_suppliers]
            
            print(f"  • تعداد فاکتورهای عادی: {len(invoices) - len(cash_invoices)}")
            print(f"  • تعداد فاکتورهای نقدی: {len(cash_invoices)}")
            print(f"  • تعداد کل اقلام: {total_items}")
            print(f"  • مجموع مبلغ: {total_amount:,.0f} ریال")
        
        print("\n💡 نکات:")
        print("  • فاکتورهای نقدی با نام واقعی فروشگاه ذخیره شده‌اند")
        print("  • در لیست تحلیل، می‌توانید فیلتری برای 'فروشگاه‌های نقدی' اضافه کنید")
        print("  • تاریخچه قیمت‌ها برای همه محصولات به‌روزرسانی شد")
        
    except Exception as e:
        print(f"\n❌ خطا: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
