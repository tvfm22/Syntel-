# ─────────────────────────────────────────────────────────────────────────────
# Prompt 5 — Tenant/warehouse scoping و سخت‌سازی IDOR برای جریان‌های حواله
#
#  - get_scoped_object_or_404 / filter_by_cafe: شکستِ بسته (fail-closed) —
#    بدون کافهٔ فعال یا مدلِ بدون مسیرِ کافه، هرگز fallback بی‌scope وجود ندارد
#    (404 / queryset خالی).
#  - همهٔ نقاط ورود حواله و مدیریت انبار: بدون کافه یا با شناسهٔ کافهٔ دیگر → 404.
#  - شناسه‌های ارسالی (انبار، محصول، دسته، موجودی، حواله) فقط در کافهٔ فعلی
#    معتبرند — کافهٔ دیگر هرگز قابل دسترسی نیست (IDOR).
#
# Prompt 10 — نوشتن روی موجودی از «مسیرهای تامین از انبار»:
#  - پیش از این چهار مسیر (درخواست انبار، لیست خرید، کالاهای موجود، داشبورد
#    مسئول انبار) موجودی را با id خامِ POST و بدون سنجاقِ کافه کم می‌کردند →
#    مدیر کافهٔ A می‌توانست موجودی کافهٔ B را دستکاری کند.
#  - حالا همه از apply_stock_delta عبور می‌کنند که cafe اجباری دارد و
#    StockTransaction را هم‌زاد با کسر می‌سازد.
# ─────────────────────────────────────────────────────────────────────────────
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Category, OfficerAssignment, Product, PurchaseItem, PurchaseRequest,
    StockItem, StockTransaction, Unit, Warehouse, WarehouseRequest,
    WarehouseRequestItem, WarehouseVoucher,
)
from purchases.services.inventory import (
    InsufficientStockError, StockScopeError, apply_stock_delta,
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
                           'Product', 'Unit', 'Category',
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


class ApplyStockDeltaScopeTests(TestCase):
    """apply_stock_delta — تنها نقطهٔ مجازِ تغییر موجودی خارج از جریان حواله

    پیش از این، تغییر موجودی از راه `_change_stock_quantity` (بدون cafe) انجام
    می‌شد؛ هر id خامی از POST کافی بود تا موجودیِ کافهٔ دیگری کم شود.
    """

    def setUp(self):
        self.cafe_a = Cafe.objects.create(name='A', code='a', is_active=True)
        self.cafe_b = Cafe.objects.create(name='B', code='b', is_active=True)
        self.user = User.objects.create_user(
            username='wh_a', password='testpass123', role='warehouse', cafe=self.cafe_a)

        self.unit_a = Unit.objects.create(
            name='Kilogram', symbol='kg', cafe=self.cafe_a)
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe_a, code='whA', name='انبار A', is_active=True,
            is_supply_enabled=True)
        self.product_a = Product.objects.create(
            name='Sugar', unit=self.unit_a, cafe=self.cafe_a, min_stock=1)
        self.stock_a = StockItem.objects.create(
            product=self.product_a, warehouse=self.wh_a, quantity=Decimal('50'),
            unit=self.unit_a)

        # کافهٔ B — هرگز نباید از مسیر کافهٔ A لمس شود
        self.unit_b = Unit.objects.create(
            name='Gram', symbol='g', cafe=self.cafe_b)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe_b, code='whB', name='انبار B', is_active=True,
            is_supply_enabled=True)
        self.product_b = Product.objects.create(
            name='Tea', unit=self.unit_b, cafe=self.cafe_b, min_stock=1)
        self.stock_b = StockItem.objects.create(
            product=self.product_b, warehouse=self.wh_b, quantity=Decimal('100'),
            unit=self.unit_b)
        # موجودیِ بدون انبار (لجacy) — باید رد شود، نه کرش
        self.stock_orphan = StockItem.objects.create(
            product=self.product_b, warehouse=None, quantity=Decimal('7'),
            unit=self.unit_b)
        self.stock_no_warehouse_wh = StockItem.objects.create(
            product=self.product_a, warehouse=None, quantity=Decimal('5'),
            unit=self.unit_a)

    def _txn_count(self):
        return StockTransaction.objects.count()

    def test_foreign_cafe_stock_is_rejected_and_untouched(self):
        """موجودی کافهٔ دیگر با cafe کافهٔ A → StockScopeError، بدون تغییر و بدون سند"""
        before_txn = self._txn_count()
        with self.assertRaises(StockScopeError):
            apply_stock_delta(self.stock_b, Decimal('-10'), cafe=self.cafe_a,
                              created_by=self.user, reference_type='warehouse_request',
                              reference_id=1)
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, Decimal('100.0000'))
        self.assertEqual(self._txn_count(), before_txn)

    def test_without_cafe_is_rejected(self):
        """بدون کافهٔ فعال → StockScopeError (هیچ fallback بی‌scope نداریم)"""
        with self.assertRaises(StockScopeError):
            apply_stock_delta(self.stock_a, Decimal('-1'), cafe=None)

    def test_stock_item_without_warehouse_is_rejected(self):
        """موجودیِ بدون انبار نمی‌تواند کافه داشته باشد → رد، نه کرش"""
        with self.assertRaises(StockScopeError):
            apply_stock_delta(self.stock_no_warehouse_wh, Decimal('-1'),
                              cafe=self.cafe_a, created_by=self.user)
        self.stock_no_warehouse_wh.refresh_from_db()
        self.assertEqual(self.stock_no_warehouse_wh.quantity, Decimal('5.0000'))

    def test_own_cafe_deduction_records_audit_row(self):
        """کسر مجاز: موجودی کم می‌شود و ردیف ممیزی با قبل/بعد درست ساخته می‌شود"""
        txn = apply_stock_delta(
            self.stock_a, Decimal('-10'), cafe=self.cafe_a, created_by=self.user,
            reference_type='warehouse_request', reference_id=99, description='تست')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('40.0000'))
        self.assertEqual(txn.transaction_type, 'out')
        self.assertEqual(txn.quantity, Decimal('10.0000'))
        self.assertEqual(txn.previous_quantity, Decimal('50.0000'))
        self.assertEqual(txn.new_quantity, Decimal('40.0000'))
        self.assertEqual(txn.reference_type, 'warehouse_request')
        self.assertEqual(txn.reference_id, 99)

    def test_insufficient_stock_leaves_nothing_behind(self):
        """کسر بیش از موجودی → InsufficientStockError و بدون ردیف ممیزی"""
        before_txn = self._txn_count()
        with self.assertRaises(InsufficientStockError):
            apply_stock_delta(self.stock_a, Decimal('-999'), cafe=self.cafe_a,
                              created_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('50.0000'))
        self.assertEqual(self._txn_count(), before_txn)


