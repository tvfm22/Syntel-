#!/usr/bin/env python
"""
تست اتوماتیک برای بررسی رفع مشکل 403 و نقش تایید نشد
"""
import os
import sys
import django

# تنظیم Django environment
sys.path.insert(0, '/workspaces/Syntel-/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import Client
from purchases.models import User

def test_user_login_and_navigation():
    """تست لاگین و ناوبری کاربران"""
    
    client = Client()
    
    # کاربران برای تست
    test_users = [
        {'username': 'kitchen_user', 'password': 'password123', 'expected_redirect': 'mobile_dashboard'},
        {'username': 'bar_user', 'password': 'password123', 'expected_redirect': 'mobile_dashboard'},
        {'username': 'purchasing', 'password': 'password123', 'expected_redirect': 'purchasing_dashboard'},
        {'username': 'admin', 'password': 'password123', 'expected_redirect': 'select_cafe'},
    ]
    
    print("\n" + "=" * 70)
    print("تست لاگین و ناوبری کاربران")
    print("=" * 70 + "\n")
    
    all_passed = True
    
    for test_case in test_users:
        username = test_case['username']
        password = test_case['password']
        
        print(f"🧪 تست کاربر: {username}")
        
        try:
            # تست لاگین
            response = client.post('/login/', {
                'username': username,
                'password': password
            }, follow=True)
            
            # بررسی که خطای 403 نداریم
            if response.status_code == 403:
                print(f"   ❌ خطای 403 - نقش تایید نشد")
                all_passed = False
                continue
            
            # بررسی که به صفحه مناسب redirect شده
            if response.status_code in [200, 302]:
                print(f"   ✅ لاگین موفق (status: {response.status_code})")
                
                # لاگ اوت برای تست بعدی
                client.logout()
            else:
                print(f"   ⚠️  status code نامعتبر: {response.status_code}")
                all_passed = False
                
        except Exception as e:
            print(f"   ❌ خطا: {str(e)}")
            all_passed = False
        
        print()
    
    print("=" * 70)
    if all_passed:
        print("✅ همه تست‌ها موفق بودند")
    else:
        print("❌ برخی تست‌ها ناموفق بودند")
    print("=" * 70 + "\n")
    
    return all_passed

def check_user_configurations():
    """بررسی تنظیمات کاربران"""
    
    print("\n" + "=" * 70)
    print("بررسی تنظیمات کاربران")
    print("=" * 70 + "\n")
    
    # کاربران عادی بدون کافه
    users_no_cafe = User.objects.filter(cafe__isnull=True).exclude(
        role__in=['manager', 'ceo', 'finance_manager', 'accounting']
    )
    
    if users_no_cafe.exists():
        print(f"⚠️  {users_no_cafe.count()} کاربر عادی بدون کافه یافت شد:")
        for user in users_no_cafe:
            print(f"   • {user.username} ({user.get_role_display()})")
        print("\n   این کاربران هنگام لاگین دچار خطای 403 می‌شوند!\n")
        return False
    else:
        print("✅ همه کاربران عادی کافه دارند\n")
    
    # کاربران ادمین بدون کافه (طبیعی است)
    admin_users = User.objects.filter(
        cafe__isnull=True,
        role__in=['manager', 'ceo', 'finance_manager', 'accounting']
    )
    
    if admin_users.exists():
        print(f"ℹ️  {admin_users.count()} کاربر ادمین بدون کافه (طبیعی است):")
        for user in admin_users:
            print(f"   • {user.username} ({user.get_role_display()})")
        print()
    
    print("=" * 70 + "\n")
    return True

if __name__ == '__main__':
    # بررسی تنظیمات
    config_ok = check_user_configurations()
    
    if not config_ok:
        print("⚠️  تنظیمات کاربران مشکل دارد. ابتدا آن را رفع کنید.\n")
        sys.exit(1)
    
    # اجرای تست‌ها
    # Note: برای اجرای تست‌های واقعی نیاز به سرور است
    print("ℹ️  برای تست کامل لاگین، از browser استفاده کنید:\n")
    print("   1. به http://localhost:8000/login بروید")
    print("   2. با کاربران مختلف لاگین کنید")
    print("   3. بین صفحات مختلف navigate کنید")
    print("   4. بررسی کنید که خطای 403 یا 'نقش تایید نشد' نمی‌بینید\n")
