# -*- coding: utf-8 -*-
"""Integration tests for Baba Adam catalog + menu import and Rial COGS UI."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import Cafe, Department, Product, ProductPriceEntry
from purchases.models_cogs import KukerItem, KukerRecipeLine, MenuSection
from purchases.services.baba_adam_menu import (
    COGS_ONLY_CONSUMABLE,
    NEW_MENU_PRODUCTS,
    import_baba_adam_menu,
)
from purchases.services.menu_cogs import MenuGraph


class BabaAdamMenuImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(code='baba_adam', name='کافه رستوران بابا آدم', is_active=True)
        cls.other_cafe = Cafe.objects.create(code='local', name='کافه لوکال', is_active=True)

        User = get_user_model()
        cls.admin = User.objects.create_user(
            username='baba_admin', password='p', role='admin', cafe=cls.cafe,
        )

        # Seed Part 1 base catalog first, then Part 2 recipes & missing items
        call_command('import_catalog_csv', cafe='baba_adam')
        cls.summary = import_baba_adam_menu(cafe=cls.cafe)

    def setUp(self):
        self.client = Client(HTTP_HOST='localhost')
        self.client.force_login(self.admin)

    def test_menu_counts_and_sections(self):
        self.assertEqual(self.summary['dishes'], 140)
        self.assertEqual(self.summary['preps'], 136)
        self.assertEqual(self.summary['recipe_lines'], 1869)
        self.assertEqual(
            set(MenuSection.objects.filter(cafe=self.cafe, kind='dish').values_list('name', flat=True)),
            {'صبحانه', 'سالاد و پیش غذا', 'فرنگی', 'ایرانی', 'منو اصلی بار'},
        )
        self.assertEqual(
            set(MenuSection.objects.filter(cafe=self.cafe, kind='prep').values_list('name', flat=True)),
            {'آماده سازی آشپزخانه', 'آماده سازی بار'},
        )

    def test_missing_products_created_and_water_is_cogs_only(self):
        self.assertEqual(len(self.summary['created_products']), len(NEW_MENU_PRODUCTS))
        self.assertEqual(Product.objects.filter(cafe=self.cafe).count(), 361)
        self.assertEqual(Product.objects.filter(cafe=self.cafe, is_active=True).count(), 341)
        self.assertEqual(Product.objects.filter(cafe=self.cafe, is_active=False).count(), 20)

        # Water/ice must remain COGS-only (no orderable Product row)
        self.assertFalse(Product.objects.filter(cafe=self.cafe, name=COGS_ONLY_CONSUMABLE).exists())
        water_ki = KukerItem.objects.get(cafe=self.cafe, name=COGS_ONLY_CONSUMABLE, kind=KukerItem.KIND_RAW)
        self.assertIsNone(water_ki.product)
        self.assertEqual(water_ki.price, Decimal('0.0000'))

        # Newly created products have logical units & categories
        soda = Product.objects.get(cafe=self.cafe, name='آب گاز دار سودا 250 میل')
        self.assertEqual(soda.unit.name, 'بطری')
        self.assertEqual(soda.base_unit_price, Decimal('187501.0000'))
        self.assertEqual(list(soda.departments.values_list('name', flat=True)), ['بار'])

        malt = Product.objects.get(cafe=self.cafe, name='آرگو مالت قوطی 330 CC')
        self.assertEqual(malt.unit.name, 'قوطی')
        self.assertIn('آماده سازی', list(malt.departments.values_list('name', flat=True)))

        # Previously-inactive items used in recipes are now active with section access
        for pname in ('آرد سمولینا', 'پودر کاکائو', 'خرما خشک', 'ران بی خص', 'سبزی قورمه'):
            p = Product.objects.get(cafe=self.cafe, name=pname)
            self.assertTrue(p.is_active, pname)
            self.assertTrue(p.departments.exists(), pname)

    def test_bar_and_bar_prep_grant_bar_department(self):
        bar_prep_product_ids = set(
            KukerRecipeLine.objects.filter(
                item__cafe=self.cafe,
                item__sheet='آماده سازی بار',
                material__kind=KukerItem.KIND_RAW,
                material__product__isnull=False,
            ).values_list('material__product_id', flat=True)
        )
        self.assertGreater(len(bar_prep_product_ids), 20)
        for p in Product.objects.filter(id__in=bar_prep_product_ids).prefetch_related('departments'):
            self.assertIn('بار', {d.name for d in p.departments.all()}, p.name)
        self.assertFalse(Department.objects.filter(cafe=self.cafe, name='آماده سازی بار').exists())

    def test_canonical_multi_l_resolution(self):
        # ادویه ریبز -> نمک دریا, خمیر پیتزا -> نمک ید دار
        ribs = KukerItem.objects.get(cafe=self.cafe, name='ادویه ریبز', kind=KukerItem.KIND_PREP)
        self.assertTrue(ribs.lines.filter(material__name='نمک دریا').exists())

        dough = KukerItem.objects.get(cafe=self.cafe, name='خمیر نان و پیتزا', kind=KukerItem.KIND_PREP)
        self.assertTrue(dough.lines.filter(material__name='نمک ید دار').exists())

        # سالاد ناتی -> خرما پیارم, مایه کباب برگ -> راسته مغز بره
        nati = KukerItem.objects.get(cafe=self.cafe, name='سالاد ناتی', kind=KukerItem.KIND_DISH)
        self.assertTrue(nati.lines.filter(material__name='خرما پیارم').exists())

        barg = KukerItem.objects.get(cafe=self.cafe, name='مایه کباب برگ', kind=KukerItem.KIND_PREP)
        self.assertTrue(barg.lines.filter(material__name='راسته مغز بره').exists())

    def test_all_dishes_compute_finite_rial_cogs_without_unit_explosions(self):
        graph = MenuGraph.from_db(cafe=self.cafe)
        for k in graph.dishes():
            cons = graph.unit_consumption(k)
            cost = (
                sum((sv[1] for sv in cons['raw'].values()), Decimal('0'))
                + sum((sv[1] for sv in cons['bad'].values()), Decimal('0'))
            )
            name = graph.items[k]['name']
            self.assertGreater(cost, Decimal('0'), name)
            self.assertLess(cost, Decimal('50000000'), name)
            if name != 'سوت لاچ':
                self.assertEqual(cons['bad'], {}, f'{name}: {cons["bad"]}')

    def test_ui_views_display_rial_and_subdish_recipe_saves_cleanly(self):
        for name in ('cogs_menu_list', 'cogs_manage_items', 'cogs_consumption_calc'):
            r = self.client.get(reverse(name, kwargs={'cafe_code': self.cafe.code}))
            self.assertEqual(r.status_code, 200)
            html = r.content.decode('utf-8')
            self.assertIn('ریال', html)
            self.assertNotIn('تومان', html)

        kubideh = KukerItem.objects.get(cafe=self.cafe, name='خوارک کوبیده', kind=KukerItem.KIND_DISH)
        get_r = self.client.get(reverse(
            'cogs_recipe_api', kwargs={'cafe_code': self.cafe.code, 'item_id': kubideh.pk},
        ))
        self.assertEqual(get_r.status_code, 200)
        data = get_r.json()
        self.assertEqual(sorted(m['name'] for m in data['extra_materials']), ['ته چین', 'نان محلی'])

        post_r = self.client.post(
            reverse('cogs_recipe_api', kwargs={'cafe_code': self.cafe.code, 'item_id': kubideh.pk}),
            data=json.dumps({
                'name': data['item']['name'],
                'section_id': data['item']['section_id'],
                'out_qty': data['item']['out_qty'],
                'out_unit': data['item']['out_unit'],
                'lines': [
                    {'material_id': l['material_id'], 'qty': l['qty'], 'unit': l['unit'], 'fee': l['fee']}
                    for l in data['lines']
                ],
            }),
            content_type='application/json',
        )
        self.assertEqual(post_r.status_code, 200, post_r.content)
        self.assertEqual(post_r.json()['lines_count'], len(data['lines']))

    def test_idempotency_and_other_cafe_isolation(self):
        before_entries = ProductPriceEntry.objects.filter(cafe=self.cafe).count()
        before_prods = Product.objects.filter(cafe=self.cafe).count()

        second = import_baba_adam_menu(cafe=self.cafe)
        self.assertEqual(second['created_products'], [])
        self.assertEqual(second['activated_products'], [])
        self.assertEqual(Product.objects.filter(cafe=self.cafe).count(), before_prods)
        self.assertEqual(ProductPriceEntry.objects.filter(cafe=self.cafe).count(), before_entries)
        self.assertEqual(Product.objects.filter(cafe=self.other_cafe).count(), 0)
        self.assertEqual(KukerItem.objects.filter(cafe=self.other_cafe).count(), 0)
