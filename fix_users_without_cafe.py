#!/usr/bin/env python
"""
اسکریپت اختصاص کافه به کاربران بدون کافه  
برای رفع مشکل خطای 403 و نقش تایید نشد
"""
import os
import sys
import django

# تنظیم Django environment  
sys.path.insert(0, '/workspaces/Syntel-/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from purchases.models import User, Cafe

def fix_users_without_cafe():
    """اختصاص کافه لوکال به کاربران عادی که کافه ندارند"""
    
    # کاربرانی که نقش ادمین ندارند و کافه هم ندارند
    users_without_cafe = User.objects.filter(cafe__isnull=True).exclude(
        role__in=['manager', 'ceo', 'finance_manager', 'accounting']
    )
    
    if not users_without_cafe.exists():
        print("✅ همه کاربران عادی کافه دارند.")
        return
    
    # پیدا کردن کافه لوکال
    try:
        local_cafe = Cafe.objects.get(code='local')
    except Cafe.DoesNotExist:
        print("❌ کافه لوکال یافت نشد. ابتدا کافه را ایجاد کنید.")
        return
    
    print(f"\n🔧 اختصاص کافه '{local_cafe.name}' به کاربران زیر:\n")
    
    updated_count = 0
    for user in users_without_cafe:
        print(f"   • {user.username} ({user.get_role_display()})")
        user.cafe = local_cafe
        user.save()
        updated_count += 1
    
    print(f"\n✅ {updated_count} کاربر به‌روزرسانی شدند.")
    
    # نمایش کاربران ادمین که کافه ندارند (این طبیعی است)
    admin_users = User.objects.filter(
        cafe__isnull=True,
        role__in=['manager', 'ceo', 'finance_manager', 'accounting']
    )
    
    if admin_users.exists():
        print(f"\nℹ️  کاربران ادمین بدون کافه (طبیعی است):")
        for user in admin_users:
            print(f"   • {user.username} ({user.get_role_display()})")

if __name__ == '__main__':
    print("=" * 60)
    print("اسکریپت اختصاص کافه به کاربران بدون کافه")
    print("=" * 60)
    fix_users_without_cafe()
    print("\n" + "=" * 60)
