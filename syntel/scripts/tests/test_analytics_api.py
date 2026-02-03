#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test Analytics APIs
"""
import os
import sys
import django

# Setup Django
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from purchases.models import Cafe

User = get_user_model()

print("=" * 60)
print("Test Analytics APIs")
print("=" * 60)

# ایجاد client
client = Client()

# Find a user
try:
    user = User.objects.filter(is_active=True).first()
    if not user:
        print("No active user found!")
        sys.exit(1)
    print(f"\nUser found: {user.username}")
except Exception as e:
    print(f"Error finding user: {e}")
    sys.exit(1)

# Find cafe
try:
    cafe = Cafe.objects.first()
    if not cafe:
        print("No cafe found!")
        sys.exit(1)
    print(f"Cafe found: {cafe.code}")
except Exception as e:
    print(f"Error finding cafe: {e}")
    sys.exit(1)

# Login
login_success = client.force_login(user)
print(f"Login successful")

# Test APIs
apis = [
    f'/{cafe.code}/api/analytics/departments/expenses/?period=monthly',
]

print(f"\n{'='*60}")
print("Testing API Endpoints")
print(f"{'='*60}\n")

for api in apis:
    print(f"Testing: {api}")
    try:
        response = client.get(api)
        print(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   Success!")
            if 'departments' in data:
                print(f"   Departments count: {len(data['departments'])}")
                for dept in data['departments'][:3]:
                    print(f"      - {dept['name']}: {dept['current_expense']:,.0f}")
        else:
            print(f"   Failed: {response.content.decode()[:200]}")
    except Exception as e:
        print(f"   Error: {str(e)}")
    print()

print("=" * 60)
print("Test completed!")
print("=" * 60)
