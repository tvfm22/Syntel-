#!/usr/bin/env python
"""
Script to create department users (Kitchen, Bar, Counter, Cashier, Facilities)
Run this script to create users for each department
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

def create_department_users():
    """Create users for all departments"""
    
    print("Creating department users...")
    
    departments = [
        {
            'username': 'kitchen',
            'password': 'kitchen123',
            'first_name': 'سرآشپز',
            'last_name': 'آشپزخانه',
            'role': 'kitchen'
        },
        {
            'username': 'bar',
            'password': 'bar123',
            'first_name': 'مسئول',
            'last_name': 'بار',
            'role': 'bar'
        },
        {
            'username': 'counter',
            'password': 'counter123',
            'first_name': 'مسئول',
            'last_name': 'کانتر',
            'role': 'counter'
        },
        {
            'username': 'cashier',
            'password': 'cashier123',
            'first_name': 'مسئول',
            'last_name': 'صندوق',
            'role': 'cashier'
        },
        {
            'username': 'facilities',
            'password': 'facilities123',
            'first_name': 'مسئول',
            'last_name': 'تاسیسات',
            'role': 'facilities'
        }
    ]
    
    created_users = []
    existing_users = []
    
    for dept in departments:
        if User.objects.filter(username=dept['username']).exists():
            existing_users.append(dept['username'])
            print(f"⚠ User '{dept['username']}' already exists")
        else:
            try:
                User.objects.create_user(
                    username=dept['username'],
                    password=dept['password'],
                    first_name=dept['first_name'],
                    last_name=dept['last_name'],
                    role=dept['role']
                )
                created_users.append(dept['username'])
                print(f"✓ Created {dept['role']} user: {dept['username']} / {dept['password']}")
            except Exception as e:
                print(f"✗ Error creating {dept['username']}: {e}")
    
    print("\n" + "="*50)
    if created_users:
        print(f"✓ Created {len(created_users)} new user(s): {', '.join(created_users)}")
    if existing_users:
        print(f"⚠ {len(existing_users)} user(s) already existed: {', '.join(existing_users)}")
    
    if created_users:
        print("\nNew user credentials:")
        for dept in departments:
            if dept['username'] in created_users:
                print(f"  {dept['role']}: {dept['username']} / {dept['password']}")

if __name__ == '__main__':
    create_department_users()
