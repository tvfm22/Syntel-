# -*- coding: utf-8 -*-
"""Import the categorized product catalog CSV into a target cafe.

Usage:
    python manage.py import_catalog_csv [path-to-csv] [--cafe baba_adam]

Creates:
- Measurement units (کیلوگرم, گرم, لیتر, عدد, قوطی, بطری, شیشه)
- Main categories (ignoring subcategories) with requires_recipe=False
- Request sections (Department) from the 6 section columns
- Products with logical units, active status (False when no section is marked),
  and M2M department permissions matching the Excel '1' flags.
"""
import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from purchases.models import Cafe, Category, Department, Product, Unit

SECTION_COLUMNS = [
    'بار',
    'صبحانه',
    'آماده سازی',
    'ایرانی',
    'سالاد و پیش غذا',
    'فرنگی',
]

UNIT_DEFINITIONS = [
    ('کیلوگرم', 'kg'),
    ('گرم', 'g'),
    ('لیتر', 'L'),
    ('عدد', 'عدد'),
    ('قوطی', 'قوطی'),
    ('بطری', 'بطری'),
    ('شیشه', 'شیشه'),
]

# Explicit unit mappings based on item pricing, packaging, and kitchen usage.
_EXACT_UNIT_BY_NAME = {
    # عدد
    'تخم مرغ': 'عدد',
    'آووکادو': 'عدد',
    'نارگیل': 'عدد',
    'کپسول خامه': 'عدد',
    'آنترومه تروپیکال': 'عدد',
    'آنترومه شکلات': 'عدد',
    'کوکی': 'عدد',
    # قوطی
    'کمپوت آناناس': 'قوطی',
    'کنسرو آرتیشو': 'قوطی',
    'کنسرو لوبیا': 'قوطی',
    'کنسرو نخود فرنگی': 'قوطی',
    'کنسرو خردل': 'قوطی',
    # بطری / شیشه
    'دوغ 1.5 لیتری': 'بطری',
    'دوغ 300 سی سی ساده': 'بطری',
    'آب گاز دار (سودا ) یک لیتری': 'بطری',
    'دوغ شمرون شیشه 280 ویژه رستوران': 'شیشه',
    # لیتر
    'نکتار آلبالو': 'لیتر',
    'نکتار آناناس': 'لیتر',
    'نکتار انبه': 'لیتر',
    'نکتار پرتقال': 'لیتر',
    'نوشیدنی آلوئه ورا': 'لیتر',
    'شیر بادام': 'لیتر',
    'شیر پرچرب': 'لیتر',
    'چاشنی سوشی مرین': 'لیتر',
    'سس تریاکی': 'لیتر',
    'سس سویا': 'لیتر',
    'سس ورچستر': 'لیتر',
    'ماماسیتا': 'لیتر',
    # گرم (موارد گران‌قیمت، مصرف در مقیاس کم، قارچ، ادویه، ...)
    'سنگ نمک': 'گرم',
    'نمک دریا': 'گرم',
    'بهبود دهنده S500': 'گرم',
    'پانکو': 'گرم',
    'مایع خمیر رضوی': 'گرم',
    'دانه کینوا': 'گرم',
    'ردکارنت': 'گرم',
    'کرنبری': 'گرم',
    'زرشک تازه': 'گرم',
    'بیبی اسفناج': 'گرم',
    'جعفری فری (جعفری ژاپنی)': 'گرم',
    'جوانه ماش': 'گرم',
    'ریحان ایتالیایی': 'گرم',
    'گل خوراکی': 'گرم',
    'میکروگرین': 'گرم',
    'پنیر پارمسان پودری': 'گرم',
    'پنیر چدار ورقه ای': 'گرم',
    'پنیر خامه ای': 'گرم',
    'پنیر دیپ بلوچیز': 'گرم',
    'پنیر لبنه': 'گرم',
    'تست چدار': 'گرم',
    'خامه سولیانو (خامه قنادی)': 'گرم',
    'شیر عسل': 'گرم',
    'چای تویینینگز آبی': 'گرم',
    'چای تویینینگز زرد': 'گرم',
    'دمنوش 7 افسانه': 'گرم',
    'دمنوش باغ راز': 'گرم',
    'دمنوش زاگرس': 'گرم',
    'دمنوش شیر زردچوبه': 'گرم',
    'ماچا': 'گرم',
    # کیلوگرم (اقلام حجیم/عمده یا کم‌هزینه در دسته‌های غیراصلی)
    'نمک ید دار': 'کیلوگرم',
    'رب گوجه فرنگی': 'کیلوگرم',
    'سس خردل': 'کیلوگرم',
    'سس کچاپ': 'کیلوگرم',
    'سس مایونز': 'کیلوگرم',
    'شکر سفید': 'کیلوگرم',
    'قند شکسته': 'کیلوگرم',
    'نبات فله': 'کیلوگرم',
    'بستنی وانیلی': 'کیلوگرم',
    'زنودالست (باقلوا)': 'کیلوگرم',
    'پنیر پیتزا تهران ناپول': 'کیلوگرم',
    'پنیر فتا': 'کیلوگرم',
    'خامه حیوانی': 'کیلوگرم',
    'خامه صبحانه': 'کیلوگرم',
    'کره حیوانی': 'کیلوگرم',
    'کشک': 'کیلوگرم',
    'ماست پرچرب سون': 'کیلوگرم',
    'ماست چکیده': 'کیلوگرم',
    'چای پرسنلی': 'کیلوگرم',
    'چای گلستان سیاه': 'کیلوگرم',
    'قهوه پرسنلی': 'کیلوگرم',
    'قهوه ترکیبی 70 درصد عربیکا': 'کیلوگرم',
    'قهوه ترک ویژه': 'کیلوگرم',
}


