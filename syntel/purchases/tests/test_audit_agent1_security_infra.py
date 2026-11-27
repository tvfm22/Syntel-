"""Security, Multi-Tenancy, Backup & Infrastructure audit tests (Agent 1)."""
from datetime import timedelta
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest import mock
import zipfile

from django.contrib.auth import authenticate, get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe,
    Category,
    Invoice,
    PriceHistory,
    Product,
    PurchaseRequest,
    Requester,
    Supplier,
    Unit,
)
from purchases.views.backups import _build_data_folder_zip
from syntel.data_paths import resolve_data_path

User = get_user_model()


class Agent1SecurityAndInfraTests(TestCase):
    """Comprehensive tests for auth, RBAC, isolation, backups, monitoring, and infra."""

    @classmethod
    def setUpTestData(cls):
        cls.cafe_a = Cafe.objects.create(name="Cafe A", code="cafe_a", is_active=True)
        cls.cafe_b = Cafe.objects.create(name="Cafe B", code="cafe_b", is_active=True)
        cls.cafe_inactive = Cafe.objects.create(name="Inactive Cafe", code="cafe_off", is_active=False)

        cls.superuser = User.objects.create_superuser(
            username="root_super",
            email="super@example.com",
            password="StrongPass123!",
        )
        cls.global_admin = User.objects.create_user(
            username="global_admin",
            email="gadmin@example.com",
            password="StrongPass123!",
            role="admin",
            cafe=None,
        )
        cls.mgr_a = User.objects.create_user(
            username="mgr_a",
            email="mgra@example.com",
            password="StrongPass123!",
            role="manager",
            cafe=cls.cafe_a,
        )
        cls.mgr_b = User.objects.create_user(
            username="mgr_b",
            email="mgrb@example.com",
            password="StrongPass123!",
            role="manager",
            cafe=cls.cafe_b,
        )
        cls.staff_a = User.objects.create_user(
            username="staff_a",
            email="staffa@example.com",
            password="StrongPass123!",
            role="requester",
            cafe=cls.cafe_a,
        )
        cls.staff_b = User.objects.create_user(
            username="staff_b",
            email="staffb@example.com",
            password="StrongPass123!",
            role="requester",
            cafe=cls.cafe_b,
        )
        cls.admin_a = User.objects.create_user(
            username="admin_a",
            email="admina@example.com",
            password="StrongPass123!",
            role="admin",
            cafe=cls.cafe_a,
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

    # ------------------------------------------------------------------
    # 1. Impersonation & User Management Privilege Boundaries
    # ------------------------------------------------------------------

    def test_single_cafe_manager_impersonation_restrictions(self):
        client = self._login(self.mgr_a, self.cafe_a)

        # Blocked from impersonating superuser, global admin, cafe-scoped admin, and other-cafe user
        for target in (self.superuser, self.global_admin, self.admin_a, self.mgr_b, self.staff_b):
            resp = client.post(reverse("user_impersonate", args=[self.cafe_a.code, target.id]))
            self.assertIn(resp.status_code, (403, 404))
            self.assertEqual(client.session["_auth_user_id"], str(self.mgr_a.id))

        # Allowed to impersonate non-privileged user in own cafe
        resp_ok = client.post(reverse("user_impersonate", args=[self.cafe_a.code, self.staff_a.id]))
        self.assertEqual(resp_ok.status_code, 302)
        self.assertEqual(client.session["_auth_user_id"], str(self.staff_a.id))
        self.assertEqual(client.session["impersonator_id"], self.mgr_a.id)

        # Stop impersonation returns to manager
        resp_stop = client.post(reverse("user_impersonate_stop", args=[self.cafe_a.code]))
        self.assertEqual(resp_stop.status_code, 302)
        self.assertEqual(client.session["_auth_user_id"], str(self.mgr_a.id))

    def test_single_cafe_manager_cannot_create_admin_or_global_user(self):
        client = self._login(self.mgr_a, self.cafe_a)

        resp_admin = client.post(reverse("user_create", args=[self.cafe_a.code]), {
            "username": "escalated_admin",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "role": "admin",
            "cafe_id": str(self.cafe_a.id),
        })
        self.assertEqual(resp_admin.status_code, 403)
        self.assertFalse(User.objects.filter(username="escalated_admin").exists())

        resp_global = client.post(reverse("user_create", args=[self.cafe_a.code]), {
            "username": "escalated_global",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "role": "manager",
            "cafe_id": "all",
        })
        self.assertEqual(resp_global.status_code, 403)
        self.assertFalse(User.objects.filter(username="escalated_global").exists())

    def test_user_list_and_management_tenant_isolation(self):
        client = self._login(self.mgr_a, self.cafe_a)
        resp = client.get(reverse("user_list", args=[self.cafe_a.code]))
        self.assertEqual(resp.status_code, 200)
        listed_ids = set(resp.context["users"].values_list("id", flat=True))
        self.assertIn(self.staff_a.id, listed_ids)
        self.assertNotIn(self.global_admin.id, listed_ids)
        self.assertNotIn(self.superuser.id, listed_ids)
        self.assertNotIn(self.staff_b.id, listed_ids)

        # Cannot reset password / update / delete other-cafe or global/admin users
        for target in (self.superuser, self.global_admin, self.admin_a, self.staff_b):
            r_reset = client.post(reverse("user_reset_password", args=[self.cafe_a.code, target.id]), {
                "new_password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            })
            self.assertIn(r_reset.status_code, (403, 404))

            r_del = client.post(reverse("user_delete", args=[self.cafe_a.code, target.id]))
            self.assertIn(r_del.status_code, (403, 404))

    def test_user_reset_password_min_length_and_confirmation(self):
        client = self._login(self.mgr_a, self.cafe_a)
        url = reverse("user_reset_password", args=[self.cafe_a.code, self.staff_a.id])

        # Too short
        r_short = client.post(url, {"new_password": "short", "confirm_password": "short"})
        self.assertEqual(r_short.status_code, 200)
        self.staff_a.refresh_from_db()
        self.assertTrue(self.staff_a.check_password("StrongPass123!"))

        # Mismatch
        r_mismatch = client.post(url, {
            "new_password": "LongEnough123!",
            "confirm_password": "DifferentPass123!",
        })
        self.assertEqual(r_mismatch.status_code, 200)
        self.staff_a.refresh_from_db()
        self.assertTrue(self.staff_a.check_password("StrongPass123!"))

        # Valid
        r_ok = client.post(url, {
            "new_password": "NewValidPass123!",
            "confirm_password": "NewValidPass123!",
        })
        self.assertEqual(r_ok.status_code, 302)
        self.staff_a.refresh_from_db()
        self.assertTrue(self.staff_a.check_password("NewValidPass123!"))

    def test_login_csrf_enforced_and_unauthorized_cafe_rejected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        resp = csrf_client.post(reverse("login"), {
            "username": self.staff_a.username,
            "password": "StrongPass123!",
            "cafe_code": self.cafe_a.code,
        })
        self.assertEqual(resp.status_code, 403)

        # Normal client attempting cross-cafe login
        client = Client()
        resp_cross = client.post(reverse("login"), {
            "username": self.staff_a.username,
            "password": "StrongPass123!",
            "cafe_code": self.cafe_b.code,
        })
        self.assertEqual(resp_cross.status_code, 200)
        self.assertNotIn("_auth_user_id", client.session)

    # ------------------------------------------------------------------
    # 2. Backup / Restore / Drive Lockdown & ZIP Secret Exclusion
    # ------------------------------------------------------------------

    def test_single_cafe_manager_blocked_from_all_backup_and_drive_endpoints(self):
        client = self._login(self.mgr_a, self.cafe_a)

        get_endpoints = [
            reverse("backup_management", args=[self.cafe_a.code]),
            reverse("download_database_file", args=[self.cafe_a.code]),
            reverse("download_backup_file", args=[self.cafe_a.code, "dummy.json"]),
            reverse("export_data_folder", args=[self.cafe_a.code]),
            reverse("export_data_json", args=[self.cafe_a.code]),
            reverse("drive_connect", args=[self.cafe_a.code]),
            reverse("drive_callback"),
        ]
        for url in get_endpoints:
            resp = client.get(url)
            self.assertEqual(resp.status_code, 403, f"Expected 403 for GET {url}, got {resp.status_code}")

        post_endpoints = [
            reverse("backup_database", args=[self.cafe_a.code]),
            reverse("restore_database_file", args=[self.cafe_a.code]),
            reverse("delete_backup_file", args=[self.cafe_a.code, "dummy.json"]),
            reverse("import_data_json", args=[self.cafe_a.code]),
            reverse("drive_save_config", args=[self.cafe_a.code]),
            reverse("drive_disconnect", args=[self.cafe_a.code]),
            reverse("drive_backup_now", args=[self.cafe_a.code]),
        ]
        for url in post_endpoints:
            resp = client.post(url)
            self.assertEqual(resp.status_code, 403, f"Expected 403 for POST {url}, got {resp.status_code}")

        # Global admin CAN access backup_management
        ga_client = self._login(self.global_admin, self.cafe_a)
        resp_ok = ga_client.get(reverse("backup_management", args=[self.cafe_a.code]))
        self.assertEqual(resp_ok.status_code, 200)

    def test_backup_zip_excludes_sessions_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            (root / "sessions").mkdir()
            (root / "sessions" / "sessionid_secret").write_text("sessiondata", encoding="utf-8")
            (root / "uploads").mkdir()
            (root / "uploads" / "receipt.png").write_bytes(b"PNGDATA")
            (root / "drive_token.json").write_text('{"access_token":"secret"}', encoding="utf-8")
            (root / "drive_config.json").write_text('{"client_secret":"secret"}', encoding="utf-8")
            (root / "drive_autobackup.json").write_text('{"status":"ok"}', encoding="utf-8")
            (root / ".env.production").write_text("DJANGO_SECRET_KEY=topsecret", encoding="utf-8")
            (root / "tls.key").write_text("PRIVATE KEY", encoding="utf-8")
            (root / "cert.pem").write_text("CERT", encoding="utf-8")

            zip_path = root / "out.zip"
            with override_settings(DATA_PATH=root, TMP_DIR=root / "tmp"):
                _build_data_folder_zip(zip_path)

            with zipfile.ZipFile(zip_path, "r") as zf:
                names = set(zf.namelist())

            self.assertIn("uploads/receipt.png", names)
            for forbidden in (
                "sessions/sessionid_secret",
                "drive_token.json",
                "drive_config.json",
                "drive_autobackup.json",
                ".env.production",
                "tls.key",
                "cert.pem",
            ):
                self.assertNotIn(forbidden, names)

    def test_restore_database_rejects_corrupt_sqlite_and_postgres(self):
        client = self._login(self.superuser, self.cafe_a)
        url = reverse("restore_database_file", args=[self.cafe_a.code])

        # Valid SQLite header followed by garbage -> fails PRAGMA integrity_check
        corrupt = io.BytesIO(b"SQLite format 3\x00" + b"\xff" * 4096)
        corrupt.name = "corrupt.sqlite3"
        resp = client.post(url, {"db_file": corrupt}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "بررسی یکپارچگی ناموفق بود")

        # PostgreSQL engine must reject SQLite file restore
        with mock.patch("purchases.views.backups._is_sqlite_engine", return_value=False):
            resp_pg = client.post(url, {"db_file": io.BytesIO(b"SQLite format 3\x00")}, follow=True)
            self.assertEqual(resp_pg.status_code, 200)
            self.assertContains(resp_pg, "PostgreSQL")

    # ------------------------------------------------------------------
    # 3. Middleware, Context Processor & Auth Backend
    # ------------------------------------------------------------------

    def test_middleware_inactive_cafe_superuser_and_drive_exempt(self):
        # Superuser with role='kitchen' and cafe=None can access active cafe
        su = User.objects.create_superuser(
            username="cli_superuser",
            email="clisu@example.com",
            password="StrongPass123!",
            role="kitchen",
            cafe=None,
        )
        su_client = self._login(su)
        resp_su = su_client.get(reverse("dashboard", args=[self.cafe_a.code]))
        self.assertEqual(resp_su.status_code, 200)

        # Inactive cafe blocks access even for superadmin with stale session
        resp_inactive = su_client.get(reverse("dashboard", args=[self.cafe_inactive.code]))
        self.assertEqual(resp_inactive.status_code, 302)
        self.assertEqual(resp_inactive.url, reverse("select_cafe"))

        # Single-cafe user assigned to inactive cafe gets 404 and cleared session
        inactive_cafe_user = User.objects.create_user(
            username="user_in_inactive",
            password="StrongPass123!",
            role="requester",
            cafe=self.cafe_inactive,
        )
        ic_client = self._login(inactive_cafe_user)
        resp_ic = ic_client.get(reverse("dashboard", args=[self.cafe_inactive.code]))
        self.assertEqual(resp_ic.status_code, 404)

        # /backup/drive/callback/ is exempt from CafeMiddleware (redirects to login instead of select_cafe)
        anon_client = Client()
        resp_cb = anon_client.get(reverse("drive_callback"))
        self.assertEqual(resp_cb.status_code, 302)
        self.assertIn("/login/", resp_cb.url)

    def test_context_processor_does_not_fallback_to_local(self):
        Cafe.objects.create(name="Local", code="local", is_active=True)
        client = self._login(self.global_admin)
        resp = client.get(reverse("select_cafe"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["current_cafe"])
        self.assertIsNone(resp.context["cafe_code"])

    def test_case_insensitive_backend_rejects_ambiguous_accounts(self):
        User.objects.create_user(username="AmbigUser", password="StrongPass123!", role="requester", cafe=self.cafe_a)
        User.objects.create_user(username="ambiguser", password="OtherStrongPass123!", role="requester", cafe=self.cafe_a)

        # Exact match still succeeds
        self.assertIsNotNone(authenticate(username="AmbigUser", password="StrongPass123!"))
        # Non-exact lookup with colliding accounts must fail closed
        self.assertIsNone(authenticate(username="AMBIGUSER", password="StrongPass123!"))
        self.assertIsNone(authenticate(username="AMBIGUSER", password="OtherStrongPass123!"))

    # ------------------------------------------------------------------
    # 4. Requester Tenant Isolation
    # ------------------------------------------------------------------

    def test_requester_tenant_isolation(self):
        req_a = Requester.objects.create(name="kitchen", cafe=self.cafe_a)
        req_b = Requester.objects.create(name="kitchen", cafe=self.cafe_b)

        client_a = self._login(self.mgr_a, self.cafe_a)
        r_list = client_a.get(reverse("requester_list", args=[self.cafe_a.code]))
        self.assertEqual(r_list.status_code, 200)
        ids = set(r_list.context["requesters"].values_list("id", flat=True))
        self.assertIn(req_a.id, ids)
        self.assertNotIn(req_b.id, ids)

        # Cannot delete Cafe B's requester from Cafe A
        r_del = client_a.post(reverse("requester_delete", args=[self.cafe_a.code, req_b.id]))
        self.assertEqual(r_del.status_code, 404)
        self.assertTrue(Requester.objects.filter(id=req_b.id).exists())

    # ------------------------------------------------------------------
    # 5. CSV Export & System Monitoring Contract
    # ------------------------------------------------------------------

    def test_export_data_csv_purchases_invoices_and_allowlist(self):
        pr = PurchaseRequest.objects.create(user=self.staff_a, cafe=self.cafe_a, status="pending")
        Invoice.objects.create(
            invoice_number="INV-CASH-1",
            supplier=None,
            store_name="Cash Shop",
            total_amount=Decimal("1500.00"),
            cafe=self.cafe_a,
        )

        client = self._login(self.mgr_a, self.cafe_a)

        r_pur = client.get(reverse("export_data_csv", args=[self.cafe_a.code]) + "?model=purchases")
        self.assertEqual(r_pur.status_code, 200)
        body = r_pur.content.decode("utf-8")
        self.assertIn(self.staff_a.username, body)
        self.assertIn(str(pr.id), body)

        r_inv = client.get(reverse("export_data_csv", args=[self.cafe_a.code]) + "?model=invoices")
        self.assertEqual(r_inv.status_code, 200)
        self.assertIn("INV-CASH-1", r_inv.content.decode("utf-8"))

        # Invalid/injection model name rejected
        r_bad = client.get(
            reverse("export_data_csv", args=[self.cafe_a.code]) + '?model=bad"\r\nX-Injected:1'
        )
        self.assertEqual(r_bad.status_code, 400)

    def test_system_monitoring_and_api_contract(self):
        client = self._login(self.mgr_a, self.cafe_a)

        resp = client.get(reverse("system_monitoring", args=[self.cafe_a.code]))
        self.assertEqual(resp.status_code, 200)
        trends = resp.context["performance_trends"]
        self.assertIn("daily_requests", trends)
        self.assertIn("daily_payments", trends)
        self.assertIn("daily_revenue", trends)
        json.dumps(trends)  # Must be JSON-serializable

        api_resp = client.get(reverse("system_monitoring_api", args=[self.cafe_a.code]))
        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertIn("resources", payload)
        self.assertIn("cpu_percent", payload["resources"])
        self.assertIn("memory_percent", payload["resources"])
        self.assertIn("disk_usage", payload["resources"])
        self.assertIn("health", payload)
        self.assertIn("pending_invoices", payload["health"])
        self.assertIn("outstanding_payments", payload["health"])
        self.assertIn("active_connections", payload["health"])

    # ------------------------------------------------------------------
    # 6. Management Commands & Price Alert Tenant Isolation
    # ------------------------------------------------------------------

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_check_price_alerts_isolated_per_cafe(self):
        mail.outbox.clear()
        cat_a = Category.objects.create(name="CatA", cafe=self.cafe_a)
        unit_a = Unit.objects.create(name="KgA", symbol="kg", cafe=self.cafe_a)
        prod_a = Product.objects.create(name="Espresso Bean A", category=cat_a, unit=unit_a, cafe=self.cafe_a)
        sup_a = Supplier.objects.create(name="Supplier A", cafe=self.cafe_a)
        PriceHistory.objects.create(
            product=prod_a,
            price=Decimal("250000.00"),
            supplier=sup_a,
            percent_change=Decimal("15.00"),
            date=timezone.now() - timedelta(hours=1),
        )

        call_command("check_price_alerts")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.mgr_a.email])
        self.assertNotIn(self.mgr_b.email, mail.outbox[0].to)
        self.assertIn("Espresso Bean A", mail.outbox[0].body)

    def test_seed_commands_require_cafe_and_strong_password(self):
        with self.assertRaises(CommandError):
            call_command("populate_base_data")
        with self.assertRaises(CommandError):
            call_command("populate_payment_types")
        with self.assertRaises(CommandError):
            call_command("setup_users", cafe=self.cafe_a.code, password="123")

    # ------------------------------------------------------------------
    # 7. Media Auth, Health Check Error Hiding, Schema Gate & Data Path
    # ------------------------------------------------------------------

    def test_media_requires_auth_and_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmp_media:
            media_root = Path(tmp_media)
            (media_root / "receipt.txt").write_text("receipt-content", encoding="utf-8")

            with override_settings(MEDIA_ROOT=media_root):
                anon = Client()
                r_anon = anon.get("/media/receipt.txt")
                self.assertEqual(r_anon.status_code, 302)
                self.assertIn("/login/", r_anon.url)

                authed = self._login(self.staff_a, self.cafe_a)
                r_ok = authed.get("/media/receipt.txt")
                self.assertEqual(r_ok.status_code, 200)
                r_ok.close()

                r_trav = authed.get("/media/../../etc/passwd")
                self.assertEqual(r_trav.status_code, 404)

    def test_health_detailed_hides_raw_db_exception_and_schema_restricted(self):
        anon = Client()
        with mock.patch("django.db.backends.utils.CursorWrapper.execute", side_effect=RuntimeError("SECRET_DSN_LEAK")):
            resp = anon.get(reverse("health_check_detailed"))
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "unhealthy")
        self.assertEqual(body["database"], "error")
        self.assertNotIn("SECRET_DSN_LEAK", resp.content.decode("utf-8"))

        # Schema requires admin (is_staff)
        r_schema_anon = anon.get(reverse("schema"))
        self.assertIn(r_schema_anon.status_code, (401, 403))

    def test_resolve_data_path_raises_on_unwritable_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            blocker_file = Path(tmp_dir) / "not_a_dir"
            blocker_file.write_text("file", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DATA_PATH": str(blocker_file)}):
                with self.assertRaises(RuntimeError):
                    resolve_data_path(Path(tmp_dir))
