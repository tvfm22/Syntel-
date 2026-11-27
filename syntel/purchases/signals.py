from decimal import Decimal, ROUND_HALF_UP
import logging

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver

from .models import (
    Invoice, Payment, PaymentAllocation, PriceHistory, Requester,
    StockItem, Warehouse, WarehouseVoucher, WarehouseVoucherItem,
)
from .services.inventory import finalize_voucher, next_voucher_number, reverse_voucher
from .services.units import unit_family

logger = logging.getLogger(__name__)

_MAX_PERCENT_CHANGE = Decimal('99999999.99')
_MIN_PERCENT_CHANGE = Decimal('-99999999.99')


def _invalidate_dashboard_stats_cache(cafe=None):
    """حذف کلیدهای کش آمار داشبورد متناسب با ساختار کلید در views/price_dashboard.py."""
    cafe_codes = ['all']
    if cafe and getattr(cafe, 'code', None):
        cafe_codes.append(cafe.code)
    keys = ['dashboard_stats']
    for code in cafe_codes:
        for days in (None, '7', '30', '90', '365'):
            keys.append(f'dashboard_stats_{code}_None_None_{days}')
    cache.delete_many(keys)
    if hasattr(cache, 'delete_pattern'):
        for code in cafe_codes:
            cache.delete_pattern(f'dashboard_stats_{code}_*')


def _recalculate_invoices(invoice_ids):
    ids = sorted({iid for iid in (invoice_ids or []) if iid})
    if not ids:
        return
    with transaction.atomic():
        for inv in Invoice.objects.select_for_update().filter(id__in=ids).order_by('id'):
            inv.update_payment_status()


@receiver(post_save, sender=Invoice)
def create_price_history_on_approval(sender, instance, created, **kwargs):
    if kwargs.get('raw'):
        return
    if instance.status != 'approved' or not instance.approved_at:
        return

    with transaction.atomic():
        locked = (
            Invoice.objects.select_for_update()
            .select_related('cafe', 'supplier')
            .filter(pk=instance.pk)
            .first()
        )
        if not locked or locked.status != 'approved' or not locked.approved_at:
            return
        if PriceHistory.objects.filter(invoice=locked).exists():
            return

        created_any = False
        for item in locked.invoice_items.select_related('product').all():
            if not (item.product_id and item.unit_price):
                continue
            last_history = (
                PriceHistory.objects.filter(product_id=item.product_id)
                .order_by('-date', '-id')
                .first()
            )
            percent_change = Decimal('0.00')
            if last_history and last_history.price:
                raw_pct = (
                    (Decimal(str(item.unit_price)) - Decimal(str(last_history.price)))
                    / Decimal(str(last_history.price))
                ) * Decimal('100')
                raw_pct = max(_MIN_PERCENT_CHANGE, min(_MAX_PERCENT_CHANGE, raw_pct))
                percent_change = raw_pct.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            PriceHistory.objects.create(
                product=item.product,
                price=item.unit_price,
                date=locked.approved_at,
                invoice=locked,
                supplier=locked.supplier,
                percent_change=percent_change,
            )
            created_any = True

        if created_any:
            _invalidate_dashboard_stats_cache(locked.cafe)


def _convert_qty_and_price(qty, unit_price, from_unit, to_unit):
    """تبدیل مقدار و قیمت واحد بین دو واحد هم‌خانواده (مثلاً کیلوگرم <-> گرم، لیتر <-> میل)."""
    if not from_unit or not to_unit or from_unit.pk == to_unit.pk:
        return qty, unit_price

    from_fam = unit_family(from_unit.name or from_unit.symbol)
    to_fam = unit_family(to_unit.name or to_unit.symbol)
    factor = None
    if (from_fam, to_fam) in (('kg', 'gram'), ('liter', 'mil')):
        factor = Decimal('1000')
    elif (from_fam, to_fam) in (('gram', 'kg'), ('mil', 'liter')):
        factor = Decimal('0.001')

    if factor is None:
        return qty, unit_price

    new_qty = Decimal(str(qty)) * factor
    new_price = (Decimal(str(unit_price)) / factor) if unit_price else Decimal('0')
    return new_qty, new_price


