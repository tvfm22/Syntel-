"""Tests for the Assets & Furnishings (اموال و اثاثیه) models, services, permissions, and media isolation."""
from decimal import Decimal
import io
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from PIL import Image

from purchases.models import (
    AssetAudit,
    AssetAuditItem,
    AssetCatalog,
    AssetCategory,
    AssetMovement,
    AssetUnit,
    Cafe,
    Department,
    DepartmentAssetStock,
    Invoice,
    PurchaseItem,
    PurchaseRequest,
)
from purchases.services.assets import (
    AssetPermissionError,
    AssetStateError,
    AssetValidationError,
    InsufficientAssetStockError,
    add_unexpected_audit_item,
    allocate_initial_or_addition,
    approve_audit_and_apply_adjustments,
    approve_movement,
    compress_and_thumbnail_image,
    compress_evidence_image,
    confirm_transfer_receipt,
    create_audit_session,
    create_replacement_purchase_request,
    create_serialized_unit,
    initiate_transfer,
    reject_movement,
    reject_transfer,
    report_damage_or_disposal,
    request_audit_recount,
    save_audit_item_count,
    submit_audit,
    update_unit_repair_status,
)
from purchases.views.helpers import (
    CAFE_LOOKUP_PATHS,
    filter_by_cafe,
    user_can_access_department_assets,
    user_can_manage_assets,
)

User = get_user_model()


