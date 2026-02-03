#!/usr/bin/env python
"""
اصلاح سیستم کاربران برای چند کافه‌ای

منطق جدید:
- کاربران عادی: باید cafe داشته باشند و فقط به همان کافه دسترسی دارند
- کاربران ادمین (manager, finance_manager, accounting, ceo):
  - اگر cafe=None: می‌توانند بین کافه‌ها انتخاب کنند (سوپر ادمین)
  - اگر cafe مشخص: فقط به همان کافه دسترسی دارند

کاربران موجود همه به کافه لوکال تعلق دارند.
برای کاربران سوپر ادمین جدید، cafe=None تنظیم می‌شود.
"""

import os
import sys
import django

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User, Cafe

def show_users():
    """نمایش وضعیت فعلی کاربران"""
    print("\n" + "=" * 60)
    print("وضعیت فعلی کاربران")
    print("=" * 60)
    
    admin_roles = ['manager', 'finance_manager', 'accounting', 'ceo']
    
    for user in User.objects.all().order_by('role', 'username'):
        cafe_name = user.cafe.name if user.cafe else "بدون کافه (سوپر ادمین)"
        is_admin = "✓" if user.role in admin_roles else ""
        print(f"  {user.username:<20} | نقش: {user.role:<15} | کافه: {cafe_name:<25} | ادمین: {is_admin}")

def fix_users():
    """اصلاح کاربران - همه کاربران موجود به کافه لوکال تعلق دارند"""
    local_cafe = Cafe.objects.filter(code='local').first()
    
    if not local_cafe:
        print("❌ کافه لوکال یافت نشد!")
        return
    
    # همه کاربران که cafe ندارند را به لوکال اختصاص بده
    users_without_cafe = User.objects.filter(cafe__isnull=True)
    count = users_without_cafe.count()
    
    if count > 0:
        users_without_cafe.update(cafe=local_cafe)
        print(f"✅ {count} کاربر به کافه لوکال اختصاص داده شد")
    else:
        print("✅ همه کاربران قبلاً کافه دارند")

def create_super_admin():
    """ایجاد یک کاربر سوپر ادمین برای هر دو کافه"""
    # بررسی وجود سوپر ادمین
    super_admin = User.objects.filter(username='superadmin').first()
    
    if super_admin:
        super_admin.cafe = None  # دسترسی به هر دو کافه
        super_admin.save()
        print(f"✅ کاربر superadmin به‌روزرسانی شد (دسترسی به هر دو کافه)")
    else:
        # ایجاد سوپر ادمین جدید
        super_admin = User.objects.create_user(
            username='superadmin',
            password='superadmin123',
            role='ceo',
            first_name='سوپر',
            last_name='ادمین',
            cafe=None  # دسترسی به هر دو کافه
        )
        print(f"✅ کاربر superadmin ایجاد شد (رمز: superadmin123)")

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("       اصلاح سیستم کاربران")
    print("=" * 60)
    
    show_users()
    
    print("\n" + "=" * 60)
    print("اصلاحات...")
    print("=" * 60)
    
    fix_users()
    create_super_admin()
    
    show_users()
    
    print("\n" + "=" * 60)
    print("راهنما:")
    print("=" * 60)
    print("- کاربران با کافه مشخص: فقط به همان کافه دسترسی دارند")
    print("- کاربر superadmin (بدون کافه): صفحه انتخاب کافه می‌بیند")
    print("- فقط manager و ceo می‌توانند در حین کار کافه را عوض کنند")
    print("=" * 60)
