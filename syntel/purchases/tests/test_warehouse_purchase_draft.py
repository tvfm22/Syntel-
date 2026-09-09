"""تست صفحه «درخواست خرید» مسئول انبار

جریان:
- ارجاع به خرید در داشبورد انبار → قلم به پیش‌نویس صفحه درخواست خرید می‌رود
- ویرایش/حذف قلم پیش‌نویس، افزودن کالای جدید
- «ارسال» → PurchaseRequest با source=warehouse_purchase که مثل همه درخواست‌ها
  مستقیماً برای مسئول انبار (تخصیص انبار مشترک در انتظار) ارسال می‌شود
- درخواست ارسال‌شده تا قبل از اقدام مسئول انبار قابل ویرایش/حذف قلم است
"""
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from purchases.models import (
    Cafe, Category, Unit, Product, ProductBrand, PurchaseRequest,
    PurchaseItem, OfficerAssignment, PurchaseDraft, PurchaseDraftItem,
)

User = get_user_model()


class WarehousePurchaseDraftTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafe = Cafe.objects.create(name='کافه تست', code='testcafe', is_active=True)
        self.wo = User.objects.create_user(
            username='wo1', password='testpass123', role='warehouse', cafe=self.cafe)
        self.ceo = User.objects.create_user(
            username='ceo_test', password='testpass123', role='ceo', cafe=self.cafe)
        self.requester = User.objects.create_user(
            username='req1', password='testpass123', role='requester', cafe=self.cafe)

        self.category = Category.objects.create(name='دسته تست')
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.product = Product.objects.create(
            name='محصول تست', category=self.category, unit=self.unit, cafe=self.cafe)
        self.brand = ProductBrand.objects.create(
            product=self.product, brand_name='برند الف', trade_unit='قوطی')

        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        # درخواست از درخواست‌دهنده + قلم (در انبار موجود نیست → برای انبار ارجاع می‌شود)
        self.pr = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending')
        self.item = PurchaseItem.objects.create(
            request=self.pr, product=self.product, name='محصول تست',
            quantity=5, unit='kg', description='تست')

    def _login_as_warehouse(self):
        self.client.login(username='wo1', password='testpass123')

    def test_dashboard_to_purchase_creates_draft_and_redirects(self):
        """ارجاع به خرید در داشبورد انبار → قلم به پیش‌نویس می‌رود و کاربر در همان داشبورد می‌ماند"""
        self._login_as_warehouse()
        OfficerAssignment.objects.create(
            item=self.item, target_role='warehouse', assigned_to=self.wo)
        dash_url = reverse('warehouse_officer_dashboard', args=['testcafe'])
        resp = self.client.post(dash_url, {'action': 'to_purchase', 'item_id': self.item.id})
        self.assertEqual(PurchaseDraft.objects.count(), 1)
        self.assertEqual(PurchaseDraftItem.objects.count(), 1)
        d_item = PurchaseDraftItem.objects.first()
        self.assertEqual(d_item.source_item, self.item)
        self.assertEqual(d_item.quantity, 5)
        # کاربر به صفحه جدید منتقل نمی‌شود — در همان داشبورد می‌ماند
        # (ریدایرکت POST بدون delivery به ‎?delivery=<امروز> هم هست؛ follow تا مقصد نهایی)
        resp = self.client.post(dash_url, {'action': 'to_purchase', 'item_id': self.item.id}, follow=True)
        self.assertEqual(resp.status_code, 200)
        # وضعیت تخصیص = returned
        self.item.refresh_from_db()
        self.assertEqual(self.item.officer_assignments.first().status, 'returned')

    def test_draft_page_shows_items(self):
        self._login_as_warehouse()
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name='محصول تست', quantity=3, unit='kg')
        resp = self.client.get(reverse('warehouse_purchase_draft', args=['testcafe']))
        self.assertContains(resp, 'محصول تست')

    def test_add_item_to_draft(self):
        self._login_as_warehouse()
        resp = self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']), {
            'action': 'add_item', 'product_id': str(self.product.id),
            'brand_id': str(self.brand.id), 'new_qty': '7', 'unit': 'قوطی',
        })
        self.assertEqual(PurchaseDraftItem.objects.count(), 1)
        d_item = PurchaseDraftItem.objects.first()
        self.assertEqual(d_item.quantity, 7)
        self.assertEqual(d_item.product_brand, self.brand)
        self.assertEqual(d_item.unit, 'قوطی')

    def test_edit_and_delete_draft_item(self):
        self._login_as_warehouse()
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        d_item = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name='محصول تست', quantity=3, unit='kg')
        # ویرایش
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'edit_item', 'item_id': d_item.id, 'quantity': '9'})
        d_item.refresh_from_db()
        self.assertEqual(d_item.quantity, 9)
        # حذف
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'delete_item', 'item_id': d_item.id})
        self.assertEqual(PurchaseDraftItem.objects.count(), 0)
        self.assertEqual(PurchaseDraft.objects.count(), 0)  # پیش‌نویس خالی حذف شد

    def test_send_creates_request_routed_to_warehouse(self):
        self._login_as_warehouse()
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, product_brand=self.brand,
            name='محصول تست', quantity=5, unit='kg')
        resp = self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']), {
            'action': 'send', 'delivery_date': '2026-09-15',
        })
        self.assertRedirects(resp, reverse('warehouse_purchase_draft', args=['testcafe']))
        # درخواست ساخته شد و مستقیماً برای مسئول انبار ارسال شد
        self.assertEqual(PurchaseRequest.objects.count(), 2)  # اصلی + جدید
        new_req = PurchaseRequest.objects.filter(source='warehouse_purchase').first()
        self.assertIsNotNone(new_req)
        self.assertEqual(new_req.status, 'pending')
        self.assertEqual(new_req.user, self.wo)
        self.assertEqual(new_req.items.count(), 1)
        self.assertEqual(new_req.items.first().quantity, 5)
        # گزینه «تحویل فوری» حذف شده: تاریخ ارسالی ثبت می‌شود و فوری همیشه خالی است
        self.assertEqual(new_req.delivery_date.isoformat(), '2026-09-15')
        self.assertFalse(new_req.delivery_immediate)
        # تخصیص انبار مشترکِ در انتظار برای قلم ساخته شد
        a = new_req.items.first().officer_assignments.first()
        self.assertIsNotNone(a)
        self.assertEqual(a.target_role, 'warehouse')
        self.assertIsNone(a.assigned_to)
        self.assertEqual(a.status, 'pending')
        # پیش‌نویس حذف شد
        self.assertEqual(PurchaseDraft.objects.count(), 0)
        self.assertEqual(PurchaseDraftItem.objects.count(), 0)

    def test_send_empty_draft_rejected(self):
        self._login_as_warehouse()
        resp = self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                                {'action': 'send'})
        self.assertEqual(PurchaseRequest.objects.filter(source='warehouse_purchase').count(), 0)

    def test_edit_and_delete_sent_request_items(self):
        """درخواست ارسال‌شده تا قبل از اقدام مسئول انبار قابل ویرایش/حذف قلم است"""
        self._login_as_warehouse()
        new_req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending',
            source='warehouse_purchase')
        sent_item = PurchaseItem.objects.create(
            request=new_req, product=self.product, name='محصول تست', quantity=4, unit='kg')
        # تخصیص انبار مشترک در انتظار (مثل خروجی «ارسال» واقعی)
        OfficerAssignment.objects.create(
            item=sent_item, target_role='warehouse', assigned_to=None, status='pending')
        # ویرایش مقدار
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'edit_sent', 'request_id': new_req.id,
                          'item_id': sent_item.id, 'quantity': '12'})
        sent_item.refresh_from_db()
        self.assertEqual(sent_item.quantity, 12)
        # حذف قلم → درخواست خالی حذف می‌شود
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'delete_sent', 'request_id': new_req.id,
                          'item_id': sent_item.id})
        self.assertEqual(PurchaseRequest.objects.filter(id=new_req.id).count(), 0)

    def test_cannot_edit_actioned_request(self):
        """بعد از اقدام روی قلم (تخصیص purchased) دیگر قابل ویرایش نیست"""
        self._login_as_warehouse()
        req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending', source='warehouse_purchase')
        sent_item = PurchaseItem.objects.create(
            request=req, product=self.product, name='محصول تست', quantity=4, unit='kg')
        OfficerAssignment.objects.create(
            item=sent_item, target_role='warehouse', assigned_to=None, status='purchased')
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'edit_sent', 'request_id': req.id,
                          'item_id': sent_item.id, 'quantity': '99'})
        sent_item.refresh_from_db()
        self.assertEqual(sent_item.quantity, 4)  # تغییری نکرد

    def test_requester_role_cannot_access(self):
        self.client.login(username='req1', password='testpass123')
        resp = self.client.get(reverse('warehouse_purchase_draft', args=['testcafe']))
        self.assertEqual(resp.status_code, 302)  # ریدایرکت (به login یا داشبورد خرید)
