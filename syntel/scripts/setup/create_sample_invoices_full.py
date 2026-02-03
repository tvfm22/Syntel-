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
    Category, Unit, User
)
from django.utils import timezone
from datetime import timedelta
import random
from decimal import Decimal

def create_suppliers():
    """ساخت 5 تامین‌کننده"""
    suppliers_data = [
        {
            'name': 'شرکت پخش مواد غذایی آرین',
            'supplier_code': 'SUP001',
            'phone': '021-88776655',
            'address': 'تهران، میدان ونک، خیابان ملاصدرا',
            'postal_code': '1991735844',
        },
        {
            'name': 'تامین‌کنندگان صنعتی سپهر',
            'supplier_code': 'SUP002',
            'phone': '021-44332211',
            'address': 'تهران، بزرگراه آزادگان، خیابان صنعتی',
            'postal_code': '1234567890',
        },
        {
            'name': 'شرکت بازرگانی نوین پارس',
            'supplier_code': 'SUP003',
            'phone': '031-33445566',
            'address': 'اصفهان، خیابان چهارباغ عباسی',
            'postal_code': '8163914811',
        },
        {
            'name': 'گروه تجاری کیان صنعت',
            'supplier_code': 'SUP004',
            'phone': '051-37778899',
            'address': 'مشهد، بلوار وکیل‌آباد، پلاک 125',
            'postal_code': '9185843575',
        },
        {
            'name': 'فروشگاه عمده فروشی رضوان',
            'supplier_code': 'SUP005',
            'phone': '061-33221100',
            'address': 'اهواز، خیابان کیانپارس، نبش کوچه 14',
            'postal_code': '6155914785',
        }
    ]
    
    suppliers = []
    print("\n🏪 در حال ساخت تامین‌کنندگان...")
    for data in suppliers_data:
        supplier, created = Supplier.objects.get_or_create(
            supplier_code=data['supplier_code'],
            defaults={
                'name': data['name'],
                'phone': data['phone'],
                'address': data['address'],
                'postal_code': data['postal_code'],
                'status': 'active'
            }
        )
        if created:
            print(f"  ✓ {supplier.name} ساخته شد")
        else:
            print(f"  → {supplier.name} قبلاً وجود داشت")
        suppliers.append(supplier)
    
    return suppliers

def create_products_and_categories():
    """ساخت محصولات و دسته‌بندی‌ها"""
    print("\n📦 در حال ساخت دسته‌بندی‌ها و محصولات...")
    
    # دسته‌بندی‌ها
    categories_data = [
        'مواد غذایی', 'لوازم التحریر', 'لوازم نظافت', 
        'تجهیزات اداری', 'لوازم الکترونیکی', 'ابزارآلات'
    ]
    categories = []
    for cat_name in categories_data:
        cat, created = Category.objects.get_or_create(
            name=cat_name,
            defaults={'description': f'دسته‌بندی {cat_name}'}
        )
        categories.append(cat)
        if created:
            print(f"  ✓ دسته‌بندی {cat_name}")
    
    # واحدها
    units_data = ['عدد', 'کیلوگرم', 'لیتر', 'بسته', 'کارتن', 'متر']
    units = []
    for unit_name in units_data:
        unit, created = Unit.objects.get_or_create(name=unit_name)
        units.append(unit)
    
    # محصولات
    products_data = [
        ('برنج هندی درجه یک', 'مواد غذایی', 'کیلوگرم'),
        ('روغن مایع آفتابگردان', 'مواد غذایی', 'لیتر'),
        ('شکر سفید', 'مواد غذایی', 'کیلوگرم'),
        ('چای ایرانی', 'مواد غذایی', 'کیلوگرم'),
        ('ماکارونی', 'مواد غذایی', 'بسته'),
        ('رب گوجه فرنگی', 'مواد غذایی', 'کیلوگرم'),
        ('قلم پلاستیکی', 'لوازم التحریر', 'عدد'),
        ('دفتر 100 برگ', 'لوازم التحریر', 'عدد'),
        ('پوشه فانتزی', 'لوازم التحریر', 'عدد'),
        ('ماژیک وایت برد', 'لوازم التحریر', 'عدد'),
        ('کاغذ A4', 'لوازم التحریر', 'بسته'),
        ('مداد مشکی', 'لوازم التحریر', 'عدد'),
        ('مایع ظرفشویی', 'لوازم نظافت', 'لیتر'),
        ('پودر لباسشویی', 'لوازم نظافت', 'کیلوگرم'),
        ('وایتکس', 'لوازم نظافت', 'لیتر'),
        ('دستمال کاغذی', 'لوازم نظافت', 'بسته'),
        ('سطل زباله', 'لوازم نظافت', 'عدد'),
        ('جارو برقی', 'تجهیزات اداری', 'عدد'),
        ('پرینتر لیزری', 'تجهیزات اداری', 'عدد'),
        ('میز اداری', 'تجهیزات اداری', 'عدد'),
        ('صندلی اداری', 'تجهیزات اداری', 'عدد'),
        ('کیبورد و موس بی‌سیم', 'لوازم الکترونیکی', 'عدد'),
        ('هارد اکسترنال 1 ترابایت', 'لوازم الکترونیکی', 'عدد'),
        ('فلش مموری 64 گیگ', 'لوازم الکترونیکی', 'عدد'),
        ('کابل شبکه', 'لوازم الکترونیکی', 'متر'),
        ('پیچ گوشتی', 'ابزارآلات', 'عدد'),
        ('چکش', 'ابزارآلات', 'عدد'),
        ('انبردست', 'ابزارآلات', 'عدد'),
        ('متر نواری', 'ابزارآلات', 'عدد'),
        ('قفل درب', 'ابزارآلات', 'عدد'),
    ]
    
    products = []
    for prod_name, cat_name, unit_name in products_data:
        category = next(c for c in categories if c.name == cat_name)
        unit = next(u for u in units if u.name == unit_name)
        
        prod, created = Product.objects.get_or_create(
            name=prod_name,
            defaults={
                'category': category,
                'unit': unit,
                'description': f'محصول {prod_name}',
                'min_stock': 10,
                'current_stock': 50
            }
        )
        products.append(prod)
    
    print(f"  ✓ {len(products)} محصول آماده شد")
    return products

