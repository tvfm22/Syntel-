# ─────────────────────────────────────────────────────────────────────────────
# Prompt 5 — Tenant/warehouse scoping و سخت‌سازی IDOR برای جریان‌های حواله
#
#  - get_scoped_object_or_404 / filter_by_cafe: شکستِ بسته (fail-closed) —
#    بدون کافهٔ فعال یا مدلِ بدون مسیرِ کافه، هرگز fallback بی‌scope وجود ندارد
#    (404 / queryset خالی).
#  - همهٔ نقاط ورود حواله و مدیریت انبار: بدون کافه یا با شناسهٔ کافهٔ دیگر → 404.
#  - شناسه‌های ارسالی (انبار، محصول، دسته، موجودی، حواله) فقط در کافهٔ فعلی
#    معتبرند — کافهٔ دیگر هرگز قابل دسترسی نیست (IDOR).
# ─────────────────────────────────────────────────────────────────────────────
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Category, Product, StockItem, Unit, Warehouse, WarehouseVoucher,
)
from purchases.views import (
    CAFE_LOOKUP_PATHS, filter_by_cafe, get_scoped_object_or_404,
)

User = get_user_model()


def _set_cafe_session(client, cafe):
    session = client.session
    session['selected_cafe_id'] = cafe.id
    session['selected_cafe_code'] = cafe.code
    session.save()


class ScopedLookupHelperTests(TestCase):
    """get_scoped_object_or_404 / filter_by_cafe — شکستِ بسته وقتی کافه نیست"""

    def setUp(self):
        self.factory = RequestFactory()
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.wh = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)

    def _request(self, cafe=None, session_id=None):
        req = self.factory.get('/')
        req.session = {}
        if cafe is not None:
            req.cafe = cafe
        if session_id is not None:
            req.session['selected_cafe_id'] = session_id
        return req

    def test_get_scoped_object_or_404_without_cafe_raises_404(self):
        """بدون کافه: حتی با وجود رکورد، Http404 می‌دهد — نه fallback بی‌scope"""
        req = self._request()  # نه request.cafe نه session
        with self.assertRaises(Http404):
            get_scoped_object_or_404(Warehouse, req, id=self.wh.id)

    def test_get_scoped_object_or_404_with_session_cafe_works(self):
        """کافه از session — جستجو به همان کافه محدود می‌شود"""
        req = self._request(session_id=self.cafe.id)
        wh = get_scoped_object_or_404(Warehouse, req, id=self.wh.id)
        self.assertEqual(wh, self.wh)

    def test_get_scoped_object_or_404_unmapped_model_raises_404(self):
        """مدلی که در CAFE_LOOKUP_PATHS نیست → 404 (ناشناخته = ممنوع)"""
        req = self._request(cafe=self.cafe)
        with self.assertRaises(Http404):
            get_scoped_object_or_404(User, req, id=1)

    def test_filter_by_cafe_without_cafe_returns_none(self):
        """بدون کافه: queryset خالی برمی‌گرداند — نه کل رکوردها"""
        WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number='ISS-00001', source_warehouse=self.wh)
        req = self._request()
        qs = filter_by_cafe(WarehouseVoucher.objects.all(), req)
        self.assertEqual(qs.count(), 0)

    def test_filter_by_cafe_with_cafe_returns_own_rows(self):
        """با کافه: فقط ردیف‌های همان کافه"""
        other = Cafe.objects.create(name='Other', code='other', is_active=True)
        wh_other = Warehouse.objects.create(
            cafe=other, code='whO', name='انبار دیگر', is_active=True)
        WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number='ISS-00001', source_warehouse=self.wh)
        WarehouseVoucher.objects.create(
            cafe=other, voucher_type='issue', status='submitted',
            voucher_number='ISS-00002', source_warehouse=wh_other)
        req = self._request(cafe=self.cafe)
        qs = filter_by_cafe(WarehouseVoucher.objects.all(), req)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().voucher_number, 'ISS-00001')

    def test_filter_by_cafe_unmapped_model_returns_none(self):
        """مدلِ بدون مسیر کافه: حتی با کافهٔ فعال هم queryset خالی (شکستِ بسته)"""
        req = self._request(cafe=self.cafe)
        self.assertEqual(filter_by_cafe(User.objects.all(), req).count(), 0)

    def test_cafe_lookup_paths_cover_voucher_models(self):
        """هر مدل حواله/انبار باید مسیر کافه داشته باشد"""
        for model_name in ('WarehouseVoucher', 'WarehouseVoucherItem',
                           'StockItem', 'StockTransaction', 'Warehouse',
                           'Product', 'ProductBrand', 'Unit', 'Category',
                           'Department', 'Supplier', 'Requester'):
            self.assertIn(model_name, CAFE_LOOKUP_PATHS,
                          f'{model_name} باید در CAFE_LOOKUP_PATHS باشد')


