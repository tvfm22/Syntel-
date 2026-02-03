import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Category, Supplier, Product, PriceHistory
from django.db.models import Subquery, OuterRef

print("🔍 تست API داده‌ها")
print("=" * 60)

# 1. تست فیلترها
print("\n1️⃣ دسته‌بندی‌های فعال:")
categories = Category.objects.filter(is_active=True)
print(f"   تعداد: {categories.count()}")
for cat in categories[:5]:
    print(f"   • {cat.name} (ID: {cat.id})")

print("\n2️⃣ فروشندگان فعال:")
suppliers = Supplier.objects.filter(status='active')
print(f"   تعداد: {suppliers.count()}")
for sup in suppliers[:5]:
    print(f"   • {sup.name} (ID: {sup.id})")

# 2. تست محصولات با قیمت
print("\n3️⃣ محصولات با قیمت:")
latest_history = PriceHistory.objects.filter(
    product=OuterRef('pk')
).order_by('-date')

products = Product.objects.annotate(
    current_price=Subquery(latest_history.values('price')[:1]),
    percent_change=Subquery(latest_history.values('percent_change')[:1]),
    last_update=Subquery(latest_history.values('date')[:1])
).filter(current_price__isnull=False)

print(f"   تعداد: {products.count()}")
for p in products[:5]:
    cat_name = p.category.name if p.category else 'بدون دسته'
    print(f"   • {p.name} - {cat_name} - {p.current_price} ریال ({p.percent_change}%)")

# 3. تست فیلتر دسته‌بندی
print("\n4️⃣ تست فیلتر دسته‌بندی:")
if categories.exists():
    test_cat = categories.first()
    filtered_products = products.filter(category=test_cat)
    print(f"   دسته '{test_cat.name}': {filtered_products.count()} محصول")
    for p in filtered_products[:3]:
        print(f"      • {p.name}")

# 4. آمار
print("\n5️⃣ آمار:")
increased = products.filter(percent_change__gt=0).count()
decreased = products.filter(percent_change__lt=0).count()
high_increase = products.filter(percent_change__gt=5).count()
print(f"   افزایش قیمت: {increased}")
print(f"   کاهش قیمت: {decreased}")
print(f"   افزایش بیش از 5%: {high_increase}")

print("\n" + "=" * 60)
print("✅ همه داده‌ها آماده است")
