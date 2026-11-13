# -*- coding: utf-8 -*-
"""انتقال کالاهای ناقصِ بدون دسته‌بندیِ قبلی به دستهٔ «تحقیق و توسعه» کافهٔ مربوطه."""
from django.db import migrations


RD_CATEGORY_NAME = 'تحقیق و توسعه'
RD_CATEGORY_DESCRIPTION = 'کالاهای تعریف‌شده توسط درخواست‌کنندگان (تحقیق و توسعه)'


def move_incomplete_products_to_rd(apps, schema_editor):
    Product = apps.get_model('purchases', 'Product')
    Category = apps.get_model('purchases', 'Category')

    incomplete_qs = Product.objects.filter(is_complete=False, category__isnull=True)
    if not incomplete_qs.exists():
        return

    rd_cache = {}
    for product in incomplete_qs.iterator():
        cafe_id = product.cafe_id
        if cafe_id not in rd_cache:
            cat_qs = Category.objects.filter(name=RD_CATEGORY_NAME, cafe_id=cafe_id)
            cat = cat_qs.first()
            if cat is None:
                cat = Category.objects.create(
                    name=RD_CATEGORY_NAME,
                    cafe_id=cafe_id,
                    description=RD_CATEGORY_DESCRIPTION,
                    is_active=True,
                    requires_recipe=False,
                )
            elif not cat.is_active:
                cat.is_active = True
                cat.save(update_fields=['is_active'])
            rd_cache[cafe_id] = cat.id
        product.category_id = rd_cache[cafe_id]
        product.save(update_fields=['category'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0094_product_inactive_without_recipe'),
    ]

    operations = [
        migrations.RunPython(move_incomplete_products_to_rd, noop_reverse),
    ]