class EndpointFailClosedWithoutCafeTests(TestCase):
    """کاربرِ لاگین‌شدهٔ بدون کافه: هیچ نقطهٔ ورود حواله/انبار داده نمی‌دهد"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='no_cafe_user', password='testpass123', role='store')
        self.client.login(username='no_cafe_user', password='testpass123')

        # دنیا وجود دارد ولی کاربر کافه ندارد
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.wh = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=1)
        self.stock = StockItem.objects.create(
            product=self.product, warehouse=self.wh, quantity=10, unit=self.unit)
        self.voucher = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number='ISS-00001', source_warehouse=self.wh,
            created_by=self.user)

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'main', **kwargs})

    def test_voucher_detail_404_without_cafe(self):
        r = self.client.get(self._url('voucher_detail', voucher_id=self.voucher.id))
        self.assertEqual(r.status_code, 404)

    def test_warehouse_inventory_check_404_without_cafe(self):
        r = self.client.get(
            self._url('warehouse_inventory_check', warehouse_id=self.wh.id))
        self.assertEqual(r.status_code, 404)

    def test_warehouse_inventory_submit_404_without_cafe(self):
        r = self.client.post(
            self._url('warehouse_inventory_submit', warehouse_id=self.wh.id),
            data=json.dumps({'items': [{'stock_item_id': self.stock.id,
                                        'physical_qty': '5'}]}),
            content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_api_product_stock_history_404_without_cafe(self):
        r = self.client.get(self._url(
            'api_product_stock_history',
            warehouse_id=self.wh.id, stock_item_id=self.stock.id))
        self.assertEqual(r.status_code, 404)


class CrossCafeIDORTests(TestCase):
    """شناسه‌های کافهٔ دیگر در نقاط ورود حواله/انبار → 404/400 — هرگز نشت"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='manager_a', password='testpass123', role='manager')
        self.cafe_a = Cafe.objects.create(name='A', code='a', is_active=True)
        self.cafe_b = Cafe.objects.create(name='B', code='b', is_active=True)
        self.user.cafe = self.cafe_a
        self.user.save()
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='manager_a', password='testpass123')

        self.unit_a = Unit.objects.create(
            name='Kilogram', symbol='kg', cafe=self.cafe_a)
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe_a, code='whA', name='انبار A', is_active=True)
        self.product_a = Product.objects.create(
            name='Sugar', unit=self.unit_a, cafe=self.cafe_a, min_stock=1)
        self.stock_a = StockItem.objects.create(
            product=self.product_a, warehouse=self.wh_a, quantity=10,
            unit=self.unit_a)
        self.voucher_a = WarehouseVoucher.objects.create(
            cafe=self.cafe_a, voucher_type='issue', status='submitted',
            voucher_number='ISS-00001', source_warehouse=self.wh_a,
            created_by=self.user)

        # کافهٔ B با دادهٔ کامل — نباید از URL های کافهٔ A قابل دسترسی باشد
        self.unit_b = Unit.objects.create(
            name='Gram', symbol='g', cafe=self.cafe_b)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe_b, code='whB', name='انبار B', is_active=True)
        self.product_b = Product.objects.create(
            name='Tea', unit=self.unit_b, cafe=self.cafe_b, min_stock=1)
        self.stock_b = StockItem.objects.create(
            product=self.product_b, warehouse=self.wh_b, quantity=3,
            unit=self.unit_b)
        self.voucher_b = WarehouseVoucher.objects.create(
            cafe=self.cafe_b, voucher_type='issue', status='submitted',
            voucher_number='ISS-00002', source_warehouse=self.wh_b,
            created_by=self.user)

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'a', **kwargs})

    def test_warehouse_edit_foreign_warehouse_404(self):
        r = self.client.get(self._url('warehouse_edit', warehouse_id=self.wh_b.id))
        self.assertEqual(r.status_code, 404)

    def test_warehouse_toggle_status_foreign_warehouse_404(self):
        r = self.client.post(
            self._url('warehouse_toggle_status', warehouse_id=self.wh_b.id))
        self.assertEqual(r.status_code, 404)

    def test_warehouse_inventory_check_foreign_warehouse_404(self):
        r = self.client.get(
            self._url('warehouse_inventory_check', warehouse_id=self.wh_b.id))
        self.assertEqual(r.status_code, 404)

    def test_warehouse_inventory_submit_foreign_warehouse_404(self):
        r = self.client.post(
            self._url('warehouse_inventory_submit', warehouse_id=self.wh_b.id),
            data=json.dumps({'items': []}), content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_warehouse_inventory_submit_foreign_stock_item_400(self):
        """موجودیِ انبارِ کافهٔ دیگر در ثبت انبارگردانی → 400 و بدون تغییر"""
        r = self.client.post(
            self._url('warehouse_inventory_submit', warehouse_id=self.wh_a.id),
            data=json.dumps({'items': [{'stock_item_id': self.stock_b.id,
                                        'physical_qty': '5'}]}),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, 3)  # دست‌نخورده
        self.assertEqual(WarehouseVoucher.objects.filter(cafe=self.cafe_b,
                                                         voucher_type='adjustment').count(), 0)

    def test_api_history_foreign_stock_item_404(self):
        r = self.client.get(self._url(
            'api_product_stock_history',
            warehouse_id=self.wh_a.id, stock_item_id=self.stock_b.id))
        self.assertEqual(r.status_code, 404)

    def test_voucher_detail_foreign_voucher_404(self):
        r = self.client.get(self._url('voucher_detail', voucher_id=self.voucher_b.id))
        self.assertEqual(r.status_code, 404)

    def test_voucher_create_foreign_warehouse_404(self):
        """انبارِ کافهٔ دیگر به‌عنوان مبدأ در POST حواله → 404 (قبل از هر نوشته)"""
        before = WarehouseVoucher.objects.count()  # ۲ حوالهٔ setUp (یکی از هر کافه)
        r = self.client.post(
            self._url('voucher_create', voucher_type='issue'),
            data={'source_warehouse': str(self.wh_b.id)})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(WarehouseVoucher.objects.count(), before)  # هیچ حوالهٔ جدیدی

    def test_own_cafe_objects_still_reachable(self):
        """کنترل: دادهٔ کافهٔ خودِ کاربر با همان URL ها در دسترس است"""
        r = self.client.get(self._url('voucher_detail', voucher_id=self.voucher_a.id))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(self._url('warehouse_edit', warehouse_id=self.wh_a.id))
        self.assertEqual(r.status_code, 200)


class VoucherCreateSubmittedUnitScopingTests(TestCase):
    """شناسهٔ واحدِ (unit) ارسالی در فرم حواله باید متعلق به کافهٔ فعلی باشد

    (شِمای جدید: پارسِ قلم‌ها پیش از هر نوشته انجام می‌شود؛ جستجوی واحد
    با سنجاقِ کافهٔ فعلی است و واحدِ کافهٔ دیگر → خطای پارس → فرم دوباره)
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='store_a', password='testpass123', role='store')
        self.cafe_a = Cafe.objects.create(name='A', code='a', is_active=True)
        self.cafe_b = Cafe.objects.create(name='B', code='b', is_active=True)
        self.user.cafe = self.cafe_a
        self.user.save()
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='store_a', password='testpass123')

        self.unit_a = Unit.objects.create(
            name='Kilogram', symbol='kg', cafe=self.cafe_a)
        self.unit_b = Unit.objects.create(
            name='Gram', symbol='g', cafe=self.cafe_b)
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe_a, code='whA', name='انبار A', is_active=True)
        self.product_a = Product.objects.create(
            name='Sugar', unit=self.unit_a, cafe=self.cafe_a, min_stock=1)
        self.url = reverse('voucher_create', kwargs={'cafe_code': 'a',
                                                     'voucher_type': 'issue'})

    def _post(self, unit_id, **extra):
        data = {
            'source_warehouse': str(self.wh_a.id),
            'product_id[]': [str(self.product_a.id)],
            'unit_id[]': [str(unit_id)],
            'base_quantity[]': ['1'],
            'unit_price[]': ['0'],
            'item_description[]': ['t'],
            'action': 'draft',  # پیش‌نویس: بدون نهایی‌سازی — فقط مسیر سنجش پارس
        }
        data.update(extra)
        return self.client.post(self.url, data=data)

    def test_foreign_unit_id_is_rejected(self):
        """unit کافهٔ دیگر در POST حواله → جستجو با کافهٔ فعلی انجام می‌شود
        (بدون سنجاقِ واحدِ کافهٔ دیگر) و حواله‌ای ثبت نمی‌شود"""
        with patch('purchases.views.Unit.objects.get',
                   wraps=Unit.objects.get) as unit_get:
            r = self._post(self.unit_b.id)
            # جستجوی واحد به کافهٔ A قفل شده — unit کافهٔ B پیدا نمی‌شود
            unit_get.assert_called_with(id=self.unit_b.id, cafe=self.cafe_a)
        # فرم با پیام خطا دوباره رندر می‌شود؛ هیچ حوالهٔ واقعی ساخته نشده
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)

    def test_own_unit_id_passes_scoped_lookup(self):
        """unit کافهٔ خود → جستجو با همان کافه انجام می‌شود و پیش‌نویس ساخته می‌شود"""
        with patch('purchases.views.Unit.objects.get',
                   wraps=Unit.objects.get) as unit_get:
            r = self._post(self.unit_a.id)
            unit_get.assert_called_with(id=self.unit_a.id, cafe=self.cafe_a)
        # پیش‌نویس حواله ساخته و به صفحهٔ جزئیات هدایت می‌شود
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WarehouseVoucher.objects.filter(cafe=self.cafe_a,
                                                         status='draft').count(), 1)
