"""تست موقت: بخش «کالاهای دریافتی من» — مشاهده و تایید دریافت کالا توسط درخواست‌دهنده.

نکتهٔ کلیدی: «تایید دریافت» صرفاً اعلامِ وصول است و هیچ اثر انبار/مالی ندارد؛
کسرِ موجودی همان لحظه‌ای است که مسئول انبار کالا را تامین می‌کند و به تایید
درخواست‌دهنده وابسته نیست. این تست‌ها همان را تضمین می‌کنند.
"""
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from purchases.models import (
    User, Cafe, Category, Product, Unit,
    Warehouse, StockItem, StockTransaction,
    PurchaseRequest, PurchaseItem, OfficerAssignment,
)


class ReceivedItemsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(name='کافه تست', code='rcvcafe')
        cls.unit = Unit.objects.create(name='کیلوگرم', symbol='kg')
        cls.cat = Category.objects.create(name='لبنیات', cafe=cls.cafe)
        cls.product = Product.objects.create(
            name='شیر', category=cls.cat, unit=cls.unit, min_stock=0, cafe=cls.cafe,
        )
        cls.warehouse = Warehouse.objects.create(name='انبار اصلی', code='WH1', cafe=cls.cafe)
        cls.stock = StockItem.objects.create(
            product=cls.product, warehouse=cls.warehouse, unit=cls.unit,
            quantity=100, min_quantity=0,
        )

        cls.requester = User.objects.create_user(
            username='rcv_req', password='testpass123',
            role='requester', is_active=True, cafe=cls.cafe,
        )
        cls.other = User.objects.create_user(
            username='rcv_other', password='testpass123',
            role='requester', is_active=True, cafe=cls.cafe,
        )
        cls.officer = User.objects.create_user(
            username='rcv_wo', password='testpass123',
            role='warehouse', is_active=True, cafe=cls.cafe,
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    def _item(self, user, qty=3, name='شیر'):
        req = PurchaseRequest.objects.create(user=user, cafe=self.cafe)
        return PurchaseItem.objects.create(
            request=req, product=self.product, name=name,
            quantity=qty, unit='کیلوگرم',
        )

    def _fulfill(self, item, base_qty=Decimal('5'), status='fulfilled'):
        return OfficerAssignment.objects.create(
            item=item, target_role='warehouse', status=status,
            stock_item=self.stock, supplied_quantity=base_qty,
            assigned_to=self.officer,
        )

    def login(self, username='rcv_req'):
        c = Client()
        c.login(username=username, password='testpass123')
        self.client = c
        return c

    def url(self):
        return reverse('my_received_items', args=[self.cafe.code])

    # ── نمایش ────────────────────────────────────────────────────────────────
    def test_fulfilled_item_shown_with_delivered_qty(self):
        item = self._item(self.requester, qty=3)
        self._fulfill(item, base_qty=Decimal('5'))
        c = self.login()
        r = c.get(self.url())
        self.assertEqual(r.status_code, 200)
        content = r.content.decode('utf-8')
        self.assertIn('کالاهای دریافتی من', content)
        self.assertIn('شیر', content)
        self.assertIn('درخواستی: <b>3 کیلوگرم</b>', content)
        self.assertIn('تحویل‌شده: 5 کیلوگرم', content)
        self.assertIn('انبار اصلی', content)
        self.assertIn('تایید دریافت', content)
        self.assertIn(f'value="{item.id}"', content)

    def test_pending_assignment_not_listed(self):
        """قلمی که مسئول انبار هنوز تامینش نکرده، «دریافتی» نیست."""
        item = self._item(self.requester, name='نان')
        self._fulfill(item, status='pending')
        c = self.login()
        content = c.get(self.url()).content.decode('utf-8')
        self.assertNotIn('نان', content)
        self.assertIn('هنوز کالایی تحویل نگرفته‌اید', content)

    def test_not_in_stock_assignment_not_listed(self):
        item = self._item(self.requester, name='پنیر')
        self._fulfill(item, status='not_in_stock')
        c = self.login()
        content = c.get(self.url()).content.decode('utf-8')
        self.assertNotIn('پنیر', content)

    def test_other_users_items_not_listed(self):
        self._fulfill(self._item(self.other, name='کره'))
        c = self.login()
        content = c.get(self.url()).content.decode('utf-8')
        self.assertNotIn('کره', content)
        self.assertIn('هنوز کالایی تحویل نگرفته‌اید', content)

    def test_multiple_deliveries_all_shown(self):
        """یک قلم که در چند نوبت تحویل شده — همهٔ مقادیر نمایش داده می‌شوند."""
        item = self._item(self.requester)
        self._fulfill(item, base_qty=Decimal('4'))
        self._fulfill(item, base_qty=Decimal('3'))
        c = self.login()
        content = c.get(self.url()).content.decode('utf-8')
        self.assertIn('تحویل‌شده: 4 کیلوگرم', content)
        self.assertIn('تحویل‌شده: 3 کیلوگرم', content)

    # ── تایید دریافت ─────────────────────────────────────────────────────────
    def test_confirm_single_item(self):
        item = self._item(self.requester)
        self._fulfill(item)
        c = self.login()
        r = c.post(self.url(), {'action': 'confirm', 'item_id': item.id})
        self.assertRedirects(r, self.url())

        item.refresh_from_db()
        self.assertIsNotNone(item.received_confirmed_at)
        self.assertEqual(item.received_confirmed_by_id, self.requester.id)

        page = c.get(self.url()).content.decode('utf-8')
        self.assertIn('تاییدشده', page)
        self.assertIn('همهٔ اقلام دریافتی تایید شده‌اند', page)

    def test_confirm_has_no_stock_effect(self):
        """تایید دریافت نه موجودی را تغییر می‌دهد و نه تراکنشی می‌سازد و نه تخصیص را دست می‌زند."""
        item = self._item(self.requester)
        self._fulfill(item, base_qty=Decimal('5'))
        # کسرِ موجودی همان‌جا که انبار تامین کرد انجام شده (اینجا دستی شبیه‌سازی می‌شود)
        self.stock.quantity = Decimal('95')
        self.stock.save(update_fields=['quantity'])
        tx_before = StockTransaction.objects.count()

        c = self.login()
        c.post(self.url(), {'action': 'confirm', 'item_id': item.id})

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('95'))
        self.assertEqual(StockTransaction.objects.count(), tx_before)
        self.assertEqual(
            OfficerAssignment.objects.get(item=item).status, 'fulfilled',
        )

    def test_confirm_all_confirms_every_pending(self):
        a = self._item(self.requester, name='شیر')
        b = self._item(self.requester, name='نان')
        self._fulfill(a)
        self._fulfill(b)
        c = self.login()
        r = c.post(self.url(), {'action': 'confirm_all'})
        self.assertRedirects(r, self.url())
        for it in (a, b):
            it.refresh_from_db()
            self.assertIsNotNone(it.received_confirmed_at)
        page = c.get(self.url()).content.decode('utf-8')
        self.assertIn('همهٔ اقلام دریافتی تایید شده‌اند', page)

    def test_cannot_confirm_unfulfilled_item_id(self):
        """آی‌دیِ قلمی که تحویل نشده (یا مال دیگری است) نباید تایید شود."""
        mine = self._item(self.requester, name='شیر')
        not_fulfilled = self._item(self.requester, name='نان')
        others = self._item(self.other, name='کره')
        self._fulfill(others)

        c = self.login()
        for target in (not_fulfilled, others):
            r = c.post(self.url(), {'action': 'confirm', 'item_id': target.id}, follow=True)
            self.assertContains(r, 'قلمی برای تایید دریافت پیدا نشد')
            target.refresh_from_db()
            self.assertIsNone(target.received_confirmed_at)
        mine.refresh_from_db()
        self.assertIsNone(mine.received_confirmed_at)

    def test_already_confirmed_stays_confirmed(self):
        """تایید دوباره بی‌اثر است و تاریخِ تاییدِ اول را عوض نمی‌کند."""
        item = self._item(self.requester)
        self._fulfill(item)
        c = self.login()
        c.post(self.url(), {'action': 'confirm', 'item_id': item.id})
        item.refresh_from_db()
        first = item.received_confirmed_at

        c.post(self.url(), {'action': 'confirm', 'item_id': item.id})
        item.refresh_from_db()
        self.assertEqual(item.received_confirmed_at, first)

    def test_reopen_removes_item_from_received_list(self):
        """بازگشاییِ قلم توسط مسئول انبار، تخصیص‌ها را پاک می‌کند → از «دریافتی» حذف می‌شود."""
        item = self._item(self.requester)
        assignment = self._fulfill(item)
        c = self.login()
        self.assertIn('شیر', c.get(self.url()).content.decode('utf-8'))

        assignment.delete()  # همان کاری که reopen انجام می‌دهد
        content = c.get(self.url()).content.decode('utf-8')
        self.assertIn('هنوز کالایی تحویل نگرفته‌اید', content)

    # ── داشبورد موبایل و منوی کنار ───────────────────────────────────────────
    def test_mobile_dashboard_entry_with_pending_badge(self):
        item = self._item(self.requester)
        self._fulfill(item)
        c = self.login()
        content = c.get(reverse('mobile_dashboard', args=[self.cafe.code])).content.decode('utf-8')
        self.assertIn('کالاهای دریافتی من', content)
        self.assertIn(self.url(), content)  # لینک منو
        self.assertIn('class="menu-badge"', content)
        self.assertIn('1 قلم در انتظار تایید دریافت', content)
        self.assertIn('ابتدا تایید کنید؛ تا آن زمان درخواست جدید ثبت نمیشود', content)

    def test_mobile_dashboard_badge_hidden_when_nothing_pending(self):
        c = self.login()
        content = c.get(reverse('mobile_dashboard', args=[self.cafe.code])).content.decode('utf-8')
        self.assertIn('کالاهای دریافتی من', content)
        self.assertNotIn('class="menu-badge"', content)
        self.assertIn('مشاهده و تایید کالاهای تحویل‌شده', content)

    def test_sidebar_nav_entry_present_and_active(self):
        self._fulfill(self._item(self.requester))
        c = self.login()
        content = c.get(self.url()).content.decode('utf-8')
        self.assertIn('nav-item active', content)
        self.assertIn('کالاهای دریافتی من', content)
