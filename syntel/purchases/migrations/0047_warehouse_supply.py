from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0046_warehouse'),
    ]

    operations = [
        # اضافه کردن is_supply_enabled به انبار
        migrations.AddField(
            model_name='warehouse',
            name='is_supply_enabled',
            field=models.BooleanField(
                default=True,
                verbose_name='قابل تامین (در درخواست‌های خرید قابل انتخاب باشد)',
            ),
        ),
        # اضافه کردن warehouse FK به StockRequest
        migrations.AddField(
            model_name='stockrequest',
            name='warehouse',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='stock_requests',
                to='purchases.warehouse',
                verbose_name='انبار تامین‌کننده',
            ),
        ),
    ]
