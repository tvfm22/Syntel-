from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Cafe, Category, Unit, Product, PurchaseRequest,
    PurchaseItem, OfficerAssignment, Warehouse, StockItem,
    ProductBrand, StockTransaction,
)

User = get_user_model()


class TrimDecimalRegressionTests(TestCase):
    """خطای «f» وسط عدد: format از django.utils.numberformat سایهٔ builtin بود

    «7f5» به‌جای «7.5» — فقط اعدادی که جزء اعشاری دارند دچار این خطا می‌شدند.
    """

    def test_no_f_letter_in_decimal_numbers(self):
        from purchases.templatetags.custom_filters import trim_decimal
        self.assertEqual(trim_decimal('7.5'), '7.5')
        self.assertEqual(trim_decimal('15.2'), '15.2')
        self.assertEqual(trim_decimal('42.75'), '42.75')
        self.assertEqual(trim_decimal('0.4'), '0.4')
        self.assertNotIn('f', trim_decimal('7.5'))
        self.assertNotIn('f', trim_decimal('42.75'))

    def test_integers_unaffected(self):
        from purchases.templatetags.custom_filters import trim_decimal
        self.assertEqual(trim_decimal('10'), '10')
        self.assertEqual(trim_decimal('5'), '5')


class StockTransactionRateTests(TestCase):
    """تاریخچه: برند و نرخ تبدیل هنگام کسر از انبار ثبت و نمایش داده شود"""

    def setUp(self):
        self.client = Client()
        # مانند کاربران واقعی که داده‌مایگریشن نقش دوم انبار را به آن‌ها داده است
        self.user = User.objects.create_user(
            username='stock_user', password='testpass123',
            role='purchasing', secondary_role='warehouse')

        self.category = Category.objects.create(name='Test Category')
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')

        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        self.user.cafe = self.cafe
        self.user.save()

        self.today = timezone.localtime(timezone.now()).date()

        self.warehouse = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)

        self.product = Product.objects.create(
            name='Oil Product', category=self.category, unit=self.unit, min_stock=1)
        # هر بسته = 2.5 کیلوگرم (واحد پایه)
        self.brand = ProductBrand.objects.create(
            product=self.product, brand_name='برند روغن خورشید',
            conversion_rate=Decimal('2.5'), trade_unit='بسته')
        self.stock = StockItem.objects.create(
            product=self.product, product_brand=self.brand,
            warehouse=self.warehouse, quantity=50, unit=self.unit)

    def _add_item(self):
        req = PurchaseRequest.objects.create(
            user=self.user, cafe=self.cafe, status='pending')
        item = PurchaseItem.objects.create(
            request=req, product=self.product, name=self.product.name,
            quantity=2, unit='بسته')
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse',
            assigned_to=self.user, status='pending')
        return item

    def test_fulfill_records_conversion_rate(self):
        """تراکنش خروج هنگام تامین، نرخ تبدیل برند را ذخیره می‌کند"""
        item = self._add_item()
        self.client.login(username='stock_user', password='testpass123')
        url = reverse('warehouse_officer_dashboard', kwargs={'cafe_code': 'main'})
        response = self.client.post(url, {
            'action': 'fulfill',
            'item_id': str(item.id),
            'date': self.today.isoformat(),
            'stock_item_id': str(self.stock.id),
            'qty': '2',
        })
        self.assertEqual(response.status_code, 302)
        tx = StockTransaction.objects.get(
            reference_type='officer_assignment', transaction_type='out')
        self.assertEqual(tx.quantity, Decimal('5.0000'))       # 2 بسته × 2.5
        self.assertEqual(tx.conversion_rate, Decimal('2.5000'))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('45.0000'))

    def test_history_page_shows_brand_and_rate(self):
        """صفحه تاریخچه، برند و نرخ تبدیل و معادل تجاری را نمایش می‌دهد"""
        item = self._add_item()
        self.client.login(username='stock_user', password='testpass123')
        url = reverse('warehouse_officer_dashboard', kwargs={'cafe_code': 'main'})
        self.client.post(url, {
            'action': 'fulfill',
            'item_id': str(item.id),
            'date': self.today.isoformat(),
            'stock_item_id': str(self.stock.id),
            'qty': '2',
        })
        hist_url = reverse(
            'stock_transactions',
            kwargs={'cafe_code': 'main', 'stock_id': self.stock.id})
        response = self.client.get(hist_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'برند روغن خورشید')
        self.assertContains(response, 'نرخ تبدیل: هر بسته = 2.5 Kilogram')
        # معادل تجاری 5 کیلوگرم → 2 بسته
        self.assertContains(response, 'معادل تجاری: 2 بسته')
