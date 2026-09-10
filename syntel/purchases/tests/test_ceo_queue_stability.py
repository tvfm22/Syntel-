"""صف مدیرعامل پایدار است — درخواست با ویرایش/حذف قلم و تغییر وضعیت ناپدید نمی‌شود

قاعدهٔ کاربر: «چیزی از داشبورد مدیرعامل ناپدید نشه در تغییر وضعیت یا هرچیزی»
- حذف آخرین قلم یک درخواست، درخواست را حذف نمی‌کند؛ درخواست بدون قلم در صف می‌ماند
  تا خودِ مدیرعامل آن را تایید/رد کند.
- `_sync_request_status` درخواستِ در صف مدیرعامل را از صف بیرون نمی‌برد.
- صفحهٔ «درخواست‌های در انتظار» درخواست بدون قلم را با برچسب «بدون قلم» نشان می‌دهد.
"""
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from purchases.models import (
    Cafe, Category, Unit, Product, PurchaseRequest, PurchaseItem, OfficerAssignment,
)
from purchases.views import _sync_request_status

User = get_user_model()


class CeoQueueStabilityTests(TestCase):
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

        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

    def _login_ceo(self):
        self.client.login(username='ceo_test', password='testpass123')

    def _login_wo(self):
        self.client.login(username='wo1', password='testpass123')

    def _queue_request(self, source='warehouse_purchase', qty=4):
        req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending_manager_approval', source=source)
        item = PurchaseItem.objects.create(
            request=req, product=self.product, name='محصول تست', quantity=qty, unit='kg')
        return req, item

    # ── حذف آخرین قلم ────────────────────────────────────────────────────────

    def test_delete_sent_last_item_keeps_request_in_queue(self):
        """حذف آخرین قلم در صفحهٔ «درخواست خرید» انبار، درخواست را حذف نمی‌کند"""
        self._login_wo()
        req, item = self._queue_request()
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'delete_sent', 'request_id': req.id, 'item_id': item.id})
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending_manager_approval')
        self.assertEqual(req.items.count(), 0)
        # و همچنان در صف مدیرعامل دیده می‌شود
        self._login_ceo()
        resp = self.client.get(reverse('manager_pending_requests', args=['testcafe']))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['requests'].values_list('id', flat=True)), [req.id])

    def test_delete_item_last_item_of_queue_request_keeps_request(self):
        """حذف آخرین قلم از صفحهٔ «درخواست کالا»، درخواستِ صف مدیرعامل را حذف نمی‌کند"""
        self._login_ceo()
        req, item = self._queue_request(source='requester')
        self.client.post(reverse('all_departments_requests', args=['testcafe']),
                         {'action': 'delete_item', 'item_id': item.id})
        self.assertEqual(PurchaseRequest.objects.filter(id=req.id).count(), 1)
        self.assertEqual(PurchaseItem.objects.filter(request=req).count(), 0)

    def test_delete_item_last_item_of_non_queue_request_still_removes_it(self):
        """درخواستِ خارج از صف مدیرعامل با خالی شدن مثل قبل حذف می‌شود"""
        self._login_ceo()
        req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending', source='requester')
        item = PurchaseItem.objects.create(
            request=req, product=self.product, name='محصول تست', quantity=4, unit='kg')
        self.client.post(reverse('all_departments_requests', args=['testcafe']),
                         {'action': 'delete_item', 'item_id': item.id})
        self.assertEqual(PurchaseRequest.objects.filter(id=req.id).count(), 0)

    # ── همگام‌سازی وضعیت ─────────────────────────────────────────────────────

    def test_sync_status_keeps_queue_request_in_queue(self):
        """ویرایش اقلام، درخواستِ در صف مدیرعامل را به وضعیت دیگر نمی‌برد (keep_queue)"""
        req, item = self._queue_request(source='requester')
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', assigned_to=self.wo, status='pending')
        _sync_request_status(req, keep_queue=True)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending_manager_approval')

    def test_sync_status_default_still_routes_direct_to_warehouse(self):
        """مسیرهای گردش‌کار عادی (ارسال مستقیم به انبار) بدون keep_queue مثل قبل کار می‌کنند"""
        req, item = self._queue_request(source='requester')
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', assigned_to=self.wo, status='pending')
        _sync_request_status(req)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_sync_status_still_advances_approved_request(self):
        """گارد جدید رفتار قبلی درخواست‌های خارج از صف را تغییر نمی‌دهد"""
        req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending', source='requester')
        item = PurchaseItem.objects.create(
            request=req, product=self.product, name='محصول تست', quantity=4, unit='kg')
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', assigned_to=self.wo, status='fulfilled')
        _sync_request_status(req)
        req.refresh_from_db()
        self.assertEqual(req.status, 'purchased')

    # ── نمایش در صف ──────────────────────────────────────────────────────────

    def test_queue_page_shows_empty_request_with_badge(self):
        """درخواست بدون قلم در صف مدیرعامل با برچسب «بدون قلم» رندر می‌شود"""
        req, item = self._queue_request()
        item.delete()
        self._login_ceo()
        resp = self.client.get(reverse('manager_pending_requests', args=['testcafe']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'بدون قلم')
        self.assertContains(resp, f'#{req.id}')

    def test_ceo_approve_empty_request_leaves_queue_by_ceo_action(self):
        """مدیرعامل می‌تواند درخواست خالی را رد کند — تنها راه خروج از صف، اقدام خودِ مدیرعامل است"""
        req, item = self._queue_request()
        item.delete()
        self._login_ceo()
        self.client.post(
            reverse('manager_reject_request', args=['testcafe', req.id]),
            {'rejection_reason': 'بدون قلم'})
        req.refresh_from_db()
        self.assertEqual(req.status, 'rejected')
