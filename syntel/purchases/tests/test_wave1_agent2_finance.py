from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe,
    Debt,
    DebtPayment,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentAccount,
    PaymentAllocation,
    PaymentLocation,
    PriceHistory,
    Product,
    PurchaseOrder,
    PurchaseRequest,
    Requester,
    StockItem,
    Supplier,
    SupplierEvaluation,
    Unit,
    Warehouse,
    WarehouseVoucher,
)
from purchases.views.helpers import _allocate_payment_across_invoices, get_cafe_from_request

User = get_user_model()


def _login_with_cafe(user, cafe):
    client = Client()
    client.force_login(user)
    session = client.session
    session['selected_cafe_id'] = cafe.id
    session['selected_cafe_code'] = cafe.code
    session.save()
    return client


class SignalsHardeningTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='C1', code='c1', is_active=True)
        self.user = User.objects.create_user(
            username='mgr1', password='p', role='manager', cafe=self.cafe
        )
        self.supplier = Supplier.objects.create(name='Sup1', cafe=self.cafe, status='active')
        self.warehouse = Warehouse.objects.create(cafe=self.cafe, code='w1', name='Main WH', is_active=True)
        self.store_req = Requester.objects.create(name='store', cafe=self.cafe)
        self.kg = Unit.objects.create(name='کیلوگرم', symbol='kg', cafe=self.cafe)
        self.gram = Unit.objects.create(name='گرم', symbol='g', cafe=self.cafe)

    def test_post_save_raw_flag_skips_signals(self):
        prod = Product.objects.create(name='Coffee', unit=self.kg, cafe=self.cafe)
        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='RAW-1',
            supplier=self.supplier,
            status='approved',
            approved_at=timezone.now(),
            approved_by=self.user,
            total_amount=Decimal('1000'),
        )
        # Clear any side effects and fire raw=True manually
        PriceHistory.objects.all().delete()
        WarehouseVoucher.objects.all().delete()
        InvoiceItem.objects.create(
            invoice=inv,
            product=prod,
            description='Coffee',
            requester=self.store_req,
            quantity=Decimal('2'),
            unit_price=Decimal('500'),
            line_total=Decimal('1000'),
        )
        post_save.send(sender=Invoice, instance=inv, created=False, raw=True)
        self.assertEqual(PriceHistory.objects.count(), 0)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)

    def test_stock_entry_converts_unit_and_skips_unitless_product(self):
        prod_kg = Product.objects.create(name='Sugar', unit=self.kg, cafe=self.cafe)
        prod_no_unit = Product.objects.create(name='Mystery', unit=None, cafe=self.cafe)
        # Pre-existing StockItem in grams!
        stock_gram = StockItem.objects.create(
            product=prod_kg, warehouse=self.warehouse, quantity=Decimal('500'), unit=self.gram
        )

        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='UNIT-1',
            supplier=self.supplier,
            status='draft',
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=inv,
            product=prod_kg,
            description='Sugar',
            requester=self.store_req,
            quantity=Decimal('2'),
            unit_price=Decimal('100000'),
            line_total=Decimal('200000'),
        )
        InvoiceItem.objects.create(
            invoice=inv,
            product=prod_no_unit,
            description='Mystery',
            requester=self.store_req,
            quantity=Decimal('1'),
            unit_price=Decimal('5000'),
            line_total=Decimal('5000'),
        )
        inv.status = 'approved'
        inv.approved_by = self.user
        inv.approved_at = timezone.now()
        inv.save()

        stock_gram.refresh_from_db()
        # 500g + 2kg (2000g) = 2500g
        self.assertEqual(stock_gram.quantity, Decimal('2500'))

    def test_stock_reversed_on_invoice_cancel_and_delete(self):
        prod = Product.objects.create(name='Milk', unit=self.kg, cafe=self.cafe)
        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='REV-1',
            supplier=self.supplier,
            status='draft',
            created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=inv,
            product=prod,
            description='Milk',
            requester=self.store_req,
            quantity=Decimal('5'),
            unit_price=Decimal('1000'),
            line_total=Decimal('5000'),
        )
        inv.status = 'approved'
        inv.approved_by = self.user
        inv.approved_at = timezone.now()
        inv.save()

        stock = StockItem.objects.get(product=prod, warehouse=self.warehouse)
        self.assertEqual(stock.quantity, Decimal('5'))

        # Cancel invoice -> reverses stock back to 0
        inv.status = 'cancelled'
        inv.updated_by = self.user
        inv.save()

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('0'))

        # Re-approve (excluding reversed voucher allows new receipt) then delete
        inv.status = 'approved'
        inv.save()
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('5'))

        inv.delete()
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('0'))


class InvoiceWorkflowAndCalculationTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='C2', code='c2', is_active=True)
        self.mgr = User.objects.create_user(username='mgr2', password='p', role='manager', cafe=self.cafe)
        self.purch = User.objects.create_user(username='purch2', password='p', role='purchasing', cafe=self.cafe)
        self.supplier = Supplier.objects.create(name='Sup2', cafe=self.cafe, status='active')

    def test_invoice_approve_view_and_permissions(self):
        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='APP-1',
            supplier=self.supplier,
            status='sent',
            total_amount=Decimal('1000'),
        )
        url = reverse('invoice_approve', kwargs={'cafe_code': self.cafe.code, 'invoice_id': inv.id})

        # purchasing cannot approve
        c_purch = _login_with_cafe(self.purch, self.cafe)
        resp_forbidden = c_purch.post(url)
        self.assertEqual(resp_forbidden.status_code, 403)

        # manager can approve
        c_mgr = _login_with_cafe(self.mgr, self.cafe)
        resp = c_mgr.post(url)
        self.assertEqual(resp.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'approved')
        self.assertEqual(inv.approved_by, self.mgr)
        self.assertIsNotNone(inv.approved_at)

    def test_can_be_paid_and_send_cancel_guards(self):
        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='PAY-G-1',
            supplier=self.supplier,
            status='sent',
            total_amount=Decimal('1000'),
        )
        self.assertTrue(inv.can_be_paid())
        for st in ('approved', 'partially_paid', 'overdue'):
            inv.status = st
            self.assertTrue(inv.can_be_paid())
        for st in ('draft', 'paid', 'cancelled'):
            inv.status = st
            self.assertFalse(inv.can_be_paid())

        # Paid invoice cannot be sent back to 'sent' or cancelled
        inv.status = 'paid'
        inv.save()
        c_mgr = _login_with_cafe(self.mgr, self.cafe)
        c_mgr.post(reverse('invoice_send', kwargs={'cafe_code': self.cafe.code, 'invoice_id': inv.id}))
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'paid')

        c_mgr.post(reverse('invoice_cancel', kwargs={'cafe_code': self.cafe.code, 'invoice_id': inv.id}))
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'paid')

    def test_invoice_save_recalc_matches_update_and_zeros_when_items_deleted(self):
        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='CALC-1',
            supplier=self.supplier,
            status='draft',
            tax_rate=Decimal('10'),
            discount_rate=Decimal('5'),
            shipping_cost=Decimal('200'),
            cash_discount=Decimal('50'),
        )
        it = InvoiceItem.objects.create(
            invoice=inv,
            description='Item A',
            quantity=Decimal('2'),
            unit_price=Decimal('1000'),
            tax_amount=Decimal('100'),
            discount_amount=Decimal('50'),
            line_total=Decimal('2050'),
        )
        inv.save()
        inv.refresh_from_db()
        # subtotal=2000, tax=100 + 200(10%) = 300, discount=50 + 100(5%) = 150
        # total = 2000 + 300 - 150 + 200 - 50 = 2300
        self.assertEqual(inv.subtotal, Decimal('2000'))
        self.assertEqual(inv.tax_amount, Decimal('300'))
        self.assertEqual(inv.discount_amount, Decimal('150'))
        self.assertEqual(inv.total_amount, Decimal('2300'))
        # Draft must NOT be promoted to sent!
        self.assertEqual(inv.status, 'draft')

        # Delete all items -> totals must become 0 (plus shipping - cash_discount)
        it.delete()
        inv.shipping_cost = Decimal('0')
        inv.cash_discount = Decimal('0')
        inv.save()
        inv.refresh_from_db()
        self.assertEqual(inv.subtotal, Decimal('0'))
        self.assertEqual(inv.total_amount, Decimal('0'))
        self.assertEqual(inv.status, 'draft')


