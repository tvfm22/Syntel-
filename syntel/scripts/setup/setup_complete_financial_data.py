"""
اسکریپت کامل ایجاد داده‌های مالی از اول
- پاکسازی داده‌های قبلی (اختیاری)
- ایجاد داده‌های پایه
- ایجاد 40 فاکتور (20 نقدی + 20 تامین‌کننده)
- هر فاکتور حداقل 5 محصول
- 90% محصولات تکراری
- درخواست‌ها قبل از فاکتورها
- تایید توسط مسئول خرید
- تقسیم بین انبار و خرید
- بازه زمانی یک ماه
"""
import os
import django
import random
from datetime import datetime, timedelta
from decimal import Decimal
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.utils import timezone
from django.db import transaction
from purchases.models import (
    User, Category, Product, Supplier, Unit,
    Invoice, InvoiceItem, PurchaseRequest, 
    PurchaseItem, Requester, PaymentType, PaymentLocation,
    PriceHistory, StockItem, Payment
)

# تاریخ شروع - یک ماه گذشته
START_DATE = timezone.now() - timedelta(days=30)
END_DATE = timezone.now()

def get_random_date():
    """تولید تاریخ تصادفی در بازه یک ماهه"""
    days = random.randint(0, 30)
    return START_DATE + timedelta(days=days)

def clear_old_data():
    """پاکسازی داده‌های قبلی (خودکار)"""
    print("\n🗑️  پاکسازی داده‌های یک ماه اخیر...")
    print("   توجه: فاکتورها، درخواست‌ها و تاریخچه قیمت یک ماه اخیر پاک می‌شود")
    print("   داده‌های پایه (محصولات، دسته‌بندی‌ها) حفظ می‌شود")
    
    # همیشه پاک می‌کنیم
    if True:
        print("\n  در حال پاکسازی...")
        one_month_ago = timezone.now() - timedelta(days=30)
        
        # پاک کردن پرداخت‌ها
        Payment.objects.filter(created_at__gte=one_month_ago).delete()
        
        # پاک کردن تاریخچه قیمت
        PriceHistory.objects.filter(date__gte=one_month_ago).delete()
        
        # پاک کردن فاکتورها (این آیتم‌ها را هم پاک می‌کند)
        Invoice.objects.filter(created_at__gte=one_month_ago).delete()
        
        # پاک کردن درخواست‌ها (این آیتم‌ها را هم پاک می‌کند)
        PurchaseRequest.objects.filter(created_at__gte=one_month_ago).delete()
        
        print("  ✓ داده‌های یک ماه اخیر پاک شد")

def create_units():
    """ایجاد واحدهای اندازه‌گیری"""
    print("\n📏 واحدهای اندازه‌گیری...")
    units_data = [
        ('کیلوگرم', 'kg'),
        ('گرم', 'g'),
        ('لیتر', 'L'),
        ('عدد', 'pc'),
        ('بسته', 'pkg'),
        ('کارتن', 'ctn'),
        ('متر', 'm'),
        ('قوطی', 'can'),
        ('بطری', 'btl'),
        ('جعبه', 'box'),
    ]
    
    units = {}
    for name, symbol in units_data:
        unit, created = Unit.objects.get_or_create(
            name=name,
            defaults={'symbol': symbol, 'is_active': True}
        )
        units[name] = unit
    
    print(f"  ✓ {len(units)} واحد آماده")
    return units

def create_categories():
    """ایجاد دسته‌بندی‌های محصول"""
    print("\n📁 دسته‌بندی‌ها...")
    
    categories_data = {
        'مواد غذایی': ['برنج و حبوبات', 'روغن و چربی', 'ادویه و چاشنی', 'کنسرو'],
        'نوشیدنی': ['نوشیدنی گازدار', 'آب و آبمیوه', 'چای و قهوه'],
        'پروتئین': ['گوشت قرمز', 'مرغ', 'ماهی', 'سوسیس'],
        'لبنیات': ['شیر و ماست', 'پنیر', 'کره'],
        'میوه و سبزی': ['میوه تازه', 'سبزیجات'],
        'مواد شوینده': ['ظرفشویی', 'لباسشویی', 'ضدعفونی'],
        'لوازم مصرفی': ['دستمال کاغذی', 'ظروف یکبار مصرف', 'کیسه زباله'],
    }
    
    categories = {}
    for parent_name, children in categories_data.items():
        parent, _ = Category.objects.get_or_create(
            name=parent_name,
            parent=None,
            defaults={'is_active': True}
        )
        categories[parent_name] = parent
        
        for child_name in children:
            child, _ = Category.objects.get_or_create(
                name=child_name,
                parent=parent,
                defaults={'is_active': True}
            )
            categories[child_name] = child
    
    print(f"  ✓ {len(categories)} دسته‌بندی آماده")
    return categories

