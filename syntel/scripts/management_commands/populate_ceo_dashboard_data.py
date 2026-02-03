"""
اسکریپت ایجاد داده‌های نمونه کامل برای داشبورد مدیرعامل
"""
import os
import django
import random
from datetime import datetime, timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.utils import timezone
from purchases.models import (
    User, Category, Product, Supplier, 
    Invoice, InvoiceItem, PurchaseRequest, 
    PurchaseItem, StockItem
)

def create_complete_test_data():
    print("=" * 70)
    print("🚀 شروع ایجاد داده‌های کامل برای داشبورد مدیرعامل")
    print("=" * 70)
    
    # 1. ایجاد کاربران نمونه
    print("\n👥 مرحله 1: ایجاد کاربران...")
    users = []
    user_data = [
        {'username': 'store_manager', 'first_name': 'رضا', 'last_name': 'احمدی', 'role': 'store'},
        {'username': 'bar_staff', 'first_name': 'مریم', 'last_name': 'محمدی', 'role': 'bar'},
        {'username': 'kitchen_chef', 'first_name': 'علی', 'last_name': 'کریمی', 'role': 'kitchen'},
        {'username': 'finance_manager', 'first_name': 'سارا', 'last_name': 'رضایی', 'role': 'finance'},
        {'username': 'requester1', 'first_name': 'حسن', 'last_name': 'محمودی', 'role': 'requester'},
        {'username': 'requester2', 'first_name': 'فاطمه', 'last_name': 'حسینی', 'role': 'requester'},
    ]
    
    for data in user_data:
        user, created = User.objects.get_or_create(
            username=data['username'],
            defaults={
                'first_name': data['first_name'],
                'last_name': data['last_name'],
                'role': data['role'],
                'email': f"{data['username']}@syntel.com",
                'is_active': True
            }
        )
        if created:
            user.set_password('123456')
            user.save()
            print(f"   ✅ {data['first_name']} {data['last_name']} ({data['role']})")
        users.append(user)
    
    print(f"   📊 کل کاربران: {User.objects.count()}")
    
    # 2. ایجاد دسته‌بندی‌ها
    print("\n📁 مرحله 2: ایجاد دسته‌بندی‌ها...")
    categories = []
    category_names = [
        'مواد غذایی اولیه', 
        'نوشیدنی و آشامیدنی', 
        'لوازم بهداشتی و نظافت', 
        'لوازم آشپزخانه', 
        'محصولات یخچالی و منجمد',
        'ادویه و چاشنی',
        'میوه و سبزیجات'
    ]
    
    for name in category_names:
        cat, created = Category.objects.get_or_create(
            name=name,
            defaults={'description': f'دسته‌بندی {name}'}
        )
        categories.append(cat)
        if created:
            print(f"   ✅ {name}")
    
    print(f"   📊 کل دسته‌بندی‌ها: {Category.objects.count()}")
    
    # 3. ایجاد محصولات
    print("\n📦 مرحله 3: ایجاد محصولات...")
    products = []
    product_data = [
        # مواد غذایی اولیه
        ('برنج ایرانی', categories[0], 10, 15),
        ('آرد سفید', categories[0], 8, 20),
        ('روغن مایع', categories[0], 12, 18),
        ('شکر', categories[0], 15, 25),
        ('نمک', categories[0], 20, 30),
        
        # نوشیدنی
        ('نوشابه', categories[1], 20, 30),
        ('آب معدنی', categories[1], 30, 50),
        ('چای', categories[1], 10, 15),
        ('قهوه', categories[1], 8, 12),
        ('دلستر', categories[1], 15, 25),
        
        # بهداشتی
        ('دستمال کاغذی', categories[2], 25, 40),
        ('مایع ظرفشویی', categories[2], 20, 35),
        ('پاک‌کننده سطح', categories[2], 15, 30),
        ('صابون مایع', categories[2], 18, 28),
        
        # لوازم آشپزخانه
        ('ظرف یکبار مصرف', categories[3], 30, 50),
        ('فویل آلومینیوم', categories[3], 12, 20),
        ('سلفون', categories[3], 15, 25),
        
        # محصولات یخچالی
        ('گوشت گوسفندی', categories[4], 5, 8),
        ('مرغ', categories[4], 8, 12),
        ('ماهی', categories[4], 6, 10),
        ('پنیر', categories[4], 10, 15),
        ('ماست', categories[4], 12, 20),
        
        # ادویه
        ('زعفران', categories[5], 3, 5),
        ('زردچوبه', categories[5], 8, 12),
        ('فلفل سیاه', categories[5], 10, 15),
        
        # میوه و سبزی
        ('سیب‌زمینی', categories[6], 20, 30),
        ('پیاز', categories[6], 25, 35),
        ('گوجه‌فرنگی', categories[6], 15, 25),
    ]
    
    for name, cat, min_qty, current_qty in product_data:
        prod, created = Product.objects.get_or_create(
            name=name,
            defaults={
                'category': cat,
                'min_stock': min_qty,
                'current_stock': current_qty + random.randint(0, 50),
                'description': f'محصول {name}'
            }
        )
        products.append(prod)
        
        # ایجاد موجودی انبار
        stock, created = StockItem.objects.get_or_create(
            product=prod,
            defaults={
                'quantity': current_qty + random.randint(10, 80),
                'min_quantity': min_qty
            }
        )
        
        if created:
            print(f"   ✅ {name}")
    
    print(f"   📊 کل محصولات: {Product.objects.count()}")
    print(f"   📊 کل موجودی انبار: {StockItem.objects.count()}")
    
    # 4. ایجاد تامین‌کنندگان
    print("\n🏢 مرحله 4: ایجاد تامین‌کنندگان...")
    suppliers = []
    supplier_data = [
        ('شرکت پخش مواد غذایی پارس', 'آقای احمدی', '021-22334455'),
        ('شرکت توزیع نوشیدنی سحر', 'خانم محمدی', '021-33445566'),
        ('شرکت گوشت و مرغ البرز', 'آقای کریمی', '021-44556677'),
        ('فروشگاه زنجیره‌ای هایپراستار', 'آقای رضایی', '021-55667788'),
        ('شرکت بهداشتی گلرنگ', 'خانم حسینی', '021-66778899'),
        ('شرکت توزیع میوه و تره‌بار تازه', 'آقای محمودی', '021-77889900'),
        ('شرکت ادویه‌جات زرین', 'خانم نوری', '021-88990011'),
    ]
    
    for name, contact, phone in supplier_data:
        sup, created = Supplier.objects.get_or_create(
            name=name,
            defaults={
                'contact_person': contact,
                'phone': phone,
                'address': 'تهران، ایران',
                'status': 'active'
            }
        )
        suppliers.append(sup)
        if created:
            print(f"   ✅ {name}")
    
    print(f"   📊 کل تامین‌کنندگان: {Supplier.objects.count()}")
    
    # 5. ایجاد درخواست‌های خرید (3 ماه گذشته)
    print("\n📝 مرحله 5: ایجاد درخواست‌های خرید...")
    requests_created = 0
    
    for i in range(50):  # 50 درخواست در 3 ماه
        days_ago = random.randint(1, 90)
        created_date = timezone.now() - timedelta(days=days_ago)
        
        req = PurchaseRequest.objects.create(
            user=random.choice(users),
            status=random.choice(['pending', 'purchased', 'partial', 'draft']),
            created_at=created_date
        )
        
        # اضافه کردن 2 تا 6 آیتم به هر درخواست
        num_items = random.randint(2, 6)
        for _ in range(num_items):
            product = random.choice(products)
            PurchaseItem.objects.create(
                request=req,
                product=product,
                name=product.name,
                quantity=random.randint(5, 100),
                unit='عدد',
                supply_status=random.choice(['pending', 'purchased', 'from_stock'])
            )
        
        requests_created += 1
        if requests_created % 10 == 0:
            print(f"   ⏳ {requests_created} درخواست ایجاد شد...")
    
    print(f"   ✅ {requests_created} درخواست خرید ایجاد شد")
    print(f"   📊 کل درخواست‌های خرید: {PurchaseRequest.objects.count()}")
    
    # 6. ایجاد فاکتورها (3 ماه گذشته)
    print("\n🧾 مرحله 6: ایجاد فاکتورها...")
    invoices_created = 0
    total_invoice_items = 0
    
    for i in range(60):  # 60 فاکتور در 3 ماه
        days_ago = random.randint(1, 90)
        created_date = timezone.now() - timedelta(days=days_ago)
        invoice_date = created_date.date()
        due_date = (created_date + timedelta(days=30)).date()
        
        supplier = random.choice(suppliers)
        invoice = Invoice.objects.create(
            invoice_number=f'INV-{timezone.now().year}-{10000 + i}',
            supplier=supplier,
            invoice_date=invoice_date,
            due_date=due_date,
            status=random.choice(['draft', 'approved', 'paid', 'sent']),
            payment_status=random.choice(['unpaid', 'partial', 'paid']),
            notes=f'فاکتور خرید شماره {i+1} از {supplier.name}',
            created_by=random.choice(users),
            created_at=created_date
        )
        
        # اضافه کردن 3 تا 10 آیتم به هر فاکتور
        subtotal = Decimal('0')
        num_items = random.randint(3, 10)
        
        for _ in range(num_items):
            product = random.choice(products)
            quantity = random.randint(10, 200)
            # قیمت‌های واقعی‌تر (10,000 تا 1,000,000 تومان)
            base_price = random.randint(10000, 1000000)
            # تغییرات قیمتی ±20%
            price_variance = random.uniform(0.80, 1.20)
            unit_price = Decimal(str(int(base_price * price_variance)))
            line_total = unit_price * quantity
            subtotal += line_total
            
            InvoiceItem.objects.create(
                invoice=invoice,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total
            )
            total_invoice_items += 1
        
        # محاسبه مالیات و تخفیف
        tax_rate = Decimal(str(random.choice([0, 9, 10])))  # 0%, 9%, 10%
        discount_rate = Decimal(str(random.choice([0, 2, 5, 10])))  # 0%, 2%, 5%, 10%
        
        tax_amount = (subtotal * tax_rate) / 100
        discount_amount = (subtotal * discount_rate) / 100
        total = subtotal + tax_amount - discount_amount
        
        invoice.subtotal = subtotal
        invoice.tax_rate = tax_rate
        invoice.tax_amount = tax_amount
        invoice.discount_rate = discount_rate
        invoice.discount_amount = discount_amount
        invoice.total_amount = total
        invoice.save()
        
        invoices_created += 1
        if invoices_created % 15 == 0:
            print(f"   ⏳ {invoices_created} فاکتور ایجاد شد...")
    
    print(f"   ✅ {invoices_created} فاکتور ایجاد شد")
    print(f"   ✅ {total_invoice_items} آیتم فاکتور ایجاد شد")
    print(f"   📊 کل فاکتورها: {Invoice.objects.count()}")
    print(f"   📊 کل آیتم‌های فاکتور: {InvoiceItem.objects.count()}")
    
    # 7. خلاصه نهایی
    print("\n" + "=" * 70)
    print("✅ تمام داده‌ها با موفقیت ایجاد شد!")
    print("=" * 70)
    print("\n📊 خلاصه کامل:")
    print(f"   👥 کاربران: {User.objects.count()}")
    print(f"   📁 دسته‌بندی‌ها: {Category.objects.count()}")
    print(f"   📦 محصولات: {Product.objects.count()}")
    print(f"   📦 موجودی انبار: {StockItem.objects.count()}")
    print(f"   🏢 تامین‌کنندگان: {Supplier.objects.count()}")
    print(f"   📝 درخواست‌های خرید: {PurchaseRequest.objects.count()}")
    print(f"   🧾 فاکتورها: {Invoice.objects.count()}")
    print(f"   📋 آیتم‌های فاکتور: {InvoiceItem.objects.count()}")
    
    # محاسبه جمع هزینه‌های تایید شده
    approved_total = Invoice.objects.filter(status='approved').aggregate(
        total=models.Sum('total_amount')
    )['total'] or 0
    
    print(f"\n💰 جمع هزینه‌های تایید شده: {approved_total:,.0f} تومان")
    
    print("\n" + "=" * 70)
    print("🌐 برای مشاهده داشبورد مدیرعامل:")
    print("=" * 70)
    print("   1️⃣  مطمئن شوید سرور Django در حال اجراست:")
    print("       python manage.py runserver")
    print("\n   2️⃣  با یوزر مدیرعامل لاگین کنید:")
    print("       👤 نام کاربری: ceo")
    print("       🔐 رمز عبور: ceo123")
    print("\n   3️⃣  به این آدرس مراجعه کنید:")
    print("       🔗 http://localhost:8000/ceo-dashboard/")
    print("\n" + "=" * 70)

if __name__ == '__main__':
    from django.db import models
    create_complete_test_data()
