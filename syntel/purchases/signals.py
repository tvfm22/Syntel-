import logging

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    Invoice, PriceHistory, Requester, StockItem, Warehouse,
    WarehouseVoucher, WarehouseVoucherItem,
)
from .services.inventory import finalize_voucher, next_voucher_number

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Invoice)
def create_price_history_on_approval(sender, instance, created, **kwargs):
    if instance.status == 'approved' and instance.approved_at:
        # Check if we already have history for this invoice to avoid duplicates
        if not PriceHistory.objects.filter(invoice=instance).exists():
            for item in instance.invoice_items.all():
                if item.product and item.unit_price:
                    # Calculate percent change
                    last_history = PriceHistory.objects.filter(
                        product=item.product
                    ).order_by('-date').first()

                    percent_change = 0
                    if last_history and last_history.price:
                        percent_change = ((item.unit_price - last_history.price) / last_history.price) * 100

                    PriceHistory.objects.create(
                        product=item.product,
                        price=item.unit_price,
                        date=instance.approved_at,
                        invoice=instance,
                        supplier=instance.supplier,
                        percent_change=percent_change
                    )

            # Invalidate dashboard stats cache
            cache.delete('dashboard_stats')


@receiver(post_save, sender=Invoice)
def add_to_stock_on_store_invoice(sender, instance, created, **kwargs):
    """وقتی فاکتوری تایید می‌شود، اقلامی که درخواست‌کنندهٔ «انبار» دارند
    با یک حوالهٔ ورود (receipt) به موجودی انبار اضافه می‌شوند.

    مسیر اعمال موجودی: ساخت حوالهٔ ورود + نهایی‌سازی از طریق سرویس مرکزی
    (services.inventory) — نوشتن مستقیم موجودی وجود ندارد.

    نکته: درخواست‌کننده روی «قلم فاکتور» (InvoiceItem.requester) ثبت می‌شود —
    نه روی خود فاکتور؛ پیاده‌سازی قبلی روی فاکتور چک می‌کرد و در عمل هرگز
    اجرا نمی‌شد (کد مرده).
    """
    # فقط برای فاکتورهای تایید شده
    if instance.status != 'approved' or not instance.approved_at:
        return

    try:
        store_requester = Requester.objects.get(name='store')
    except Requester.DoesNotExist:
        return  # اگر درخواست‌کننده انبار وجود نداشت، کاری نکن

    # اقلامی که درخواست‌کنندهٔ «انبار» دارند
    store_items = [
        item for item in instance.invoice_items.select_related('product', 'requester')
        if item.requester_id == store_requester.id
        and item.product_id and item.quantity
    ]
    if not store_items:
        return

    # جلوگیری از ثبت دوباره (post_save چندباره / ذخیرهٔ مجدد فاکتور)
    invoice_ct = ContentType.objects.get_for_model(instance)
    if WarehouseVoucher.objects.filter(
        source_content_type=invoice_ct,
        source_object_id=instance.id,
        voucher_type='receipt',
    ).exists():
        return

    # پیدا کردن انبار مرتبط با کافه فاکتور
    target_warehouse = None
    if instance.cafe:
        target_warehouse = Warehouse.objects.filter(
            cafe=instance.cafe, is_active=True
        ).order_by('id').first()

    if not target_warehouse:
        # بدون انبارِ فعال، کالا هنوز وارد موجودی نمی‌شود (حوالهٔ ورود
        # بدون انبارِ مقصد نامعتبر است)؛ پیش از این، دادهٔ نادیدنی
        # (موجودیِ بدون انبار) ساخته می‌شد.
        logger.warning(
            'فاکتور #%s (اقلام انبار) تایید شد ولی کافهٔ %s انبارِ فعال ندارد — ورود ثبت نشد.',
            instance.id, instance.cafe.code if instance.cafe else None,
        )
        return

    supplier_name = instance.supplier.name if instance.supplier else ''
    voucher = WarehouseVoucher(
        cafe=instance.cafe,
        voucher_type='receipt',
        status='submitted',
        voucher_number=next_voucher_number(instance.cafe_id, 'receipt'),
        destination_warehouse=target_warehouse,
        description=(
            f'ورود کالا از فاکتور #{instance.id}'
            f' - {supplier_name} - انبار {target_warehouse.name}'
        ),
        created_by=instance.created_by,
        source_content_type=invoice_ct,
        source_object_id=instance.id,
    )
    voucher.save()

    for item in store_items:
        # دریافت یا ایجاد موجودی انبار برای این انبار مشخص
        stock_item, _created = StockItem.objects.get_or_create(
            product=item.product,
            warehouse=target_warehouse,
            defaults={
                'quantity': 0,
                'unit': item.product.unit,
                'min_quantity': 0
            }
        )
        WarehouseVoucherItem.objects.create(
            voucher=voucher,
            product=item.product,
            stock_item=stock_item,
            unit=item.product.unit,
            base_quantity=item.quantity,
            description=f'فاکتور #{instance.id}',
        )

    finalize_voucher(voucher, finalized_by=instance.created_by)
    logger.info(
        'فاکتور #%s → حوالهٔ ورود %s نهایی شد (%d قلم انبار)',
        instance.id, voucher.voucher_number, len(store_items),
    )