@receiver(post_save, sender=Invoice)
def add_to_stock_on_store_invoice(sender, instance, created, **kwargs):
    """وقتی فاکتوری تایید می‌شود، اقلامی که درخواست‌کنندهٔ «انبار» دارند
    با یک حوالهٔ ورود (receipt) به موجودی انبار اضافه می‌شوند.

    مسیر اعمال موجودی: ساخت حوالهٔ ورود + نهایی‌سازی از طریق سرویس مرکزی
    (services.inventory) — نوشتن مستقیم موجودی وجود ندارد.
    """
    if kwargs.get('raw'):
        return
    if instance.status != 'approved' or not instance.approved_at:
        return
    if instance.cafe_id is None:
        logger.warning(
            'فاکتور #%s فاقد کافه است — ورود خودکار به انبار مجاز نیست (fail-closed).',
            instance.pk,
        )
        return

    with transaction.atomic():
        locked = (
            Invoice.objects.select_for_update()
            .select_related('cafe', 'supplier', 'approved_by', 'created_by')
            .filter(pk=instance.pk)
            .first()
        )
        if not locked or locked.status != 'approved' or not locked.approved_at:
            return
        if locked.cafe_id is None:
            return

        store_requester = Requester.objects.filter(
            name='store', cafe_id=locked.cafe_id
        ).first()
        if not store_requester:
            return

        store_items = [
            item
            for item in locked.invoice_items.select_related('product', 'product__unit', 'requester')
            if item.requester_id == store_requester.id
            and item.product_id
            and item.quantity
            and item.quantity > 0
        ]
        if not store_items:
            return

        invoice_ct = ContentType.objects.get_for_model(locked)
        if WarehouseVoucher.objects.filter(
            source_content_type=invoice_ct,
            source_object_id=locked.id,
            voucher_type='receipt',
        ).exclude(status__in=['cancelled', 'reversed']).exists():
            return

        target_warehouse = (
            Warehouse.objects.filter(cafe_id=locked.cafe_id, is_active=True)
            .order_by('id')
            .first()
        )
        if not target_warehouse:
            logger.warning(
                'فاکتور #%s (اقلام انبار) تایید شد ولی کافهٔ %s انبارِ فعال ندارد — ورود ثبت نشد.',
                locked.id,
                locked.cafe.code if locked.cafe else None,
            )
            return

        prepared_items = []
        for item in store_items:
            existing_stock = (
                StockItem.objects.filter(product=item.product, warehouse=target_warehouse)
                .select_related('unit')
                .first()
            )
            target_unit = (
                existing_stock.unit
                if (existing_stock and existing_stock.unit_id)
                else item.product.unit
            )
            if not target_unit:
                logger.warning(
                    'فاکتور #%s: محصول «%s» (#%s) فاقد واحد اندازه‌گیری است — از ورود به انبار عبور شد.',
                    locked.id,
                    item.product.name,
                    item.product_id,
                )
                continue

            qty, price = _convert_qty_and_price(
                item.quantity,
                item.unit_price or Decimal('0'),
                item.product.unit,
                target_unit,
            )
            prepared_items.append((item, existing_stock, target_unit, qty, price))

        if not prepared_items:
            return

        actor = locked.approved_by or locked.created_by
        supplier_name = locked.supplier.name if locked.supplier else ''
        voucher = WarehouseVoucher(
            cafe=locked.cafe,
            voucher_type='receipt',
            status='submitted',
            voucher_number=next_voucher_number(locked.cafe_id, 'receipt'),
            destination_warehouse=target_warehouse,
            description=(
                f'ورود کالا از فاکتور #{locked.id}'
                f' - {supplier_name} - انبار {target_warehouse.name}'
            ),
            created_by=actor,
            source_content_type=invoice_ct,
            source_object_id=locked.id,
        )
        voucher.save()

        for item, existing_stock, target_unit, qty, price in prepared_items:
            if existing_stock is not None:
                stock_item = existing_stock
            else:
                stock_item, _created = StockItem.objects.get_or_create(
                    product=item.product,
                    warehouse=target_warehouse,
                    defaults={
                        'quantity': Decimal('0'),
                        'unit': target_unit,
                        'min_quantity': Decimal('0'),
                    },
                )
            WarehouseVoucherItem.objects.create(
                voucher=voucher,
                product=item.product,
                stock_item=stock_item,
                unit=target_unit,
                base_quantity=qty,
                unit_price=price,
                description=f'فاکتور #{locked.id}',
            )

        finalize_voucher(voucher, finalized_by=actor)
        logger.info(
            'فاکتور #%s → حوالهٔ ورود %s نهایی شد (%d قلم انبار)',
            locked.id,
            voucher.voucher_number,
            len(prepared_items),
        )


