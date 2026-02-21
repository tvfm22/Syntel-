from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0045_alter_invoice_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='Warehouse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='نام انبار')),
                ('code', models.CharField(max_length=20, verbose_name='کد انبار')),
                ('description', models.TextField(blank=True, verbose_name='توضیحات')),
                ('location', models.CharField(blank=True, max_length=300, verbose_name='محل انبار')),
                ('manager_name', models.CharField(blank=True, max_length=100, verbose_name='مسئول انبار')),
                ('manager_phone', models.CharField(blank=True, max_length=20, verbose_name='تلفن مسئول')),
                ('is_active', models.BooleanField(default=True, verbose_name='فعال')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')),
                ('cafe', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='warehouses',
                    to='purchases.cafe',
                    verbose_name='کافه'
                )),
            ],
            options={
                'verbose_name': 'انبار',
                'verbose_name_plural': 'انبارها',
                'ordering': ['name'],
            },
        ),
        migrations.AddConstraint(
            model_name='warehouse',
            constraint=models.UniqueConstraint(fields=['cafe', 'code'], name='unique_warehouse_code_per_cafe'),
        ),
    ]