def create_products(categories, units):
    """ایجاد محصولات (محصولات پرتکرار برای 90% استفاده در فاکتورها)"""
    print("\n📦 محصولات...")
    
    # محصولات اصلی که در 90% فاکتورها استفاده می‌شوند
    main_products_data = [
        ('برنج ایرانی درجه یک', 'برنج و حبوبات', 'کیلوگرم', 95000),
        ('برنج هندی', 'برنج و حبوبات', 'کیلوگرم', 72000),
        ('روغن مایع', 'روغن و چربی', 'لیتر', 98000),
        ('روغن زیتون', 'روغن و چربی', 'لیتر', 340000),
        ('نمک', 'ادویه و چاشنی', 'کیلوگرم', 18000),
        ('فلفل', 'ادویه و چاشنی', 'گرم', 200),
        ('زردچوبه', 'ادویه و چاشنی', 'گرم', 150),
        ('رب گوجه', 'کنسرو', 'کیلوگرم', 92000),
        ('کنسرو تن', 'کنسرو', 'قوطی', 85000),
        ('ماکارونی', 'کنسرو', 'بسته', 42000),
        ('نوشابه کوکا', 'نوشیدنی گازدار', 'بطری', 22000),
        ('نوشابه فانتا', 'نوشیدنی گازدار', 'بطری', 22000),
        ('دوغ', 'نوشیدنی گازدار', 'بطری', 18000),
        ('آب معدنی', 'آب و آبمیوه', 'بطری', 9000),
        ('آبمیوه پرتقال', 'آب و آبمیوه', 'لیتر', 75000),
        ('چای ایرانی', 'چای و قهوه', 'کیلوگرم', 480000),
        ('قهوه', 'چای و قهوه', 'بسته', 195000),
        ('گوشت گوسفند', 'گوشت قرمز', 'کیلوگرم', 920000),
        ('گوشت گاو', 'گوشت قرمز', 'کیلوگرم', 780000),
        ('مرغ کامل', 'مرغ', 'کیلوگرم', 165000),
        ('فیله مرغ', 'مرغ', 'کیلوگرم', 215000),
        ('ماهی قزل‌آلا', 'ماهی', 'کیلوگرم', 380000),
        ('میگو', 'ماهی', 'کیلوگرم', 750000),
        ('سوسیس', 'سوسیس', 'کیلوگرم', 240000),
        ('کالباس', 'سوسیس', 'کیلوگرم', 320000),
        ('شیر پرچرب', 'شیر و ماست', 'لیتر', 48000),
        ('ماست', 'شیر و ماست', 'کیلوگرم', 62000),
        ('پنیر سفید', 'پنیر', 'کیلوگرم', 360000),
        ('پنیر پیتزا', 'پنیر', 'کیلوگرم', 420000),
        ('کره', 'کره', 'کیلوگرم', 480000),
        ('سیب', 'میوه تازه', 'کیلوگرم', 95000),
        ('پرتقال', 'میوه تازه', 'کیلوگرم', 85000),
        ('موز', 'میوه تازه', 'کیلوگرم', 135000),
        ('گوجه فرنگی', 'سبزیجات', 'کیلوگرم', 52000),
        ('خیار', 'سبزیجات', 'کیلوگرم', 42000),
        ('پیاز', 'سبزیجات', 'کیلوگرم', 35000),
        ('سیب زمینی', 'سبزیجات', 'کیلوگرم', 38000),
        ('مایع ظرفشویی', 'ظرفشویی', 'لیتر', 72000),
        ('پودر لباسشویی', 'لباسشویی', 'کیلوگرم', 135000),
        ('وایتکس', 'ضدعفونی', 'لیتر', 45000),
        ('دستمال کاغذی', 'دستمال کاغذی', 'بسته', 52000),
        ('لیوان یکبار مصرف', 'ظروف یکبار مصرف', 'بسته', 42000),
        ('بشقاب یکبار مصرف', 'ظروف یکبار مصرف', 'بسته', 48000),
        ('کیسه زباله', 'کیسه زباله', 'بسته', 35000),
    ]
    
    products = []
    main_product_list = []
    
    for name, cat_name, unit_name, base_price in main_products_data:
        category = categories.get(cat_name)
        unit = units.get(unit_name)
        
        product, created = Product.objects.get_or_create(
            name=name,
            defaults={
                'category': category,
                'unit': unit,
                'min_stock': random.randint(10, 50),
                'current_stock': random.randint(50, 200),
                'is_active': True
            }
        )
        products.append(product)
        main_product_list.append((product, base_price))
    
    print(f"  ✓ {len(products)} محصول آماده")
    return products, main_product_list

