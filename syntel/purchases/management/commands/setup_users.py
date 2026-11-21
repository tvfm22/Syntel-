import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from purchases.models import Cafe, Department, User


class Command(BaseCommand):
    help = 'Create default users idempotently for a target cafe (dev/setup only)'

    def add_arguments(self, parser):
        parser.add_argument('--cafe', required=True, help='Target cafe code')
        parser.add_argument('--password', help='Initial password (or set SYNTEL_SEED_PASSWORD)')
        parser.add_argument('--force', action='store_true', help='Allow running when DEBUG is False')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError('Refusing to run setup_users when DEBUG=False without --force.')

        password = options.get('password') or os.environ.get('SYNTEL_SEED_PASSWORD')
        if not password or len(password) < 8:
            raise CommandError('Provide a password >= 8 chars via --password or SYNTEL_SEED_PASSWORD.')

        cafe = Cafe.objects.filter(code=options['cafe'], is_active=True).first()
        if cafe is None:
            raise CommandError(f'Active cafe "{options["cafe"]}" not found.')

        dept_names = {
            'kitchen': 'آشپزخانه',
            'bar': 'بار',
            'counter': 'کانتر',
            'cashier': 'صندوق',
            'facilities': 'تاسیسات',
        }
        departments = {}
        for key, label in dept_names.items():
            dept, _ = Department.objects.get_or_create(
                name=label,
                cafe=cafe,
                defaults={'is_active': True},
            )
            departments[key] = dept

        specs = [
            ('manager', 'manager', 'مدیر', 'سیستم', None),
            ('purchasing', 'purchasing', 'مسئول', 'خرید', None),
            ('warehouse', 'warehouse', 'مسئول', 'انبار', None),
            ('kitchen', 'requester', 'سرآشپز', 'آشپزخانه', departments['kitchen']),
            ('bar', 'requester', 'مسئول', 'بار', departments['bar']),
            ('counter', 'requester', 'مسئول', 'کانتر', departments['counter']),
            ('cashier', 'requester', 'مسئول', 'صندوق', departments['cashier']),
            ('facilities', 'requester', 'مسئول', 'تاسیسات', departments['facilities']),
        ]

        created_count = 0
        for base_name, role, first_name, last_name, dept in specs:
            username = f'{cafe.code}_{base_name}'
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'role': role,
                    'first_name': first_name,
                    'last_name': last_name,
                    'cafe': cafe,
                    'department': dept,
                    'is_active': True,
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=['password'])
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Created user: {username} ({role})'))
            else:
                self.stdout.write(f'User already exists: {username}')

        self.stdout.write(self.style.SUCCESS(f'Done. Created {created_count} user(s) for cafe "{cafe.code}".'))
