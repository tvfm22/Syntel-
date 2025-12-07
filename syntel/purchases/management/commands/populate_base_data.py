from django.core.management.base import BaseCommand
from purchases.models import Category, Unit, Product

class Command(BaseCommand):
    help = 'Populate database with sample base data (categories, units, products)'

    def handle(self, *args, **options):
        self.stdout.write('Creating sample base data...')

        # Create sample categories
        categories_data = [
            {'name': 'مواد غذایی', 'description': 'مواد غذایی و محصولات خوراکی'},
            {'name': 'نوشیدنی‌ها', 'description': 'نوشیدنی‌های گرم و سرد'},
            {'name': 'لبنیات', 'description': 'محصولات لبنی و فراورده‌های شیر'},
            {'name': 'میوه و سبزیجات', 'description': 'میوه‌ها و سبزیجات تازه'},
            {'name': 'تنقلات', 'description': 'کیک، شیرینی و تنقلات'},
            {'name': 'نان و غلات', 'description': 'نان و محصولات آردی'},
            {'name': 'مواد شوینده', 'description': 'شوینده‌ها و مواد پاک‌کننده'},
            {'name': 'لوازم یکبار مصرف', 'description': 'ظروف و لوازم یکبار مصرف'},
        ]

        categories = {}
        for cat_data in categories_data:
            category, created = Category.objects.get_or_create(
                name=cat_data['name'],
                defaults={
                    'description': cat_data['description'],
                    'is_active': True,
                }
            )
            categories[cat_data['name']] = category
            if created:
                self.stdout.write(f'Created category: {category.name}')

        # Create sample units
        units_data = [
            {'name': 'کیلوگرم', 'symbol': 'kg'},
            {'name': 'گرم', 'symbol': 'g'},
            {'name': 'لیتر', 'symbol': 'L'},
            {'name': 'میلی‌لیتر', 'symbol': 'ml'},
            {'name': 'عدد', 'symbol': 'عدد'},
            {'name': 'بطری', 'symbol': 'بطری'},
            {'name': 'قوطی', 'symbol': 'قوطی'},
            {'name': 'پاکت', 'symbol': 'پاکت'},
            {'name': 'جعبه', 'symbol': 'جعبه'},
            {'name': 'شخص', 'symbol': 'نفر'},
        ]

        units = {}
        for unit_data in units_data:
            unit, created = Unit.objects.get_or_create(
                name=unit_data['name'],
                defaults={
                    'symbol': unit_data['symbol'],
                    'is_active': True,
                }
            )
            units[unit_data['name']] = unit
            if created:
                self.stdout.write(f'Created unit: {unit.name} ({unit.symbol})')

        # Create sample products
        products_data = [
            # مواد غذایی
            {'name': 'قهوه عربیکا', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 2.0, 'description': 'قهوه مخصوص اسپرسو'},
            {'name': 'شکر سفید', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 5.0, 'description': 'شکر تصفیه شده'},
            {'name': 'نمک', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 1.0, 'description': 'نمک طعام'},
            {'name': 'برنج ایرانی', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 10.0, 'description': 'برنج طارم درجه یک'},

            # نوشیدنی‌ها
            {'name': 'چای سیاه', 'category': 'نوشیدنی‌ها', 'unit': 'کیلوگرم', 'min_stock': 1.5, 'description': 'چای سیاه سیلان'},
            {'name': 'قهوه فوری', 'category': 'نوشیدنی‌ها', 'unit': 'کیلوگرم', 'min_stock': 0.5, 'description': 'قهوه فوری نسکافه'},
            {'name': 'آب معدنی', 'category': 'نوشیدنی‌ها', 'unit': 'بطری', 'min_stock': 20.0, 'description': 'آب معدنی ۱.۵ لیتری'},
            {'name': 'نوشابه کوکا', 'category': 'نوشیدنی‌ها', 'unit': 'قوطی', 'min_stock': 12.0, 'description': 'نوشابه گازدار کوکا کولا'},

            # لبنیات
            {'name': 'شیر گاو', 'category': 'لبنیات', 'unit': 'لیتر', 'min_stock': 10.0, 'description': 'شیر تازه پاستوریزه'},
            {'name': 'ماست یونانی', 'category': 'لبنیات', 'unit': 'کیلوگرم', 'min_stock': 5.0, 'description': 'ماست برای دسرها'},
            {'name': 'خامه فرم‌گرفته', 'category': 'لبنیات', 'unit': 'کیلوگرم', 'min_stock': 2.0, 'description': 'خامه برای دسرها'},
            {'name': 'پنیر سفید', 'category': 'لبنیات', 'unit': 'کیلوگرم', 'min_stock': 3.0, 'description': 'پنیر سفید محلی'},

            # میوه و سبزیجات
            {'name': 'لیمون', 'category': 'میوه و سبزیجات', 'unit': 'کیلوگرم', 'min_stock': 5.0, 'description': 'لیمون تازه'},
            {'name': 'نعناع تازه', 'category': 'میوه و سبزیجات', 'unit': 'کیلوگرم', 'min_stock': 1.0, 'description': 'نعناع برای نوشیدنی‌ها'},
            {'name': 'زنجبیل', 'category': 'میوه و سبزیجات', 'unit': 'کیلوگرم', 'min_stock': 0.5, 'description': 'زنجبیل تازه'},
            {'name': 'آبلیمو', 'category': 'میوه و سبزیجات', 'unit': 'لیتر', 'min_stock': 3.0, 'description': 'آبلیمو طبیعی'},

            # تنقلات
            {'name': 'کیک شکلاتی', 'category': 'تنقلات', 'unit': 'عدد', 'min_stock': 10.0, 'description': 'کیک آماده سرو'},
            {'name': 'کیک وانیلی', 'category': 'تنقلات', 'unit': 'عدد', 'min_stock': 8.0, 'description': 'کیک وانیلی با خامه'},
            {'name': 'بیسکویت شکلاتی', 'category': 'تنقلات', 'unit': 'پاکت', 'min_stock': 5.0, 'description': 'بیسکویت شکلاتی آماده'},
            {'name': 'شکلات تلخ', 'category': 'تنقلات', 'unit': 'کیلوگرم', 'min_stock': 1.0, 'description': 'شکلات تلخ ۷۰%'},

            # نان و غلات
            {'name': 'نان سنگک', 'category': 'نان و غلات', 'unit': 'عدد', 'min_stock': 15.0, 'description': 'نان تازه سنگک'},
            {'name': 'نان لواش', 'category': 'نان و غلات', 'unit': 'عدد', 'min_stock': 20.0, 'description': 'نان لواش تازه'},
            {'name': 'برگر', 'category': 'نان و غلات', 'unit': 'عدد', 'min_stock': 25.0, 'description': 'نان برگر آماده'},
            {'name': 'نان تست', 'category': 'نان و غلات', 'unit': 'پاکت', 'min_stock': 6.0, 'description': 'نان تست بسته‌ای'},

            # مواد شوینده
            {'name': 'مایع ظرفشویی', 'category': 'مواد شوینده', 'unit': 'لیتر', 'min_stock': 2.0, 'description': 'مایع ظرفشویی قوی'},
            {'name': 'شامپو', 'category': 'مواد شوینده', 'unit': 'لیتر', 'min_stock': 1.0, 'description': 'شامپو مایع'},
            {'name': 'دستمال کاغذی', 'category': 'مواد شوینده', 'unit': 'پاکت', 'min_stock': 10.0, 'description': 'دستمال کاغذی یکبار مصرف'},
            {'name': 'کیسه زباله', 'category': 'مواد شوینده', 'unit': 'کیلوگرم', 'min_stock': 2.0, 'description': 'کیسه زباله سیاه'},

            # لوازم یکبار مصرف
            {'name': 'لیوان کاغذی', 'category': 'لوازم یکبار مصرف', 'unit': 'جعبه', 'min_stock': 5.0, 'description': 'لیوان کاغذی ۲۰۰ سی‌سی'},
            {'name': 'بشقاب کاغذی', 'category': 'لوازم یکبار مصرف', 'unit': 'جعبه', 'min_stock': 3.0, 'description': 'بشقاب کاغذی یکبار مصرف'},
            {'name': 'قاشق و چنگال پلاستیکی', 'category': 'لوازم یکبار مصرف', 'unit': 'جعبه', 'min_stock': 4.0, 'description': 'قاشق و چنگال پلاستیکی'},
            {'name': 'نایلون', 'category': 'لوازم یکبار مصرف', 'unit': 'کیلوگرم', 'min_stock': 1.0, 'description': 'نایلون بسته‌بندی'},
        ]

        for product_data in products_data:
            try:
                category = categories[product_data['category']]
                unit = units[product_data['unit']]

                product, created = Product.objects.get_or_create(
                    name=product_data['name'],
                    defaults={
                        'category': category,
                        'unit': unit,
                        'min_stock': product_data['min_stock'],
                        'description': product_data['description'],
                        'is_active': True,
                    }
                )

                if created:
                    self.stdout.write(f'Created product: {product.name} ({product.category.name})')
            except KeyError as e:
                self.stdout.write(self.style.WARNING(f'Skipping product {product_data["name"]}: Missing {e}'))

        self.stdout.write(self.style.SUCCESS('Successfully created sample base data!'))

        # Summary
        total_categories = Category.objects.count()
        total_units = Unit.objects.count()
        total_products = Product.objects.count()

        self.stdout.write(f'\nSummary:')
        self.stdout.write(f'  - Total categories: {total_categories}')
        self.stdout.write(f'  - Total units: {total_units}')
        self.stdout.write(f'  - Total products: {total_products}')