def create_suppliers():
    """ایجاد تامین‌کنندگان"""
    print("\n🏢 تامین‌کنندگان...")
    
    suppliers_data = [
        ('شرکت پخش مواد غذایی شرق', 'SUP-FIN-001', 'محمد رضایی', '021-22334455'),
        ('تامین پروتئین سحر', 'SUP-FIN-002', 'علی احمدی', '021-33445566'),
        ('بازرگانی نوین غرب', 'SUP-FIN-003', 'سارا محمدی', '021-44556677'),
        ('شرکت لبنیات پاک', 'SUP-FIN-004', 'رضا کریمی', '031-55667788'),
        ('پخش نوشیدنی آرین', 'SUP-FIN-005', 'مریم حسینی', '021-66778899'),
        ('شوینده پاکیزه', 'SUP-FIN-006', 'حسین نوری', '021-77889900'),
        ('میوه و سبزی مرکزی', 'SUP-FIN-007', 'فاطمه موسوی', '021-88990011'),
        ('بازرگانی کیان', 'SUP-FIN-008', 'امیر صادقی', '021-99001122'),
    ]
    
    suppliers = []
    for name, code, contact, phone in suppliers_data:
        supplier, created = Supplier.objects.get_or_create(
            supplier_code=code,
            defaults={
                'name': name,
                'contact_person': contact,
                'phone': phone,
                'status': 'active',
            }
        )
        suppliers.append(supplier)
    
    print(f"  ✓ {len(suppliers)} تامین‌کننده آماده")
    return suppliers

def create_users():
    """ایجاد/دریافت کاربران"""
    print("\n👤 کاربران...")
    
    users = {}
    
    # مسئول خرید
    user, _ = User.objects.get_or_create(
        username='purchasing_mgr',
        defaults={
            'first_name': 'علی',
            'last_name': 'خریدار',
            'role': 'purchasing',
            'email': 'purchasing@syntel.com'
        }
    )
    if not user.has_usable_password():
        user.set_password('123456')
        user.save()
    users['purchasing'] = user
    
    # کاربر آشپزخانه
    user, _ = User.objects.get_or_create(
        username='kitchen_staff',
        defaults={
            'first_name': 'رضا',
            'last_name': 'آشپز',
            'role': 'kitchen',
        }
    )
    users['kitchen'] = user
    
    # کاربر بار
    user, _ = User.objects.get_or_create(
        username='bar_staff',
        defaults={
            'first_name': 'مریم',
            'last_name': 'بار',
            'role': 'bar',
        }
    )
    users['bar'] = user
    
    # مدیر
    user, _ = User.objects.get_or_create(
        username='manager_user',
        defaults={
            'first_name': 'محمد',
            'last_name': 'مدیر',
            'role': 'manager',
        }
    )
    users['manager'] = user
    
    # مالی
    user, _ = User.objects.get_or_create(
        username='finance_mgr',
        defaults={
            'first_name': 'سارا',
            'last_name': 'مالی',
            'role': 'finance_manager',
        }
    )
    users['finance'] = user
    
    print(f"  ✓ {len(users)} کاربر آماده")
    return users

def create_requesters():
    """ایجاد درخواست‌کنندگان"""
    print("\n👥 درخواست‌کنندگان...")
    
    requesters = {}
    for name in ['kitchen', 'bar', 'facilities', 'store']:
        requester, _ = Requester.objects.get_or_create(name=name)
        requesters[name] = requester
    
    print(f"  ✓ {len(requesters)} درخواست‌کننده آماده")
    return requesters