def resolve_logical_unit(name: str, main_category: str, subgroup: str) -> str:
    """Determine the logical purchasing/ordering unit for a catalog item."""
    if name in _EXACT_UNIT_BY_NAME:
        return _EXACT_UNIT_BY_NAME[name]

    if subgroup == 'نان':
        return 'عدد'
    if subgroup in ('سیروپ', 'روغن', 'سرکه و آبغوره و آبلیمو', 'عرقیات'):
        return 'لیتر'
    if subgroup in ('ادویه و عطاری', 'قارچ', 'پاستا و نودل'):
        return 'گرم'
    if subgroup in ('برنج', 'غلات و حبوبات') or subgroup == 'آرد و خمیر':
        return 'کیلوگرم'

    if main_category == 'گوشت، مرغ، آبزیان و فرآورده‌های پروتئینی':
        return 'کیلوگرم'
    if main_category == 'میوه، صیفی‌جات، سبزیجات و قارچ':
        return 'کیلوگرم'
    if main_category in (
        'خشکبار، مغزیجات، میوه خشک و دانه‌ها',
        'شیرینی، شکلات، دسر، قند و مواد قنادی',
        'روغن، سس، چاشنی، ترشی، مربا و کنسروجات',
    ):
        return 'گرم'

    return 'گرم'


def _default_csv_path() -> Path:
    project_root = getattr(settings, 'PROJECT_ROOT', None) or Path(__file__).resolve().parents[4]
    return Path(project_root) / 'reference' / 'catalog' / 'baba_adam_products.csv'


