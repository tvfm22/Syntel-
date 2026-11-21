import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from purchases.models import Cafe, Department, User


class Command(BaseCommand):
    help = 'Seed initial users idempotently for a target cafe (dev/setup only)'

    def add_arguments(self, parser):
        parser.add_argument('--cafe', required=True, help='Target cafe code')
        parser.add_argument('--password', help='Initial password (or set SYNTEL_SEED_PASSWORD)')
        parser.add_argument('--force', action='store_true', help='Allow running when DEBUG is False')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError('Refusing to run seed_users when DEBUG=False without --force.')

        password = options.get('password') or os.environ.get('SYNTEL_SEED_PASSWORD')
        if not password or len(password) < 8:
            raise CommandError('Provide a password >= 8 chars via --password or SYNTEL_SEED_PASSWORD.')

        cafe = Cafe.objects.filter(code=options['cafe'], is_active=True).first()
        if cafe is None:
            raise CommandError(f'Active cafe "{options["cafe"]}" not found.')

        requester_depts = ['آشپزخانه', 'بار', 'کانتر', 'صندوق', 'تاسیسات']
        dept_objs = {}
        for label in requester_depts:
            dept_objs[label], _ = Department.objects.get_or_create(
                name=label, cafe=cafe, defaults={'is_active': True}
            )

        users_spec = [
            ('kitchen', 'requester', dept_objs['آشپزخانه']),
            ('bar', 'requester', dept_objs['بار']),
            ('counter', 'requester', dept_objs['کانتر']),
            ('cashier', 'requester', dept_objs['صندوق']),
            ('facilities', 'requester', dept_objs['تاسیسات']),
            ('purchasing', 'purchasing', None),
            ('manager', 'manager', None),
        ]

        for base_username, role, dept in users_spec:
            username = f'{cafe.code}_{base_username}'
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'role': role,
                    'cafe': cafe,
                    'department': dept,
                    'is_active': True,
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=['password'])

        self.stdout.write(self.style.SUCCESS(f'Initial users seeded for cafe "{cafe.code}".'))
