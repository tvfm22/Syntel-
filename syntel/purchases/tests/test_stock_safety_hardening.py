# -*- coding: utf-8 -*-
"""Regression tests for duplicate submits, cross-cafe access, and reopen audit.

These tests deliberately exercise the HTTP views (not just services) because
the bugs were in the views: repeated POSTs, raw-id lookups, and hard deletes.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipIf
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, OfficerAssignment, Product, PurchaseDraftItem, PurchaseItem,
    PurchaseRequest, StockItem, StockTransaction, Unit, Warehouse,
    WarehouseRequest, WarehouseRequestItem,
)
from purchases.services.inventory import (
    InsufficientStockError, StockScopeError, apply_stock_delta,
)
from purchases.views import CAFE_LOOKUP_PATHS, _manager_purchase_request_or_404

User = get_user_model()
D = Decimal


class _StockSafetyBase(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafe_a = Cafe.objects.create(name='A', code='a', is_active=True)
        self.cafe_b = Cafe.objects.create(name='B', code='b', is_active=True)
        self.unit_a = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe_a)
        self.unit_b = Unit.objects.create(name='Gram', symbol='g', cafe=self.cafe_b)
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe_a, code='whA', name='انبار A', is_active=True)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe_b, code='whB', name='انبار B', is_active=True)
        self.product_a = Product.objects.create(
            name='Sugar', unit=self.unit_a, cafe=self.cafe_a, min_stock=1)
        self.product_b = Product.objects.create(
            name='Tea', unit=self.unit_b, cafe=self.cafe_b, min_stock=1)
        self.stock_a = StockItem.objects.create(
            product=self.product_a, warehouse=self.wh_a, quantity=D('100'),
            unit=self.unit_a)
        self.stock_b = StockItem.objects.create(
            product=self.product_b, warehouse=self.wh_b, quantity=D('100'),
            unit=self.unit_b)

        self.warehouse_user = User.objects.create_user(
            username='wh_a', password='testpass123', role='warehouse',
            cafe=self.cafe_a)
        self.purchasing_user = User.objects.create_user(
            username='purch_a', password='testpass123', role='purchasing',
            cafe=self.cafe_a)
        self.manager_a = User.objects.create_user(
            username='manager_a', password='testpass123', role='manager',
            cafe=self.cafe_a)
        self.requester = User.objects.create_user(
            username='requester_a', password='testpass123', role='kitchen',
            cafe=self.cafe_a)

    def login(self, user=None, cafe=None):
        user = user or self.warehouse_user
        cafe = cafe or self.cafe_a
        session = self.client.session
        session['selected_cafe_id'] = cafe.id
        session['selected_cafe_code'] = cafe.code
        session.save()
        self.client.force_login(user)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': self.cafe_a.code, **kwargs})

    def make_warehouse_request(self, qty='5'):
        req = WarehouseRequest.objects.create(
            user=self.requester, cafe=self.cafe_a, status='pending')
        item = WarehouseRequestItem.objects.create(
            warehouse_request=req, product=self.product_a, name='Sugar',
            quantity=D(qty), unit='Kilogram')
        return req, item


class ProcessWarehouseRequestHardeningTests(_StockSafetyBase):
    def setUp(self):
        super().setUp()
        self.login(self.warehouse_user)
        self.wh_request, self.wh_item = self.make_warehouse_request()

    def post(self, **extra):
        data = {
            'action': 'submit',
            f'supply_status_{self.wh_item.id}': 'from_stock',
            f'stock_item_{self.wh_item.id}': str(self.stock_a.id),
            f'supplied_qty_{self.wh_item.id}': '5',
        }
        data.update(extra)
        return self.client.post(
            self.url('process_warehouse_request', request_id=self.wh_request.id),
            data=data)

    def test_same_post_twice_deducts_once(self):
        first = self.post()
        second = self.post()
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.stock_a.refresh_from_db()
        self.wh_item.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('95'))
        self.assertEqual(self.wh_item.supply_status, 'from_stock')
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='warehouse_request',
                reference_id=self.wh_request.id).count(), 1)

    def test_same_to_purchase_post_twice_creates_one_request(self):
        data = {f'supply_status_{self.wh_item.id}': 'to_purchase'}
        before = PurchaseRequest.objects.count()
        first = self.post(**data)
        second = self.post(**data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(PurchaseRequest.objects.count(), before + 1)
        self.assertEqual(
            PurchaseRequest.objects.filter(
                from_warehouse_request__id=self.wh_request.id).count(), 1)

    def test_stock_product_must_match_requested_product(self):
        wrong = StockItem.objects.create(
            product=self.product_b, warehouse=self.wh_a, quantity=D('100'),
            unit=self.unit_b)
        r = self.post(**{f'stock_item_{self.wh_item.id}': str(wrong.id)})
        self.assertEqual(r.status_code, 200)
        wrong.refresh_from_db()
        self.wh_item.refresh_from_db()
        self.assertEqual(wrong.quantity, D('100'))
        self.assertIsNone(self.wh_item.stock_item)
        self.assertIsNone(self.wh_item.supplied_quantity)

    def test_supplied_quantity_cannot_exceed_requested(self):
        r = self.post(**{f'supplied_qty_{self.wh_item.id}': '6'})
        self.assertEqual(r.status_code, 200)
        self.stock_a.refresh_from_db()
        self.wh_item.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))
        self.assertIsNone(self.wh_item.stock_item)

    def test_partial_supply_is_allowed(self):
        r = self.post(**{f'supplied_qty_{self.wh_item.id}': '3'})
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.wh_item.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('97'))
        self.assertEqual(self.wh_item.supplied_quantity, D('3'))
        self.assertEqual(self.wh_item.supply_status, 'from_stock')

    def test_invalid_supply_status_is_rejected_without_writes(self):
        r = self.post(**{f'supply_status_{self.wh_item.id}': 'pending'})
        self.assertEqual(r.status_code, 200)
        self.stock_a.refresh_from_db()
        self.wh_item.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))
        self.assertEqual(self.wh_item.supply_status, 'pending')
        self.assertFalse(
            StockTransaction.objects.filter(reference_id=self.wh_request.id).exists())

    def test_mixed_stock_and_purchase_is_not_fulfilled(self):
        second = WarehouseRequestItem.objects.create(
            warehouse_request=self.wh_request, product=self.product_a,
            name='Flour', quantity=D('3'), unit='Kilogram')
        r = self.post(**{
            f'supply_status_{second.id}': 'to_purchase',
        })
        self.assertEqual(r.status_code, 302)
        self.wh_request.refresh_from_db()
        self.assertEqual(self.wh_request.status, 'partial')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('95'))


class WarehouseOfficerHardeningTests(_StockSafetyBase):
    def setUp(self):
        super().setUp()
        self.login(self.warehouse_user)
        self.request = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_a, status='pending')
        self.item = PurchaseItem.objects.create(
            request=self.request, product=self.product_a, name='Sugar',
            quantity=D('5'), unit='Kilogram')
        self.assignment = OfficerAssignment.objects.create(
            item=self.item, target_role='warehouse', status='pending',
            assigned_to=self.warehouse_user, assigned_by=self.warehouse_user)
        self.dash_url = self.url('warehouse_officer_dashboard') + (
            '?day=' + timezone.localtime(timezone.now()).date().isoformat())

    def post(self, action, **extra):
        data = {'action': action, 'item_id': str(self.item.id)}
        data.update(extra)
        return self.client.post(self.dash_url, data=data)

    def test_fulfill_twice_deducts_once(self):
        first = self.post(
            'fulfill', stock_item_id=str(self.stock_a.id), qty='5')
        second = self.post(
            'fulfill', stock_item_id=str(self.stock_a.id), qty='5')
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('95'))
        self.assertEqual(
            OfficerAssignment.objects.filter(item=self.item).count(), 1)
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='officer_assignment',
                reference_id=self.item.id).count(), 1)

    def test_to_purchase_twice_creates_one_draft_item(self):
        first = self.post('to_purchase')
        second = self.post('to_purchase')
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            PurchaseDraftItem.objects.filter(source_item=self.item).count(), 1)
        self.assertEqual(
            OfficerAssignment.objects.filter(item=self.item).count(), 1)
        self.assertEqual(
            OfficerAssignment.objects.get(item=self.item).status, 'returned')

    def test_fulfill_cannot_exceed_requested_quantity(self):
        r = self.post(
            'fulfill', stock_item_id=str(self.stock_a.id), qty='6')
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assignment.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))
        self.assertEqual(self.assignment.status, 'pending')


class PurchaseListHardeningTests(_StockSafetyBase):
    def setUp(self):
        super().setUp()
        self.login(self.purchasing_user)
        self.request = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_a, status='purchased')
        self.item = PurchaseItem.objects.create(
            request=self.request, product=self.product_a, name='Sugar',
            quantity=D('5'), unit='Kilogram', supply_status='pending')

    def post(self, stock_item=None, qty='5'):
        stock_item = stock_item or self.stock_a
        return self.client.post(
            self.url('purchase_list_with_stock_supply'),
            data={
                'item_ids': [str(self.item.id)],
                f'action_{self.item.id}': 'from_stock',
                f'stock_sid_{self.item.id}': [str(stock_item.id)],
                f'stock_qty_{self.item.id}': [qty],
            })

    def test_same_post_twice_deducts_once(self):
        first = self.post()
        second = self.post()
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.stock_a.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('95'))
        self.assertEqual(self.item.supply_status, 'from_stock')
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='purchase_item',
                reference_id=self.item.id).count(), 1)

    def test_stock_product_must_match_item_product(self):
        wrong = StockItem.objects.create(
            product=self.product_b, warehouse=self.wh_a, quantity=D('100'),
            unit=self.unit_b)
        r = self.post(stock_item=wrong)
        self.assertEqual(r.status_code, 302)
        wrong.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(wrong.quantity, D('100'))
        self.assertEqual(self.item.supply_status, 'pending')
        self.assertFalse(
            StockTransaction.objects.filter(reference_id=self.item.id).exists())

    def test_supplied_quantity_cannot_exceed_requested(self):
        r = self.post(qty='6')
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))
        self.assertEqual(self.item.supply_status, 'pending')


class AvailableProductsInventoryErrorTests(_StockSafetyBase):
    def setUp(self):
        super().setUp()
        self.login(self.warehouse_user)
        self.client.raise_request_exception = False

    def post(self):
        return self.client.post(
            self.url('warehouse_available_products'),
            data={
                'product_id': [str(self.product_a.id)],
                'warehouse_id': [str(self.wh_a.id)],
                'brand_id': [''],
                'qty': ['5'],
                'status': ['from_stock'],
            })

    def test_inventory_insufficient_error_is_friendly(self):
        with patch('purchases.views.apply_stock_delta',
                   side_effect=InsufficientStockError('Sugar')):
            r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseRequest.objects.filter(cafe=self.cafe_a).count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))

    def test_inventory_scope_error_is_friendly(self):
        with patch('purchases.views.apply_stock_delta',
                   side_effect=StockScopeError('کافه')):
            r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseRequest.objects.filter(cafe=self.cafe_a).count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))


class AvailableProductsStatusTests(_StockSafetyBase):
    def setUp(self):
        super().setUp()
        self.login(self.warehouse_user)

    def test_mixed_status_is_partial(self):
        r = self.client.post(
            self.url('warehouse_available_products'),
            data={
                'product_id': [str(self.product_a.id), str(self.product_a.id)],
                'warehouse_id': [str(self.wh_a.id), str(self.wh_a.id)],
                'brand_id': ['', ''],
                'qty': ['5', '2'],
                'status': ['from_stock', 'to_purchase'],
            })
        self.assertEqual(r.status_code, 302)
        wh_request = WarehouseRequest.objects.get(cafe=self.cafe_a)
        self.assertEqual(wh_request.status, 'partial')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('95'))


class CrossCafeHardeningTests(_StockSafetyBase):
    def test_lookup_paths_cover_all_flow_models(self):
        expected = {
            'PurchaseItem': 'request__cafe',
            'OfficerAssignment': 'item__request__cafe',
            'StockRequest': 'purchase_item__request__cafe',
            'WarehouseRequestItem': 'warehouse_request__cafe',
            'PurchaseDraft': 'cafe',
        }
        for model_name, path in expected.items():
            self.assertEqual(CAFE_LOOKUP_PATHS.get(model_name), path)

    def test_process_warehouse_request_rejects_foreign_request(self):
        self.login(self.warehouse_user)
        foreign = WarehouseRequest.objects.create(
            user=self.requester, cafe=self.cafe_b, status='pending')
        item = WarehouseRequestItem.objects.create(
            warehouse_request=foreign, product=self.product_b, name='Tea',
            quantity=D('2'), unit='Gram')
        r = self.client.post(
            self.url('process_warehouse_request', request_id=foreign.id),
            data={
                f'supply_status_{item.id}': 'declined',
                'action': 'submit',
            })
        self.assertEqual(r.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, 'pending')

    def test_purchase_list_rejects_foreign_item(self):
        self.login(self.purchasing_user)
        foreign_req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_b, status='purchased')
        foreign_item = PurchaseItem.objects.create(
            request=foreign_req, product=self.product_b, name='Tea',
            quantity=D('2'), unit='Gram', supply_status='pending')
        r = self.client.post(
            self.url('purchase_list_with_stock_supply'),
            data={
                'item_ids': [str(foreign_item.id)],
                f'action_{foreign_item.id}': 'from_stock',
                f'stock_sid_{foreign_item.id}': [str(self.stock_b.id)],
                f'stock_qty_{foreign_item.id}': ['1'],
            })
        self.assertEqual(r.status_code, 404)
        foreign_item.refresh_from_db()
        self.assertEqual(foreign_item.supply_status, 'pending')
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, D('100'))

    def test_purchase_officer_rejects_foreign_assignment(self):
        self.login(self.purchasing_user)
        foreign_req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_b, status='pending')
        foreign_item = PurchaseItem.objects.create(
            request=foreign_req, product=self.product_b, name='Tea',
            quantity=D('2'), unit='Gram')
        foreign_assignment = OfficerAssignment.objects.create(
            item=foreign_item, target_role='purchasing', status='pending',
            assigned_to=None)
        r = self.client.post(
            self.url('purchase_officer_dashboard'),
            data={'action': 'purchased', 'item_id': str(foreign_assignment.id)})
        self.assertEqual(r.status_code, 404)
        foreign_assignment.refresh_from_db()
        self.assertEqual(foreign_assignment.status, 'pending')

    def test_manager_approve_rejects_foreign_request(self):
        self.login(self.manager_a)
        foreign = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_b,
            status='pending_manager_approval')
        r = self.client.post(
            self.url('manager_approve_request', request_id=foreign.id))
        self.assertEqual(r.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, 'pending_manager_approval')
        self.assertFalse(foreign.manager_confirmed)

    def test_manager_reject_rejects_foreign_request(self):
        self.login(self.manager_a)
        foreign = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_b,
            status='pending_manager_approval')
        r = self.client.post(
            self.url('manager_reject_request', request_id=foreign.id),
            data={'rejection_reason': 'x'})
        self.assertEqual(r.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, 'pending_manager_approval')

    def test_manager_next_url_must_be_local(self):
        self.login(self.manager_a)
        own = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_a,
            status='pending_manager_approval')
        r = self.client.post(
            self.url('manager_approve_request', request_id=own.id)
            + '?next=https%3A%2F%2Fevil.example%2Fsteal')
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('evil.example', r.url)
        own.refresh_from_db()
        self.assertEqual(own.status, 'pending')

    def test_superadmin_without_cafe_uses_object_cafe(self):
        superadmin = User.objects.create_user(
            username='super_manager', password='testpass123',
            role='manager', cafe=None)
        own = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_a,
            status='pending_manager_approval')
        request = RequestFactory().post('/')
        request.user = superadmin
        request.session = {}
        found = _manager_purchase_request_or_404(request, own.id)
        self.assertEqual(found, own)

    def test_non_superadmin_without_cafe_gets_404(self):
        manager_no_cafe = User.objects.create_user(
            username='plain_manager', password='testpass123',
            role='manager', cafe=None)
        own = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_a,
            status='pending_manager_approval')
        request = RequestFactory().post('/')
        request.user = manager_no_cafe
        request.session = {}
        # role=manager و cafe=None همان سوپرادمین است؛ برای «کاربر عادی بدون کافه»
        # نقش را عوض می‌کنیم تا مسیر 404 آزموده شود.
        manager_no_cafe.role = 'purchasing'
        manager_no_cafe.save(update_fields=['role'])
        with self.assertRaises(Http404):
            _manager_purchase_request_or_404(request, own.id)


class ReopenAuditTests(_StockSafetyBase):
    def setUp(self):
        super().setUp()
        self.login(self.warehouse_user)
        self.request = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe_a, status='pending')
        self.item = PurchaseItem.objects.create(
            request=self.request, product=self.product_a, name='Sugar',
            quantity=D('5'), unit='Kilogram')
        self.assignment = OfficerAssignment.objects.create(
            item=self.item, target_role='warehouse', status='fulfilled',
            assigned_to=self.warehouse_user, assigned_by=self.warehouse_user,
            stock_item=self.stock_a, supplied_quantity=D('5'))
        apply_stock_delta(
            self.stock_a, D('-5'), cafe=self.cafe_a, created_by=self.warehouse_user,
            reference_type='officer_assignment', reference_id=self.item.id,
            description='تامین اولیه')
        self.dash_url = self.url('warehouse_officer_dashboard') + (
            '?day=' + timezone.localtime(timezone.now()).date().isoformat())

    def reopen(self, **extra):
        data = {'action': 'reopen', 'item_id': str(self.item.id)}
        data.update(extra)
        return self.client.post(self.dash_url, data=data)

    def test_reopen_keeps_original_transaction_and_adds_compensating_row(self):
        r = self.reopen(reason='اشتباه در تامین')
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.item.refresh_from_db()
        self.assignment.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))
        self.assertEqual(self.assignment.status, 'reopened')
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='officer_assignment',
                reference_id=self.item.id).count(), 1)
        reopen_tx = StockTransaction.objects.get(
            reference_type='officer_assignment_reopen',
            reference_id=self.item.id)
        self.assertIn('اشتباه در تامین', reopen_tx.description)
        self.assertEqual(reopen_tx.transaction_type, 'in')
        self.assertEqual(reopen_tx.quantity, D('5'))
        # قلم بازگشایی‌شده دوباره در بخش «قابل تامین» دیده می‌شود
        page = self.client.get(self.dash_url)
        visible_ids = [
            row['item'].id
            for card in page.context['request_cards']
            for row in card['visible']
        ]
        self.assertIn(self.item.id, visible_ids)

    def test_second_reopen_is_rejected_without_stock_change(self):
        self.reopen(reason='بار اول')
        second = self.reopen(reason='بار دوم')
        self.assertEqual(second.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('100'))
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='officer_assignment_reopen',
                reference_id=self.item.id).count(), 1)
        self.assertEqual(
            OfficerAssignment.objects.filter(item=self.item).count(), 1)

    def test_reopen_after_received_confirmation_is_blocked(self):
        self.item.received_confirmed_at = timezone.now()
        self.item.save(update_fields=['received_confirmed_at'])
        r = self.reopen(reason='تلاش بعد از دریافت')
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assignment.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('95'))
        self.assertEqual(self.assignment.status, 'fulfilled')
        self.assertFalse(
            StockTransaction.objects.filter(
                reference_type='officer_assignment_reopen',
                reference_id=self.item.id).exists())

    def test_reopen_without_reason_is_blocked(self):
        r = self.reopen()
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assignment.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('95'))
        self.assertEqual(self.assignment.status, 'fulfilled')
        self.assertFalse(
            StockTransaction.objects.filter(
                reference_type='officer_assignment_reopen',
                reference_id=self.item.id).exists())


@skipIf(connection.vendor == 'sqlite',
        'SQLite does not provide row-level SELECT FOR UPDATE concurrency semantics')
class ConcurrentSubmitTests(TransactionTestCase):
    """Two simultaneous fulfill requests must deduct stock only once.

    PostgreSQL (and other DBs with row locks) runs this; SQLite is skipped
    explicitly because SELECT FOR UPDATE has no row-lock semantics there.
    """

    def setUp(self):
        self.cafe = Cafe.objects.create(name='C', code='c', is_active=True)
        self.unit = Unit.objects.create(name='kg', symbol='kg', cafe=self.cafe)
        self.warehouse = Warehouse.objects.create(
            cafe=self.cafe, code='whC', name='WH C', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=1)
        self.stock = StockItem.objects.create(
            product=self.product, warehouse=self.warehouse,
            quantity=D('100'), unit=self.unit)
        self.user = User.objects.create_user(
            username='concurrent_wh', password='testpass123',
            role='warehouse', cafe=self.cafe)
        self.requester = User.objects.create_user(
            username='concurrent_req', password='testpass123',
            role='kitchen', cafe=self.cafe)
        self.request = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending')
        self.item = PurchaseItem.objects.create(
            request=self.request, product=self.product, name='Sugar',
            quantity=D('5'), unit='Kilogram')
        OfficerAssignment.objects.create(
            item=self.item, target_role='warehouse', status='pending',
            assigned_to=None, assigned_by=self.user)
        self.url = reverse('warehouse_officer_dashboard',
                           kwargs={'cafe_code': self.cafe.code}) + (
            '?day=' + timezone.localtime(timezone.now()).date().isoformat())

    def _submit(self, barrier):
        close_old_connections()
        try:
            client = Client()
            session = client.session
            session['selected_cafe_id'] = self.cafe.id
            session['selected_cafe_code'] = self.cafe.code
            session.save()
            client.force_login(self.user)
            barrier.wait(timeout=10)
            return client.post(self.url, {
                'action': 'fulfill',
                'item_id': str(self.item.id),
                'stock_item_id': [str(self.stock.id)],
                'qty': ['5'],
            }).status_code
        finally:
            close_old_connections()

    def test_two_threads_do_not_double_deduct(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._submit(barrier), range(2)))
        self.assertEqual(results, [302, 302])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, D('95'))
        self.assertEqual(
            StockTransaction.objects.filter(
                reference_type='officer_assignment',
                reference_id=self.item.id).count(), 1)
        self.assertEqual(
            OfficerAssignment.objects.filter(item=self.item).count(), 1)
