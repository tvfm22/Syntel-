"""سنگ‌آزمایش: صفحه «درخواست خرید» مسئول انبار — چیدمان جدید پیش‌نویس

۱. فرم «افزودن کالای جدید» بالای صفحه (قبل از فهرست اقلام) باشد
۲. هر قلم: ماندگاری (تعریف‌شده روی محصول) + موجودی انبار + چیپ زمان تحویل داشته باشد
۳. تگ «ارجاعی» دیگر نمایش داده نشود
۴. چیپ زمان تحویل ردیف‌ها با انتخابِ نوار ارسال همگام است (متن اولیه = همان گزینه اول)
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Unit, Product, ProductBrand, PurchaseDraft,
    User, Warehouse, StockItem,
)


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

    def test_add_form_on_top_then_draft_rows_with_info(self):
        # افزودن کالا → رفتن به ردیف پیش‌نویس
        resp = self._add_item(self.brand.id)
        self.assertEqual(resp.status_code, 302)
        draft = PurchaseDraft.objects.filter(cafe=self.cafe, created_by=self.officer).first()
        self.assertIsNotNone(draft)
        self.assertEqual(draft.items.count(), 1)

        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        content = page.content.decode('utf-8')

        # فرم «افزودن کالای جدید» قبل از کارت «اقلام آماده ارسال» باشد
        self.assertIn('افزودن کالای جدید', content)
        self.assertIn('اقلام آماده ارسال', content)
        self.assertLess(content.find('افزودن کالای جدید'),
                        content.find('اقلام آماده ارسال'),
                        'فرم افزودن باید بالای فهرست اقلام باشد')
        # فرم پستی در بخش پایینِ لیست دیگر تکرار نشده باشد (فقط یک id)
        self.assertEqual(content.count('id="product-select"'), 1,
                         'فرم افزودن کالا فقط یک‌بار (بالای صفحه) باشد')

        # قلم پیش‌نویس: ماندگاری + موجودی + چیپ زمان تحویل
        self.assertIn('ماندگاری: 180 روز', content)
        self.assertIn('موجودی: 25 بطری', content)
        self.assertIn('class="delivery-chip row-delivery"', content)
        self.assertIn('row-delivery-text', content)

        # تگ «ارجاعی» حذف شده باشد
        self.assertNotIn('ارجاعی', content)

    def test_free_name_item_still_shows_delivery_chip(self):
        """قلم با نام آزاد (بدون محصول): چیپ تحویل هست ولی ماندگاری/موجودی نیست"""
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
        page = self.client.get(self.url)
        content = page.content.decode('utf-8')
        self.assertIn('دستمال کاغذی', content)
        self.assertIn('row-delivery', content)
        self.assertNotIn('ماندگاری:', content)
        self.assertNotIn('موجودی:', content)
        self.assertNotIn('ارجاعی', content)

    def test_delivery_chip_matches_first_option_and_sync_script_exists(self):
        """چیپ ردیف‌ها با گزینهٔ پیش‌فرض (اول) نوار ارسال شروع می‌شود و اسکریپت همگام دارد"""
        import re
        self._add_item(self.brand.id)
        page = self.client.get(self.url)
        content = page.content.decode('utf-8')
        # گزینه اول سِلکت از پیش انتخاب شده باشد
        m = re.search(
            r'<select name="delivery_date"[^>]*>.*?<option value="([^"]+)" selected>(.*?)</option>',
            content, re.S)
        self.assertIsNotNone(m, 'گزینه اول سِلکت زمان تحویل باید selected باشد')
        label = m.group(2).strip()
        # چیپ اولیهٔ ردیف‌ها همان برچسب گزینه اول باشد
        self.assertIn('تحویل: <span class="row-delivery-text">%s</span>' % label, content)
        # اسکریپت همگام‌سازی با رویداد change وجود داشته باشد
        self.assertIn("deliverySelect.addEventListener('change', syncRowDelivery)", content)
