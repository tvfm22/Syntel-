# -*- coding: utf-8 -*-
"""Import new consumable, detergent, hookah, beverage, and office products for baba_adam,
and add Kanter/Salon permissions to existing spice & doogh items.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from purchases.models import Cafe, Category, Department, Product, Unit


NEW_PRODUCTS = [
    # (name, category_name, unit_name, general_scope_or_None, [department_names])
    ('دستمال کاغذی رولی', 'لوازم مصرفی و یکبار مصرف', 'رول', 'general_kitchen_bar', []),
    ('دستمال اقتصادی 22.4*19', 'لوازم مصرفی و یکبار مصرف', 'بسته', 'general_kitchen_bar', []),
    ('دستمال کوکتل', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['کانتر', 'سالن']),
    ('دستمال پولیش', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['کانتر', 'سالن', 'خدمات']),
    ('دستمال فایبر گلس', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['خدمات']),
    ('اسکاج زبر', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['خدمات']),
    ('اسکاج نرم', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['خدمات']),
    ('ابر', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['خدمات']),
    ('مایع ظرفشویی', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات']),
    ('مایع دستشویی', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات']),
    ('مایع ظرفشویی ماشین', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات']),
    ('قرص رشنال آبی', 'لوازم شوینده و بهداشتی', 'عدد', None, ['فرنگی', 'آماده سازی', 'سالاد و پیش غذا', 'صبحانه']),
    ('قرص رشنال سبز', 'لوازم شوینده و بهداشتی', 'عدد', None, ['فرنگی', 'آماده سازی', 'سالاد و پیش غذا', 'صبحانه']),
    ('مایع لوله باز کن', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات']),
    ('مایع چند منظوره', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['سالن', 'کانتر', 'خدمات']),
    ('مایع شیشه شور', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['سالن', 'کانتر', 'خدمات']),
    ('پودر لباسشویی', 'لوازم شوینده و بهداشتی', 'کیلوگرم', None, ['خدمات']),
    ('پودر چنته', 'لوازم شوینده و بهداشتی', 'کیلوگرم', None, ['خدمات']),
    ('شامپو فرش', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات', 'سالن']),
    ('مایع جرم گیر', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات']),
    ('مایع سفید کننده', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات']),
    ('مایع خوشبو کننده فاضلاب', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['خدمات']),
    ('اسپری چند منظوره', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['سالن', 'کانتر', 'خدمات']),
    ('اسپری شیشه شور', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['سالن', 'کانتر', 'خدمات']),
    ('اسپری خوشبو کننده سطوح', 'لوازم شوینده و بهداشتی', 'لیتر', None, ['سالن', 'کانتر', 'خدمات']),
    ('ظرف بیرون بر بزرگ', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['کانتر', 'سالن']),
    ('ظرف بیرون بر کوچک', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['کانتر', 'سالن']),
    ('تنظیف', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['فرنگی', 'صبحانه']),
    ('پودر بکواش دستگاه اسپرسو', 'لوازم شوینده و بهداشتی', 'گرم', None, ['بار']),
    ('قرص گرایندر دستگاه آسیاب قهوه', 'لوازم شوینده و بهداشتی', 'عدد', None, ['بار']),
    ('کیسه زباله مشکی بزرگ', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['خدمات']),
    ('کیسه زباله رولی', 'لوازم مصرفی و یکبار مصرف', 'رول', None, ['سالن', 'کانتر', 'خدمات']),
    ('دستکش ظرفشویی', 'لوازم مصرفی و یکبار مصرف', 'جفت', None, ['خدمات']),
    ('دستکش یکبار مصرف', 'لوازم مصرفی و یکبار مصرف', 'بسته', 'general', []),
    ('دستکش لاتکس', 'لوازم مصرفی و یکبار مصرف', 'بسته', 'general_kitchen_bar', []),
    ('فویل آلومینیومی', 'لوازم مصرفی و یکبار مصرف', 'رول', 'general_kitchen_bar', []),
    ('سلفون', 'لوازم مصرفی و یکبار مصرف', 'رول', 'general_kitchen_bar', []),
    ('کلاه یکبار مصرف', 'لوازم مصرفی و یکبار مصرف', 'بسته', 'general_kitchen_bar', []),
    ('ظرف فوم یکبار مصرف', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['ایرانی']),
    ('شلنگ یکبار مصرف قلیان', 'موارد مصرفی قلیون بار', 'عدد', None, ['قلیون بار']),
    ('زغال زیر کار', 'موارد مصرفی قلیون بار', 'کیلوگرم', None, ['قلیون بار']),
    ('زغال قلیان', 'موارد مصرفی قلیون بار', 'کیلوگرم', None, ['قلیون بار']),
    ('جعبه پیتزا', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['کانتر', 'سالن']),
    ('سینگل', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['فرنگی', 'کانتر', 'سالن']),
    ('شمع وارنر', 'لوازم مصرفی و یکبار مصرف', 'عدد', None, ['بار']),
    ('سیخ چوبی', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['بار']),
    ('نی سایز 6', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['بار']),
    ('نی سایز 8', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['بار']),
    ('نی سایز 10', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['بار']),
    ('کیسه فریزر', 'لوازم مصرفی و یکبار مصرف', 'بسته', 'general_kitchen_bar', []),
    ('زیپ کیپ', 'لوازم مصرفی و یکبار مصرف', 'بسته', 'general_kitchen_bar', []),
    ('نایلون وکیوم', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['فرنگی', 'آماده سازی', 'سالاد و پیش غذا', 'صبحانه']),
    ('نایلون قیفی', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['فرنگی', 'آماده سازی', 'سالاد و پیش غذا', 'صبحانه']),
    ('کاور پلاستیکی توالت فرنگی', 'لوازم مصرفی و یکبار مصرف', 'بسته', None, ['خدمات']),
    ('رول پرینتر', 'اداری', 'رول', 'general', []),
    ('کوکا کلاسیک شیشه ای', 'نوشیدنی، قهوه، چای و سیروپ', 'شیشه', None, ['کانتر', 'سالن']),
    ('کوکا زیرو شیشه ای', 'نوشیدنی، قهوه، چای و سیروپ', 'شیشه', None, ['کانتر', 'سالن']),
    ('کوکا اسپرایت شیشه ای', 'نوشیدنی، قهوه، چای و سیروپ', 'شیشه', None, ['کانتر', 'سالن']),
    ('کوکا فانتا شیشه ای', 'نوشیدنی، قهوه، چای و سیروپ', 'شیشه', None, ['کانتر', 'سالن']),
    ('آب معدنی', 'نوشیدنی، قهوه، چای و سیروپ', 'بطری', None, ['کانتر', 'سالن']),
    ('چسب کاغذی باریک', 'اداری', 'رول', 'general', []),
    ('چسب کاغذی پهن', 'اداری', 'رول', 'general', []),
    ('ماژیک', 'اداری', 'عدد', 'general', []),
    ('خودکار', 'اداری', 'عدد', 'general', []),
    ('چسب 5 سانتی', 'اداری', 'رول', None, ['مالی و صندوق']),
    ('برگه A5', 'اداری', 'بسته', None, ['مالی و صندوق']),
    ('برگه A4', 'اداری', 'بسته', None, ['مالی و صندوق']),
    ('کاور', 'اداری', 'بسته', None, ['مالی و صندوق']),
    ('سوزن منگنه', 'اداری', 'بسته', None, ['مالی و صندوق']),
    ('تنباکو بلوبری', 'موارد مصرفی قلیون بار', 'گرم', None, ['قلیون بار']),
    ('تنباکو میکس استوایی', 'موارد مصرفی قلیون بار', 'گرم', None, ['قلیون بار']),
    ('تنباکو دوسیب', 'موارد مصرفی قلیون بار', 'گرم', None, ['قلیون بار']),
    ('تنباکو لاو', 'موارد مصرفی قلیون بار', 'گرم', None, ['قلیون بار']),
    ('تنباکو مسکو', 'موارد مصرفی قلیون بار', 'گرم', None, ['قلیون بار']),
    ('تنباکو انگور نعنا', 'موارد مصرفی قلیون بار', 'گرم', None, ['قلیون بار']),
    ('تنباکو آدامس دارچین', 'موارد مصرفی قلیون بار', 'گرم', None, ['قلیون بار']),
    ('نمک آجی نوموتو', 'ادویه‌جات، نمک، عطاری و عرقیات', 'گرم', None, ['فرنگی', 'آماده سازی', 'سالاد و پیش غذا']),
]

EXISTING_DOOGH_UPDATES = [
    ('دوغ شمرون شیشه 280 ویژه رستوران', 'نوشیدنی، قهوه، چای و سیروپ', 'شیشه', ['کانتر', 'سالن']),
    ('دوغ 1.5 لیتری', 'نوشیدنی، قهوه، چای و سیروپ', 'بطری', ['کانتر', 'سالن']),
]

EXISTING_ADD_SECTIONS = [
    ('نمک ید دار', ['کانتر', 'سالن']),
    ('دان فلفل سیاه', ['کانتر', 'سالن']),
    ('سماق', ['کانتر', 'سالن']),
]


class Command(BaseCommand):
    help = 'Define new products, assign sections/general scopes, and update existing products.'

    def add_arguments(self, parser):
        parser.add_argument('--cafe', dest='cafe_code', default='baba_adam')

    @transaction.atomic
    def handle(self, *args, **options):
        cafe = Cafe.objects.filter(code=options['cafe_code']).first()
        if cafe is None:
            raise CommandError(f'Cafe {options["cafe_code"]} not found.')

        categories = {c.name: c for c in Category.objects.filter(cafe=cafe, parent__isnull=True)}
        departments = {d.name: d for d in Department.objects.filter(cafe=cafe, is_active=True)}
        units = {u.name: u for u in Unit.objects.filter(cafe=cafe)}

        def get_or_create_unit(unit_name):
            if unit_name not in units:
                u, _ = Unit.objects.get_or_create(
                    name=unit_name,
                    cafe=cafe,
                    defaults={'symbol': unit_name, 'is_active': True},
                )
                units[unit_name] = u
            return units[unit_name]

        created_count = 0
        updated_count = 0

        for name, cat_name, unit_name, general_scope, dept_names in NEW_PRODUCTS:
            cat = categories.get(cat_name)
            if cat is None:
                raise CommandError(f'Missing category: {cat_name}')
            unit = get_or_create_unit(unit_name)
            matched_depts = [departments[dn] for dn in dept_names]

            if general_scope:
                usage_location = general_scope
            elif len(matched_depts) == 1:
                usage_location = matched_depts[0].name[:20]
            else:
                usage_location = 'multi'

            product, created = Product.objects.update_or_create(
                name=name,
                cafe=cafe,
                defaults={
                    'category': cat,
                    'unit': unit,
                    'usage_location': usage_location,
                    'is_active': True,
                    'is_complete': True,
                },
            )
            product.departments.set(matched_depts)
            if created:
                created_count += 1
            else:
                updated_count += 1

        for name, cat_name, unit_name, dept_names in EXISTING_DOOGH_UPDATES:
            product = Product.objects.filter(cafe=cafe, name=name).first()
            if product is None:
                raise CommandError(f'Expected existing product not found: {name}')
            product.category = categories[cat_name]
            product.unit = get_or_create_unit(unit_name)
            product.is_active = True
            product.is_complete = True
            matched_depts = [departments[dn] for dn in dept_names]
            product.usage_location = matched_depts[0].name[:20] if len(matched_depts) == 1 else 'multi'
            product.save()
            product.departments.set(matched_depts)
            updated_count += 1

        for name, add_dept_names in EXISTING_ADD_SECTIONS:
            product = Product.objects.filter(cafe=cafe, name=name).first()
            if product is None:
                raise CommandError(f'Expected existing product not found: {name}')
            add_depts = [departments[dn] for dn in add_dept_names]
            product.departments.add(*add_depts)
            total_depts = product.departments.count()
            product.usage_location = product.departments.first().name[:20] if total_depts == 1 else 'multi'
            product.save(update_fields=['usage_location'])
            updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'[{cafe.code}] Done: created={created_count}, updated={updated_count}, total_units={len(units)}'
            )
        )
