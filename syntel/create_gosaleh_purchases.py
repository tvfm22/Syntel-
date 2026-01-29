import os
import django
from datetime import datetime, timedelta
from decimal import Decimal
import random

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Category, Product, Unit, Supplier, Invoice, InvoiceItem

# پیدا کردن دسته اصلی گوشت
parent_category = Category.objects.filter(name__icontains='گوشت').exclude(parent__isnull=False).first()

if not parent_category:
    print("دسته گوشت پیدا نشد، از دسته اول استفاده می‌شود")
    parent_category = Category.objects.filter(parent__isnull=True).first()

print(f"دسته اصلی: {parent_category.name}")

# ایجاد زیردسته گوشت راسته گوساله
subcategory, created = Category.objects.get_or_create(
    name='گوشت راسته گوساله',
    parent=parent_category,
    defaults={'is_active': True}
)

if created:
    print(f"✓ زیردسته جدید ایجاد شد: {subcategory.name}")
else:
    print(f"✓ زیردسته موجود است: {subcategory.name}")

# پیدا کردن یا ایجاد واحد
unit, _ = Unit.objects.get_or_create(
    name='کیلوگرم',
    defaults={'abbreviation': 'kg'}
)

# ایجاد محصول
product, created = Product.objects.get_or_create(
    name='گوشت راسته گوساله',
    category=subcategory,
    defaults={
        'unit': unit,
        'is_active': True,
        'description': 'گوشت راسته گوساله درجه یک'
    }
)

if created:
    print(f"✓ محصول جدید ایجاد شد: {product.name}")
else:
    print(f"✓ محصول موجود است: {product.name}")

# پیدا کردن یا ایجاد فروشندگان
supplier_names = [
    'قصابی علی',
    'فروشگاه گوشت رضا',
    'بازار گوشت مهدی',
    'قصابی حسین'
]

suppliers = []
for supplier_name in supplier_names:
    supplier, created = Supplier.objects.get_or_create(
        name=supplier_name,
        defaults={
            'contact_person': supplier_name,
            'phone': f'021-{random.randint(22000000, 88999999)}',
            'status': 'active',
            'supplier_code': f'SUP-{random.randint(1000, 9999)}'
        }
    )
    suppliers.append(supplier)
    if created:
        print(f"✓ فروشنده جدید: {supplier_name}")

# ایجاد 8 فاکتور در بازه 2 ماهه
print("\nایجاد فاکتورها...")

# تاریخ پایان: امروز
end_date = datetime.now().date()
# تاریخ شروع: 2 ماه قبل
start_date = end_date - timedelta(days=60)

# قیمت پایه
base_price = 450000

invoices_created = 0

for i in range(8):
    # انتخاب تاریخ تصادفی در بازه
    days_diff = (end_date - start_date).days
    random_days = random.randint(0, days_diff)
    invoice_date = start_date + timedelta(days=random_days)
    
    # انتخاب فروشنده تصادفی
    supplier = random.choice(suppliers)
    
    # محاسبه قیمت با رشد تدریجی (0 تا 10%)
    progress = i / 7  # از 0 تا 1
    price_increase = progress * 0.10  # 0% تا 10%
    unit_price = int(base_price * (1 + price_increase))
    
    # مقدار تصادفی بین 10 تا 50 کیلو
    quantity = random.uniform(10, 50)
    
    # ایجاد فاکتور
    invoice = Invoice.objects.create(
        invoice_number=f'INV-GOSALEH-{datetime.now().strftime("%Y%m%d")}-{i+1:03d}',
        supplier=supplier,
        invoice_date=invoice_date,
        due_date=invoice_date + timedelta(days=30),
        status='approved',
        total_amount=Decimal(str(quantity * unit_price)),
        notes=f'خرید گوشت راسته گوساله از {supplier.name}'
    )
    
    # ایجاد آیتم فاکتور
    invoice_item = InvoiceItem.objects.create(
        invoice=invoice,
        product=product,
        quantity=Decimal(str(round(quantity, 2))),
        unit_price=Decimal(str(unit_price)),
        line_total=Decimal(str(quantity * unit_price))
    )
    
    invoices_created += 1
    print(f"  {invoices_created}. {invoice_date} - {supplier.name} - {quantity:.1f} کیلو × {unit_price:,} = {invoice_item.line_total:,} ریال")

print(f"\n✓ {invoices_created} فاکتور با موفقیت ایجاد شد")
print(f"✓ دسته: {parent_category.name} > {subcategory.name}")
print(f"✓ محصول: {product.name}")
print(f"✓ بازه زمانی: {start_date} تا {end_date}")
