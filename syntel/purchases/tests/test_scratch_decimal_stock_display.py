"""سنگ‌آزمایش: نمایش موجودی با حداکثر ۲ رقم اعشار و حذف صفرهای آخر

قانون: 25.00 → «25»، 12.50 → «12.5»، 8.333… → «8.33» — در همهٔ جاهای
نمایش موجودی (چیپ پیش‌نویس درخواست خرید، داشبورد انبارِ کمبود، API بررسی موجودی).
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Unit, Product, PurchaseDraft,
    User, Warehouse, StockItem,
)


class DecimalStockDisplayTest(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code='dec', name='کافه اعشار', is_active=True)
        self.unit_kg = Unit.objects.create(name='کیلوگرم', symbol='kg')
        self.unit_bottle = Unit.objects.create(name='بطری', symbol='btl')
        self.unit_box = Unit.objects.create(name='کارتن', symbol='box')
        self.officer = User.objects.create_user(
            username='dec_wh', password='x', role='warehouse', cafe=self.cafe)

        # سه محصول با موجودی‌های اعشاری، نیمه و صحیح
        self.product_third = Product.objects.create(
            name='شیر دامداران', cafe=self.cafe, unit=self.unit_bottle,
            shelf_life_days=90, is_active=True, is_complete=True)
        self.product_half = Product.objects.create(
            name='شیر پگاه', cafe=self.cafe, unit=self.unit_bottle,
            shelf_life_days=90, is_active=True, is_complete=True)
        self.product_one = Product.objects.create(
            name='شیر مهر', cafe=self.cafe, unit=self.unit_box,
            shelf_life_days=90, is_active=True, is_complete=True)
        wh = Warehouse.objects.create(name='انبار اصلی', code='DEC-WH1', cafe=self.cafe)
        StockItem.objects.create(
            product=self.product_third, warehouse=wh, quantity=Decimal('8.3333'), unit=self.unit_bottle)
        StockItem.objects.create(
            product=self.product_half, warehouse=wh, quantity=Decimal('12.50'), unit=self.unit_bottle)
        StockItem.objects.create(
            product=self.product_one, warehouse=wh, quantity=Decimal('25.00'), unit=self.unit_box)

        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session.save()
        self.client.force_login(self.officer)

        self.draft_url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})

    def _add_item(self, product):
        return self.client.post(self.draft_url, data={
            'action': 'add_item',
            'product_id': str(product.id),
            'quantity': '1',
            'unit': product.unit.name,
            'description': '',
            'new_name': '',
        })

    def _draft_content(self, products):
        for p in products:
            self.assertEqual(self._add_item(p).status_code, 302)
        draft = PurchaseDraft.objects.filter(cafe=self.cafe, created_by=self.officer).first()
        self.assertIsNotNone(draft)
        return self.client.get(self.draft_url).content.decode('utf-8')

    def test_draft_chips_fraction_and_whole_units(self):
        """ستون موجودی جدول پیش‌نویس: 8.3333 → «8.33»، 12.50 → «12.5»، 25.00 → «25»"""
        content = self._draft_content([self.product_third, self.product_half, self.product_one])
        self.assertIn('8.33 بطری', content)
        self.assertIn('12.5 بطری', content)
        self.assertIn('25 کارتن', content)
        # دم‌های اعشاری و صفرهای بی‌مورد نباید دیده شوند
        self.assertNotIn('8.3333', content)
        self.assertNotIn('12.50', content)
        self.assertNotIn('25.00', content)

    def test_check_availability_json_trimmed(self):
        """API بررسی موجودی: stock_trade کوتاه (۸.۳۳) و qty صحیح بدون اعشار (۱۰) برگردد"""
        url = reverse('check_product_availability', kwargs={'cafe_code': self.cafe.code})
        r = self.client.get(url, {'product_id': self.product_third.id, 'qty': '10'})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['stock_trade'], '8.33')
        self.assertEqual(data['qty'], '10')
        # درخواست ۱۰ > موجودی ۸.۳۳ → موجود نیست
        self.assertFalse(data['available'])
        # درخواست کمتر از موجودی (۵ ≤ ۸.۳۳) → موجود است
        r1 = self.client.get(url, {'product_id': self.product_third.id, 'qty': '5'})
        self.assertTrue(r1.json()['available'])
        self.assertEqual(r1.json()['stock_trade'], '8.33')
        # قلم اعشاریِ درخواستی هم بدون صفر اضافه برگردد
        r2 = self.client.get(url, {'product_id': self.product_third.id, 'qty': '3.5'})
        self.assertEqual(r2.json()['qty'], '3.5')

    def test_warehouse_dashboard_low_stock_decimal(self):
        """داشبورد انبار (کمبود): موجودی ۴.۷۵ و حداقل صحیح، بدون صفر زائد"""
        wh = Warehouse.objects.create(name='انبار دوم', code='DEC-WH2', cafe=self.cafe)
        StockItem.objects.create(
            product=self.product_one, warehouse=wh, unit=self.unit_box,
            quantity=Decimal('4.75'), min_quantity=Decimal('10'))

        content = self.client.get(
            reverse('warehouse_dashboard', kwargs={'cafe_code': self.cafe.code})
        ).content.decode('utf-8')
        self.assertIn('4.75', content)
        self.assertNotIn('4.750', content)
        self.assertNotIn('4.7500', content)
