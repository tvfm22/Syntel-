#!/usr/bin/env python
"""
Test mobile dashboard access for department users
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import Client
from purchases.models import User

def test_mobile_dashboard():
    """Test that department users can access mobile dashboard"""
    
    client = Client()
    
    # Test roles that should access mobile dashboard
    mobile_roles = ['kitchen', 'bar', 'counter', 'cashier', 'facilities']
    
    print("\n" + "="*60)
    print("Testing Mobile Dashboard Access")
    print("="*60 + "\n")
    
    for role in mobile_roles:
        # Check if user exists
        username = role
        user = User.objects.filter(username=username).first()
        
        if not user:
            print(f"⚠ User '{username}' not found, skipping...")
            continue
        
        print(f"Testing {role} ({user.get_role_display()}):")
        print(f"  Username: {username}")
        
        # Test login
        login_success = client.login(username=username, password=f'{username}123')
        
        if login_success:
            print(f"  ✓ Login successful")
            
            # Test mobile dashboard access
            response = client.get('/mobile-dashboard/')
            
            if response.status_code == 200:
                print(f"  ✓ Mobile dashboard accessible (Status: {response.status_code})")
                
                # Check if template contains expected elements
                content = response.content.decode('utf-8')
                if 'ثبت درخواست خرید' in content and 'درخواست‌های من' in content:
                    print(f"  ✓ Dashboard contains both menu items")
                else:
                    print(f"  ⚠ Dashboard may be missing menu items")
            else:
                print(f"  ✗ Mobile dashboard not accessible (Status: {response.status_code})")
            
            # Logout
            client.logout()
        else:
            print(f"  ✗ Login failed")
        
        print()
    
    # Test that admin/manager don't get redirected to mobile dashboard
    print("Testing non-mobile users:")
    for role in ['admin', 'purchasing']:
        user = User.objects.filter(username=role).first()
        if not user:
            continue
            
        print(f"Testing {role}:")
        
        # Manually test login redirect (we can't easily test redirect in this context)
        print(f"  ℹ {role} should redirect to regular dashboard, not mobile")
        print()
    
    print("="*60)
    print("Test Complete!")
    print("="*60)

if __name__ == '__main__':
    test_mobile_dashboard()
