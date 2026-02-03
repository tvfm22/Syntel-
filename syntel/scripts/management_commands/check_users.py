#!/usr/bin/env python
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User

def main():
    users = User.objects.all()
    print(f"Total users in database: {users.count()}")
    print("\nUsers:")
    for user in users:
        print(f"  Username: {user.username}")
        print(f"  Role: {user.role}")
        print(f"  Active: {user.is_active}")
        print(f"  First name: {user.first_name}")
        print(f"  Last name: {user.last_name}")
        print("  ---")

    if users.count() == 0:
        print("\nNo users found. Creating a default admin user...")
        try:
            admin = User.objects.create_superuser(
                username='admin',
                password='admin123',
                first_name='مدیر',
                last_name='سیستم',
                role='manager'
            )
            print("Created admin user:")
            print("  Username: admin")
            print("  Password: admin123")
            print("  Role: manager")
        except Exception as e:
            print(f"Error creating user: {e}")

if __name__ == '__main__':
    main()