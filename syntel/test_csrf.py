#!/usr/bin/env python
"""
تست CSRF Token برای صفحه لاگین
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()

def test_login_csrf():
    """تست CSRF token در صفحه لاگین"""
    client = Client()
    
    # دریافت صفحه لاگین
    print("📋 دریافت صفحه لاگین...")
    response = client.get('/login/')
    
    if response.status_code == 200:
        print("✅ صفحه لاگین با موفقیت بارگیری شد")
        
        # بررسی وجود CSRF token
        if 'csrftoken' in response.cookies:
            print(f"✅ CSRF Cookie موجود است: {response.cookies['csrftoken'].value[:20]}...")
        else:
            print("⚠️ CSRF Cookie یافت نشد")
        
        # بررسی محتوای صفحه
        content = response.content.decode('utf-8')
        if 'csrf_token' in content or 'csrfmiddlewaretoken' in content:
            print("✅ CSRF token در فرم موجود است")
        else:
            print("❌ CSRF token در فرم یافت نشد")
    else:
        print(f"❌ خطا در بارگیری صفحه: {response.status_code}")
    
    # تست لاگین با یوزر admin (اگر وجود داشته باشد)
    print("\n🔐 تست لاگین...")
    try:
        admin_user = User.objects.filter(username='admin').first()
        if admin_user:
            # لاگین با csrf token
            response = client.post('/login/', {
                'username': 'admin',
                'password': 'admin',
            })
            
            if response.status_code == 302:  # Redirect after successful login
                print("✅ لاگین موفقیت‌آمیز بود")
            elif response.status_code == 403:
                print("❌ خطای CSRF 403 - توکن نامعتبر است")
            else:
                print(f"⚠️ پاسخ غیرمنتظره: {response.status_code}")
        else:
            print("⚠️ کاربر admin وجود ندارد")
    except Exception as e:
        print(f"❌ خطا در تست لاگین: {e}")
    
    print("\n✅ تست کامل شد")

if __name__ == '__main__':
    test_login_csrf()
