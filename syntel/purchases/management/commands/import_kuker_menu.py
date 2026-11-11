# -*- coding: utf-8 -*-
"""Import the Kuker menu workbook (منو کوکر.xlsx) into the menu/recipe tables.

Usage:
    python manage.py import_kuker_menu [path-to-xlsx] [--cafe CODE]

The command refuses to save anything if the workbook's own stated
"قیمت تمام شده" values do not match the computed ones.
Each cafe keeps its own isolated copy of the menu; without --cafe the
workbook goes to «baba_adam» (or the first cafe).
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from purchases.models import Cafe
from purchases.services.menu_cogs import import_menu

DEFAULT_PATH = str(settings.MENU_WORKBOOK_PATH)


class Command(BaseCommand):
    help = 'خواندن فایل منوی کوکر و ساختن اقلام/رسپی‌ها برای صفحه بهای تمام شده'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=DEFAULT_PATH,
                            help='مسیر فایل اکسل منو (پیش‌فرض: reference/menu/منو کوکر.xlsx)')
        parser.add_argument('--cafe', dest='cafe', default=None,
                            help='کد کافه مقصد (پیش‌فرض: baba_adam یا اولین کافه)')

    def handle(self, *args, **options):
        path = options['path']
        if not os.path.exists(path):
            self.stderr.write(self.style.ERROR(f'فایل پیدا نشد: {path}'))
            return
        cafe = None
        code = options.get('cafe')
        if code:
            cafe = Cafe.objects.filter(code=code).first()
            if cafe is None:
                self.stderr.write(self.style.ERROR(f'کافه‌ای با کد «{code}» پیدا نشد.'))
                return
        try:
            summary = import_menu(path, cafe=cafe)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'خطا در وارد‌کردن منو: {exc}'))
            raise
        self.stdout.write(self.style.SUCCESS(
            'منو برای کافه «{cafe}» وارد شد: {items} قلم ({dishes} غذای فروشنی، {raws} ماده اولیه) '
            'و {lines} سطر رسپی'.format(**summary)))
