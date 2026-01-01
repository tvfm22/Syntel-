import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.contrib.auth import get_user_model
from purchases.models import PurchaseRequest, PurchaseItem
from django.utils import timezone

User = get_user_model()

# Get users by role
kitchen_user = User.objects.filter(role='kitchen').first()
bar_user = User.objects.filter(role='bar').first()
purchasing_user = User.objects.filter(role='purchasing').first()

print(f'Kitchen user: {kitchen_user}')
print(f'Bar user: {bar_user}')
print(f'Purchasing user: {purchasing_user}')

# Create sample requests for kitchen
if kitchen_user and purchasing_user:
    req1 = PurchaseRequest.objects.create(
        user=kitchen_user,
        status='purchased',
        processed_by=purchasing_user,
        processed_at=timezone.now()
    )
    PurchaseItem.objects.create(request=req1, name='گوشت چرخ کرده', quantity=10, unit='کیلو', purchased_quantity=10, supply_status='purchased')
    PurchaseItem.objects.create(request=req1, name='مرغ', quantity=15, unit='کیلو', purchased_quantity=12, supply_status='purchased')
    PurchaseItem.objects.create(request=req1, name='روغن', quantity=5, unit='لیتر', purchased_quantity=5, supply_status='from_stock')
    print(f'Created request 1: {req1.id}')

    req2 = PurchaseRequest.objects.create(
        user=kitchen_user,
        status='purchased',
        processed_by=purchasing_user,
        processed_at=timezone.now()
    )
    PurchaseItem.objects.create(request=req2, name='برنج', quantity=20, unit='کیلو', purchased_quantity=20, supply_status='purchased')
    PurchaseItem.objects.create(request=req2, name='پیاز', quantity=10, unit='کیلو', purchased_quantity=8, supply_status='purchased')
    PurchaseItem.objects.create(request=req2, name='سیب زمینی', quantity=15, unit='کیلو', purchased_quantity=0, supply_status='not_supplied')
    print(f'Created request 2: {req2.id}')
else:
    print('Kitchen user or purchasing user not found!')

# Create sample request for bar
if bar_user and purchasing_user:
    req3 = PurchaseRequest.objects.create(
        user=bar_user,
        status='purchased',
        processed_by=purchasing_user,
        processed_at=timezone.now()
    )
    PurchaseItem.objects.create(request=req3, name='آب پرتقال', quantity=20, unit='لیتر', purchased_quantity=20, supply_status='purchased')
    PurchaseItem.objects.create(request=req3, name='لیمو', quantity=5, unit='کیلو', purchased_quantity=5, supply_status='purchased')
    PurchaseItem.objects.create(request=req3, name='نعناع', quantity=2, unit='کیلو', purchased_quantity=2, supply_status='from_stock')
    print(f'Created request 3: {req3.id}')
else:
    print('Bar user or purchasing user not found!')

print('Done! Sample requests created.')