def create_purchase_requests(users, requesters, main_products):
    """
    ایجاد 50 درخواست خرید
    - قبل از فاکتورها
    - با آیتم‌های متناسب با فاکتورها
    """
    print("\n📝 ایجاد درخواست‌های خرید...")
    
    requests = []
    purchasing_manager = users['purchasing']
    manager = users['manager']
    
    for i in range(50):
        request_date = get_random_date()
        
        # انتخاب درخواست‌کننده و کاربر
        requester_key = random.choice(list(requesters.keys()))
        if requester_key == 'kitchen':
            user = users.get('kitchen', list(users.values())[0])
        elif requester_key == 'bar':
            user = users.get('bar', list(users.values())[0])
        else:
            user = list(users.values())[0]
        
        # وضعیت - اکثر تایید شده
        status = random.choices(
            ['pending', 'purchased', 'partial'],
            weights=[0.1, 0.7, 0.2]
        )[0]
        
        # ایجاد درخواست
        request = PurchaseRequest.objects.create(
            user=user,
            status=status,
            manager_confirmed=(status in ['purchased', 'partial']),
            manager_confirmed_by=manager if status in ['purchased', 'partial'] else None,
            manager_confirmed_at=request_date if status in ['purchased', 'partial'] else None,
        )
        PurchaseRequest.objects.filter(pk=request.pk).update(created_at=request_date)
        
        # اضافه کردن آیتم‌ها (5 تا 8 آیتم)
        num_items = random.randint(5, 8)
        selected_products = random.sample(main_products, min(num_items, len(main_products)))
        
        for product, base_price in selected_products:
            quantity = random.randint(5, 30)
            
            # تعیین وضعیت تامین
            if status == 'purchased':
                supply_status = random.choice(['purchased', 'from_stock'])
            elif status == 'partial':
                supply_status = random.choice(['purchased', 'from_stock', 'pending'])
            else:
                supply_status = 'pending'
            
            PurchaseItem.objects.create(
                request=request,
                product=product,
                name=product.name,
                quantity=quantity,
                unit=product.unit.name if product.unit else 'عدد',
                supply_status=supply_status,
                purchased_quantity=quantity if supply_status == 'purchased' else None,
            )
        
        requests.append(request)
        
        if (i + 1) % 10 == 0:
            print(f"  ✓ {i + 1} درخواست")
    
    print(f"  📊 {len(requests)} درخواست ایجاد شد")
    return requests

