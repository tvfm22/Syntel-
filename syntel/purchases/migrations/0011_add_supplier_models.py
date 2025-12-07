# Generated manually for supplier models

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0010_paymentmethod_payment'),
    ]

    operations = [
        migrations.CreateModel(
            name='Supplier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True, verbose_name='نام تأمین‌کننده')),
                ('supplier_code', models.CharField(blank=True, max_length=20, unique=True, verbose_name='کد تأمین‌کننده')),
                ('supplier_type', models.CharField(choices=[('individual', 'شخص حقیقی'), ('company', 'شرکت'), ('cooperative', 'تعاونی'), ('government', 'دولتی'), ('other', 'سایر')], default='company', max_length=20, verbose_name='نوع تأمین‌کننده')),
                ('status', models.CharField(choices=[('active', 'فعال'), ('inactive', 'غیرفعال'), ('blacklisted', 'لیست سیاه'), ('pending', 'در انتظار تأیید')], default='pending', max_length=20, verbose_name='وضعیت')),
                ('contact_person', models.CharField(blank=True, max_length=100, verbose_name='شخص تماس')),
                ('phone', models.CharField(blank=True, max_length=20, verbose_name='تلفن')),
                ('mobile', models.CharField(blank=True, max_length=20, verbose_name='موبایل')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='ایمیل')),
                ('website', models.URLField(blank=True, verbose_name='وب‌سایت')),
                ('address', models.TextField(blank=True, verbose_name='آدرس')),
                ('city', models.CharField(blank=True, max_length=100, verbose_name='شهر')),
                ('state', models.CharField(blank=True, max_length=100, verbose_name='استان')),
                ('postal_code', models.CharField(blank=True, max_length=20, verbose_name='کد پستی')),
                ('country', models.CharField(default='ایران', max_length=100, verbose_name='کشور')),
                ('tax_id', models.CharField(blank=True, max_length=50, verbose_name='شناسه مالیاتی')),
                ('registration_number', models.CharField(blank=True, max_length=50, verbose_name='شماره ثبت')),
                ('business_license', models.CharField(blank=True, max_length=100, verbose_name='مجوز کسب')),
                ('bank_name', models.CharField(blank=True, max_length=100, verbose_name='نام بانک')),
                ('bank_account', models.CharField(blank=True, max_length=50, verbose_name='شماره حساب')),
                ('bank_routing', models.CharField(blank=True, max_length=50, verbose_name='کد شعبه/شبا')),
                ('rating', models.DecimalField(decimal_places=2, default=0, max_digits=3, verbose_name='امتیاز عملکرد')),
                ('total_orders', models.PositiveIntegerField(default=0, verbose_name='تعداد کل سفارشات')),
                ('total_amount', models.DecimalField(decimal_places=2, default=0, max_digits=15, verbose_name='مجموع مبلغ سفارشات')),
                ('on_time_delivery_rate', models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='نرخ تحویل به موقع (%)')),
                ('quality_rating', models.DecimalField(decimal_places=2, default=0, max_digits=3, verbose_name='امتیاز کیفیت')),
                ('payment_terms', models.TextField(blank=True, verbose_name='شرایط پرداخت پیش‌فرض')),
                ('credit_limit', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='حد اعتباری')),
                ('current_balance', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='مانده فعلی')),
                ('notes', models.TextField(blank=True, verbose_name='یادداشت‌ها')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')),
                ('categories', models.ManyToManyField(blank=True, related_name='suppliers', to='purchases.category', verbose_name='دسته‌بندی‌های تأمین')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_suppliers', to=settings.AUTH_USER_MODEL, verbose_name='ایجاد شده توسط')),
            ],
            options={
                'verbose_name': 'تأمین‌کننده',
                'verbose_name_plural': 'تأمین‌کنندگان',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='SupplierEvaluation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('evaluation_type', models.CharField(choices=[('automatic', 'ارزیابی خودکار'), ('manual', 'ارزیابی دستی'), ('periodic', 'ارزیابی دوره‌ای')], default='manual', max_length=20, verbose_name='نوع ارزیابی')),
                ('evaluation_date', models.DateField(default=django.utils.timezone.now, verbose_name='تاریخ ارزیابی')),
                ('quality_rating', models.PositiveIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name='امتیاز کیفیت')),
                ('delivery_rating', models.PositiveIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name='امتیاز تحویل')),
                ('price_rating', models.PositiveIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name='امتیاز قیمت')),
                ('communication_rating', models.PositiveIntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], verbose_name='امتیاز ارتباط')),
                ('overall_rating', models.DecimalField(blank=True, decimal_places=2, max_digits=3, null=True, verbose_name='امتیاز کلی')),
                ('strengths', models.TextField(blank=True, verbose_name='نقاط قوت')),
                ('weaknesses', models.TextField(blank=True, verbose_name='نقاط ضعف')),
                ('recommendations', models.TextField(blank=True, verbose_name='پیشنهادات')),
                ('comments', models.TextField(blank=True, verbose_name='نظرات کلی')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')),
                ('evaluator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='supplier_evaluations', to=settings.AUTH_USER_MODEL, verbose_name='ارزیاب')),
                ('related_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='purchases.purchaseorder', verbose_name='سفارش مرتبط')),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='evaluations', to='purchases.supplier', verbose_name='تأمین‌کننده')),
            ],
            options={
                'verbose_name': 'ارزیابی تأمین‌کننده',
                'verbose_name_plural': 'ارزیابی‌های تأمین‌کننده',
                'ordering': ['-evaluation_date'],
                'unique_together': {('supplier', 'evaluator', 'evaluation_date', 'evaluation_type')},
            },
        ),
    ]