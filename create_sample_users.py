#!/usr/bin/env python
import os
import django
import sys

# Setup Django
sys.path.append('/workspaces/Syntel-/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User, Cafe

def create_sample_users():
    # Create a sample cafe if it doesn't exist
    cafe, created = Cafe.objects.get_or_create(
        code='sample',
        defaults={
            'name': 'کافه نمونه',
            'address': 'آدرس نمونه',
            'phone': '123456789'
        }
    )
    if created:
        print("کافه نمونه ایجاد شد.")
    else:
        print("کافه نمونه موجود است.")

    # Roles and their details
    roles = [
        ('kitchen', 'kitchen_user', 'کاربر آشپزخانه'),
        ('bar', 'bar_user', 'کاربر بار'),
        ('counter', 'counter_user', 'کاربر کانتر'),
        ('cashier', 'cashier_user', 'کاربر صندوق'),
        ('facilities', 'facilities_user', 'کاربر تاسیسات'),
        ('purchasing', 'purchasing_user', 'کاربر خرید'),
        ('manager', 'manager_user', 'کاربر مدیر'),
        ('accounting', 'accounting_user', 'کاربر حسابداری'),
        ('finance_manager', 'finance_manager_user', 'کاربر مدیر مالی'),
        ('ceo', 'ceo_user', 'کاربر مدیرعامل'),
    ]

    created_users = []

    for role, username, full_name in roles:
        # Check if user exists
        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            print(f"کاربر {username} موجود است.")
        else:
            # Create user
            user = User.objects.create_user(
                username=username,
                password='password123',  # Simple password for demo
                first_name=full_name,
                role=role
            )
            # Set cafe based on role
            if role in ['manager', 'ceo']:
                user.cafe = None  # Can switch cafes
            else:
                user.cafe = cafe
            user.save()
            print(f"کاربر {username} ایجاد شد.")
        
        created_users.append(user)

    print("\nلیست کاربران نمونه:")
    for user in created_users:
        cafe_name = user.cafe.name if user.cafe else 'بدون کافه (سوپر ادمین)'
        print(f"- {user.username}: {user.get_role_display()} - کافه: {cafe_name}")

if __name__ == '__main__':
    create_sample_users()