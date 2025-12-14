import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Product, Category

print("🔄 اصلاح دسته‌بندی محصولات 'سایر'")
print("=" * 60)

# ایجاد دسته‌بندی‌های اضافی
additional_categories = {
    'نان و نانوایی': ['نان', 'بیسکویت', 'کیک', 'کلوچه'],
    'شیرینی و شکلات': ['شکلات', 'شیرینی', 'کیک'],
    'مواد اولیه پخت': ['شکر', 'آرد', 'پودر'],
    'لوازم بسته‌بندی': ['نایلون', 'کیسه', 'ورق', 'فویل'],
    'سبزیجات و تزئینات': ['نعناع', 'جعفری', 'شوید', 'تره'],
}

# ایجاد دسته‌بندی‌ها
created_cats = {}
for cat_name in additional_categories.keys():
    cat, created = Category.objects.get_or_create(
        name=cat_name,
        defaults={'is_active': True}
    )
    created_cats[cat_name] = cat
    if created:
        print(f"✓ ایجاد: {cat_name}")

# اختصاص دسته‌بندی به محصولات سایر
other_category = Category.objects.get(name='سایر')
other_products = Product.objects.filter(category=other_category)

print(f"\n📦 محصولات در دسته 'سایر': {other_products.count()}")
print("\n🔄 اختصاص دسته‌بندی جدید...")

mapping = {
    'نان تست': 'نان و نانوایی',
    'نان سنگک': 'نان و نانوایی',
    'نان لواش': 'نان و نانوایی',
    'بیسکویت شکلاتی': 'نان و نانوایی',
    'کیک شکلاتی': 'شیرینی و شکلات',
    'کیک وانیلی': 'شیرینی و شکلات',
    'شکلات تلخ': 'شیرینی و شکلات',
    'شکر سفید': 'مواد اولیه پخت',
    'نایلون': 'لوازم بسته‌بندی',
    'کیسه زباله': 'لوازم بسته‌بندی',
    'نعناع تازه': 'سبزیجات و تزئینات',
    'قاشق و چنگال پلاستیکی': 'ظروف یکبار مصرف',
}

# پیدا کردن دسته ظروف یکبار مصرف
disposable_cat = Category.objects.get(name='ظروف یکبار مصرف')

updated = 0
for product in other_products:
    if product.name in mapping:
        cat_name = mapping[product.name]
        if cat_name == 'ظروف یکبار مصرف':
            product.category = disposable_cat
        else:
            product.category = created_cats[cat_name]
        product.save()
        print(f"   ✓ {product.name} → {cat_name}")
        updated += 1

print(f"\n✅ {updated} محصول بروزرسانی شد")

# آمار نهایی
print("\n📊 آمار نهایی:")
all_cats = Category.objects.filter(is_active=True)
for cat in all_cats:
    count = Product.objects.filter(category=cat).count()
    if count > 0:
        print(f"   • {cat.name}: {count} محصول")
