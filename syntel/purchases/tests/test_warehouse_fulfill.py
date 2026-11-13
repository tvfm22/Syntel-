from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Cafe, Category, Unit, Product, PurchaseRequest,
    PurchaseItem, OfficerAssignment, Warehouse, StockItem,
)

User = get_user_model()


class WarehouseFulfillOptionsTests(TestCase):
    """گزینه‌های انبار در فرم تامین انبار

    - محصولی که فقط در یک انبار موجودی دارد: انتخاب انبار و افزودن ردیف
      نمایش داده نشود (همان ردیف تنها به‌صورت نامرئی ارسال می‌شود)
    - محصولی که در دو انبار موجودی دارد: سلکت انتخاب انبار و دکمه «تأمین از انبار دیگر» نمایش داده شوند
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

        # محصول — فقط در انبار اصلی موجودی دارد
        self.p_single_wh = Product.objects.create(
            name='Single Warehouse Product', category=self.category, unit=self.unit, min_stock=1)
        self.stock_single_wh = StockItem.objects.create(
            product=self.p_single_wh, warehouse=self.warehouse1,
            quantity=10, unit=self.unit)

        # محصول — در دو انبار موجودی دارد
        self.p_multi_wh = Product.objects.create(
            name='Multi Warehouse Product', category=self.category, unit=self.unit, min_stock=1)
        self.stock_multi_1 = StockItem.objects.create(
            product=self.p_multi_wh, warehouse=self.warehouse1,
            quantity=10, unit=self.unit)
        self.stock_multi_2 = StockItem.objects.create(
            product=self.p_multi_wh, warehouse=self.warehouse2,
            quantity=10, unit=self.unit)

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
        # GET بدون day به ‎?day=<امروز> ریدایرکت می‌شود؛ با follow دنبال می‌کنیم
        return self.client.get(self.dash_url, follow=True)

    def test_single_warehouse_hides_select_options(self):
        """فقط یک انبار: نه انتخاب انبار نه افزودن انبار دیگر"""
        self._add_item(self.p_single_wh)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        # سلکت انبار و دکمه افزودن انبار نباشند
        self.assertNotContains(response, 'انتخاب انبار...')
        self.assertNotContains(response, 'تأمین از انبار دیگر')
        # همان ردیف تنها به‌صورت نامرئی ارسال می‌شود
        self.assertContains(
            response, f'<input type="hidden" name="stock_item_id" value="{self.stock_single_wh.id}">')
        self.assertContains(response, 'انبار اصلی')
        # واحدِ اصلیِ کالا نمایش داده می‌شود (نام واحد، نه نماد)
        self.assertContains(response, 'موجودی 10 Kilogram')

    def test_multi_warehouse_keeps_select(self):
        """چند انبار: انتخاب انبار لازم است، نمایش داده شود"""
        self._add_item(self.p_multi_wh)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'انتخاب انبار...')
        self.assertContains(response, 'تأمین از انبار دیگر')
        # سلکت‌ها همان دو انبار را دارند
        self.assertContains(response, 'انبار اصلی')
        self.assertContains(response, 'انبار فرعی')
        self.assertNotContains(
            response, f'<option value="{self.stock_multi_1.id}" selected>')
        self.assertNotContains(
            response, f'<option value="{self.stock_multi_2.id}" selected>')

    def test_fulfill_works_with_hidden_stock_input(self):
        """تامین از طریق ردیف مخفی (بدون سلکت) همچنان کار می‌کند"""
        item = self._add_item(self.p_single_wh)
        self.client.login(username='warehouse_user', password='testpass123')
        response = self.client.post(self.dash_url, {
            'action': 'fulfill',
            'item_id': str(item.id),
            'date': self.today.isoformat(),
            'stock_item_id': str(self.stock_single_wh.id),
            'qty': '2',
        })
        self.assertEqual(response.status_code, 302)
        assignment = item.officer_assignments.get(target_role='warehouse')
        self.assertEqual(assignment.status, 'fulfilled')
        self.stock_single_wh.refresh_from_db()
        self.assertEqual(self.stock_single_wh.quantity, 8)  # 10 - 2

        # پیام موفقیت: فقط نام کالا و تامین از انبار
        # ریدایرکت به داشبورد بدون day خودش به ‎?day=<امروز> ریدایرکت می‌شود
        page = self.client.get(response.url, follow=True)
        self.assertContains(page, '«Single Warehouse Product» از انبار تامین شد.')
        self.assertNotContains(page, 'مجموع')
