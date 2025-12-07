#!/usr/bin/env python
"""
Script to create default users for Syntel system
Run this script to create default users if none exist
"""
import os
import sys
import django

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(__file__))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User

def create_default_users():
    """Create default users for testing"""

    # Check if users already exist
    if User.objects.count() > 0:
        print("Users already exist in the database:")
        for user in User.objects.all():
            print(f"  {user.username} ({user.role}) - Active: {user.is_active}")
        return

    print("Creating default users...")

    # Create admin user
    try:
        admin = User.objects.create_user(
            username='admin',
            password='admin123',
            first_name='مدیر',
            last_name='سیستم',
            role='manager'
        )
        print("✓ Created admin user: admin / admin123")
    except Exception as e:
        print(f"✗ Error creating admin: {e}")

    # Create purchasing user
    try:
        purchasing = User.objects.create_user(
            username='purchasing',
            password='purchase123',
            first_name='مسئول',
            last_name='خرید',
            role='purchasing'
        )
        print("✓ Created purchasing user: purchasing / purchase123")
    except Exception as e:
        print(f"✗ Error creating purchasing user: {e}")

    # Create kitchen user
    try:
        kitchen = User.objects.create_user(
            username='kitchen',
            password='kitchen123',
            first_name='سرآشپز',
            last_name='آشپزخانه',
            role='kitchen'
        )
        print("✓ Created kitchen user: kitchen / kitchen123")
    except Exception as e:
        print(f"✗ Error creating kitchen user: {e}")

    # Create bar user
    try:
        bar = User.objects.create_user(
            username='bar',
            password='bar123',
            first_name='مسئول',
            last_name='بار',
            role='bar'
        )
        print("✓ Created bar user: bar / bar123")
    except Exception as e:
        print(f"✗ Error creating bar user: {e}")

    # Create counter user
    try:
        counter = User.objects.create_user(
            username='counter',
            password='counter123',
            first_name='مسئول',
            last_name='کانتر',
            role='counter'
        )
        print("✓ Created counter user: counter / counter123")
    except Exception as e:
        print(f"✗ Error creating counter user: {e}")

    print("\nDefault users created successfully!")
    print("You can now login with:")
    print("  Admin: admin / admin123")
    print("  Purchasing: purchasing / purchase123")
    print("  Kitchen: kitchen / kitchen123")
    print("  Bar: bar / bar123")
    print("  Counter: counter / counter123")

if __name__ == '__main__':
    create_default_users()