"""
تست‌های جامع ویوهای مدیریت اموال و اثاثیه سکشن‌ها، انتقال دوطرفه،
آپلود و فشرده‌سازی عکس، اتصال به فاکتور و خرید جایگزین، کنترل دسترسی و منو.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from purchases.models import (
    AssetCatalog,
    AssetCategory,
    AssetMovement,
    AssetUnit,
    Cafe,
    Department,
    DepartmentAssetStock,
    Invoice,
    InvoiceItem,
    PurchaseRequest,
)
from purchases.services.assets import allocate_initial_or_addition

User = get_user_model()


def _make_upload_image(name: str = 'cup.png', width: int = 800, height: int = 600) -> SimpleUploadedFile:
    buf = io.BytesIO()
    img = Image.new('RGB', (width, height), color=(50, 110, 190))
    img.save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


class AssetsManagementViewsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp_media = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(MEDIA_ROOT=cls._tmp_media.name)
        cls._media_override.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.__exit__(None, None, None)
        cls._tmp_media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.cafe_a = Cafe.objects.create(name='کافه الف', code='mgmt_ca', is_active=True)
        self.cafe_b = Cafe.objects.create(name='کافه ب', code='mgmt_cb', is_active=True)

        self.dept_bar = Department.objects.create(name='بار', cafe=self.cafe_a, is_active=True)
        self.dept_kitchen = Department.objects.create(name='آشپزخانه', cafe=self.cafe_a, is_active=True)
        self.dept_b = Department.objects.create(name='سالن ب', cafe=self.cafe_b, is_active=True)

        self.mgr_a = User.objects.create_user(
            username='mgmt_mgr_a', password='pw', role='manager', cafe=self.cafe_a
        )
        self.mgr_b = User.objects.create_user(
            username='mgmt_mgr_b', password='pw', role='manager', cafe=self.cafe_b
        )
        self.req_bar = User.objects.create_user(
            username='mgmt_req_bar',
            password='pw',
            role='requester',
            cafe=self.cafe_a,
            department=self.dept_bar,
        )
        self.req_kitchen = User.objects.create_user(
            username='mgmt_req_kitchen',
            password='pw',
            role='requester',
            cafe=self.cafe_a,
            department=self.dept_kitchen,
        )

        self.cat_dishes = AssetCategory.objects.create(cafe=self.cafe_a, name='ظروف سرو')
        self.cup = AssetCatalog.objects.create(
            cafe=self.cafe_a,
            category=self.cat_dishes,
            name='فنجان لاته',
            code='CUP-01',
            tracking_mode='quantity',
            estimated_unit_value=Decimal('180000'),
        )
        self.grinder = AssetCatalog.objects.create(
            cafe=self.cafe_a,
            category=self.cat_dishes,
            name='آسیاب قهوه',
            code='GRN-01',
            tracking_mode='serialized',
            estimated_unit_value=Decimal('45000000'),
        )

    def test_catalog_create_compresses_image_and_generates_thumbnail_and_menu_renders(self):
        client = Client()
        client.force_login(self.mgr_a)

        dash_url = reverse('assets_dashboard', kwargs={'cafe_code': self.cafe_a.code})
        dash_resp = client.get(dash_url)
        self.assertEqual(dash_resp.status_code, 200)
        html = dash_resp.content.decode('utf-8')
        self.assertIn(reverse('assets_dashboard', kwargs={'cafe_code': self.cafe_a.code}), html)
        self.assertIn(reverse('asset_audit_list', kwargs={'cafe_code': self.cafe_a.code}), html)

        create_url = reverse('asset_catalog_create', kwargs={'cafe_code': self.cafe_a.code})
        resp = client.post(
            create_url,
            data={
                'name': 'لیوان آیس',
                'code': 'GLS-99',
                'category_id': self.cat_dishes.id,
                'tracking_mode': 'quantity',
                'estimated_unit_value': '120000',
                'is_active': '1',
                'image': _make_upload_image('ice_glass.png', 1600, 1200),
            },
        )
        self.assertEqual(resp.status_code, 302)
        created = AssetCatalog.objects.get(cafe=self.cafe_a, code='GLS-99')
        self.assertTrue(bool(created.image))
        self.assertTrue(bool(created.thumbnail))
        with Image.open(created.thumbnail.path) as thumb_img:
            self.assertLessEqual(thumb_img.width, 240)
            self.assertLessEqual(thumb_img.height, 240)

    def test_requester_access_control_and_damage_report_approval_flow(self):
        allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=20,
            user=self.mgr_a,
            min_expected_quantity=25,
        )

        req_client = Client()
        req_client.force_login(self.req_bar)

        # منو برای requester هم رندر می‌شود
        dash_resp = req_client.get(reverse('assets_dashboard', kwargs={'cafe_code': self.cafe_a.code}))
        self.assertEqual(dash_resp.status_code, 200)
        dash_html = dash_resp.content.decode('utf-8')
        self.assertIn(reverse('assets_dashboard', kwargs={'cafe_code': self.cafe_a.code}), dash_html)
        self.assertIn(reverse('asset_audit_list', kwargs={'cafe_code': self.cafe_a.code}), dash_html)

        # دسترسی به بخش خود مجاز، به بخش دیگر ممنوع (403)
        own_stock_url = reverse(
            'asset_department_stock',
            kwargs={'cafe_code': self.cafe_a.code, 'department_id': self.dept_bar.id},
        )
        other_stock_url = reverse(
            'asset_department_stock',
            kwargs={'cafe_code': self.cafe_a.code, 'department_id': self.dept_kitchen.id},
        )
        self.assertEqual(req_client.get(own_stock_url).status_code, 200)
        self.assertEqual(req_client.get(other_stock_url).status_code, 403)

        # requester حق ساخت کاتالوگ یا تخصیص مستقیم ندارد
        self.assertEqual(
            req_client.get(reverse('asset_catalog_create', kwargs={'cafe_code': self.cafe_a.code})).status_code,
            403,
        )
        self.assertEqual(
            req_client.post(
                reverse(
                    'asset_stock_allocate',
                    kwargs={'cafe_code': self.cafe_a.code, 'department_id': self.dept_bar.id},
                ),
                data={'asset_id': self.cup.id, 'quantity': 5},
            ).status_code,
            403,
        )

        # گزارش خرابی توسط requester در حالت pending می‌ماند و موجودی را تغییر نمی‌دهد
        dmg_url = reverse(
            'asset_damage_report',
            kwargs={'cafe_code': self.cafe_a.code, 'department_id': self.dept_bar.id},
        )
        resp = req_client.post(
            dmg_url,
            data={
                'asset_id': self.cup.id,
                'quantity': 3,
                'reason': 'شکستگی در سرویس عصر',
                'auto_approve': '1',
                'evidence_photo': _make_upload_image('broken_cup.png', 400, 300),
            },
        )
        self.assertEqual(resp.status_code, 302)
        mv = AssetMovement.objects.get(cafe=self.cafe_a, movement_type='disposal', asset=self.cup)
        self.assertEqual(mv.status, 'pending')
        stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup)
        self.assertEqual(stock.quantity_healthy, 20)

        # requester حق تأیید سند pending ندارد
        approve_url = reverse('asset_movement_approve', kwargs={'cafe_code': self.cafe_a.code, 'pk': mv.id})
        self.assertEqual(req_client.post(approve_url).status_code, 403)

        # مدیر تأیید می‌کند و موجودی کسر می‌شود
        mgr_client = Client()
        mgr_client.force_login(self.mgr_a)
        self.assertEqual(mgr_client.post(approve_url).status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity_healthy, 17)

    def test_two_way_transfer_handshake_and_cross_cafe_isolation(self):
        allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=10,
            user=self.mgr_a,
        )

        bar_client = Client()
        bar_client.force_login(self.req_bar)

        resp = bar_client.post(
            reverse('asset_transfer_create', kwargs={'cafe_code': self.cafe_a.code}),
            data={
                'to_department_id': self.dept_kitchen.id,
                'asset_id': self.cup.id,
                'quantity': 4,
                'reason': 'انتقال برای سرویس ناهار',
            },
        )
        self.assertEqual(resp.status_code, 302)
        tr = AssetMovement.objects.get(cafe=self.cafe_a, movement_type='transfer')
        self.assertEqual(tr.status, 'pending_receipt')

        # کاربر بار (مبدأ) مجاز به تأیید تحویل مقصد نیست
        confirm_url = reverse('asset_transfer_confirm', kwargs={'cafe_code': self.cafe_a.code, 'pk': tr.id})
        self.assertEqual(bar_client.post(confirm_url).status_code, 403)

        # کاربر آشپزخانه (مقصد) تحویل را تأیید می‌کند
        kitchen_client = Client()
        kitchen_client.force_login(self.req_kitchen)
        self.assertEqual(kitchen_client.post(confirm_url).status_code, 302)

        tr.refresh_from_db()
        self.assertEqual(tr.status, 'approved')
        self.assertEqual(DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup).quantity_healthy, 6)
        self.assertEqual(
            DepartmentAssetStock.objects.get(department=self.dept_kitchen, asset=self.cup).quantity_healthy, 4
        )

        # ایزوله‌سازی چندکافه‌ای: مدیر کافه ب به داده‌های کافه الف دسترسی ندارد
        b_client = Client()
        b_client.force_login(self.mgr_b)
        self.assertEqual(
            b_client.get(reverse('asset_catalog_edit', kwargs={'cafe_code': self.cafe_b.code, 'pk': self.cup.id})).status_code,
            404,
        )

    def test_serialized_unit_invoice_import_and_replacement_purchase_request(self):
        client = Client()
        client.force_login(self.mgr_a)

        # ایجاد فاکتور در کافه الف
        inv = Invoice.objects.create(
            cafe=self.cafe_a,
            invoice_number='INV-ASSET-101',
            store_name='تجهیزات کافه تهران',
            invoice_date=date(2026, 11, 20),
            due_date=date(2026, 12, 20),
            status='approved',
            total_amount=Decimal('50000000'),
        )
        InvoiceItem.objects.create(
            invoice=inv,
            description='آسیاب قهوه صنعتی',
            quantity=Decimal('1'),
            unit_price=Decimal('45000000'),
            line_total=Decimal('45000000'),
        )

        # ورود کالای پلاک‌دار از فاکتور
        import_url = reverse('asset_import_from_invoice', kwargs={'cafe_code': self.cafe_a.code})
        resp = client.post(
            import_url,
            data={
                'invoice_id': inv.id,
                'asset_id': self.grinder.id,
                'department_id': self.dept_bar.id,
                'asset_tag': 'TAG-GRN-01',
                'serial_number': 'SN-998877',
                'unit_value': '45000000',
            },
        )
        self.assertEqual(resp.status_code, 302)
        unit = AssetUnit.objects.get(cafe=self.cafe_a, asset_tag='TAG-GRN-01')
        self.assertEqual(unit.invoice_id, inv.id)
        self.assertEqual(unit.department_id, self.dept_bar.id)

        # تغییر وضعیت تعمیر پلاک
        repair_url = reverse('asset_unit_repair_action', kwargs={'cafe_code': self.cafe_a.code, 'pk': unit.id})
        self.assertEqual(
            client.post(repair_url, data={'new_status': 'in_repair', 'reason': 'تعویض تیغه'}).status_code,
            302,
        )
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'in_repair')

        # ایجاد درخواست خرید جایگزین برای کسری سکشن
        allocate_initial_or_addition(
            cafe=self.cafe_a,
            department=self.dept_bar,
            asset=self.cup,
            quantity=12,
            user=self.mgr_a,
            min_expected_quantity=20,
        )
        stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.cup)
        repl_url = reverse('asset_create_replacement_request', kwargs={'cafe_code': self.cafe_a.code})
        repl_resp = client.post(
            repl_url,
            data={
                'department_id': self.dept_bar.id,
                'stock_id': stock.id,
                'asset_id': self.cup.id,
                'quantity': 8,
                'name': self.cup.name,
            },
        )
        self.assertEqual(repl_resp.status_code, 302)
        pr = PurchaseRequest.objects.get(cafe=self.cafe_a)
        self.assertEqual(pr.items.count(), 1)
        self.assertEqual(pr.items.first().quantity, Decimal('8'))
