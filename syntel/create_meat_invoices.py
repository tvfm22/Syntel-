import os
import django
import sys
from datetime import datetime, timedelta
from decimal import Decimal
import random

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import (
    Category, Product, Supplier, Invoice, InvoiceItem,
    Unit, Requester
)
from django.contrib.auth import get_user_model
from django.utils import timezone as django_timezone

User = get_user_model()

def create_sample_meat_data():
    print("🥩 شروع ایجاد داده‌های نمونه گوشت...")
    
    # 1. ایجاد دسته‌بندی اصلی "گوشت"
    meat_category, created = Category.objects.get_or_create(
        name="گوشت",
        defaults={
            'parent': None,
            'is_active': True
        }
    )
    if created:
        print(f"✅ دسته‌بندی اصلی '{meat_category.name}' ایجاد شد")
    else:
        print(f"ℹ️  دسته‌بندی '{meat_category.name}' از قبل وجود دارد")
    
    # 2. ایجاد زیردسته "گوشت گاو"
    beef_category, created = Category.objects.get_or_create(
        name="گوشت گاو",
        defaults={
            'parent': meat_category,
            'is_active': True
        }
    )
    if created:
        print(f"✅ زیردسته '{beef_category.name}' ایجاد شد")
    else:
        print(f"ℹ️  زیردسته '{beef_category.name}' از قبل وجود دارد")
    
    # 3. ایجاد واحد کیلوگرم
    kg_unit, _ = Unit.objects.get_or_create(
        name="کیلوگرم",
        defaults={'abbreviation': 'kg'}
    )
    
    # 4. ایجاد فروشندگان
    suppliers_data = [
        {'name': 'قصابی رضا', 'code': 'MEAT001'},
        {'name': 'گوشت فروشی امیر', 'code': 'MEAT002'},
        {'name': 'پروتئین محمد', 'code': 'MEAT003'},
        {'name': 'قصابی سنتی علی', 'code': 'MEAT004'},
    ]
    
    suppliers = []
    for sup_data in suppliers_data:
        supplier, created = Supplier.objects.get_or_create(
            name=sup_data['name'],
            defaults={
                'supplier_code': sup_data['code'],
                'status': 'active',
                'phone': f'021-{random.randint(10000000, 99999999)}',
                'mobile': f'0912{random.randint(1000000, 9999999)}',
                'address': 'تهران'
            }
        )
        suppliers.append(supplier)
        if created:
            print(f"✅ فروشنده '{supplier.name}' ایجاد شد")
    
    # 5. ایجاد محصولات گوشت گاو
    products_data = [
        {'name': 'گوشت راسته گاو', 'base_price': 450000},
        {'name': 'گوشت چرخ کرده گاو', 'base_price': 380000},
        {'name': 'گوشت ماهیچه گاو', 'base_price': 420000},
        {'name': 'گوشت گردن گاو', 'base_price': 350000},
        {'name': 'گوشت سردست گاو', 'base_price': 400000},
    ]
    
    products = []
    for prod_data in products_data:
        product, created = Product.objects.get_or_create(
            name=prod_data['name'],
            defaults={
                'category': beef_category,
                'unit': kg_unit,
                'min_stock': 10,
                'current_stock': 50,
                'is_active': True
            }
        )
        product.base_price = prod_data['base_price']
        products.append(product)
        if created:
            print(f"✅ محصول '{product.name}' ایجاد شد")
    
    # 6. ایجاد کاربر برای فاکتورها
    try:
        user = User.objects.filter(role='purchasing').first()
        if not user:
            user = User.objects.filter(is_staff=True).first()
        if not user:
            print("❌ هیچ کاربری برای ایجاد فاکتور یافت نشد")
            return
    except Exception as e:
        print(f"❌ خطا در یافتن کاربر: {e}")
        return
    
    # 7. دریافت Requester
    requester = Requester.objects.first()
    if not requester:
        requester = Requester.objects.create(name='kitchen')
        print("✅ Requester ایجاد شد")
    
    # 8. ایجاد فاکتورها در یک ماه گذشته
    print("\n📝 ایجاد فاکتورهای خرید...")
    
    today = datetime.now().date()
    invoices_created = 0
    
    # برای هر محصول، 4-6 خرید در ماه گذشته
    for product in products:
        num_purchases = random.randint(4, 6)
        base_price = product.base_price
        
        for i in range(num_purchases):
            # تاریخ تصادفی در ماه گذشته
            days_ago = random.randint(1, 30)
            invoice_date = today - timedelta(days=days_ago)
            
            # انتخاب فروشنده تصادفی
            supplier = random.choice(suppliers)
            
            # محاسبه قیمت با رشد 0 تا 10 درصد
            # قیمت‌ها باید از قدیم به جدید افزایش داشته باشند
            growth_factor = 1 + (days_ago / 30) * random.uniform(0, 0.10)
            unit_price = int(base_price / growth_factor)
            
            # مقدار تصادفی (10 تا 50 کیلو)
            quantity = Decimal(str(random.randint(10, 50)))
            
            # ایجاد فاکتور
            try:
                invoice = Invoice.objects.create(
                    supplier=supplier,
                    invoice_date=invoice_date,
                    due_date=invoice_date + timedelta(days=7),
                    status='approved',
                    payment_status='paid',
                    subtotal=Decimal('0'),
                    total_amount=Decimal('0'),
                    created_by=user,
                    approved_by=user,
                    approved_at=django_timezone.now()
                )
                
                # ایجاد آیتم فاکتور
                line_total = quantity * Decimal(str(unit_price))
                
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=product,
                    description=f"{product.name} - خرید از {supplier.name}",
                    quantity=quantity,
                    unit_price=Decimal(str(unit_price)),
                    line_total=line_total,
                    requester=requester
                )
                
                # به‌روزرسانی مجموع فاکتور
                invoice.subtotal = line_total
                invoice.total_amount = line_total
                invoice.save()
                
                invoices_created += 1
                
            except Exception as e:
                print(f"❌ خطا در ایجاد فاکتور: {e}")
                import traceback
                traceback.print_exc()
                continue
    
    print(f"\n✅ {invoices_created} فاکتور با موفقیت ایجاد شد!")
    print(f"✅ {len(products)} محصول گوشت گاو")
    print(f"✅ {len(suppliers)} فروشنده")
    print(f"✅ بازه زمانی: {30} روز گذشته")
    print(f"✅ رشد قیمت: 0 تا 10 درصد")
    
    print("\n🎯 می‌توانید به صفحه زیر بروید:")
    print("   http://localhost:8000/analytics/category-analysis/")
    print("   و دسته 'گوشت' و زیردسته 'گوشت گاو' را انتخاب کنید")

if __name__ == '__main__':
    try:
        create_sample_meat_data()
    except Exception as e:
        print(f"❌ خطای کلی: {e}")
        import traceback
        traceback.print_exc()
