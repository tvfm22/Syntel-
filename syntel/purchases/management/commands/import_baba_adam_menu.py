# -*- coding: utf-8 -*-
"""Import Cafe Baba Adam menu, preps, recipes, missing products, and section access."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from purchases.models import Cafe
from purchases.services.baba_adam_menu import BABA_ADAM_MENU_PATH, import_baba_adam_menu


class Command(BaseCommand):
    help = 'ایمپورت کامل رسپی‌ها، کالاهای منو و دسترسی سکشن‌های کافه باباآدم از اکسل اصلاح‌شده.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--path',
            default=str(BABA_ADAM_MENU_PATH),
            help='مسیر فایل اکسل منو (پیش‌فرض: reference/menu/baba_adam_menu_v2.xlsx)',
        )
        parser.add_argument(
            '--cafe',
            default='baba_adam',
            help='کد کافه هدف (پیش‌فرض: baba_adam)',
        )
        parser.add_argument(
            '--no-prices',
            action='store_true',
            help='عدم ورود قیمت‌های ریالی مواد اولیه',
        )

    def handle(self, *args, **options):
        wb_path = Path(options['path'])
        if not wb_path.exists():
            raise CommandError(f'فایل اکسل پیدا نشد: {wb_path}')

        cafe = Cafe.objects.filter(code=options['cafe']).first()
        if cafe is None:
            raise CommandError(f'کافه با کد «{options["cafe"]}» یافت نشد.')

        summary = import_baba_adam_menu(
            path=wb_path,
            cafe=cafe,
            apply_prices=not options['no_prices'],
        )
        self.stdout.write(self.style.SUCCESS(
            f"ایمپورت منوی {summary['cafe']} انجام شد: "
            f"{summary['dishes']} غذا، {summary['preps']} نیمه‌آماده، "
            f"{summary['recipe_lines']} سطر رسپی، "
            f"{len(summary['created_products'])} کالای جدید، "
            f"{len(summary['activated_products'])} کالای فعال‌شده، "
            f"{summary['priced_products']} قیمت ریالی ثبت‌شده، "
            f"{len(summary['warnings'])} هشدار واحد."
        ))
        for w in summary['warnings']:
            self.stdout.write(self.style.WARNING(f'  ! {w}'))
