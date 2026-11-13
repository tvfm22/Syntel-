from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Cafe, Category, Unit, Product, PurchaseRequest,
    PurchaseItem, OfficerAssignment, Warehouse, StockItem,
)

User = get_user_model()


class RequestManagementTests(TestCase):
    """صفحه «مدیریت درخواست‌ها» داشبورد ادمین + دانلود CSV

    - لیست خرید: اقلام درخواستی که برای مسئول خرید ارسال شده‌اند، با فیلتر تاریخ
    - اقلام برگشت‌خورده به داشبورد مدیرعامل در لیست و CSV نیستند
    """

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user(
            username='admin_user', password='testpass123', role='admin')
        self.purchasing_user = User.objects.create_user(
            username='purchasing_user', password='testpass123', role='purchasing')
        self.kitchen_user = User.objects.create_user(
            username='kitchen_user', password='testpass123', role='kitchen')

        self.category = Category.objects.create(name='Test Category')
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.product = Product.objects.create(
            name='Test Product', category=self.category, unit=self.unit, min_stock=10)

        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        # کاربر عادی باید کافه خودش را داشته باشد تا middleware کافه اجازه بدهد
        self.kitchen_user.cafe = self.cafe
        self.kitchen_user.save()

        # تاریخ «امروز» در منطقه زمانی محلی (فیلتر __date هم در همان منطقه انجام می‌شود)
        self.today = timezone.localtime(timezone.now()).date()

        # درخواست امروز با سه قلم: در انتظار / خریداری‌شده / برگشت‌خورده
        self.request = PurchaseRequest.objects.create(
            user=self.kitchen_user, cafe=self.cafe, status='pending')
        self.item_pending = PurchaseItem.objects.create(
            request=self.request, product=self.product, name='Test Product',
            quantity=2, unit='kg')
        self.item_purchased = PurchaseItem.objects.create(
            request=self.request, name='Test Product 2', quantity=1, unit='kg')
        self.item_returned = PurchaseItem.objects.create(
            request=self.request, name='Test Product 3', quantity=1, unit='kg')

        OfficerAssignment.objects.create(
            item=self.item_pending, target_role='purchasing',
            assigned_to=self.purchasing_user, status='pending')
        OfficerAssignment.objects.create(
            item=self.item_purchased, target_role='purchasing',
            assigned_to=self.purchasing_user, status='purchased')
        OfficerAssignment.objects.create(
            item=self.item_returned, target_role='purchasing',
            assigned_to=self.purchasing_user, status='returned')

        # درخواست دیروز (برای تست فیلتر تاریخ) — auto_now_add مقدار اولیه را
        # بازنویسی می‌کند، پس created_at را بعد از ساخت با update تنظیم می‌کنیم
        self.yesterday_req = PurchaseRequest.objects.create(
            user=self.kitchen_user, cafe=self.cafe, status='pending')
        PurchaseRequest.objects.filter(pk=self.yesterday_req.pk).update(
            created_at=timezone.localtime() - timezone.timedelta(days=1))

        # ── داده‌های لیست انبار ──
        self.warehouse = Warehouse.objects.create(
            cafe=self.cafe, code='wh', name='انبار اصلی', is_active=True)
        self.warehouse2 = Warehouse.objects.create(
            cafe=self.cafe, code='wh2', name='انبار فرعی', is_active=True)
        self.stock1 = StockItem.objects.create(
            product=self.product,
            warehouse=self.warehouse, quantity=10, unit=self.unit)
        self.stock2 = StockItem.objects.create(
            product=self.product,
            warehouse=self.warehouse2, quantity=10, unit=self.unit)

        # قلم انبار: امروز از دو انبار تامین شده (3 + 4 = 7 تحویلی)
        # بدون product تا نام خودِ قلم نمایش داده شود
        self.wh_item = PurchaseItem.objects.create(
            request=self.request, name='WH Product',
            quantity=5, unit='kg', description='توضیح تست')
        OfficerAssignment.objects.create(
            item=self.wh_item, target_role='warehouse', status='fulfilled',
            stock_item=self.stock1, supplied_quantity=3)
        OfficerAssignment.objects.create(
            item=self.wh_item, target_role='warehouse', status='fulfilled',
            stock_item=self.stock2, supplied_quantity=4)
        # قلم انبار در انتظار — نباید در لیست تحویل‌شده‌ها باشد
        self.wh_pending_item = PurchaseItem.objects.create(
            request=self.request, name='WH Pending', quantity=2, unit='kg')
        OfficerAssignment.objects.create(
            item=self.wh_pending_item, target_role='warehouse', status='pending')
        self.item_yesterday = PurchaseItem.objects.create(
            request=self.yesterday_req, name='Yesterday Item', quantity=3, unit='kg')
        OfficerAssignment.objects.create(
            item=self.item_yesterday, target_role='purchasing',
            assigned_to=self.purchasing_user, status='pending')

    def _dashboard_url(self, **params):
        url = reverse('request_management_dashboard', kwargs={'cafe_code': 'main'})
        if params:
            url += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        return url

    def test_requires_login(self):
        response = self.client.get(self._dashboard_url())
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_non_admin_redirected(self):
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(self._dashboard_url())
        self.assertEqual(response.status_code, 302)

    def test_purchase_list_shows_sent_items_not_returned(self):
        self.client.login(username='admin_user', password='testpass123')
        response = self.client.get(self._dashboard_url(date=self.today.isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'request_management.html')
        self.assertContains(response, 'Test Product')
        self.assertContains(response, 'Test Product 2')
        self.assertContains(response, 'purchasing_user')
        self.assertContains(response, 'آشپزخانه')  # بخش درخواست‌دهنده
        self.assertNotContains(response, 'Test Product 3')  # برگشت‌خورده حذف است

    def test_date_filter(self):
        self.client.login(username='admin_user', password='testpass123')
        response = self.client.get(self._dashboard_url(date=self.today.isoformat()))
        self.assertNotContains(response, 'Yesterday Item')
        yesterday = (self.today - timezone.timedelta(days=1)).isoformat()
        response = self.client.get(self._dashboard_url(date=yesterday))
        self.assertContains(response, 'Yesterday Item')
        self.assertNotContains(response, 'Test Product')

    def test_warehouse_tab_shows_fulfilled_items(self):
        self.client.login(username='admin_user', password='testpass123')
        response = self.client.get(self._dashboard_url(tab='warehouse', date=self.today.isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'WH Product')
        self.assertContains(response, '7 kg')  # مجموع تحویلی دو انبار (3 + 4)
        self.assertContains(response, 'توضیح تست')
        self.assertNotContains(response, 'WH Pending')  # قلم در انتظار نمایش داده نمی‌شود

    def test_warehouse_tab_date_filter(self):
        self.client.login(username='admin_user', password='testpass123')
        # قلم انبار دیروز ساخته می‌شود و در لیست امروز نباشد
        yesterday_item = PurchaseItem.objects.create(
            request=self.yesterday_req, name='WH Yesterday', quantity=1, unit='kg')
        OfficerAssignment.objects.create(
            item=yesterday_item, target_role='warehouse', status='fulfilled',
            stock_item=self.stock1, supplied_quantity=1)
        response = self.client.get(self._dashboard_url(tab='warehouse', date=self.today.isoformat()))
        self.assertNotContains(response, 'WH Yesterday')

    def test_warehouse_pdf(self):
        self.client.login(username='admin_user', password='testpass123')
        url = reverse('request_management_warehouse_pdf', kwargs={'cafe_code': 'main'})
        response = self.client.get(url + f'?date={self.today.isoformat()}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_warehouse_pdf_empty_day(self):
        """روزی که تحویلی ندارد: PDF خالی هم بدون خطا ساخته شود"""
        self.client.login(username='admin_user', password='testpass123')
        url = reverse('request_management_warehouse_pdf', kwargs={'cafe_code': 'main'})
        empty_day = (self.today - timezone.timedelta(days=3)).isoformat()
        response = self.client.get(url + f'?date={empty_day}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_csv_export(self):
        self.client.login(username='admin_user', password='testpass123')
        url = reverse('request_management_csv', kwargs={'cafe_code': 'main'})
        response = self.client.get(url + f'?date={self.today.isoformat()}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment', response['Content-Disposition'])
        content = response.content.decode('utf-8-sig')
        self.assertIn('کالای درخواست‌شده', content)
        self.assertIn('Test Product', content)
        self.assertIn('Test Product 2', content)
        self.assertIn('purchasing_user', content)
        self.assertNotIn('Test Product 3', content)  # برگشت‌خورده در CSV نیست
        # درخواست دیروز در CSV امروز نیست
        self.assertNotIn('Yesterday Item', content)
