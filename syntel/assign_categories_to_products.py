import os
import django
from decimal import Decimal

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Product, Category, PriceHistory

print("🔍 بررسی محصولات و دسته‌بندی‌ها")
print("=" * 60)

# بررسی محصولات بدون دسته‌بندی
products_without_category = Product.objects.filter(category__isnull=True)
print(f"\n📦 محصولات بدون دسته‌بندی: {products_without_category.count()}")

if products_without_category.exists():
    print("\nلیست محصولات بدون دسته‌بندی:")
    for p in products_without_category:
        print(f"   • {p.name}")

# نمایش دسته‌بندی‌های موجود
categories = Category.objects.filter(is_active=True)
print(f"\n📂 دسته‌بندی‌های موجود: {categories.count()}")
for cat in categories:
    product_count = Product.objects.filter(category=cat).count()
    print(f"   • {cat.name} ({product_count} محصول)")

# تعریف دسته‌بندی‌های پیشنهادی برای محصولات غذایی
print("\n" + "=" * 60)
print("🏷️ ایجاد/بررسی دسته‌بندی‌های مورد نیاز...")

# دسته‌بندی‌های پیشنهادی
suggested_categories = {
    'میوه و سبزیجات': ['آبلیمو', 'لیمو', 'پرتقال', 'سیب', 'گوجه', 'خیار', 'پیاز'],
    'نوشیدنی': ['آب معدنی', 'نوشابه', 'چای', 'قهوه', 'دوغ', 'آبمیوه'],
    'لبنیات': ['شیر', 'ماست', 'پنیر', 'خامه', 'کره', 'خامه فرم‌گرفته'],
    'حبوبات و برنج': ['برنج', 'عدس', 'لوبیا', 'نخود', 'برنج ایرانی', 'برنج هندی'],
    'گوشت و پروتئین': ['گوشت', 'مرغ', 'ماهی', 'تخم مرغ', 'برگر', 'سوسیس'],
    'ادویه و چاشنی': ['نمک', 'فلفل', 'زعفران', 'دارچین', 'زنجبیل', 'زردچوبه'],
    'کنسرو و بسته‌بندی': ['کنسرو', 'رب', 'سس', 'ترشی', 'مربا', 'عسل'],
    'بهداشتی و نظافت': ['شامپو', 'صابون', 'مایع ظرفشویی', 'دستمال کاغذی', 'پودر لباسشویی'],
    'ظروف یکبار مصرف': ['لیوان کاغذی', 'بشقاب کاغذی', 'سفره یکبار مصرف', 'دستکش'],
}

# ایجاد دسته‌بندی‌ها اگر وجود ندارند
created_categories = {}
for cat_name in suggested_categories.keys():
    cat, created = Category.objects.get_or_create(
        name=cat_name,
        defaults={'is_active': True}
    )
    created_categories[cat_name] = cat
    if created:
        print(f"   ✓ دسته‌بندی '{cat_name}' ایجاد شد")
    else:
        print(f"   • دسته‌بندی '{cat_name}' از قبل وجود دارد")

print("\n" + "=" * 60)
print("🔄 اختصاص دسته‌بندی به محصولات...")

updated_count = 0
not_found_count = 0

# همه محصولات
all_products = Product.objects.all()

for product in all_products:
    if product.category:
        continue  # اگر دسته‌بندی دارد، رد شو
    
    # پیدا کردن دسته‌بندی مناسب
    assigned = False
    product_name_lower = product.name.lower()
    
    for cat_name, keywords in suggested_categories.items():
        for keyword in keywords:
            if keyword.lower() in product_name_lower:
                product.category = created_categories[cat_name]
                product.save()
                print(f"   ✓ {product.name} → {cat_name}")
                updated_count += 1
                assigned = True
                break
        if assigned:
            break
    
    if not assigned:
        # اگر کلمه کلیدی پیدا نشد، به دسته "سایر" اضافه کن
        other_cat, _ = Category.objects.get_or_create(
            name='سایر',
            defaults={'is_active': True}
        )
        product.category = other_cat
        product.save()
        print(f"   ⚠ {product.name} → سایر (پیدا نشد)")
        not_found_count += 1

print("\n" + "=" * 60)
print(f"✅ عملیات تکمیل شد!")
print(f"   📊 تعداد محصولات بروزرسانی شده: {updated_count}")
print(f"   ⚠ محصولات در دسته 'سایر': {not_found_count}")

# نمایش آمار نهایی
print("\n📊 آمار نهایی دسته‌بندی‌ها:")
all_categories = Category.objects.filter(is_active=True)
for cat in all_categories:
    product_count = Product.objects.filter(category=cat).count()
    if product_count > 0:
        print(f"   • {cat.name}: {product_count} محصول")

# بررسی محصولات بدون دسته‌بندی
remaining_without_cat = Product.objects.filter(category__isnull=True).count()
if remaining_without_cat > 0:
    print(f"\n⚠ هنوز {remaining_without_cat} محصول بدون دسته‌بندی وجود دارد")
else:
    print(f"\n✅ همه محصولات دارای دسته‌بندی هستند")
