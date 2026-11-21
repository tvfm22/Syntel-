#!/usr/bin/env python
"""
بروزرسانی تاریخچه قیمت محصولات با داده‌های آزمایشی (نیازمند --confirm-destructive)
"""
import argparse
from datetime import datetime, timedelta
from decimal import Decimal
import os
import random
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')


def main(confirm_destructive: bool = False):
    if not confirm_destructive:
        raise RuntimeError("This script deletes all PriceHistory rows; pass --confirm-destructive to proceed.")

    django.setup()
    from django.db import transaction
    from django.utils import timezone
    from purchases.models import PriceHistory, Product, Supplier

    print("🔄 بروزرسانی قیمت‌های محصولات با نوسانات واقعی‌تر")
    print("=" * 60)

    products = list(Product.objects.all())
    suppliers = list(Supplier.objects.filter(status='active'))
    if not suppliers:
        print("❌ فروشنده‌ای یافت نشد!")
        return

    base_prices = {
        'آب معدنی': 5000, 'آبلیمو': 80000, 'برگر': 150000, 'برنج ایرانی': 180000,
        'بشقاب کاغذی': 50000, 'بیسکویت شکلاتی': 45000, 'پنیر سفید': 120000,
        'چای سیاه': 200000, 'خامه فرم‌گرفته': 85000, 'دستمال کاغذی': 35000,
        'زنجبیل': 150000, 'شامپو': 90000, 'شکر سفید': 60000, 'شکلات تلخ': 120000,
        'شیر گاو': 30000, 'قاشق و چنگال پلاستیکی': 40000, 'قهوه عربیکا': 350000,
        'قهوه فوری': 180000, 'کیسه زباله': 25000, 'کیک شکلاتی': 140000,
        'کیک وانیلی': 130000, 'لیمون': 70000, 'لیوان کاغذی': 45000,
        'ماست یونانی': 65000, 'مایع ظرفشویی': 55000, 'نان تست': 35000,
        'نان سنگک': 15000, 'نان لواش': 12000, 'نایلون': 30000,
        'نعناع تازه': 40000, 'نمک': 20000, 'نوشابه کوکا': 18000,
    }

    start_date = timezone.now() - timedelta(days=180)
    total_created = 0

    with transaction.atomic():
        print("\n🗑️ پاک کردن تاریخچه قیمت قبلی...")
        PriceHistory.objects.all().delete()
        print("✓ تاریخچه قبلی پاک شد")

        for product in products:
            base_price = base_prices.get(product.name, 50000)
            num_records = random.randint(5, 8)
            current_price = base_price

            for i in range(num_records):
                days_offset = int((180 / num_records) * i) + random.randint(-5, 5)
                record_date = start_date + timedelta(days=days_offset)
                supplier = random.choice(suppliers)
                change_percent = random.uniform(0, 10) if random.random() < 0.8 else random.uniform(-5, 0)
                new_price = max(1000, int((current_price * (1 + change_percent / 100)) / 1000) * 1000)
                percent_change = Decimal('0') if i == 0 else Decimal(str(((new_price - current_price) / current_price) * 100))

                PriceHistory.objects.create(
                    product=product,
                    price=Decimal(str(new_price)),
                    date=record_date,
                    supplier=supplier,
                    percent_change=percent_change,
                )
                current_price = new_price
                total_created += 1

    print(f"\n✅ {total_created} رکورد قیمت ایجاد شد!")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Regenerate sample PriceHistory records.')
    parser.add_argument(
        '--confirm-destructive',
        action='store_true',
        help='Required flag to confirm deleting all PriceHistory records.',
    )
    args = parser.parse_args()
    main(confirm_destructive=args.confirm_destructive)
