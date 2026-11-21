from django.core.management.base import BaseCommand, CommandError

from purchases.models import Cafe, PaymentType


class Command(BaseCommand):
    help = 'Populate database with sample payment types for a cafe'

    def add_arguments(self, parser):
        parser.add_argument('--cafe', required=True, help='Target cafe code')

    def handle(self, *args, **options):
        cafe = Cafe.objects.filter(code=options['cafe'], is_active=True).first()
        if cafe is None:
            raise CommandError(f'Active cafe "{options["cafe"]}" not found.')

        self.stdout.write(f'Creating sample payment types for cafe "{cafe.code}"...')

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
                cafe=cafe,
                defaults={
                    'description': pt_data['description'],
                    'is_active': True,
                },
            )
            if created:
                self.stdout.write(f'Created payment type: {payment_type.name}')

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully created sample payment types for cafe "{cafe.code}"!'
            )
        )
