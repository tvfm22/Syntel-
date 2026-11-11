# -*- coding: utf-8 -*-
"""Flag the food categories that actually exist in this database as
``requires_recipe``.

The explicit name list in 0090 covers the canonical category names. Real
installations use variations («لبنیات و تخم مرغ», «گوشت و محصولات پروتئینی»,
«خشکبار و مغزیجات», ...), so this pass matches on food keywords instead of
exact names. Idempotent.
"""
from django.db import migrations

FOOD_KEYWORDS = [
    'مواد غذایی',
    'نوشیدنی',
    'لبنیات',
    'تخم مرغ',
    'میوه',
    'سبزی',
    'تره بار',
    'گوشت',
    'پروتئین',
    'مرغ',
    'حبوبات',
    'برنج',
    'ادویه',
    'عطاری',
    'چاشنی',
    'سس',
    'کنسرو',
    'ترشی',
    'نان',
    'غلات',
    'تنقلات',
    'خشکبار',
    'مغزیجات',
    'شیرینی',
    'قنادی',
    'خوار و بار',
    'مواد اولیه',
]


def forwards(apps, schema_editor):
    Category = apps.get_model('purchases', 'Category')
    for category in Category.objects.filter(requires_recipe=False):
        if any(word in category.name for word in FOOD_KEYWORDS):
            category.requires_recipe = True
            category.save(update_fields=['requires_recipe'])


def backwards(apps, schema_editor):
    """Leave the flags in place — turning them off would lose the admin's intent."""


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0090_cogs_cafe_sections_and_smallest_units'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