class _StockWritePathTestsMixin:
    """دنیای دوکافه‌ای + دادهٔ هر چهار مسیر نوشتن روی موجودی"""

    role = 'manager'          # نقش کاربرِ مهاجم/ذی‌نفع
    supply_status = 'pending'  # وضعیت درخواست خرید برای مسیرهای لیست خرید و داشبورد

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='user_a', password='testpass123', role=self.role)
        self.cafe_a = Cafe.objects.create(name='A', code='a', is_active=True)
        self.cafe_b = Cafe.objects.create(name='B', code='b', is_active=True)
        self.user.cafe = self.cafe_a
        self.user.save()
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='user_a', password='testpass123')

        self.unit_a = Unit.objects.create(
            name='Kilogram', symbol='kg', cafe=self.cafe_a)
        self.unit_b = Unit.objects.create(
            name='Gram', symbol='g', cafe=self.cafe_b)

        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe_a, code='whA', name='انبار A', is_active=True,
            is_supply_enabled=True)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe_b, code='whB', name='انبار B', is_active=True,
            is_supply_enabled=True)

        self.product_a = Product.objects.create(
            name='Sugar', unit=self.unit_a, cafe=self.cafe_a, min_stock=1)
        self.product_b = Product.objects.create(
            name='Tea', unit=self.unit_b, cafe=self.cafe_b, min_stock=1)

        self.stock_a = StockItem.objects.create(
            product=self.product_a, warehouse=self.wh_a, quantity=Decimal('50'),
            unit=self.unit_a)
        self.stock_b = StockItem.objects.create(
            product=self.product_b, warehouse=self.wh_b, quantity=Decimal('100'),
            unit=self.unit_b)

        # درخواست کافهٔ A با یک قلم — منبعِ ردیف‌های فرم هر مسیر
        self.request_a = PurchaseRequest.objects.create(
            user=self.user, cafe=self.cafe_a, status=self.supply_status)
        self.item_a = PurchaseItem.objects.create(
            request=self.request_a, product=self.product_a, name='Sugar',
            quantity=20, unit='Kilogram', supply_status='pending')

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'a', **kwargs})

    def _assert_untouched(self):
        """موجودی کافهٔ B دست‌نخورده + هیچ سند خروجی برایش ثبت نشده"""
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, Decimal('100.0000'))
        self.assertEqual(
            StockTransaction.objects.filter(stock_item=self.stock_b).count(), 0)


