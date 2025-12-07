from django.core.management.base import BaseCommand
from purchases.models import PaymentType

class Command(BaseCommand):
    help = 'Populate database with sample payment types'

    def handle(self, *args, **options):
        self.stdout.write('Creating sample payment types...')

        # Create sample payment types
        payment_types_data = [
            {'name': 'نقدی', 'description': 'پرداخت نقدی'},
            {'name': 'کارت اعتباری', 'description': 'پرداخت با کارت اعتباری یا بانکی'},
            {'name': 'کارت بانکی', 'description': 'پرداخت با کارت بانکی (دبیت کارت)'},
            {'name': 'چک', 'description': 'پرداخت با چک بانکی'},
            {'name': 'انتقال بانکی', 'description': 'انتقال بانکی یا پایا'},
            {'name': 'ساتنا', 'description': 'پرداخت ساتنا (بین بانکی)'},
            {'name': 'درگاه پرداخت', 'description': 'پرداخت آنلاین از طریق درگاه'},
            {'name': 'اعتبار', 'description': 'پرداخت اعتباری (برای مشتریان خاص)'},
        ]

        for pt_data in payment_types_data:
            payment_type, created = PaymentType.objects.get_or_create(
                name=pt_data['name'],
                defaults={
                    'description': pt_data['description'],
                    'is_active': True,
                }
            )
            if created:
                self.stdout.write(f'Created payment type: {payment_type.name}')

        self.stdout.write(self.style.SUCCESS('Successfully created sample payment types!'))

        # Summary
        total_payment_types = PaymentType.objects.count()
        self.stdout.write(f'\nSummary:')
        self.stdout.write(f'  - Total payment types: {total_payment_types}')