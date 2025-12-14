import os
import django
import requests

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import PriceHistory, Product
from django.db.models import Subquery, OuterRef

print("🔍 بررسی داده‌ها و API")
print("=" * 60)

# بررسی داده‌های PriceHistory
print(f"\n1. تعداد کل PriceHistory: {PriceHistory.objects.count()}")
print(f"2. تعداد محصولات منحصر به فرد: {PriceHistory.objects.values('product').distinct().count()}")

# بررسی محصولات با قیمت
latest = PriceHistory.objects.filter(product=OuterRef('pk')).order_by('-date')
products = Product.objects.annotate(
    current_price=Subquery(latest.values('price')[:1]),
    percent_change=Subquery(latest.values('percent_change')[:1])
).filter(current_price__isnull=False)

print(f"3. محصولات با قیمت فعال: {products.count()}")

# نمایش چند نمونه
print("\n📊 نمونه محصولات:")
for p in products[:5]:
    print(f"   • {p.name}: {p.current_price} ریال (تغییر: {p.percent_change}%)")

# محصولات با افزایش بیش از 5%
high_increase = products.filter(percent_change__gt=5).count()
print(f"\n4. محصولات با افزایش بیش از 5%: {high_increase}")

# بررسی API (اگر سرور در حال اجرا باشد)
print("\n🌐 بررسی API:")
try:
    # توجه: این فقط اگر سرور در حال اجرا باشد کار می‌کند
    response = requests.get('http://127.0.0.1:8000/purchases/api/dashboard/stats/', timeout=2)
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ API Stats کار می‌کند")
        print(f"     - افزایش: {data.get('increased_count', 0)}")
        print(f"     - کاهش: {data.get('decreased_count', 0)}")
        print(f"     - میانگین: {data.get('avg_change', 0)}")
    else:
        print(f"   ⚠ API Status: {response.status_code}")
except requests.exceptions.ConnectionError:
    print("   ⚠ سرور Django در حال اجرا نیست!")
    print("   💡 لطفا سرور را با دستور زیر اجرا کنید:")
    print("      python manage.py runserver")
except Exception as e:
    print(f"   ❌ خطا: {e}")

print("\n" + "=" * 60)
