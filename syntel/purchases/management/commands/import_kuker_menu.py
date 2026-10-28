# -*- coding: utf-8 -*-
"""Import the Kuker menu workbook (منو کوکر.xlsx) into the menu/recipe tables.

Usage:
    python manage.py import_kuker_menu [path-to-xlsx]

The command refuses to save anything if the workbook's own stated
"قیمت تمام شده" values do not match the computed ones.
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from purchases.services.menu_cogs import import_menu

DEFAULT_PATH = str(settings.MENU_WORKBOOK_PATH)


class Command(BaseCommand):
    help = 'خواندن فایل منوی کوکر و ساختن اقلام/رسپی‌ها برای صفحه بهای تمام شده'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=DEFAULT_PATH,
                            help='مسیر فایل اکسل منو (پیش‌فرض: reference/menu/منو کوکر.xlsx)')

    def handle(self, *args, **options):
        path = options['path']
        if not os.path.exists(path):
            self.stderr.write(self.style.ERROR(f'فایل پیدا نشد: {path}'))
            return
        try:
            summary = import_menu(path)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'خطا در واردکردن منو: {exc}'))
            raise
        self.stdout.write(self.style.SUCCESS(
            'منو وارد شد: {items} قلم ({dishes} غذای فروشنی، {raws} ماده اولیه) و {lines} سطر رسپی'
            .format(**summary)))
