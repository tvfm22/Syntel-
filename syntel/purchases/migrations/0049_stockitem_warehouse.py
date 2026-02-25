from django.db import migrations, models
import django.db.models.deletion


def assign_existing_to_default_warehouse(apps, schema_editor):
    """انتقال همه موجودی‌های موجود به انبار مرکزی (اولین انبار فعال)"""
    StockItem = apps.get_model('purchases', 'StockItem')
    Warehouse = apps.get_model('purchases', 'Warehouse')

    # انتخاب اولین انبار فعال
    default_warehouse = Warehouse.objects.filter(is_active=True).order_by('id').first()
    if default_warehouse is None:
        return  # اگر انباری وجود ندارد کاری نکن

    updated = StockItem.objects.filter(warehouse__isnull=True).update(warehouse=default_warehouse)
    print(f'\n  ✅ {updated} موجودی به انبار "{default_warehouse.name}" منتقل شد.')


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0048_remove_warehouse_unique_warehouse_code_per_cafe_and_more'),
    ]

    operations = [
        # مرحله ۱: تبدیل OneToOneField به ForeignKey (همان ستون، فقط constraint عوض می‌شه)
        migrations.AlterField(
            model_name='stockitem',
            name='product',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='stock_items',
                to='purchases.product',
                verbose_name='محصول',
            ),
        ),

        # مرحله ۲: افزودن فیلد warehouse به صورت nullable
        migrations.AddField(
            model_name='stockitem',
            name='warehouse',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='stock_items',
                to='purchases.warehouse',
                verbose_name='انبار',
            ),
        ),

        # مرحله ۳: انتقال داده‌های موجود به انبار پیش‌فرض
        migrations.RunPython(
            assign_existing_to_default_warehouse,
            migrations.RunPython.noop,
        ),

        # مرحله ۴: unique_together برای (product, warehouse)
        migrations.AlterUniqueTogether(
            name='stockitem',
            unique_together={('product', 'warehouse')},
        ),

        # مرحله ۵: index روی warehouse
        migrations.AddIndex(
            model_name='stockitem',
            index=models.Index(fields=['warehouse'], name='purchases_s_warehou_idx'),
        ),
    ]