def _make_jpeg_bytes(width=1600, height=800, orientation=None) -> bytes:
    img = Image.new("RGB", (width, height), color=(20, 120, 200))
    exif = img.getexif()
    if orientation is not None:
        exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class AssetsModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe_a = Cafe.objects.create(name="Cafe A", code="asset_ca", is_active=True)
        cls.cafe_b = Cafe.objects.create(name="Cafe B", code="asset_cb", is_active=True)

        cls.dept_bar = Department.objects.create(name="بار", cafe=cls.cafe_a, is_active=True)
        cls.dept_kitchen = Department.objects.create(name="آشپزخانه", cafe=cls.cafe_a, is_active=True)
        cls.dept_b = Department.objects.create(name="سالن ب", cafe=cls.cafe_b, is_active=True)

        cls.manager_a = User.objects.create_user(
            username="asset_mgr_a",
            password="StrongPass123!",
            role="manager",
            cafe=cls.cafe_a,
        )
        cls.warehouse_a = User.objects.create_user(
            username="asset_wh_a",
            password="StrongPass123!",
            role="warehouse",
            cafe=cls.cafe_a,
        )
        cls.req_bar = User.objects.create_user(
            username="asset_req_bar",
            password="StrongPass123!",
            role="requester",
            cafe=cls.cafe_a,
            department=cls.dept_bar,
        )
        cls.req_kitchen = User.objects.create_user(
            username="asset_req_kitchen",
            password="StrongPass123!",
            role="requester",
            cafe=cls.cafe_a,
            department=cls.dept_kitchen,
        )
        cls.user_b = User.objects.create_user(
            username="asset_user_b",
            password="StrongPass123!",
            role="manager",
            cafe=cls.cafe_b,
        )
        cls.req_b = User.objects.create_user(
            username="asset_req_b",
            password="StrongPass123!",
            role="requester",
            cafe=cls.cafe_b,
            department=cls.dept_b,
        )
        cls.superuser = User.objects.create_superuser(
            username="asset_super",
            email="assetsuper@example.com",
            password="StrongPass123!",
        )

        cls.cat_dishes = AssetCategory.objects.create(cafe=cls.cafe_a, name="ظروف")
        cls.cat_equip = AssetCategory.objects.create(cafe=cls.cafe_a, name="تجهیزات برقی")

        cls.cup = AssetCatalog.objects.create(
            cafe=cls.cafe_a,
            category=cls.cat_dishes,
            name="فنجان اسپرسو",
            code="CUP-01",
            tracking_mode="quantity",
            estimated_unit_value=Decimal("250000"),
        )
        cls.espresso_machine = AssetCatalog.objects.create(
            cafe=cls.cafe_a,
            category=cls.cat_equip,
            name="دستگاه اسپرسوساز",
            code="EQ-01",
            brand_model="La Marzocco",
            tracking_mode="serialized",
            estimated_unit_value=Decimal("500000000"),
        )
        cls.asset_b = AssetCatalog.objects.create(
            cafe=cls.cafe_b,
            name="صندلی کافه ب",
            code="CHR-B1",
            tracking_mode="quantity",
            estimated_unit_value=Decimal("1500000"),
        )

    def _login(self, user, cafe=None):
        client = Client()
        self.assertTrue(client.login(username=user.username, password="StrongPass123!"))
        if cafe is not None:
            session = client.session
            session["selected_cafe_id"] = cafe.id
            session["selected_cafe_code"] = cafe.code
            session.save()
        return client

    def test_category_cycle_validation_and_helpers_permissions(self):
        child = AssetCategory.objects.create(cafe=self.cafe_a, name="زیرمجموعه ظروف", parent=self.cat_dishes)
        self.cat_dishes.parent = child
        with self.assertRaises(ValidationError):
            self.cat_dishes.clean()
        self.cat_dishes.parent = None

        # Cross-cafe stock validation
        bad_stock = DepartmentAssetStock(cafe=self.cafe_a, department=self.dept_b, asset=self.cup)
        with self.assertRaises(ValidationError):
            bad_stock.clean()

        # Permission helpers
        self.assertTrue(user_can_manage_assets(self.manager_a))
        self.assertTrue(user_can_manage_assets(self.warehouse_a))
        self.assertTrue(user_can_manage_assets(self.superuser))
        self.assertFalse(user_can_manage_assets(self.req_bar))

        self.assertTrue(user_can_access_department_assets(self.req_bar, self.dept_bar))
        self.assertFalse(user_can_access_department_assets(self.req_bar, self.dept_kitchen))
        self.assertFalse(user_can_access_department_assets(self.manager_a, self.dept_b))
        self.assertTrue(user_can_access_department_assets(self.manager_a, self.dept_kitchen))

        # CAFE_LOOKUP_PATHS registration
        for model_cls in (
            AssetCategory,
            AssetCatalog,
            DepartmentAssetStock,
            AssetUnit,
            AssetMovement,
            AssetAudit,
            AssetAuditItem,
        ):
            self.assertIn(model_cls.__name__, CAFE_LOOKUP_PATHS)

        rf = RequestFactory()
        req = rf.get("/")
        req.cafe = self.cafe_a
        qs = filter_by_cafe(AssetCatalog.objects.all(), req)
        self.assertIn(self.cup, qs)
        self.assertNotIn(self.asset_b, qs)

    def test_image_compression_exif_orientation_and_thumbnail(self):
        # Orientation=6 rotates 1600x800 -> 800x1600 before thumbnailing to max 1200x1200
        raw = _make_jpeg_bytes(width=1600, height=800, orientation=6)
        upload = SimpleUploadedFile("espresso.jpg", raw, content_type="image/jpeg")

        main_cf, thumb_cf = compress_and_thumbnail_image(upload, max_size=(1200, 1200), thumb_size=(240, 240))
        with Image.open(io.BytesIO(main_cf.read())) as main_img:
            self.assertLessEqual(max(main_img.size), 1200)
            self.assertGreater(main_img.height, main_img.width)
        with Image.open(io.BytesIO(thumb_cf.read())) as thumb_img:
            self.assertLessEqual(max(thumb_img.size), 240)
            self.assertGreater(thumb_img.height, thumb_img.width)

        evidence_cf = compress_evidence_image(
            SimpleUploadedFile("damage.jpg", raw, content_type="image/jpeg"),
            max_size=(800, 800),
        )
        with Image.open(io.BytesIO(evidence_cf.read())) as ev_img:
            self.assertLessEqual(max(ev_img.size), 800)

    def test_initial_allocation_and_serialized_unit_sync(self):
        mv = allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=20,
            user=self.manager_a,
            movement_type="initial",
            min_expected_quantity=25,
            location_note="قفسه بالا",
            reason="شروع سیستم",
        )
        self.assertEqual(mv.status, "approved")
        self.assertEqual(mv.unit_value_snapshot, Decimal("250000"))

        stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup)
        self.assertEqual(stock.quantity_healthy, 20)
        self.assertEqual(stock.total_quantity, 20)
        self.assertEqual(stock.shortage_quantity, 5)

        # Serialized unit creation and repair status sync
        unit = create_serialized_unit(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.espresso_machine,
            asset_tag="TAG-ESP-01",
            user=self.manager_a,
            serial_number="SN-991",
            purchase_price=Decimal("450000000"),
            custodian=self.req_bar,
        )
        eq_stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.espresso_machine)
        self.assertEqual(eq_stock.quantity_healthy, 1)
        self.assertEqual(eq_stock.quantity_damaged, 0)

        mv_repair = update_unit_repair_status(unit, "in_repair", self.warehouse_a, reason="خرابی پمپ")
        self.assertEqual(mv_repair.movement_type, "repair_out")
        eq_stock.refresh_from_db()
        self.assertEqual(eq_stock.quantity_healthy, 0)
        self.assertEqual(eq_stock.quantity_damaged, 1)

        mv_back = update_unit_repair_status(unit, "active", self.manager_a, reason="تعمیر شد")
        self.assertEqual(mv_back.movement_type, "repair_in")
        eq_stock.refresh_from_db()
        self.assertEqual(eq_stock.quantity_healthy, 1)
        self.assertEqual(eq_stock.quantity_damaged, 0)

    def test_two_way_transfer_confirm_reject_and_no_negative_stock(self):
        allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=10,
            user=self.manager_a,
        )

        # Initiate handshake transfer of 6 from bar -> kitchen
        mv1 = initiate_transfer(
            cafe=self.cafe_a,
            asset=self.cup,
            from_department=self.dept_bar,
            to_department=self.dept_kitchen,
            quantity=6,
            user=self.req_bar,
            reason="کمبود لیوان در آشپزخانه",
            require_handshake=True,
        )
        self.assertEqual(mv1.status, "pending_receipt")

        # Stock is NOT deducted yet before destination confirms
        bar_stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup)
        self.assertEqual(bar_stock.quantity_healthy, 10)
        self.assertFalse(
            DepartmentAssetStock.objects.filter(department=self.dept_kitchen, asset=self.cup).exists()
        )

        # Cannot initiate another transfer of 5 (10 - 6 pending = 4 available < 5)
        with self.assertRaises(InsufficientAssetStockError):
            initiate_transfer(
                cafe=self.cafe_a,
                asset=self.cup,
                from_department=self.dept_bar,
                to_department=self.dept_kitchen,
                quantity=5,
                user=self.req_bar,
                require_handshake=True,
            )

        # Destination rejects mv1 -> stock stays 10 and reservation is freed
        rejected = reject_transfer(mv1, self.req_kitchen, reason="نیاز برطرف شد")
        self.assertEqual(rejected.status, "rejected")
        bar_stock.refresh_from_db()
        self.assertEqual(bar_stock.quantity_healthy, 10)

        # Now transfer 7 and confirm receipt by kitchen requester
        mv2 = initiate_transfer(
            cafe=self.cafe_a,
            asset=self.cup,
            from_department=self.dept_bar,
            to_department=self.dept_kitchen,
            quantity=7,
            user=self.req_bar,
            require_handshake=True,
        )
        # Source user cannot confirm receipt for destination
        with self.assertRaises(AssetPermissionError):
            confirm_transfer_receipt(mv2, self.req_bar)

        confirmed = confirm_transfer_receipt(mv2, self.req_kitchen)
        self.assertEqual(confirmed.status, "approved")
        self.assertEqual(confirmed.received_by, self.req_kitchen)

        bar_stock.refresh_from_db()
        kitchen_stock = DepartmentAssetStock.objects.get(department=self.dept_kitchen, asset=self.cup)
        self.assertEqual(bar_stock.quantity_healthy, 3)
        self.assertEqual(kitchen_stock.quantity_healthy, 7)

    def test_requester_cannot_directly_mutate_stock_or_approve(self):
        allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=12,
            user=self.manager_a,
        )
        unit = create_serialized_unit(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.espresso_machine,
            asset_tag="TAG-REQ-1",
            user=self.manager_a,
        )

        # Requester cannot allocate, create units, direct-transfer, or update repair status
        with self.assertRaises(AssetPermissionError):
            allocate_initial_or_addition(self.cafe_a, self.dept_bar, self.cup, 5, self.req_bar)
        with self.assertRaises(AssetPermissionError):
            create_serialized_unit(self.cafe_a, self.dept_bar, self.espresso_machine, "TAG-X", self.req_bar)
        with self.assertRaises(AssetPermissionError):
            initiate_transfer(
                self.cafe_a, self.cup, self.dept_bar, self.dept_kitchen, 2, self.req_bar, require_handshake=False
            )
        with self.assertRaises(AssetPermissionError):
            update_unit_repair_status(unit, "disposed", self.req_bar)

        # Requester reporting damage ALWAYS stays pending even if auto_approve=True
        mv_pending = report_damage_or_disposal(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=2,
            user=self.req_bar,
            reason="شکستگی هنگام شستشو",
            auto_approve=True,
        )
        self.assertEqual(mv_pending.status, "pending")
        bar_stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup)
        self.assertEqual(bar_stock.quantity_healthy, 12)

        # Requester cannot approve or reject movements
        with self.assertRaises(AssetPermissionError):
            approve_movement(mv_pending, self.req_bar)
        with self.assertRaises(AssetPermissionError):
            reject_movement(mv_pending, self.req_bar, reason="nope")

        # Manager approves -> stock decreases to 10
        approved = approve_movement(mv_pending, self.manager_a)
        self.assertEqual(approved.status, "approved")
        bar_stock.refresh_from_db()
        self.assertEqual(bar_stock.quantity_healthy, 10)

        # Manager auto-approve works directly
        mv_direct = report_damage_or_disposal(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=1,
            user=self.manager_a,
            reason="اسقاط مستقیم",
            auto_approve=True,
        )
        self.assertEqual(mv_direct.status, "approved")
        bar_stock.refresh_from_db()
        self.assertEqual(bar_stock.quantity_healthy, 9)

    def test_audit_blind_count_recount_unexpected_and_adjustments(self):
        allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=10,
            user=self.manager_a,
        )
        unit = create_serialized_unit(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.espresso_machine,
            asset_tag="TAG-AUD-1",
            user=self.manager_a,
        )
        extra_chair = AssetCatalog.objects.create(
            cafe=self.cafe_a,
            name="چهارپایه بار",
            code="STL-01",
            tracking_mode="quantity",
            estimated_unit_value=Decimal("1000000"),
        )

        audit = create_audit_session(
            cafe=self.cafe_a,
            department=self.dept_bar,
            title="انبارگردانی پایان ماه",
            user=self.manager_a,
            audit_type="full",
            blind_count=True,
        )
        self.assertTrue(audit.blind_count)
        self.assertEqual(audit.status, "in_progress")
        self.assertEqual(audit.items.count(), 2)

        cup_item = audit.items.get(asset=self.cup)
        unit_item = audit.items.get(asset_unit=unit)

        save_audit_item_count(cup_item, self.req_bar, counted_healthy=7, counted_damaged=1, note="۲ عدد مفقود، ۱ لب‌پر")
        save_audit_item_count(unit_item, self.req_bar, counted_status="needs_repair", note="نشتی بخار")
        unexpected = add_unexpected_audit_item(
            audit, extra_chair, counted_healthy=3, user=self.req_bar, note="یافت‌شده کنار کانتر"
        )
        self.assertTrue(unexpected.is_unexpected_item)

        submit_audit(audit, self.req_bar)
        self.assertEqual(audit.status, "submitted")

        # Cannot edit counts while submitted
        with self.assertRaises(AssetStateError):
            save_audit_item_count(cup_item, self.req_bar, counted_healthy=8, counted_damaged=0)

        # Requester cannot request recount or approve
        with self.assertRaises(AssetPermissionError):
            request_audit_recount(audit, self.req_bar, item_ids=[cup_item.id])
        with self.assertRaises(AssetPermissionError):
            approve_audit_and_apply_adjustments(audit, self.req_bar)

        # Manager requests recount on cup_item only
        request_audit_recount(audit, self.manager_a, item_ids=[cup_item.id], review_notes="لطفا فنجان‌ها بازشماری شوند")
        self.assertEqual(audit.status, "recount_requested")
        cup_item.refresh_from_db()
        unit_item.refresh_from_db()
        self.assertTrue(cup_item.needs_recount)
        self.assertFalse(unit_item.needs_recount)

        # Recount cup_item: 8 healthy, 1 damaged (variance_healthy = -2, variance_damaged = +1)
        save_audit_item_count(cup_item, self.req_bar, counted_healthy=8, counted_damaged=1)
        cup_item.refresh_from_db()
        self.assertFalse(cup_item.needs_recount)
        self.assertEqual(cup_item.variance_healthy, -2)
        self.assertEqual(cup_item.variance_damaged, 1)
        self.assertEqual(cup_item.variance_total, -1)
        self.assertEqual(cup_item.variance_value, Decimal("-250000"))

        submit_audit(audit, self.req_bar)
        adjustments = approve_audit_and_apply_adjustments(audit, self.manager_a, review_notes="تأیید نهایی")
        self.assertEqual(audit.status, "approved")
        self.assertTrue(all(mv.movement_type == "audit_adjustment" and mv.audit_id == audit.id for mv in adjustments))
        # cup healthy (-2), cup damaged (+1), unit (needs_repair), unexpected chair (+3) = 4 adjustments
        self.assertEqual(len(adjustments), 4)

        cup_stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup)
        self.assertEqual(cup_stock.quantity_healthy, 8)
        self.assertEqual(cup_stock.quantity_damaged, 1)

        unit.refresh_from_db()
        self.assertEqual(unit.status, "needs_repair")

        chair_stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=extra_chair)
        self.assertEqual(chair_stock.quantity_healthy, 3)

    def test_create_replacement_purchase_request_from_shortage(self):
        allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=4,
            user=self.manager_a,
            min_expected_quantity=10,
        )
        stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup)
        self.assertEqual(stock.shortage_quantity, 6)

        pr = create_replacement_purchase_request(
            cafe=self.cafe_a,
            department=self.dept_bar,
            user=self.req_bar,
            shortage_items=[{"stock": stock, "note": "جبران کسری بار"}],
        )
        self.assertIsInstance(pr, PurchaseRequest)
        self.assertEqual(pr.cafe, self.cafe_a)
        items = list(PurchaseItem.objects.filter(request=pr))
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].product)
        self.assertEqual(items[0].name, self.cup.name)
        self.assertEqual(items[0].quantity, Decimal("6.0000"))
        self.assertIn("CUP-01", items[0].description)

    def test_protected_media_assets_tenant_isolation(self):
        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=tmp_media):
                for sub in ("catalog", "catalog/thumbs", "units", "evidence", "audit_evidence"):
                    (Path(tmp_media) / "assets" / sub).mkdir(parents=True, exist_ok=True)

                img_bytes = _make_jpeg_bytes(400, 300)
                self.asset_b.image = SimpleUploadedFile("b_chair.jpg", img_bytes, content_type="image/jpeg")
                self.asset_b.thumbnail = SimpleUploadedFile("b_chair_thumb.jpg", img_bytes, content_type="image/jpeg")
                self.asset_b.save()

                unit_b = AssetUnit.objects.create(
                    cafe=self.cafe_b,
                    asset=AssetCatalog.objects.create(
                        cafe=self.cafe_b,
                        name="تجهیز ب",
                        code="EQ-B1",
                        tracking_mode="serialized",
                    ),
                    department=self.dept_b,
                    asset_tag="TAG-B1",
                    photo=SimpleUploadedFile("b_unit.jpg", img_bytes, content_type="image/jpeg"),
                )
                mv_b = AssetMovement.objects.create(
                    cafe=self.cafe_b,
                    movement_type="disposal",
                    status="pending",
                    asset=self.asset_b,
                    from_department=self.dept_b,
                    quantity=1,
                    evidence_photo=SimpleUploadedFile("b_ev.jpg", img_bytes, content_type="image/jpeg"),
                )
                audit_b = AssetAudit.objects.create(
                    cafe=self.cafe_b,
                    department=self.dept_b,
                    title="Audit B",
                )
                audit_item_b = AssetAuditItem.objects.create(
                    audit=audit_b,
                    asset=self.asset_b,
                    evidence_photo=SimpleUploadedFile("b_aud.jpg", img_bytes, content_type="image/jpeg"),
                )

                self.cup.image = SimpleUploadedFile("a_cup.jpg", img_bytes, content_type="image/jpeg")
                self.cup.save()

                client_a = self._login(self.req_bar, self.cafe_a)
                client_b = self._login(self.user_b, self.cafe_b)
                client_super = self._login(self.superuser, self.cafe_a)

                # Cafe A user is blocked (403) from all Cafe B asset media paths
                for path_str in (
                    self.asset_b.image.name,
                    self.asset_b.thumbnail.name,
                    unit_b.photo.name,
                    mv_b.evidence_photo.name,
                    audit_item_b.evidence_photo.name,
                ):
                    self.assertEqual(client_a.get(f"/media/{path_str}").status_code, 403)

                    r_b = client_b.get(f"/media/{path_str}")
                    self.assertEqual(r_b.status_code, 200)
                    r_b.close()

                    r_s = client_super.get(f"/media/{path_str}")
                    self.assertEqual(r_s.status_code, 200)
                    r_s.close()

                # Cafe A user can view Cafe A asset image, and Cafe B user is blocked
                r_a = client_a.get(f"/media/{self.cup.image.name}")
                self.assertEqual(r_a.status_code, 200)
                r_a.close()
                self.assertEqual(client_b.get(f"/media/{self.cup.image.name}").status_code, 403)
