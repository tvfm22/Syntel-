import os
import django
from decimal import Decimal
from datetime import datetime, timedelta
import random

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Product, PriceHistory, Supplier

print("🔄 بروزرسانی قیمت‌های محصولات با نوسانات واقعی‌تر")
print("=" * 60)

# دریافت محصولات
products = list(Product.objects.all())
suppliers = list(Supplier.objects.filter(status='active'))

if not suppliers:
    print("❌ فروشنده‌ای یافت نشد!")
    exit()

# تعریف قیمت پایه واقعی برای محصولات
base_prices = {
    'آب معدنی': 5000,
    'آبلیمو': 80000,
    'برگر': 150000,
    'برنج ایرانی': 180000,
    'بشقاب کاغذی': 50000,
    'بیسکویت شکلاتی': 45000,
    'پنیر سفید': 120000,
    'چای سیاه': 200000,
    'خامه فرم‌گرفته': 85000,
    'دستمال کاغذی': 35000,
    'زنجبیل': 150000,
    'شامپو': 90000,
    'شکر سفید': 60000,
    'شکلات تلخ': 120000,
    'شیر گاو': 30000,
    'قاشق و چنگال پلاستیکی': 40000,
    'قهوه عربیکا': 350000,
    'قهوه فوری': 180000,
    'کیسه زباله': 25000,
    'کیک شکلاتی': 140000,
    'کیک وانیلی': 130000,
    'لیمون': 70000,
    'لیوان کاغذی': 45000,
    'ماست یونانی': 65000,
    'مایع ظرفشویی': 55000,
    'نان تست': 35000,
    'نان سنگک': 15000,
    'نان لواش': 12000,
    'نایلون': 30000,
    'نعناع تازه': 40000,
    'نمک': 20000,
    'نوشابه کوکا': 18000,
}

print("\n🗑️ پاک کردن تاریخچه قیمت قبلی...")
PriceHistory.objects.all().delete()
print("✓ تاریخچه قبلی پاک شد")

print("\n📊 ایجاد تاریخچه قیمت واقعی‌تر...")

# تاریخ شروع (6 ماه پیش)
start_date = datetime.now() - timedelta(days=180)

total_created = 0

for product in products:
    # دریافت قیمت پایه
    base_price = base_prices.get(product.name, 50000)
    
    # تولید 5-8 رکورد قیمت برای هر محصول
    num_records = random.randint(5, 8)
    
    current_price = base_price
    
    for i in range(num_records):
        # تاریخ تصادفی
        days_offset = int((180 / num_records) * i) + random.randint(-5, 5)
        record_date = start_date + timedelta(days=days_offset)
        
        # انتخاب فروشنده تصادفی
        supplier = random.choice(suppliers)
        
        # محاسبه تغییر قیمت (بین -5% تا +10%)
        # احتمال افزایش بیشتر از کاهش
        if random.random() < 0.8:  # 80% احتمال افزایش
            change_percent = random.uniform(0, 10)
        else:  # 20% احتمال کاهش
            change_percent = random.uniform(-5, 0)
        
        # قیمت جدید
        new_price = current_price * (1 + change_percent / 100)
        new_price = int(new_price / 1000) * 1000  # گرد کردن به هزار تومان
        
        # محاسبه درصد تغییر نسبت به قیمت قبلی
        if i == 0:
            percent_change = Decimal('0')
        else:
            percent_change = Decimal(str(((new_price - current_price) / current_price) * 100))
        
        # ایجاد رکورد
        PriceHistory.objects.create(
            product=product,
            price=Decimal(str(new_price)),
            date=record_date,
            supplier=supplier,
            percent_change=percent_change
        )
        
        current_price = new_price
        total_created += 1
    
    print(f"✓ {product.name}: {num_records} رکورد (قیمت نهایی: {int(current_price):,} ریال)")

print("\n" + "=" * 60)
print(f"✅ {total_created} رکورد قیمت با نوسانات واقعی‌تر ایجاد شد!")

# نمایش آمار
print("\n📊 آمار نوسانات:")
all_changes = PriceHistory.objects.exclude(percent_change=0)
if all_changes.exists():
    max_increase = all_changes.order_by('-percent_change').first()
    max_decrease = all_changes.order_by('percent_change').first()
    
    print(f"   بیشترین افزایش: {max_increase.product.name} (+{max_increase.percent_change:.2f}%)")
    print(f"   بیشترین کاهش: {max_decrease.product.name} ({max_decrease.percent_change:.2f}%)")
    
    avg_change = sum([abs(float(p.percent_change)) for p in all_changes]) / all_changes.count()
    print(f"   میانگین نوسان: {avg_change:.2f}%")