class ProcessWarehouseRequestScopeTests(_StockWritePathTestsMixin, TestCase):
    """مسیر ۱ — process_warehouse_request: stock_item از POST
    (پیش‌تر با id خام کم می‌شد؛ حالا get_scoped_object_or_404)"""

    role = 'warehouse'

    def setUp(self):
        super().setUp()
        self.wh_request = WarehouseRequest.objects.create(
            user=self.user, cafe=self.cafe_a, status='pending')
        self.wh_item = WarehouseRequestItem.objects.create(
            warehouse_request=self.wh_request, product=self.product_a,
            name='Sugar', quantity=Decimal('10'), unit='Kilogram')

    def _post(self, stock_item, qty='10'):
        return self.client.post(
            self._url('process_warehouse_request', request_id=self.wh_request.id),
            data={
                f'supply_status_{self.wh_item.id}': 'from_stock',
                f'stock_item_{self.wh_item.id}': str(stock_item.id),
                f'supplied_qty_{self.wh_item.id}': qty,
                'action': 'submit',
            })

    def test_foreign_stock_item_rejected(self):
        """موجودیِ کافهٔ B → قلم تامین نمی‌شود، کسری رخ نمی‌دهد و کافهٔ B دست‌نخورده می‌ماند

        (خطای جستجو در فرم به پیام خطا تبدیل می‌شود و صفحه دوباره رندر می‌گردد؛
        مهم این است که هیچ نوشتنی روی موجودی انجام نشود.)
        """
        r = self._post(self.stock_b)
        self.assertEqual(r.status_code, 200)
        self._assert_untouched()
        self.wh_item.refresh_from_db()
        self.assertIsNone(self.wh_item.stock_item)
        self.assertIsNone(self.wh_item.supplied_quantity)

    def test_own_stock_item_deducts(self):
        """کنترل: موجودی کافهٔ خود → کسر انجام می‌شود"""
        r = self._post(self.stock_a, qty='10')
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('40.0000'))
        self.assertEqual(
            StockTransaction.objects.filter(
                stock_item=self.stock_a, reference_type='warehouse_request').count(), 1)


class PurchaseListStockSupplyScopeTests(_StockWritePathTestsMixin, TestCase):
    """مسیر ۲ — purchase_list_with_stock_supply: stock_sid از POST
    (پیش‌تر با id خام کم می‌شد؛ حالا فیلتر warehouse__cafe=cafe)"""

    role = 'purchasing'
    supply_status = 'purchased'

    def _post(self, stock_item, sids=None, qtys=None):
        return self.client.post(
            self._url('purchase_list_with_stock_supply'),
            data={
                'item_ids': [str(self.item_a.id)],
                f'action_{self.item_a.id}': 'from_stock',
                f'stock_sid_{self.item_a.id}': sids if sids else [str(stock_item.id)],
                f'stock_qty_{self.item_a.id}': qtys if qtys else ['5'],
            })

    def test_foreign_stock_item_rejected(self):
        """موجودیِ کافهٔ B → پیام خطا، بدون کسر و بدون تغییر وضعیت قلم"""
        r = self._post(self.stock_b)
        self.assertEqual(r.status_code, 302)
        self._assert_untouched()
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.supply_status, 'pending')
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='purchase_item').count(), 0)

    def test_own_cafe_stock_deducts(self):
        """کنترل: موجودی کافهٔ خود → کسر و ثبت وضعیت «تامین از انبار»"""
        r = self._post(self.stock_a)
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('45.0000'))
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.supply_status, 'from_stock')

    def test_foreign_stock_item_mixed_with_own_is_rejected_whole(self):
        """ردیف بیگانه در میان ردیف‌های مجاز → کل قلم رد می‌شود (هیچ کسری)"""
        r = self._post(self.stock_a, sids=[str(self.stock_a.id), str(self.stock_b.id)],
                       qtys=['5', '5'])
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('50.0000'))
        self._assert_untouched()


