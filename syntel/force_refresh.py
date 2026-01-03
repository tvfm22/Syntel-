#!/usr/bin/env python
"""
Force browser refresh by clearing Django cache
"""
import os
import django
import sys

# Setup Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.core.cache import cache

# Clear all cache
cache.clear()
print("✓ کش Django پاک شد")
print("✓ حالا صفحه را با Ctrl+Shift+R رفرش کنید")
print("✓ یا مرورگر را ببندید و دوباره باز کنید")
