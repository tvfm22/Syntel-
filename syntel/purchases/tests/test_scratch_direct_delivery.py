# ─────────────────────────────────────────────────────────────────────────────
# Scratch — حوالهٔ «تحویل مستقیم» به بخش (درخواست: 2026-09-09)
#
#  - نوع حوالهٔ جدید direct_delivery از فرم ثبت حوالهٔ مسئول انبار
#  - انتخاب «بخش گیرنده» الزامی است (بدون آن → خطا و سند ساخته نمی‌شود)
#  - کالا از انبارِ انتخابی (محل عبور) می‌گذرد: ورودِ کامل و بلافاصله خروجِ کامل
#    → موجودیِ خالصِ انبار تغییر نمی‌کند ولی دو ردیف «in» و «out» در سابقه ثبت می‌شود
#  - کالای تازه‌خریدِ بدون موجودیِ قبلی: ردیفِ موجودیِ صفر ساخته می‌شود
#  - در لیست حواله‌ها «تحویل به: <بخش>» نمایش و فیلتر بخش آن را پیدا می‌کند
#  - برگشت: حوالهٔ آینه‌ایِ DLV…-R با همان بخش؛ موجودی همچنان خالصِ صفر
# ─────────────────────────────────────────────────────────────────────────────
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Department, Product, ProductBrand, StockItem, StockTransaction, Unit,
    Warehouse, WarehouseVoucher,
)

User = get_user_model()


def _set_cafe_session(client, cafe):
    session = client.session
    session['selected_cafe_id'] = cafe.id
    session['selected_cafe_code'] = cafe.code
    session.save()


class DirectDeliveryBase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='store_dlv', password='testpass123', role='store')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.user.cafe = self.cafe
        self.user.save()
        _set_cafe_session(self.client, self.cafe)
        self.client.login(username='store_dlv', password='testpass123')

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe, code='whA', name='انبار مرکزی', is_active=True)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe, code='whB', name='انبار دوم', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=1)
        self.stock_a = StockItem.objects.create(
            product=self.product, warehouse=self.wh_a, quantity=10, unit=self.unit)
        self.dept = Department.objects.create(
            name='بخش پذیرایی', cafe=self.cafe, is_active=True)

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'main', **kwargs})

    def _create_dlv(self, qty='5', dest=None, department=None, product=None,
                    action='submit'):
        data = {
            'action': action,
            'dest_warehouse': str((dest or self.wh_a).id),
            'product_id[]': [str((product or self.product).id)],
            'product_brand_id[]': [''],
            'stock_item_id[]': [''],
            'unit_id[]': [str(self.unit.id)],
            'base_quantity[]': [qty],
            'unit_price[]': ['1000'],
            'item_description[]': ['قلم آزمایشی تحویل مستقیم'],
        }
        if department is not None:
            data['department'] = str(department.id)
        return self.client.post(
            self._url('voucher_create', voucher_type='direct_delivery'), data=data)


