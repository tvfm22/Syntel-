from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.cache import cache
from .models import Invoice, PriceHistory

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
