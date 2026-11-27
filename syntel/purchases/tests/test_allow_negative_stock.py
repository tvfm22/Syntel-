from decimal import Decimal
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe,
    Department,
    OfficerAssignment,
    Product,
    PurchaseItem,
    PurchaseRequest,
    StockItem,
    StockTransaction,
    Unit,
    Warehouse,
    WarehouseRequest,
    WarehouseRequestItem,
    WarehouseVoucher,
    WarehouseVoucherItem,
)
from purchases.services.inventory import (
    InsufficientStockError,
    VoucherValidationError,
    apply_stock_delta,
    finalize_voucher,
    next_voucher_number,
    reverse_voucher,
)

User = get_user_model()
D = Decimal


class AllowNegativeStockTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafe = Cafe.objects.create(name='کافه تست', code='negcafe', is_active=True)
        self.unit = Unit.objects.create(name='کیلوگرم', symbol='kg', cafe=self.cafe)
        self.dept = Department.objects.create(name='بار', cafe=self.cafe, is_active=True)

        self.manager = User.objects.create_user(
            username='neg_mgr', password='testpass123', role='manager', cafe=self.cafe,
        )
        self.wh_user = User.objects.create_user(
            username='neg_wh', password='testpass123', role='warehouse', cafe=self.cafe,
        )
        self.requester = User.objects.create_user(
            username='neg_req', password='testpass123', role='bar',
            department=self.dept, cafe=self.cafe,
        )

        self.wh_strict = Warehouse.objects.create(
            cafe=self.cafe, code='WH-STRICT', name='انبار عادی',
            is_active=True, allow_negative_stock=False,
        )
        self.wh_flex = Warehouse.objects.create(
            cafe=self.cafe, code='WH-FLEX', name='انبار آزمایشی',
            is_active=True, allow_negative_stock=True,
        )

        self.coffee = Product.objects.create(
            name='قهوه', unit=self.unit, cafe=self.cafe, min_stock=1,
        )
        self.milk = Product.objects.create(
            name='شیر', unit=self.unit, cafe=self.cafe, min_stock=1,
        )

    def _login(self, user):
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()
        self.client.force_login(user)

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': self.cafe.code, **kwargs})

    def test_strict_warehouse_still_blocks_negative_and_missing(self):
        v_missing = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number=next_voucher_number(self.cafe.id, 'issue'),
            source_warehouse=self.wh_strict, created_by=self.wh_user,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v_missing, product=self.coffee, unit=self.unit, base_quantity=D('2'),
        )
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v_missing, finalized_by=self.wh_user)

        si = StockItem.objects.create(
            product=self.coffee, warehouse=self.wh_strict, quantity=D('1'), unit=self.unit,
        )
        v_over = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number=next_voucher_number(self.cafe.id, 'issue'),
            source_warehouse=self.wh_strict, created_by=self.wh_user,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v_over, product=self.coffee, stock_item=si,
            unit=self.unit, base_quantity=D('5'),
        )
        with self.assertRaises(InsufficientStockError):
            finalize_voucher(v_over, finalized_by=self.wh_user)
        si.refresh_from_db()
        self.assertEqual(si.quantity, D('1'))

    def test_issue_without_initial_stock_then_receipt_and_inventory_check_reconcile(self):
        # ۱) خروج ۵ کیلو بدون داشتن هیچ StockItem اولیه
        v_out1 = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number=next_voucher_number(self.cafe.id, 'issue'),
            source_warehouse=self.wh_flex, created_by=self.wh_user,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v_out1, product=self.coffee, unit=self.unit, base_quantity=D('5'),
        )
        res1 = finalize_voucher(v_out1, finalized_by=self.wh_user)
        self.assertTrue(res1.applied)

        si = StockItem.objects.get(product=self.coffee, warehouse=self.wh_flex)
        self.assertEqual(si.quantity, D('-5'))
        tx1 = res1.stock_transactions[0]
        self.assertEqual(tx1.previous_quantity, D('0'))
        self.assertEqual(tx1.new_quantity, D('-5'))

        # ۲) ورود ۳ کیلو -> موجودی می‌شود -2
        v_in = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='receipt', status='submitted',
            voucher_number=next_voucher_number(self.cafe.id, 'receipt'),
            destination_warehouse=self.wh_flex, created_by=self.wh_user,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v_in, product=self.coffee, unit=self.unit, base_quantity=D('3'),
        )
        finalize_voucher(v_in, finalized_by=self.wh_user)
        si.refresh_from_db()
        self.assertEqual(si.quantity, D('-2'))

        # ۳) بررسی گزارش مصرف: ۵ کیلو خروج ثبت شده دیده می‌شود
        self._login(self.manager)
        r_rep = self.client.get(self._url('consumption_report'))
        self.assertEqual(r_rep.status_code, 200)
        self.assertContains(r_rep, 'قهوه')

        # ۴) پایان دوره: انبارگردانی با شمارش واقعی ۱۲ کیلو
        r_inv = self.client.post(
            self._url('warehouse_inventory_submit', warehouse_id=self.wh_flex.id),
            data=json.dumps({'items': [{'stock_item_id': si.id, 'physical_qty': '12'}]}),
            content_type='application/json',
        )
        self.assertEqual(r_inv.status_code, 200)
        self.assertTrue(r_inv.json()['success'])
        si.refresh_from_db()
        self.assertEqual(si.quantity, D('12'))

        # ۵) خاموش کردن حالت موجودی منفی پس از انبارگردانی
        r_edit = self.client.post(
            self._url('warehouse_edit', warehouse_id=self.wh_flex.id),
            data=json.dumps({
                'name': self.wh_flex.name,
                'code': self.wh_flex.code,
                'is_active': True,
                'is_supply_enabled': True,
                'allow_negative_stock': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(r_edit.status_code, 200)
        self.wh_flex.refresh_from_db()
        self.assertFalse(self.wh_flex.allow_negative_stock)

        # حالا خروج بیش از موجودی (۱۳ > ۱۲) دوباره مسدود است
        with self.assertRaises(InsufficientStockError):
            apply_stock_delta(si, D('-13'), cafe=self.cafe, created_by=self.wh_user)
        si.refresh_from_db()
        self.assertEqual(si.quantity, D('12'))

    def test_reverse_receipt_allowed_when_warehouse_allows_negative(self):
        v_in = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='receipt', status='submitted',
            voucher_number=next_voucher_number(self.cafe.id, 'receipt'),
            destination_warehouse=self.wh_flex, created_by=self.wh_user,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v_in, product=self.milk, unit=self.unit, base_quantity=D('4'),
        )
        finalize_voucher(v_in, finalized_by=self.wh_user)

        si = StockItem.objects.get(product=self.milk, warehouse=self.wh_flex)
        apply_stock_delta(si, D('-4'), cafe=self.cafe, created_by=self.wh_user)
        si.refresh_from_db()
        self.assertEqual(si.quantity, D('0'))

        # برگشتِ رسید ۴ تایی باعث می‌شود موجودی به -4 برود و خطا ندهد
        reverse_voucher(v_in, created_by=self.manager)
        si.refresh_from_db()
        self.assertEqual(si.quantity, D('-4'))

    def test_warehouse_create_and_edit_persist_allow_negative_stock(self):
        self._login(self.manager)
        r_create = self.client.post(
            self._url('warehouse_create'),
            data=json.dumps({
                'name': 'انبار مصرف آزاد',
                'code': 'WH-NEG-NEW',
                'is_supply_enabled': True,
                'allow_negative_stock': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(r_create.status_code, 200)
        payload = r_create.json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['warehouse']['allow_negative_stock'])
        new_wh = Warehouse.objects.get(id=payload['warehouse']['id'])
        self.assertTrue(new_wh.allow_negative_stock)

        r_get = self.client.get(self._url('warehouse_edit', warehouse_id=new_wh.id))
        self.assertEqual(r_get.status_code, 200)
        self.assertTrue(r_get.json()['warehouse']['allow_negative_stock'])

    def test_warehouse_officer_dashboard_fulfill_without_prior_stock(self):
        today = timezone.localtime(timezone.now()).date()
        pr = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending', delivery_date=today,
        )
        item = PurchaseItem.objects.create(
            request=pr, product=self.milk, name='شیر', quantity=D('6'), unit='کیلوگرم',
        )
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', assigned_to=self.wh_user, status='pending',
        )

        self._login(self.wh_user)
        dash_url = self._url('warehouse_officer_dashboard')
        r_get = self.client.get(dash_url, follow=True)
        self.assertEqual(r_get.status_code, 200)
        self.assertContains(r_get, f'value="wh:{self.wh_flex.id}"')

        r_post = self.client.post(dash_url, {
            'action': 'fulfill',
            'item_id': str(item.id),
            'stock_item_id': f'wh:{self.wh_flex.id}',
            'qty': '6',
        })
        self.assertEqual(r_post.status_code, 302)
        si = StockItem.objects.get(product=self.milk, warehouse=self.wh_flex)
        self.assertEqual(si.quantity, D('-6'))
        self.assertEqual(
            StockTransaction.objects.filter(
                stock_item=si, reference_type='officer_assignment', reference_id=item.id,
            ).count(),
            1,
        )

    def test_process_warehouse_request_allows_negative_for_flex_warehouse(self):
        si = StockItem.objects.create(
            product=self.milk, warehouse=self.wh_flex, quantity=D('0'), unit=self.unit,
        )
        wh_req = WarehouseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending',
        )
        wh_item = WarehouseRequestItem.objects.create(
            warehouse_request=wh_req, product=self.milk, name='شیر',
            quantity=D('3'), unit='کیلوگرم',
        )

        self._login(self.wh_user)
        r = self.client.post(
            self._url('process_warehouse_request', request_id=wh_req.id),
            data={
                f'supply_status_{wh_item.id}': 'from_stock',
                f'stock_item_{wh_item.id}': str(si.id),
                f'supplied_qty_{wh_item.id}': '3',
            },
        )
        self.assertEqual(r.status_code, 302)
        si.refresh_from_db()
        self.assertEqual(si.quantity, D('-3'))
        wh_item.refresh_from_db()
        self.assertEqual(wh_item.supply_status, 'from_stock')

    def test_voucher_create_renders_compact_stock_badge_and_locked_unit(self):
        self._login(self.wh_user)
        r = self.client.get(self._url('voucher_create', voucher_type='issue'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<span>موجودی</span>')
        self.assertNotContains(r, 'موجودی انبار (اختیاری)')
        self.assertContains(r, 'class="row-stock-badge"')
        self.assertContains(r, 'class="row-unit-display" readonly')
        self.assertContains(r, f'unit_id: {self.unit.id}')
        self.assertContains(r, 'unit_name: "کیلوگرم"')

        # ثبت حواله خروج با فرم جدید (stock_item_id خالی) روی انبار با موجودی منفی مجاز
        r_post = self.client.post(
            self._url('voucher_create', voucher_type='issue'),
            data={
                'action': 'submit',
                'source_warehouse': str(self.wh_flex.id),
                'date': '1405/09/01',
                'product_id[]': [str(self.coffee.id)],
                'stock_item_id[]': [''],
                'unit_id[]': [str(self.unit.id)],
                'base_quantity[]': ['2.5'],
                'unit_price[]': ['0'],
                'item_description[]': [''],
            },
        )
        self.assertEqual(r_post.status_code, 302)
        si = StockItem.objects.get(product=self.coffee, warehouse=self.wh_flex)
        self.assertEqual(si.quantity, D('-2.5'))