class DirectDeliveryCreateTests(DirectDeliveryBase):

    def test_create_finalizes_with_pass_through_net_zero(self):
        r = self._create_dlv(qty='5', dest=self.wh_a, department=self.dept)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        self.assertEqual(v.status, 'finalized')
        self.assertTrue(v.voucher_number.startswith('DLV-'))
        self.assertEqual(v.department_id, self.dept.id)
        self.assertEqual(v.destination_warehouse_id, self.wh_a.id)
        self.assertIsNone(v.source_warehouse_id)
        # موجودیِ خالص تغییر نمی‌کند؛ هر دو حرکت ثبت می‌شود
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        txs = list(StockTransaction.objects.filter(voucher=v).order_by('id'))
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0].transaction_type, 'in')
        self.assertEqual(txs[0].quantity, Decimal('5'))
        self.assertEqual(txs[0].previous_quantity, Decimal('10'))
        self.assertEqual(txs[0].new_quantity, Decimal('15'))
        self.assertEqual(txs[1].transaction_type, 'out')
        self.assertEqual(txs[1].quantity, Decimal('5'))
        self.assertEqual(txs[1].previous_quantity, Decimal('15'))
        self.assertEqual(txs[1].new_quantity, Decimal('10'))

    def test_create_on_other_warehouse_untouched(self):
        r = self._create_dlv(qty='2', dest=self.wh_b, department=self.dept)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        self.assertEqual(v.destination_warehouse_id, self.wh_b.id)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))  # انبار دیگر دست‌نخورده

    def test_fresh_product_gets_zero_stock_row_after_pass(self):
        fresh = Product.objects.create(
            name='Flour', unit=self.unit, cafe=self.cafe, min_stock=0)
        r = self._create_dlv(qty='4', dest=self.wh_a, department=self.dept,
                             product=fresh)
        self.assertEqual(r.status_code, 302)
        si = StockItem.objects.get(product=fresh, warehouse=self.wh_a)
        self.assertEqual(si.quantity, Decimal('0'))  # ساخته شد و خالصِ صفر ماند
        txs = StockTransaction.objects.filter(voucher__voucher_type='direct_delivery')
        self.assertEqual(txs.count(), 2)

    def test_missing_department_rejected(self):
        r = self._create_dlv(qty='5', dest=self.wh_a, department=None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
        self.assertContains(r, 'بخش گیرنده را انتخاب کنید')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        # بدون تراکنش
        self.assertEqual(StockTransaction.objects.count(), 0)

    def test_draft_action_leaves_no_movement(self):
        r = self._create_dlv(qty='5', dest=self.wh_a, department=self.dept,
                             action='draft')
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        self.assertEqual(v.status, 'draft')
        self.assertEqual(StockTransaction.objects.count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))

    def test_create_page_shows_pill_and_department_field(self):
        r = self.client.get(
            self._url('voucher_create', voucher_type='direct_delivery'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'بخش گیرنده')
        # فرمِ رسید نباید فیلد بخش را نشان دهد
        r2 = self.client.get(self._url('voucher_create', voucher_type='receipt'))
        self.assertEqual(r2.status_code, 200)
        self.assertNotContains(r2, 'بخش گیرنده')


class DirectDeliveryListAndReverseTests(DirectDeliveryBase):

    def test_list_shows_department_and_department_filter_finds_it(self):
        r = self._create_dlv(qty='3', dest=self.wh_a, department=self.dept)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        page = self.client.get(self._url('voucher_list'))
        self.assertContains(page, v.voucher_number)
        self.assertContains(page, f'تحویل به: {self.dept.name}')
        filtered = self.client.get(
            self._url('voucher_list'), {'department': str(self.dept.id)})
        self.assertContains(filtered, v.voucher_number)

    def test_detail_shows_delivered_department(self):
        self._create_dlv(qty='3', dest=self.wh_a, department=self.dept)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        page = self.client.get(self._url('voucher_detail', voucher_id=v.id))
        self.assertContains(page, self.dept.name)
        self.assertContains(page, 'کالا تحویل داده شد')

    def test_reverse_mirror_keeps_department_and_net_zero(self):
        self._create_dlv(qty='5', dest=self.wh_a, department=self.dept)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        r = self.client.post(
            self._url('voucher_reverse', voucher_id=v.id),
            data={'reason': 'ثبت اشتباه'})
        self.assertEqual(r.status_code, 302)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        self.assertEqual(mirror.voucher_type, 'direct_delivery')
        self.assertTrue(mirror.voucher_number.endswith('-R'))
        self.assertEqual(mirror.department_id, self.dept.id)
        self.assertEqual(mirror.destination_warehouse_id, self.wh_a.id)
        self.assertEqual(mirror.status, 'finalized')
        v.refresh_from_db()
        self.assertEqual(v.status, 'reversed')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        # هر دو سند دو حرکت داشته‌اند — چهار ردیف سابقه، موجودی همان ۱۰
        txs = list(StockTransaction.objects.filter(voucher=mirror).order_by('id'))
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0].transaction_type, 'in')
        self.assertEqual(txs[0].new_quantity, Decimal('15'))
        self.assertEqual(txs[1].transaction_type, 'out')
        self.assertEqual(txs[1].new_quantity, Decimal('10'))
