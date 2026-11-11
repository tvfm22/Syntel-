# -*- coding: utf-8 -*-
"""Tests for page 2 of the COGS module — `cogs_manage_items`.

Covers the three blocks of the page:
  A) creating a menu dish / semi-prepared item, with an existing section or a
     section created inline, and the hand-off to the recipe editor
  B) defining a raw material (Product + KukerItem together) with category,
     usage location and base unit
  C) price entry with live-preview normalization, price-history row and cost
     snapshots — plus cafe isolation on every endpoint
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Category, Department, KukerItem, KukerItemCostHistory, KukerRecipeLine,
    MenuSection, Product, ProductPriceEntry,
)


class ItemsPageBase(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code='baba_adam', name='بابا آدم')
        self.other_cafe = Cafe.objects.create(code='local', name='لوکال')
        User = get_user_model()
        self.admin = User.objects.create_superuser('items_admin', 'i@i.com', 'x', role='admin')
        self.client = Client()
        self.client.force_login(self.admin)

        self.kitchen = MenuSection.objects.create(
            cafe=self.cafe, name='آماده سازی آشپزخانه', kind=MenuSection.KIND_PREP,
            display_order=0)
        self.bar = MenuSection.objects.create(
            cafe=self.cafe, name='آماده سازی بار', kind=MenuSection.KIND_PREP,
            display_order=1)
        self.dish_section = MenuSection.objects.create(
            cafe=self.cafe, name='منوی فرنگی', kind=MenuSection.KIND_DISH,
            display_order=10)

        self.category = Category.objects.create(
            name='لبنیات', cafe=self.cafe, requires_recipe=True)
        self.cheese = self.make_raw('پنیر', Decimal('2'), 'گرم')
        self.dish = KukerItem.objects.create(
            cafe=self.cafe, name='پاستا', key='پاستا', kind=KukerItem.KIND_DISH,
            out_qty=1, out_unit='پرس', section=self.dish_section)
        KukerRecipeLine.objects.create(item=self.dish, material=self.cheese,
                                       quantity=100, unit='گرم', fee=None)

    def make_raw(self, name, price, unit='گرم', cafe=None):
        return KukerItem.objects.create(
            cafe=cafe or self.cafe, name=name, key=name, kind=KukerItem.KIND_RAW,
            out_unit=unit, price=price, is_custom=True)

    def url(self, name, **kwargs):
        kwargs.setdefault('cafe_code', self.cafe.code)
        return reverse(name, kwargs=kwargs)


# ---------------------------------------------------------------------------
# الف) ساخت غذای منو / نیمه‌آماده / بخش
# ---------------------------------------------------------------------------
class MenuItemCreateTest(ItemsPageBase):
    def test_page_shows_both_kind_tabs_and_section_choices(self):
        html = self.client.get(self.url('cogs_manage_items')).content.decode()
        self.assertIn('غذای منو', html)
        self.assertIn('نیمه‌آماده', html)
        self.assertIn('منوی فرنگی', html)
        self.assertIn('آماده سازی آشپزخانه', html)
        self.assertIn('آماده سازی بار', html)

    def test_create_dish_with_existing_section_opens_recipe_editor(self):
        r = self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'dish', 'name': 'پاستا آلفردو',
            'section_id': self.dish_section.pk, 'out_qty': '2', 'out_unit': 'پرس',
        })
        self.assertEqual(r.status_code, 302)
        self.assertIn(f'recipe=', r['Location'])

        item = KukerItem.objects.get(cafe=self.cafe, name='پاستا آلفردو')
        self.assertEqual(item.kind, KukerItem.KIND_DISH)
        self.assertEqual(item.section_id, self.dish_section.pk)
        self.assertEqual(item.out_qty, Decimal('2'))
        self.assertTrue(item.is_custom)
        # ریدایرکت باید همان قلم را برای باز شدن خودکار ویرایشگر ببرد
        self.assertIn(f'recipe={item.pk}', r['Location'])

    def test_redirected_page_auto_opens_the_recipe_editor(self):
        r = self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'dish', 'name': 'برگر مخصوص',
            'section_id': self.dish_section.pk, 'out_qty': '1', 'out_unit': 'پرس',
        })
        item = KukerItem.objects.get(cafe=self.cafe, name='برگر مخصوص')
        html = self.client.get(r['Location']).content.decode()
        self.assertIn('recipeModal', html)
        self.assertIn(f'openRecipe({item.pk})', html)

    def test_create_prep_in_kitchen_section(self):
        r = self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'prep', 'name': 'سس مخصوص',
            'section_id': self.kitchen.pk, 'out_qty': '1', 'out_unit': 'پرس',
        })
        self.assertEqual(r.status_code, 302)
        item = KukerItem.objects.get(cafe=self.cafe, name='سس مخصوص')
        self.assertEqual(item.kind, KukerItem.KIND_PREP)
        self.assertEqual(item.section_id, self.kitchen.pk)

    def test_create_prep_in_bar_section(self):
        self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'prep', 'name': 'شربت لیمو',
            'section_id': self.bar.pk, 'out_qty': '1', 'out_unit': 'میل',
        })
        item = KukerItem.objects.get(cafe=self.cafe, name='شربت لیمو')
        self.assertEqual(item.section_id, self.bar.pk)

    def test_prep_cannot_use_a_dish_section(self):
        r = self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'prep', 'name': 'سس گوجه',
            'section_id': self.dish_section.pk, 'out_qty': '1', 'out_unit': 'گرم',
        })
        self.assertEqual(r.status_code, 302)
        self.assertFalse(KukerItem.objects.filter(cafe=self.cafe, name='سس گوجه').exists())

    def test_prep_without_section_falls_back_to_a_fixed_prep_section(self):
        self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'prep', 'name': 'آب لیمو',
            'out_qty': '1', 'out_unit': 'میل',
        })
        item = KukerItem.objects.get(cafe=self.cafe, name='آب لیمو')
        self.assertIn(item.section_id, (self.kitchen.pk, self.bar.pk))

    def test_inline_section_is_persisted_and_can_be_used(self):
        r = self.client.post(self.url('cogs_section_save'), {
            'name': 'دسرها', 'kind': 'dish',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        section_id = r.json()['section']['id']

        r2 = self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'dish', 'name': 'کیک شکلاتی',
            'section_id': section_id, 'out_qty': '1', 'out_unit': 'پرس',
        })
        self.assertEqual(r2.status_code, 302)
        item = KukerItem.objects.get(cafe=self.cafe, name='کیک شکلاتی')
        self.assertEqual(item.section_id, section_id)
        self.assertEqual(MenuSection.objects.get(pk=section_id).name, 'دسرها')

    def test_create_by_section_name_builds_the_section(self):
        self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'dish', 'name': 'سالاد سزار',
            'section_name': 'سالادها', 'out_qty': '1', 'out_unit': 'پرس',
        })
        item = KukerItem.objects.get(cafe=self.cafe, name='سالاد سزار')
        self.assertEqual(item.section.name, 'سالادها')
        self.assertEqual(item.section.kind, MenuSection.KIND_DISH)

    def test_duplicate_item_name_is_rejected(self):
        self.client.post(self.url('cogs_manage_items'), {
            'action': 'item', 'kind': 'dish', 'name': 'پاستا',
            'section_id': self.dish_section.pk, 'out_qty': '1', 'out_unit': 'پرس',
        })
        self.assertEqual(KukerItem.objects.filter(cafe=self.cafe, name='پاستا').count(), 1)

    def test_json_endpoint_returns_id_and_recipe_url(self):
        r = self.client.post(self.url('cogs_menu_item_create'), {
            'kind': 'prep', 'name': 'خمیر پیتزا', 'section_id': self.kitchen.pk,
            'out_qty': '10', 'out_unit': 'عدد',
        })
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['item']['kind'], 'prep')
        self.assertEqual(data['item']['section_id'], self.kitchen.pk)
        self.assertIn(f"/cogs/recipe/{data['item']['id']}/", data['recipe_url'])

    def test_json_endpoint_reports_errors_with_400(self):
        r = self.client.post(self.url('cogs_menu_item_create'), {
            'kind': 'dish', 'name': '', 'out_qty': '1', 'out_unit': 'پرس'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])

    def test_item_cannot_be_created_in_another_cafe(self):
        foreign_section = MenuSection.objects.create(
            cafe=self.other_cafe, name='بخش دیگر', kind=MenuSection.KIND_DISH)
        self.client.post('/local/cogs/menu-item/create/', {
            'kind': 'dish', 'name': 'غذای لوکال', 'section_id': foreign_section.pk,
            'out_qty': '1', 'out_unit': 'پرس',
        })
        self.assertTrue(KukerItem.objects.filter(cafe=self.other_cafe, name='غذای لوکال').exists())
        self.assertFalse(KukerItem.objects.filter(cafe=self.cafe, name='غذای لوکال').exists())

        # بخش کافهٔ دیگر نباید در کافهٔ فعال قابل استفاده باشد
        r = self.client.post(self.url('cogs_menu_item_create'), {
            'kind': 'dish', 'name': 'غذای دزدی', 'section_id': foreign_section.pk,
            'out_qty': '1', 'out_unit': 'پرس',
        })
        self.assertEqual(r.status_code, 400)


# ---------------------------------------------------------------------------
# ب) تعریف ماده اولیه (Product + KukerItem) و جدول
# ---------------------------------------------------------------------------
class RawMaterialCreateTest(ItemsPageBase):
    def test_creates_product_and_kuker_item_together(self):
        r = self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'هویج', 'category_id': self.category.pk, 'usage_location': 'general',
            'base_unit': 'گرم', 'price': '250000', 'price_unit': 'kg',
        })
        self.assertEqual(r.status_code, 200, r.content)

        item = KukerItem.objects.get(cafe=self.cafe, name='هویج')
        product = Product.objects.get(cafe=self.cafe, name='هویج')
        self.assertEqual(item.kind, KukerItem.KIND_RAW)
        self.assertEqual(item.product_id, product.pk)
        self.assertEqual(item.price, Decimal('250'))
        self.assertEqual(item.out_unit, 'گرم')
        self.assertEqual(product.base_unit_price, Decimal('250'))
        self.assertEqual(product.smallest_unit, 'گرم')
        self.assertEqual(product.category_id, self.category.pk)

    def test_inline_category_is_created_and_attached(self):
        self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'زیره', 'category_name': 'ادویه‌ها', 'base_unit': 'گرم',
        })
        item = KukerItem.objects.get(cafe=self.cafe, name='زیره')
        self.assertEqual(item.product.category.name, 'ادویه‌ها')
        self.assertTrue(Category.objects.filter(cafe=self.cafe, name='ادویه‌ها').exists())

    def test_usage_location_accepts_a_cafe_department(self):
        Department.objects.create(cafe=self.cafe, name='آشپزخانه')
        self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'نمک', 'base_unit': 'گرم', 'usage_location': 'آشپزخانه',
        })
        product = Product.objects.get(cafe=self.cafe, name='نمک')
        self.assertEqual(product.usage_location, 'آشپزخانه')

    def test_unknown_usage_location_falls_back_to_general(self):
        self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'فلفل', 'base_unit': 'گرم', 'usage_location': 'بخش ناموجود',
        })
        self.assertEqual(Product.objects.get(cafe=self.cafe, name='فلفل').usage_location,
                         'general')

    def test_package_price_is_normalized_on_creation(self):
        self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'برنج', 'base_unit': 'گرم', 'price': '1750000',
            'price_unit': 'package', 'package_size': '5', 'package_unit': 'کیلوگرم',
        })
        item = KukerItem.objects.get(cafe=self.cafe, name='برنج')
        self.assertEqual(item.price, Decimal('350.0000'))
        self.assertEqual(item.out_unit, 'گرم')
        entry = ProductPriceEntry.objects.get(product=item.product)
        self.assertEqual(entry.entered_amount, Decimal('1750000'))
        self.assertEqual(entry.package_size, Decimal('5'))
        self.assertEqual(entry.price_per_smallest_unit, Decimal('350.0000'))

    def test_duplicate_name_is_rejected_with_400(self):
        r = self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'پنیر', 'base_unit': 'گرم'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(KukerItem.objects.filter(cafe=self.cafe, name='پنیر').count(), 1)

    def test_base_unit_and_purchase_unit_must_match(self):
        r = self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'دستمال', 'base_unit': 'عدد', 'price': '250000', 'price_unit': 'kg'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(KukerItem.objects.filter(cafe=self.cafe, name='دستمال').exists())

    def test_creation_is_scoped_to_the_active_cafe(self):
        self.client.post('/local/cogs/raw-material/create/', {
            'name': 'کره', 'base_unit': 'گرم', 'price': '3', 'price_unit': 'gram'})
        self.assertTrue(Product.objects.filter(cafe=self.other_cafe, name='کره').exists())
        self.assertFalse(Product.objects.filter(cafe=self.cafe, name='کره').exists())

    def test_table_shows_category_usage_and_recipe_usage_count(self):
        self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'خامه', 'category_id': self.category.pk, 'base_unit': 'گرم',
            'usage_location': 'general',
        })
        html = self.client.get(self.url('cogs_manage_items')).content.decode()
        self.assertIn('خامه', html)
        self.assertIn('لبنیات', html)
        self.assertIn('محل استفاده', html)
        self.assertIn('واحد پایه', html)
        # پنیر در یک رسپی مصرف شده است
        self.assertIn('۱ رسپی', html)

    def test_category_filter_narrows_the_table(self):
        self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'خامه', 'category_id': self.category.pk, 'base_unit': 'گرم'})
        html = self.client.get(
            self.url('cogs_manage_items') + f'?cat={self.category.pk}').content.decode()
        self.assertIn('خامه', html)
        self.assertNotIn('پنیر', html)

    def test_search_matches_code_and_category(self):
        self.client.post(self.url('cogs_raw_material_create'), {
            'name': 'خامه', 'category_id': self.category.pk, 'base_unit': 'گرم'})
        html = self.client.get(self.url('cogs_manage_items') + '?q=لبنیات').content.decode()
        self.assertIn('خامه', html)
        self.assertNotIn('پنیر', html)

    def test_other_cafe_materials_never_appear(self):
        self.make_raw('زعفران', Decimal('900'), 'گرم', cafe=self.other_cafe)
        html = self.client.get(self.url('cogs_manage_items')).content.decode()
        self.assertNotIn('زعفران', html)


# ---------------------------------------------------------------------------
# ج) ورود قیمت با پیش‌نمایش و تاریخچه
# ---------------------------------------------------------------------------
class PriceEntryTest(ItemsPageBase):
    def test_preview_normalizes_kilogram_to_gram(self):
        r = self.client.get(self.url('cogs_price_preview'), {
            'price': '350000', 'unit': 'kg'})
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(Decimal(data['stored_price']), Decimal('350'))
        self.assertEqual(data['smallest_unit'], 'گرم')
        self.assertIn('۳۵۰,۰۰۰ تومان برای ۱ کیلوگرم', data['entered_text'])
        self.assertIn('۳۵۰ تومان به ازای هر ۱ گرم', data['stored_text'])

    def test_preview_handles_custom_package_size(self):
        r = self.client.get(self.url('cogs_price_preview'), {
            'price': '1750000', 'unit': 'package',
            'package_size': '5', 'package_unit': 'کیلوگرم'})
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(Decimal(data['stored_price']), Decimal('350'))
        self.assertIn('بستهٔ ۵ کیلوگرم', data['entered_text'])

    def test_preview_of_pieces_needs_no_division(self):
        data = self.client.get(self.url('cogs_price_preview'), {
            'price': '30000', 'unit': 'piece'}).json()
        self.assertEqual(Decimal(data['stored_price']), Decimal('30000'))
        self.assertEqual(data['smallest_unit'], 'عدد')
        self.assertFalse(data['needs_division'])

    def test_preview_rejects_garbage(self):
        r = self.client.get(self.url('cogs_price_preview'), {'price': 'abc', 'unit': 'kg'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])

    def test_preview_requires_a_cafe(self):
        # بدون کافهٔ فعال هیچ محاسبه‌ای انجام نمی‌شود: میدل‌ور به کافهٔ مجاز
        # ریدایرکت می‌کند و مسیر ناشناخته هرگز به ویو نمی‌رسد.
        r = self.client.get('/no_such_cafe/cogs/price/preview/?price=1&unit=kg')
        self.assertIn(r.status_code, (302, 404))

    def test_price_entry_updates_both_sides_and_records_history(self):
        r = self.client.post(self.url('cogs_price_entry'), {
            'item_id': self.cheese.pk, 'price': '350000', 'unit': 'kg'})
        self.assertEqual(r.status_code, 200, r.content)

        self.cheese.refresh_from_db()
        product = Product.objects.get(cafe=self.cafe, name='پنیر')
        self.assertEqual(self.cheese.price, Decimal('350.0000'))
        self.assertEqual(self.cheese.out_unit, 'گرم')
        self.assertEqual(product.base_unit_price, Decimal('350.0000'))
        self.assertEqual(product.smallest_unit, 'گرم')

        entry = ProductPriceEntry.objects.get(product=product)
        self.assertEqual(entry.entered_amount, Decimal('350000'))
        self.assertEqual(entry.entered_unit, 'کیلوگرم')
        self.assertEqual(entry.price_per_smallest_unit, Decimal('350.0000'))
        self.assertEqual(entry.smallest_unit, 'گرم')
        self.assertEqual(entry.cafe, self.cafe)
        self.assertEqual(entry.recorded_by, self.admin)

    def test_price_entry_records_a_cost_snapshot_for_dependent_dishes(self):
        self.client.post(self.url('cogs_price_entry'), {
            'item_id': self.cheese.pk, 'price': '350000', 'unit': 'kg'})
        latest = (KukerItemCostHistory.objects.filter(item=self.dish)
                  .order_by('-recorded_at', '-id').first())
        self.assertIsNotNone(latest)
        # ۱۰۰ گرم × ۳۵۰ = ۳۵٬۰۰۰
        self.assertEqual(latest.cost_per_unit, Decimal('35000.0000'))

    def test_price_entry_creates_a_product_when_missing(self):
        loose = self.make_raw('زردچوبه', None, 'گرم')
        self.assertIsNone(loose.product_id)
        r = self.client.post(self.url('cogs_price_entry'), {
            'item_id': loose.pk, 'price': '2.5', 'unit': 'gram'})
        self.assertEqual(r.status_code, 200, r.content)
        loose.refresh_from_db()
        self.assertIsNotNone(loose.product_id)
        self.assertEqual(loose.product.base_unit_price, Decimal('2.5'))

    def test_price_entry_rejects_unit_of_a_different_family(self):
        pieces = self.make_raw('دستمال', Decimal('3000'), 'عدد')
        r = self.client.post(self.url('cogs_price_entry'), {
            'item_id': pieces.pk, 'price': '250000', 'unit': 'kg'})
        self.assertEqual(r.status_code, 400)
        pieces.refresh_from_db()
        self.assertEqual(pieces.price, Decimal('3000'))

    def test_price_entry_cannot_touch_another_cafe(self):
        foreign = self.make_raw('زعفران', Decimal('900'), 'گرم', cafe=self.other_cafe)
        r = self.client.post(self.url('cogs_price_entry'), {
            'item_id': foreign.pk, 'price': '1', 'unit': 'gram'})
        self.assertEqual(r.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.price, Decimal('900'))

    def test_page_post_action_price_still_works_without_javascript(self):
        r = self.client.post(self.url('cogs_manage_items'), {
            'action': 'price', 'item_id': self.cheese.pk, 'price': '5', 'unit': 'gram'})
        self.assertEqual(r.status_code, 302)
        self.cheese.refresh_from_db()
        self.assertEqual(self.cheese.price, Decimal('5.0000'))
        self.assertTrue(ProductPriceEntry.objects.filter(
            product__name='پنیر', cafe=self.cafe).exists())

    def test_price_modal_lists_the_six_purchase_units(self):
        html = self.client.get(self.url('cogs_manage_items')).content.decode()
        for label in ('۱ کیلوگرم', '۱ گرم', '۱ لیتر', '۱ میل', '۱ عدد', 'بستهٔ دلخواه'):
            with self.subTest(label=label):
                self.assertIn(label, html)


# ---------------------------------------------------------------------------
# ناوبری و رندر
# ---------------------------------------------------------------------------
class PageRenderTest(ItemsPageBase):
    def test_page_renders_for_the_active_cafe(self):
        r = self.client.get(self.url('cogs_manage_items'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('ماده اولیه جدید', html)
        self.assertIn('وارد کردن قیمت', html)

    def test_recipe_editor_modal_is_shared_with_page_one(self):
        page1 = self.client.get(self.url('cogs_menu_list')).content.decode()
        page2 = self.client.get(self.url('cogs_manage_items')).content.decode()
        for html in (page1, page2):
            self.assertIn('id="recipeModal"', html)
            self.assertIn('window.CogsRecipe', html)

    def test_empty_page_shows_an_empty_state(self):
        Cafe.objects.create(code='empty_items', name='خالی')
        r = self.client.get('/empty_items/cogs/items/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('هنوز ماده اولیه‌ای تعریف نشده است', r.content.decode())
