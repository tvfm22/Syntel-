from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

try:
    import jdatetime
except ImportError:
    jdatetime = None

from purchases.models import PriceHistory


def _format_jalali(dt):
    local_dt = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    if jdatetime is not None:
        try:
            jd = jdatetime.datetime.fromgregorian(datetime=local_dt)
            return jd.strftime('%Y/%m/%d %H:%M')
        except Exception:
            pass
    return local_dt.strftime('%Y-%m-%d %H:%M')


class Command(BaseCommand):
    help = 'Check for significant price increases and send cafe-scoped alerts to managers'

    def handle(self, *args, **options):
        start_time = timezone.now() - timedelta(hours=24)

        high_increases = (
            PriceHistory.objects.filter(
                date__gte=start_time,
                percent_change__gt=5,
            )
            .select_related('product', 'product__cafe', 'supplier')
            .order_by('product__cafe_id', '-date')
        )

        if not high_increases.exists():
            self.stdout.write(self.style.SUCCESS('No significant price increases found.'))
            return

        by_cafe = defaultdict(list)
        for history in high_increases:
            cafe = getattr(history.product, 'cafe', None)
            if cafe is None:
                continue
            by_cafe[cafe].append(history)

        if not by_cafe:
            self.stdout.write(self.style.WARNING('No cafe-scoped price increases found.'))
            return

        User = get_user_model()
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@syntel.com')
        sent_cafes = 0

        for cafe, records in by_cafe.items():
            managers_emails = list(
                User.objects.filter(
                    role='manager',
                    is_active=True,
                    cafe=cafe,
                    email__isnull=False,
                )
                .exclude(email='')
                .values_list('email', flat=True)
            )
            if not managers_emails:
                self.stdout.write(
                    self.style.WARNING(f'No manager emails found for cafe "{cafe.code}".')
                )
                continue

            lines = [
                f'هشدار: افزایش قیمت قابل توجه در محصولات کافه «{cafe.name}» مشاهده شده است:',
                '',
            ]
            for history in records:
                supplier_name = history.supplier.name if history.supplier else 'نامشخص'
                lines.extend([
                    f'- {history.product.name}: {history.percent_change}% افزایش',
                    f'  قیمت جدید: {history.price}',
                    f'  تأمین‌کننده: {supplier_name}',
                    f'  تاریخ: {_format_jalali(history.date)}',
                    '',
                ])
            lines.append('لطفاً برای جزئیات بیشتر به داشبورد نظارت بر قیمت مراجعه کنید.')

            try:
                send_mail(
                    subject=f'هشدار افزایش قیمت محصولات - {cafe.name}',
                    message='\n'.join(lines),
                    from_email=from_email,
                    recipient_list=managers_emails,
                    fail_silently=False,
                )
                sent_cafes += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Alert email for cafe "{cafe.code}" sent to {len(managers_emails)} manager(s).'
                    )
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f'Failed to send email for cafe "{cafe.code}": {exc}')
                )

        if sent_cafes == 0:
            self.stdout.write(self.style.WARNING('No alert emails were delivered.'))
