# -*- coding: utf-8 -*-
"""Tests for comma-separated amount/price input boxes and backend parsing."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Category, Debt, DebtPayment, Invoice, KukerItem, Payment,
    PaymentLocation, Product, Supplier, Unit, Warehouse, WarehouseVoucher,
)
from purchases.templatetags.custom_filters import jalali_date, jalali_datetime, jalali_time, num2, number_format
from purchases.views.helpers import _parse_decimal


class CommaParsingHelperTests(TestCase):
    def test_parse_decimal_handles_commas_and_persian_arabic_digits(self):
        self.assertEqual(_parse_decimal("1,500,000"), Decimal("1500000"))
        self.assertEqual(_parse_decimal("۱٬۵۰۰٬۰۰۰"), Decimal("1500000"))
        self.assertEqual(_parse_decimal("۱،۵۰۰،۰۰۰"), Decimal("1500000"))
        self.assertEqual(_parse_decimal("٢٥٠,٠٠٠٫٧٥"), Decimal("250000.75"))
        self.assertEqual(_parse_decimal("  1,234.56  "), Decimal("1234.56"))
        self.assertIsNone(_parse_decimal(""))
        self.assertIsNone(_parse_decimal(None))
        self.assertIsNone(_parse_decimal("abc"))

    def test_template_filters_output_english_digits(self):
        from datetime import date, datetime, time
        self.assertEqual(number_format("1500000"), "1,500,000")
        self.assertEqual(number_format(None), "0")
        self.assertEqual(num2("1500000.50"), "1,500,000.5")
        self.assertRegex(jalali_date(date(2025, 3, 21)), r"^[0-9/]+$")
        self.assertRegex(jalali_datetime(datetime(2025, 3, 21, 10, 5)), r"^[0-9/: ]+$")
        self.assertEqual(jalali_time(time(9, 7)), "09:07")


class CommaAmountViewsAndTemplatesTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code="baba_adam", name="بابا آدم")
        User = get_user_model()
        self.admin = User.objects.create_superuser("comma_admin", "c@c.com", "pass1234", role="admin")
        self.ceo = User.objects.create_user("comma_ceo", "ceo@c.com", "pass1234", role="ceo")
        self.manager = User.objects.create_user("comma_mgr", "mgr@c.com", "pass1234", role="manager")
        self.client = Client()
        self.client.force_login(self.admin)

        self.unit = Unit.objects.create(cafe=self.cafe, name="کیلوگرم", symbol="kg")
        self.category = Category.objects.create(cafe=self.cafe, name="مواد غذایی", is_active=True)
        self.supplier = Supplier.objects.create(cafe=self.cafe, name="فروشگاه تست", status="active")
        self.payment_location = PaymentLocation.objects.create(cafe=self.cafe, code="CASH", name="صندوق")
        self.warehouse = Warehouse.objects.create(cafe=self.cafe, name="انبار مرکزی", code="WH1", is_active=True)
        self.product = Product.objects.create(
            cafe=self.cafe,
            name="شکر",
            unit=self.unit,
            category=self.category,
            base_unit_price=Decimal("150"),
            smallest_unit="گرم",
        )

    def url(self, name, **kwargs):
        kwargs.setdefault("cafe_code", self.cafe.code)
        return reverse(name, kwargs=kwargs)

    def test_amount_inputs_render_with_comma_classes_and_text_type(self):
        self.client.force_login(self.manager)
        checks = [
            (self.url("product_create"), ["id=\"base_unit_price\"", "js-comma"], ['type="number" id="base_unit_price"']),
            (self.url("supplier_create"), ["id=\"credit_limit\"", "js-comma"], ['type="number" id="credit_limit"']),
            (self.url("invoice_register"), ["money-input", "id=\"payment-amount\"", "js-comma"], []),
            (self.url("payment_create"), ["id=\"id_amount\"", "js-comma"], []),
            (self.url("voucher_create", voucher_type="receipt"), ["row-price js-comma", "formatPriceField"], ['type="number" class="row-price"']),
            (self.url("supplier_select") + f"?supplier_id={self.supplier.id}", ['name="amount" class="js-comma"'], ['type="number" name="amount"']),
        ]
        for path, must_contain, must_not_contain in checks:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            html = resp.content.decode("utf-8")
            for snippet in must_contain:
                self.assertIn(snippet, html, f"Missing {snippet} in {path}")
            for bad in must_not_contain:
                self.assertNotIn(bad, html, f"Unexpected {bad} in {path}")

        self.client.force_login(self.admin)
        debt = Debt.objects.create(cafe=self.cafe, company="بدهی تست", amount=Decimal("1500000"), created_by=self.admin)
        debt_pay = DebtPayment.objects.create(cafe=self.cafe, debt=debt, company=debt.company, amount=Decimal("500000"), paid_by=self.ceo)
        for path, must_contain, extra_headers in [
            (self.url("cogs_manage_items"), ['id="rawPrice" class="js-comma"', 'id="pmPrice" class="js-comma"'], {}),
            (self.url("debt_list"), ['id="amount" name="amount" class="form-control js-comma"'], {}),
            (self.url("debt_edit", debt_id=debt.id), ['id="edit_amount" name="amount" class="form-control js-comma"'], {}),
            (self.url("debt_payment_edit", payment_id=debt_pay.id), ['id="edit_pay_amount" name="amount" class="form-control js-comma"'], {}),
            (self.url("mobile_debt_dashboard"), ['id="m-debt-amount" name="amount" class="m-input js-comma"'], {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"}),
        ]:
            resp = self.client.get(path, **extra_headers)
            self.assertEqual(resp.status_code, 200, path)
            html = resp.content.decode("utf-8")
            for snippet in must_contain:
                self.assertIn(snippet, html, f"Missing {snippet} in {path}")

        self.client.force_login(self.ceo)
        resp_ceo = self.client.get(self.url("ceo_debt_payments"))
        self.assertEqual(resp_ceo.status_code, 200)
        html_ceo = resp_ceo.content.decode("utf-8")
        self.assertIn('id="off_amount" name="amount" class="form-control js-comma"', html_ceo)
        self.assertIn('id="pay_amount" name="paid_amount" class="form-control js-comma"', html_ceo)

    def test_product_create_accepts_comma_and_persian_base_unit_price(self):
        self.client.force_login(self.manager)
        resp = self.client.post(self.url("product_create"), {
            "name": "قهوه تست",
            "unit": self.unit.id,
            "category": self.category.id,
            "usage_general": "general",
            "base_unit_price": "۲۵۰٬۰۰۰.۵",
            "price_unit": "کیلوگرم",
        })
        self.assertEqual(resp.status_code, 302)
        p = Product.objects.get(cafe=self.cafe, name="قهوه تست")
        self.assertEqual(p.base_unit_price, Decimal("250.0005"))

    def test_debt_views_accept_comma_formatted_amounts(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.url("debt_list"), {
            "company": "تأمین‌کننده تست",
            "amount": "1,500,000",
            "priority": "medium",
        })
        self.assertEqual(resp.status_code, 302)
        debt = Debt.objects.get(cafe=self.cafe, company="تأمین‌کننده تست")
        self.assertEqual(debt.amount, Decimal("1500000"))

        resp_edit = self.client.post(self.url("debt_edit", debt_id=debt.id), {
            "company": "تأمین‌کننده تست",
            "amount": "۲٬۰۰۰٬۰۰۰",
            "priority": "high",
        })
        self.assertEqual(resp_edit.status_code, 302)
        debt.refresh_from_db()
        self.assertEqual(debt.amount, Decimal("2000000"))

        self.client.force_login(self.ceo)
        resp_pay = self.client.post(self.url("debt_pay", debt_id=debt.id), {
            "paid_amount": "500,000",
        })
        self.assertEqual(resp_pay.status_code, 302)
        payment = DebtPayment.objects.get(debt=debt)
        self.assertEqual(payment.amount, Decimal("500000"))

        resp_pay_edit = self.client.post(self.url("debt_payment_edit", payment_id=payment.id), {
            "amount": "750,000",
        })
        self.assertEqual(resp_pay_edit.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("750000"))

    def test_invoice_register_and_payment_accept_comma_and_persian_amounts(self):
        self.client.force_login(self.manager)
        resp = self.client.post(self.url("invoice_register"), {
            "supplier_name": self.supplier.name,
            "supplier": self.supplier.id,
            "invoice_number": "INV-COMMA-1",
            "invoice_date": "1404/01/15",
            "request_date": "1404/01/15",
            "total_cash_discount": "50,000",
            "shipping_cost": "100,000",
            "description_0": "شکر",
            "quantity_0": "2",
            "unit_price_0": "۱٬۰۰۰٬۰۰۰",
            "tax_amount_0": "10,000",
            "discount_amount_0": "20,000",
            "add_payment": "on",
            "payment_amount": "1,000,000",
            "payment_location_id": self.payment_location.id,
            "payment_date": "2026-04-04",
        })
        self.assertEqual(resp.status_code, 302)
        inv = Invoice.objects.get(cafe=self.cafe, invoice_number="INV-COMMA-1")
        self.assertEqual(inv.total_amount, Decimal("2040000.00"))
        self.assertEqual(inv.payment_records().first().amount, Decimal("1000000.00"))

        resp_p = self.client.post(self.url("payment_create_for_invoice", invoice_id=inv.id), {
            "amount": "500,000",
            "payment_location": self.payment_location.id,
            "payment_date": "1404/01/16",
        })
        self.assertEqual(resp_p.status_code, 302)
        self.assertEqual(inv.payment_records().count(), 2)

    def test_voucher_create_accepts_comma_formatted_unit_price(self):
        self.client.force_login(self.manager)
        resp = self.client.post(self.url("voucher_create", voucher_type="receipt"), {
            "dest_warehouse": self.warehouse.id,
            "voucher_date": "1404/01/15",
            "submit_action": "draft",
            "product_id[]": [str(self.product.id)],
            "unit_id[]": [str(self.unit.id)],
            "base_quantity[]": ["3"],
            "unit_price[]": ["1,250,000"],
            "item_description[]": [""],
        })
        self.assertEqual(resp.status_code, 302)
        voucher = WarehouseVoucher.objects.filter(cafe=self.cafe).latest("id")
        self.assertEqual(voucher.items.first().unit_price, Decimal("1250000.00"))

    def test_cogs_manage_items_accepts_comma_formatted_price(self):
        self.client.force_login(self.admin)
        raw = KukerItem.objects.create(
            cafe=self.cafe, name="اسپرسو", key="اسپرسو", kind=KukerItem.KIND_RAW,
            out_unit="گرم", price=Decimal("100"), is_custom=True,
        )
        resp = self.client.post(
            self.url("cogs_manage_items"),
            {"action": "price", "item_id": raw.id, "price": "۳۵۰٬۰۰۰", "unit": "kg"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        raw.refresh_from_db()
        self.assertEqual(raw.price, Decimal("350.0000"))

    def test_invoice_update_accepts_comma_and_persian_amounts(self):
        self.client.force_login(self.manager)
        inv = Invoice.objects.create(
            cafe=self.cafe,
            supplier=self.supplier,
            invoice_number="INV-SUP-1",
            invoice_date="2026-04-04",
            total_amount=Decimal("1000000.00"),
            created_by=self.manager,
        )
        resp = self.client.post(self.url("invoice_update", invoice_id=inv.id), {
            "invoice_number": "INV-SUP-1",
            "invoice_date": "1404/01/15",
            "tax_rate": "0",
            "discount_rate": "0",
            "shipping_cost": "150,000",
            "description_new_0": "شکر",
            "quantity_new_0": "1",
            "unit_price_new_0": "۱٬۰۰۰٬۰۰۰",
            "tax_amount_new_0": "0",
            "discount_amount_new_0": "0",
        })
        self.assertEqual(resp.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.shipping_cost, Decimal("150000.00"))
        self.assertEqual(inv.total_amount, Decimal("1150000.00"))
