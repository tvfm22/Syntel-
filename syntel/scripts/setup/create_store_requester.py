#!/usr/bin/env python
"""
اسکریپت ایجاد درخواست‌کننده انبار
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import Requester

def create_store_requester():
    """ایجاد درخواست‌کننده انبار"""
    store_requester, created = Requester.objects.get_or_create(
        name='store',
        defaults={'name': 'store'}
    )
    
    if created:
        print("✅ درخواست‌کننده انبار با موفقیت ایجاد شد")
    else:
        print("ℹ️  درخواست‌کننده انبار قبلاً وجود داشت")
    
    # نمایش تمام درخواست‌کنندگان
    print("\n📋 لیست درخواست‌کنندگان:")
    for req in Requester.objects.all():
        print(f"  - {req.get_name_display()} ({req.name})")

if __name__ == '__main__':
    create_store_requester()
