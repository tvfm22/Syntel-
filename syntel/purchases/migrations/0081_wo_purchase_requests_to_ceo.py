"""درخواست‌های خریدِ مسئول انبار به صف تایید مدیرعامل منتقل می‌شوند.

جریان قبلی: «ارسال» از صفحه درخواست خرید، درخواست (source=warehouse_purchase) را
مستقیماً به مسئول انبار تخصیص می‌داد — در حالی که خودِ مسئول انبار فرستنده بود
(تخصیص به صندوق مشترکِ خودش؛ حلقهٔ بی‌معنا روی داشبورد انبار).

جریان جدید: درخواست خرید بدون تخصیص ساخته می‌شود و تا تایید مدیرعامل در صف
«در انتظار تایید» می‌ماند؛ پس از تایید، واحد خرید از فهرست «ثبت سفارش خرید»
برایش سفارش ثبت می‌کند.

این مایگریشن درخواست‌های قبلیِ باز را به وضعیت جدید می‌برد:
- فقط درخواست‌های منبع «warehouse_purchase» که هنوز «pending» هستند
- فقط وقتی هیچ قلمی اقدام نشده (هیچ تخصیص fulfilled/purchased/not_in_stock/returned)
- تخصیص‌های در انتظارِ این درخواست‌ها حذف می‌شوند و وضعیت → pending_manager_approval
"""
from django.db import migrations


def forward(apps, schema_editor):
    PurchaseRequest = apps.get_model('purchases', 'PurchaseRequest')
    OfficerAssignment = apps.get_model('purchases', 'OfficerAssignment')

    # درخواست‌های خرید انبارِ باز که تخصیص دارند (از جریان قبلی خود-ارجاعی)
    req_ids = list(
        OfficerAssignment.objects
        .filter(item__request__source='warehouse_purchase',
                item__request__status='pending')
        .values_list('item__request_id', flat=True)
        .distinct()
    )
    for rid in req_ids:
        req = PurchaseRequest.objects.filter(id=rid).first()
        if req is None:
            continue
        # اگر روی قلمی اقدام شده (خرید/تامین/عدم موجودی/بازگشت)، کار نیمه‌تمام
        # است و نباید به صف تایید برگردد
        actioned = OfficerAssignment.objects.filter(
            item__request=req,
            status__in=('purchased', 'fulfilled', 'not_in_stock', 'returned'),
        ).exists()
        if actioned:
            continue
        OfficerAssignment.objects.filter(item__request=req).delete()
        req.status = 'pending_manager_approval'
        req.save(update_fields=['status'])


def backward(apps, schema_editor):
    # برگشت تخصیص‌های حذف‌شده قابل بازسازی نیست؛ عملیاتی نوشته نشده.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0080_direct_to_warehouse'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
