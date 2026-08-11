from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Cafe, Category, Unit, Product, PurchaseRequest,
    PurchaseItem, OfficerAssignment,
)

User = get_user_model()


class CeoSelfBuyTests(TestCase):
    """«خرید توسط خودم» در صفحه «درخواست کالا» مدیرعامل

    - مدیرعامل هنگام ارسال، قلم را برای خودش نگه می‌دارد (لیست خرید من)
    - در «لیست خرید من» فقط تخصیص‌های خودش دیده می‌شود (نه سهم مشترک)
    - در «مدیریت درخواست‌ها» ادمین، مسئول خرید این اقلام نام مدیرعامل است
    """

    def setUp(self):
        self.client = Client()
        self.ceo_user = User.objects.create_user(
            username='ceo_user', password='testpass123', role='ceo')
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

        # middleware کافه: کاربران عادی باید کافه خودشان را داشته باشند
        self.ceo_user.cafe = self.cafe
        self.kitchen_user.cafe = self.cafe
        self.ceo_user.save()
        self.kitchen_user.save()

        self.today = timezone.localtime(timezone.now()).date()

        self.request = PurchaseRequest.objects.create(
            user=self.kitchen_user, cafe=self.cafe, status='pending')
        self.item = PurchaseItem.objects.create(
            request=self.request, product=self.product, name='Test Product',
            quantity=2, unit='kg')

        # قلم مشترک (assigned_to=None) و قلم مسئول خرید دیگر — نباید در
        # «لیست خرید من» مدیرعامل باشند
        self.shared_item = PurchaseItem.objects.create(
            request=self.request, name='Shared Item', quantity=1, unit='kg')
        OfficerAssignment.objects.create(
            item=self.shared_item, target_role='purchasing',
            assigned_to=None, status='pending')
        self.other_item = PurchaseItem.objects.create(
            request=self.request, name='Officer Item', quantity=1, unit='kg')
        OfficerAssignment.objects.create(
            item=self.other_item, target_role='purchasing',
            assigned_to=self.purchasing_user, status='pending')

        self.send_url = reverse(
            'all_departments_requests', kwargs={'cafe_code': 'main'}) + '?type=purchase'
        self.my_list_url = reverse(
            'purchase_officer_dashboard', kwargs={'cafe_code': 'main'})

    # ── فرم ارسال: گزینه «خرید توسط خودم» ──

    def test_self_buy_option_visible_for_ceo(self):
        self.client.login(username='ceo_user', password='testpass123')
        response = self.client.get(
            reverse('all_departments_requests', kwargs={'cafe_code': 'main'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'خرید توسط خودم')

    def test_send_assigns_to_self(self):
        self.client.login(username='ceo_user', password='testpass123')
        response = self.client.post(self.send_url, {
            'action': 'send',
            'item_ids': str(self.item.id),
            'officer_user_id': str(self.ceo_user.id),
        })
        self.assertEqual(response.status_code, 302)
        assignment = OfficerAssignment.objects.get(item=self.item)
        self.assertEqual(assignment.target_role, 'purchasing')
        self.assertEqual(assignment.assigned_to, self.ceo_user)
        self.assertEqual(assignment.status, 'pending')

    def test_send_invalid_officer_still_rejected(self):
        """مدیرعامل نمی‌تواند قلم را به یک کاربر عادی (بدون نقش خرید) ارسال کند"""
        self.client.login(username='ceo_user', password='testpass123')
        response = self.client.post(self.send_url, {
            'action': 'send',
            'item_ids': str(self.item.id),
            'officer_user_id': str(self.kitchen_user.id),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            OfficerAssignment.objects.filter(item=self.item).exists(),
            'ارسال به کاربر بدون نقش خرید نباید تخصیص بسازد')

    # ── صفحه «لیست خرید من» مدیرعامل ──

    def test_ceo_can_open_my_purchase_list(self):
        self.client.login(username='ceo_user', password='testpass123')
        response = self.client.get(self.my_list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'لیست خرید من')
        self.assertNotContains(response, 'داشبورد مسئول خرید')  # عنوان متفاوت

    def test_my_list_shows_own_items_only(self):
        self.client.login(username='ceo_user', password='testpass123')
        OfficerAssignment.objects.create(
            item=self.item, target_role='purchasing',
            assigned_to=self.ceo_user, status='pending')
        response = self.client.get(self.my_list_url)
        self.assertContains(response, 'Test Product')
        self.assertNotContains(response, 'Shared Item')     # سهم مشترک نه
        self.assertNotContains(response, 'Officer Item')    # قلم مسئول دیگر نه

    def test_my_list_mark_purchased(self):
        self.client.login(username='ceo_user', password='testpass123')
        assignment = OfficerAssignment.objects.create(
            item=self.item, target_role='purchasing',
            assigned_to=self.ceo_user, status='pending')
        response = self.client.post(self.my_list_url, {
            'action': 'purchased', 'item_id': str(assignment.id)})
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, 'purchased')

    def test_my_list_return_to_requests(self):
        self.client.login(username='ceo_user', password='testpass123')
        assignment = OfficerAssignment.objects.create(
            item=self.item, target_role='purchasing',
            assigned_to=self.ceo_user, status='pending')
        response = self.client.post(self.my_list_url, {
            'action': 'return', 'item_id': str(assignment.id)})
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, 'returned')

    # ── دیده شدن در «مدیریت درخواست‌ها» ادمین با نام مدیرعامل ──

    def _make_self_buy_assignment(self):
        return OfficerAssignment.objects.create(
            item=self.item, target_role='purchasing',
            assigned_to=self.ceo_user, status='pending')

    def test_admin_list_shows_ceo_name_as_officer(self):
        self.client.login(username='ceo_user', password='testpass123')
        self._make_self_buy_assignment()
        self.client.logout()
        admin_user = User.objects.create_user(
            username='admin_user', password='testpass123', role='admin')
        self.client.login(username='admin_user', password='testpass123')
        url = reverse('request_management_dashboard', kwargs={'cafe_code': 'main'})
        response = self.client.get(url + f'?date={self.today.isoformat()}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Product')
        self.assertContains(response, 'ceo_user')  # مسئول خرید: نام مدیرعامل

    def test_admin_csv_shows_ceo_name_as_officer(self):
        self.client.login(username='ceo_user', password='testpass123')
        self._make_self_buy_assignment()
        self.client.logout()
        admin_user = User.objects.create_user(
            username='admin_user', password='testpass123', role='admin')
        self.client.login(username='admin_user', password='testpass123')
        url = reverse('request_management_csv', kwargs={'cafe_code': 'main'})
        response = self.client.get(url + f'?date={self.today.isoformat()}')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8-sig')
        self.assertIn('Test Product', content)
        self.assertIn('ceo_user', content)
