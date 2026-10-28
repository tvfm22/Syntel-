"""تست‌های بهبود رابط کاربری بخش «حواله‌های انبار»:

- فهرست «موجودی انبار» در فرم ایجاد، بدون انتخاب انبار هم پر است (قبلاً خالی بود)
- خطای اعتبارسنجی، اقلام/توضیحات/تاریخِ واردشده را پاک نمی‌کند
- باکس‌های تاریخ شمسی (مثل «ثبت فاکتور نقدی») در ایجاد/ویرایش/فیلترها
- تاریخ فرم ایجاد پیش‌فرض «امروزِ شمسی» است و POST شمسی درست تبدیل می‌شود
- بخش «حواله‌های انبار» منو فقط لینک‌های حواله را دارد (سوابق حرکت/مدیریت انبارها
  به بخش «انبار» منتقل شده‌اند)
- فیلترهای کم‌کاربرد لیست داخل «فیلترهای بیشتر» جمع شده‌اند
"""
import re
from datetime import datetime

import jdatetime
from django.contrib.auth import get_user_model
from django.utils import timezone

from .test_voucher_ui import VoucherUIBaseline

User = get_user_model()


class VoucherCreateUXTests(VoucherUIBaseline):

    def test_stock_options_present_without_preselected_warehouse(self):
        r = self.client.get(self._url('voucher_create', voucher_type='receipt'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        # هر دو موجودی (انبار مرکزی و انبار بخش) در فهرست هستند
        self.assertIn('sid: %d' % self.stock_a.id, html)
        self.assertIn('sid: %d' % self.stock_b.id, html)

    def test_date_field_is_prefilled_with_today_jalali(self):
        r = self.client.get(self._url('voucher_create', voucher_type='receipt'))
        html = r.content.decode('utf-8')
        m = re.search(r'name="date"[^>]*value="([^"]*)"', html)
        self.assertIsNotNone(m)
        self.assertRegex(m.group(1), r'^1[34]\d{2}/\d{2}/\d{2}$')
        today = jdatetime.date.fromgregorian(date=timezone.localdate())
        self.assertEqual(m.group(1), today.strftime('%Y/%m/%d'))

    def test_date_box_is_the_cash_invoice_jalali_component(self):
        r = self.client.get(self._url('voucher_create', voucher_type='receipt'))
        html = r.content.decode('utf-8')
        # سه فیلد سال/ماه/روز + ورودی مخفی، دقیقاً مثل فاکتور نقدی
        self.assertIn('class="date-input-group"', html)
        self.assertIn('placeholder="سال"', html)
        self.assertIn('placeholder="ماه"', html)
        self.assertIn('placeholder="روز"', html)
        self.assertIn("jalali_date_group.js", html)
        self.assertIn("jalali_date_group.css", html)
        self.assertNotIn('type="datetime-local"', html)

    def test_validation_error_preserves_entered_items(self):
        r = self.client.post(self._url('voucher_create', voucher_type='receipt'), {
            'action': 'submit',
            'product_id[]': [str(self.product.id), ''],
            'product_brand_id[]': ['', ''],
            'stock_item_id[]': ['', ''],
            'unit_id[]': [str(self.unit.id), ''],
            'base_quantity[]': ['0', ''],
            'unit_price[]': ['1000', ''],
            'item_description[]': ['قلم نگه‌داشته‌شده', ''],
            'date': '1405/07/28',
            'description': 'شرح کلی آزمون',
            'dest_warehouse': str(self.wh_a.id),
        })
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('مقدار پایه نباید صفر باشد', html)
        self.assertIn('product_name: "Sugar"', html)
        self.assertIn('base: "0"', html)
        self.assertIn('قلم نگه‌داشته‌شده', html)
        self.assertIn('شرح کلی آزمون', html)
        self.assertIn('value="1405/07/28"', html)

    def test_invalid_jalali_date_shows_error_and_preserves_it(self):
        r = self.client.post(self._url('voucher_create', voucher_type='receipt'), {
            'action': 'submit',
            'product_id[]': [str(self.product.id)],
            'product_brand_id[]': [''],
            'stock_item_id[]': [''],
            'unit_id[]': [str(self.unit.id)],
            'base_quantity[]': ['5'],
            'unit_price[]': ['1000'],
            'date': '1405/13/40',
            'dest_warehouse': str(self.wh_a.id),
        })
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('تاریخ واردشده معتبر نیست', html)
        self.assertIn('value="1405/13/40"', html)


class VoucherJalaliDateTests(VoucherUIBaseline):
    """تبدیل درست تاریخ شمسیِ فرم به میلادی و فیلتر بر همان اساس"""

    def _latest_voucher(self):
        from purchases.models import WarehouseVoucher
        return WarehouseVoucher.objects.filter(cafe=self.cafe).latest('id')

    def test_create_with_jalali_date_stores_gregorian_date(self):
        r = self._create_post('receipt', dest=self.wh_a, qty='5',
                              date='1405/07/28')
        self.assertEqual(r.status_code, 302)
        v = self._latest_voucher()
        expected = jdatetime.date(1405, 7, 28).togregorian()
        self.assertEqual(timezone.localtime(v.date).date(), expected)

    def test_edit_page_shows_jalali_date_of_draft(self):
        r = self._create_post('receipt', dest=self.wh_a, qty='5',
                              date='1405/07/28', action='draft')
        self.assertEqual(r.status_code, 302)
        v = self._latest_voucher()
        r = self.client.get(self._url('voucher_edit', voucher_id=v.id))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        m = re.search(r'name="date"[^>]*value="([^"]*)"', html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), '1405/07/28')

    def test_list_filter_accepts_jalali_dates(self):
        from purchases.models import WarehouseVoucher
        self._create_post('receipt', dest=self.wh_a, qty='5', date='1405/07/28')
        v = self._latest_voucher()
        other = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='receipt', status='draft',
            voucher_number='RCV-OTHER', destination_warehouse=self.wh_a,
            date=timezone.make_aware(datetime(2025, 1, 1, 10, 0)),
            created_by=self.user)

        # بازهٔ شمسیِ همان روز
        r = self.client.get(self._url('voucher_list'), {
            'date_from': '1405/07/28', 'date_to': '1405/07/28'})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn(v.voucher_number, html)
        self.assertNotIn(other.voucher_number, html)
        # مقدار شمسی در باکس تاریخ حفظ شده است
        self.assertIn('value="1405/07/28"', html)

    def test_list_filters_render_jalali_boxes(self):
        r = self.client.get(self._url('voucher_list'))
        html = r.content.decode('utf-8')
        self.assertIn('name="date_from"', html)
        self.assertIn('name="date_to"', html)
        self.assertNotIn('type="date"', html)
        self.assertIn("jalali_date_group.js", html)

    def test_stock_movements_filters_render_jalali_boxes(self):
        r = self.client.get(self._url('stock_movements'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('name="date_from"', html)
        self.assertIn('name="date_to"', html)
        self.assertNotIn('type="date"', html)
        self.assertIn("jalali_date_group.js", html)

    def test_stock_movements_filter_accepts_jalali_date(self):
        self._create_post('receipt', dest=self.wh_a, qty='5',
                          date='1405/07/28')
        today = jdatetime.date.fromgregorian(
            date=timezone.localdate()).strftime('%Y/%m/%d')
        r = self.client.get(self._url('stock_movements'), {
            'date_from': today, 'date_to': today})
        self.assertContains(r, self.product.name)
        # بازهٔ گذشته → حرکتی نیست
        r = self.client.get(self._url('stock_movements'), {
            'date_from': '1404/01/01', 'date_to': '1404/01/02'})
        self.assertContains(r, 'حرکتی یافت نشد')


class VoucherNavigationUXTests(VoucherUIBaseline):

    def setUp(self):
        super().setUp()
        self.officer = User.objects.create_user(
            username='officer_nav', password='testpass123',
            role='warehouse', cafe=self.cafe)
        self.client.force_login(self.officer)

    def _voucher_section(self, html):
        marker = '<div class="nav-section-title">حواله‌های انبار</div>'
        self.assertIn(marker, html)
        after = html.split(marker, 1)[1]
        return after.split('</nav>', 1)[0]

    def test_voucher_menu_section_contains_only_voucher_links(self):
        r = self.client.get(self._url('voucher_list'))
        self.assertEqual(r.status_code, 200)
        section = self._voucher_section(r.content.decode('utf-8'))
        self.assertEqual(section.count('class="nav-item'), 2)
        self.assertIn('لیست حواله‌ها', section)
        self.assertIn('ثبت حواله', section)
        self.assertIn('/vouchers/', section)
        self.assertIn('/vouchers/create/receipt/', section)
        self.assertNotIn('سوابق حرکت', section)
        self.assertNotIn('مدیریت انبارها', section)
        self.assertNotIn('/stock/movements/', section)
        self.assertNotIn('href="/main/warehouses/"', section)

    def test_active_state_on_voucher_detail(self):
        self._create_post('receipt', dest=self.wh_a, qty='5')
        v = self._voucher_model()
        r = self.client.get(self._url('voucher_detail', voucher_id=v.id))
        self.assertEqual(r.status_code, 200)
        section = self._voucher_section(r.content.decode('utf-8'))
        self.assertIn('nav-item active', section)

    def _voucher_model(self):
        from purchases.models import WarehouseVoucher
        return WarehouseVoucher.objects.filter(cafe=self.cafe).latest('id')


class VoucherListUXTests(VoucherUIBaseline):

    def test_advanced_filters_are_collapsed(self):
        r = self.client.get(self._url('voucher_list'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('filter-more', html)
        self.assertIn('فیلترهای بیشتر', html)

    def test_duplicate_header_buttons_removed(self):
        r = self.client.get(self._url('voucher_list'))
        html = r.content.decode('utf-8')
        self.assertNotIn('سوابق حرکت موجودی', html)

    def test_advanced_filters_open_when_active(self):
        r = self.client.get(self._url('voucher_list'), {'product': self.product.id})
        html = r.content.decode('utf-8')
        self.assertIn('class="filter-more" open', html)
        self.assertIn('active-dot', html)
