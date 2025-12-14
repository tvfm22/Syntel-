import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.views_dashboard import ProductChangeListAPIView, DashboardFiltersAPIView
from rest_framework.test import APIRequestFactory
from purchases.models import User

print("🧪 تست مستقیم API Views")
print("=" * 60)

# ایجاد factory برای request های fake
factory = APIRequestFactory()

# دریافت یک کاربر
user = User.objects.first()
if not user:
    print("❌ کاربری یافت نشد!")
    exit()

print(f"✓ استفاده از کاربر: {user.username}")

# 1. تست API فیلترها
print("\n1️⃣ تست API فیلترها:")
request = factory.get('/api/dashboard/filters/')
request.user = user

view = DashboardFiltersAPIView.as_view()
response = view(request)

print(f"   Status: {response.status_code}")
if response.status_code == 200:
    data = response.data
    print(f"   ✓ دسته‌بندی‌ها: {len(data['categories'])}")
    print(f"   ✓ فروشندگان: {len(data['suppliers'])}")
    
    if data['categories']:
        print("\n   دسته‌بندی‌های موجود:")
        for cat in data['categories'][:10]:
            print(f"      • {cat['name']} (ID: {cat['id']})")
else:
    print(f"   ❌ خطا: {response.data}")

# 2. تست API محصولات بدون فیلتر
print("\n2️⃣ تست API محصولات (بدون فیلتر):")
request = factory.get('/api/dashboard/products/changes/')
request.user = user

view = ProductChangeListAPIView.as_view()
response = view(request)

print(f"   Status: {response.status_code}")
if response.status_code == 200:
    products = response.data
    print(f"   ✓ تعداد محصولات: {len(products)}")
    if products:
        print("\n   نمونه محصولات:")
        for product in products[:5]:
            print(f"      • {product['name']}")
            print(f"        دسته: {product.get('category_name', 'ندارد')}")
            print(f"        قیمت: {product['current_price']}, تغییر: {product['percent_change']}%")

# 3. تست فیلتر دسته‌بندی
print("\n3️⃣ تست فیلتر دسته‌بندی:")
# دریافت اولین دسته‌بندی از response قبلی
filters_request = factory.get('/api/dashboard/filters/')
filters_request.user = user
filters_response = DashboardFiltersAPIView.as_view()(filters_request)

if filters_response.status_code == 200 and filters_response.data['categories']:
    test_cat = filters_response.data['categories'][0]
    test_cat_id = test_cat['id']
    test_cat_name = test_cat['name']
    
    print(f"   تست با دسته '{test_cat_name}' (ID: {test_cat_id})")
    
    request = factory.get(f'/api/dashboard/products/changes/?category={test_cat_id}')
    request.user = user
    
    view = ProductChangeListAPIView.as_view()
    response = view(request)
    
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        filtered_products = response.data
        print(f"   ✓ محصولات فیلتر شده: {len(filtered_products)}")
        if filtered_products:
            print(f"\n   محصولات در دسته '{test_cat_name}':")
            for p in filtered_products:
                print(f"      • {p['name']} - {p.get('category_name', '-')}")
        else:
            print(f"   ⚠ هیچ محصولی در دسته '{test_cat_name}' یافت نشد")
            print("   این طبیعی است اگر محصولی در این دسته قیمت ثبت نشده باشد")

print("\n" + "=" * 60)
print("✅ تست‌ها تکمیل شد")
