"""ارسال مستقیم درخواست کالا به مسئول انبار.

از این پس درخواست‌های فرم «درخواست کالا» بلافاصله برای مسئول انبار ارسال می‌شوند
(تخصیص انبار مشترک، بدون مرحله تایید مدیر). این مایگریشن همین تغییر را برای
درخواست‌های قبلی که هنوز در انتظار تایید مانده‌اند اعمال می‌کند:

- فقط درخواست‌های منبع «requester» با وضعیت «pending_manager_approval»
- فقط اقلامی که هیچ تخصیصی ندارند
- تخصیص انبار مشترک (assigned_to=None) با وضعیت «pending»
- وضعیت درخواست بر اساس تخصیص‌ها همگام می‌شود (→ «pending»)
"""
from django.db import migrations


def forward(apps, schema_editor):
    PurchaseRequest = apps.get_model('purchases', 'PurchaseRequest')
    PurchaseItem = apps.get_model('purchases', 'PurchaseItem')
    OfficerAssignment = apps.get_model('purchases', 'OfficerAssignment')

    stuck = PurchaseRequest.objects.filter(
        status='pending_manager_approval', source='requester')
    for req in stuck:
        items = list(PurchaseItem.objects.filter(request=req))
        if not items:
            continue
        assigned_item_ids = set(
            OfficerAssignment.objects.filter(item__request=req).values_list('item_id', flat=True))
        created = False
        for item in items:
            if item.id in assigned_item_ids:
                continue
            OfficerAssignment.objects.create(
                item=item,
                target_role='warehouse',
                assigned_to=None,
                assigned_by=req.user,
                status='pending',
                notes='ارسال مستقیم از فرم درخواست کالا (تبدیل گروهی درخواست‌های قبلی)',
            )
            created = True
        if created:
            # همه اقلام اکنون تخصیص در انتظار دارند → در انتظار اقدام مسئول انبار
            req.status = 'pending'
            req.save(update_fields=['status'])


def backward(apps, schema_editor):
    # برگشت کامل تخصیص‌های این مایگرینینگ قابل تشخیص نیست؛ عملیاتی نوشته نشده.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0079_product_is_complete'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
