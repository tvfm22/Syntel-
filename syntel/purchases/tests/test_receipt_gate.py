"""قاعدهٔ «تایید دریافتِ تحویلهای قبلی، پیش از ثبت درخواست جدید».

منبع تحویل: تخصیص تامینشدهٔ مسئول انبار (OfficerAssignment.status='fulfilled')
روی قلم درخواستِ خود کاربر. تا ``received_confirmed_at`` خالی باشد، هر دو فرم
درخواست (کالا و از انبار) سمت سرور مسدودند؛ بعد از «تایید دریافت» آزاد میشوند.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Category, OfficerAssignment, Product, PurchaseItem, PurchaseRequest,
    StockItem, Unit, Warehouse, WarehouseRequest,
)
from purchases.services.receipt_gate import (
    pending_receipt_count,
    receipt_gate_context,
)

User = get_user_model()


class ReceiptGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(name='Gate Cafe', code='gate', is_active=True)
        cls.other_cafe = Cafe.objects.create(name='Other Cafe', code='gateother', is_active=True)
        cls.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=cls.cafe)
        cls.category = Category.objects.create(name='General', cafe=cls.cafe)
        cls.product = Product.objects.create(
            name='Sugar', category=cls.category, unit=cls.unit,
            min_stock=0, cafe=cls.cafe,
        )
        cls.warehouse = Warehouse.objects.create(name='Main WH', code='GWH', cafe=cls.cafe)
        cls.stock = StockItem.objects.create(
            product=cls.product, warehouse=cls.warehouse, unit=cls.unit,
            quantity=100, min_quantity=0,
        )
        cls.requester = User.objects.create_user(
            username='gate_req', password='testpass123', role='kitchen', cafe=cls.cafe)
        cls.other = User.objects.create_user(
            username='gate_other', password='testpass123', role='kitchen', cafe=cls.cafe)
        cls.officer = User.objects.create_user(
            username='gate_off', password='testpass123', role='warehouse', cafe=cls.cafe)
        cls.admin = User.objects.create_user(
            username='gate_admin', password='testpass123', role='admin',
            is_superuser=True, cafe=cls.cafe)

    # ── helpers ──────────────────────────────────────────────────────────────
    def setUp(self):
        self.client = Client()
        self.client.login(username='gate_req', password='testpass123')

    def url(self, name):
        return reverse(name, kwargs={'cafe_code': self.cafe.code})

    def _deliver(self, user=None, cafe=None, confirmed=False, status='fulfilled'):
        req = PurchaseRequest.objects.create(user=user or self.requester,
                                             cafe=cafe or self.cafe)
        item = PurchaseItem.objects.create(
            request=req, product=self.product, name='Sugar',
            quantity=Decimal('3'), unit='Kilogram',
        )
        if confirmed:
            item.received_confirmed_at = timezone.now()
            item.received_confirmed_by = user or self.requester
            item.save(update_fields=['received_confirmed_at', 'received_confirmed_by'])
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', status=status,
            stock_item=self.stock, supplied_quantity=Decimal('3'),
            assigned_to=self.officer,
        )
        return item

    def _confirm_all(self):
        return self.client.post(self.url('my_received_items'),
                                {'action': 'confirm_all'})

    def _post_goods(self):
        return self.client.post(self.url('purchase_request'), {
            'product_name': ['Sugar'],
            'item_quantity': ['2'],
            'item_unit': ['Kilogram'],
            'item_description': [''],
            'item_product_id': [str(self.product.id)],
            'item_brand_id': [''],
        })

    def _post_warehouse(self):
        return self.client.post(self.url('warehouse_request_form'), {
            'product_name': ['Sugar'],
            'item_quantity': ['2'],
            'item_unit': ['Kilogram'],
            'item_description': [''],
            'item_product_id': [str(self.product.id)],
            'item_brand_id': [''],
        })

    # ── منبع حقیقت ───────────────────────────────────────────────────────────
    def test_pending_count_is_scoped_to_user_cafe_and_confirmation(self):
        self._deliver()                                   # مانع
        self._deliver(confirmed=True)                     # تاییدشده
        self._deliver(user=self.other)                    # کاربر دیگر
        self._deliver(cafe=self.other_cafe)               # کافهٔ دیگر
        self._deliver(status='pending')                   # هنوز تحویل نشده
        self.assertEqual(pending_receipt_count(self.requester, self.cafe), 1)
        self.assertIsNotNone(receipt_gate_context(self.requester, self.cafe,
                                                  self.cafe.code))
        self.assertEqual(pending_receipt_count(self.requester, None), 2)

    def test_superuser_is_not_blocked(self):
        self._deliver(user=self.admin)
        self.assertEqual(pending_receipt_count(self.admin, self.cafe), 0)

    # ── بنر قبل از پر کردن فرم ───────────────────────────────────────────────
    def test_form_get_shows_banner_and_disables_submit(self):
        self._deliver()
        for name in ('purchase_request', 'warehouse_request_form'):
            with self.subTest(form=name):
                html = self.client.get(self.url(name)).content.decode('utf-8')
                self.assertIn('قبل از درخواست جدید', html)
                self.assertIn(self.url('my_received_items'), html)
                self.assertIn('disabled title=', html)

    def test_form_get_is_clean_without_pending_deliveries(self):
        html = self.client.get(self.url('purchase_request')).content.decode('utf-8')
        self.assertNotIn('قبل از درخواست جدید', html)

    # ── قفل سمت سرور ─────────────────────────────────────────────────────────
    def test_goods_request_blocked_while_delivery_unconfirmed(self):
        self._deliver()
        r = self._post_goods()
        self.assertEqual(r.status_code, 200)          # فرم دوباره رندر شد
        self.assertEqual(PurchaseRequest.objects.count(), 1)
        html = r.content.decode('utf-8')
        self.assertIn('قبل از ثبت درخواست جدید', html)
        self.assertIn('قبل از درخواست جدید', html)     # بنر همچنان هست
        self.assertIn('Sugar', html)                  # ردیفهای واردشده حفظ شدند

    def test_goods_request_allowed_after_confirming(self):
        self._deliver()
        self.assertEqual(self._confirm_all().status_code, 302)
        r = self._post_goods()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, self.url('purchase_request_success'))
        self.assertEqual(PurchaseRequest.objects.count(), 2)

    def test_warehouse_request_blocked_then_allowed(self):
        self._deliver()
        r = self._post_warehouse()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseRequest.objects.count(), 0)
        self.assertIn('قبل از ثبت درخواست جدید', r.content.decode('utf-8'))

        self._confirm_all()
        r = self._post_warehouse()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WarehouseRequest.objects.count(), 1)

    def test_confirmed_or_foreign_delivery_does_not_block(self):
        self._deliver(confirmed=True)
        self._deliver(user=self.other)
        self.assertEqual(self._post_goods().status_code, 302)
        self.assertEqual(PurchaseRequest.objects.count(), 3)

    def test_received_items_page_offers_new_request_once_clear(self):
        self._deliver()
        r = self._confirm_all()
        self.assertEqual(r.status_code, 302)
        html = self.client.get(self.url('my_received_items')).content.decode('utf-8')
        self.assertIn('ثبت درخواست جدید', html)
        self.assertIn(self.url('purchase_request'), html)

    # ── ویرایش درخواست قدیمی (راه دور زدن قفل) ───────────────────────────────
    def _editable_request(self):
        """درخواست قدیمی که هنوز وضعیتی از انبار نگرفته و قابل ویرایش است."""
        req = PurchaseRequest.objects.create(user=self.requester, cafe=self.cafe)
        item = PurchaseItem.objects.create(
            request=req, product=self.product, name='Sugar',
            quantity=Decimal('1'), unit='Kilogram',
        )
        return req, item

    def _edit_url(self, req):
        return reverse('edit_my_request', args=[self.cafe.code, req.id])

    def _edit_payload(self, item, new_row=False):
        data = {
            'item_id': [str(item.id)],
            'name': ['Sugar'],
            'quantity': ['7'],
            'unit': ['Kilogram'],
            'description': [''],
        }
        if new_row:
            data['item_id'].append('')
            data['name'].append('Salt')
            data['quantity'].append('2')
            data['unit'].append('Kilogram')
            data['description'].append('')
        return data

    def test_edit_page_shows_banner_while_gate_active(self):
        self._deliver()
        req, _ = self._editable_request()
        html = self.client.get(self._edit_url(req)).content.decode('utf-8')
        self.assertIn('قبل از درخواست جدید', html)
        self.assertIn(self.url('my_received_items'), html)
        self.assertIn('disabled title=', html)

    def test_edit_cannot_add_new_item_while_gate_active(self):
        self._deliver()
        req, item = self._editable_request()
        r = self.client.post(self._edit_url(req), self._edit_payload(item, new_row=True))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(req.items.count(), 1, 'هیچ قلم تازهای نباید ذخیره شود')
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('1'), 'ویرایش ردیفهای قدیمی هم نباید ذخیره شود')
        html = r.content.decode('utf-8')
        self.assertIn('قبل از ثبت درخواست جدید', html)
        self.assertIn('Salt', html)       # ردیف تایپشده حفظ شد
        self.assertIn('قبل از درخواست جدید', html)

    def test_edit_existing_items_allowed_while_gate_active(self):
        self._deliver()
        req, item = self._editable_request()
        r = self.client.post(self._edit_url(req), self._edit_payload(item))
        self.assertEqual(r.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('7'))

    def test_edit_add_item_allowed_after_confirming(self):
        self._deliver()
        req, item = self._editable_request()
        self.assertEqual(self._confirm_all().status_code, 302)
        r = self.client.post(self._edit_url(req), self._edit_payload(item, new_row=True))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(req.items.filter(name='Salt').exists())
