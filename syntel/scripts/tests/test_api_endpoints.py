import requests
from requests.auth import HTTPBasicAuth

print("🧪 تست API های داشبورد قیمت")
print("=" * 60)

base_url = "http://127.0.0.1:8000"

# باید با یک کاربر لاگین کنید - این username و password را تغییر دهید
username = "admin"
password = "admin"  # رمز عبور واقعی را وارد کنید

print("\n1️⃣ تست API فیلترها:")
try:
    response = requests.get(
        f"{base_url}/api/dashboard/filters/",
        auth=HTTPBasicAuth(username, password)
    )
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ دسته‌بندی‌ها: {len(data['categories'])}")
        print(f"   ✓ فروشندگان: {len(data['suppliers'])}")
        
        if data['categories']:
            print("\n   دسته‌بندی‌ها:")
            for cat in data['categories'][:5]:
                print(f"      • {cat['name']} (ID: {cat['id']})")
        
        if data['suppliers']:
            print("\n   فروشندگان:")
            for sup in data['suppliers'][:5]:
                print(f"      • {sup['name']} (ID: {sup['id']})")
    else:
        print(f"   ❌ خطا: {response.text}")
except Exception as e:
    print(f"   ❌ خطا: {e}")

print("\n2️⃣ تست API آمار:")
try:
    response = requests.get(
        f"{base_url}/api/dashboard/stats/",
        auth=HTTPBasicAuth(username, password)
    )
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ افزایش: {data['increased_count']}")
        print(f"   ✓ کاهش: {data['decreased_count']}")
        print(f"   ✓ میانگین: {data['avg_change']}")
        if data['max_increase']:
            print(f"   ✓ بیشترین: {data['max_increase']['name']} ({data['max_increase']['percent']}%)")
    else:
        print(f"   ❌ خطا: {response.text}")
except Exception as e:
    print(f"   ❌ خطا: {e}")

print("\n3️⃣ تست API محصولات:")
try:
    response = requests.get(
        f"{base_url}/api/dashboard/products/changes/",
        auth=HTTPBasicAuth(username, password)
    )
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ تعداد محصولات: {len(data)}")
        if data:
            print("\n   نمونه محصولات:")
            for product in data[:3]:
                print(f"      • {product['name']} - {product.get('category_name', 'بدون دسته')}")
                print(f"        قیمت: {product['current_price']}, تغییر: {product['percent_change']}%")
    else:
        print(f"   ❌ خطا: {response.text}")
except Exception as e:
    print(f"   ❌ خطا: {e}")

print("\n4️⃣ تست فیلتر دسته‌بندی:")
try:
    # دریافت اولین دسته‌بندی
    response = requests.get(
        f"{base_url}/api/dashboard/filters/",
        auth=HTTPBasicAuth(username, password)
    )
    if response.status_code == 200:
        categories = response.json()['categories']
        if categories:
            test_cat_id = categories[0]['id']
            test_cat_name = categories[0]['name']
            
            print(f"   تست با دسته '{test_cat_name}' (ID: {test_cat_id}):")
            response = requests.get(
                f"{base_url}/api/dashboard/products/changes/",
                params={'category': test_cat_id},
                auth=HTTPBasicAuth(username, password)
            )
            print(f"   Status: {response.status_code}")
            if response.status_code == 200:
                products = response.json()
                print(f"   ✓ محصولات یافت شده: {len(products)}")
                if products:
                    for p in products[:3]:
                        print(f"      • {p['name']} ({p.get('category_name', '-')})")
                else:
                    print("   ⚠ هیچ محصولی در این دسته یافت نشد")
except Exception as e:
    print(f"   ❌ خطا: {e}")

print("\n" + "=" * 60)
print("✅ تست‌ها تکمیل شد")
print("\n💡 نکته: اگر خطای 401 دیدید، username و password را در اسکریپت درست وارد کنید")
