# -*- coding: utf-8 -*-
"""Tests for the three-page COGS module (list / pricing / consumption).

Covers the navigation contract, cafe scoping, the recipe editor payload,
section management and the cost-history chart endpoint.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from purchases.models import (
    Cafe, CogsReport, KukerItem, KukerItemCostHistory, KukerRecipeLine, MenuSection, Product,
)


class CogsPagesBase(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code='baba_adam', name='بابا آدم')
        self.other_cafe = Cafe.objects.create(code='local', name='لوکال')
        User = get_user_model()
        self.admin = User.objects.create_superuser('cogs_admin', 'a@a.com', 'x', role='admin')
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

        self.cheese = self.make_raw('پنیر', Decimal('2'), 'گرم')
        self.sauce = KukerItem.objects.create(
            cafe=self.cafe, name='سس پنیر', key='سسپنیر', kind=KukerItem.KIND_PREP,
            out_qty=1, out_unit='پرس', section=self.kitchen)
        KukerRecipeLine.objects.create(item=self.sauce, material=self.cheese,
                                       quantity=100, unit='گرم', fee=None)
        self.dish = KukerItem.objects.create(
            cafe=self.cafe, name='پاستا', key='پاستا', kind=KukerItem.KIND_DISH,
            out_qty=1, out_unit='پرس', section=self.dish_section)
        KukerRecipeLine.objects.create(item=self.dish, material=self.sauce,
                                       quantity=1, unit='پرس', fee=None)

        # قلم کافهٔ دیگر — نباید در هیچ صفحه‌ای دیده شود
        self.other_dish = KukerItem.objects.create(
            cafe=self.other_cafe, name='غذای کافه دیگر', key='غذایکافه دیگر',
            kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')

    def make_raw(self, name, price, unit='گرم', cafe=None):
        return KukerItem.objects.create(
            cafe=cafe or self.cafe, name=name, key=name, kind=KukerItem.KIND_RAW,
            out_unit=unit, price=price, is_custom=True)

    def url(self, name, **kwargs):
        from django.urls import reverse
        kwargs.setdefault('cafe_code', self.cafe.code)
        return reverse(name, kwargs=kwargs)


# ---------------------------------------------------------------------------
# ناوبری
# ---------------------------------------------------------------------------
class CogsNavigationTest(CogsPagesBase):
    def test_sidebar_has_three_cogs_sub_links(self):
        html = self.client.get(self.url('cogs_menu_list')).content.decode()
        for name in ('cogs_menu_list', 'cogs_manage_items', 'cogs_consumption_calc'):
            with self.subTest(name=name):
                self.assertIn(self.url(name), html)

    def test_product_list_stays_in_sidebar(self):
        html = self.client.get(self.url('cogs_menu_list')).content.decode()
        self.assertIn(self.url('product_list'), html)

    def test_top_sub_nav_marks_active_page(self):
        html = self.client.get(self.url('cogs_manage_items')).content.decode()
        self.assertIn('cogs-nav-tab is-active', html)
        self.assertIn('افزودن آیتم و قیمت‌گذاری کالاها', html)

    def test_all_three_pages_render(self):
        for name in ('cogs_menu_list', 'cogs_manage_items', 'cogs_consumption_calc'):
            with self.subTest(name=name):
                r = self.client.get(self.url(name))
                self.assertEqual(r.status_code, 200)

    def test_legacy_dashboard_url_still_works(self):
        r = self.client.get(self.url('cogs_dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('محاسبه میزان مصرف', r.content.decode())


# ---------------------------------------------------------------------------
# صفحه ۱ — لیست غذاها و نیمه‌آماده‌ها
# ---------------------------------------------------------------------------
class CogsMenuListTest(CogsPagesBase):
    def test_shows_dish_and_prep_in_their_sections(self):
        html = self.client.get(self.url('cogs_menu_list')).content.decode()
        self.assertIn('پاستا', html)
        self.assertIn('سس پنیر', html)
        self.assertIn('منوی فرنگی', html)

    def test_fixed_prep_sections_always_visible(self):
        # «آماده سازی بار» هیچ قلمی ندارد ولی باید نمایش داده شود
        html = self.client.get(self.url('cogs_menu_list')).content.decode()
        self.assertIn('آماده سازی آشپزخانه', html)
        self.assertIn('آماده سازی بار', html)

    def test_other_cafe_items_are_hidden(self):
        html = self.client.get(self.url('cogs_menu_list')).content.decode()
        self.assertNotIn('غذای کافه دیگر', html)

    def test_shows_ingredient_count_and_out_qty(self):
        html = self.client.get(self.url('cogs_menu_list')).content.decode()
        # پاستا یک سطر رسپی دارد، سس پنیر هم یک سطر
        self.assertIn('تعداد مواد', html)
        self.assertIn('خروجی هر بچ', html)

    def test_creates_default_prep_sections_on_first_visit(self):
        MenuSection.objects.all().delete()
        self.client.get(self.url('cogs_menu_list'))
        names = set(MenuSection.objects.filter(
            cafe=self.cafe, kind=MenuSection.KIND_PREP).values_list('name', flat=True))
        self.assertEqual(names, {'آماده سازی آشپزخانه', 'آماده سازی بار'})

    def test_empty_cafe_shows_empty_state(self):
        Cafe.objects.create(code='empty_cafe', name='خالی')
        r = self.client.get('/empty_cafe/cogs/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('هنوز هیچ غذایی', r.content.decode())


# ---------------------------------------------------------------------------
# صفحه ۲ — قیمت‌گذاری کالاها
# ---------------------------------------------------------------------------
class CogsManageItemsTest(CogsPagesBase):
    def test_lists_raw_materials_only(self):
        html = self.client.get(self.url('cogs_manage_items')).content.decode()
        self.assertIn('پنیر', html)
        self.assertNotIn('سس پنیر', html)      # نیمه‌آماده در این صفحه نیست

    def test_other_cafe_materials_are_hidden(self):
        self.make_raw('زعفران', Decimal('900'), 'گرم', cafe=self.other_cafe)
        html = self.client.get(self.url('cogs_manage_items')).content.decode()
        self.assertNotIn('زعفران', html)

    def test_state_filter(self):
        self.make_raw('بدون قیمت', None, 'گرم')
        html = self.client.get(self.url('cogs_manage_items') + '?state=unpriced').content.decode()
        self.assertIn('بدون قیمت', html)
        self.assertNotIn('پنیر', html)

    def test_search_filter(self):
        html = self.client.get(self.url('cogs_manage_items') + '?q=پنیر').content.decode()
        self.assertIn('پنیر', html)

    def test_add_normalizes_kilogram_to_gram(self):
        r = self.client.post(self.url('cogs_manage_items'), {
            'action': 'add', 'name': 'هویج', 'unit': 'کیلوگرم', 'price': '250000',
        })
        self.assertEqual(r.status_code, 302)
        item = KukerItem.objects.get(cafe=self.cafe, name='هویج')
        self.assertEqual(item.price, Decimal('250.0000'))
        self.assertEqual(item.out_unit, 'گرم')
        self.assertTrue(Product.objects.filter(cafe=self.cafe, name='هویج').exists())

    def test_price_update_records_cost_history(self):
        r = self.client.post(self.url('cogs_manage_items'), {
            'action': 'price', 'item_id': self.cheese.pk, 'price': '5', 'unit': 'گرم',
        })
        self.assertEqual(r.status_code, 302)
        self.cheese.refresh_from_db()
        self.assertEqual(self.cheese.price, Decimal('5.0000'))
        latest = (KukerItemCostHistory.objects.filter(item=self.dish)
                  .order_by('-recorded_at', '-id').first())
        self.assertIsNotNone(latest)
        self.assertEqual(latest.cost_per_unit, Decimal('500'))

    def test_price_update_ignores_items_of_another_cafe(self):
        other_raw = self.make_raw('زعفران', Decimal('900'), 'گرم', cafe=self.other_cafe)
        self.client.post(self.url('cogs_manage_items'), {
            'action': 'price', 'item_id': other_raw.pk, 'price': '1', 'unit': 'گرم',
        })
        other_raw.refresh_from_db()
        self.assertEqual(other_raw.price, Decimal('900'))

    def test_rejects_duplicate_name(self):
        self.client.post(self.url('cogs_manage_items'), {
            'action': 'add', 'name': 'پنیر', 'unit': 'گرم', 'price': '10',
        })
        self.assertEqual(KukerItem.objects.filter(cafe=self.cafe, name='پنیر').count(), 1)


# ---------------------------------------------------------------------------
# ویرایش رسپی
# ---------------------------------------------------------------------------
class CogsRecipeApiTest(CogsPagesBase):
    def test_get_returns_item_lines_and_picker(self):
        r = self.client.get(self.url('cogs_recipe_api', item_id=self.dish.pk))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['item']['name'], 'پاستا')
        self.assertEqual(len(data['lines']), 1)

        kinds = {m['name']: m['kind'] for m in data['materials']}
        self.assertEqual(kinds['پنیر'], 'raw')
        self.assertEqual(kinds['سس پنیر'], 'prep')
        # آیتم خودش نباید در فهرست مواد باشد
        self.assertNotIn('پاستا', kinds)
        # غذای منو به‌عنوان ماده پیشنهاد نمی‌شود
        self.assertTrue(all(m['kind'] in ('raw', 'prep') for m in data['materials']))
        self.assertTrue(all(m['kind_label'] for m in data['materials']))
        self.assertEqual(len(data['sections']), 1)

    def test_get_is_cafe_scoped(self):
        r = self.client.get(f'/local/cogs/recipe/{self.dish.pk}/')
        self.assertEqual(r.status_code, 404)

    def test_post_updates_name_section_and_output(self):
        new_section = MenuSection.objects.create(
            cafe=self.cafe, name='پیش‌غذا', kind=MenuSection.KIND_DISH)
        payload = {
            'name': 'پاستا آلفردو',
            'section_id': new_section.pk,
            'out_qty': '2',
            'out_unit': 'پرس',
            'lines': [{'material_id': self.sauce.pk, 'qty': '2', 'unit': 'پرس'}],
        }
        r = self.client.post(self.url('cogs_recipe_api', item_id=self.dish.pk),
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.name, 'پاستا آلفردو')
        self.assertEqual(self.dish.section_id, new_section.pk)
        self.assertEqual(self.dish.out_qty, Decimal('2'))
        # بهای هر واحد = (۲ × ۲۰۰) / ۲ = ۲۰۰
        self.assertEqual(Decimal(r.json()['cost_per']), Decimal('200'))

    def test_post_rejects_self_consumption(self):
        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': self.dish.pk, 'qty': '1', 'unit': 'پرس'}]}
        r = self.client.post(self.url('cogs_recipe_api', item_id=self.dish.pk),
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_post_rejects_cycle(self):
        # سس الف ← سس ب ؛ حالا سس ب را می‌خواهیم وابسته به سس الف کنیم → چرخه
        prep_a = KukerItem.objects.create(
            cafe=self.cafe, name='سس الف', key='سسالف', kind=KukerItem.KIND_PREP,
            out_qty=1, out_unit='پرس')
        prep_b = KukerItem.objects.create(
            cafe=self.cafe, name='سس ب', key='سسب', kind=KukerItem.KIND_PREP,
            out_qty=1, out_unit='پرس')
        KukerRecipeLine.objects.create(item=prep_a, material=prep_b,
                                       quantity=1, unit='پرس', fee=None)

        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': prep_a.pk, 'qty': '1', 'unit': 'پرس'}]}
        r = self.client.post(self.url('cogs_recipe_api', item_id=prep_b.pk),
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('چرخه', r.json()['error'])
        # رسپی ذخیره نمی‌شود
        self.assertFalse(KukerRecipeLine.objects.filter(item=prep_b).exists())

    def test_dish_cannot_be_used_as_ingredient(self):
        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': self.dish.pk, 'qty': '1', 'unit': 'پرس'}]}
        r = self.client.post(self.url('cogs_recipe_api', item_id=self.sauce.pk),
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('غذای منو', r.json()['error'])
        self.assertEqual(KukerRecipeLine.objects.filter(item=self.sauce).count(), 1)

    def test_post_keeps_non_standard_output_unit(self):
        """واحدهای خارج از فهرست استاندارد (مثل «پرس 330») نباید عوض شوند."""
        self.dish.out_unit = 'پرس 330'
        self.dish.save(update_fields=['out_unit'])
        payload = {'out_qty': '1', 'out_unit': 'پرس 330',
                   'lines': [{'material_id': self.sauce.pk, 'qty': '1', 'unit': 'پرس'}]}
        r = self.client.post(self.url('cogs_recipe_api', item_id=self.dish.pk),
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.out_unit, 'پرس 330')
        self.assertEqual(r.json()['item']['out_unit'], 'پرس 330')

    def test_get_reports_non_standard_output_unit(self):
        self.dish.out_unit = 'پرس 330'
        self.dish.save(update_fields=['out_unit'])
        data = self.client.get(self.url('cogs_recipe_api', item_id=self.dish.pk)).json()
        self.assertEqual(data['item']['out_unit'], 'پرس 330')

    def test_post_rejects_zero_quantity(self):
        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': self.sauce.pk, 'qty': '0', 'unit': 'پرس'}]}
        r = self.client.post(self.url('cogs_recipe_api', item_id=self.dish.pk),
                             data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_post_records_cost_history(self):
        payload = {'out_qty': '1', 'out_unit': 'پرس',
                   'lines': [{'material_id': self.sauce.pk, 'qty': '3', 'unit': 'پرس'}]}
        self.client.post(self.url('cogs_recipe_api', item_id=self.dish.pk),
                         data=json.dumps(payload), content_type='application/json')
        self.assertTrue(KukerItemCostHistory.objects.filter(item=self.dish).exists())


# ---------------------------------------------------------------------------
# تاریخچه بها (نمودار)
# ---------------------------------------------------------------------------
class CogsCostHistoryTest(CogsPagesBase):
    def test_empty_history_returns_zero_points(self):
        r = self.client.get(self.url('cogs_item_cost_history', item_id=self.dish.pk))
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['min'], '')

    def test_points_are_ordered_old_to_new(self):
        for cost in ('100', '150', '120'):
            KukerItemCostHistory.objects.create(item=self.dish,
                                                cost_per_unit=Decimal(cost))
        data = self.client.get(
            self.url('cogs_item_cost_history', item_id=self.dish.pk)).json()
        self.assertEqual([p['cost'] for p in data['points']], ['100.0000', '150.0000', '120.0000'])
        self.assertEqual(data['min'], '100.0000')
        self.assertEqual(data['max'], '150.0000')
        self.assertEqual(data['latest'], '120.0000')
        self.assertTrue(data['points'][0]['at'])
        self.assertTrue(data['points'][0]['at_full'])

    def test_history_is_cafe_scoped(self):
        r = self.client.get(f'/local/cogs/item/{self.dish.pk}/cost-history/')
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# بخش‌ها
# ---------------------------------------------------------------------------
class CogsSectionTest(CogsPagesBase):
    def test_creates_section(self):
        r = self.client.post(self.url('cogs_section_save'), {
            'name': 'دسرها', 'kind': 'dish',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(MenuSection.objects.filter(
            cafe=self.cafe, name='دسرها', kind=MenuSection.KIND_DISH).exists())

    def test_renames_section(self):
        self.client.post(self.url('cogs_section_save'), {
            'section_id': self.dish_section.pk, 'name': 'منوی اروپایی', 'kind': 'dish',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.dish_section.refresh_from_db()
        self.assertEqual(self.dish_section.name, 'منوی اروپایی')

    def test_rejects_duplicate_name(self):
        r = self.client.post(self.url('cogs_section_save'), {
            'name': 'منوی فرنگی', 'kind': 'dish',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(MenuSection.objects.filter(
            cafe=self.cafe, name='منوی فرنگی', kind='dish').count(), 1)

    def test_cannot_rename_section_of_another_cafe(self):
        foreign = MenuSection.objects.create(
            cafe=self.other_cafe, name='بخش دیگر', kind=MenuSection.KIND_DISH)
        r = self.client.post(self.url('cogs_section_save'), {
            'section_id': foreign.pk, 'name': 'دزدی', 'kind': 'dish',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.name, 'بخش دیگر')


# ---------------------------------------------------------------------------
# صفحه ۳ — محاسبه مصرف
# ---------------------------------------------------------------------------
class CogsConsumptionTest(CogsPagesBase):
    def test_saves_report_for_active_cafe(self):
        r = self.client.post(self.url('cogs_consumption_calc'), {
            'start_year': '1404', 'start_month': '1', 'start_day': '1',
            'end_year': '1404', 'end_month': '1', 'end_day': '30',
            f'qty__{self.dish.pk}': '4',
        })
        self.assertEqual(r.status_code, 302)
        report = CogsReport.objects.get(cafe=self.cafe)
        self.assertEqual(report.lines.count(), 1)
        self.assertEqual(report.lines.first().qty, Decimal('4'))

    def test_shows_results_of_saved_report(self):
        report = CogsReport.objects.create(
            cafe=self.cafe, period_start='2026-01-01', period_end='2026-01-30')
        from purchases.models import CogsReportLine
        CogsReportLine.objects.create(report=report, item=self.dish, qty=3)
        html = self.client.get(
            self.url('cogs_consumption_calc') + f'?report={report.id}').content.decode()
        self.assertIn('بهای تمام شده', html)
        self.assertIn('پاستا', html)

    def test_other_cafe_report_is_404(self):
        report = CogsReport.objects.create(
            cafe=self.other_cafe, period_start='2026-01-01', period_end='2026-01-30')
        r = self.client.get(self.url('cogs_consumption_calc') + f'?report={report.id}')
        self.assertEqual(r.status_code, 404)
