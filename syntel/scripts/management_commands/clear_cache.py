import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.core.cache import cache

print("🔄 پاک کردن کش...")
cache.clear()
print("✅ کش با موفقیت پاک شد!")
