import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import RequestFactory
from purchases.views_analytics import api_invoice_subcategories
from django.contrib.auth import get_user_model

User = get_user_model()

# ایجاد یک request فیک
factory = RequestFactory()
request = factory.get('/api/invoices/subcategories/?category_id=1')

# اضافه کردن user
user = User.objects.filter(is_active=True).first()
if user:
    request.user = user
    print(f"User: {user.username}")
    
    # صدا زدن API
    response = api_invoice_subcategories(request)
    print(f"Status: {response.status_code}")
    print(f"Data: {response.data}")
else:
    print("No active user found")