class WarehouseAvailableProductsScopeTests(_StockWritePathTestsMixin, TestCase):
    """مسیر ۳ — warehouse_available_products: product_id/warehouse_id/brand_id
    از POST (پیش‌تر با id خامِ انبار و محصولِ کافهٔ دیگر کم می‌شد)"""

    role = 'warehouse'

    def _post(self, product, warehouse, qty='5', status='from_stock', brand_id=''):
        return self.client.post(
            self._url('warehouse_available_products'),
            data={
                'product_id': [str(product.id)],
                'warehouse_id': [str(warehouse.id)],
                'brand_id': [str(brand_id)],
                'qty': [qty],
                'status': [status],
            })

    def test_foreign_product_and_warehouse_rejected(self):
        """محصول و انبار کافهٔ B → خطای اعتبارسنجی، موجودی دست‌نخورده"""
        r = self._post(self.product_b, self.wh_b)
        self.assertEqual(r.status_code, 200)  # فرم با پیام خطا دوباره رندر می‌شود
        self._assert_untouched()
        self.assertEqual(WarehouseRequest.objects.filter(cafe=self.cafe_a).count(), 0)
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='warehouse_request').count(), 0)

    def test_own_product_and_warehouse_deducts(self):
        """کنترل: محصول و انبار کافهٔ خود → کسر و ثبت درخواست انبار"""
        r = self._post(self.product_a, self.wh_a, qty='5')
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('45.0000'))
        self.assertEqual(WarehouseRequest.objects.filter(cafe=self.cafe_a).count(), 1)
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='warehouse_request').count(), 1)

    def test_own_product_with_foreign_warehouse_rejected(self):
        """محصول کافهٔ A + انبار کافهٔ B → رد (انبار کافهٔ دیگر)"""
        r = self._post(self.product_a, self.wh_b)
        self.assertEqual(r.status_code, 200)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('50.0000'))
        self._assert_untouched()


