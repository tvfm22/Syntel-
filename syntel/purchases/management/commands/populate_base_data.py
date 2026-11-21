from django.core.management.base import BaseCommand, CommandError

from purchases.models import Cafe, Category, Product, Unit


class Command(BaseCommand):
    help = 'Populate database with sample base data (categories, units, products) for a cafe'

    def add_arguments(self, parser):
        parser.add_argument('--cafe', required=True, help='Target cafe code')

    def handle(self, *args, **options):
        cafe = Cafe.objects.filter(code=options['cafe'], is_active=True).first()
        if cafe is None:
            raise CommandError(f'Active cafe "{options["cafe"]}" not found.')

        self.stdout.write(f'Creating sample base data for cafe "{cafe.code}"...')

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
                parent=None,
                cafe=cafe,
                defaults={
                    'description': cat_data['description'],
                    'is_active': True,
                },
            )
            categories[cat_data['name']] = category
            if created:
                self.stdout.write(f'Created category: {category.name}')

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
                cafe=cafe,
                defaults={
                    'symbol': unit_data['symbol'],
                    'is_active': True,
                },
            )
            units[unit_data['name']] = unit
            if created:
                self.stdout.write(f'Created unit: {unit.name} ({unit.symbol})')

        products_data = [
            {'name': 'قهوه عربیکا', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 2, 'description': 'قهوه مخصوص اسپرسو'},
            {'name': 'شکر سفید', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 5, 'description': 'شکر تصفیه شده'},
            {'name': 'نمک', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 1, 'description': 'نمک طعام'},
            {'name': 'برنج ایرانی', 'category': 'مواد غذایی', 'unit': 'کیلوگرم', 'min_stock': 10, 'description': 'برنج طارم درجه یک'},
            {'name': 'چای سیاه', 'category': 'نوشیدنی‌ها', 'unit': 'کیلوگرم', 'min_stock': 2, 'description': 'چای سیاه سیلان'},
            {'name': 'قهوه فوری', 'category': 'نوشیدنی‌ها', 'unit': 'کیلوگرم', 'min_stock': 1, 'description': 'قهوه فوری نسکافه'},
            {'name': 'آب معدنی', 'category': 'نوشیدنی‌ها', 'unit': 'بطری', 'min_stock': 20, 'description': 'آب معدنی ۱.۵ لیتری'},
            {'name': 'نوشابه کوکا', 'category': 'نوشیدنی‌ها', 'unit': 'قوطی', 'min_stock': 12, 'description': 'نوشابه گازدار کوکا کولا'},
            {'name': 'شیر گاو', 'category': 'لبنیات', 'unit': 'لیتر', 'min_stock': 10, 'description': 'شیر تازه پاستوریزه'},
            {'name': 'ماست یونانی', 'category': 'لبنیات', 'unit': 'کیلوگرم', 'min_stock': 5, 'description': 'ماست برای دسرها'},
            {'name': 'خامه فرم‌گرفته', 'category': 'لبنیات', 'unit': 'کیلوگرم', 'min_stock': 2, 'description': 'خامه برای دسرها'},
            {'name': 'پنیر سفید', 'category': 'لبنیات', 'unit': 'کیلوگرم', 'min_stock': 3, 'description': 'پنیر سفید محلی'},
            {'name': 'لیمون', 'category': 'میوه و سبزیجات', 'unit': 'کیلوگرم', 'min_stock': 5, 'description': 'لیمون تازه'},
            {'name': 'نعناع تازه', 'category': 'میوه و سبزیجات', 'unit': 'کیلوگرم', 'min_stock': 1, 'description': 'نعناع برای نوشیدنی‌ها'},
            {'name': 'زنجبیل', 'category': 'میوه و سبزیجات', 'unit': 'کیلوگرم', 'min_stock': 1, 'description': 'زنجبیل تازه'},
            {'name': 'آبلیمو', 'category': 'میوه و سبزیجات', 'unit': 'لیتر', 'min_stock': 3, 'description': 'آبلیمو طبیعی'},
            {'name': 'کیک شکلاتی', 'category': 'تنقلات', 'unit': 'عدد', 'min_stock': 10, 'description': 'کیک آماده سرو'},
            {'name': 'کیک وانیلی', 'category': 'تنقلات', 'unit': 'عدد', 'min_stock': 8, 'description': 'کیک وانیلی با خامه'},
            {'name': 'بیسکویت شکلاتی', 'category': 'تنقلات', 'unit': 'پاکت', 'min_stock': 5, 'description': 'بیسکویت شکلاتی آماده'},
            {'name': 'شکلات تلخ', 'category': 'تنقلات', 'unit': 'کیلوگرم', 'min_stock': 1, 'description': 'شکلات تلخ ۷۰%'},
            {'name': 'نان سنگک', 'category': 'نان و غلات', 'unit': 'عدد', 'min_stock': 15, 'description': 'نان تازه سنگک'},
            {'name': 'نان لواش', 'category': 'نان و غلات', 'unit': 'عدد', 'min_stock': 20, 'description': 'نان لواش تازه'},
            {'name': 'برگر', 'category': 'نان و غلات', 'unit': 'عدد', 'min_stock': 25, 'description': 'نان برگر آماده'},
            {'name': 'نان تست', 'category': 'نان و غلات', 'unit': 'پاکت', 'min_stock': 6, 'description': 'نان تست بسته‌ای'},
            {'name': 'مایع ظرفشویی', 'category': 'مواد شوینده', 'unit': 'لیتر', 'min_stock': 2, 'description': 'مایع ظرفشویی قوی'},
            {'name': 'شامپو', 'category': 'مواد شوینده', 'unit': 'لیتر', 'min_stock': 1, 'description': 'شامپو مایع'},
            {'name': 'دستمال کاغذی', 'category': 'مواد شوینده', 'unit': 'پاکت', 'min_stock': 10, 'description': 'دستمال کاغذی یکبار مصرف'},
            {'name': 'کیسه زباله', 'category': 'مواد شوینده', 'unit': 'کیلوگرم', 'min_stock': 2, 'description': 'کیسه زباله سیاه'},
            {'name': 'لیوان کاغذی', 'category': 'لوازم یکبار مصرف', 'unit': 'جعبه', 'min_stock': 5, 'description': 'لیوان کاغذی ۲۰۰ سی‌سی'},
            {'name': 'بشقاب کاغذی', 'category': 'لوازم یکبار مصرف', 'unit': 'جعبه', 'min_stock': 3, 'description': 'بشقاب کاغذی یکبار مصرف'},
            {'name': 'قاشق و چنگال پلاستیکی', 'category': 'لوازم یکبار مصرف', 'unit': 'جعبه', 'min_stock': 4, 'description': 'قاشق و چنگال پلاستیکی'},
            {'name': 'نایلون', 'category': 'لوازم یکبار مصرف', 'unit': 'کیلوگرم', 'min_stock': 1, 'description': 'نایلون بسته‌بندی'},
        ]

        for product_data in products_data:
            category = categories.get(product_data['category'])
            unit = units.get(product_data['unit'])
            product, created = Product.objects.get_or_create(
                name=product_data['name'],
                cafe=cafe,
                defaults={
                    'category': category,
                    'unit': unit,
                    'min_stock': product_data['min_stock'],
                    'description': product_data['description'],
                    'is_active': True,
                },
            )
            if created:
                self.stdout.write(f'Created product: {product.name}')

        self.stdout.write(self.style.SUCCESS(f'Successfully populated base data for cafe "{cafe.code}"!'))