def create_invoices_with_requests(suppliers, main_products, users, requesters):
    """
    ایجاد 40 فاکتور (20 نقدی + 20 تامین‌کننده)
    - حداقل 5 محصول
    - 90% محصولات تکراری
    - متناسب با درخواست‌ها
    """
    print("\n🧾 ایجاد فاکتورها...")
    
    invoices = []
    purchasing_user = users['purchasing']
    
    # محصولات تکراری (90%)
    common_products = main_products[:int(len(main_products) * 0.9)]
    other_products = main_products[int(len(main_products) * 0.9):]
    
    # 20 فاکتور نقدی
    print("\n  💵 فاکتورهای نقدی:")
    cash_stores = [
        'فروشگاه رفاه',
        'هایپرمی',
        'فروشگاه اتکا',
        'سوپرمارکت سینا',
        'فروشگاه محلی',
    ]
    
    for i in range(20):
        invoice_date = get_random_date()
        
        # انتخاب محصولات (90% تکراری)
        num_products = random.randint(5, 10)
        num_common = int(num_products * 0.9)
        
        selected = random.sample(common_products, min(num_common, len(common_products)))
        if other_products and (num_products - num_common) > 0:
            selected.extend(random.sample(other_products, min(num_products - num_common, len(other_products))))
        
        # ایجاد فاکتور
        invoice = Invoice.objects.create(
            invoice_number=f'CASH-{invoice_date.strftime("%Y%m%d")}-{i+1:03d}',
            store_name=random.choice(cash_stores),
            supplier=None,
            invoice_date=invoice_date.date(),
            due_date=invoice_date.date(),
            status='paid',
            payment_status='paid',
            tax_rate=Decimal('9'),
            created_by=purchasing_user,
        )
        
        # اضافه کردن آیتم‌ها
        total = Decimal('0')
        for product, base_price in selected:
            quantity = Decimal(str(random.randint(3, 20)))
            unit_price = Decimal(str(int(base_price * random.uniform(0.9, 1.1))))
            line_total = quantity * unit_price
            total += line_total
            
            InvoiceItem.objects.create(
                invoice=invoice,
                product=product,
                description=product.name,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
                requester=random.choice(list(requesters.values())),
            )
        
        invoice.subtotal = total
        invoice.tax_amount = total * Decimal('0.09')
        invoice.total_amount = total + invoice.tax_amount
        invoice.save()
        
        Invoice.objects.filter(pk=invoice.pk).update(created_at=invoice_date)
        invoices.append(invoice)
        
        if (i + 1) % 5 == 0:
            print(f"    ✓ {i + 1} فاکتور نقدی")
    
    # 20 فاکتور تامین‌کننده
    print("\n  🏢 فاکتورهای تامین‌کننده:")
    
    for i in range(20):
        invoice_date = get_random_date()
        supplier = random.choice(suppliers)
        
        # انتخاب محصولات (90% تکراری)
        num_products = random.randint(5, 12)
        num_common = int(num_products * 0.9)
        
        selected = random.sample(common_products, min(num_common, len(common_products)))
        if other_products and (num_products - num_common) > 0:
            selected.extend(random.sample(other_products, min(num_products - num_common, len(other_products))))
        
        # وضعیت‌ها
        status_options = [
            ('approved', 'partial'),
            ('approved', 'unpaid'),
            ('paid', 'paid'),
            ('sent', 'unpaid'),
        ]
        status, payment_status = random.choice(status_options)
        
        # ایجاد فاکتور
        invoice = Invoice.objects.create(
            invoice_number=f'INV-{invoice_date.strftime("%Y%m")}-{supplier.supplier_code[-3:]}-{i+1:02d}',
            supplier=supplier,
            invoice_date=invoice_date.date(),
            due_date=(invoice_date + timedelta(days=30)).date(),
            status=status,
            payment_status=payment_status,
            tax_rate=Decimal('9'),
            discount_rate=Decimal(str(random.choice([0, 2, 3, 5]))),
            created_by=purchasing_user,
        )
        
        # اضافه کردن آیتم‌ها
        total = Decimal('0')
        for product, base_price in selected:
            quantity = Decimal(str(random.randint(5, 50)))
            unit_price = Decimal(str(int(base_price * random.uniform(0.9, 1.1))))
            line_total = quantity * unit_price
            total += line_total
            
            InvoiceItem.objects.create(
                invoice=invoice,
                product=product,
                description=product.name,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
                requester=random.choice(list(requesters.values())),
            )
        
        discount = total * invoice.discount_rate / 100
        tax = (total - discount) * Decimal('0.09')
        invoice.subtotal = total
        invoice.discount_amount = discount
        invoice.tax_amount = tax
        invoice.total_amount = total - discount + tax
        invoice.save()
        
        Invoice.objects.filter(pk=invoice.pk).update(created_at=invoice_date)
        invoices.append(invoice)
        
        if (i + 1) % 5 == 0:
            print(f"    ✓ {i + 1} فاکتور تامین‌کننده")
    
    print(f"\n  📊 {len(invoices)} فاکتور ایجاد شد")
    return invoices

def create_price_history(invoices):
    """ایجاد تاریخچه قیمت از فاکتورها"""
    print("\n📈 تاریخچه قیمت...")
    
    count = 0
    for invoice in invoices:
        for item in invoice.invoice_items.all():
            if item.product:
                last_price = PriceHistory.objects.filter(
                    product=item.product
                ).order_by('-date').first()
                
                percent_change = None
                if last_price and last_price.price:
                    try:
                        percent_change = ((item.unit_price - last_price.price) / last_price.price) * 100
                    except:
                        percent_change = Decimal('0')
                
                PriceHistory.objects.create(
                    product=item.product,
                    price=item.unit_price,
                    date=timezone.make_aware(datetime.combine(invoice.invoice_date, datetime.min.time())),
                    invoice=invoice,
                    supplier=invoice.supplier,
                    percent_change=percent_change
                )
                count += 1
    
    print(f"  ✓ {count} رکورد تاریخچه قیمت")
    return count

def create_stock_items(main_products):
    """بروزرسانی موجودی انبار"""
    print("\n📦 موجودی انبار...")
    
    count = 0
    for product, base_price in main_products:
        StockItem.objects.update_or_create(
            product=product,
            defaults={
                'quantity': Decimal(str(random.randint(50, 300))),
                'unit': product.unit,
                'min_quantity': Decimal(str(random.randint(10, 50))),
            }
        )
        count += 1
    
    print(f"  ✓ {count} موجودی بروزرسانی شد")
    return count

