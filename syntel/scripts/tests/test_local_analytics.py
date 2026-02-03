# -*- coding: utf-8 -*-
import os, sys, django

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()

print("Testing Analytics API with 'local' cafe...")

client = Client()
user = User.objects.filter(is_active=True).first()
client.force_login(user)

print(f"User: {user.username}")
print(f"Testing: /local/api/analytics/departments/expenses/?period=monthly\n")

response = client.get('/local/api/analytics/departments/expenses/?period=monthly')
print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(f"Success! Departments: {len(data['departments'])}\n")
    for dept in data['departments']:
        print(f"  - {dept['name']}: {dept['current_expense']:,.0f} Toman")
        print(f"    Growth: {dept['growth_rate']}%")
else:
    print(f"Failed: {response.content.decode()[:300]}")
