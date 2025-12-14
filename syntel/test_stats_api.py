import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.contrib.auth import get_user_model
from purchases.views_dashboard import DashboardStatsAPIView
from rest_framework.test import APIRequestFactory
from django.db.models import Q

User = get_user_model()

# ایجاد request تست
factory = APIRequestFactory()
request = factory.get('/api/dashboard/stats/')

# ایجاد یوزر
user, created = User.objects.get_or_create(username='testuser', is_staff=True, is_superuser=True)
request.user = user

# تست API
view = DashboardStatsAPIView.as_view()
response = view(request)

print('📊 تست API آمار داشبورد')
print('=' * 60)
print(f'Status: {response.status_code}')
print(f'Data: {response.data}')
print()

# تست با فیلتر 7 روز
request_7days = factory.get('/api/dashboard/stats/', {'days': '7'})
request_7days.user = user
response_7days = view(request_7days)

print('📊 تست با فیلتر 7 روز')
print('=' * 60)
print(f'Data: {response_7days.data}')
print()

# تست با فیلتر 30 روز
request_30days = factory.get('/api/dashboard/stats/', {'days': '30'})
request_30days.user = user
response_30days = view(request_30days)

print('📊 تست با فیلتر 30 روز')
print('=' * 60)
print(f'Data: {response_30days.data}')
