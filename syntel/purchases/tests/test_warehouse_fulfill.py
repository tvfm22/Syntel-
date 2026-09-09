from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Cafe, Category, Unit, Product, PurchaseRequest,
    PurchaseItem, OfficerAssignment, Warehouse, StockItem, ProductBrand,
)

User = get_user_model()


class WarehouseFulfillOptionsTests(TestCase):
    """گزینه‌های برند در فرم تامین انبار

    - محصول بدون برند که فقط در یک انبار موجودی دارد: انتخاب برند و افزودن برند
      نمایش داده نشود (همان ردیف تنها به‌صورت نامرئی ارسال می‌شود)
    - محصول با دقیقاً یک برند که فقط در یک انبار موجودی دارد: همان گزینه
      به‌صورت پیش‌فرض انتخاب شده باشد
    """

    def setUp(self):
        self.client = Client()
        self.wh_user = User.objects.create_user(
            username='warehouse_user', password='testpass123', role='warehouse')

        self.category = Category.objects.create(name='Test Category')
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')

        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        self.wh_user.cafe = self.cafe
        self.wh_user.save()

        self.today = timezone.localtime(timezone.now()).date()

        self.warehouse1 = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)
        self.warehouse2 = Warehouse.objects.create(
            cafe=self.cafe, code='wh2', name='انبار فرعی', is_active=True)

        # محصول بدون برند — فقط در انبار اصلی موجودی دارد
        self.p_unbranded = Product.objects.create(
            name='Unbranded Product', category=self.category, unit=self.unit, min_stock=1)
        self.stock_unbranded = StockItem.objects.create(
            product=self.p_unbranded, warehouse=self.warehouse1,
            quantity=10, unit=self.unit)

        # محصول بدون برند — در دو انبار موجودی دارد
        self.p_unbranded_multi = Product.objects.create(
            name='Unbranded Multi', category=self.category, unit=self.unit, min_stock=1)
        StockItem.objects.create(
            product=self.p_unbranded_multi, warehouse=self.warehouse1,
            quantity=10, unit=self.unit)
        StockItem.objects.create(
            product=self.p_unbranded_multi, warehouse=self.warehouse2,
            quantity=10, unit=self.unit)

        # محصول تک‌برند — فقط در یک انبار موجودی دارد
        self.p_single_brand = Product.objects.create(
            name='Single Brand', category=self.category, unit=self.unit, min_stock=1)
        self.brand_a = ProductBrand.objects.create(
            product=self.p_single_brand, brand_name='برند آلفا', conversion_rate=1)
        self.stock_single_brand = StockItem.objects.create(
            product=self.p_single_brand, product_brand=self.brand_a,
            warehouse=self.warehouse1, quantity=10, unit=self.unit)

        # محصول تک‌برند — در دو انبار موجودی دارد
        self.p_single_brand_multi = Product.objects.create(
            name='Single Brand Multi', category=self.category, unit=self.unit, min_stock=1)
        self.brand_b = ProductBrand.objects.create(
            product=self.p_single_brand_multi, brand_name='برند بتا', conversion_rate=1)
        self.stock_single_brand_multi_1 = StockItem.objects.create(
            product=self.p_single_brand_multi, product_brand=self.brand_b,
            warehouse=self.warehouse1, quantity=10, unit=self.unit)
        self.stock_single_brand_multi_2 = StockItem.objects.create(
            product=self.p_single_brand_multi, product_brand=self.brand_b,
            warehouse=self.warehouse2, quantity=10, unit=self.unit)

        # محصول دوبرند — در یک انبار
        self.p_two_brands = Product.objects.create(
            name='Two Brands', category=self.category, unit=self.unit, min_stock=1)
        self.brand_c1 = ProductBrand.objects.create(
            product=self.p_two_brands, brand_name='برند گاما', conversion_rate=1)
        self.brand_c2 = ProductBrand.objects.create(
            product=self.p_two_brands, brand_name='برند دلتا', conversion_rate=1)
        StockItem.objects.create(
            product=self.p_two_brands, product_brand=self.brand_c1,
            warehouse=self.warehouse1, quantity=10, unit=self.unit)
        StockItem.objects.create(
            product=self.p_two_brands, product_brand=self.brand_c2,
            warehouse=self.warehouse1, quantity=10, unit=self.unit)

        self.dash_url = reverse('warehouse_officer_dashboard', kwargs={'cafe_code': 'main'})

    def _add_item(self, product, name=None):
        """قلم درخواست با زمان تحویل امروز و تخصیص در انتظار (تا در لیست داشبورد باشد)"""
        req = PurchaseRequest.objects.create(
            user=self.wh_user, cafe=self.cafe, status='pending',
            delivery_date=self.today)
        item = PurchaseItem.objects.create(
            request=req, product=product, name=name or product.name,
            quantity=2, unit='kg')
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse',
            assigned_to=self.wh_user, status='pending')
        return item

    def _get(self):
        self.client.login(username='warehouse_user', password='testpass123')
        # GET بدون delivery به ‎?delivery=<امروز> ریدایرکت می‌شود؛ با follow دنبال می‌کنیم
        return self.client.get(self.dash_url, follow=True)

    def test_unbranded_single_warehouse_hides_brand_options(self):
        """بدون برند + فقط یک انبار: نه انتخاب برند نه افزودن برند"""
        self._add_item(self.p_unbranded)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        # سلکت برند و دکمه افزودن برند نباشند
        self.assertNotContains(response, 'انتخاب برند...')
        self.assertNotContains(response, 'تأمین از برند دیگر')
        # همان ردیف تنها به‌صورت نامرئی ارسال می‌شود
        self.assertContains(
            response, f'<input type="hidden" name="stock_item_id" value="{self.stock_unbranded.id}">')
        self.assertContains(response, 'بدون برند')
        self.assertContains(response, 'موجودی 10 kg')

    def test_unbranded_multi_warehouse_keeps_select(self):
        """بدون برند ولی در چند انبار: انتخاب انبار لازم است، نمایش داده شود"""
        self._add_item(self.p_unbranded_multi)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'انتخاب برند...')
        self.assertContains(response, 'تأمین از برند دیگر')
        # سلکت‌ها همان دو انبار را دارند
        self.assertContains(response, 'انبار اصلی')
        self.assertContains(response, 'انبار فرعی')

    def test_single_brand_single_warehouse_default_selected(self):
        """یک برند + فقط یک انبار: همان گزینه پیش‌فرض انتخاب شده باشد"""
        self._add_item(self.p_single_brand)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'انتخاب برند...')
        self.assertContains(response, f'<option value="{self.stock_single_brand.id}" selected>')
        self.assertContains(response, 'برند آلفا')
        # هنوز هم قابلیت افزودن برند دیگر وجود دارد
        self.assertContains(response, 'تأمین از برند دیگر')

    def test_single_brand_multi_warehouse_no_default(self):
        """یک برند ولی در دو انبار: هیچ‌کدام پیش‌فرض نباشند"""
        self._add_item(self.p_single_brand_multi)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'برند بتا')
        self.assertNotContains(
            response, f'<option value="{self.stock_single_brand_multi_1.id}" selected>')
        self.assertNotContains(
            response, f'<option value="{self.stock_single_brand_multi_2.id}" selected>')

    def test_two_brands_no_default(self):
        """دو برند: هیچ برندی پیش‌فرض انتخاب نشود"""
        self._add_item(self.p_two_brands)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'برند گاما')
        self.assertContains(response, 'برند دلتا')
        self.assertNotContains(response, 'selected>')

    def test_fulfill_works_with_hidden_stock_input(self):
        """تامین از طریق ردیف مخفی (بدون سلکت) همچنان کار می‌کند"""
        item = self._add_item(self.p_unbranded)
        self.client.login(username='warehouse_user', password='testpass123')
        response = self.client.post(self.dash_url, {
            'action': 'fulfill',
            'item_id': str(item.id),
            'date': self.today.isoformat(),
            'stock_item_id': str(self.stock_unbranded.id),
            'qty': '2',
        })
        self.assertEqual(response.status_code, 302)
        assignment = item.officer_assignments.get(target_role='warehouse')
        self.assertEqual(assignment.status, 'fulfilled')
        self.stock_unbranded.refresh_from_db()
        self.assertEqual(self.stock_unbranded.quantity, 8)  # 10 - 2

        # پیام موفقیت: فقط نام کالا و تامین از انبار (بدون جزئیات تعداد برندها)
        # ریدایرکت به داشبورد بدون delivery خودش به ‎?delivery=<امروز> ریدایرکت می‌شود
        page = self.client.get(response.url, follow=True)
        self.assertContains(page, '«Unbranded Product» از انبار تامین شد.')
        self.assertNotContains(page, 'مجموع')
