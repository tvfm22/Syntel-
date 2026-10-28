"""سنگ‌آزمایش: قفلِ تغییر وضعیتِ کالای ثبت‌شده در لیست خرید و ارسال‌شده به مدیرعامل.

درخواست کاربر (2026-09-10): «وقتی کالایی برای لیست خرید ثبت شده و برای مدیر عامل
ارسال شده دیگر قابل تغییر وضعیت در داشبورد مسئول انبار نباشد.»

جریان: «ارجاع به خرید» در داشبورد → قلم در پیش‌نویس «درخواست خرید» → «ارسال» →
درخواست خریدِ مسئول انبار (source=warehouse_purchase) در انتظار تایید مدیرعامل.
از لحظهٔ ارسال، قلمِ مبدأ در داشبورد انبار قفل می‌شود (بدون دکمهٔ بازگشایی و با
گارد سرور روی همهٔ اکشن‌های تغییر وضعیت)؛ ردِ مدیرعامل قفل را باز می‌کند.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Product, PurchaseDraft, PurchaseItem, PurchaseRequest, OfficerAssignment,
    StockItem, StockTransaction, Unit, Warehouse,
)

User = get_user_model()


class SentToCeoLockTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.officer = User.objects.create_user(
            username='wo_lock', password='testpass123', role='warehouse')
        self.ceo = User.objects.create_user(
            username='ceo_lock', password='testpass123', role='manager')

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()
        self.officer.cafe = self.cafe
        self.officer.save()
        self.ceo.cafe = self.cafe
        self.ceo.save()

        self.warehouse = Warehouse.objects.create(name='WH', cafe=self.cafe)
        self.product = Product.objects.create(name='Sugar', unit=self.unit, cafe=self.cafe)
        self.stock = StockItem.objects.create(
            product=self.product, warehouse=self.warehouse,
            quantity=Decimal('50'), unit=self.unit)

        # درخواستِ مدیرعامل با قلمی که در انبار موجودی دارد (ثبت‌شدهٔ امروز)
        self.request = PurchaseRequest.objects.create(
            user=self.ceo, cafe=self.cafe, status='pending',
            delivery_date=timezone.localtime(timezone.now()).date())
        self.item = PurchaseItem.objects.create(
            request=self.request, product=self.product, name='Sugar',
            quantity=Decimal('5'), unit='kg')

        self.client.force_login(self.officer)
        self.dash_url = reverse('warehouse_officer_dashboard', args=['main'])
        self.draft_url = reverse('warehouse_purchase_draft', args=['main'])
        self.today = timezone.localtime(timezone.now()).date().isoformat()

    # ── کمک‌کارها ──────────────────────────────────────────────────────────

    def _refer(self):
        """ارجاع به خرید (بدون ارسال) — قلم به پیش‌نویس می‌رود"""
        return self.client.post(self.dash_url, {'action': 'to_purchase', 'item_id': self.item.id})

    def _send(self):
        """ارسال پیش‌نویس برای تایید مدیرعامل"""
        return self.client.post(self.draft_url, {'action': 'send'})

    def _refer_and_send(self):
        self._refer()
        resp = self._send()
        sent = PurchaseRequest.objects.filter(source='warehouse_purchase').first()
        self.assertIsNotNone(sent, 'درخواست خریدِ ارسال‌شده ساخته نشد')
        return sent

    def _done_entry(self):
        resp = self.client.get(self.dash_url + '?day=' + self.today)
        self.assertEqual(resp.status_code, 200)
        for card in resp.context['request_cards']:
            for d in card['done']:
                if d['item'].id == self.item.id:
                    return resp, d
        return resp, None

    def _assignment(self):
        return self.item.officer_assignments.filter(target_role='warehouse').first()

    # ── تست‌ها ─────────────────────────────────────────────────────────────

    def test_sent_item_is_locked_on_dashboard(self):
        """بعد از ارسال: کارت قلم قفل است — بدون دکمهٔ بازگشایی و با برچسب ارسال به مدیرعامل"""
        self._refer_and_send()
        resp, entry = self._done_entry()
        self.assertIsNotNone(entry, 'قلم باید در بخش «ثبت‌شده»ی داشبورد باشد')
        self.assertTrue(entry['locked'], 'قلمِ ارسال‌شده باید قفل باشد')
        self.assertEqual(entry['locked_request_id'],
                         PurchaseRequest.objects.get(source='warehouse_purchase').id)
        body = resp.content.decode('utf-8')
        self.assertIn('ارسال به مدیرعامل', body)
        self.assertIn('done-row done-row-locked', body)
        self.assertNotIn(f'id="reopen-form-{self.item.id}"', body,
                         'فرم بازگشایی نباید برای قلم قفل‌شده رندر شود')

    def test_reopen_blocked_after_send(self):
        """اکشن بازگشایی روی قلمِ ارسال‌شده انجام نمی‌شود (وضعیت/تخصیص دست‌نخورده)"""
        self._refer_and_send()
        resp = self.client.post(self.dash_url, {'action': 'reopen', 'item_id': self.item.id},
                                follow=True)
        self.assertContains(resp, 'قابل تغییر وضعیت نیست')
        assign = self._assignment()
        self.assertIsNotNone(assign)
        self.assertEqual(assign.status, 'returned', 'تخصیص نباید پاک/عوض شود')
        self.assertEqual(PurchaseDraft.objects.count(), 0)
        # هنوز در بخش ثبت‌شده و قفل است
        _resp, entry = self._done_entry()
        self.assertIsNotNone(entry)
        self.assertTrue(entry['locked'])

    def test_fulfill_and_not_in_stock_blocked_after_send(self):
        """تامین از انبار / عدم موجودی هم روی قلمِ ارسال‌شده انجام نمی‌شود"""
        self._refer_and_send()
        self.client.post(self.dash_url, {
            'action': 'fulfill', 'item_id': self.item.id,
            'stock_item_id': str(self.stock.id), 'qty': '5'}, follow=True)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('50'), 'موجودی نباید کم شود')
        self.assertEqual(StockTransaction.objects.filter(reference_id=self.item.id).count(), 0)
        self.assertEqual(self._assignment().status, 'returned')

        self.client.post(self.dash_url, {'action': 'not_in_stock', 'item_id': self.item.id},
                         follow=True)
        self.assertEqual(self._assignment().status, 'returned',
                         'عدم موجودی نباید روی قلمِ ارسال‌شده ثبت شود')

    def test_reopen_works_before_send(self):
        """قبل از ارسال (فقط ارجاع به پیش‌نویس) بازگشایی همچنان کار می‌کند — رفتار درخواست قبلی"""
        self._refer()
        self.assertEqual(PurchaseDraft.objects.count(), 1)
        # روی صفحه هم دکمه/فرم بازگشایی برای قلمِ ارجاعیِ ارسال‌نشده هست
        page = self.client.get(self.dash_url + '?day=' + self.today)
        self.assertContains(page, f'id="reopen-form-{self.item.id}"')
        self.assertContains(page, f'onclick="reopenItem({self.item.id})"')
        # (توجه: رشتهٔ CSS «done-row-locked» همیشه در صفحه هست؛ سطرِ قفل است که نباید باشد)
        self.assertNotContains(page, 'done-row done-row-locked')
        resp = self.client.post(self.dash_url, {
            'action': 'reopen', 'item_id': self.item.id,
            'reason': 'تست بازگشایی'}, follow=True)
        self.assertContains(resp, 'بازگشایی شد')
        self.assertEqual(self._assignment().status, 'reopened',
                         'تخصیص باید با وضعیت بازگشایی‌شده نگه داشته شود')
        self.assertEqual(PurchaseDraft.objects.count(), 0, 'قلم از پیش‌نویس هم حذف شود')

    def test_lock_releases_after_ceo_reject(self):
        """ردِ مدیرعامل قفل را باز می‌کند: قلم دوباره قابل بازگشایی است"""
        sent = self._refer_and_send()

        self.client.force_login(self.ceo)
        self.client.post(
            reverse('manager_reject_request', args=[self.cafe.code, sent.id]),
            {'rejection_reason': 'تست رد'})
        sent.refresh_from_db()
        self.assertEqual(sent.status, 'rejected')

        self.client.force_login(self.officer)
        _resp, entry = self._done_entry()
        self.assertIsNotNone(entry)
        self.assertFalse(entry['locked'], 'بعد از رد مدیرعامل قلم باید آزاد باشد')
        resp = self.client.post(self.dash_url, {
            'action': 'reopen', 'item_id': self.item.id,
            'reason': 'تست بازگشایی پس از رد'}, follow=True)
        self.assertContains(resp, 'بازگشایی شد')
        self.assertEqual(self._assignment().status, 'reopened')
