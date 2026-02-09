#!/usr/bin/env python
import os
import django
import sys

# Setup Django
sys.path.append('/workspaces/Syntel-/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User

def list_existing_users():
    users = User.objects.all().order_by('username')
    if not users:
        print("هیچ کاربری موجود نیست.")
        return

    print("لیست کاربران موجود:")
    print("-" * 50)
    for user in users:
        cafe_name = user.cafe.name if user.cafe else 'بدون کافه'
        print(f"نام کاربری: {user.username}")
        print(f"نام کامل: {user.get_full_name()}")
        print(f"نقش: {user.get_role_display()}")
        print(f"کافه: {cafe_name}")
        print(f"ایمیل: {user.email}")
        print(f"فعال: {'بله' if user.is_active else 'خیر'}")
        print(f"ادمین: {'بله' if user.is_staff else 'خیر'}")
        print(f"سوپر ادمین: {'بله' if user.is_superuser else 'خیر'}")
        print("-" * 50)

if __name__ == '__main__':
    list_existing_users()