class InvoiceRegisterAndPaymentsTests(TestCase):
    def setUp(self):
        self.cafe1 = Cafe.objects.create(name='C3', code='c3', is_active=True)
        self.cafe2 = Cafe.objects.create(name='C4', code='c4', is_active=True)
        self.mgr = User.objects.create_user(username='mgr3', password='p', role='manager', cafe=self.cafe1)
        self.sup1 = Supplier.objects.create(name='Sup3', cafe=self.cafe1, status='active')
        self.prod1 = Product.objects.create(name='Espresso Beans', cafe=self.cafe1)
        self.prod2 = Product.objects.create(name='Oat Milk', cafe=self.cafe1)

    def test_invoice_register_handles_non_contiguous_indices_and_resolves_product(self):
        client = _login_with_cafe(self.mgr, self.cafe1)
        url = reverse('invoice_register', kwargs={'cafe_code': self.cafe1.code})
        resp = client.post(url, {
            'supplier': str(self.sup1.id),
            'invoice_number': 'REG-NONCONTIG-1',
            'invoice_date': '1404/01/15',
            'request_date': '1404/01/15',
            'description_0': 'Espresso Beans',
            'product_id_0': str(self.prod1.id),
            'quantity_0': '2',
            'unit_price_0': '1000',
            # index 1 deleted!
            'description_2': 'oat milk',  # typed manually, case-insensitive match
            'product_id_2': '',
            'quantity_2': '3',
            'unit_price_2': '500',
        })
        self.assertEqual(resp.status_code, 302)
        inv = Invoice.objects.get(invoice_number='REG-NONCONTIG-1', cafe=self.cafe1)
        self.assertEqual(inv.total_amount, Decimal('3500'))
        items = list(inv.invoice_items.order_by('id'))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].product_id, self.prod1.id)
        self.assertEqual(items[1].product_id, self.prod2.id)

    def test_payment_list_total_and_allocation_guards(self):
        inv1 = Invoice.objects.create(
            cafe=self.cafe1, invoice_number='P1', supplier=self.sup1,
            status='sent', total_amount=Decimal('1000')
        )
        inv2 = Invoice.objects.create(
            cafe=self.cafe1, invoice_number='P2', supplier=self.sup1,
            status='sent', total_amount=Decimal('2000')
        )
        bulk_pay = Payment.objects.create(
            amount=Decimal('3000'), payment_date=timezone.localdate(),
            created_by=self.mgr, payment_status='completed'
        )
        PaymentAllocation.objects.create(payment=bulk_pay, invoice=inv1, amount=Decimal('1000'))
        PaymentAllocation.objects.create(payment=bulk_pay, invoice=inv2, amount=Decimal('2000'))

        client = _login_with_cafe(self.mgr, self.cafe1)
        resp = client.get(reverse('payment_list', kwargs={'cafe_code': self.cafe1.code}))
        self.assertEqual(resp.status_code, 200)
        # Must be 3000, not 6000!
        self.assertEqual(resp.context['total_amount'], Decimal('3000'))

        # Allocating again onto already-settled invoices must not create payments
        created, allocated, rem = _allocate_payment_across_invoices(
            invoices_list=[inv1, inv2],
            total_amount=Decimal('500'),
            payment_date=timezone.localdate(),
            payment_location=None,
            reference_number='',
            notes='',
            created_by=self.mgr,
        )
        self.assertEqual(created, [])
        self.assertEqual(allocated, [])
        self.assertEqual(rem, Decimal('500'))

    def test_get_cafe_from_request_checks_user_access(self):
        rf = RequestFactory()
        req = rf.get('/')
        req.user = self.mgr  # belongs to cafe1 only
        req.session = {'selected_cafe_id': self.cafe2.id}
        self.assertIsNone(get_cafe_from_request(req))

        req.session = {'selected_cafe_id': self.cafe1.id}
        self.assertEqual(get_cafe_from_request(req), self.cafe1)


class SuppliersAndDebtsTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='C5', code='c5', is_active=True)
        self.admin = User.objects.create_user(username='adm5', password='p', role='admin', cafe=self.cafe)
        self.ceo = User.objects.create_user(username='ceo5', password='p', role='ceo', cafe=self.cafe)
        self.sup = Supplier.objects.create(name='Sup5', cafe=self.cafe, status='active')
        self.acc = PaymentAccount.objects.create(cafe=self.cafe, bank_name='Melli', is_active=True)

    def test_supplier_evaluation_create_no_typeerror_and_duplicate_handled(self):
        client = _login_with_cafe(self.admin, self.cafe)
        url = reverse('supplier_evaluation_create', kwargs={'cafe_code': self.cafe.code, 'supplier_id': self.sup.id})
        resp = client.post(url, {
            'quality_rating': '5',
            'delivery_rating': '4',
            'price_rating': '4',
            'communication_rating': '5',
            'comments': 'Good',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SupplierEvaluation.objects.filter(supplier=self.sup).count(), 1)

        # Duplicate on same day does not 500
        resp_dup = client.post(url, {'quality_rating': '3'})
        self.assertEqual(resp_dup.status_code, 200)
        self.assertEqual(SupplierEvaluation.objects.filter(supplier=self.sup).count(), 1)

    def test_debt_payment_edit_cap_close_remainder_and_offlist_delete(self):
        debt = Debt.objects.create(
            company='Sup5', supplier=self.sup, amount=Decimal('2000000'),
            debt_date=timezone.localdate(), cafe=self.cafe, created_by=self.admin,
        )
        c_ceo = _login_with_cafe(self.ceo, self.cafe)
        # Pay 1,500,000 with close_remainder=1 -> status becomes paid
        c_ceo.post(reverse('debt_pay', kwargs={'cafe_code': self.cafe.code, 'debt_id': debt.id}), {
            'paid_amount': '1500000',
            'close_remainder': '1',
            'account_id': str(self.acc.id),
        })
        debt.refresh_from_db()
        self.assertEqual(debt.status, 'paid')
        dp = debt.payments.first()

        # Attempt to edit payment above debt.amount -> rejected
        c_ceo.post(reverse('debt_payment_edit', kwargs={'cafe_code': self.cafe.code, 'payment_id': dp.id}), {
            'amount': '2500000',
        })
        dp.refresh_from_db()
        self.assertEqual(dp.amount, Decimal('1500000'))

        # Edit description only -> status remains 'paid'
        c_ceo.post(reverse('debt_payment_edit', kwargs={'cafe_code': self.cafe.code, 'payment_id': dp.id}), {
            'amount': '1500000',
            'description': 'Updated note',
        })
        debt.refresh_from_db()
        self.assertEqual(debt.status, 'paid')

        # Off-list debt payment delete removes orphan off-list debt
        c_ceo.post(reverse('debt_pay_offlist', kwargs={'cafe_code': self.cafe.code}), {
            'company': 'OffList Co',
            'amount': '500000',
        })
        off_debt = Debt.objects.get(company='OffList Co', is_off_list=True)
        off_dp = off_debt.payments.first()
        c_ceo.post(reverse('debt_payment_delete', kwargs={'cafe_code': self.cafe.code, 'payment_id': off_dp.id}))
        self.assertFalse(Debt.objects.filter(pk=off_debt.pk).exists())
