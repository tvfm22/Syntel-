#!/usr/bin/env python
"""
اصلاح سیستم کاربران برای چند کافه‌ای (نیازمند --confirm-destructive)

قواعد حفاظتی:
- کاربران `is_superuser=True` و مدیران کل (role در ['ceo', 'admin'] با cafe=None)
  هرگز به کافه لوکال محدود نمی‌شوند و `cafe=None` خود را حفظ می‌کنند.
"""
import argparse
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')


def show_users():
    from purchases.models import User

    print("\n" + "=" * 60)
    print("وضعیت فعلی کاربران")
    print("=" * 60)
    admin_roles = ['admin', 'manager', 'finance_manager', 'accounting', 'ceo']
    for user in User.objects.all().order_by('role', 'username'):
        cafe_name = user.cafe.name if user.cafe else "بدون کافه (سراسری)"
        is_admin = "✓" if (user.is_superuser or user.role in admin_roles) else ""
        print(f"  {user.username:<20} | نقش: {user.role:<15} | کافه: {cafe_name:<25} | ادمین: {is_admin}")


def fix_users(confirm_destructive: bool = False):
    """اختصاص کاربران غیرسراسریِ بدون کافه به کافه لوکال بدون تغییر سوپرادمین‌ها و مدیرعامل کل."""
    if not confirm_destructive:
        raise RuntimeError("This script modifies user cafe assignments; pass --confirm-destructive.")

    from django.db import transaction
    from purchases.models import Cafe, User

    local_cafe = Cafe.objects.filter(code='local').first()
    if not local_cafe:
        print("❌ کافه لوکال یافت نشد!")
        return 0

    with transaction.atomic():
        users_without_cafe = (
            User.objects.filter(cafe__isnull=True, is_superuser=False)
            .exclude(role__in=['ceo', 'admin'])
        )
        count = users_without_cafe.count()
        if count > 0:
            users_without_cafe.update(cafe=local_cafe)
            print(f"✅ {count} کاربر به کافه لوکال اختصاص داده شد (سوپرادمین‌ها و مدیرعامل کل حفظ شدند)")
        else:
            print("✅ هیچ کاربر نیازمند اصلاح یافت نشد")
        return count


def create_super_admin(password: str, confirm_destructive: bool = False):
    """ایجاد/تنظیم کاربر superadmin با رمز معتبر (از --password یا SYNTEL_SEED_PASSWORD)."""
    if not confirm_destructive:
        raise RuntimeError("Creating/updating superadmin requires --confirm-destructive.")

    from django.db import transaction
    from purchases.models import User

    with transaction.atomic():
        super_admin = User.objects.filter(username='superadmin').first()
        if super_admin:
            if super_admin.cafe is not None:
                super_admin.cafe = None
                super_admin.save(update_fields=['cafe'])
            print("✅ کاربر superadmin با دسترسی سراسری حفظ/به‌روزرسانی شد")
        elif password and len(password) >= 8:
            User.objects.create_user(
                username='superadmin',
                password=password,
                role='ceo',
                first_name='سوپر',
                last_name='ادمین',
                cafe=None,
            )
            print("✅ کاربر superadmin ایجاد شد")
        else:
            print("ℹ️ برای ایجاد کاربر جدید superadmin مقدار --password (>= 8 کاراکتر) را بدهید.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Fix user cafe assignments safely.')
    parser.add_argument(
        '--confirm-destructive',
        action='store_true',
        help='Required flag to confirm modifying user records.',
    )
    parser.add_argument(
        '--password',
        default=os.environ.get('SYNTEL_SEED_PASSWORD', ''),
        help='Password for creating superadmin if missing.',
    )
    args = parser.parse_args()

    django.setup()
    show_users()
    fix_users(confirm_destructive=args.confirm_destructive)
    create_super_admin(password=args.password, confirm_destructive=args.confirm_destructive)
    show_users()
