"""
تست‌های جامع انبارگردانی اموال و ۶ گزارش تحلیلی + خروجی‌های Excel و PDF.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import NoReverseMatch, include, path, reverse
from django.utils import timezone
import openpyxl
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
)
from purchases.services.assets import (
    allocate_initial_or_addition,
    create_audit_session,
    create_serialized_unit,
    report_damage_or_disposal,
    update_unit_repair_status,
)
from purchases.urls_assets_audit import assets_audit_urlpatterns
from syntel.urls import urlpatterns as base_root_urlpatterns


User = get_user_model()

# اگر purchases/urls.py هنوز assets_audit_urlpatterns را include نکرده باشد،
# در تست‌ها موقتاً مسیرهای انبارگردانی و گزارش اموال را روی URLConf تست سوار می‌کنیم.
try:
    reverse('asset_audit_list', kwargs={'cafe_code': 'probe'})
    _NEEDS_URL_OVERRIDE = False
except NoReverseMatch:
    _NEEDS_URL_OVERRIDE = True

urlpatterns = list(base_root_urlpatterns) + [
    path('<str:cafe_code>/', include(assets_audit_urlpatterns)),
]


def _make_test_image(name: str = 'evidence.png') -> SimpleUploadedFile:
    buf = io.BytesIO()
    img = Image.new('RGB', (40, 40), color=(220, 40, 60))
    img.save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


class AssetsAuditAndReportsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if _NEEDS_URL_OVERRIDE:
            cls._urlconf_cm = override_settings(ROOT_URLCONF=__name__)
            cls._urlconf_cm.__enter__()
        else:
            cls._urlconf_cm = None

    @classmethod
    def tearDownClass(cls):
        if cls._urlconf_cm is not None:
            cls._urlconf_cm.__exit__(None, None, None)
        super().tearDownClass()

    def setUp(self):
        self.cafe = Cafe.objects.create(name='کافه تست اموال', code='asset_cafe', is_active=True)
        self.other_cafe = Cafe.objects.create(name='کافه دیگر', code='other_cafe', is_active=True)

        self.dept_kitchen = Department.objects.create(name='آشپزخانه', cafe=self.cafe, is_active=True)
        self.dept_bar = Department.objects.create(name='بار', cafe=self.cafe, is_active=True)
        self.other_dept = Department.objects.create(name='سالن', cafe=self.other_cafe, is_active=True)

        self.manager = User.objects.create_user(
            username='asset_mgr', password='pw', role='manager', cafe=self.cafe
        )
        self.other_manager = User.objects.create_user(
            username='other_mgr', password='pw', role='manager', cafe=self.other_cafe
        )
        self.req_kitchen = User.objects.create_user(
            username='req_kitchen',
            password='pw',
            role='requester',
            cafe=self.cafe,
            department=self.dept_kitchen,
        )
        self.req_bar = User.objects.create_user(
            username='req_bar',
            password='pw',
            role='requester',
            cafe=self.cafe,
            department=self.dept_bar,
        )

        self.cat_dishes = AssetCategory.objects.create(cafe=self.cafe, name='ظروف')
        self.cat_equip = AssetCategory.objects.create(cafe=self.cafe, name='تجهیزات برقی')

        self.plate = AssetCatalog.objects.create(
            cafe=self.cafe,
            category=self.cat_dishes,
            name='بشقاب سرامیکی',
            code='PLT-01',
            tracking_mode='quantity',
            estimated_unit_value=Decimal('250000'),
        )
        self.glass = AssetCatalog.objects.create(
            cafe=self.cafe,
            category=self.cat_dishes,
            name='لیوان دوجداره',
            code='GLS-01',
            tracking_mode='quantity',
            estimated_unit_value=Decimal('180000'),
        )
        self.espresso_machine = AssetCatalog.objects.create(
            cafe=self.cafe,
            category=self.cat_equip,
            name='اسپرسوساز صنعتی',
            code='ESP-01',
            tracking_mode='serialized',
            estimated_unit_value=Decimal('120000000'),
        )

    def _url(self, name: str, cafe_code: str | None = None, **kwargs) -> str:
        kwargs['cafe_code'] = cafe_code or self.cafe.code
        return reverse(name, kwargs=kwargs)

    def test_blind_count_hides_expected_quantities_from_html(self):
        """در حالت شمارش کور، موجودی سیستم نباید در HTML شمارش و کارت HTMX افشا شود."""
        DepartmentAssetStock.objects.create(
            cafe=self.cafe,
            department=self.dept_kitchen,
            asset=self.plate,
            quantity_healthy=73941,
            quantity_damaged=19827,
        )
        unit = create_serialized_unit(
            cafe=self.cafe,
            department=self.dept_kitchen,
            asset=self.espresso_machine,
            asset_tag='TAG-BLIND-01',
            user=self.manager,
            purchase_price=Decimal('95000000'),
        )
        audit = create_audit_session(
            cafe=self.cafe,
            department=self.dept_kitchen,
            title='شمارش کور تست',
            user=self.manager,
            audit_type='full',
            blind_count=True,
        )
        q_item = audit.items.get(asset=self.plate)
        self.assertEqual(q_item.expected_healthy, 73941)
        self.assertEqual(q_item.expected_damaged, 19827)

        client = Client()
        client.force_login(self.req_kitchen)

        resp = client.get(self._url('asset_audit_count', audit_id=audit.id))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('شمارش کور فعال', html)
        self.assertNotIn('73941', html)
        self.assertNotIn('19827', html)
        self.assertNotIn('موجودی سیستم', html)
        self.assertNotIn('وضعیت ثبت‌شده در سیستم', html)
        self.assertIn(unit.asset_tag, html)

        # ذخیره HTMX هم نباید مقادیر سیستم را برگرداند
        save_resp = client.post(
            self._url('asset_audit_save_item', audit_id=audit.id, item_id=q_item.id),
            {'counted_healthy': '5', 'counted_damaged': '1', 'note': 'شمارش اولیه'},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(save_resp.status_code, 200)
        card_html = save_resp.content.decode('utf-8')
        self.assertIn('ذخیره شد', card_html)
        self.assertNotIn('73941', card_html)
        self.assertNotIn('19827', card_html)
        self.assertNotIn('موجودی سیستم', card_html)

    def test_htmx_save_item_and_add_unexpected_with_photo(self):
        """ذخیره HTMX با اعداد فارسی و عکس مدرک + افزودن کالای خارج از لیست."""
        allocate_initial_or_addition(
            cafe=self.cafe,
            department=self.dept_kitchen,
            asset=self.plate,
            quantity=12,
            user=self.manager,
        )
        audit = create_audit_session(
            cafe=self.cafe,
            department=self.dept_kitchen,
            title='شمارش آشپزخانه',
            user=self.manager,
            blind_count=False,
        )
        item = audit.items.get(asset=self.plate)

        client = Client()
        client.force_login(self.req_kitchen)

        # در حالت غیرکور موجودی سیستم نمایش داده می‌شود
        page_resp = client.get(self._url('asset_audit_count', audit_id=audit.id))
        self.assertEqual(page_resp.status_code, 200)
        self.assertIn('موجودی سیستم', page_resp.content.decode('utf-8'))

        # ذخیره HTMX با ارقام فارسی و عکس مدرک
        photo = _make_test_image('broken_plate.png')
        save_resp = client.post(
            self._url('asset_audit_save_item', audit_id=audit.id, item_id=item.id),
            {
                'counted_healthy': '۱۰',
                'counted_damaged': '۲',
                'note': '۲ بشقاب لب‌پر شده',
                'evidence_photo': photo,
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(save_resp.status_code, 200)
        self.assertIn('ذخیره شد', save_resp.content.decode('utf-8'))

        item.refresh_from_db()
        self.assertTrue(item.is_counted)
        self.assertEqual(item.counted_healthy, 10)
        self.assertEqual(item.counted_damaged, 2)
        self.assertTrue(bool(item.evidence_photo))

        # ثبت کالای یافت‌شده خارج از لیست (لیوان در لیست اولیه نبود)
        unexp_resp = client.post(
            self._url('asset_audit_add_unexpected', audit_id=audit.id),
            {
                'asset_id': str(self.glass.id),
                'counted_healthy': '4',
                'counted_damaged': '1',
                'note': 'پیدا شد داخل کابینت',
            },
        )
        self.assertEqual(unexp_resp.status_code, 302)
        unexp_item = AssetAuditItem.objects.get(audit=audit, asset=self.glass)
        self.assertTrue(unexp_item.is_unexpected_item)
        self.assertEqual(unexp_item.expected_healthy, 0)
        self.assertEqual(unexp_item.counted_healthy, 4)
        self.assertEqual(unexp_item.counted_damaged, 1)

    def test_requester_cannot_access_other_department_or_other_cafe_audit(self):
        """جلوگیری از دسترسی requester به انبارگردانی سکشن دیگر یا کافه دیگر و عملیات مدیر."""
        allocate_initial_or_addition(
            cafe=self.cafe,
            department=self.dept_kitchen,
            asset=self.plate,
            quantity=10,
            user=self.manager,
        )
        kitchen_audit = create_audit_session(
            cafe=self.cafe,
            department=self.dept_kitchen,
            title='انبارگردانی آشپزخانه',
            user=self.manager,
        )
        kitchen_item = kitchen_audit.items.first()

        # کاربر بار نباید به انبارگردانی آشپزخانه دسترسی داشته باشد
        bar_client = Client()
        bar_client.force_login(self.req_bar)

        self.assertEqual(
            bar_client.get(self._url('asset_audit_count', audit_id=kitchen_audit.id)).status_code,
            403,
        )
        self.assertEqual(
            bar_client.post(
                self._url('asset_audit_save_item', audit_id=kitchen_audit.id, item_id=kitchen_item.id),
                {'counted_healthy': '1', 'counted_damaged': '0'},
            ).status_code,
            403,
        )
        self.assertEqual(
            bar_client.post(
                self._url('asset_audit_add_unexpected', audit_id=kitchen_audit.id),
                {'asset_id': str(self.glass.id), 'counted_healthy': '1'},
            ).status_code,
            403,
        )
        self.assertEqual(
            bar_client.post(self._url('asset_audit_submit', audit_id=kitchen_audit.id)).status_code,
            403,
        )

        # لیست انبارگردانی برای کاربر بار نباید جلسه آشپزخانه را نشان دهد
        list_resp = bar_client.get(self._url('asset_audit_list'))
        self.assertEqual(list_resp.status_code, 200)
        self.assertNotIn('انبارگردانی آشپزخانه', list_resp.content.decode('utf-8'))

        # requester مجاز به ایجاد، بررسی، بازشماری یا تأیید نهایی نیست
        kitchen_client = Client()
        kitchen_client.force_login(self.req_kitchen)
        self.assertEqual(kitchen_client.get(self._url('asset_audit_create')).status_code, 403)
        self.assertEqual(
            kitchen_client.get(self._url('asset_audit_review', audit_id=kitchen_audit.id)).status_code,
            403,
        )
        self.assertEqual(
            kitchen_client.post(self._url('asset_audit_recount', audit_id=kitchen_audit.id)).status_code,
            403,
        )
        self.assertEqual(
            kitchen_client.post(self._url('asset_audit_approve', audit_id=kitchen_audit.id)).status_code,
            403,
        )

        # ایزوله‌سازی بین کافه‌ها
        other_client = Client()
        other_client.force_login(self.other_manager)
        self.assertEqual(
            other_client.get(
                self._url('asset_audit_review', cafe_code=self.other_cafe.code, audit_id=kitchen_audit.id)
            ).status_code,
            404,
        )

    def test_full_audit_lifecycle_recount_and_approval_adjustments(self):
        """تست کامل چرخه submitted -> recount_requested -> submitted -> approved و تعدیل خودکار موجودی."""
        allocate_initial_or_addition(
            cafe=self.cafe,
            department=self.dept_bar,
            asset=self.glass,
            quantity=10,
            user=self.manager,
        )
        unit = create_serialized_unit(
            cafe=self.cafe,
            department=self.dept_bar,
            asset=self.espresso_machine,
            asset_tag='ESP-BAR-01',
            user=self.manager,
            purchase_price=Decimal('100000000'),
        )

        mgr_client = Client()
        mgr_client.force_login(self.manager)

        # ایجاد انبارگردانی از طریق ویوی مدیر
        create_resp = mgr_client.post(
            self._url('asset_audit_create'),
            {
                'department_id': str(self.dept_bar.id),
                'title': 'انبارگردانی ماهانه بار',
                'audit_type': 'full',
                'blind_count': '1',
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        audit = AssetAudit.objects.get(cafe=self.cafe, title='انبارگردانی ماهانه بار')
        q_item = audit.items.get(asset=self.glass)
        s_item = audit.items.get(asset_unit=unit)

        req_client = Client()
        req_client.force_login(self.req_bar)

        # شمارش اولیه توسط کاربر بار
        req_client.post(
            self._url('asset_audit_save_item', audit_id=audit.id, item_id=q_item.id),
            {'counted_healthy': '8', 'counted_damaged': '1'},
        )
        req_client.post(
            self._url('asset_audit_save_item', audit_id=audit.id, item_id=s_item.id),
            {'counted_status': 'in_repair', 'note': 'ارسال‌شده به تعمیرگاه'},
        )
        req_client.post(self._url('asset_audit_submit', audit_id=audit.id))
        audit.refresh_from_db()
        self.assertEqual(audit.status, 'submitted')

        # بررسی مدیر و ارجاع فقط ردیف لیوان برای بازشماری
        review_resp = mgr_client.get(self._url('asset_audit_review', audit_id=audit.id))
        self.assertEqual(review_resp.status_code, 200)
        self.assertIn('ESP-BAR-01', review_resp.content.decode('utf-8'))

        mgr_client.post(
            self._url('asset_audit_recount', audit_id=audit.id),
            {'item_ids': [str(q_item.id)], 'review_notes': 'لیوان‌ها را دوباره بشمارید'},
        )
        audit.refresh_from_db()
        q_item.refresh_from_db()
        s_item.refresh_from_db()
        self.assertEqual(audit.status, 'recount_requested')
        self.assertTrue(q_item.needs_recount)
        self.assertFalse(s_item.needs_recount)

        # بازشماری توسط کاربر بار و ارسال مجدد
        req_client.post(
            self._url('asset_audit_save_item', audit_id=audit.id, item_id=q_item.id),
            {'counted_healthy': '7', 'counted_damaged': '1', 'note': 'تایید کسری ۳ عدد'},
        )
        req_client.post(self._url('asset_audit_submit', audit_id=audit.id))
        audit.refresh_from_db()
        q_item.refresh_from_db()
        self.assertEqual(audit.status, 'submitted')
        self.assertFalse(q_item.needs_recount)

        # تأیید نهایی توسط مدیر و بررسی تعدیل خودکار موجودی و ثبت AssetMovement
        approve_resp = mgr_client.post(
            self._url('asset_audit_approve', audit_id=audit.id),
            {'review_notes': 'تأیید نهایی شد'},
        )
        self.assertEqual(approve_resp.status_code, 302)
        audit.refresh_from_db()
        self.assertEqual(audit.status, 'approved')

        glass_stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.glass)
        self.assertEqual(glass_stock.quantity_healthy, 7)
        self.assertEqual(glass_stock.quantity_damaged, 1)

        unit.refresh_from_db()
        self.assertEqual(unit.status, 'in_repair')
        machine_stock = DepartmentAssetStock.objects.get(department=self.dept_bar, asset=self.espresso_machine)
        self.assertEqual(machine_stock.quantity_healthy, 0)
        self.assertEqual(machine_stock.quantity_damaged, 1)

        adj_movements = AssetMovement.objects.filter(audit=audit, movement_type='audit_adjustment', status='approved')
        self.assertEqual(adj_movements.count(), 3)

    def test_all_six_reports_and_excel_pdf_exports(self):
        """تست HTTP 200، رندر صحیح، Content-Type اکسل و PDF برای هر ۶ گزارش و داشبورد."""
        today = timezone.localdate()

        # ۱. موجودی با کسری استاندارد برای گزارش ۱ و ۴
        allocate_initial_or_addition(
            cafe=self.cafe,
            department=self.dept_kitchen,
            asset=self.plate,
            quantity=10,
            min_expected_quantity=20,
            user=self.manager,
        )
        # ۲. ثبت شکستگی برای گزارش ۳ و ۶
        report_damage_or_disposal(
            cafe=self.cafe,
            department=self.dept_kitchen,
            asset=self.plate,
            quantity=2,
            user=self.manager,
            reason='شکستگی حین شستشو',
            auto_approve=True,
        )
        # ۳. پلاک‌های دارای وضعیت تعمیر و گارانتی برای گزارش ۵
        u1 = create_serialized_unit(
            cafe=self.cafe,
            department=self.dept_bar,
            asset=self.espresso_machine,
            asset_tag='TAG-REP-1',
            user=self.manager,
            warranty_until=today + timedelta(days=10),
        )
        update_unit_repair_status(u1, 'needs_repair', self.manager, reason='نشتی بخار')

        create_serialized_unit(
            cafe=self.cafe,
            department=self.dept_bar,
            asset=self.espresso_machine,
            asset_tag='TAG-EXP-1',
            user=self.manager,
            warranty_until=today - timedelta(days=5),
        )

        # ۴. یک انبارگردانی با مغایرت برای گزارش ۲
        audit = create_audit_session(
            cafe=self.cafe,
            department=self.dept_kitchen,
            title='انبارگردانی گزارش',
            user=self.manager,
        )
        it = audit.items.get(asset=self.plate)
        it.counted_healthy = 6
        it.counted_damaged = 0
        it.is_counted = True
        it.save()
        audit.status = 'submitted'
        audit.save()

        mgr_client = Client()
        mgr_client.force_login(self.manager)

        # دسترسی requester به گزارش‌های مدیریتی مسدود است
        req_client = Client()
        req_client.force_login(self.req_kitchen)
        self.assertEqual(req_client.get(self._url('asset_reports_dashboard')).status_code, 403)
        self.assertEqual(req_client.get(self._url('asset_report_valuation')).status_code, 403)

        # داشبورد و ۶ گزارش HTML
        report_urls = [
            self._url('asset_reports_dashboard'),
            self._url('asset_report_valuation'),
            self._url('asset_report_variance'),
            self._url('asset_report_breakage') + '?months=3',
            self._url('asset_report_breakage') + '?months=6',
            self._url('asset_report_breakage') + '?months=12',
            self._url('asset_report_shortage'),
            self._url('asset_report_repairs') + '?tab=needs_repair',
            self._url('asset_report_repairs') + '?tab=in_repair',
            self._url('asset_report_repairs') + '?tab=expiring_warranty',
            self._url('asset_report_repairs') + '?tab=expired_warranty',
            self._url('asset_report_kardex'),
        ]
        for url in report_urls:
            resp = mgr_client.get(url)
            self.assertEqual(resp.status_code, 200, msg=f'Failed on URL: {url}')

        # بررسی محتوای گزارش‌های کلیدی
        shortage_html = mgr_client.get(self._url('asset_report_shortage')).content.decode('utf-8')
        self.assertIn('بشقاب سرامیکی', shortage_html)

        repairs_html = mgr_client.get(self._url('asset_report_repairs') + '?tab=needs_repair').content.decode('utf-8')
        self.assertIn('TAG-REP-1', repairs_html)

        expiring_html = mgr_client.get(self._url('asset_report_repairs') + '?tab=expiring_warranty').content.decode('utf-8')
        self.assertIn('TAG-REP-1', expiring_html)

        expired_html = mgr_client.get(self._url('asset_report_repairs') + '?tab=expired_warranty').content.decode('utf-8')
        self.assertIn('TAG-EXP-1', expired_html)

        # خروجی‌های Excel
        excel_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        for name, kwargs in [
            ('asset_report_valuation_export', {'fmt': 'excel'}),
            ('asset_report_variance_export', {'fmt': 'excel'}),
            ('asset_report_kardex_export', {}),
        ]:
            resp = mgr_client.get(self._url(name, **kwargs))
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp['Content-Type'], excel_type)
            wb = openpyxl.load_workbook(io.BytesIO(resp.content))
            self.assertGreaterEqual(len(wb.sheetnames), 1)
            self.assertGreaterEqual(wb.active.max_row, 2)

        # خروجی‌های PDF
        for name in ('asset_report_valuation_export', 'asset_report_variance_export'):
            resp = mgr_client.get(self._url(name, fmt='pdf'))
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp['Content-Type'], 'application/pdf')
            self.assertTrue(resp.content.startswith(b'%PDF'))

        # فرمت نامعتبر
        bad_resp = mgr_client.get(self._url('asset_report_valuation_export', fmt='csv'))
        self.assertEqual(bad_resp.status_code, 400)
