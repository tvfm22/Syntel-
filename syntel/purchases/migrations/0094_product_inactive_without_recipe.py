# -*- coding: utf-8 -*-
"""غیرفعال‌کردن پیش‌فرض محصولاتی که در هیچ رسپی منو مصرف نشده‌اند."""
from django.db import migrations


def deactivate_products_without_recipe(apps, schema_editor):
    Product = apps.get_model('purchases', 'Product')
    KukerRecipeLine = apps.get_model('purchases', 'KukerRecipeLine')

    used_pairs = set()
    used_product_ids = set()
    for prod_id, item_cafe_id in KukerRecipeLine.objects.filter(
        material__product_id__isnull=False,
    ).values_list('material__product_id', 'item__cafe_id'):
        used_pairs.add((prod_id, item_cafe_id))
        used_product_ids.add(prod_id)

    to_deactivate = []
    for prod_id, cafe_id in Product.objects.filter(is_active=True).values_list('id', 'cafe_id'):
        in_recipe = ((prod_id, cafe_id) in used_pairs) if cafe_id is not None else (prod_id in used_product_ids)
        if not in_recipe:
            to_deactivate.append(prod_id)

    if to_deactivate:
        Product.objects.filter(id__in=to_deactivate).update(is_active=False)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0093_remove_warehousevoucheritem_product_brand_and_more'),
    ]

    operations = [
        migrations.RunPython(deactivate_products_without_recipe, noop_reverse),
    ]
