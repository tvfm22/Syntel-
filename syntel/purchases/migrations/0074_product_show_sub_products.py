from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0073_purchaserequest_delivery_date_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='show_sub_products',
            field=models.BooleanField(default=False, help_text='اگر فعال باشد، هنگام نمایش کالا به درخواست‌دهندگان، نام‌های تجاری آن به‌صورت جداگانه نمایش داده می‌شود.', verbose_name='نمایش زیر محصولات'),
        ),
    ]