class WarehouseOfficerDashboardScopeTests(_StockWritePathTestsMixin, TestCase):
    """مسیر ۴ — داشبورد مسئول انبار (action=fulfill): stock_item_id/qty از POST
    (پیش‌تر با id خام کم می‌شد؛ حالا فیلتر warehouse__cafe=cafe)

    نکتهٔ مهم: حتی اگر خودِ قلم واقعاً مال کافهٔ A باشد، باز هم نمی‌توان با
    اسلایس موجودیِ کافهٔ B آن را تامین کرد — همان حفرهٔ اصلی.
    """

    role = 'warehouse'

    def setUp(self):
        super().setUp()
        self.item_a.officer_assignments.create(
            target_role='warehouse', status='pending',
            assigned_by=self.user, assigned_to=self.user)

    def _url(self, name, **kwargs):
        # داشبورد بدون پارامتر day به «امروز» ریدایرکت می‌شود؛ صریح می‌فرستیم
        url = super()._url(name, **kwargs)
        return url + '?day=' + timezone.localtime(timezone.now()).date().isoformat()

    def _post(self, stock_item, qty='5', action='fulfill'):
        return self.client.post(
            self._url('warehouse_officer_dashboard'),
            data={'action': action, 'item_id': str(self.item_a.id),
                  'stock_item_id': [str(stock_item.id)], 'qty': [qty]})

    def test_foreign_stock_item_rejected(self):
        """اسلایس موجودیِ کافهٔ B → پیام خطا، بدون کسر و بدون تخصیص fulfilled"""
        r = self._post(self.stock_b)
        self.assertEqual(r.status_code, 302)
        self._assert_untouched()
        self.assertEqual(
            OfficerAssignment.objects.filter(item=self.item_a,
                                             status='fulfilled').count(), 0)

    def test_foreign_stock_item_rejected_even_for_same_product(self):
        """موجودیِ کافهٔ B برای *همان* محصول کافهٔ A → همچنان رد

        این تست عمداً از گاردِ «موجودی با کالا مطابقت ندارد» عبور می‌کند تا
        گاردِ کافه‌محور تنها چیزی باشد که جلوی کسر را می‌گیرد — کافهٔ B موجودیِ
        همان محصول را دارد و تنها مانع، تعلقِ آن به کافهٔ دیگر است.
        """
        stock_a_dup = StockItem.objects.create(
            product=self.product_a, warehouse=self.wh_b, quantity=Decimal('100'),
            unit=self.unit_a)
        r = self._post(stock_a_dup)
        self.assertEqual(r.status_code, 302)
        self._assert_untouched()
        stock_a_dup.refresh_from_db()
        self.assertEqual(stock_a_dup.quantity, Decimal('100.0000'))
        self.assertEqual(
            OfficerAssignment.objects.filter(item=self.item_a,
                                             status='fulfilled').count(), 0)

    def test_own_stock_item_deducts(self):
        """کنترل: موجودی کافهٔ خود → کسر و تخصیص fulfilled"""
        r = self._post(self.stock_a, qty='5')
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('45.0000'))
        self.assertEqual(
            OfficerAssignment.objects.filter(item=self.item_a,
                                             status='fulfilled').count(), 1)


class OfficerDashboardReopenScopeTests(_StockWritePathTestsMixin, TestCase):
    """بازگشایی قلم (action=reopen): تراکنش‌های تامین قبلی باید فقط وقتی برگردند
    که موجودی‌شان واقعاً به همین کافه تعلق دارد"""

    role = 'warehouse'

    def setUp(self):
        super().setUp()
        self.assignment = OfficerAssignment.objects.create(
            item=self.item_a, target_role='warehouse', status='fulfilled',
            assigned_by=self.user, assigned_to=self.user)
        # موجودی کافهٔ B پایین می‌آید و سندِ خروجی به نام همین قلم ثبت می‌شود
        # (دادهٔ آلوده/لجcy) — بازگشایی نباید موجودی کافهٔ B را بالا ببرد
        apply_stock_delta(self.stock_b, Decimal('-10'), cafe=self.cafe_b,
                          created_by=self.user, reference_type='officer_assignment',
                          reference_id=self.item_a.id, description='دادهٔ آلوده')
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, Decimal('90.0000'))

    def _url(self, name, **kwargs):
        url = super()._url(name, **kwargs)
        return url + '?day=' + timezone.localtime(timezone.now()).date().isoformat()

    def test_reopen_does_not_credit_foreign_stock(self):
        """بازگشایی: موجودی کافهٔ B دست‌نخورده می‌ماند (سند هم بازنمی‌گردد)"""
        r = self.client.post(
            self._url('warehouse_officer_dashboard'),
            data={'action': 'reopen', 'item_id': str(self.item_a.id),
                  'reason': 'تست بازگشایی'})
        self.assertEqual(r.status_code, 302)
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, Decimal('90.0000'))
        # ردیف ممیزیِ نامرتبط حذف نمی‌شود و سند برگشت هم ساخته نمی‌شود
        self.assertEqual(
            StockTransaction.objects.filter(stock_item=self.stock_b).count(), 1)
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='officer_assignment_reopen').count(), 0)
        # تخصیص حذف نمی‌شود؛ با وضعیت terminal «بازگشایی‌شده» نگه داشته می‌شود
        assignment = OfficerAssignment.objects.get(item=self.item_a)
        self.assertEqual(assignment.status, 'reopened')
