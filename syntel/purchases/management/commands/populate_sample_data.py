from datetime import timedelta
import os
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from purchases.models import ActivityLog, Cafe, Department, PurchaseItem, PurchaseRequest, User


class Command(BaseCommand):
    help = 'Populate database with sample purchase request data (dev only)'

    def add_arguments(self, parser):
        parser.add_argument('--cafe', required=True, help='Target cafe code')
        parser.add_argument('--password', help='Sample user password (or set SYNTEL_SEED_PASSWORD)')
        parser.add_argument('--force', action='store_true', help='Allow running when DEBUG is False')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError('Refusing to run populate_sample_data when DEBUG=False without --force.')

        password = options.get('password') or os.environ.get('SYNTEL_SEED_PASSWORD')
        if not password or len(password) < 8:
            raise CommandError('Provide a password >= 8 chars via --password or SYNTEL_SEED_PASSWORD.')

        cafe = Cafe.objects.filter(code=options['cafe'], is_active=True).first()
        if cafe is None:
            raise CommandError(f'Active cafe "{options["cafe"]}" not found.')

        self.stdout.write(f'Creating sample data for cafe "{cafe.code}"...')

        dept_kitchen, _ = Department.objects.get_or_create(name='آشپزخانه', cafe=cafe, defaults={'is_active': True})
        dept_bar, _ = Department.objects.get_or_create(name='بار', cafe=cafe, defaults={'is_active': True})
        dept_counter, _ = Department.objects.get_or_create(name='کانتر', cafe=cafe, defaults={'is_active': True})

        users_data = [
            {'key': 'manager', 'role': 'manager', 'first_name': 'مدیر', 'last_name': 'سیستم', 'dept': None},
            {'key': 'kitchen1', 'role': 'requester', 'first_name': 'علی', 'last_name': 'آشپز', 'dept': dept_kitchen},
            {'key': 'bar1', 'role': 'requester', 'first_name': 'مریم', 'last_name': 'بارتندر', 'dept': dept_bar},
            {'key': 'counter1', 'role': 'requester', 'first_name': 'رضا', 'last_name': 'پیشخدمت', 'dept': dept_counter},
            {'key': 'purchasing1', 'role': 'purchasing', 'first_name': 'سارا', 'last_name': 'خریدار', 'dept': None},
        ]

        users = {}
        for spec in users_data:
            username = f'{cafe.code}_{spec["key"]}'
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'role': spec['role'],
                    'first_name': spec['first_name'],
                    'last_name': spec['last_name'],
                    'cafe': cafe,
                    'department': spec['dept'],
                    'is_active': True,
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=['password'])
                self.stdout.write(f'Created user: {user.username}')
            users[spec['key']] = user

        cafe_items = [
            {'name': 'قهوه عربیکا', 'quantity': 5, 'unit': 'کیلوگرم', 'description': 'قهوه مخصوص اسپرسو'},
            {'name': 'شیر گاو', 'quantity': 20, 'unit': 'لیتر', 'description': 'شیر تازه پاستوریزه'},
            {'name': 'شکر سفید', 'quantity': 10, 'unit': 'کیلوگرم', 'description': 'شکر تصفیه شده'},
            {'name': 'لیمون', 'quantity': 15, 'unit': 'کیلوگرم', 'description': 'لیمون تازه'},
            {'name': 'نعناع تازه', 'quantity': 3, 'unit': 'کیلوگرم', 'description': 'نعناع برای نوشیدنی‌ها'},
            {'name': 'کیک شکلاتی', 'quantity': 20, 'unit': 'عدد', 'description': 'کیک آماده سرو'},
            {'name': 'نان سنگک', 'quantity': 30, 'unit': 'عدد', 'description': 'نان تازه سنگک'},
            {'name': 'ماست یونانی', 'quantity': 12, 'unit': 'کیلوگرم', 'description': 'ماست برای دسرها'},
            {'name': 'عسل طبیعی', 'quantity': 5, 'unit': 'کیلوگرم', 'description': 'عسل کوهستان'},
            {'name': 'آب معدنی', 'quantity': 50, 'unit': 'بطری', 'description': 'آب معدنی ۱.۵ لیتری'},
            {'name': 'نوشابه کوکا', 'quantity': 24, 'unit': 'قوطی', 'description': 'نوشابه گازدار'},
            {'name': 'یخ', 'quantity': 20, 'unit': 'کیلوگرم', 'description': 'یخ برای نوشیدنی‌ها'},
        ]

        requests_data = [
            {'user': 'kitchen1', 'status': 'pending', 'items': cafe_items[:4], 'days_ago': 7},
            {'user': 'bar1', 'status': 'pending_manager_approval', 'items': cafe_items[4:8], 'days_ago': 3},
            {'user': 'counter1', 'status': 'purchased', 'items': cafe_items[8:12], 'days_ago': 10},
            {
                'user': 'kitchen1',
                'status': 'rejected',
                'items': [cafe_items[0], cafe_items[2]],
                'days_ago': 5,
                'rejection_reason': 'موجودی کافی در انبار وجود دارد',
            },
        ]

        for i, req_data in enumerate(requests_data):
            user = users[req_data['user']]
            created_at = timezone.now() - timedelta(days=req_data['days_ago'])
            req = PurchaseRequest.objects.create(
                user=user,
                cafe=cafe,
                status=req_data['status'],
            )
            PurchaseRequest.objects.filter(pk=req.pk).update(created_at=created_at)

            if req_data['status'] in ['purchased', 'rejected']:
                req.processed_by = users['purchasing1']
                req.processed_at = created_at + timedelta(hours=random.randint(1, 24))
                if req_data['status'] == 'rejected':
                    req.rejection_reason = req_data.get('rejection_reason', '')
                req.save()

            for item_data in req_data['items']:
                PurchaseItem.objects.create(
                    request=req,
                    name=item_data['name'],
                    quantity=item_data['quantity'],
                    unit=item_data['unit'],
                    description=item_data['description'],
                )

            ActivityLog.objects.create(
                user=user,
                request=req,
                action='created',
                description=f'درخواست خرید توسط {user.get_full_name()} ایجاد شد',
            )
            self.stdout.write(f'Created sample request #{i + 1}')

        self.stdout.write(self.style.SUCCESS(f'Successfully created sample data for cafe "{cafe.code}"!'))