class Command(BaseCommand):
    help = 'وارد کردن دسته‌بندی‌ها، بخش‌ها، واحدها و کالاها از فایل CSV کاتالوگ'

    def add_arguments(self, parser):
        parser.add_argument(
            'path',
            nargs='?',
            default=str(_default_csv_path()),
            help='مسیر فایل CSV کاتالوگ محصولات',
        )
        parser.add_argument(
            '--cafe',
            dest='cafe_code',
            default='baba_adam',
            help='کد کافه مقصد (پیش‌فرض: baba_adam)',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = Path(options['path'])
        if not csv_path.exists():
            raise CommandError(f'فایل CSV پیدا نشد: {csv_path}')

        cafe_code = options['cafe_code']
        cafe = Cafe.objects.filter(code=cafe_code).first()
        if cafe is None:
            raise CommandError(f'کافه‌ای با کد «{cafe_code}» پیدا نشد.')

        with csv_path.open('r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows = [r for r in reader if (r.get('نام_محصول') or '').strip()]

        if not rows:
            raise CommandError('هیچ ردیف کالایی در فایل یافت نشد.')

        # 1. Units
        unit_map = {}
        for u_name, u_symbol in UNIT_DEFINITIONS:
            unit_obj, created = Unit.objects.get_or_create(
                name=u_name,
                cafe=cafe,
                defaults={'symbol': u_symbol, 'is_active': True},
            )
            if not created:
                changed = False
                if not unit_obj.symbol and u_symbol:
                    unit_obj.symbol = u_symbol
                    changed = True
                if not unit_obj.is_active:
                    unit_obj.is_active = True
                    changed = True
                if changed:
                    unit_obj.save()
            unit_map[u_name] = unit_obj

        # 2. Sections (Departments)
        dept_map = {}
        for sec_name in SECTION_COLUMNS:
            dept_obj, created = Department.objects.get_or_create(
                name=sec_name,
                cafe=cafe,
                defaults={'is_active': True},
            )
            if not created and not dept_obj.is_active:
                dept_obj.is_active = True
                dept_obj.save(update_fields=['is_active'])
            dept_map[sec_name] = dept_obj

        # 3. Main Categories (ignore subcategories; requires_recipe=False so orderable immediately)
        category_map = {}
        for row in rows:
            cat_name = (row.get('دسته_بندی_اصلی') or '').strip()
            if not cat_name or cat_name in category_map:
                continue
            cat_obj, created = Category.objects.get_or_create(
                name=cat_name,
                parent=None,
                cafe=cafe,
                defaults={'is_active': True, 'requires_recipe': False},
            )
            if not created:
                updates = []
                if not cat_obj.is_active:
                    cat_obj.is_active = True
                    updates.append('is_active')
                if cat_obj.requires_recipe:
                    cat_obj.requires_recipe = False
                    updates.append('requires_recipe')
                if updates:
                    cat_obj.save(update_fields=updates)
            category_map[cat_name] = cat_obj

        # 4. Products
        created_count = 0
        updated_count = 0
        active_count = 0
        inactive_count = 0

        for row in rows:
            name = row['نام_محصول'].strip()
            main_cat = (row.get('دسته_بندی_اصلی') or '').strip()
            subgroup = (row.get('زیرگروه') or '').strip()

            unit_name = resolve_logical_unit(name, main_cat, subgroup)
            unit_obj = unit_map[unit_name]
            cat_obj = category_map.get(main_cat)

            matched_depts = [
                dept_map[col]
                for col in SECTION_COLUMNS
                if (row.get(col) or '').strip() == '1'
            ]

            if matched_depts:
                is_active = True
                usage_location = matched_depts[0].name[:20] if len(matched_depts) == 1 else 'multi'
                active_count += 1
            else:
                is_active = False
                usage_location = 'none'
                inactive_count += 1

            product, created = Product.objects.update_or_create(
                name=name,
                cafe=cafe,
                defaults={
                    'category': cat_obj,
                    'unit': unit_obj,
                    'usage_location': usage_location,
                    'is_active': is_active,
                    'is_complete': True,
                },
            )
            product.departments.set(matched_depts)

            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'[{cafe.code}] کاتالوگ وارد شد: {len(rows)} کالا '
                f'(ایجاد: {created_count}، بروزرسانی: {updated_count}) | '
                f'فعال: {active_count}، غیرفعال/بدون بخش: {inactive_count} | '
                f'دسته‌بندی: {len(category_map)} | بخش: {len(dept_map)} | واحد: {len(unit_map)}'
            )
        )
