"""Wave 1 Agent 1 regression tests for security, multi-tenancy, backups, monitoring, notifications, and infra."""
from datetime import timedelta
from decimal import Decimal
import io
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest import mock

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token

from purchases import drive_backup
from purchases.models import (
    Cafe,
    Debt,
    DebtPayment,
    Invoice,
    Notification,
    Payment,
    Product,
    PurchaseInvoice,
    PurchaseRequest,
    Requester,
    StockItem,
    Warehouse,
)
from purchases.views.backups import LOCK_BACKUP_OPERATION
from purchases.views.monitoring import _build_monitoring_metrics

User = get_user_model()


class Wave1Agent1SecurityInfraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe_a = Cafe.objects.create(name="Cafe A", code="ca_w1", is_active=True)
        cls.cafe_b = Cafe.objects.create(name="Cafe B", code="cb_w1", is_active=True)

        cls.superuser = User.objects.create_superuser(
            username="w1_super",
            email="w1super@example.com",
            password="StrongPass123!",
        )
        cls.global_admin = User.objects.create_user(
            username="w1_gadmin",
            email="w1ga@example.com",
            password="StrongPass123!",
            role="admin",
            cafe=None,
        )
        cls.mgr_a = User.objects.create_user(
            username="w1_mgr_a",
            email="w1mgra@example.com",
            password="StrongPass123!",
            role="manager",
            cafe=cls.cafe_a,
        )
        cls.staff_a = User.objects.create_user(
            username="w1_staff_a",
            email="w1staffa@example.com",
            password="StrongPass123!",
            role="requester",
            cafe=cls.cafe_a,
        )
        cls.staff_b = User.objects.create_user(
            username="w1_staff_b",
            email="w1staffb@example.com",
            password="StrongPass123!",
            role="requester",
            cafe=cls.cafe_b,
        )
        cls.ceo_a = User.objects.create_user(
            username="w1_ceo_a",
            email="w1ceoa@example.com",
            password="StrongPass123!",
            role="ceo",
            cafe=cls.cafe_a,
        )
        cls.sec_ceo_a = User.objects.create_user(
            username="w1_sec_ceo_a",
            email="w1secceoa@example.com",
            password="StrongPass123!",
            role="purchasing",
            secondary_role="ceo",
            cafe=cls.cafe_a,
        )

    def setUp(self):
        cache.clear()

    def _login(self, user, cafe=None):
        client = Client()
        self.assertTrue(client.login(username=user.username, password="StrongPass123!"))
        if cafe is not None:
            session = client.session
            session["selected_cafe_id"] = cafe.id
            session["selected_cafe_code"] = cafe.code
            session.save()
        return client

    # ------------------------------------------------------------------
    # 1. Accounts, RBAC, Impersonation, Token Revocation, Rate Limit
    # ------------------------------------------------------------------

    def test_manager_cannot_create_or_manage_ceo_or_secondary_ceo(self):
        client = self._login(self.mgr_a, self.cafe_a)

        # Cannot create CEO or secondary_role=ceo
        for payload in (
            {"role": "ceo", "secondary_role": ""},
            {"role": "purchasing", "secondary_role": "ceo"},
            {"role": "purchasing", "secondary_role": "admin"},
        ):
            resp = client.post(
                reverse("user_create", args=[self.cafe_a.code]),
                {
                    "username": f"u_{payload['role']}_{payload['secondary_role']}",
                    "password1": "StrongPass123!",
                    "password2": "StrongPass123!",
                    "cafe_id": str(self.cafe_a.id),
                    **payload,
                },
            )
            self.assertEqual(resp.status_code, 403)

        # Cannot update, reset password, or delete ceo or secondary_role=ceo
        for target in (self.ceo_a, self.sec_ceo_a):
            r_upd = client.post(
                reverse("user_update", args=[self.cafe_a.code, target.id]),
                {"username": target.username, "role": "requester", "is_active": "on"},
            )
            self.assertEqual(r_upd.status_code, 403)

            r_rst = client.post(
                reverse("user_reset_password", args=[self.cafe_a.code, target.id]),
                {"new_password": "NewStrongPass123!", "confirm_password": "NewStrongPass123!"},
            )
            self.assertEqual(r_rst.status_code, 403)

            r_del = client.post(reverse("user_delete", args=[self.cafe_a.code, target.id]))
            self.assertEqual(r_del.status_code, 403)

        # Cannot escalate own role to ceo or admin
        r_self = client.post(
            reverse("user_update", args=[self.cafe_a.code, self.mgr_a.id]),
            {"username": self.mgr_a.username, "role": "ceo", "is_active": "on"},
        )
        self.assertEqual(r_self.status_code, 403)
        self.mgr_a.refresh_from_db()
        self.assertEqual(self.mgr_a.role, "manager")

    def test_impersonate_blocks_secondary_privileged_and_stop_validates_original(self):
        client = self._login(self.mgr_a, self.cafe_a)
        resp = client.post(reverse("user_impersonate", args=[self.cafe_a.code, self.sec_ceo_a.id]))
        self.assertEqual(resp.status_code, 403)

        # Global admin impersonating staff_b (cafe_b) from cafe_a returns to cafe_a on stop
        gclient = self._login(self.global_admin, self.cafe_a)
        r_imp = gclient.post(reverse("user_impersonate", args=[self.cafe_a.code, self.staff_b.id]))
        self.assertEqual(r_imp.status_code, 302)
        self.assertEqual(gclient.session["selected_cafe_code"], self.cafe_b.code)
        self.assertEqual(gclient.session["impersonator_cafe_code"], self.cafe_a.code)

        r_stop = gclient.post(reverse("user_impersonate_stop", args=[self.cafe_b.code]))
        self.assertEqual(r_stop.status_code, 302)
        self.assertIn(f"/{self.cafe_a.code}/", r_stop["Location"])
        self.assertEqual(gclient.session["selected_cafe_code"], self.cafe_a.code)

        # If impersonator loses role/is_active, impersonate_stop refuses and redirects to login
        temp_mgr = User.objects.create_user(
            username="w1_temp_mgr",
            password="StrongPass123!",
            role="manager",
            cafe=self.cafe_a,
        )
        tclient = self._login(temp_mgr, self.cafe_a)
        tclient.post(reverse("user_impersonate", args=[self.cafe_a.code, self.staff_a.id]))
        temp_mgr.role = "requester"
        temp_mgr.save(update_fields=["role"])
        r_blocked_stop = tclient.post(reverse("user_impersonate_stop", args=[self.cafe_a.code]))
        self.assertEqual(r_blocked_stop.status_code, 302)
        self.assertIn("/login", r_blocked_stop["Location"])

    def test_drf_token_revoked_on_update_reset_delete(self):
        client = self._login(self.mgr_a, self.cafe_a)
        u = User.objects.create_user(
            username="w1_tok_user",
            password="StrongPass123!",
            role="purchasing",
            secondary_role="warehouse",
            cafe=self.cafe_a,
        )
        Token.objects.create(user=u)

        # Role change revokes token and clears secondary_role when not purchasing/warehouse
        r_upd = client.post(
            reverse("user_update", args=[self.cafe_a.code, u.id]),
            {
                "username": u.username,
                "role": "requester",
                "secondary_role": "warehouse",
                "is_active": "on",
            },
        )
        self.assertEqual(r_upd.status_code, 302)
        u.refresh_from_db()
        self.assertIsNone(u.secondary_role)
        self.assertFalse(Token.objects.filter(user=u).exists())

        # Password reset revokes token
        Token.objects.create(user=u)
        r_rst = client.post(
            reverse("user_reset_password", args=[self.cafe_a.code, u.id]),
            {"new_password": "AnotherStrongPass1!", "confirm_password": "AnotherStrongPass1!"},
        )
        self.assertEqual(r_rst.status_code, 302)
        self.assertFalse(Token.objects.filter(user=u).exists())

        # Delete revokes token
        Token.objects.create(user=u)
        r_del = client.post(reverse("user_delete", args=[self.cafe_a.code, u.id]))
        self.assertEqual(r_del.status_code, 302)
        self.assertFalse(Token.objects.filter(user_id=u.id).exists())

    def test_password_validation_and_no_strip_and_is_active_checkbox(self):
        client = self._login(self.mgr_a, self.cafe_a)

        # Weak numeric-only password rejected by validate_password
        r_weak = client.post(
            reverse("user_create", args=[self.cafe_a.code]),
            {
                "username": "w1_weak_u",
                "password1": "12345678",
                "password2": "12345678",
                "role": "requester",
                "cafe_id": str(self.cafe_a.id),
            },
        )
        self.assertEqual(r_weak.status_code, 200)
        self.assertFalse(User.objects.filter(username="w1_weak_u").exists())

        # Unchecked is_active via form hidden field creates inactive user
        r_inactive = client.post(
            reverse("user_create", args=[self.cafe_a.code]),
            {
                "username": "w1_inactive_u",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                "role": "requester",
                "cafe_id": str(self.cafe_a.id),
                "is_active_present": "1",
            },
        )
        self.assertEqual(r_inactive.status_code, 302)
        u_inact = User.objects.get(username="w1_inactive_u")
        self.assertFalse(u_inact.is_active)

        # user_update preserves leading/trailing spaces in password
        padded_pwd = " StrongPass123! "
        r_upd = client.post(
            reverse("user_update", args=[self.cafe_a.code, u_inact.id]),
            {
                "username": u_inact.username,
                "role": "requester",
                "is_active": "on",
                "new_password": padded_pwd,
                "confirm_new_password": padded_pwd,
            },
        )
        self.assertEqual(r_upd.status_code, 302)
        self.assertIsNotNone(authenticate(username=u_inact.username, password=padded_pwd))

    def test_requester_list_seeds_store_once_and_does_not_recreate_deleted(self):
        client = self._login(self.mgr_a, self.cafe_a)
        Requester.objects.filter(cafe=self.cafe_a).delete()

        r1 = client.get(reverse("requester_list", args=[self.cafe_a.code]))
        self.assertEqual(r1.status_code, 200)
        names = set(Requester.objects.filter(cafe=self.cafe_a).values_list("name", flat=True))
        self.assertEqual(names, {"kitchen", "bar", "facilities", "store"})

        # Delete 'store' and verify GET does not recreate it
        Requester.objects.filter(cafe=self.cafe_a, name="store").delete()
        r2 = client.get(reverse("requester_list", args=[self.cafe_a.code]))
        self.assertEqual(r2.status_code, 200)
        names_after = set(Requester.objects.filter(cafe=self.cafe_a).values_list("name", flat=True))
        self.assertEqual(names_after, {"kitchen", "bar", "facilities"})

    def test_login_rate_limiting(self):
        client = Client()
        login_url = reverse("login")
        for _ in range(5):
            r = client.post(login_url, {"username": self.staff_a.username, "password": "wrong"})
            self.assertEqual(r.status_code, 200)

        r_locked = client.post(login_url, {"username": self.staff_a.username, "password": "StrongPass123!"})
        self.assertEqual(r_locked.status_code, 429)

    # ------------------------------------------------------------------
    # 2. Protected Media Tenant Isolation
    # ------------------------------------------------------------------

    def test_protected_media_tenant_isolation(self):
        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=tmp_media):
                inv_dir = Path(tmp_media) / "invoices"
                debts_dir = Path(tmp_media) / "debts" / "payments"
                inv_dir.mkdir(parents=True, exist_ok=True)
                debts_dir.mkdir(parents=True, exist_ok=True)

                pr_b = PurchaseRequest.objects.create(user=self.staff_b, cafe=self.cafe_b, status="pending")
                pi_b = PurchaseInvoice.objects.create(
                    request=pr_b,
                    invoice_file=SimpleUploadedFile("b_inv.pdf", b"%PDF-1.4 b"),
                )
                debt_b = Debt.objects.create(
                    company="Sup B",
                    amount=100,
                    cafe=self.cafe_b,
                    payment_photo=SimpleUploadedFile("b_debt.jpg", b"jpgdata"),
                )
                dp_a = DebtPayment.objects.create(
                    debt=None,
                    company="Sup A",
                    amount=50,
                    cafe=self.cafe_a,
                    payment_photo=SimpleUploadedFile("a_dp.jpg", b"jpgdata_a"),
                )

                client_a = self._login(self.staff_a, self.cafe_a)
                # Cafe A user blocked from Cafe B invoice and debt photo
                self.assertEqual(client_a.get(f"/media/{pi_b.invoice_file.name}").status_code, 403)
                self.assertEqual(client_a.get(f"/media/{debt_b.payment_photo.name}").status_code, 403)
                # Cafe A user can read Cafe A debt payment photo
                r_dp_a = client_a.get(f"/media/{dp_a.payment_photo.name}")
                self.assertEqual(r_dp_a.status_code, 200)
                r_dp_a.close()
                # Superuser can read Cafe B invoice
                client_super = self._login(self.superuser, self.cafe_a)
                r_sup = client_super.get(f"/media/{pi_b.invoice_file.name}")
                self.assertEqual(r_sup.status_code, 200)
                r_sup.close()

    # ------------------------------------------------------------------
    # 3. Settings & Docker Configuration
    # ------------------------------------------------------------------

    def test_settings_and_docker_hardening(self):
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)

        repo_root = Path(settings.PROJECT_ROOT)
        settings_src = (repo_root / "syntel" / "syntel" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("SESSION_SAVE_EVERY_REQUEST = False", settings_src)
        self.assertIn("SESSION_ENGINE = 'django.contrib.sessions.backends.db'", settings_src)
        dockerignore = (repo_root / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("data/db/", dockerignore)
        self.assertIn("*.sqlite3*", dockerignore)

        dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("mv /app/data/db/db.sqlite3 /app/seed_data/db/db.sqlite3", dockerfile)
        self.assertIn("/app/data/db /app/data/logs", dockerfile)

    # ------------------------------------------------------------------
    # 4. Backups, Drive Backup, Monitoring, Notifications
    # ------------------------------------------------------------------

    def test_backup_management_get_does_not_trigger_auto_backup_and_lock_shared(self):
        client = self._login(self.superuser, self.cafe_a)
        with mock.patch("purchases.views.backups.trigger_auto_backup_async") as mock_trigger:
            resp = client.get(reverse("backup_management", args=[self.cafe_a.code]))
            self.assertEqual(resp.status_code, 200)
            mock_trigger.assert_not_called()

        # Shared lock blocks backup_database when another operation holds LOCK_BACKUP_OPERATION
        cache.set(LOCK_BACKUP_OPERATION, "1", timeout=60)
        try:
            r_locked = client.post(reverse("backup_database", args=[self.cafe_a.code]))
            self.assertEqual(r_locked.status_code, 302)
        finally:
            cache.delete(LOCK_BACKUP_OPERATION)

    def test_export_csv_sanitizes_formulas_and_uses_stock_items(self):
        client = self._login(self.mgr_a, self.cafe_a)
        wh = Warehouse.objects.create(name="Main WH", cafe=self.cafe_a)
        prod = Product.objects.create(name="=CMD|'/C calc'!A0", cafe=self.cafe_a, current_stock=999)
        StockItem.objects.create(product=prod, warehouse=wh, quantity=Decimal("12.50"), min_quantity=Decimal("5"))

        resp = client.get(reverse("export_data_csv", args=[self.cafe_a.code]) + "?model=products")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8-sig")
        self.assertIn("'=CMD|'/C calc'!A0", body)
        self.assertIn("12.5", body)
        self.assertNotIn("999", body)

    def test_drive_backup_range_and_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with override_settings(DATA_PATH=Path(tmp_dir)):
                drive_backup.save_token({
                    "access_token": "old_tok",
                    "refresh_token": "old_ref",
                    "expires_at": 0,
                })
                mock_post_resp = mock.MagicMock()
                mock_post_resp.json.return_value = {
                    "access_token": "new_tok",
                    "refresh_token": "new_ref",
                    "expires_in": 3600,
                }
                with mock.patch("purchases.drive_backup.requests.post", return_value=mock_post_resp):
                    tok = drive_backup._refresh_access_token()
                    self.assertEqual(tok, "new_tok")
                    self.assertEqual(drive_backup.load_token()["refresh_token"], "new_ref")

                # Test 308 Range header handling
                payload_path = Path(tmp_dir) / "sample.bin"
                payload_path.write_bytes(b"0123456789")

                put_calls = []

                def fake_put(url, headers=None, data=None, timeout=None):
                    put_calls.append((headers["Content-Range"], data))
                    r = mock.MagicMock()
                    if len(put_calls) == 1:
                        r.status_code = 308
                        r.headers = {"Range": "bytes=0-3"}
                    else:
                        r.status_code = 200
                        r.json.return_value = {"id": "drive_file_123"}
                    return r

                with mock.patch.object(drive_backup, "UPLOAD_CHUNK_SIZE", 6), \
                     mock.patch("purchases.drive_backup._start_resumable_session", return_value="https://upload.example"), \
                     mock.patch("purchases.drive_backup.requests.Session.put", side_effect=fake_put):
                    fid = drive_backup._upload_resumable_file("new_tok", "sample.bin", payload_path)
                    self.assertEqual(fid, "drive_file_123")
                    self.assertEqual(put_calls[0][0], "bytes 0-5/10")
                    self.assertEqual(put_calls[1][0], "bytes 4-9/10")
                    self.assertEqual(put_calls[1][1], b"456789")

    def test_monitoring_m2m_subquery_outstanding_and_stock_item(self):
        today = timezone.localdate()
        inv1 = Invoice.objects.create(
            cafe=self.cafe_a,
            invoice_number="INV-W1-1",
            due_date=today,
            total_amount=Decimal("1000"),
            status="approved",
        )
        inv2 = Invoice.objects.create(
            cafe=self.cafe_a,
            invoice_number="INV-W1-2",
            due_date=today,
            total_amount=Decimal("2000"),
            status="approved",
        )
        pay = Payment.objects.create(amount=Decimal("500"), payment_date=today, payment_status="completed")
        pay.invoices.add(inv1, inv2)

        wh = Warehouse.objects.create(name="WH Mon", cafe=self.cafe_a)
        p_ok = Product.objects.create(name="Prod OK", cafe=self.cafe_a, current_stock=0, min_stock=10)
        p_low = Product.objects.create(name="Prod Low", cafe=self.cafe_a, current_stock=100, min_stock=1)
        StockItem.objects.create(product=p_ok, warehouse=wh, quantity=Decimal("20"), min_quantity=Decimal("5"))
        StockItem.objects.create(product=p_low, warehouse=wh, quantity=Decimal("2"), min_quantity=Decimal("5"))

        metrics = _build_monitoring_metrics(self.cafe_a)
        # M2M payment must not double count 500 -> 1000 in 7-day trend
        today_rev = [d["amount"] for d in metrics["performance_trends"]["daily_revenue"] if d["date"] == today.isoformat()][0]
        self.assertEqual(today_rev, 500.0)
        # Outstanding payments = remaining_amount of inv1 (500) + inv2 (2000) = 2500
        self.assertEqual(metrics["health_status"]["outstanding_payments"], 2500.0)
        # Low stock counted from StockItem (only p_low)
        self.assertEqual(metrics["health_status"]["low_stock_products"], 1)

    def test_notifications_recent_includes_id_and_detail_marks_read(self):
        notif = Notification.objects.create(
            cafe=self.cafe_a,
            recipient=self.staff_a,
            title="Test Notif",
            message="Hello",
            is_read=False,
        )
        client = self._login(self.staff_a, self.cafe_a)

        r_recent = client.get(reverse("recent_notifications", args=[self.cafe_a.code]))
        self.assertEqual(r_recent.status_code, 200)
        items = r_recent.json()["notifications"]
        self.assertEqual(items[0]["id"], notif.id)

        from django.http import HttpResponse
        with mock.patch("purchases.views.notifications.render", return_value=HttpResponse("ok")):
            r_detail = client.get(reverse("notification_detail", args=[self.cafe_a.code, notif.id]))
        self.assertEqual(r_detail.status_code, 200)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
