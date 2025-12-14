from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from purchases.models import PriceHistory
from datetime import timedelta

class Command(BaseCommand):
    help = 'Check for significant price increases and send alerts to managers'

    def handle(self, *args, **options):
        # Time range: last 24 hours
        start_time = timezone.now() - timedelta(hours=24)
        
        # Find high increases
        high_increases = PriceHistory.objects.filter(
            date__gte=start_time,
            percent_change__gt=5
        ).select_related('product', 'supplier')

        if not high_increases.exists():
            self.stdout.write(self.style.SUCCESS('No significant price increases found.'))
            return

        # Prepare email content
        message = "هشدار: افزایش قیمت قابل توجه در محصولات زیر مشاهده شده است:\n\n"
        for history in high_increases:
            message += f"- {history.product.name}: {history.percent_change}% افزایش\n"
            message += f"  قیمت جدید: {history.price}\n"
            message += f"  تأمین‌کننده: {history.supplier.name if history.supplier else 'نامشخص'}\n"
            message += f"  تاریخ: {history.date.strftime('%Y-%m-%d %H:%M')}\n\n"

        message += "\nلطفاً برای جزئیات بیشتر به داشبورد نظارت بر قیمت مراجعه کنید."

        # Get managers emails
        User = get_user_model()
        managers_emails = User.objects.filter(
            role='manager', 
            email__isnull=False
        ).exclude(email='').values_list('email', flat=True)
        
        if not managers_emails:
            self.stdout.write(self.style.WARNING('No manager emails found.'))
            return

        # Send email
        try:
            send_mail(
                subject='هشدار افزایش قیمت محصولات',
                message=message,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@syntel.com'),
                recipient_list=list(managers_emails),
                fail_silently=False,
            )
            self.stdout.write(self.style.SUCCESS(f'Alert email sent to {len(managers_emails)} managers.'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to send email: {str(e)}'))
