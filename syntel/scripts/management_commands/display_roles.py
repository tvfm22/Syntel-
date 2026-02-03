#!/usr/bin/env python
"""
Script to display available user roles
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User

def display_roles():
    """Display all available user roles"""
    
    print("\n" + "="*60)
    print("نقش‌های کاربری موجود در سیستم")
    print("="*60 + "\n")
    
    print("لیست نقش‌ها:")
    for role_code, role_name in User.ROLE_CHOICES:
        print(f"  ✓ {role_name} ({role_code})")
    
    print("\n" + "="*60)
    print("دسترسی‌ها:")
    print("="*60 + "\n")
    
    print("درخواست خرید:")
    print("  ✓ آشپزخانه (kitchen)")
    print("  ✓ بار (bar)")
    print("  ✓ کانتر (counter)")
    print("  ✓ صندوق (cashier)")
    print("  ✓ تاسیسات (facilities)")
    print("  ✓ خرید (purchasing)")
    print("  ✓ مدیر (manager)")
    
    print("\nمدیریت درخواست‌ها:")
    print("  ✓ خرید (purchasing)")
    print("  ✓ مدیر (manager)")
    
    print("\nمدیریت کاربران:")
    print("  ✓ مدیر (manager)")
    
    print("\n" + "="*60)

if __name__ == '__main__':
    display_roles()
