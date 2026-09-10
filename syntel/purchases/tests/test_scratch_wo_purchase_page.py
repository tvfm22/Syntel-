"""سنگ‌آزمایش: صفحه «درخواست خرید» مسئول انبار — جدول اقلام + ستون «زمان درخواستی تحویل»

۱. فرم «افزودن کالای جدید» بالای صفحه (قبل از جدول اقلام) باشد
۲. جدول اقلام ستون‌های کالا/برند/ماندگاری/موجودی/…/زمان درخواستی تحویل دارد
۳. ردیفِ ارجاع‌شده از درخواست کاربر: ستون تحویل، delivery_date همان درخواستِ اصلی
   را نشان می‌دهد — حتی اگر آن تاریخ گذشته باشد؛ ردیفِ دستی: «—»
۴. ارسال هنوز مثل قبل از نوار «زمان تحویل» (تاریخ آینده) انجام می‌شود
"""
import datetime

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Unit, Product, ProductBrand, PurchaseDraft, PurchaseDraftItem,
    PurchaseItem, PurchaseRequest,
    User, Warehouse, StockItem,
)
from purchases.templatetags.custom_filters import jalali_date


class WarehousePurchaseDraftPageTest(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code='pd', name='Purchase Draft Cafe', is_active=True)
        self.unit = Unit.objects.create(name='کیلوگرم', symbol='kg')
        self.officer = User.objects.create_user(
            username='wh_pd', password='x', role='warehouse', cafe=self.cafe)

        # محصولی که هنگام تعریفش عمر مفید (ماندگاری) ثبت شده + برند دارای موجودی
        self.product = Product.objects.create(
            name='شیر', cafe=self.cafe, unit=self.unit,
            shelf_life_days=180, is_active=True, is_complete=True)
        self.brand = ProductBrand.objects.create(
            product=self.product, brand_name='پگاه', trade_unit='بطری', conversion_rate=1)
        wh = Warehouse.objects.create(name='انبار اصلی', code='PD-WH1', cafe=self.cafe)
        self.stock = StockItem.objects.create(
            product=self.product, product_brand=self.brand,
            warehouse=wh, quantity=25, unit=self.unit)

        self.url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})

        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session.save()
        self.client.force_login(self.officer)

    def _add_item(self, brand_id):
        return self.client.post(self.url, data={
            'action': 'add_item',
            'product_id': str(self.product.id),
            'brand_id': str(brand_id or ''),
            'quantity': '3',
            'unit': 'کیلوگرم',
            'description': '',
            'new_name': '',
        })

    def _delivery_cell(self, content):
        """محتوای اولین خانهٔ «زمان درخواستی تحویل» (بین class=cell-delivery و </td>)"""
        head = content.find('class="cell-delivery"')
        self.assertGreater(head, -1, 'خانهٔ زمان درخواستی تحویل باید وجود داشته باشد')
        tail = content.find('</td>', head)
        return content[head:tail]

    def test_add_form_on_top_then_table_with_headers(self):
        # افزودن کالا → رفتن به ردیف پیش‌نویس
        resp = self._add_item(self.brand.id)
        self.assertEqual(resp.status_code, 302)
        draft = PurchaseDraft.objects.filter(cafe=self.cafe, created_by=self.officer).first()
        self.assertIsNotNone(draft)
        self.assertEqual(draft.items.count(), 1)

        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        content = page.content.decode('utf-8')

        # فرم «افزودن کالای جدید» قبل از جدول اقلام باشد.
        # مکان‌یابی با نشانگرهای markup انجام می‌شود نه متنِ فارسی: متنِ عنوان‌ها در
        # کامنت/سلکتورهای CSS داخل <style> هم تکرار می‌شود و find() اولی را می‌گیرد.
        self.assertIn('افزودن کالای جدید', content)
        self.assertIn('اقلام آماده ارسال', content)
        self.assertLess(content.find('class="add-form-row"'),
                        content.find('class="officer-table items-table"'),
                        'فرم افزودن باید بالای فهرست اقلام باشد')
        # فرم پستی در بخش پایینِ لیست دیگر تکرار نشده باشد (فقط یک id)
        self.assertEqual(content.count('id="product-select"'), 1,
                         'فرم افزودن کالا فقط یک‌بار (بالای صفحه) باشد')

        # ستون‌های جدول: ماندگاری + موجودی + «زمان درخواستی تحویل» (نه «تحویل»)
        self.assertIn('>زمان درخواستی تحویل<', content)
        self.assertIn('>ماندگاری<', content)
        self.assertIn('180 روز', content)
        self.assertIn('25 بطری', content)
        self.assertNotIn('ارجاعی', content)

        # ردیفِ دستی (بدون درخواست منبع): خانهٔ تحویل «—» است
        cell = self._delivery_cell(content)
        self.assertIn('—', cell)

    def test_referred_item_shows_requested_delivery_even_if_past(self):
        """ردیفِ ارجاع‌شده از درخواست کاربر: تاریخ درخواستیِ اصلی (حتی گذشته) نمایش داده شود"""
        requester = User.objects.create_user(
            username='dept_user', password='x', role='user', cafe=self.cafe)
        # تاریخ درخواستیِ گذشته (مثلاً یک ماه قبل)
        past_date = (timezone.localtime(timezone.now()).date() - datetime.timedelta(days=30))
        src_req = PurchaseRequest.objects.create(
            user=requester, cafe=self.cafe,
            status='pending_manager_approval', source='requester',
            delivery_date=past_date)
        src_item = PurchaseItem.objects.create(
            request=src_req, product=self.product, name='شیر',
            quantity=3, unit='کیلوگرم')

        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.officer)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, product_brand=self.brand,
            name='شیر', quantity=3, unit='کیلوگرم',
            source_item=src_item)

        content = self.client.get(self.url).content.decode('utf-8')
        cell = self._delivery_cell(content)
        # همان تاریخ درخواستِ اصلی (گذشته) نمایش داده شده — نه انتخاب نوار ارسال
        self.assertIn(jalali_date(past_date), cell)
        self.assertNotIn('فوری', cell)

    def test_free_name_item_still_renders_row(self):
        """قلم با نام آزاد (بدون محصول): سطر هست، خانه‌های ماندگاری/موجودی/تحویل «—»"""
        resp = self.client.post(self.url, data={
            'action': 'add_item',
            'product_id': '',
            'brand_id': '',
            'new_name': 'دستمال کاغذی',
            'quantity': '2',
            'unit': 'بسته',
            'description': '',
        })
        self.assertEqual(resp.status_code, 302)
        content = self.client.get(self.url).content.decode('utf-8')
        self.assertIn('دستمال کاغذی', content)
        cell = self._delivery_cell(content)
        self.assertIn('—', cell)
        # نوار ارسالِ پیش‌نویس سر جایش است (انتخابگر زمان تحویل در جریان جدیدِ
        # «ارسال به مدیرعامل» عمداً برداشته شده — درخواست خریدِ انبار تاریخ تحویل ندارد)
        self.assertIn('name="action" value="send"', content)
        self.assertNotIn('syncRowDelivery', content)
