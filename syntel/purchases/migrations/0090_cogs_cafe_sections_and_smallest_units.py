# -*- coding: utf-8 -*-
"""Data migration: cafe scoping, menu sections, food categories, smallest units.

1. Every existing menu item / COGS report is attached to a cafe (the menu
   workbook belongs to «بابا آدم»; falls back to the first cafe).
2. Default prep sections are seeded per cafe and existing items are filed into
   the matching section.
3. Food categories get ``requires_recipe=True``.
4. Raw-material prices entered per کیلوگرم/لیتر are re-stored per گرم/میل.
5. Every priced raw material is linked to an inventory product.
"""
from decimal import Decimal

from django.db import migrations

FOOD_CATEGORIES = [
    'مواد غذایی',
    'نوشیدنی‌ها',
    'نوشیدنی',
    'لبنیات',
    'میوه و سبزیجات',
    'گوشت و پروتئین',
    'حبوبات و برنج',
    'ادویه و چاشنی',
    'کنسرو و بسته‌بندی',
    'نان و غلات',
    'تنقلات',
]

PREP_SECTIONS = ['آماده سازی آشپزخانه', 'آماده سازی بار']

SHEET_LABELS = {
    'منو فرنگی اصلی': 'منوی فرنگی',
    'منو اصلی بار': 'منوی بار',
    'رستوران بابا آدم ': 'رستوران بابا آدم',
    'سفره چی': 'سفره چی',
    'آماده سازی آشپزخانه': 'آماده‌سازی آشپزخانه',
    'آماده سازی بار': 'آماده‌سازی بار',
}

KG_UNITS = {'کیلوگرم', 'کیلو', 'kg', 'kgs'}
LITER_UNITS = {'لیتر', 'l', 'lt', 'ltr'}
THOUSAND = Decimal('1000')


def _cafe_for_menu(Cafe):
    return Cafe.objects.filter(code='baba_adam').first() or Cafe.objects.order_by('id').first()


def forwards(apps, schema_editor):
    Cafe = apps.get_model('purchases', 'Cafe')
    KukerItem = apps.get_model('purchases', 'KukerItem')
    CogsReport = apps.get_model('purchases', 'CogsReport')
    MenuSection = apps.get_model('purchases', 'MenuSection')
    Product = apps.get_model('purchases', 'Product')
    Category = apps.get_model('purchases', 'Category')

    # 1) cafe scoping -------------------------------------------------------
    cafe = _cafe_for_menu(Cafe)
    if cafe is not None:
        KukerItem.objects.filter(cafe__isnull=True).update(cafe=cafe)
        CogsReport.objects.filter(cafe__isnull=True).update(cafe=cafe)

    # 2) menu sections ------------------------------------------------------
    for c in Cafe.objects.all():
        for order, name in enumerate(PREP_SECTIONS):
            MenuSection.objects.get_or_create(
                cafe=c, name=name, kind='prep',
                defaults={'display_order': order, 'is_active': True})

    for item in KukerItem.objects.filter(cafe__isnull=False).exclude(kind='raw'):
        if item.section_id:
            continue
        if item.kind == 'prep':
            name = item.sheet if item.sheet in PREP_SECTIONS else PREP_SECTIONS[0]
            section, _ = MenuSection.objects.get_or_create(
                cafe_id=item.cafe_id, name=name, kind='prep', defaults={'display_order': 0})
        else:
            name = (SHEET_LABELS.get(item.sheet, item.sheet) or '').strip() or 'منوی اصلی'
            section, _ = MenuSection.objects.get_or_create(
                cafe_id=item.cafe_id, name=name, kind='dish', defaults={'display_order': 50})
        item.section = section
        item.save(update_fields=['section'])

    # 3) food categories need a recipe --------------------------------------
    Category.objects.filter(name__in=FOOD_CATEGORIES).update(requires_recipe=True)

    # 4) prices per smallest unit + 5) product link -------------------------
    for item in KukerItem.objects.filter(kind='raw'):
        unit = (item.out_unit or '').strip()
        if item.price is not None and unit in KG_UNITS:
            item.price = item.price / THOUSAND
            item.out_unit = 'گرم'
        elif item.price is not None and unit in LITER_UNITS:
            item.price = item.price / THOUSAND
            item.out_unit = 'میل'

        product = Product.objects.filter(cafe_id=item.cafe_id, name=item.name).first()
        if product is None and item.price is not None:
            product = Product.objects.create(
                cafe_id=item.cafe_id, name=item.name, is_active=True, is_complete=True)
        if product is not None:
            if item.price is not None:
                product.base_unit_price = item.price
                product.smallest_unit = item.out_unit or ''
                product.save(update_fields=['base_unit_price', 'smallest_unit'])
            item.product = product
        if item.price is not None or item.product_id:
            item.save(update_fields=['price', 'out_unit', 'product'])


def backwards(apps, schema_editor):
    """Nothing to undo — the old shape had no cafe/section/product columns."""


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0089_cogs_multitenant_unify'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