def _reverse_invoice_receipt_vouchers(invoice, actor=None, reason=''):
    """برگشت حواله‌های ورودِ نهایی‌شده و برگشت‌نخوردهٔ مرتبط با فاکتور."""
    if not invoice or not invoice.pk:
        return
    invoice_ct = ContentType.objects.get_for_model(Invoice)
    vouchers = list(
        WarehouseVoucher.objects.filter(
            source_content_type=invoice_ct,
            source_object_id=invoice.pk,
            voucher_type='receipt',
            status='finalized',
            reversed_at__isnull=True,
            reverses__isnull=True,
        ).order_by('id')
    )
    for voucher in vouchers:
        reverse_voucher(
            voucher,
            created_by=actor or invoice.updated_by or invoice.approved_by or invoice.created_by,
            description=reason or f'برگشت خودکار حوالهٔ فاکتور #{invoice.pk}',
        )


@receiver(post_save, sender=Invoice)
def reverse_stock_on_invoice_cancel(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    if instance.status != 'cancelled':
        return
    _reverse_invoice_receipt_vouchers(
        instance,
        actor=instance.updated_by or instance.approved_by or instance.created_by,
        reason=f'برگشت خودکار به دلیل لغو فاکتور #{instance.pk}',
    )


@receiver(pre_delete, sender=Invoice)
def reverse_stock_on_invoice_delete(sender, instance, **kwargs):
    _reverse_invoice_receipt_vouchers(
        instance,
        actor=instance.updated_by or instance.approved_by or instance.created_by,
        reason=f'برگشت خودکار به دلیل حذف فاکتور #{instance.pk}',
    )


@receiver(post_save, sender=PaymentAllocation)
def recalc_invoice_on_allocation_save(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    _recalculate_invoices([instance.invoice_id])


@receiver(post_delete, sender=PaymentAllocation)
def recalc_invoice_on_allocation_delete(sender, instance, **kwargs):
    _recalculate_invoices([instance.invoice_id])


@receiver(pre_delete, sender=Payment)
def capture_payment_invoices_before_delete(sender, instance, **kwargs):
    ids = set()
    if instance.invoice_id:
        ids.add(instance.invoice_id)
    if instance.pk:
        ids.update(instance.invoices.values_list('id', flat=True))
        ids.update(instance.allocations.values_list('invoice_id', flat=True))
    instance._deleted_invoice_ids = list(ids)


@receiver(post_delete, sender=Payment)
def recalc_invoices_on_payment_delete(sender, instance, **kwargs):
    ids = getattr(instance, '_deleted_invoice_ids', None) or (
        [instance.invoice_id] if instance.invoice_id else []
    )
    _recalculate_invoices(ids)


@receiver(m2m_changed, sender=Payment.invoices.through)
def recalc_invoices_on_payment_m2m_change(sender, instance, action, pk_set, **kwargs):
    if action in ('post_add', 'post_remove'):
        _recalculate_invoices(pk_set)
    elif action == 'pre_clear':
        instance._cleared_m2m_invoice_ids = list(
            instance.invoices.values_list('id', flat=True)
        )
    elif action == 'post_clear':
        _recalculate_invoices(getattr(instance, '_cleared_m2m_invoice_ids', []))
