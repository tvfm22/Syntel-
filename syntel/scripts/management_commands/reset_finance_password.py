#!/usr/bin/env python
"""
Script to reset the finance_manager password (requires --confirm-destructive and password).
"""
import argparse
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')


def reset_finance_password(new_password: str, confirm_destructive: bool = False):
    """Reset password for finance_manager user safely."""
    if not confirm_destructive:
        raise RuntimeError("Password reset requires --confirm-destructive.")
    if not new_password or len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters long.")

    django.setup()
    from django.db import transaction
    from purchases.models import User

    with transaction.atomic():
        user = User.objects.get(username='finance_manager')
        user.set_password(new_password)
        user.save(update_fields=['password'])

    print("✓ Password reset successful for user 'finance_manager'.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Reset finance_manager password.')
    parser.add_argument(
        '--password',
        default=os.environ.get('SYNTEL_FINANCE_PASSWORD', ''),
        help='New password >= 8 chars (or set SYNTEL_FINANCE_PASSWORD)',
    )
    parser.add_argument(
        '--confirm-destructive',
        action='store_true',
        help='Required flag to confirm resetting password.',
    )
    args = parser.parse_args()
    reset_finance_password(new_password=args.password, confirm_destructive=args.confirm_destructive)
