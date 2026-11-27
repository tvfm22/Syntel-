from decimal import Decimal
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from purchases.models import (
    ActivityLog,
    Cafe,
    Category,
    Department,
    OfficerAssignment,
    Product,
    PurchaseDraft,
    PurchaseDraftItem,
    PurchaseItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequest,
    StockItem,
    StockTransaction,
    Supplier,
    Unit,
    Warehouse,
    WarehouseVoucher,
    WarehouseVoucherItem,
)
from purchases.services.inventory import (
    next_voucher_number,
    reopen_officer_assignments,
    save_voucher_with_number,
)
from purchases.services.purchase_approval import evaluate_draft_items_ceo_status
from purchases.services.recipe_gate import RD_CATEGORY_NAME
from purchases.views.workflow_helpers import (
    _dispatch_warehouse_purchase_items,
    _item_sent_to_ceo,
    _warehouse_decided_request_ids,
)

User = get_user_model()


class Wave1Agent3Tests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name="کافه تست", code="testcafe", is_active=True)
        self.dept_kitchen = Department.objects.create(
            name="آشپزخانه", cafe=self.cafe, is_active=True, is_recipe_department=True
        )
        self.dept_bar = Department.objects.create(
            name="بار", cafe=self.cafe, is_active=True, is_recipe_department=True
        )
        self.unit_kg = Unit.objects.create(name="کیلوگرم", symbol="kg", cafe=self.cafe)
        self.cat_general = Category.objects.create(name="خشکبار", cafe=self.cafe)
        self.cat_rd = Category.objects.create(name=RD_CATEGORY_NAME, cafe=self.cafe)

        self.wh1 = Warehouse.objects.create(cafe=self.cafe, code="WH1", name="انبار ۱", is_active=True)
        self.wh2 = Warehouse.objects.create(cafe=self.cafe, code="WH2", name="انبار ۲", is_active=True)

        self.product = Product.objects.create(
            name="شکر", unit=self.unit_kg, category=self.cat_general,
            cafe=self.cafe, is_active=True, is_complete=True, usage_location="general",
        )
        self.product_incomplete = Product.objects.create(
            name="کالای ناقص", unit=self.unit_kg, category=self.cat_general,
            cafe=self.cafe, is_active=True, is_complete=False, usage_location="general",
        )
        self.product_rd = Product.objects.create(
            name="کالای تحقیق", unit=self.unit_kg, category=self.cat_rd,
            cafe=self.cafe, is_active=True, is_complete=True, usage_location="general",
        )

        self.stock1 = StockItem.objects.create(
            product=self.product, warehouse=self.wh1, quantity=Decimal("10"), unit=self.unit_kg
        )
        self.stock2 = StockItem.objects.create(
            product=self.product, warehouse=self.wh2, quantity=Decimal("10"), unit=self.unit_kg
        )

        self.manager = User.objects.create_user(
            username="mgr", password="pw", role="manager", cafe=self.cafe
        )
        self.warehouse_user = User.objects.create_user(
            username="wh_user", password="pw", role="warehouse", cafe=self.cafe
        )
        self.purchasing_user = User.objects.create_user(
            username="pur_user", password="pw", role="purchasing", cafe=self.cafe
        )
        self.requester_kitchen = User.objects.create_user(
            username="req_k", password="pw", role="requester",
            department=self.dept_kitchen, cafe=self.cafe,
        )
        self.requester_bar = User.objects.create_user(
            username="req_b", password="pw", role="requester",
            department=self.dept_bar, cafe=self.cafe,
        )
        self.sec_manager = User.objects.create_user(
            username="sec_mgr", password="pw", role="warehouse",
            secondary_role="manager", cafe=self.cafe,
        )

    def test_voucher_edit_submit_finalizes_draft_and_secondary_role_manager(self):
        v = WarehouseVoucher.objects.create(
            cafe=self.cafe,
            voucher_type="issue",
            status="draft",
            voucher_number=next_voucher_number(self.cafe.id, "issue"),
            source_warehouse=self.wh1,
            created_by=self.warehouse_user,
        )
        WarehouseVoucherItem.objects.create(
            voucher=v, product=self.product, stock_item=self.stock1,
            unit=self.unit_kg, base_quantity=Decimal("3"),
        )
        client = Client()
        client.force_login(self.sec_manager)
        url = reverse("voucher_edit", kwargs={"cafe_code": self.cafe.code, "voucher_id": v.id})
        resp = client.post(url, {"action": "submit", "quick_submit": "1"})
        self.assertEqual(resp.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.status, "finalized")
        self.stock1.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal("7.0000"))

    def test_warehouse_inventory_submit_requires_manager_approval(self):
        client = Client()
        client.force_login(self.warehouse_user)
        url = reverse(
            "warehouse_inventory_submit",
            kwargs={"cafe_code": self.cafe.code, "warehouse_id": self.wh1.id},
        )
        resp = client.post(
            url,
            data=json.dumps({"items": [{"stock_item_id": self.stock1.id, "physical_qty": "6"}]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["pending_approval"])
        v = WarehouseVoucher.objects.get(id=data["voucher_id"])
        self.assertEqual(v.status, "submitted")
        self.stock1.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal("10.0000"))

        # Manager approves it
        client.force_login(self.manager)
        approve_url = reverse("voucher_approve", kwargs={"cafe_code": self.cafe.code, "voucher_id": v.id})
        client.post(approve_url)
        v.refresh_from_db()
        self.assertEqual(v.status, "finalized")
        self.stock1.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal("6.0000"))

    def test_save_voucher_with_number_retries_on_integrity_error(self):
        existing = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type="receipt", status="draft",
            voucher_number="RCV-00001", destination_warehouse=self.wh1,
            created_by=self.warehouse_user,
        )
        v2 = WarehouseVoucher(
            cafe=self.cafe, voucher_type="receipt", status="draft",
            voucher_number="RCV-00001", destination_warehouse=self.wh1,
            created_by=self.warehouse_user,
        )
        with patch(
            "purchases.services.inventory.next_voucher_number",
            side_effect=["RCV-00002"],
        ):
            save_voucher_with_number(v2, self.cafe.id, "receipt")
        self.assertEqual(v2.voucher_number, "RCV-00002")
        self.assertNotEqual(v2.id, existing.id)

    def test_multi_warehouse_fulfill_and_reopen_restores_all_stock(self):
        pr = PurchaseRequest.objects.create(user=self.requester_kitchen, cafe=self.cafe, status="pending")
        pi = PurchaseItem.objects.create(
            request=pr, product=self.product, name=self.product.name,
            quantity=Decimal("6"), unit="کیلوگرم",
        )
        shared_assign = OfficerAssignment.objects.create(
            item=pi, target_role="warehouse", assigned_to=None, status="pending"
        )
        client = Client()
        client.force_login(self.warehouse_user)
        url = reverse("warehouse_officer_dashboard", kwargs={"cafe_code": self.cafe.code})
        resp = client.post(url, {
            "action": "fulfill",
            "item_id": str(pi.id),
            "stock_item_id": [str(self.stock1.id)],
            "qty": ["4"],
            "stock_item_id_1": str(self.stock2.id),
            "qty_1": "2",
        })
        self.assertEqual(resp.status_code, 302)
        shared_assign.refresh_from_db()
        self.assertEqual(shared_assign.assigned_to_id, self.warehouse_user.id)
        self.assertEqual(shared_assign.status, "fulfilled")
        pi.refresh_from_db()
        self.assertEqual(pi.supply_status, "from_stock")
        self.assertEqual(pi.purchased_quantity, Decimal("6.0000"))

        self.stock1.refresh_from_db()
        self.stock2.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal("6.0000"))
        self.assertEqual(self.stock2.quantity, Decimal("8.0000"))

        # Reopen restores BOTH warehouses
        reopened_cnt = reopen_officer_assignments(
            pi, cafe=self.cafe, reopened_by=self.warehouse_user, reason="اصلاح"
        )
        self.assertEqual(reopened_cnt, 2)
        self.stock1.refresh_from_db()
        self.stock2.refresh_from_db()
        self.assertEqual(self.stock1.quantity, Decimal("10.0000"))
        self.assertEqual(self.stock2.quantity, Decimal("10.0000"))

    def test_purchase_orders_status_blocks_fulfilled_delete_and_status_change(self):
        pr = PurchaseRequest.objects.create(user=self.requester_kitchen, cafe=self.cafe, status="purchased")
        pi = PurchaseItem.objects.create(
            request=pr, product=self.product, name=self.product.name,
            quantity=Decimal("2"), unit="کیلوگرم",
        )
        assign = OfficerAssignment.objects.create(
            item=pi, target_role="warehouse", assigned_to=self.warehouse_user, status="fulfilled"
        )
        client = Client()
        client.force_login(self.manager)
        url = reverse("purchase_orders_status", kwargs={"cafe_code": self.cafe.code})
        client.post(url, {"assignment_id": str(assign.id), "action": "delete"})
        self.assertTrue(OfficerAssignment.objects.filter(id=assign.id).exists())
        client.post(url, {"assignment_id": str(assign.id), "action": "status", "status": "pending"})
        assign.refresh_from_db()
        self.assertEqual(assign.status, "fulfilled")

    def test_dispatch_general_purchasing_creates_shared_assignment_and_return_unlocks(self):
        src_pr = PurchaseRequest.objects.create(user=self.requester_kitchen, cafe=self.cafe, status="pending")
        src_pi = PurchaseItem.objects.create(
            request=src_pr, product=self.product, name=self.product.name,
            quantity=Decimal("2"), unit="کیلوگرم",
        )
        OfficerAssignment.objects.create(
            item=src_pi, target_role="warehouse", assigned_to=self.warehouse_user, status="returned"
        )
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.warehouse_user)
        d_item = PurchaseDraftItem.objects.create(
            draft=draft, source_item=src_pi, product=self.product,
            name=self.product.name, quantity=Decimal("2"), unit="کیلوگرم",
        )
        _ceo_req, direct_req, _ceo_entries, _direct_entries = _dispatch_warehouse_purchase_items(
            cafe=self.cafe,
            request_user=self.warehouse_user,
            entries=[d_item],
            target_officer=None,
            is_self_buy=False,
        )
        self.assertIsNotNone(direct_req)
        copy_item = direct_req.items.first()
        p_assign = OfficerAssignment.objects.get(item=copy_item, target_role="purchasing")
        self.assertIsNone(p_assign.assigned_to)
        self.assertEqual(p_assign.status, "pending")
        self.assertIsNotNone(_item_sent_to_ceo(src_pi))

        # Purchasing returns the item -> unlocks src_pi and closes direct_req
        client = Client()
        client.force_login(self.purchasing_user)
        url = reverse("purchase_officer_dashboard", kwargs={"cafe_code": self.cafe.code})
        client.post(url, {"action": "return", "item_id": str(p_assign.id)})
        direct_req.refresh_from_db()
        self.assertEqual(direct_req.status, "rejected")
        self.assertIsNone(_item_sent_to_ceo(src_pi))

    def test_purchase_order_create_uses_draft_and_creates_order_items(self):
        supplier = Supplier.objects.create(name="تامین", cafe=self.cafe)
        pr = PurchaseRequest.objects.create(
            user=self.warehouse_user, cafe=self.cafe, status="pending", source="warehouse_purchase"
        )
        pi = PurchaseItem.objects.create(
            request=pr, product=self.product, name=self.product.name,
            quantity=Decimal("5"), unit="کیلوگرم",
        )
        client = Client()
        client.force_login(self.purchasing_user)
        url = reverse("purchase_order_create", kwargs={"cafe_code": self.cafe.code, "request_id": pr.id})
        resp = client.post(url, {"supplier": str(supplier.id)})
        self.assertEqual(resp.status_code, 302)
        order = PurchaseOrder.objects.get(request=pr)
        self.assertEqual(order.status, "draft")
        self.assertEqual(PurchaseOrderItem.objects.filter(order=order, request_item=pi).count(), 1)

    def test_approvals_activity_log_uses_updated_and_creates_shared_assignment(self):
        pr = PurchaseRequest.objects.create(
            user=self.warehouse_user, cafe=self.cafe,
            status="pending_manager_approval", source="warehouse_purchase",
        )
        pi = PurchaseItem.objects.create(
            request=pr, product=self.product, name=self.product.name,
            quantity=Decimal("5"), unit="کیلوگرم",
        )
        client = Client()
        client.force_login(self.manager)
        url = reverse("manager_approve_request", kwargs={"cafe_code": self.cafe.code, "request_id": pr.id})
        client.post(url)
        self.assertTrue(
            OfficerAssignment.objects.filter(
                item=pi, target_role="purchasing", assigned_to__isnull=True, status="pending"
            ).exists()
        )
        log = ActivityLog.objects.get(request=pr)
        self.assertEqual(log.action, "updated")

    def test_dashboard_isolates_requester_requests(self):
        PurchaseRequest.objects.create(user=self.requester_kitchen, cafe=self.cafe, status="pending")
        PurchaseRequest.objects.create(user=self.requester_bar, cafe=self.cafe, status="pending")
        client = Client()
        client.force_login(self.requester_kitchen)
        url = reverse("dashboard", kwargs={"cafe_code": self.cafe.code})
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        reqs = list(resp.context["requests"])
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].user_id, self.requester_kitchen.id)

    def test_purchase_approval_cumulative_3x_flags_all_rows_of_product(self):
        # Historical average = 2 kg
        hist_pr = PurchaseRequest.objects.create(
            user=self.warehouse_user, cafe=self.cafe, status="purchased", source="warehouse_purchase"
        )
        PurchaseItem.objects.create(
            request=hist_pr, product=self.product, name=self.product.name,
            quantity=Decimal("2"), unit="کیلوگرم", supply_status="purchased", purchased_quantity=Decimal("2"),
        )
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.warehouse_user)
        d1 = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name=self.product.name,
            quantity=Decimal("4"), unit="کیلوگرم",
        )
        d2 = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name=self.product.name,
            quantity=Decimal("4"), unit="کیلوگرم",
        )
        # Total = 8 > 3 * 2 = 6 -> BOTH d1 and d2 must need CEO approval
        res = evaluate_draft_items_ceo_status([d1, d2], cafe=self.cafe)
        self.assertTrue(res[d1.id]["needs_ceo"])
        self.assertTrue(res[d2.id]["needs_ceo"])

    def test_mobile_api_draft_delete_reopens_and_products_filter(self):
        api = APIClient()
        api.force_authenticate(user=self.requester_kitchen)
        prod_url = f"/{self.cafe.code}/api/mobile/requester/products/"
        resp = api.get(prod_url)
        self.assertEqual(resp.status_code, 200)
        names = {p["name"] for p in resp.data["products"]}
        self.assertIn("شکر", names)
        self.assertNotIn("کالای ناقص", names)
        self.assertNotIn("کالای تحقیق", names)
        sugar_entry = next(p for p in resp.data["products"] if p["name"] == "شکر")
        self.assertEqual(Decimal(str(sugar_entry["current_stock"])), Decimal("20"))

        # Reopened assignment is not treated as decided
        pr = PurchaseRequest.objects.create(user=self.requester_kitchen, cafe=self.cafe, status="pending")
        pi = PurchaseItem.objects.create(
            request=pr, product=self.product, name=self.product.name,
            quantity=Decimal("2"), unit="کیلوگرم",
        )
        OfficerAssignment.objects.create(
            item=pi, target_role="warehouse", assigned_to=self.warehouse_user, status="returned"
        )
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.warehouse_user)
        d_item = PurchaseDraftItem.objects.create(
            draft=draft, source_item=pi, product=self.product,
            name=self.product.name, quantity=Decimal("2"), unit="کیلوگرم",
        )
        api.force_authenticate(user=self.warehouse_user)
        del_url = f"/{self.cafe.code}/api/mobile/warehouse/draft/items/{d_item.id}/"
        del_resp = api.delete(del_url)
        self.assertEqual(del_resp.status_code, 200)
        self.assertNotIn(pr.id, _warehouse_decided_request_ids([pr.id]))
