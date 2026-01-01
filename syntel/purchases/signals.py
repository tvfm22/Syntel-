from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.cache import cache
from .models import Invoice, PriceHistory, StockItem, StockTransaction, Requester

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
    """
    وقتی فاکتوری با درخواست‌کننده "انبار" ثبت و تایید می‌شود،
    محصولات به موجودی انبار اضافه می‌شوند
    """
    # فقط برای فاکتورهای تایید شده
    if instance.status == 'approved' and instance.approved_at:
        # بررسی اینکه درخواست‌کننده انبار باشد
        if instance.requester:
            try:
                store_requester = Requester.objects.get(name='store')
                if instance.requester == store_requester:
                    # بررسی که قبلاً تراکنش ثبت نشده باشد
                    existing_transactions = StockTransaction.objects.filter(
                        reference_type='invoice',
                        reference_id=instance.id
                    ).exists()
                    
                    if not existing_transactions:
                        # افزودن محصولات به انبار
                        for item in instance.invoice_items.all():
                            if item.product and item.quantity:
                                # دریافت یا ایجاد موجودی انبار
                                stock_item, created = StockItem.objects.get_or_create(
                                    product=item.product,
                                    defaults={
                                        'quantity': 0,
                                        'unit': item.product.unit,
                                        'min_quantity': 0
                                    }
                                )
                                
                                # ثبت تراکنش ورود به انبار
                                previous_qty = stock_item.quantity
                                stock_item.quantity += item.quantity
                                stock_item.save()
                                
                                StockTransaction.objects.create(
                                    stock_item=stock_item,
                                    transaction_type='in',
                                    quantity=item.quantity,
                                    unit=stock_item.unit,
                                    reference_type='invoice',
                                    reference_id=instance.id,
                                    description=f'ورود از فاکتور #{instance.id} - {instance.supplier.name if instance.supplier else ""}',
                                    created_by=instance.created_by,
                                    previous_quantity=previous_qty,
                                    new_quantity=stock_item.quantity
                                )
            except Requester.DoesNotExist:
                pass  # اگر درخواست‌کننده انبار وجود نداشت، کاری نکن

