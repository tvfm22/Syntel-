# -*- coding: utf-8 -*-
"""Comprehensive unit & integration tests for Agent 2 (Data Integrity, Models & Services)."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.db.models.signals import post_save
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from purchases.forms import PurchaseItemForm
from purchases.models import (
    Cafe,
    Category,
    CogsReport,
    CogsReportLine,
    Debt,
    DebtPayment,
    Invoice,
    InvoiceItem,
    KukerItem,
    KukerItemCostHistory,
    KukerRecipeLine,
    Notification,
    OfficerAssignment,
    Payment,
    PaymentAllocation,
    PaymentLocation,
    Product,
    PurchaseDraft,
    PurchaseDraftItem,
    PurchaseItem,
    PurchaseOrder,
    PurchaseRequest,
    StockItem,
    StockTransaction,
    Supplier,
    SupplierEvaluation,
    Unit,
    Warehouse,
    WarehouseVoucher,
    WarehouseVoucherItem,
)
from purchases.services.baba_adam_menu import import_baba_adam_menu
from purchases.services.inventory import (
    VoucherStateError,
    VoucherValidationError,
    apply_stock_delta,
    finalize_voucher,
    next_voucher_number,
    reopen_officer_assignments,
    reverse_voucher,
)
from purchases.services.menu_cogs import MenuGraph
from purchases.services.product_sync import sync_product, sync_raw_material
from purchases.services.purchase_approval import evaluate_draft_items_ceo_status
from purchases.services.receipt_gate import (
    pending_receipt_count,
    receipt_gate_applies,
    receipt_gate_context,
)
from purchases.services.recipe_gate import (
    bypasses_recipe_gate,
    get_or_create_rd_category,
    is_used_in_cogs_recipe,
)
from purchases.services.units import _to_decimal, normalize_package_price, units_are_compatible

User = get_user_model()


class InvoiceAndPaymentIntegrityTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='کافه الف', code='cafe_a', is_active=True)
        self.user = User.objects.create_user(
            username='inv_user', password='p', role='accounting', cafe=self.cafe,
        )
        self.supplier = Supplier.objects.create(name='تامین‌کننده تست', cafe=self.cafe)
        self.loc = PaymentLocation.objects.create(name='کارت به کارت', cafe=self.cafe)

    def _make_invoice(self, number, total_amount, status='approved'):
        return Invoice.objects.create(
            cafe=self.cafe,
            invoice_number=number,
            supplier=self.supplier,
            total_amount=Decimal(str(total_amount)),
            due_date=timezone.localdate() + timedelta(days=7),
            status=status,
            approved_at=timezone.now() if status == 'approved' else None,
            created_by=self.user,
        )

    def test_bulk_payment_with_allocation_does_not_double_count_and_delete_recalculates(self):
        inv1 = self._make_invoice('INV-A1', '1000000')
        inv2 = self._make_invoice('INV-A2', '1000000')

        payment = Payment.objects.create(
            amount=Decimal('1500000'),
            payment_location=self.loc,
            payment_status='completed',
            created_by=self.user,
        )
        PaymentAllocation.objects.create(payment=payment, invoice=inv1, amount=Decimal('1000000'))
        PaymentAllocation.objects.create(payment=payment, invoice=inv2, amount=Decimal('500000'))

        inv1.refresh_from_db()
        inv2.refresh_from_db()
        self.assertEqual(inv1.get_total_paid(), Decimal('1000000.00'))
        self.assertEqual(inv1.payment_status, 'paid')
        self.assertEqual(inv2.get_total_paid(), Decimal('500000.00'))
        self.assertEqual(inv2.payment_status, 'partial')

        # Even if M2M is also linked, allocations prevent double-counting
        payment.invoices.add(inv1, inv2)
        inv1.refresh_from_db()
        inv2.refresh_from_db()
        self.assertEqual(inv1.get_total_paid(), Decimal('1000000.00'))
        self.assertEqual(inv2.get_total_paid(), Decimal('500000.00'))

        # Deleting the payment recalculates both invoices back to unpaid & approved (not draft)
        payment.delete()
        inv1.refresh_from_db()
        inv2.refresh_from_db()
        self.assertEqual(inv1.get_total_paid(), Decimal('0'))
        self.assertEqual(inv1.payment_status, 'unpaid')
        self.assertEqual(inv1.status, 'approved')
        self.assertEqual(inv2.get_total_paid(), Decimal('0'))
        self.assertEqual(inv2.payment_status, 'unpaid')
        self.assertEqual(inv2.status, 'approved')

    def test_legacy_m2m_payment_across_two_invoices_does_not_double_credit(self):
        inv1 = self._make_invoice('INV-M1', '1000')
        inv2 = self._make_invoice('INV-M2', '1000')
        payment = Payment.objects.create(
            amount=Decimal('1000'),
            payment_status='completed',
            created_by=self.user,
        )
        payment.invoices.add(inv1, inv2)
        inv1.refresh_from_db()
        inv2.refresh_from_db()
        self.assertEqual(inv1.get_total_paid() + inv2.get_total_paid(), Decimal('1000'))

    def test_invoice_save_preserves_item_tax_discount_cash_discount_and_fires_once(self):
        inv = self._make_invoice('INV-TAX1', '0', status='draft')
        inv.shipping_cost = Decimal('5000')
        inv.cash_discount = Decimal('10000')
        inv.save()
        InvoiceItem.objects.create(
            invoice=inv,
            description='قلم تست',
            quantity=Decimal('2'),
            unit_price=Decimal('50000'),
            tax_amount=Decimal('9000'),
            discount_amount=Decimal('4000'),
        )

        save_calls = []

        def _counter(sender, instance, **kwargs):
            if instance.pk == inv.pk:
                save_calls.append(kwargs)

        post_save.connect(_counter, sender=Invoice)
        try:
            inv.save()
        finally:
            post_save.disconnect(_counter, sender=Invoice)

        self.assertEqual(len(save_calls), 1)
        inv.refresh_from_db()
        self.assertEqual(inv.subtotal, Decimal('100000.00'))
        self.assertEqual(inv.tax_amount, Decimal('9000.00'))
        self.assertEqual(inv.discount_amount, Decimal('4000.00'))
        # 100000 + 9000 + 5000 (shipping) - 4000 (item disc) - 10000 (cash_discount) = 100000
        self.assertEqual(inv.total_amount, Decimal('100000.00'))

        # Saving non-financial update_fields must NOT trigger recalculation
        inv.notes = 'یادداشت جدید'
        inv.total_amount = Decimal('123456.00')
        inv.save(update_fields=['notes'])
        self.assertEqual(inv.total_amount, Decimal('123456.00'))
        inv.refresh_from_db()
        self.assertEqual(inv.total_amount, Decimal('100000.00'))

    def test_failed_payment_does_not_downgrade_approved_invoice_to_draft_and_none_total(self):
        inv = self._make_invoice('INV-APP1', '50000', status='approved')
        Payment.objects.create(
            invoice=inv,
            amount=Decimal('50000'),
            payment_status='failed',
            created_by=self.user,
        )
        inv.refresh_from_db()
        self.assertEqual(inv.payment_status, 'unpaid')
        self.assertEqual(inv.status, 'approved')

        # total_amount=None guard
        inv_none = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='INV-NONE',
            supplier=self.supplier,
            total_amount=None,
            due_date=timezone.localdate(),
            status='sent',
        )
        self.assertEqual(inv_none.get_remaining_amount(), Decimal('0'))
        inv_none.update_payment_status()
        self.assertEqual(inv_none.status, 'sent')

    def test_unsaved_payment_str_does_not_crash(self):
        inv = self._make_invoice('INV-STR', '1000')
        p = Payment(invoice=inv, amount=Decimal('500'))
        self.assertIn('INV-STR', str(p))
        p_empty = Payment(amount=Decimal('500'))
        self.assertIn('500', str(p_empty))


class DataProtectionAndModelHardeningTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='کافه ب', code='cafe_b', is_active=True)
        self.user = User.objects.create_user(username='creator_u', password='p', role='purchasing', cafe=self.cafe)
        self.requester = User.objects.create_user(username='req_u', password='p', role='requester', cafe=self.cafe)
        self.supplier = Supplier.objects.create(name='تامین‌کننده الف', cafe=self.cafe)
        self.unit = Unit.objects.create(name='کیلوگرم', symbol='kg', cafe=self.cafe)
        self.wh = Warehouse.objects.create(name='انبار مرکزی', code='WH1', cafe=self.cafe)
        self.product = Product.objects.create(name='شکر', unit=self.unit, cafe=self.cafe)
        self.stock = StockItem.objects.create(
            product=self.product, warehouse=self.wh, quantity=Decimal('10'), unit=self.unit,
        )

    def test_deleting_user_sets_null_without_wiping_audit_records_and_ledgers_are_protected(self):
        pr = PurchaseRequest.objects.create(user=self.requester, cafe=self.cafe, status='pending')
        po = PurchaseOrder.objects.create(
            order_number='PO-PROT-1', request=pr, supplier=self.supplier, created_by=self.user,
        )
        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='INV-PROT-1',
            purchase_order=po,
            supplier=self.supplier,
            total_amount=Decimal('500000'),
            due_date=timezone.localdate(),
            created_by=self.user,
        )
        pay = Payment.objects.create(
            invoice=inv, amount=Decimal('100000'), payment_status='completed', created_by=self.user,
        )
        StockTransaction.objects.create(
            stock_item=self.stock, transaction_type='initial', quantity=Decimal('10'),
            unit=self.unit, previous_quantity=Decimal('0'), new_quantity=Decimal('10'),
        )
        debt = Debt.objects.create(company='شرکت تست', amount=Decimal('100000'), cafe=self.cafe)
        DebtPayment.objects.create(debt=debt, company='شرکت تست', amount=Decimal('50000'), cafe=self.cafe)

        # Financial/inventory parents are PROTECTED
        with self.assertRaises(ProtectedError):
            self.supplier.delete()
        with self.assertRaises(ProtectedError):
            po.delete()
        with self.assertRaises(ProtectedError):
            inv.delete()
        with self.assertRaises(ProtectedError):
            self.product.delete()
        with self.assertRaises(ProtectedError):
            self.stock.delete()
        with self.assertRaises(ProtectedError):
            debt.delete()

        # Deleting creator user sets NULL and preserves records
        self.user.delete()
        self.requester.delete()
        pr.refresh_from_db()
        po.refresh_from_db()
        inv.refresh_from_db()
        pay.refresh_from_db()
        self.assertIsNone(pr.user)
        self.assertIsNone(po.created_by)
        self.assertIsNone(inv.created_by)
        self.assertIsNone(pay.created_by)

    def test_persian_payment_locations_and_supplier_decimal_metrics(self):
        loc1 = PaymentLocation.objects.create(name='حساب بانکی اول', cafe=self.cafe)
        loc2 = PaymentLocation.objects.create(name='حساب بانکی دوم', cafe=self.cafe)
        self.assertNotEqual(loc1.code, loc2.code)
        self.assertTrue(loc1.code)
        self.assertTrue(loc2.code)

        # Supplier.update_performance_metrics with manual invoice + Decimal multipliers
        pr = PurchaseRequest.objects.create(user=self.requester, cafe=self.cafe, status='pending')
        po = PurchaseOrder.objects.create(
            order_number='PO-PERF-1',
            request=pr,
            supplier=self.supplier,
            status='completed',
            delivery_date=timezone.localdate(),
            actual_delivery_date=timezone.localdate(),
            quality_status='passed',
        )
        SupplierEvaluation.objects.create(
            supplier=self.supplier,
            evaluator=self.user,
            quality_rating=5,
            delivery_rating=5,
            price_rating=4,
            communication_rating=5,
        )
        inv = Invoice.objects.create(
            cafe=self.cafe,
            invoice_number='INV-MAN-1',
            supplier=self.supplier,
            total_amount=Decimal('200000'),
            due_date=timezone.localdate() + timedelta(days=5),
            status='approved',
        )
        Payment.objects.create(
            invoice=inv, amount=Decimal('200000'), payment_status='completed', created_by=self.user,
        )
        self.supplier.update_performance_metrics()
        self.supplier.refresh_from_db()
        self.assertGreater(self.supplier.rating, Decimal('0'))
        self.assertEqual(self.supplier.payment_reliability_score, Decimal('100.00'))

    def test_category_cycle_prevention_and_chained_manager_aliasing(self):
        c1 = Category.objects.create(name='مادر', cafe=self.cafe)
        c2 = Category.objects.create(name='فرزند', parent=c1, cafe=self.cafe)
        c1.parent = c2
        with self.assertRaises(ValidationError):
            c1.clean()

        pr = PurchaseRequest.objects.create(user=self.requester, cafe=self.cafe, status='pending')
        po = PurchaseOrder.objects.create(order_number='PO-ALIAS', request=pr, supplier=self.supplier)
        inv = Invoice.objects.create(
            cafe=self.cafe, invoice_number='INV-ALIAS', order=po, due_date=timezone.localdate(),
        )
        self.assertEqual(Invoice.objects.filter(cafe=self.cafe).filter(order=po).get(), inv)

        notif = Notification.objects.create(user=self.requester, title='T', message='M', cafe=self.cafe)
        self.assertEqual(Notification.objects.filter(cafe=self.cafe).filter(user=self.requester).get(), notif)


class UnitAndMenuCogsTests(TestCase):
    def test_units_compatibility_and_persian_numerals(self):
        self.assertTrue(units_are_compatible('کیلوگرم', 'گرم'))
        self.assertTrue(units_are_compatible('لیتر', 'میل'))
        self.assertTrue(units_are_compatible('عدد', 'عدد'))
        self.assertFalse(units_are_compatible('عدد', 'کارتن'))
        self.assertFalse(units_are_compatible('کیلوگرم', 'عدد'))
        self.assertEqual(_to_decimal('۱۲٬۵۰۰'), Decimal('12500'))
        self.assertIsNone(_to_decimal('NaN'))
        self.assertIsNone(_to_decimal('Infinity'))
        price, u = normalize_package_price(100000, 3, 'عدد')
        self.assertEqual(price, Decimal('33333.3333'))
        self.assertEqual(u, 'عدد')

    def test_validate_against_workbook_catches_mismatch_and_cl_conversion(self):
        cafe = Cafe.objects.create(name='کافه تست', code='cogs_t')
        raw = KukerItem.objects.create(
            cafe=cafe, name='سیروپ', key='سیروپ', kind=KukerItem.KIND_RAW,
            out_unit='میل', price=Decimal('1000'), is_custom=True,
        )
        dish = KukerItem.objects.create(
            cafe=cafe, name='نوشیدنی', key='نوشیدنی', kind=KukerItem.KIND_DISH,
            out_qty=Decimal('1'), out_unit='پرس', stated_fee=Decimal('5000'), is_custom=True,
        )
        # 5 سی ال = 50 میل * 1000 = 50000 != stated_fee 5000
        KukerRecipeLine.objects.create(
            item=dish, material=raw, quantity=Decimal('5'), unit='سی ال',
        )
        graph = MenuGraph.from_db(cafe=cafe)
        self.assertEqual(graph.computed_unit_cost(str(dish.pk)), Decimal('50000'))
        bad = graph.validate_against_workbook()
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][0], 'نوشیدنی')

    def test_baba_adam_reimport_preserves_report_lines_and_cost_history(self):
        from django.core.management import call_command

        cafe = Cafe.objects.create(code='baba_adam', name='بابا آدم', is_active=True)
        call_command('import_catalog_csv', cafe='baba_adam')
        import_baba_adam_menu(cafe=cafe)

        dish = KukerItem.objects.filter(cafe=cafe, kind=KukerItem.KIND_DISH).first()
        dish_id = dish.pk
        hist_count = KukerItemCostHistory.objects.filter(item=dish).count()
        self.assertGreater(hist_count, 0)

        report = CogsReport.objects.create(
            cafe=cafe,
            period_start=timezone.localdate(),
            period_end=timezone.localdate(),
        )
        CogsReportLine.objects.create(report=report, item=dish, qty=Decimal('3'))

        # Re-import with apply_prices=False must preserve IDs, CogsReportLine, KukerItemCostHistory, and existing prices
        raw_sample = KukerItem.objects.filter(cafe=cafe, kind=KukerItem.KIND_RAW, price__gt=0).first()
        old_price = raw_sample.price
        import_baba_adam_menu(cafe=cafe, apply_prices=False)

        self.assertTrue(KukerItem.objects.filter(pk=dish_id).exists())
        self.assertTrue(CogsReportLine.objects.filter(report=report, item_id=dish_id).exists())
        self.assertGreaterEqual(KukerItemCostHistory.objects.filter(item_id=dish_id).count(), hist_count)
        raw_sample.refresh_from_db()
        self.assertEqual(raw_sample.price, old_price)


class InventoryServiceHardeningTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='کافه انبار', code='inv_c')
        self.other_cafe = Cafe.objects.create(name='کافه دیگر', code='inv_other')
        self.officer_a = User.objects.create_user(username='off_a', password='p', role='warehouse', cafe=self.cafe)
        self.officer_b = User.objects.create_user(username='off_b', password='p', role='warehouse', cafe=self.cafe)
        self.manager = User.objects.create_user(username='mgr_i', password='p', role='manager', cafe=self.cafe)
        self.requester = User.objects.create_user(username='req_i', password='p', role='requester', cafe=self.cafe)
        self.kg = Unit.objects.create(name='کیلوگرم', symbol='kg', cafe=self.cafe)
        self.liter = Unit.objects.create(name='لیتر', symbol='L', cafe=self.cafe)
        self.wh1 = Warehouse.objects.create(name='انبار ۱', code='W1', cafe=self.cafe)
        self.wh2 = Warehouse.objects.create(name='انبار ۲', code='W2', cafe=self.cafe)
        self.prod = Product.objects.create(name='قهوه', unit=self.kg, cafe=self.cafe)
        self.foreign_prod = Product.objects.create(name='چای خارجی', unit=self.kg, cafe=self.other_cafe)
        self.stock1 = StockItem.objects.create(product=self.prod, warehouse=self.wh1, quantity=Decimal('20'), unit=self.kg)
        self.stock2_wrong_unit = StockItem.objects.create(
            product=self.prod, warehouse=self.wh2, quantity=Decimal('5'), unit=self.liter,
        )

    def test_reopen_only_refunds_reopening_officer(self):
        pr = PurchaseRequest.objects.create(user=self.requester, cafe=self.cafe, status='pending')
        item = PurchaseItem.objects.create(request=pr, product=self.prod, name='قهوه', quantity=Decimal('5'), unit='کیلوگرم')

        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', assigned_to=self.officer_a,
            status='fulfilled', stock_item=self.stock1, supplied_quantity=Decimal('3'),
        )
        apply_stock_delta(
            self.stock1, Decimal('-3'), cafe=self.cafe, created_by=self.officer_a,
            reference_type='officer_assignment', reference_id=item.id,
        )
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', assigned_to=self.officer_b,
            status='fulfilled', stock_item=self.stock1, supplied_quantity=Decimal('2'),
        )
        apply_stock_delta(
            self.stock1, Decimal('-2'), cafe=self.cafe, created_by=self.officer_b,
            reference_type='officer_assignment', reference_id=item.id,
        )
        self.stock1.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal('15'))

        # Officer A reopens -> only 3 refunded, Officer B stays fulfilled and can still reopen 2 later
        refunded_a = reopen_officer_assignments(item, cafe=self.cafe, reopened_by=self.officer_a, reason='اصلاح')
        self.assertEqual(refunded_a, 1)
        self.stock1.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal('18'))

        refunded_b = reopen_officer_assignments(item, cafe=self.cafe, reopened_by=self.officer_b, reason='اصلاح ب')
        self.assertEqual(refunded_b, 1)
        self.stock1.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal('20'))

    def test_transfer_validates_destination_unit_and_foreign_product_and_zero_delta(self):
        with self.assertRaises(VoucherValidationError):
            apply_stock_delta(self.stock1, Decimal('0'), cafe=self.cafe, created_by=self.officer_a)

        # Transfer destination unit mismatch
        v = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='transfer', status='submitted',
            voucher_number=next_voucher_number(self.cafe.id, 'transfer'),
            source_warehouse=self.wh1, destination_warehouse=self.wh2, created_by=self.officer_a,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v, product=self.prod, stock_item=self.stock1, unit=self.kg, base_quantity=Decimal('2'),
        )
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.officer_a)

        # Foreign product rejected
        v2 = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='receipt', status='submitted',
            voucher_number=next_voucher_number(self.cafe.id, 'receipt'),
            destination_warehouse=self.wh1, created_by=self.officer_a,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v2, product=self.foreign_prod, unit=self.kg, base_quantity=Decimal('2'),
        )
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v2, finalized_by=self.officer_a)

    def test_reverse_adjustment_requires_authority_and_non_null_approver(self):
        v = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='adjustment', status='approved',
            voucher_number=next_voucher_number(self.cafe.id, 'adjustment'),
            source_warehouse=self.wh1, created_by=self.manager,
            approver=self.manager, approved_at=timezone.now(),
        )
        WarehouseVoucherItem.objects.create(
            voucher=v, product=self.prod, stock_item=self.stock1, unit=self.kg, base_quantity=Decimal('5'),
        )
        finalize_voucher(v, finalized_by=self.manager)

        with self.assertRaises(VoucherStateError):
            reverse_voucher(v, created_by=None)
        with self.assertRaises(VoucherStateError):
            reverse_voucher(v, created_by=self.officer_a)

        res = reverse_voucher(v, created_by=self.manager)
        self.assertTrue(res.applied)
        self.stock1.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal('20'))


class PurchaseApprovalProductSyncAndGatesTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='کافه خرید', code='pa_c')
        self.wo = User.objects.create_user(username='wo_pa', password='p', role='warehouse', cafe=self.cafe)
        self.dual = User.objects.create_user(
            username='dual_u', password='p', role='requester', secondary_role='warehouse', cafe=self.cafe,
        )
        self.cat = Category.objects.create(name='دسته عمومی', cafe=self.cafe)
        self.gram = Unit.objects.create(name='گرم', symbol='g', cafe=self.cafe)
        self.prod = Product.objects.create(
            name='پسته', category=self.cat, unit=self.gram,
            base_unit_price=Decimal('1000000'), smallest_unit='گرم', cafe=self.cafe,
        )

    def test_split_600m_order_across_two_lines_triggers_ceo_and_unit_mismatch_flagged(self):
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        d1 = PurchaseDraftItem.objects.create(
            draft=draft, product=self.prod, name='پسته', quantity=Decimal('300'), unit='گرم',
        )
        d2 = PurchaseDraftItem.objects.create(
            draft=draft, product=self.prod, name='پسته', quantity=Decimal('300'), unit='گرم',
        )
        res = evaluate_draft_items_ceo_status([d1, d2], cafe=self.cafe)
        self.assertTrue(res[d1.id]['needs_ceo'])
        self.assertIn('high_value', res[d1.id]['reason_codes'])
        self.assertTrue(res[d2.id]['needs_ceo'])
        self.assertIn('high_value', res[d2.id]['reason_codes'])

        # Ordering کارتن for گرم-priced product triggers unit_mismatch
        d3 = PurchaseDraftItem.objects.create(
            draft=draft, product=self.prod, name='پسته', quantity=Decimal('2'), unit='کارتن',
        )
        res_u = evaluate_draft_items_ceo_status([d3], cafe=self.cafe)
        self.assertTrue(res_u[d3.id]['needs_ceo'])
        self.assertIn('unit_mismatch', res_u[d3.id]['reason_codes'])

    def test_product_sync_rename_does_not_hijack_and_price_clear_syncs(self):
        p1 = Product.objects.create(name='کالا۱', cafe=self.cafe, base_unit_price=Decimal('100'), smallest_unit='گرم')
        p2 = Product.objects.create(name='کالا۲', cafe=self.cafe, base_unit_price=Decimal('200'), smallest_unit='گرم')
        ki1 = sync_product(p1)
        ki2 = sync_product(p2)
        self.assertIsNotNone(ki1)
        self.assertIsNotNone(ki2)

        # Clear price -> synced KukerItem price becomes None
        p1.base_unit_price = None
        p1.save(update_fields=['base_unit_price'])
        sync_product(p1)
        ki1.refresh_from_db()
        self.assertIsNone(ki1.price)

        # Rename p1 to an existing name on KukerItem owned by p2 -> unique key, doesn't steal ki2
        p1.name = 'کالا۲_جدید'
        sync_product(p1)
        ki2.refresh_from_db()
        self.assertEqual(ki2.product_id, p2.id)

    def test_gates_secondary_role_and_forms_zero_quantity(self):
        self.assertTrue(bypasses_recipe_gate(self.dual))
        self.assertTrue(receipt_gate_applies(self.dual))
        self.assertEqual(pending_receipt_count(self.dual, cafe=self.cafe), 0)
        self.assertIsNone(receipt_gate_context(self.dual, self.cafe, ''))

        # Zero-quantity recipe line does not pass recipe gate
        food_cat = Category.objects.create(name='مواد غذایی', cafe=self.cafe, requires_recipe=True)
        food_prod = Product.objects.create(name='کره', category=food_cat, unit=self.gram, cafe=self.cafe)
        raw_ki = KukerItem.objects.create(cafe=self.cafe, name='کره', key='کره', kind=KukerItem.KIND_RAW, product=food_prod)
        dish_ki = KukerItem.objects.create(cafe=self.cafe, name='کیک', key='کیک', kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس')
        # Zero-qty line is blocked by DB constraint, verify gate requires > 0
        self.assertFalse(is_used_in_cogs_recipe(food_prod, cafe=self.cafe))

        # PurchaseItemForm rejects quantity=0
        form = PurchaseItemForm(data={'name': 'شکر', 'quantity': '0', 'unit': 'گرم'})
        self.assertFalse(form.is_valid())
        self.assertIn('quantity', form.errors)
