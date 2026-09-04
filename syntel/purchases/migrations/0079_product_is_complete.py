from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0078_inventory_service_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_complete',
            field=models.BooleanField(default=True, help_text='کالای ناقص تا تکمیل اطلاعات در پیشنهادهای درخواست نمایش داده نمی‌شود.', verbose_name='اطلاعات کامل'),
        ),
    ]
