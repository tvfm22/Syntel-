"""
اسکریپت ایجاد کافه‌ها و انتقال داده‌ها

این اسکریپت:
1. دو کافه "کافه لوکال" و "کافه رستوران بابا آدم" را ایجاد می‌کند
2. تمام داده‌های موجود را به کافه لوکال منتقل می‌کند

نحوه اجرا:
python manage.py shell < setup_cafes.py
یا
python manage.py runscript setup_cafes
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import (
    Cafe, User, Product, Category, Unit, Supplier, 
    PurchaseRequest, Invoice, Payment, PaymentLocation,
    PaymentType, Requester, InvoiceIssue, Notification,
    PriceHistory, StockItem
)


def create_cafes():
    """ایجاد دو کافه پیش‌فرض"""
    print("=" * 50)
    print("ایجاد کافه‌ها...")
    print("=" * 50)
    
    # کافه لوکال
    local_cafe, created = Cafe.objects.get_or_create(
        code='local',
        defaults={
            'name': 'کافه لوکال',
            'address': '',
            'phone': '',
            'is_active': True,
        }
    )
    if created:
        print(f"✅ کافه '{local_cafe.name}' ایجاد شد")
    else:
        print(f"ℹ️ کافه '{local_cafe.name}' قبلاً وجود داشت")
    
    # کافه رستوران بابا آدم
    baba_adam_cafe, created = Cafe.objects.get_or_create(
        code='baba_adam',
        defaults={
            'name': 'کافه رستوران بابا آدم',
            'address': '',
            'phone': '',
            'is_active': True,
        }
    )
    if created:
        print(f"✅ کافه '{baba_adam_cafe.name}' ایجاد شد")
    else:
        print(f"ℹ️ کافه '{baba_adam_cafe.name}' قبلاً وجود داشت")
    
    return local_cafe, baba_adam_cafe


def migrate_data_to_cafe(cafe):
    """انتقال تمام داده‌های موجود به کافه مشخص"""
    print("\n" + "=" * 50)
    print(f"انتقال داده‌ها به '{cafe.name}'...")
    print("=" * 50)
    
    # لیست مدل‌ها و تعداد رکوردهای به‌روزرسانی شده
    models_to_update = [
        ('کاربران', User, 'cafe'),
        ('محصولات', Product, 'cafe'),
        ('دسته‌بندی‌ها', Category, 'cafe'),
        ('واحدها', Unit, 'cafe'),
        ('تأمین‌کنندگان', Supplier, 'cafe'),
        ('درخواست‌های خرید', PurchaseRequest, 'cafe'),
        ('فاکتورها', Invoice, 'cafe'),
        ('محل‌های پرداخت', PaymentLocation, 'cafe'),
        ('انواع پرداخت', PaymentType, 'cafe'),
        ('درخواست‌کنندگان', Requester, 'cafe'),
        ('ایرادات فاکتور', InvoiceIssue, 'cafe'),
        ('اعلان‌ها', Notification, 'cafe'),
    ]
    
    for name, model, field in models_to_update:
        try:
            # فقط رکوردهایی که کافه ندارند
            filter_kwargs = {f'{field}__isnull': True}
            count = model.objects.filter(**filter_kwargs).update(**{field: cafe})
            if count > 0:
                print(f"✅ {count} {name} به‌روزرسانی شد")
            else:
                print(f"ℹ️ {name}: همه رکوردها قبلاً کافه داشتند")
        except Exception as e:
            print(f"⚠️ خطا در {name}: {e}")
    
    print("\n" + "=" * 50)
    print("انتقال داده‌ها تکمیل شد!")
    print("=" * 50)


def main():
    print("\n" + "=" * 60)
    print("       راه‌اندازی سیستم چند کافه‌ای Syntel")
    print("=" * 60 + "\n")
    
    # ایجاد کافه‌ها
    local_cafe, baba_adam_cafe = create_cafes()
    
    # انتقال داده‌ها به کافه لوکال
    migrate_data_to_cafe(local_cafe)
    
    print("\n" + "=" * 60)
    print("         خلاصه")
    print("=" * 60)
    print(f"\nکافه‌های ایجاد شده:")
    for cafe in Cafe.objects.all():
        user_count = User.objects.filter(cafe=cafe).count()
        product_count = Product.objects.filter(cafe=cafe).count()
        print(f"  - {cafe.name} (کد: {cafe.code})")
        print(f"    کاربران: {user_count}, محصولات: {product_count}")
    
    print("\n" + "=" * 60)
    print("راه‌اندازی با موفقیت انجام شد!")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    main()