def create_invoices(suppliers, products):
    """ساخت 20 فاکتور با حداقل 10 قلم"""
    print("\n🧾 در حال ساخت فاکتورها...")
    
    # گرفتن یوزر
    try:
        user = User.objects.filter(role='manager').first()
        if not user:
            user = User.objects.filter(is_superuser=True).first()
        if not user:
            user = User.objects.first()
    except:
        print("  ✗ کاربری یافت نشد!")
        return []
    
    invoices = []
    base_date = timezone.now() - timedelta(days=60)
    
    invoice_statuses = ['draft', 'sent', 'approved', 'paid']
    payment_statuses = ['unpaid', 'partial', 'paid']
    
    # قیمت‌های نمونه برای محصولات
    product_prices = {
        'برنج هندی درجه یک': 180000,
        'روغن مایع آفتابگردان': 95000,
        'شکر سفید': 65000,
        'چای ایرانی': 320000,
        'ماکارونی': 42000,
        'رب گوجه فرنگی': 88000,
        'قلم پلاستیکی': 3500,
        'دفتر 100 برگ': 25000,
        'پوشه فانتزی': 15000,
        'ماژیک وایت برد': 18000,
        'کاغذ A4': 145000,
        'مداد مشکی': 5000,
        'مایع ظرفشویی': 45000,
        'پودر لباسشویی': 125000,
        'وایتکس': 38000,
        'دستمال کاغذی': 28000,
        'سطل زباله': 85000,
        'جارو برقی': 2500000,
        'پرینتر لیزری': 8500000,
        'میز اداری': 4200000,
        'صندلی اداری': 3500000,
        'کیبورد و موس بی‌سیم': 550000,
        'هارد اکسترنال 1 ترابایت': 2800000,
        'فلش مموری 64 گیگ': 320000,
        'کابل شبکه': 15000,
        'پیچ گوشتی': 45000,
        'چکش': 95000,
        'انبردست': 120000,
        'متر نواری': 35000,
        'قفل درب': 180000,
    }
    
    for i in range(20):
        # انتخاب تصادفی تامین‌کننده
        supplier = random.choice(suppliers)
        
        # تاریخ تصادفی
        days_offset = random.randint(0, 60)
        invoice_date = base_date + timedelta(days=days_offset)
        
        # وضعیت تصادفی
        status = random.choice(invoice_statuses)
        payment_status = random.choice(payment_statuses)
        
        # ساخت فاکتور
        invoice = Invoice.objects.create(
            invoice_number=f'INV-2025-{i+1:04d}',
            supplier=supplier,
            invoice_date=invoice_date.date(),
            due_date=(invoice_date + timedelta(days=30)).date(),
            status=status,
            payment_status=payment_status,
            tax_rate=Decimal('9'),
            discount_amount=Decimal(str(random.randint(0, 500000))),
            notes=f'فاکتور شماره {i+1} - {supplier.name}',
            created_by=user,
        )
        
        # اضافه کردن 10 تا 15 قلم کالا
        num_items = random.randint(10, 15)
        selected_products = random.sample(products, min(num_items, len(products)))
        
        for product in selected_products:
            quantity = Decimal(str(random.randint(5, 50)))
            base_price = product_prices.get(product.name, 50000)
            unit_price = Decimal(str(base_price * random.uniform(0.9, 1.1)))
            
            line_total = quantity * unit_price
            
            InvoiceItem.objects.create(
                invoice=invoice,
                product=product,
                description=f'{product.name}',
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
                notes=f'تحویل فوری'
            )
        
        # بعد از اضافه کردن همه آیتم‌ها، محاسبه مجموع
        invoice.save()  # محاسبه خودکار توسط متد save
        
        invoices.append(invoice)
        print(f"  ✓ فاکتور {invoice.invoice_number} - {supplier.name} - {num_items} قلم - مبلغ: {invoice.total_amount:,} ریال")
    
    return invoices

def main():
    print("=" * 70)
    print("🚀 ساخت داده‌های نمونه برای سیستم Syntel")
    print("=" * 70)
    
    try:
        # ساخت تامین‌کنندگان
        suppliers = create_suppliers()
        
        # ساخت محصولات
        products = create_products_and_categories()
        
        # ساخت فاکتورها
        invoices = create_invoices(suppliers, products)
        
        print("\n" + "=" * 70)
        print("✅ عملیات با موفقیت انجام شد!")
        print("=" * 70)
        print(f"\n📊 خلاصه:")
        print(f"  • تعداد تامین‌کنندگان: {len(suppliers)}")
        print(f"  • تعداد محصولات: {len(products)}")
        print(f"  • تعداد فاکتورها: {len(invoices)}")
        
        # آمار فاکتورها
        total_items = sum(invoice.invoice_items.count() for invoice in invoices)
        total_amount = sum(invoice.total_amount or 0 for invoice in invoices)
        print(f"  • تعداد کل اقلام فاکتور: {total_items}")
        print(f"  • مجموع مبلغ فاکتورها: {total_amount:,.0f} ریال")
        
        print("\n💡 می‌توانید از پنل ادمین یا داشبورد مالی فاکتورها را مشاهده کنید.")
        
    except Exception as e:
        print(f"\n❌ خطا: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
