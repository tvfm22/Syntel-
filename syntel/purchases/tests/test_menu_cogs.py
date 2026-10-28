# -*- coding: utf-8 -*-
"""Tests for the Kuker menu import and the cost-of-goods (بهای تمام شده) engine.

Ground truth comes from the workbook itself: the «بها» sheet states the
workbook's own computed cost for each Sofreh Chi dish.
"""
from decimal import Decimal

from django.conf import settings
from django.test import TestCase, Client
from django.contrib.auth import get_user_model

from purchases.models import Cafe, KukerItem, CogsReport
from purchases.services.menu_cogs import MenuGraph, compute_cogs

MENU_PATH = str(settings.MENU_WORKBOOK_PATH)

# هزینهی تولید غذا per serving, from the «بها» sheet of the workbook
EXCEL_COSTS = {
    'چلوکباب کوبیده': Decimal('31442243.41'),
    'چلو جوجه کباب ران بدون استخوان': Decimal('44288465.19'),
    'چلوکباب سلطانی': Decimal('34352879.72'),
    'چلو کباب وزیری': Decimal('48112300.57'),
}


class MenuCogsEngineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from purchases.services.menu_cogs import import_menu
        cls.summary = import_menu(MENU_PATH)
        cls.graph = MenuGraph.from_db()

    def test_import_summary(self):
        self.assertTrue(self.summary['items'] > 400)
        self.assertTrue(self.summary['dishes'] > 50)
        self.assertTrue(self.summary['lines'] > 1000)

    def test_all_stated_costs_match_workbook(self):
        bad = self.graph.validate_against_workbook()
        self.assertEqual(bad, [], f'بهای تمامشده با اکسل نمیخواند: {bad[:3]}')

    def test_dish_costs_match_workbook(self):
        for name, expected in EXCEL_COSTS.items():
            with self.subTest(dish=name):
                key = [k for k in self.graph.dishes() if self.graph.items[k]['name'] == name]
                self.assertTrue(key, f'غذای {name} در منو پیدا نشد')
                cons = self.graph.unit_consumption(key[0])
                cost = (sum((s[1] for s in cons['raw'].values()), Decimal('0'))
                        + sum((s[1] for s in cons['bad'].values()), Decimal('0')))
                self.assertTrue(
                    abs(cost - expected) / expected < Decimal('0.0001'),
                    f'{name}: computed={cost} expected={expected}')

    def test_semi_prepared_expands_to_raw_quantities(self):
        """چلوکباب کوبیده = 1 پرس: rice pan is 6 servings -> 1000/6 g rice per serving."""
        key = [k for k in self.graph.dishes() if self.graph.items[k]['name'] == 'چلوکباب کوبیده'][0]
        cons = self.graph.unit_consumption(key)
        rice = [q for (mk, unit), (q, c) in cons['raw'].items()
                if self.graph.items[mk]['name'] == 'برنج هاشمی']
        self.assertEqual(len(rice), 1)
        self.assertAlmostEqual(float(rice[0]), 1000 / 6, places=1)
        meat = [q for (mk, unit), (q, c) in cons['raw'].items()
                if self.graph.items[mk]['name'] == 'لاشه گوسفندی']
        # 300g of kubide maye (1300g batch, 1000g meat) -> 300 * 1000/1300
        self.assertAlmostEqual(float(meat[0]), 300 * 1000 / 1300, places=1)

    def test_period_totals_scale_linearly(self):
        key = [k for k in self.graph.dishes() if self.graph.items[k]['name'] == 'چلوکباب کوبیده'][0]
        one = compute_cogs({key: '1'})
        ten = compute_cogs({key: '10'})
        self.assertAlmostEqual(float(ten['total_cost']), float(one['total_cost']) * 10, places=0)

    def test_zero_sales_produces_no_rows(self):
        result = compute_cogs({})
        self.assertEqual(result['raw_rows'], [])
        self.assertEqual(result['total_cost'], Decimal('0'))


class CogsPageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from purchases.services.menu_cogs import import_menu
        import_menu(MENU_PATH)
        Cafe.objects.get_or_create(code='baba_adam', defaults={'name': 'بابا آدم'})
        User = get_user_model()
        cls.admin = User.objects.filter(username='admin').first()
        if cls.admin is None:
            cls.admin = User.objects.create_superuser('admin', 'a@a.com', 'x', role='admin')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_page_get(self):
        r = self.client.get('/baba_adam/cogs/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'بهای تمام شده')
        self.assertContains(r, 'فروش غذاها در بازه')

    def test_post_saves_report_and_shows_consumption(self):
        from purchases.services.menu_cogs import MenuGraph
        key = [k for k in MenuGraph.from_db().dishes()
               if MenuGraph.from_db().items[k]['name'] == 'چلوکباب کوبیده'][0]
        r = self.client.post('/baba_adam/cogs/', {
            'start_year': '1405', 'start_month': '7', 'start_day': '1',
            'end_year': '1405', 'end_month': '7', 'end_day': '30',
            f'qty__{key}': '50',
        })
        self.assertEqual(r.status_code, 302)
        report = CogsReport.objects.first()
        self.assertIsNotNone(report)
        r = self.client.get(f'/baba_adam/cogs/?report={report.id}')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'مصرف مواد اولیه')
        self.assertContains(r, 'برنج هاشمی')


