from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from purchases.models import (
    Cafe, Category, Invoice, InvoiceItem, Payment, Product,
    PurchaseOrder, PurchaseRequest, Requester, Supplier, SupplierEvaluation,
)

User = get_user_model()


def _set_cafe_session(client, cafe):
    session = client.session
    session['selected_cafe_id'] = cafe.id
    session['selected_cafe_code'] = cafe.code
    session.save()


class MultiTenancyIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafe_a = Cafe.objects.create(name='Cafe A', code='cafe_a', is_active=True)
        self.cafe_b = Cafe.objects.create(name='Cafe B', code='cafe_b', is_active=True)

        self.user_a = User.objects.create_user(
            username='mgr_a', password='testpass123', role='manager', cafe=self.cafe_a
        )
        self.user_b = User.objects.create_user(
            username='mgr_b', password='testpass123', role='manager', cafe=self.cafe_b
        )

        self.cat_a = Category.objects.create(name='Cat A', cafe=self.cafe_a)
        self.cat_b = Category.objects.create(name='Cat B', cafe=self.cafe_b)

        self.prod_a = Product.objects.create(name='Prod A', category=self.cat_a, cafe=self.cafe_a)
        self.prod_b = Product.objects.create(name='Prod B', category=self.cat_b, cafe=self.cafe_b)

        self.sup_a = Supplier.objects.create(name='Sup A', cafe=self.cafe_a)
        self.sup_b = Supplier.objects.create(name='Sup B', cafe=self.cafe_b)

        self.req_a = PurchaseRequest.objects.create(user=self.user_a, cafe=self.cafe_a, status='pending')
        self.req_b = PurchaseRequest.objects.create(user=self.user_b, cafe=self.cafe_b, status='pending')

        self.po_a = PurchaseOrder.objects.create(
            order_number='ORD-A1', request=self.req_a, supplier=self.sup_a, status='sent'
        )
        self.po_b = PurchaseOrder.objects.create(
            order_number='ORD-B1', request=self.req_b, supplier=self.sup_b, status='sent'
        )

        self.inv_a = Invoice.objects.create(
            purchase_order=self.po_a, invoice_number='INV-A1', total_amount=Decimal('100.00'), cafe=self.cafe_a
        )
        self.inv_b = Invoice.objects.create(
            purchase_order=self.po_b, invoice_number='INV-B1', total_amount=Decimal('200.00'), cafe=self.cafe_b
        )

        self.pay_a = Payment.objects.create(
            invoice=self.inv_a, amount=Decimal('100.00'), payment_date='2026-11-01', created_by=self.user_a
        )
        self.pay_b = Payment.objects.create(
            invoice=self.inv_b, amount=Decimal('200.00'), payment_date='2026-11-01', created_by=self.user_b
        )

        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='mgr_a', password='testpass123')

    def test_supplier_idor_rejected(self):
        """User from Cafe A cannot access or modify Cafe B's supplier"""
        r1 = self.client.get(reverse('supplier_detail', kwargs={'cafe_code': 'cafe_a', 'supplier_id': self.sup_b.id}))
        self.assertEqual(r1.status_code, 404)

        r2 = self.client.post(reverse('supplier_delete', kwargs={'cafe_code': 'cafe_a', 'supplier_id': self.sup_b.id}))
        self.assertEqual(r2.status_code, 404)
        self.assertTrue(Supplier.objects.filter(id=self.sup_b.id).exists())

    def test_invoice_idor_and_bulk_delete_rejected(self):
        """User from Cafe A cannot view or bulk delete Cafe B's invoices"""
        r1 = self.client.get(reverse('invoice_detail', kwargs={'cafe_code': 'cafe_a', 'invoice_id': self.inv_b.id}))
        self.assertEqual(r1.status_code, 404)

        r2 = self.client.post(reverse('invoice_bulk_delete', kwargs={'cafe_code': 'cafe_a'}), {
            'invoice_ids': [str(self.inv_a.id), str(self.inv_b.id)]
        })
        self.assertEqual(r2.status_code, 302)
        # inv_a deleted, inv_b preserved!
        self.assertFalse(Invoice.objects.filter(id=self.inv_a.id).exists())
        self.assertTrue(Invoice.objects.filter(id=self.inv_b.id).exists())

    def test_purchase_order_idor_rejected(self):
        """User from Cafe A cannot access or update Cafe B's purchase order"""
        r1 = self.client.get(reverse('purchase_order_detail', kwargs={'cafe_code': 'cafe_a', 'order_id': self.po_b.id}))
        self.assertEqual(r1.status_code, 404)

        r2 = self.client.post(reverse('purchase_order_send', kwargs={'cafe_code': 'cafe_a', 'order_id': self.po_b.id}))
        self.assertEqual(r2.status_code, 404)

    def test_product_idor_rejected(self):
        """User from Cafe A cannot update or delete Cafe B's product"""
        r1 = self.client.post(reverse('product_delete', kwargs={'cafe_code': 'cafe_a', 'product_id': self.prod_b.id}))
        self.assertEqual(r1.status_code, 404)
        self.assertTrue(Product.objects.filter(id=self.prod_b.id).exists())

    def test_payment_idor_rejected(self):
        """User from Cafe A cannot view or delete Cafe B's payment"""
        r1 = self.client.get(reverse('payment_detail', kwargs={'cafe_code': 'cafe_a', 'payment_id': self.pay_b.id}))
        self.assertEqual(r1.status_code, 404)

        r2 = self.client.post(reverse('payment_delete', kwargs={'cafe_code': 'cafe_a', 'payment_id': self.pay_b.id}))
        self.assertEqual(r2.status_code, 404)
        self.assertTrue(Payment.objects.filter(id=self.pay_b.id).exists())

    def test_price_history_api_cross_tenant_empty(self):
        """PriceHistoryAPIView returns empty for product of other cafe"""
        r = self.client.get(reverse('api_price_history', kwargs={'cafe_code': 'cafe_a', 'pk': self.prod_b.id}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_signals_multiple_store_requesters_does_not_crash(self):
        """When multiple cafes have Requester(name='store'), invoice post_save does not crash"""
        Requester.objects.create(name='store', cafe=self.cafe_a)
        Requester.objects.create(name='store', cafe=self.cafe_b)

        # Approving invoice should trigger add_to_stock_on_store_invoice cleanly
        self.inv_b.status = 'approved'
        self.inv_b.approved_at = '2026-11-11T12:00:00Z'
        self.inv_b.save()  # must not raise MultipleObjectsReturned

    def test_can_switch_cafe_consistency(self):
        """Superadmin with cafe=None has can_switch_cafe=True across all CAFE_SELECTION_ROLES"""
        superadmin = User.objects.create_user(
            username='super_admin', password='testpass123', role='admin', cafe=None
        )
        self.assertTrue(superadmin.can_switch_cafe)
        self.assertTrue(superadmin.needs_cafe_selection)

    def test_login_superadmin_without_cafe_code_redirects_to_select_cafe(self):
        """Superadmin logging in without selecting cafe is redirected to select_cafe (not logged out)"""
        User.objects.create_user(
            username='sa_mgr', password='testpass123', role='manager', cafe=None
        )
        self.client.logout()
        r = self.client.post(reverse('login'), {
            'username': 'sa_mgr',
            'password': 'testpass123',
            'cafe_code': '',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse('select_cafe'))
