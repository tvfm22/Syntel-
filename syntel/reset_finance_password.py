#!/usr/bin/env python
"""
Script to reset the finance_manager password
"""
import os
import sys
import django

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(__file__))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User

def reset_finance_password():
    """Reset password for finance_manager user"""
    
    try:
        user = User.objects.get(username='finance_manager')
        new_password = 'finance123'
        user.set_password(new_password)
        user.save()
        
        print("✓ Password reset successful!")
        print(f"\nUsername: finance_manager")
        print(f"New Password: {new_password}")
        print(f"Role: {user.role}")
        print(f"Name: {user.first_name} {user.last_name}")
        
    except User.DoesNotExist:
        print("✗ User 'finance_manager' not found in database!")
    except Exception as e:
        print(f"✗ Error: {e}")

if __name__ == '__main__':
    reset_finance_password()
