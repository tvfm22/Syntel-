#!/usr/bin/env python
import os
import django
import sys

# Setup Django
sys.path.append('/workspaces/Syntel-/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User

def reset_all_passwords():
    users = User.objects.all()
    new_password = '123456'

    updated_count = 0
    for user in users:
        user.set_password(new_password)
        user.save()
        updated_count += 1
        print(f"رمز عبور کاربر {user.username} تغییر یافت.")

    print(f"\nکل کاربران: {users.count()}")
    print(f"کاربران بروزرسانی شده: {updated_count}")
    print(f"رمز عبور جدید: {new_password}")

if __name__ == '__main__':
    reset_all_passwords()