class CogsEditorTest(TestCase):
    """فیلتر منو، ویرایش رسپی، افزودن کالا و آیتم منوی جدید."""

    @classmethod
    def setUpTestData(cls):
        from purchases.services.menu_cogs import import_menu
        import_menu(MENU_PATH)
        Cafe.objects.get_or_create(code='baba_adam', defaults={'name': 'بابا آدم'})
        User = get_user_model()
        cls.admin = User.objects.filter(username='admin').first()
        if cls.admin is None:
            cls.admin = User.objects.create_superuser('admin2', 'a2@a.com', 'x', role='admin')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_menu_filter_tabs_rendered(self):
        r = self.client.get('/baba_adam/cogs/')
        self.assertContains(r, 'cogs-menu-tab')
        self.assertContains(r, 'data-menu="سفره چی"')
        self.assertContains(r, 'همه منوها')

    def test_recipe_api_returns_recipe_and_cost(self):
        item = KukerItem.objects.get(name='چلوکباب کوبیده', kind='dish')
        r = self.client.get(f'/baba_adam/cogs/recipe/{item.id}/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertGreaterEqual(len(data['lines']), 5)
        self.assertAlmostEqual(float(data['item']['cost_per']), 31442243.41, delta=1000)
        self.assertTrue(data['materials'])

    def test_recipe_edit_recalculates_cost(self):
        import json
        dish = KukerItem.objects.create(name='غذای آزمون', key='غذایازمون', kind='dish',
                                        out_qty=1, out_unit='پرس', sheet='سفره چی', is_custom=True)
        rice = KukerItem.objects.get(name='برنج هاشمی')
        payload = {'out_qty': '1', 'out_unit': 'پرس', 'lines': [
            {'material_id': rice.id, 'qty': '200', 'unit': 'گرم', 'fee': '4200'},
        ]}
        r = self.client.post(f'/baba_adam/cogs/recipe/{dish.id}/',
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertAlmostEqual(float(r.json()['cost_per']), 840000, delta=1)
        self.assertEqual(dish.lines.count(), 1)

    def test_recipe_edit_creates_missing_material(self):
        import json
        dish = KukerItem.objects.create(name='غذای آزمون۲', key='غذایازمون2', kind='dish',
                                        out_qty=1, out_unit='پرس', sheet='سفره چی', is_custom=True)
        payload = {'out_qty': '1', 'out_unit': 'پرس', 'lines': [
            {'name': 'کالای کاملاً جدید', 'qty': '100', 'unit': 'گرم', 'fee': '500'},
        ]}
        r = self.client.post(f'/baba_adam/cogs/recipe/{dish.id}/',
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(KukerItem.objects.filter(name='کالای کاملاً جدید', is_custom=True).exists())
        self.assertAlmostEqual(float(r.json()['cost_per']), 50000, delta=1)

    def test_recipe_rejects_self_reference(self):
        import json
        dish = KukerItem.objects.create(name='غذای خودارجاع', key='غذایخودارجاع', kind='dish',
                                        out_qty=1, out_unit='پرس', sheet='سفره چی', is_custom=True)
        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': dish.id, 'qty': '1', 'unit': 'پرس', 'fee': '1'}]}
        r = self.client.post(f'/baba_adam/cogs/recipe/{dish.id}/',
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(dish.lines.count(), 0)

    def test_add_raw_item(self):
        r = self.client.post('/baba_adam/cogs/item/add/', {
            'name': 'کالای دستی', 'unit': 'گرم', 'price': '2500',
            'code': '123', 'next': '/baba_adam/cogs/'})
        self.assertEqual(r.status_code, 302)
        item = KukerItem.objects.get(name='کالای دستی')
        self.assertEqual(item.kind, 'raw')
        self.assertTrue(item.is_custom)
        self.assertEqual(float(item.price), 2500)

    def test_add_menu_item_appears_in_sales_table(self):
        r = self.client.post('/baba_adam/cogs/menu-item/add/', {
            'name': 'غذای جدید منو', 'kind': 'dish', 'sheet': 'سفره چی',
            'out_qty': '1', 'out_unit': 'پرس', 'next': '/baba_adam/cogs/'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(KukerItem.objects.filter(name='غذای جدید منو', kind='dish', is_custom=True).exists())
        r = self.client.get('/baba_adam/cogs/')
        self.assertContains(r, 'غذای جدید منو')

    def test_custom_items_survive_reimport(self):
        from purchases.services.menu_cogs import import_menu
        KukerItem.objects.create(name='کالای ماندگار', key='کالایماندگار', kind='raw',
                                 out_unit='گرم', price=100, is_custom=True)
        import_menu(MENU_PATH)
        self.assertTrue(KukerItem.objects.filter(name='کالای ماندگار', is_custom=True).exists())


class CogsEditorTest(TestCase):
    """فیلتر منو، ویرایش رسپی، افزودن کالا و آیتم منوی جدید."""

    @classmethod
    def setUpTestData(cls):
        from purchases.services.menu_cogs import import_menu
        import_menu(MENU_PATH)
        Cafe.objects.get_or_create(code='baba_adam', defaults={'name': 'بابا آدم'})
        User = get_user_model()
        cls.admin = User.objects.filter(username='admin').first()
        if cls.admin is None:
            cls.admin = User.objects.create_superuser('admin2', 'a2@a.com', 'x', role='admin')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_menu_filter_tabs_rendered(self):
        r = self.client.get('/baba_adam/cogs/')
        self.assertContains(r, 'cogs-menu-tab')
        self.assertContains(r, 'data-menu="سفره چی"')
        self.assertContains(r, 'همه منوها')

    def test_recipe_api_returns_recipe_and_cost(self):
        item = KukerItem.objects.get(name='چلوکباب کوبیده', kind='dish')
        r = self.client.get(f'/baba_adam/cogs/recipe/{item.id}/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertGreaterEqual(len(data['lines']), 5)
        self.assertAlmostEqual(float(data['item']['cost_per']), 31442243.41, delta=1000)
        self.assertTrue(data['materials'])

    def test_recipe_edit_recalculates_cost(self):
        import json
        dish = KukerItem.objects.create(name='غذای آزمون', key='غذایازمون', kind='dish',
                                        out_qty=1, out_unit='پرس', sheet='سفره چی', is_custom=True)
        rice = KukerItem.objects.get(name='برنج هاشمی')
        payload = {'out_qty': '1', 'out_unit': 'پرس', 'lines': [
            {'material_id': rice.id, 'qty': '200', 'unit': 'گرم', 'fee': '4200'},
        ]}
        r = self.client.post(f'/baba_adam/cogs/recipe/{dish.id}/',
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertAlmostEqual(float(r.json()['cost_per']), 840000, delta=1)
        self.assertEqual(dish.lines.count(), 1)

    def test_recipe_edit_creates_missing_material(self):
        import json
        dish = KukerItem.objects.create(name='غذای آزمون۲', key='غذایازمون2', kind='dish',
                                        out_qty=1, out_unit='پرس', sheet='سفره چی', is_custom=True)
        payload = {'out_qty': '1', 'out_unit': 'پرس', 'lines': [
            {'name': 'کالای کاملاً جدید', 'qty': '100', 'unit': 'گرم', 'fee': '500'},
        ]}
        r = self.client.post(f'/baba_adam/cogs/recipe/{dish.id}/',
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(KukerItem.objects.filter(name='کالای کاملاً جدید', is_custom=True).exists())
        self.assertAlmostEqual(float(r.json()['cost_per']), 50000, delta=1)

    def test_recipe_rejects_self_reference(self):
        import json
        dish = KukerItem.objects.create(name='غذای خودارجاع', key='غذایخودارجاع', kind='dish',
                                        out_qty=1, out_unit='پرس', sheet='سفره چی', is_custom=True)
        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': dish.id, 'qty': '1', 'unit': 'پرس', 'fee': '1'}]}
        r = self.client.post(f'/baba_adam/cogs/recipe/{dish.id}/',
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(dish.lines.count(), 0)

    def test_add_raw_item(self):
        r = self.client.post('/baba_adam/cogs/item/add/', {
            'name': 'کالای دستی', 'unit': 'گرم', 'price': '2500',
            'code': '123', 'next': '/baba_adam/cogs/'})
        self.assertEqual(r.status_code, 302)
        item = KukerItem.objects.get(name='کالای دستی')
        self.assertEqual(item.kind, 'raw')
        self.assertTrue(item.is_custom)
        self.assertEqual(float(item.price), 2500)

    def test_add_menu_item_appears_in_sales_table(self):
        r = self.client.post('/baba_adam/cogs/menu-item/add/', {
            'name': 'غذای جدید منو', 'kind': 'dish', 'sheet': 'سفره چی',
            'out_qty': '1', 'out_unit': 'پرس', 'next': '/baba_adam/cogs/'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(KukerItem.objects.filter(name='غذای جدید منو', kind='dish', is_custom=True).exists())
        r = self.client.get('/baba_adam/cogs/')
        self.assertContains(r, 'غذای جدید منو')

    def test_custom_items_survive_reimport(self):
        from purchases.services.menu_cogs import import_menu
        KukerItem.objects.create(name='کالای ماندگار', key='کالایماندگار', kind='raw',
                                 out_unit='گرم', price=100, is_custom=True)
        import_menu(MENU_PATH)
        self.assertTrue(KukerItem.objects.filter(name='کالای ماندگار', is_custom=True).exists())
