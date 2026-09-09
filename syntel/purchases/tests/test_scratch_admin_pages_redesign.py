"""تست موقت: رندر صفحات بازطراحی‌شده ادمین (دسته‌بندی، محصول، تأمین‌کننده، موجودی، تراکنش‌ها)"""
from django.test import TestCase, Client
from django.urls import reverse

from purchases.models import (
    User, Cafe, Category, Product, Unit, Supplier,
    Warehouse, StockItem, StockTransaction, ProductBrand,
)


class AdminPagesRedesignTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(name='کافه تست', code='testcafe')
        cls.unit = Unit.objects.create(name='کیلوگرم', symbol='kg')
        cls.admin = User.objects.create_user(
            username='redesign_admin', password='testpass123',
            role='admin', is_active=True,
        )
        # دسته‌بندی
        cls.cat = Category.objects.create(name='لبنیات', cafe=cls.cafe, description='محصولات لبنی')
        cls.child = Category.objects.create(name='پنیر', parent=cls.cat, cafe=cls.cafe)
        # محصول + برند
        cls.product = Product.objects.create(
            name='شیر', category=cls.cat, unit=cls.unit,
            min_stock=10, cafe=cls.cafe,
        )
        cls.brand = ProductBrand.objects.create(
            product=cls.product, brand_name='پگاه', trade_unit='بطری',
            conversion_rate=2, is_active=True,
        )
        # تأمین‌کننده
        cls.supplier = Supplier.objects.create(
            name='تأمین‌کننده تست', cafe=cls.cafe, status='active',
            supplier_type='company', phone='0211234', city='تهران',
        )
        # انبار + موجودی + تراکنش
        cls.warehouse = Warehouse.objects.create(
            name='انبار اصلی', code='WH1', cafe=cls.cafe,
        )
        cls.stock = StockItem.objects.create(
            product=cls.product, warehouse=cls.warehouse, unit=cls.unit,
            quantity=50, min_quantity=10, product_brand=cls.brand,
        )
        cls.tx = StockTransaction.objects.create(
            stock_item=cls.stock, transaction_type='in', quantity=20,
            previous_quantity=30, new_quantity=50, unit=cls.unit,
            created_by=cls.admin,
        )

    def login(self):
        c = Client()
        c.login(username='redesign_admin', password='testpass123')
        self.client = c
        return c

    def test_category_list_renders(self):
        c = self.login()
        r = c.get(reverse('category_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'لبنیات')
        self.assertContains(r, 'پنیر')

    def test_product_list_renders(self):
        c = self.login()
        r = c.get(reverse('product_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'شیر')
        self.assertContains(r, 'پگاه')

    def test_supplier_list_renders(self):
        c = self.login()
        r = c.get(reverse('supplier_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'تأمین‌کننده تست')

    def test_supplier_list_filters(self):
        c = self.login()
        # فیلتر وضعیت فعال
        r = c.get(reverse('supplier_list', args=[self.cafe.code]), {'status': 'active'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'تأمین‌کننده تست')
        # فیلتر وضعیت غیرفعال — نباید نمایش داده شود
        r = c.get(reverse('supplier_list', args=[self.cafe.code]), {'status': 'blacklisted'})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'تأمین‌کننده تست')
        # جستجو
        r = c.get(reverse('supplier_list', args=[self.cafe.code]), {'search': 'تست'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'تأمین‌کننده تست')

    def test_stock_list_renders(self):
        c = self.login()
        r = c.get(reverse('stock_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'شیر')
        self.assertContains(r, 'پگاه')
        self.assertContains(r, 'انبار اصلی')

    def test_stock_transactions_renders(self):
        c = self.login()
        r = c.get(reverse('stock_transactions', args=[self.cafe.code, self.stock.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'شیر')
        self.assertContains(r, 'ورود به انبار')

    def test_product_list_filters(self):
        c = self.login()
        r = c.get(reverse('product_list', args=[self.cafe.code]), {'is_active': 'true'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'شیر')
        r = c.get(reverse('product_list', args=[self.cafe.code]), {'search': 'شیر'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'شیر')

    def test_stock_list_null_unit_legacy(self):
        """آیتم قدیمی بدون واحد نباید صفحه موجودی را بترکاند (unit=None)"""
        from django.db import connection
        # آیتم بدون واحد — شبیه دیتای legacy
        StockItem.objects.create(
            product=self.product, warehouse=self.warehouse,
            unit=None, quantity=5, min_quantity=0,
        )
        c = self.login()
        r = c.get(reverse('stock_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'شیر')

    def test_stock_list_low_stock_filter(self):
        c = self.login()
        r = c.get(reverse('stock_list', args=[self.cafe.code]), {'low_stock': 'on'})
        self.assertEqual(r.status_code, 200)
        # موجودی ۵۰ > حداقل ۱۰ پس نباید در نتایج باشد
        self.assertNotContains(r, 'پگاه')

    def test_user_list_unified_header(self):
        """هدر یکپارچه کاربران: عنوان + تب‌ها + جستجو در یک کارت، بدون هدر دوطبقه"""
        c = self.login()
        r = c.get(reverse('user_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        content = r.content.decode('utf-8')
        # عناصر هدر یکپارچه
        self.assertIn('page-head', content)
        self.assertIn('ph-top', content)
        self.assertIn('ph-filters', content)
        # تب‌ها و جستجو داخل همان کارت
        self.assertIn('user-tabs', content)
        self.assertIn('search-input', content)
        # دیگر نباید هدر قدیمی/فیلتر جداگانه وجود داشته باشد (به‌صورت المنت)
        self.assertNotIn('class="page-header"', content)
        self.assertNotIn('class="filters-container"', content)

    def test_user_list_officer_tab(self):
        c = self.login()
        r = c.get(reverse('user_list', args=[self.cafe.code]), {'officer': '1'})
        self.assertEqual(r.status_code, 200)
        content = r.content.decode('utf-8')
        self.assertIn('officer" value="1"', content)

    def test_warehouse_officer_dashboard_unified_header(self):
        """هدر یکپارچه داشبورد مسئول انبار: عنوان + تاریخ تحویل + بخش در یک هدر (کارت فیلتر جدا ندارد)"""
        import datetime
        from django.utils import timezone
        from purchases.models import PurchaseRequest, Department, PurchaseItem
        dept = Department.objects.create(name='آشپزخانه', cafe=self.cafe)
        req = PurchaseRequest.objects.create(
            user=self.admin, cafe=self.cafe,
            # داشبورد فقط اقلامِ همان روز تحویل را نشان می‌دهد
            delivery_date=timezone.localtime(timezone.now()).date(),
        )
        PurchaseItem.objects.create(
            request=req, product=self.product, name='شیر',
            quantity=3, unit='کیلوگرم',
        )
        # نقش مسئول انبار لازم است
        officer = User.objects.create_user(
            username='redesign_wo', password='testpass123',
            role='warehouse', is_active=True, cafe=self.cafe,
        )
        c = Client()
        c.login(username='redesign_wo', password='testpass123')
        # GET بدون day به ‎?day=<امروز> ریدایرکت می‌شود؛ با follow دنبال می‌کنیم
        r = c.get(reverse('warehouse_officer_dashboard', args=[self.cafe.code]), follow=True)
        self.assertEqual(r.status_code, 200)
        content = r.content.decode('utf-8')
        # هدر یکپارچه فعلی: عنوان + ناوبری تاریخ تحویل + فیلتر بخش، همگی داخل همان page-header
        self.assertIn('class="ph-main"', content)
        self.assertIn('class="ph-icon"', content)
        self.assertIn('class="page-title"', content)
        self.assertIn('class="date-nav"', content)
        self.assertIn('class="date-display', content)
        self.assertIn('class="dept-filter"', content)
        # کارت/نوار فیلتر جداگانهٔ زیر هدر و قرص‌های فیلتر چندروزه وجود نداشته باشند
        self.assertNotIn('class="filters-container"', content)
        self.assertNotIn('filter-pill', content)
        # کارت درخواستِ همان روز تحویل نمایش داده شود (عنوان با span جدا شده)
        self.assertIn('درخواست <span class="req-no">#{}</span>'.format(req.id), content)