def create_payments(invoices, users):
    """ایجاد پرداخت‌ها"""
    print("\n💰 پرداخت‌ها...")
    
    count = 0
    for invoice in invoices:
        if invoice.payment_status in ['paid', 'partial']:
            if invoice.payment_status == 'paid':
                amount = invoice.total_amount
            else:
                amount = invoice.total_amount * Decimal(str(random.uniform(0.3, 0.7)))
            
            payment = Payment.objects.create(
                amount=amount,
                payment_date=invoice.invoice_date,
                payment_status='completed',
                created_by=users.get('finance'),
            )
            payment.invoices.add(invoice)
            count += 1
    
    print(f"  ✓ {count} پرداخت ایجاد شد")
    return count

def show_summary():
    """نمایش خلاصه"""
    from django.db.models import Sum, Count
    
    print("\n" + "=" * 70)
    print("📊 خلاصه نهایی")
    print("=" * 70)
    
    # فاکتورها
    total_invoices = Invoice.objects.count()
    cash = Invoice.objects.filter(supplier__isnull=True).count()
    supplier = Invoice.objects.filter(supplier__isnull=False).count()
    total_amount = Invoice.objects.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    
    print(f"\n🧾 فاکتورها:")
    print(f"  تعداد کل: {total_invoices}")
    print(f"  نقدی: {cash}")
    print(f"  تامین‌کننده: {supplier}")
    print(f"  مجموع مبلغ: {total_amount:,.0f} ریال")
    
    # درخواست‌ها
    requests = PurchaseRequest.objects.count()
    pending = PurchaseRequest.objects.filter(status='pending').count()
    purchased = PurchaseRequest.objects.filter(status='purchased').count()
    
    print(f"\n📝 درخواست‌ها:")
    print(f"  تعداد کل: {requests}")
    print(f"  در انتظار: {pending}")
    print(f"  خریداری شده: {purchased}")
    
    # آیتم‌ها
    items = PurchaseItem.objects.count()
    from_stock = PurchaseItem.objects.filter(supply_status='from_stock').count()
    bought = PurchaseItem.objects.filter(supply_status='purchased').count()
    
    print(f"\n📦 آیتم‌های درخواست:")
    print(f"  تعداد کل: {items}")
    print(f"  تامین از انبار: {from_stock}")
    print(f"  خرید جدید: {bought}")
    
    # سایر داده‌ها
    print(f"\n📈 داده‌های تحلیلی:")
    print(f"  تاریخچه قیمت: {PriceHistory.objects.count()} رکورد")
    print(f"  موجودی انبار: {StockItem.objects.count()} محصول")
    print(f"  پرداخت‌ها: {Payment.objects.count()} پرداخت")
    
    print("\n" + "=" * 70)
    print("✅ تمام داده‌ها آماده استفاده در نمودارهای آمار و تحلیل")
    print("=" * 70)

@transaction.atomic
def main():
    print("=" * 70)
    print("🚀 راه‌اندازی کامل سیستم مالی از اول")
    print("=" * 70)
    
    try:
        # 0. پاکسازی (اختیاری)
        clear_old_data()
        
        # 1. داده‌های پایه
        print("\n" + "=" * 70)
        print("📚 مرحله 1: ایجاد داده‌های پایه")
        print("=" * 70)
        units = create_units()
        categories = create_categories()
        products, main_products = create_products(categories, units)
        suppliers = create_suppliers()
        users = create_users()
        requesters = create_requesters()
        
        # 2. درخواست‌های خرید
        print("\n" + "=" * 70)
        print("📝 مرحله 2: ایجاد درخواست‌های خرید (قبل از فاکتورها)")
        print("=" * 70)
        requests = create_purchase_requests(users, requesters, main_products)
        
        # 3. فاکتورها
        print("\n" + "=" * 70)
        print("🧾 مرحله 3: ایجاد فاکتورها (40 عدد)")
        print("=" * 70)
        invoices = create_invoices_with_requests(suppliers, main_products, users, requesters)
        
        # 4. داده‌های تحلیلی
        print("\n" + "=" * 70)
        print("📊 مرحله 4: ایجاد داده‌های تحلیلی")
        print("=" * 70)
        create_price_history(invoices)
        create_stock_items(main_products)
        create_payments(invoices, users)
        
        # 5. خلاصه
        show_summary()
        
        print("\n🎉 تمام عملیات با موفقیت انجام شد!")
        
    except Exception as e:
        print(f"\n❌ خطا: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == '__main__':
    main()
