# ─────────────────────────────────────────────────────────────────────────────
# Prompt 4 — تبدیل جریان‌های در دامنه به حوالهٔ انبار از طریق سرویس مرکزی
#
#  - ورود فاکتور انبار → حوالهٔ ورود (receipt) از سیگنال فاکتور
#  - انبارگردانی → حوالهٔ تعدیل (adjustment) از سرویس مرکزی (به‌جای نوشتن مطلق)
#  - رگرسیون: تامین درخواست انبار (process_warehouse_request) دست‌نخورده می‌ماند
#    و دقیقاً مثل قبل موجودی را کم می‌کند — بدون هیچ حواله‌ای.
# ─────────────────────────────────────────────────────────────────────────────
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Invoice, InvoiceItem, Product, Requester, StockItem,
    StockTransaction, Supplier, Unit, Warehouse, WarehouseRequest,
    WarehouseRequestItem, WarehouseVoucher,
)

User = get_user_model()


def _set_cafe_session(client, cafe):
    session = client.session
    session['selected_cafe_id'] = cafe.id
    session['selected_cafe_code'] = cafe.code
    session.save()


class WarehouseRequestFulfillmentRegressionTests(TestCase):
    """تامین درخواست انبار (درخواست کالا) باید دقیقاً مثل قبل کار کند:

    - موجودی را با همان مکانیزم اتمیک کم کند (بدون دخالت سرویس حواله)
    - ردیف‌های StockTransaction با reference_type='warehouse_request' بسازد
    - هیچ حوالهٔ انباری نسازد
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='purchasing_user', password='testpass123', role='purchasing')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        _set_cafe_session(self.client, self.cafe)
        self.user.cafe = self.cafe
        self.user.save()

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.wh = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, min_stock=1)
        self.stock = StockItem.objects.create(
            product=self.product, warehouse=self.wh, quantity=10, unit=self.unit)

        self.request = WarehouseRequest.objects.create(
            user=self.user, cafe=self.cafe, status='pending')
        self.item = WarehouseRequestItem.objects.create(
            warehouse_request=self.request, product=self.product,
            name='Sugar', quantity=2, unit='kg')

        self.client.login(username='purchasing_user', password='testpass123')

    def test_fulfillment_mutates_stock_exactly_as_before(self):
        """تامین از انبار: موجودی کم می‌شود و تراکنش با ارجاع درخواست ثبت می‌شود"""
        response = self.client.post(reverse('process_warehouse_request',
                                            kwargs={'cafe_code': 'main',
                                                    'request_id': self.request.id}), {
            'action': 'submit',
            f'supply_status_{self.item.id}': 'from_stock',
            f'stock_item_{self.item.id}': str(self.stock.id),
            f'supplied_qty_{self.item.id}': '2',
        })
        self.assertEqual(response.status_code, 302)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('8'))  # 10 - 2

        txs = StockTransaction.objects.filter(
            reference_type='warehouse_request', reference_id=self.request.id)
        self.assertEqual(txs.count(), 1)
        tx = txs.first()
        self.assertEqual(tx.transaction_type, 'out')
        self.assertEqual(tx.quantity, Decimal('2'))
        self.assertEqual(tx.previous_quantity, Decimal('10'))
        self.assertEqual(tx.new_quantity, Decimal('8'))
        self.assertEqual(tx.stock_item_id, self.stock.id)
        self.assertIsNone(tx.voucher_id)  # به حواله ارجاع ندارد — مسیر قدیمی

        # هیچ حوالهٔ انباری ساخته نشده — مسیر تامین از سرویس استفاده نمی‌کند
        self.assertEqual(WarehouseVoucher.objects.count(), 0)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, 'approved_from_stock')

    def test_fulfillment_insufficient_stock_still_rejected(self):
        """کمبود موجودی: مثل قبل رد می‌شود و چیزی تغییر نمی‌کند"""
        response = self.client.post(reverse('process_warehouse_request',
                                            kwargs={'cafe_code': 'main',
                                                    'request_id': self.request.id}), {
            'action': 'submit',
            f'supply_status_{self.item.id}': 'from_stock',
            f'stock_item_{self.item.id}': str(self.stock.id),
            f'supplied_qty_{self.item.id}': '999',
        })
        self.assertEqual(response.status_code, 200)  # با خطا دوباره رندر می‌شود
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        self.assertEqual(StockTransaction.objects.count(), 0)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)


class InvoiceReceiptVoucherTests(TestCase):
    """فاکتور انبار (درخواست‌کننده = انبار): به‌جای نوشتن مستقیم موجودی،
    یک حوالهٔ ورود از طریق سرویس مرکزی ساخته و نهایی می‌شود."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='store_user', password='testpass123', role='purchasing')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        _set_cafe_session(self.client, self.cafe)
        self.user.cafe = self.cafe
        self.user.save()

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.wh = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, min_stock=1)
        self.supplier = Supplier.objects.create(
            name='Supplier Co', cafe=self.cafe)
        self.store_requester = Requester.objects.create(name='store', cafe=self.cafe)

        self.invoice = Invoice.objects.create(
            cafe=self.cafe, invoice_number='INV-TEST-1',
            supplier=self.supplier,
            created_by=self.user, status='draft')
        self.invoice_item = InvoiceItem.objects.create(
            invoice=self.invoice, product=self.product,
            description='Sugar 5kg', requester=self.store_requester,
            quantity=Decimal('5'), unit_price=Decimal('1000'),
            line_total=Decimal('5000'))

    def test_approved_store_invoice_creates_receipt_voucher(self):
        """تایید فاکتور انبار → حوالهٔ ورود نهایی + افزایش موجودی"""
        # پیش از تایید: نه حواله‌ای هست نه تغییری در موجودی
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
        self.assertFalse(StockItem.objects.filter(warehouse=self.wh).exists())

        self.invoice.status = 'approved'
        self.invoice.approved_at = timezone.now()
        self.invoice.save()

        vouchers = WarehouseVoucher.objects.filter(voucher_type='receipt')
        self.assertEqual(vouchers.count(), 1)
        voucher = vouchers.first()
        self.assertEqual(voucher.status, 'finalized')
        self.assertEqual(voucher.destination_warehouse_id, self.wh.id)
        self.assertEqual(voucher.source_object, self.invoice)  # GFK به فاکتور
        self.assertEqual(voucher.created_by, self.user)
        self.assertEqual(voucher.items.count(), 1)

        stock = StockItem.objects.get(warehouse=self.wh, product=self.product)
        self.assertEqual(stock.quantity, Decimal('5'))

        txs = StockTransaction.objects.filter(
            reference_type='warehouse_voucher', voucher=voucher)
        self.assertEqual(txs.count(), 1)
        tx = txs.first()
        self.assertEqual(tx.transaction_type, 'in')
        self.assertEqual(tx.quantity, Decimal('5'))
        self.assertEqual(tx.previous_quantity, Decimal('0'))
        self.assertEqual(tx.new_quantity, Decimal('5'))

    def test_double_save_does_not_double_apply(self):
        """ذخیرهٔ دوبارهٔ فاکتور تاییدشده: نه حوالهٔ دوم ساخته می‌شود نه موجودی دوباره اضافه"""
        self.invoice.status = 'approved'
        self.invoice.approved_at = timezone.now()
        self.invoice.save()
        self.invoice.save()  # post_save دوباره

        self.assertEqual(WarehouseVoucher.objects.filter(voucher_type='receipt').count(), 1)
        stock = StockItem.objects.get(warehouse=self.wh, product=self.product)
        self.assertEqual(stock.quantity, Decimal('5'))

    def test_unapproved_invoice_does_not_create_voucher(self):
        """فاکتور تاییدنشده: هیچ حواله‌ای ساخته نمی‌شود"""
        self.invoice.save()
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
        self.assertFalse(StockItem.objects.filter(warehouse=self.wh).exists())

    def test_non_store_requester_does_not_create_voucher(self):
        """درخواست‌کنندهٔ قلم غیر از انبار: ورود به موجودی ثبت نمی‌شود"""
        kitchen_requester = Requester.objects.create(name='kitchen', cafe=self.cafe)
        self.invoice_item.requester = kitchen_requester
        self.invoice_item.save()
        self.invoice.status = 'approved'
        self.invoice.approved_at = timezone.now()
        self.invoice.save()
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
        self.assertFalse(StockItem.objects.filter(warehouse=self.wh).exists())


