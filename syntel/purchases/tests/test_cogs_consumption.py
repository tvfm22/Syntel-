# -*- coding: utf-8 -*-
"""Page 3 of the COGS module — sales entry, indirect prep expansion,
saved-report editing and the category / requester-department filters.

The fixture builds a three-level chain so the recursive expansion is actually
exercised:

    غذای تست (dish) → سس تست (prep) → مایه تست (prep) → آرد (raw)
                                                    → پنیر (raw)

One portion of the dish needs 100 g flour (1 T/gram) and 10 g cheese
(2 T/gram) — 120 T per portion, 240 T for two.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Category, CogsReport, KukerItem, KukerRecipeLine, MenuSection, Product,
)
from purchases.services.menu_cogs import compute_cogs


class ConsumptionBase(TestCase):
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
            cafe=self.cafe, name='منوی تست', kind=MenuSection.KIND_DISH, display_order=10)

        self.dairy = Category.objects.create(cafe=self.cafe, name='لبنیات')
        self.grocery = Category.objects.create(cafe=self.cafe, name='خوار و بار')

        self.flour = self.make_raw('آرد', Decimal('1'), self.grocery, 'general')
        self.cheese = self.make_raw('پنیر', Decimal('2'), self.dairy, 'آشپزخانه')

        self.base = KukerItem.objects.create(
            cafe=self.cafe, name='مایه تست', key='مایهتست', kind=KukerItem.KIND_PREP,
            out_qty=1000, out_unit='گرم', section=self.kitchen)
        KukerRecipeLine.objects.create(item=self.base, material=self.flour,
                                       quantity=500, unit='گرم', fee=None)

        self.sauce = KukerItem.objects.create(
            cafe=self.cafe, name='سس تست', key='سستست', kind=KukerItem.KIND_PREP,
            out_qty=10, out_unit='پرس', section=self.kitchen)
        KukerRecipeLine.objects.create(item=self.sauce, material=self.base,
                                       quantity=2000, unit='گرم', fee=None)
        KukerRecipeLine.objects.create(item=self.sauce, material=self.cheese,
                                       quantity=100, unit='گرم', fee=None)

        self.dish = KukerItem.objects.create(
            cafe=self.cafe, name='غذای تست', key='غذایتست', kind=KukerItem.KIND_DISH,
            out_qty=1, out_unit='پرس', section=self.dish_section)
        KukerRecipeLine.objects.create(item=self.dish, material=self.sauce,
                                       quantity=1, unit='پرس', fee=None)

        # قلم کافهٔ دیگر — هرگز نباید در محاسبه یا صفحه ظاهر شود
        self.other_dish = KukerItem.objects.create(
            cafe=self.other_cafe, name='غذای کافه دیگر', key='غذایکافه دیگر',
            kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')
        self.other_raw = KukerItem.objects.create(
            cafe=self.other_cafe, name='کالای کافه دیگر', key='کالایکافه دیگر',
            kind=KukerItem.KIND_RAW, out_unit='گرم', price=99)
        KukerRecipeLine.objects.create(item=self.other_dish, material=self.other_raw,
                                       quantity=1, unit='گرم', fee=None)

    def make_raw(self, name, price, category, usage_location, cafe=None):
        cafe = cafe or self.cafe
        product = Product.objects.create(
            cafe=cafe, name=name, category=category, usage_location=usage_location,
            smallest_unit='گرم', base_unit_price=price)
        return KukerItem.objects.create(
            cafe=cafe, name=name, key=name, kind=KukerItem.KIND_RAW,
            out_unit='گرم', price=price, product=product, is_custom=True)

    def url(self, name='cogs_consumption_calc', **kwargs):
        kwargs.setdefault('cafe_code', self.cafe.code)
        return reverse(name, kwargs=kwargs)

    def save_report(self, qty='1'):
        r = self.client.post(self.url(), {
            'start_year': '1405', 'start_month': '7', 'start_day': '1',
            'end_year': '1405', 'end_month': '7', 'end_day': '30',
            f'qty__{self.dish.pk}': qty,
        })
        self.assertEqual(r.status_code, 302, r.content)
        return CogsReport.objects.filter(cafe=self.cafe).latest('id')


# ---------------------------------------------------------------------------
# موتور محاسبه
# ---------------------------------------------------------------------------
class ComputeCogsEngineTest(ConsumptionBase):
    def test_nested_prep_expands_to_raw_materials(self):
        result = compute_cogs({str(self.dish.pk): '2'}, cafe=self.cafe)
        by_name = {row['item']['name']: row for row in result['raw_rows']}

        self.assertIn('آرد', by_name, 'مواد اولیهٔ دو سطح پایین‌تر باید در جدول بیاید')
        self.assertIn('پنیر', by_name)
        # ۲ پرس → ۲۰۰ گرم آرد و ۲۰ گرم پنیر
        self.assertAlmostEqual(float(by_name['آرد']['qty']), 200.0, places=3)
        self.assertAlmostEqual(float(by_name['پنیر']['qty']), 20.0, places=3)
        self.assertAlmostEqual(float(result['total_cost']), 240.0, places=2)
        self.assertAlmostEqual(
            float(sum(row['cost'] for row in result['raw_rows'])), 240.0, places=2)

    def test_intermediate_prep_is_reported_once_per_batch(self):
        result = compute_cogs({str(self.dish.pk): '2'}, cafe=self.cafe)
        prep = {row['item']['name']: row for row in result['prep_rows']}
        self.assertIn('سس تست', prep)
        self.assertIn('مایه تست', prep)
        # ۲ پرس غذا = ۲ پرس سس = ۴۰۰ گرم مایه
        self.assertAlmostEqual(float(prep['سس تست']['qty']), 2.0, places=3)
        self.assertAlmostEqual(float(prep['مایه تست']['qty']), 400.0, places=3)

    def test_prep_row_carries_expanded_raw_breakdown(self):
        result = compute_cogs({str(self.dish.pk): '1'}, cafe=self.cafe)
        prep = {row['item']['name']: row for row in result['prep_rows']}
        raws = {line['name']: line for line in prep['سس تست']['raw_lines']}
        self.assertIn('آرد', raws, 'سس باید مواد اولیهٔ غیرمستقیمش را هم نشان دهد')
        self.assertAlmostEqual(float(raws['آرد']['qty']), 100.0, places=3)

    def test_raw_rows_carry_product_category_and_department(self):
        result = compute_cogs({str(self.dish.pk): '1'}, cafe=self.cafe)
        rows = {row['item']['name']: row for row in result['raw_rows']}
        self.assertEqual(rows['پنیر']['category_name'], 'لبنیات')
        self.assertEqual(rows['پنیر']['category_id'], str(self.dairy.pk))
        self.assertEqual(rows['پنیر']['usage_location'], 'آشپزخانه')
        self.assertEqual(rows['آرد']['usage_location'], 'general')

    def test_material_without_product_falls_back_to_uncategorized(self):
        loose = KukerItem.objects.create(
            cafe=self.cafe, name='کالای بی‌پرونده', key='کالایبیپرونده',
            kind=KukerItem.KIND_RAW, out_unit='گرم', price=5)
        KukerRecipeLine.objects.create(item=self.dish, material=loose,
                                       quantity=10, unit='گرم', fee=None)
        result = compute_cogs({str(self.dish.pk): '1'}, cafe=self.cafe)
        row = next(r for r in result['raw_rows'] if r['item']['name'] == 'کالای بی‌پرونده')
        self.assertEqual(row['category_id'], 'none')
        self.assertEqual(row['category_name'], '')
        self.assertEqual(row['usage_location'], 'general')

    def test_computation_is_scoped_to_one_cafe(self):
        result = compute_cogs({str(self.dish.pk): '1'}, cafe=self.cafe)
        names = [row['item']['name'] for row in result['raw_rows']]
        self.assertNotIn('کالای کافه دیگر', names)
        dish_names = [row['item']['name'] for row in result['dishes']]
        self.assertNotIn('غذای کافه دیگر', dish_names)

    def test_selling_a_prep_item_directly_consumes_nothing(self):
        result = compute_cogs({str(self.sauce.pk): '5'}, cafe=self.cafe)
        self.assertEqual(result['raw_rows'], [])
        self.assertEqual(result['total_cost'], Decimal('0'))


# ---------------------------------------------------------------------------
# صفحه — ورود فروش، ذخیره، ویرایش و حذف
# ---------------------------------------------------------------------------
class ConsumptionPageTest(ConsumptionBase):
    def test_sales_table_lists_dishes_grouped_by_menu_section(self):
        html = self.client.get(self.url()).content.decode()
        self.assertIn(f'name="qty__{self.dish.pk}"', html)
        self.assertNotIn(f'name="qty__{self.sauce.pk}"', html)
        self.assertNotIn(f'name="qty__{self.base.pk}"', html)
        self.assertIn('data-menu="منوی تست"', html)
        self.assertNotIn('غذای کافه دیگر', html)

    def test_default_post_saves_a_new_report(self):
        report = self.save_report(qty='4')
        self.assertEqual(report.period_start.isoformat(), '2026-09-23')
        self.assertEqual(report.lines.count(), 1)
        self.assertEqual(report.lines.first().qty, Decimal('4'))

    def test_preview_does_not_save_anything(self):
        r = self.client.post(self.url(), {
            'action': 'preview',
            'start_year': '1405', 'start_month': '7', 'start_day': '1',
            'end_year': '1405', 'end_month': '7', 'end_day': '30',
            f'qty__{self.dish.pk}': '3',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CogsReport.objects.filter(cafe=self.cafe).count(), 0)
        self.assertContains(r, 'پیش‌نمایش')
        self.assertContains(r, 'پنیر')

    def test_preview_while_editing_keeps_report_context(self):
        """پیش‌نمایش وسط ویرایش، گزارش را دست نمی‌زند و دکمه‌های ویرایش را نگه می‌دارد."""
        report = self.save_report(qty='3')
        r = self.client.post(self.url(), {
            'action': 'preview', 'report_id': report.id,
            'start_year': '1405', 'start_month': '7', 'start_day': '1',
            'end_year': '1405', 'end_month': '7', 'end_day': '30',
            f'qty__{self.dish.pk}': '8',
        })
        self.assertEqual(r.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.lines.first().qty, Decimal('3'))
        self.assertEqual(r.context['sold_total'], Decimal('8'))
        self.assertContains(r, 'در حال ویرایش گزارش')
        self.assertContains(r, 'بروزرسانی این گزارش')
        self.assertContains(r, 'پیش‌نمایش')

    def test_get_report_populates_dates_and_quantities(self):
        report = self.save_report(qty='3')
        html = self.client.get(f'{self.url()}?report={report.id}').content.decode()
        self.assertIn('value="1405"', html)          # سال شمسی بازه
        self.assertRegex(html, rf'name="qty__{self.dish.pk}"[\s\S]{{0,200}}?value="3')
        self.assertIn('بروزرسانی این گزارش', html)
        self.assertIn('ذخیره به‌عنوان گزارش جدید', html)

    def test_update_replaces_lines_in_place(self):
        report = self.save_report(qty='3')
        r = self.client.post(self.url(), {
            'action': 'update', 'report_id': report.id,
            'start_year': '1405', 'start_month': '8', 'start_day': '2',
            'end_year': '1405', 'end_month': '8', 'end_day': '9',
            f'qty__{self.dish.pk}': '7',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(CogsReport.objects.filter(cafe=self.cafe).count(), 1)
        report.refresh_from_db()
        self.assertEqual(report.period_start.isoformat(), '2026-10-24')
        self.assertEqual(report.lines.count(), 1)
        self.assertEqual(report.lines.first().qty, Decimal('7'))

    def test_save_as_new_creates_a_second_report(self):
        report = self.save_report(qty='3')
        r = self.client.post(self.url(), {
            'action': 'save_as_new', 'report_id': report.id,
            'start_year': '1405', 'start_month': '7', 'start_day': '1',
            'end_year': '1405', 'end_month': '7', 'end_day': '30',
            f'qty__{self.dish.pk}': '9',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(CogsReport.objects.filter(cafe=self.cafe).count(), 2)
        self.assertEqual(
            CogsReport.objects.filter(cafe=self.cafe).order_by('-id').first().lines.first().qty,
            Decimal('9'))

    def test_delete_removes_report(self):
        report = self.save_report(qty='3')
        r = self.client.post(self.url(), {'action': 'delete', 'report_id': report.id})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(CogsReport.objects.filter(pk=report.id).exists())

    def test_delete_ignores_reports_of_another_cafe(self):
        foreign = CogsReport.objects.create(
            cafe=self.other_cafe, period_start='2026-01-01', period_end='2026-01-30')
        r = self.client.post(self.url(), {'action': 'delete', 'report_id': foreign.pk})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CogsReport.objects.filter(pk=foreign.pk).exists())

    def test_report_of_another_cafe_is_404(self):
        foreign = CogsReport.objects.create(
            cafe=self.other_cafe, period_start='2026-01-01', period_end='2026-01-30')
        r = self.client.get(f'{self.url()}?report={foreign.pk}')
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# فیلتر دسته‌بندی و بخش درخواست‌دهنده
# ---------------------------------------------------------------------------
class ConsumptionFilterTest(ConsumptionBase):
    def setUp(self):
        super().setUp()
        self.report = self.save_report(qty='2')

    def _context(self, query=''):
        r = self.client.get(f'{self.url()}?report={self.report.id}{query}')
        self.assertEqual(r.status_code, 200)
        return r.context

    def test_filter_options_are_built_from_consumed_materials(self):
        ctx = self._context()
        names = [c['name'] for c in ctx['categories']]
        self.assertIn('لبنیات', names)
        self.assertIn('خوار و بار', names)
        depts = [d['value'] for d in ctx['departments']]
        self.assertIn('آشپزخانه', depts)
        self.assertIn('general', depts)

    def test_no_filter_shows_everything(self):
        ctx = self._context()
        self.assertEqual(len(ctx['raw_rows']), 2)
        self.assertEqual(ctx['filtered_total'], Decimal('240'))

    def test_category_filter_limits_rows_and_subtotal(self):
        ctx = self._context(f'&cat={self.dairy.pk}')
        names = [r['item']['name'] for r in ctx['raw_rows']]
        self.assertEqual(names, ['پنیر'])
        self.assertEqual(ctx['filtered_total'], Decimal('40'))
        self.assertEqual(ctx['cat_filter'], str(self.dairy.pk))

    def test_department_filter_limits_rows_and_subtotal(self):
        ctx = self._context('&dept=آشپزخانه')
        names = [r['item']['name'] for r in ctx['raw_rows']]
        self.assertEqual(names, ['پنیر'])
        self.assertEqual(ctx['filtered_total'], Decimal('40'))

    def test_both_filters_combine(self):
        ctx = self._context(f'&cat={self.grocery.pk}&dept=آشپزخانه')
        self.assertEqual(ctx['raw_rows'], [])
        self.assertEqual(ctx['filtered_total'], Decimal('0'))

    def test_unknown_filter_value_is_ignored(self):
        ctx = self._context('&cat=999999')
        self.assertEqual(ctx['cat_filter'], '')
        self.assertEqual(len(ctx['raw_rows']), 2)

    def test_filter_markup_is_rendered(self):
        html = self.client.get(
            f'{self.url()}?report={self.report.id}').content.decode()
        self.assertIn('فیلتر دسته‌بندی کالا', html)
        self.assertIn('فیلتر بخش درخواست‌دهنده', html)
        self.assertIn('id="catFilter"', html)
        self.assertIn('id="deptFilter"', html)
        # هر ردیف مواد اولیه، دسته و بخشش را برای فیلتر سمت کلاینت حمل می‌کند
        self.assertIn(f'data-cat="{self.dairy.pk}"', html)
        self.assertIn('data-dept="آشپزخانه"', html)

    def test_filter_survives_saving_from_the_form(self):
        r = self.client.post(self.url(), {
            'action': 'save', 'cat': str(self.dairy.pk),
            'start_year': '1405', 'start_month': '7', 'start_day': '1',
            'end_year': '1405', 'end_month': '7', 'end_day': '30',
            f'qty__{self.dish.pk}': '5',
        })
        self.assertEqual(r.status_code, 302)
        self.assertIn(f'cat={self.dairy.pk}', r['Location'])
