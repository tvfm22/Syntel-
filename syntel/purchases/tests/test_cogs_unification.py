# -*- coding: utf-8 -*-
"""Tests for the unified Product / COGS model.

Covers the four things the refactor promised:
  1. smallest-unit price normalization (kg→گرم, litre→میل, no-op otherwise)
  2. Product ↔ KukerItem(kind='raw') synchronization inside one cafe
  3. cafe isolation of menu items, recipes and reports
  4. dish/prep cost history snapshots
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase

from purchases.models import (
    Cafe, Category, KukerItem, KukerItemCostHistory, KukerRecipeLine, MenuSection, Product,
)
from purchases.services.menu_cogs import (
    MenuGraph, ensure_default_menu_sections, record_cost_snapshots_for_cafe,
)
from purchases.services.product_sync import sync_product, sync_raw_material
from purchases.services.units import (
    convert_quantity_to_smallest_unit,
    normalize_to_smallest_unit,
)


# ---------------------------------------------------------------------------
# 1) smallest-unit normalization
# ---------------------------------------------------------------------------
class NormalizeToSmallestUnitTest(SimpleTestCase):
    def test_kilogram_variants_become_grams(self):
        for unit in ('کیلوگرم', 'کیلو', 'kg', 'KG'):
            with self.subTest(unit=unit):
                price, smallest = normalize_to_smallest_unit(250000, unit)
                self.assertEqual(smallest, 'گرم')
                self.assertEqual(price, Decimal('250'))

    def test_litre_becomes_mil(self):
        for unit in ('لیتر', 'L', 'l'):
            with self.subTest(unit=unit):
                price, smallest = normalize_to_smallest_unit(90000, unit)
                self.assertEqual(smallest, 'میل')
                self.assertEqual(price, Decimal('90'))

    def test_smallest_units_stay_untouched(self):
        cases = {
            'گرم': Decimal('4200'),
            'میل': Decimal('15'),
            'عدد': Decimal('30000'),
            'پرس': Decimal('150000'),
            'بسته': Decimal('12000'),
        }
        for unit, expected in cases.items():
            with self.subTest(unit=unit):
                price, smallest = normalize_to_smallest_unit(expected, unit)
                self.assertEqual(price, expected)
                self.assertEqual(smallest, unit)

    def test_unknown_and_empty_units_pass_through(self):
        self.assertEqual(normalize_to_smallest_unit(5000, 'کارتن'), (Decimal('5000'), 'کارتن'))
        self.assertEqual(normalize_to_smallest_unit(5000, ''), (Decimal('5000'), ''))

    def test_missing_price_keeps_target_unit(self):
        price, smallest = normalize_to_smallest_unit(None, 'کیلوگرم')
        self.assertIsNone(price)
        self.assertEqual(smallest, 'گرم')

    def test_quantity_conversion(self):
        self.assertEqual(convert_quantity_to_smallest_unit(Decimal('0.3'), 'کیلوگرم'),
                         (Decimal('300.0'), 'گرم'))
        self.assertEqual(convert_quantity_to_smallest_unit(Decimal('1.5'), 'لیتر'),
                         (Decimal('1500.0'), 'میل'))
        self.assertEqual(convert_quantity_to_smallest_unit(Decimal('4'), 'عدد'),
                         (Decimal('4'), 'عدد'))


# ---------------------------------------------------------------------------
# base fixtures
# ---------------------------------------------------------------------------
class CogsBaseTest(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code='baba_adam', name='بابا آدم')
        self.other_cafe = Cafe.objects.create(code='local', name='لوکال')

    def make_raw(self, name, price, unit='گرم', cafe=None):
        return KukerItem.objects.create(
            cafe=cafe or self.cafe, name=name, key=name, kind=KukerItem.KIND_RAW,
            out_unit=unit, price=price, is_custom=True)


# ---------------------------------------------------------------------------
# 2) Product ↔ KukerItem sync
# ---------------------------------------------------------------------------
class ProductKukerSyncTest(CogsBaseTest):
    def test_sync_raw_material_creates_product_with_normalized_price(self):
        item = self.make_raw('هویج', 250000, unit='کیلوگرم')
        item, product = sync_raw_material(item, price=250000, unit='کیلوگرم')

        self.assertEqual(item.price, Decimal('250'))
        self.assertEqual(item.out_unit, 'گرم')
        self.assertEqual(product.cafe, self.cafe)
        self.assertEqual(product.base_unit_price, Decimal('250'))
        self.assertEqual(product.smallest_unit, 'گرم')
        self.assertEqual(item.product_id, product.pk)

    def test_sync_raw_material_reuses_existing_product(self):
        item = self.make_raw('پیاز', 100)
        existing = Product.objects.create(cafe=self.cafe, name='پیاز')
        item, product = sync_raw_material(item, price=120, unit='گرم')
        self.assertEqual(product.pk, existing.pk)
        self.assertEqual(Product.objects.filter(cafe=self.cafe, name='پیاز').count(), 1)

    def test_sync_product_pushes_price_into_raw_item(self):
        product = Product.objects.create(cafe=self.cafe, name='برنج هاشمی')
        item = sync_product(product, price=1.5, unit='کیلوگرم')

        self.assertIsNotNone(item)
        self.assertEqual(item.kind, KukerItem.KIND_RAW)
        self.assertEqual(item.cafe, self.cafe)
        self.assertEqual(item.price, Decimal('0.0015'))
        self.assertEqual(item.out_unit, 'گرم')
        self.assertEqual(item.product_id, product.pk)
        self.assertEqual(product.base_unit_price, Decimal('0.0015'))

    def test_sync_is_idempotent(self):
        product = Product.objects.create(cafe=self.cafe, name='روغن')
        first = sync_product(product, price=90000, unit='لیتر')
        second = sync_product(product, price=90000, unit='لیتر')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(KukerItem.objects.filter(cafe=self.cafe, name='روغن').count(), 1)
        self.assertEqual(second.price, Decimal('90'))
        self.assertEqual(second.out_unit, 'میل')

    def test_sync_never_crosses_cafes(self):
        product_a = Product.objects.create(cafe=self.cafe, name='شکر')
        product_b = Product.objects.create(cafe=self.other_cafe, name='شکر')
        item_a = sync_product(product_a, price=40, unit='گرم')
        item_b = sync_product(product_b, price=60, unit='گرم')
        self.assertEqual(item_a.cafe, self.cafe)
        self.assertEqual(item_b.cafe, self.other_cafe)
        self.assertNotEqual(item_a.pk, item_b.pk)
        self.assertEqual(item_a.price, Decimal('40'))
        self.assertEqual(item_b.price, Decimal('60'))

    def test_recipe_cost_uses_normalized_price_without_fee(self):
        """0.5 کیلوگرم from a 2-per-gram material costs 1000, not 1."""
        raw = self.make_raw('خامه', 2, unit='گرم')
        dish = KukerItem.objects.create(cafe=self.cafe, name='دسر آزمون', key='دسرآزمون',
                                        kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس',
                                        is_custom=True)
        KukerRecipeLine.objects.create(item=dish, material=raw, quantity=Decimal('0.5'),
                                       unit='کیلوگرم', fee=None)
        graph = MenuGraph.from_db(cafe=self.cafe)
        self.assertEqual(graph.computed_unit_cost(str(dish.pk)), Decimal('1000'))
        self.assertEqual(graph.unit_cost(str(dish.pk)), Decimal('1000'))


class ProductViewSyncTest(CogsBaseTest):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.admin = User.objects.create_superuser('cogs_admin', 'a@a.com', 'x', role='admin')
        self.client = Client()
        self.client.force_login(self.admin)

    def test_cogs_item_add_stores_smallest_unit_and_creates_product(self):
        r = self.client.post('/baba_adam/cogs/item/add/', {
            'name': 'گوشت چرخ‌کرده', 'unit': 'کیلوگرم', 'price': '1200000',
            'next': '/baba_adam/cogs/'})
        self.assertEqual(r.status_code, 302)
        item = KukerItem.objects.get(cafe=self.cafe, name='گوشت چرخ‌کرده')
        self.assertEqual(item.out_unit, 'گرم')
        self.assertEqual(item.price, Decimal('1200'))
        product = Product.objects.get(cafe=self.cafe, name='گوشت چرخ‌کرده')
        self.assertEqual(product.base_unit_price, Decimal('1200'))
        self.assertEqual(product.smallest_unit, 'گرم')
        self.assertEqual(item.product_id, product.pk)

    def test_product_update_syncs_price_to_raw_item(self):
        product = Product.objects.create(cafe=self.cafe, name='آرد', is_complete=True)
        item = sync_product(product, price=30, unit='گرم')

        r = self.client.post(f'/baba_adam/products/{product.pk}/update/', {
            'name': 'آرد',
            'base_unit_price': '55000',
            'price_unit': 'کیلوگرم',
        })
        self.assertEqual(r.status_code, 302)

        product.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(product.base_unit_price, Decimal('55'))
        self.assertEqual(product.smallest_unit, 'گرم')
        self.assertEqual(item.price, Decimal('55'))
        self.assertEqual(item.out_unit, 'گرم')

    def test_product_create_with_price_creates_raw_item(self):
        category = Category.objects.create(name='مواد غذایی', cafe=self.cafe,
                                           requires_recipe=True)
        r = self.client.post('/baba_adam/products/create/', {
            'name': 'زعفران',
            'category': str(category.pk),
            'usage_location': 'general',
            'base_unit_price': '80000000',
            'price_unit': 'کیلوگرم',
        })
        self.assertEqual(r.status_code, 302)
        product = Product.objects.get(cafe=self.cafe, name='زعفران')
        self.assertEqual(product.base_unit_price, Decimal('80000'))
        item = KukerItem.objects.get(cafe=self.cafe, product=product)
        self.assertEqual(item.kind, KukerItem.KIND_RAW)
        self.assertEqual(item.price, Decimal('80000'))
        self.assertEqual(item.out_unit, 'گرم')

    def test_price_change_from_product_management_records_history(self):
        """تغییر قیمت از صفحه محصولات هم باید بهای غذا را در تاریخچه ثبت کند."""
        raw = self.make_raw('کره', 2, unit='گرم')
        raw, product = sync_raw_material(raw)
        dish = KukerItem.objects.create(cafe=self.cafe, name='نان تست', key='نانتست',
                                        kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')
        KukerRecipeLine.objects.create(item=dish, material=raw, quantity=100,
                                       unit='گرم', fee=None)
        record_cost_snapshots_for_cafe(self.cafe)
        self.assertEqual(
            KukerItemCostHistory.objects.get(item=dish).cost_per_unit, Decimal('200'))

        r = self.client.post(f'/baba_adam/products/{product.pk}/update/', {
            'name': 'کره', 'base_unit_price': '3', 'price_unit': 'گرم'})
        self.assertEqual(r.status_code, 302)

        latest = KukerItemCostHistory.objects.filter(item=dish).order_by('-id').first()
        self.assertEqual(latest.cost_per_unit, Decimal('300'))
        self.assertEqual(KukerItemCostHistory.objects.filter(item=dish).count(), 2)

    def test_saving_without_price_change_adds_no_history(self):
        raw = self.make_raw('کره', 2, unit='گرم')
        raw, product = sync_raw_material(raw)
        dish = KukerItem.objects.create(cafe=self.cafe, name='نان تست۲', key='نانتست۲',
                                        kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')
        KukerRecipeLine.objects.create(item=dish, material=raw, quantity=100,
                                       unit='گرم', fee=None)
        record_cost_snapshots_for_cafe(self.cafe)

        self.client.post(f'/baba_adam/products/{product.pk}/update/', {
            'name': 'کره', 'base_unit_price': '2', 'price_unit': 'گرم'})
        self.assertEqual(KukerItemCostHistory.objects.filter(item=dish).count(), 1)


# ---------------------------------------------------------------------------
# 3) cafe isolation
# ---------------------------------------------------------------------------
class CafeIsolationTest(CogsBaseTest):
    def test_same_key_allowed_in_two_cafes(self):
        a = KukerItem.objects.create(cafe=self.cafe, name='شیر', key='شیر',
                                     kind=KukerItem.KIND_RAW, price=Decimal('5'), out_unit='میل')
        b = KukerItem.objects.create(cafe=self.other_cafe, name='شیر', key='شیر',
                                     kind=KukerItem.KIND_RAW, price=Decimal('9'), out_unit='میل')
        self.assertNotEqual(a.pk, b.pk)
        self.assertEqual(KukerItem.objects.filter(key='شیر').count(), 2)

    def test_menu_graph_is_scoped_per_cafe(self):
        self.make_raw('شیر', 5, unit='میل')
        self.make_raw('شیر', 9, unit='میل', cafe=self.other_cafe)
        self.make_raw('نان', 2, unit='عدد', cafe=self.other_cafe)

        graph_a = MenuGraph.from_db(cafe=self.cafe)
        graph_b = MenuGraph.from_db(cafe=self.other_cafe)

        self.assertEqual(len(graph_a.items), 1)
        self.assertEqual(len(graph_b.items), 2)
        self.assertEqual(list(graph_a.items.values())[0]['price'], Decimal('5'))
        prices_b = sorted(it['price'] for it in graph_b.items.values())
        self.assertEqual(prices_b, [Decimal('2'), Decimal('9')])

    def test_recipe_lines_do_not_leak_between_cafes(self):
        raw_a = self.make_raw('کره', 3, unit='گرم')
        dish_a = KukerItem.objects.create(cafe=self.cafe, name='املت', key='املت',
                                          kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')
        KukerRecipeLine.objects.create(item=dish_a, material=raw_a, quantity=10,
                                       unit='گرم', fee=None)

        graph_b = MenuGraph.from_db(cafe=self.other_cafe)
        self.assertEqual(graph_b.items, {})
        self.assertEqual(graph_b.dishes(), [])

    def test_importing_one_cafe_keeps_the_other_menu(self):
        from django.conf import settings
        from purchases.services.menu_cogs import import_menu
        import_menu(str(settings.MENU_WORKBOOK_PATH), cafe=self.cafe)
        other_count = KukerItem.objects.filter(cafe=self.cafe).count()
        self.assertGreater(other_count, 0)

        import_menu(str(settings.MENU_WORKBOOK_PATH), cafe=self.other_cafe)
        self.assertEqual(KukerItem.objects.filter(cafe=self.cafe).count(), other_count)
        self.assertGreater(KukerItem.objects.filter(cafe=self.other_cafe).count(), 0)

    def test_default_prep_sections_seeded_per_cafe(self):
        ensure_default_menu_sections(self.cafe)
        ensure_default_menu_sections(self.other_cafe)
        for cafe in (self.cafe, self.other_cafe):
            names = set(MenuSection.objects.filter(cafe=cafe, kind=MenuSection.KIND_PREP)
                        .values_list('name', flat=True))
            self.assertEqual(names, set(MenuSection.DEFAULT_PREP_SECTIONS))

    def test_cogs_report_page_is_cafe_scoped(self):
        User = get_user_model()
        admin = User.objects.create_superuser('cafe_admin', 'b@b.com', 'x', role='admin')
        client = Client()
        client.force_login(admin)
        r = client.get('/local/cogs/consumption/')
        self.assertEqual(r.status_code, 200)
        # کافه بدون منو → پیام «منویی وارد نشده» به‌جای منوی کافه دیگر
        self.assertContains(r, 'هنوز منویی برای این کافه وارد نشده است')


# ---------------------------------------------------------------------------
# 4) cost history snapshots
# ---------------------------------------------------------------------------
class CostHistoryTest(CogsBaseTest):
    def setUp(self):
        super().setUp()
        self.raw = self.make_raw('پنیر', 2, unit='گرم')
        self.prep = KukerItem.objects.create(cafe=self.cafe, name='سس پنیر', key='سسپنیر',
                                             kind=KukerItem.KIND_PREP, out_qty=1, out_unit='پرس')
        KukerRecipeLine.objects.create(item=self.prep, material=self.raw, quantity=100,
                                       unit='گرم', fee=None)
        self.dish = KukerItem.objects.create(cafe=self.cafe, name='پاستا', key='پاستا',
                                             kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')
        KukerRecipeLine.objects.create(item=self.dish, material=self.prep, quantity=1,
                                       unit='پرس', fee=None)

    def test_first_snapshot_records_every_dish_and_prep(self):
        result = record_cost_snapshots_for_cafe(self.cafe)
        self.assertEqual(result['checked'], 2)
        self.assertEqual(result['created'], 2)
        dish_cost = KukerItemCostHistory.objects.get(item=self.dish).cost_per_unit
        prep_cost = KukerItemCostHistory.objects.get(item=self.prep).cost_per_unit
        self.assertEqual(prep_cost, Decimal('200'))
        self.assertEqual(dish_cost, Decimal('200'))

    def test_unchanged_cost_creates_no_new_snapshot(self):
        record_cost_snapshots_for_cafe(self.cafe)
        result = record_cost_snapshots_for_cafe(self.cafe)
        self.assertEqual(result['created'], 0)
        self.assertEqual(KukerItemCostHistory.objects.count(), 2)

    def test_price_change_creates_new_snapshot(self):
        record_cost_snapshots_for_cafe(self.cafe)
        self.raw.price = Decimal('3')
        self.raw.save(update_fields=['price'])

        result = record_cost_snapshots_for_cafe(self.cafe, note='قیمت پنیر عوض شد')
        self.assertEqual(result['created'], 2)
        latest = (KukerItemCostHistory.objects.filter(item=self.dish)
                  .order_by('-recorded_at', '-id').first())
        self.assertEqual(latest.cost_per_unit, Decimal('300'))
        self.assertEqual(latest.note, 'قیمت پنیر عوض شد')
        # تاریخچه بازنویسی نمی‌شود — ردیف قبلی هم می‌ماند
        self.assertEqual(KukerItemCostHistory.objects.filter(item=self.dish).count(), 2)

    def test_history_is_cafe_scoped(self):
        other_raw = self.make_raw('پنیر', 50, unit='گرم', cafe=self.other_cafe)
        other_dish = KukerItem.objects.create(cafe=self.other_cafe, name='پاستا', key='پاستا',
                                              kind=KukerItem.KIND_DISH, out_qty=1,
                                              out_unit='پرس')
        KukerRecipeLine.objects.create(item=other_dish, material=other_raw, quantity=10,
                                       unit='گرم', fee=None)

        record_cost_snapshots_for_cafe(self.cafe)
        self.assertFalse(KukerItemCostHistory.objects.filter(item=other_dish).exists())

        record_cost_snapshots_for_cafe(self.other_cafe)
        self.assertTrue(KukerItemCostHistory.objects.filter(item=other_dish).exists())

    def test_recipe_edit_endpoint_records_history(self):
        User = get_user_model()
        admin = User.objects.create_superuser('hist_admin', 'c@c.com', 'x', role='admin')
        client = Client()
        client.force_login(admin)
        import json
        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': self.raw.pk, 'qty': '50', 'unit': 'گرم',
                              'fee': '2'}]}
        r = client.post(f'/baba_adam/cogs/recipe/{self.dish.pk}/',
                        data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(KukerItemCostHistory.objects.filter(item=self.dish).exists())

    def test_cogs_page_exposes_clickable_cost_and_chart_data(self):
        """بهای هر قلم در لیست کلیک‌پذیر است و نقطه‌های نمودار از تاریخچه می‌آید."""
        User = get_user_model()
        admin = User.objects.create_superuser('panel_admin', 'f@f.com', 'x', role='admin')
        client = Client()
        client.force_login(admin)
        record_cost_snapshots_for_cafe(self.cafe)

        html = client.get('/baba_adam/cogs/').content.decode()
        self.assertIn(f'data-cost="{self.dish.pk}"', html)
        self.assertIn('سس پنیر', html)

        data = client.get(f'/baba_adam/cogs/item/{self.dish.pk}/cost-history/').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['latest'], '200.0000')

    def test_reimport_keeps_item_ids_and_cost_history(self):
        """ایمپورت مجدد نباید شناسه اقلام و تاریخچه بهایشان را از بین ببرد."""
        from django.conf import settings
        from purchases.services.menu_cogs import import_menu
        import_menu(str(settings.MENU_WORKBOOK_PATH), cafe=self.cafe)
        item = KukerItem.objects.get(cafe=self.cafe, name='چلوکباب کوبیده', kind='dish')
        item_pk = item.pk
        history_before = KukerItemCostHistory.objects.filter(item_id=item_pk).count()
        self.assertGreater(history_before, 0)

        import_menu(str(settings.MENU_WORKBOOK_PATH), cafe=self.cafe)

        self.assertTrue(KukerItem.objects.filter(pk=item_pk).exists())
        self.assertEqual(
            KukerItemCostHistory.objects.filter(item_id=item_pk).count(), history_before)


class CategoryRequiresRecipeTest(CogsBaseTest):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.admin = User.objects.create_superuser('cat_admin', 'd@d.com', 'x', role='admin')
        self.client = Client()
        self.client.force_login(self.admin)

    def test_create_category_with_requires_recipe(self):
        r = self.client.post('/baba_adam/categories/create/', {
            'name': 'نوشیدنی‌ها',
            'description': 'نوشیدنی سرد و گرم',
            'requires_recipe': 'on',
        })
        self.assertEqual(r.status_code, 302)
        category = Category.objects.get(cafe=self.cafe, name='نوشیدنی‌ها')
        self.assertTrue(category.requires_recipe)

    def test_create_category_without_flag(self):
        self.client.post('/baba_adam/categories/create/', {'name': 'لوازم اداری'})
        category = Category.objects.get(cafe=self.cafe, name='لوازم اداری')
        self.assertFalse(category.requires_recipe)

    def test_update_category_toggles_flag(self):
        category = Category.objects.create(name='لبنیات', cafe=self.cafe)
        r = self.client.post(f'/baba_adam/categories/{category.pk}/update/', {
            'name': 'لبنیات',
            'requires_recipe': 'on',
        })
        self.assertEqual(r.status_code, 302)
        category.refresh_from_db()
        self.assertTrue(category.requires_recipe)

        self.client.post(f'/baba_adam/categories/{category.pk}/update/', {'name': 'لبنیات'})
        category.refresh_from_db()
        self.assertFalse(category.requires_recipe)

    def test_category_list_shows_recipe_badge(self):
        Category.objects.create(name='مواد غذایی', cafe=self.cafe, requires_recipe=True)
        r = self.client.get('/baba_adam/categories/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'recipe-badge')


class ProductListUnifiedFieldsTest(CogsBaseTest):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        admin = User.objects.create_superuser('plist_admin', 'e@e.com', 'x', role='admin')
        self.client = Client()
        self.client.force_login(admin)

    def test_list_shows_price_and_recipe_usage(self):
        product = Product.objects.create(cafe=self.cafe, name='خامه', base_unit_price=Decimal('2.5'),
                                         smallest_unit='گرم')
        item = sync_product(product, price=Decimal('2.5'), unit='گرم')
        dish = KukerItem.objects.create(cafe=self.cafe, name='کیک', key='کیک',
                                        kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')
        KukerRecipeLine.objects.create(item=dish, material=item, quantity=10, unit='گرم')

        r = self.client.get('/baba_adam/products/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'قیمت واحد')
        self.assertContains(r, 'خامه')
        self.assertContains(r, 'p-recipe is-used')

    def test_pricing_filter(self):
        Product.objects.create(cafe=self.cafe, name='قیمت‌دار', base_unit_price=Decimal('10'))
        Product.objects.create(cafe=self.cafe, name='بی‌قیمت')
        r = self.client.get('/baba_adam/products/?pricing=priced')
        self.assertContains(r, 'قیمت‌دار')
        self.assertNotContains(r, 'بی‌قیمت')