class InventoryCountAdjustmentVoucherTests(TestCase):
    """انبارگردانی: به‌جای تنظیم مطلق موجودی، یک حوالهٔ تعدیل (adjustment)
    از سرویس مرکزی ساخته و نهایی می‌شود — با رفع خطای انبارِ اشتباه."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='store_user', password='testpass123', role='store')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        _set_cafe_session(self.client, self.cafe)
        self.user.cafe = self.cafe
        self.user.save()

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe, code='whA', name='انبار اصلی', is_active=True)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe, code='whB', name='انبار فرعی', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, min_stock=1)
        self.si_a = StockItem.objects.create(
            product=self.product, warehouse=self.wh_a, quantity=10, unit=self.unit)
        self.si_other = StockItem.objects.create(
            product=self.product, warehouse=self.wh_b, quantity=7, unit=self.unit)

        self.client.login(username='store_user', password='testpass123')
        self.url = reverse('warehouse_inventory_submit',
                           kwargs={'cafe_code': 'main', 'warehouse_id': self.wh_a.id})

    def _post(self, items):
        return self.client.post(self.url,
                                data=json.dumps({'items': items}),
                                content_type='application/json')

    def test_count_submit_creates_adjustment_voucher(self):
        """ثبت انبارگردانی → حوالهٔ تعدیل نهایی + اعمال دلتا"""
        response = self._post([{'stock_item_id': self.si_a.id, 'physical_qty': '13'}])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['updated'], 1)

        self.si_a.refresh_from_db()
        self.assertEqual(self.si_a.quantity, Decimal('13'))

        vouchers = WarehouseVoucher.objects.filter(voucher_type='adjustment')
        self.assertEqual(vouchers.count(), 1)
        voucher = vouchers.first()
        self.assertEqual(voucher.status, 'finalized')
        self.assertEqual(voucher.source_warehouse_id, self.wh_a.id)
        self.assertEqual(voucher.approver, self.user)  # اقدامِ انبارگردان = تأیید
        self.assertEqual(voucher.created_by, self.user)
        self.assertEqual(voucher.items.count(), 1)
        self.assertEqual(voucher.items.first().base_quantity, Decimal('3'))  # 13-10

        txs = StockTransaction.objects.filter(
            reference_type='warehouse_voucher', voucher=voucher)
        self.assertEqual(txs.count(), 1)
        tx = txs.first()
        self.assertEqual(tx.transaction_type, 'adjustment')
        self.assertEqual(tx.quantity, Decimal('3'))
        self.assertEqual(tx.previous_quantity, Decimal('10'))
        self.assertEqual(tx.new_quantity, Decimal('13'))

    def test_cross_warehouse_item_is_rejected_atomically(self):
        """شناسهٔ موجودی از انبار دیگر: رد می‌شود و هیچ‌چیز تغییر نمی‌کند (رفع باگ)"""
        response = self._post([{'stock_item_id': self.si_other.id, 'physical_qty': '99'}])
        self.assertEqual(response.status_code, 400)

        self.si_other.refresh_from_db()
        self.assertEqual(self.si_other.quantity, Decimal('7'))  # دست‌نخورده
        self.assertEqual(WarehouseVoucher.objects.filter(voucher_type='adjustment').count(), 0)
        self.assertEqual(StockTransaction.objects.count(), 0)

    def test_all_or_nothing_on_error(self):
        """یک قلم خطادار کل ثبت را برمی‌گرداند — حتی قلم‌های درست قبلی (رفع باگ)"""
        response = self._post([
            {'stock_item_id': self.si_a.id, 'physical_qty': '999'},
            {'stock_item_id': self.si_a.id, 'physical_qty': '-5'},
        ])
        self.assertEqual(response.status_code, 400)

        self.si_a.refresh_from_db()
        self.assertEqual(self.si_a.quantity, Decimal('10'))  # بدون تغییر
        self.assertEqual(WarehouseVoucher.objects.filter(voucher_type='adjustment').count(), 0)
        self.assertEqual(StockTransaction.objects.count(), 0)

    def test_zero_delta_is_skipped(self):
        """موجودیِ برابر با فیزیکی: تغییری لازم نیست و حواله‌ای ساخته نمی‌شود"""
        response = self._post([{'stock_item_id': self.si_a.id, 'physical_qty': '10'}])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['updated'], 0)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
        self.assertEqual(StockTransaction.objects.count(), 0)

    def test_unknown_stock_item_is_rejected(self):
        """شناسهٔ ناموجود: رد می‌شود و هیچ‌چیز تغییر نمی‌کند"""
        response = self._post([{'stock_item_id': 999999, 'physical_qty': '5'}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
