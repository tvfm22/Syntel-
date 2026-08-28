"""حذف کامل بخش‌های سراسری (cafe=None) و الزامی کردن کافه برای بخش

بخش‌های دو کافه کاملاً از هم جدا هستند؛ هیچ بخش مشترک/سراسری قابل تعریف نیست.
"""
from django.db import migrations, models
import django.db.models.deletion


def delete_global_departments(apps, schema_editor):
    """بخش‌های سراسری قدیمی (cafe=None) را حذف کن."""
    Department = apps.get_model('purchases', 'Department')
    deleted = Department.objects.filter(cafe__isnull=True).delete()
    if deleted[0]:
        print(f"Deleted {deleted[0]} global department(s)")


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0074_product_show_sub_products'),
    ]

    operations = [
        migrations.RunPython(delete_global_departments, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='department',
            name='cafe',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='departments', to='purchases.cafe', verbose_name='کافه'),
        ),
    ]
