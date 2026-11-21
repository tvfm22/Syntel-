from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from purchases.models import (
    Cafe,
    Category,
    Invoice,
    InvoiceItem,
    KukerItem,
    MenuSection,
    Notification,
    Payment,
    PaymentLocation,
    PaymentType,
    Product,
    PurchaseItem,
    PurchaseOrder,
    PurchaseRequest,
    StockItem,
    Supplier,
    Unit,
    User,
    Warehouse,
    WarehouseVoucher,
)
from purchases.models_debts import Debt
from purchases.templatetags.custom_filters import (
    cafe_url,
    get_item,
    iso_date,
    jalali_datetime,
    num2,
    number_format,
)
from purchases.views_analytics import get_date_range


class Agent3ViewsApiFrontendAuditTests(TestCase):
    def setUp(self):
        self.cafe_a = Cafe.objects.create(name="Cafe A", code="cafea", is_active=True)
        self.cafe_b = Cafe.objects.create(name="Cafe B", code="cafeb", is_active=True)

        self.unit_kg = Unit.objects.create(name="کیلوگرم", symbol="kg", cafe=self.cafe_a, is_active=True)
        self.unit_g = Unit.objects.create(name="گرم", symbol="g", cafe=self.cafe_a, is_active=True)
        self.unit_b = Unit.objects.create(name="کیلوگرم", symbol="kg", cafe=self.cafe_b, is_active=True)

        self.cat_a = Category.objects.create(name="قهوه", cafe=self.cafe_a, is_active=True)
        self.cat_food_a = Category.objects.create(
            name="مواد غذایی", cafe=self.cafe_a, is_active=True, requires_recipe=True
        )
        self.cat_b = Category.objects.create(name="قهوه ب", cafe=self.cafe_b, is_active=True)

        self.wh_a = Warehouse.objects.create(name="انبار A", cafe=self.cafe_a, is_active=True)
        self.wh_b = Warehouse.objects.create(name="انبار B", cafe=self.cafe_b, is_active=True)

        self.prod_a = Product.objects.create(
            name="دانه قهوه", category=self.cat_a, unit=self.unit_kg, cafe=self.cafe_a, is_active=True
        )
        self.prod_food_unlinked = Product.objects.create(
            name="شیر بدون رسپی",
            category=self.cat_food_a,
            unit=self.unit_kg,
            cafe=self.cafe_a,
            is_active=True,
        )
        self.prod_b = Product.objects.create(
            name="دانه ب", category=self.cat_b, unit=self.unit_b, cafe=self.cafe_b, is_active=True
        )

        self.supplier_a = Supplier.objects.create(name="تامین A", cafe=self.cafe_a, status="active")
        self.supplier_b = Supplier.objects.create(name="تامین B", cafe=self.cafe_b, status="active")

        self.admin_a = User.objects.create_user("adm_a", password="p", role="admin", cafe=self.cafe_a)
        self.manager_a = User.objects.create_user("mgr_a", password="p", role="manager", cafe=self.cafe_a)
        self.purchasing_a = User.objects.create_user("buy_a", password="p", role="purchasing", cafe=self.cafe_a)
        self.warehouse_a = User.objects.create_user("wh_a", password="p", role="warehouse", cafe=self.cafe_a)
        self.kitchen_a = User.objects.create_user("kit_a", password="p", role="kitchen", cafe=self.cafe_a)
        self.ceo_a = User.objects.create_user("ceo_a", password="p", role="ceo", cafe=self.cafe_a)
        self.finance_b = User.objects.create_user("fin_b", password="p", role="finance_manager", cafe=self.cafe_b)

        self.pay_loc_a = PaymentLocation.objects.create(name="بانک ملت", cafe=self.cafe_a, is_active=True)
        self.pay_type_a = PaymentType.objects.create(name="کارت به کارت", cafe=self.cafe_a, is_active=True)

    # ── 1. Auth, Role 403, Removed Routes & PO Security ──────────────────────

    def test_role_required_returns_403_for_wrong_role_and_302_for_anon(self):
        url = reverse("management_dashboard", kwargs={"cafe_code": self.cafe_a.code})
        anon_resp = self.client.get(url)
        self.assertEqual(anon_resp.status_code, 302)

        self.client.force_login(self.kitchen_a)
        forbidden_resp = self.client.get(url)
        self.assertEqual(forbidden_resp.status_code, 403)

    def test_po_detail_requires_auth_role_and_cafe_scoping(self):
        pr_b = PurchaseRequest.objects.create(user=self.finance_b, cafe=self.cafe_b)
        po_b = PurchaseOrder.objects.create(
            request=pr_b,
            supplier=self.supplier_b,
            created_by=self.finance_b,
            status="ordered",
        )
        url_b_via_a = reverse("purchase_order_detail", kwargs={"cafe_code": self.cafe_a.code, "order_id": po_b.id})

        self.assertEqual(self.client.get(url_b_via_a).status_code, 302)
        self.client.force_login(self.kitchen_a)
        self.assertEqual(self.client.get(url_b_via_a).status_code, 403)
        self.client.force_login(self.manager_a)
        self.assertEqual(self.client.get(url_b_via_a).status_code, 404)

    def test_po_transitions_require_post_and_valid_state_machine(self):
        pr_a = PurchaseRequest.objects.create(user=self.kitchen_a, cafe=self.cafe_a)
        po_a = PurchaseOrder.objects.create(
            request=pr_a,
            supplier=self.supplier_a,
            created_by=self.purchasing_a,
            status="ordered",
        )
        complete_url = reverse(
            "purchase_order_complete",
            kwargs={"cafe_code": self.cafe_a.code, "order_id": po_a.id},
        )
        self.client.force_login(self.manager_a)
        self.assertEqual(self.client.get(complete_url).status_code, 405)
        self.client.post(complete_url)
        po_a.refresh_from_db()
        self.assertEqual(po_a.status, "ordered")

    def test_exposed_test_routes_removed(self):
        self.client.force_login(self.manager_a)
        self.assertEqual(self.client.get(f"/{self.cafe_a.code}/test/filter/").status_code, 404)
        self.assertEqual(self.client.get(f"/{self.cafe_a.code}/dashboard/prices/test/").status_code, 404)

    def test_notifications_require_post_and_recipient_scoping(self):
        notif = Notification.objects.create(
            recipient=self.manager_a,
            cafe=self.cafe_a,
            title="t",
            message="m",
        )
        url = reverse("mark_notification_read", kwargs={"cafe_code": self.cafe_a.code, "notification_id": notif.id})
        self.client.force_login(self.manager_a)
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.force_login(self.kitchen_a)
        self.assertEqual(self.client.post(url).status_code, 404)
        notif.refresh_from_db()
        self.assertFalse(notif.is_read)

        self.client.force_login(self.manager_a)
        resp = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"success": True})
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    # ── 2. Invoicing, Payments, Jalali & Bulk Settlement ─────────────────────

    def test_supplier_create_requires_auth_and_role(self):
        url = reverse("supplier_create", kwargs={"cafe_code": self.cafe_a.code})
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.kitchen_a)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.purchasing_a)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_invoice_register_missing_supplier_does_not_500(self):
        self.client.force_login(self.purchasing_a)
        url = reverse("invoice_register", kwargs={"cafe_code": self.cafe_a.code})
        resp = self.client.post(url, {"invoice_number": "INV-X", "invoice_date": "1404/05/15"})
        self.assertEqual(resp.status_code, 200)

    def test_invoice_bulk_delete_scoped_to_cafe(self):
        inv_a = Invoice.objects.create(
            invoice_number="A-1", supplier=self.supplier_a, cafe=self.cafe_a, created_by=self.manager_a
        )
        inv_b = Invoice.objects.create(
            invoice_number="B-1", supplier=self.supplier_b, cafe=self.cafe_b, created_by=self.finance_b
        )
        self.client.force_login(self.manager_a)
        url = reverse("invoice_bulk_delete", kwargs={"cafe_code": self.cafe_a.code})
        resp = self.client.post(url, {"invoice_ids": [str(inv_b.id)]})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Invoice.objects.filter(id=inv_b.id).exists())
        self.assertTrue(Invoice.objects.filter(id=inv_a.id).exists())

    def test_invoice_update_jalali_zero_discount_tax_no_tax_on_tax(self):
        inv = Invoice.objects.create(
            invoice_number="INV-U1",
            supplier=self.supplier_a,
            cafe=self.cafe_a,
            created_by=self.purchasing_a,
            status="pending",
        )
        self.client.force_login(self.purchasing_a)
        url = reverse("invoice_update", kwargs={"cafe_code": self.cafe_a.code, "invoice_id": inv.id})
        resp = self.client.post(
            url,
            {
                "invoice_number": "INV-U1-EDIT",
                "invoice_date": "1404/05/15",
                "due_date": "1404/05/20",
                "discount_amount": "0",
                "tax_amount": "0",
                "shipping_cost": "0",
                "cash_discount": "10",
                "discount_rate": "0",
                "tax_rate": "10",
                "description_new_0": "دانه قهوه",
                "quantity_new_0": "2",
                "unit_price_new_0": "50",
                "tax_amount_new_0": "0",
                "discount_amount_new_0": "0",
            },
        )
        self.assertEqual(resp.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.invoice_date, date(2025, 8, 6))
        self.assertEqual(inv.due_date, date(2025, 8, 11))
        self.assertEqual(inv.subtotal, Decimal("100.00"))
        self.assertEqual(inv.discount_amount, Decimal("0.00"))
        self.assertEqual(inv.cash_discount, Decimal("10.00"))
        self.assertEqual(inv.tax_amount, Decimal("10.00"))
        self.assertEqual(inv.total_amount, Decimal("100.00"))

    def test_invoice_bulk_payment_allocates_exact_per_invoice_amounts(self):
        inv1 = Invoice.objects.create(
            invoice_number="BP-1",
            supplier=self.supplier_a,
            cafe=self.cafe_a,
            created_by=self.manager_a,
            total_amount=Decimal("100.00"),
            status="approved",
        )
        inv2 = Invoice.objects.create(
            invoice_number="BP-2",
            supplier=self.supplier_a,
            cafe=self.cafe_a,
            created_by=self.manager_a,
            total_amount=Decimal("250.00"),
            status="approved",
        )
        self.client.force_login(self.manager_a)
        url = reverse("invoice_bulk_payment", kwargs={"cafe_code": self.cafe_a.code})
        resp = self.client.post(url, {"invoice_ids": [str(inv1.id), str(inv2.id)]})
        self.assertEqual(resp.status_code, 302)
        inv1.refresh_from_db()
        inv2.refresh_from_db()
        self.assertEqual(inv1.payment_status, "paid")
        self.assertEqual(inv2.payment_status, "paid")
        p1 = Payment.objects.get(invoice=inv1)
        p2 = Payment.objects.get(invoice=inv2)
        self.assertEqual(p1.amount, Decimal("100.00"))
        self.assertEqual(p2.amount, Decimal("250.00"))

    def test_payment_update_redirect_and_delete_updates_invoice_status(self):
        inv = Invoice.objects.create(
            invoice_number="PD-1",
            supplier=self.supplier_a,
            cafe=self.cafe_a,
            created_by=self.manager_a,
            total_amount=Decimal("200.00"),
            status="approved",
        )
        pay = Payment.objects.create(
            invoice=inv,
            amount=Decimal("200.00"),
            payment_location=self.pay_loc_a,
            payment_status="completed",
            created_by=self.manager_a,
        )
        inv.update_payment_status()
        inv.refresh_from_db()
        self.assertEqual(inv.payment_status, "paid")

        self.client.force_login(self.manager_a)
        upd_url = reverse("payment_update", kwargs={"cafe_code": self.cafe_a.code, "payment_id": pay.id})
        upd_resp = self.client.post(
            upd_url,
            {
                "amount": "200.00",
                "payment_date": "1404/05/15",
                "payment_location": str(self.pay_loc_a.id),
                "payment_status": "completed",
            },
        )
        self.assertEqual(upd_resp.status_code, 302)
        self.assertIn(f"/payments/{pay.id}/", upd_resp.url)

        del_url = reverse("payment_delete", kwargs={"cafe_code": self.cafe_a.code, "payment_id": pay.id})
        del_resp = self.client.post(del_url)
        self.assertEqual(del_resp.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.payment_status, "unpaid")

    # ── 3. Inventory, Debts & COGS ───────────────────────────────────────────

    def test_debt_pay_blocks_overpayment_and_partial_keeps_pending(self):
        debt = Debt.objects.create(
            cafe=self.cafe_a,
            company="تامین A",
            supplier=self.supplier_a,
            amount=Decimal("500000"),
            created_by=self.manager_a,
        )
        self.client.force_login(self.ceo_a)
        pay_url = reverse("debt_pay", kwargs={"cafe_code": self.cafe_a.code, "debt_id": debt.id})

        over_resp = self.client.post(pay_url, {"paid_amount": "600000"})
        self.assertEqual(over_resp.status_code, 302)
        debt.refresh_from_db()
        self.assertEqual(debt.status, "pending")
        self.assertEqual(debt.payments.count(), 0)

        part_resp = self.client.post(pay_url, {"paid_amount": "200000"})
        self.assertEqual(part_resp.status_code, 302)
        debt.refresh_from_db()
        self.assertEqual(debt.status, "pending")
        self.assertEqual(debt.remaining, Decimal("300000"))

        self.client.post(pay_url, {"paid_amount": "300000"})
        debt.refresh_from_db()
        self.assertEqual(debt.status, "paid")

    def test_cogs_recipe_api_validates_unit_compatibility_and_converts_cost(self):
        section = MenuSection.objects.create(
            cafe=self.cafe_a, name="بار", kind=MenuSection.KIND_DISH, is_active=True
        )
        raw = KukerItem.objects.create(
            cafe=self.cafe_a,
            name="پودر کاکائو",
            key="پودر کاکائو",
            kind=KukerItem.KIND_RAW,
            out_unit="کیلوگرم",
        )
        menu = KukerItem.objects.create(
            cafe=self.cafe_a,
            name="هات چاکلت",
            key="هات چاکلت",
            kind=KukerItem.KIND_DISH,
            section=section,
            out_qty=Decimal("1"),
            out_unit="لیوان",
        )
        self.client.force_login(self.admin_a)
        url = reverse("cogs_recipe_api", kwargs={"cafe_code": self.cafe_a.code, "item_id": menu.id})

        bad_resp = self.client.post(
            url,
            data={"lines": [{"material_id": raw.id, "qty": 100, "unit": "عدد"}]},
            content_type="application/json",
        )
        self.assertEqual(bad_resp.status_code, 400)

        ok_resp = self.client.post(
            url,
            data={"lines": [{"material_id": raw.id, "qty": 500, "unit": "گرم", "fee": 1000}]},
            content_type="application/json",
        )
        self.assertEqual(ok_resp.status_code, 200)
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        payload = get_resp.json()
        self.assertEqual(Decimal(str(payload["item"]["cost_per"])), Decimal("500000"))

    # ── 4. Analytics, Dashboard APIs & Mobile APIs ───────────────────────────

    def test_analytics_and_dashboard_apis_require_auth_and_cafe_isolation(self):
        api_client = APIClient()
        for url_name, kwargs in [
            ("api_invoice_subcategories", {}),
            ("api_invoice_suppliers", {}),
            ("api_product_purchase_history", {}),
            ("api_product_changes", {}),
        ]:
            url = reverse(url_name, kwargs={"cafe_code": self.cafe_a.code, **kwargs})
            self.assertIn(api_client.get(url).status_code, (401, 403))

        api_client.force_authenticate(user=self.kitchen_a)
        url_sub = reverse("api_invoice_subcategories", kwargs={"cafe_code": self.cafe_a.code})
        self.assertEqual(api_client.get(f"{url_sub}?category_id={self.cat_a.id}").status_code, 403)

        api_client.force_authenticate(user=self.manager_a)
        self.assertEqual(api_client.get(f"{url_sub}?category_id={self.cat_b.id}").status_code, 404)

    def test_get_date_range_exact_7_day_delta(self):
        cur_start, cur_end, _ps, _pe = get_date_range("weekly")
        self.assertEqual((cur_end - cur_start).days, 7)

    def test_finance_manager_apis_return_200_with_store_invoices_and_payments(self):
        store_inv = Invoice.objects.create(
            invoice_number="ST-1",
            supplier=None,
            store_name="فروشگاه نقدی امید",
            cafe=self.cafe_a,
            created_by=self.manager_a,
            total_amount=Decimal("150.00"),
            status="sent",
            payment_status="unpaid",
        )
        Payment.objects.create(
            invoice=store_inv,
            amount=Decimal("50.00"),
            payment_location=self.pay_loc_a,
            payment_status="completed",
            created_by=self.manager_a,
        )
        api_client = APIClient()
        api_client.force_authenticate(user=self.manager_a)
        for url_name in ("api_finance_stats", "api_finance_recent_payments", "api_finance_pending_invoices"):
            url = reverse(url_name, kwargs={"cafe_code": self.cafe_a.code})
            resp = api_client.get(url)
            self.assertEqual(resp.status_code, 200, f"{url_name} failed: {resp.content}")

    def test_mobile_voucher_create_and_finalize_succeeds(self):
        api_client = APIClient()
        token, _ = Token.objects.get_or_create(user=self.warehouse_a)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        url = reverse("mobile_api_warehouse_vouchers", kwargs={"cafe_code": self.cafe_a.code})
        resp = api_client.post(
            url,
            {
                "voucher_type": "receipt",
                "destination_warehouse_id": self.wh_a.id,
                "description": "Mobile receipt",
                "items": [{"product_id": self.prod_a.id, "quantity": "5"}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        voucher = WarehouseVoucher.objects.get(id=resp.json()["id"])
        self.assertEqual(voucher.status, "finalized")
        si = StockItem.objects.get(product=self.prod_a, warehouse=self.wh_a)
        self.assertEqual(si.quantity, Decimal("5"))

    def test_mobile_purchasing_scope_and_requester_edit_parity(self):
        api_client = APIClient()
        wh_token, _ = Token.objects.get_or_create(user=self.warehouse_a)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {wh_token.key}")
        purch_dash = reverse("mobile_api_purchasing_dashboard", kwargs={"cafe_code": self.cafe_a.code})
        self.assertEqual(api_client.get(purch_dash).status_code, 403)
        self.assertEqual(api_client.get(f"{purch_dash}?scope=warehouse_self").status_code, 200)

        pr = PurchaseRequest.objects.create(user=self.kitchen_a, cafe=self.cafe_a)
        PurchaseItem.objects.create(request=pr, product=self.prod_a, name=self.prod_a.name, quantity=Decimal("1"), unit="کیلوگرم")

        kit_token, _ = Token.objects.get_or_create(user=self.kitchen_a)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {kit_token.key}")
        edit_url = reverse(
            "mobile_api_requester_edit_request",
            kwargs={"cafe_code": self.cafe_a.code, "request_id": pr.id},
        )
        resp = api_client.patch(
            edit_url,
            {"items": [{"name": self.prod_food_unlinked.name, "quantity": "2", "unit": "کیلوگرم"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        # Pre-validation prevented deleting existing item
        self.assertEqual(pr.items.count(), 1)

    def test_mobile_me_rejects_cross_cafe_override(self):
        api_client = APIClient()
        kit_token, _ = Token.objects.get_or_create(user=self.kitchen_a)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {kit_token.key}")
        resp = api_client.get(f"{reverse('mobile_api_me')}?cafe_code={self.cafe_b.code}")
        self.assertEqual(resp.status_code, 403)

    # ── 5. Template Tags & Static Template Hardening ─────────────────────────

    def test_custom_filters_edge_cases(self):
        self.assertEqual(get_item(["not", "a", "dict"], 0), None)
        self.assertEqual(get_item({"a": 1}, "a"), 1)
        self.assertNotEqual(jalali_datetime(date(2025, 3, 21)), "")
        self.assertEqual(
            number_format("1234567890123456789"),
            "۱,۲۳۴,۵۶۷,۸۹۰,۱۲۳,۴۵۶,۷۸۹",
        )
        self.assertEqual(num2("100.00"), "۱۰۰")
        self.assertEqual(cafe_url({}, "select_cafe"), reverse("select_cafe"))
        self.assertEqual(cafe_url({}, "management_dashboard"), "#")
        aware_dt = timezone.now()
        self.assertEqual(iso_date(aware_dt), timezone.localtime(aware_dt).strftime("%Y-%m-%d"))

    def test_templates_have_no_raw_json_safe_or_persian_datepicker(self):
        tpl_root = Path(settings.BASE_DIR) / "frontend" / "templates"
        for rel in [
            "executive_summary.html",
            "financial_analytics.html",
            "inventory_analytics.html",
            "purchase_analytics.html",
            "purchases_dashboard.html",
            "supplier_analytics.html",
            "system_monitoring.html",
            "warehouse_available_products.html",
        ]:
            text = (tpl_root / rel).read_text(encoding="utf-8")
            self.assertNotIn("|safe", text, f"{rel} still uses |safe")
            self.assertIn("|json_script:", text, f"{rel} should use |json_script")

        for html_file in tpl_root.rglob("*.html"):
            content = html_file.read_text(encoding="utf-8")
            self.assertNotIn("persian-datepicker", content, f"{html_file} still references persian-datepicker")
            self.assertNotIn("new persianDatepicker", content, f"{html_file} still calls persianDatepicker")

        for rel in ["purchase_request_form.html", "warehouse_request_form.html", "price_dashboard.html"]:
            text = (tpl_root / rel).read_text(encoding="utf-8")
            self.assertIn("&#39;", text, f"{rel} missing single-quote escaping in escapeHtml")
