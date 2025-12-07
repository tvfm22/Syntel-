from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from purchases.models import User, PurchaseRequest, PurchaseItem, ActivityLog
from datetime import timedelta
import random

class Command(BaseCommand):
    help = 'Populate database with sample purchase request data'

    def handle(self, *args, **options):
        self.stdout.write('Creating sample data...')

        # Create sample users
        users_data = [
            {'username': 'admin', 'password': 'admin123', 'role': 'manager', 'first_name': 'مدیر', 'last_name': 'سیستم'},
            {'username': 'kitchen1', 'password': 'pass123', 'role': 'kitchen', 'first_name': 'علی', 'last_name': 'آشپز'},
            {'username': 'bar1', 'password': 'pass123', 'role': 'bar', 'first_name': 'مریم', 'last_name': 'بارتندر'},
            {'username': 'counter1', 'password': 'pass123', 'role': 'counter', 'first_name': 'رضا', 'last_name': 'پیشخدمت'},
            {'username': 'purchasing1', 'password': 'pass123', 'role': 'purchasing', 'first_name': 'سارا', 'last_name': 'خریدار'},
        ]

        users = {}
        for user_data in users_data:
            user, created = User.objects.get_or_create(
                username=user_data['username'],
                defaults={
                    'password': make_password(user_data['password']),
                    'role': user_data['role'],
                    'first_name': user_data['first_name'],
                    'last_name': user_data['last_name'],
                    'is_staff': user_data['role'] in ['manager', 'purchasing'],
                    'is_superuser': user_data['role'] == 'manager',
                }
            )
            users[user_data['username']] = user
            if created:
                self.stdout.write(f'Created user: {user.username} ({user.get_role_display()})')

        # Sample items for cafe
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

        # Create sample purchase requests
        requests_data = [
            {
                'user': 'kitchen1',
                'status': 'approved',
                'items': cafe_items[:4],  # First 4 items
                'days_ago': 7,
            },
            {
                'user': 'bar1',
                'status': 'pending_approval',
                'items': cafe_items[4:8],  # Next 4 items
                'days_ago': 3,
            },
            {
                'user': 'counter1',
                'status': 'purchased',
                'items': cafe_items[8:12],  # Last 4 items
                'days_ago': 10,
            },
            {
                'user': 'kitchen1',
                'status': 'rejected',
                'items': [cafe_items[0], cafe_items[2]],  # Coffee and sugar
                'days_ago': 5,
                'rejection_reason': 'موجودی کافی در انبار وجود دارد',
            },
            {
                'user': 'bar1',
                'status': 'approved',
                'items': [cafe_items[1], cafe_items[3], cafe_items[5]],  # Milk, lemon, cake
                'days_ago': 2,
            },
        ]

        for i, req_data in enumerate(requests_data):
            user = users[req_data['user']]
            created_at = timezone.now() - timedelta(days=req_data['days_ago'])

            # Create purchase request
            request = PurchaseRequest.objects.create(
                user=user,
                status=req_data['status'],
                created_at=created_at,
            )

            # Set approval info for approved/rejected requests
            if req_data['status'] in ['approved', 'rejected']:
                request.approved_by = users['purchasing1']
                request.approved_at = created_at + timedelta(hours=random.randint(1, 24))
                if req_data['status'] == 'rejected':
                    request.rejection_reason = req_data.get('rejection_reason', '')
                request.save()

            # Create purchase items
            for item_data in req_data['items']:
                PurchaseItem.objects.create(
                    request=request,
                    name=item_data['name'],
                    quantity=item_data['quantity'],
                    unit=item_data['unit'],
                    description=item_data['description'],
                )

            # Create activity logs
            ActivityLog.objects.create(
                user=user,
                request=request,
                action='created',
                description=f'درخواست خرید توسط {user.get_full_name()} ایجاد شد',
                created_at=created_at,
            )

            if req_data['status'] == 'approved':
                ActivityLog.objects.create(
                    user=users['purchasing1'],
                    request=request,
                    action='approved',
                    description='درخواست خرید تأیید شد',
                    created_at=request.approved_at,
                )
            elif req_data['status'] == 'rejected':
                ActivityLog.objects.create(
                    user=users['purchasing1'],
                    request=request,
                    action='rejected',
                    description=f'درخواست رد شد: {request.rejection_reason}',
                    created_at=request.approved_at,
                )

            self.stdout.write(f'Created request {i+1}: {request} with {len(req_data["items"])} items')

        self.stdout.write(self.style.SUCCESS('Successfully created sample data!'))
        self.stdout.write('Users created:')
        for username, user in users.items():
            self.stdout.write(f'  - {username}: {user.get_full_name()} ({user.get_role_display()}) - {username}/pass123')

        total_requests = PurchaseRequest.objects.count()
        total_items = PurchaseItem.objects.count()
        total_logs = ActivityLog.objects.count()

        self.stdout.write(f'\nSummary:')
        self.stdout.write(f'  - Total requests: {total_requests}')
        self.stdout.write(f'  - Total items: {total_items}')
        self.stdout.write(f'  - Total activity logs: {total_logs}')