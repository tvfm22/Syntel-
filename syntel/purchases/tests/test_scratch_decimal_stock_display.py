"""سنگ‌آزمایش: نمایش موجودی با حداکثر ۲ رقم اعشار و حذف صفرهای آخر

قانون: 25.00 → «25»، 12.50 → «12.5»، 8.333… → «8.33» — در همهٔ جاهای
نمایش موجودی (چیپ پیش‌نویس درخواست خرید، داشبورد انبارِ کمبود، API بررسی موجودی).
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Unit, Product, ProductBrand, PurchaseDraft,
    User, Warehouse, StockItem,
)


class DecimalStockDisplayTest(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code='dec', name='کافه اعشار', is_active=True)
        self.unit = Unit.objects.create(name='کیلوگرم', symbol='kg')
        self.officer = User.objects.create_user(
            username='dec_wh', password='x', role='warehouse', cafe=self.cafe)

        # محصول با سه برند: نرخ کسری (۳)، نیمه (۲) و صحیح (۱)
        self.product = Product.objects.create(
            name='شیر', cafe=self.cafe, unit=self.unit,
            shelf_life_days=90, is_active=True, is_complete=True)
        self.brand_third = ProductBrand.objects.create(
            product=self.product, brand_name='دامداران', trade_unit='بطری', conversion_rate=3)
        self.brand_half = ProductBrand.objects.create(
            product=self.product, brand_name='پگاه', trade_unit='بطری', conversion_rate=2)
        self.brand_one = ProductBrand.objects.create(
            product=self.product, brand_name='مهر', trade_unit='کارتن', conversion_rate=1)
        wh = Warehouse.objects.create(name='انبار اصلی', code='DEC-WH1', cafe=self.cafe)
        # واحد پایه ۲۵ → واحد تجاری ۲۵/۳ = 8.333… ، ۲۵/۲ = 12.5 ، ۲۵/۱ = 25
        for brand in (self.brand_third, self.brand_half, self.brand_one):
            StockItem.objects.create(
                product=self.product, product_brand=brand,
                warehouse=wh, quantity=25, unit=self.unit)

        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session.save()
        self.client.force_login(self.officer)

        self.draft_url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})

    def _add_item(self, brand):
        return self.client.post(self.draft_url, data={
            'action': 'add_item',
            'product_id': str(self.product.id),
            'brand_id': str(brand.id),
            'quantity': '1',
            'unit': 'کیلوگرم',
            'description': '',
            'new_name': '',
        })

    def _draft_content(self, brands):
        for b in brands:
            self.assertEqual(self._add_item(b).status_code, 302)
        draft = PurchaseDraft.objects.filter(cafe=self.cafe, created_by=self.officer).first()
        self.assertIsNotNone(draft)
        return self.client.get(self.draft_url).content.decode('utf-8')

    def test_draft_chips_fraction_and_whole_trade_units(self):
        """چیپ موجودی پیش‌نویس: ۲۵/۳ → «8.33 بطری»، ۲۵/۲ → «12.5 بطری»، ۲۵/۱ → «25 کارتن»"""
        content = self._draft_content([self.brand_third, self.brand_half, self.brand_one])
        self.assertIn('موجودی: 8.33 بطری', content)
        self.assertIn('موجودی: 12.5 بطری', content)
        self.assertIn('موجودی: 25 کارتن', content)
        # دم‌های اعشاریِ تقسیم (28 رقم) و صفرهای بی‌مورد نباید دیده شوند
        self.assertNotIn('8.333333333333', content)
        self.assertNotIn('12.50', content)
        self.assertNotIn('25.00', content)

    def test_check_availability_json_trimmed(self):
        """API بررسی موجودی: stock_trade کوتاه (۸.۳۳) و qty صحیح بدون اعشار (۱۰) برگردد"""
        url = reverse('check_product_availability', kwargs={'cafe_code': self.cafe.code})
        r = self.client.get(url, {'product_id': self.product.id,
                                  'brand_id': self.brand_third.id,
                                  'qty': '10'})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['stock_trade'], '8.33')
        self.assertEqual(data['qty'], '10')
        # درخواست ۱۰ بطری > موجودی ۸.۳۳ → موجود نیست؛ برندهای جایگزین با موجودی‌های کوتاه‌شده پیشنهاد شوند
        self.assertFalse(data['available'])
        self.assertEqual(sorted(a['available'] for a in data['alternatives']), ['12.5', '25'])
        # درخواست کمتر از موجودی (۵ ≤ ۸.۳۳) → موجود است
        r1 = self.client.get(url, {'product_id': self.product.id,
                                   'brand_id': self.brand_third.id,
                                   'qty': '5'})
        self.assertTrue(r1.json()['available'])
        self.assertEqual(r1.json()['stock_trade'], '8.33')
        # قلم اعشاریِ درخواستی هم بدون صفر اضافه برگردد
        r2 = self.client.get(url, {'product_id': self.product.id,
                                   'brand_id': self.brand_third.id,
                                   'qty': '3.5'})
        self.assertEqual(r2.json()['qty'], '3.5')

    def test_warehouse_dashboard_low_stock_decimal(self):
        """داشبورد انبار (کمبود): موجودی ۴.۷۵ و حداقل صحیح، بدون صفر زائد"""
        wh = Warehouse.objects.create(name='انبار دوم', code='DEC-WH2', cafe=self.cafe)
        unit_box = Unit.objects.create(name='کارتن', symbol='box')
        StockItem.objects.create(
            product=self.product, warehouse=wh, unit=unit_box,
            quantity=Decimal('4.75'), min_quantity=Decimal('10'))

        content = self.client.get(
            reverse('warehouse_dashboard', kwargs={'cafe_code': self.cafe.code})
        ).content.decode('utf-8')
        self.assertIn('4.75', content)
        self.assertNotIn('4.750', content)
        self.assertNotIn('4.7500', content)
