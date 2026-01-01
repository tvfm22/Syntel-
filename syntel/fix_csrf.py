#!/usr/bin/env python
"""
اسکریپت رفع خودکار مشکلات CSRF
"""
import os
import sys
import django
import shutil
from pathlib import Path

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.core.management import call_command
from django.conf import settings

def fix_csrf_issues():
    """رفع خودکار مشکلات CSRF"""
    
    print("🔧 شروع رفع مشکلات CSRF...")
    print("=" * 60)
    
    # 1. بررسی تنظیمات
    print("\n1️⃣ بررسی تنظیمات Django...")
    
    # بررسی DEBUG
    print(f"   DEBUG: {settings.DEBUG}")
    if not settings.DEBUG:
        print("   ⚠️ توصیه: DEBUG را True قرار دهید برای عیب‌یابی")
    
    # بررسی ALLOWED_HOSTS
    print(f"   ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
    
    # بررسی CSRF_TRUSTED_ORIGINS
    if hasattr(settings, 'CSRF_TRUSTED_ORIGINS'):
        print(f"   ✅ CSRF_TRUSTED_ORIGINS: {settings.CSRF_TRUSTED_ORIGINS}")
    else:
        print("   ❌ CSRF_TRUSTED_ORIGINS تنظیم نشده است")
    
    # بررسی Middleware
    print(f"\n2️⃣ بررسی Middleware...")
    csrf_middleware = 'django.middleware.csrf.CsrfViewMiddleware'
    if csrf_middleware in settings.MIDDLEWARE:
        print(f"   ✅ {csrf_middleware} موجود است")
    else:
        print(f"   ❌ {csrf_middleware} موجود نیست!")
    
    # 3. پاک کردن Sessions
    print(f"\n3️⃣ پاک کردن Sessions منقضی شده...")
    try:
        call_command('clearsessions')
        print("   ✅ Sessions پاک شدند")
    except Exception as e:
        print(f"   ❌ خطا: {e}")
    
    # 4. پاک کردن __pycache__
    print(f"\n4️⃣ پاک کردن __pycache__ ...")
    base_dir = Path(settings.BASE_DIR)
    pycache_dirs = list(base_dir.rglob('__pycache__'))
    
    for pycache_dir in pycache_dirs:
        try:
            shutil.rmtree(pycache_dir)
        except Exception as e:
            pass
    
    print(f"   ✅ {len(pycache_dirs)} پوشه __pycache__ پاک شد")
    
    # 5. بررسی فایل login.html
    print(f"\n5️⃣ بررسی فایل login.html...")
    login_template = base_dir / 'frontend' / 'templates' / 'login.html'
    
    if login_template.exists():
        with open(login_template, 'r', encoding='utf-8') as f:
            content = f.read()
            if '{% csrf_token %}' in content:
                print("   ✅ csrf_token در login.html موجود است")
            else:
                print("   ❌ csrf_token در login.html یافت نشد!")
    else:
        print("   ❌ فایل login.html یافت نشد")
    
    # 6. راهنمای بعدی
    print("\n" + "=" * 60)
    print("✅ عملیات تکمیل شد!")
    print("\n📋 مراحل بعدی:")
    print("   1. سرور را ریستارت کنید:")
    print("      python manage.py runserver")
    print("\n   2. مرورگر را در حالت Incognito باز کنید")
    print("\n   3. Cache و کوکی‌های مرورگر را پاک کنید:")
    print("      Ctrl + Shift + Delete")
    print("\n   4. دوباره لاگین کنید")
    print("\n📖 برای راهنمای کامل، CSRF_FIX_GUIDE.md را ببینید")
    print("=" * 60)

if __name__ == '__main__':
    fix_csrf_issues()
