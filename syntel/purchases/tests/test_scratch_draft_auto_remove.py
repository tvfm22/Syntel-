"""سنگ‌آزمایش: حذف خودکارِ قلمِ ارجاع‌شده از پیش‌نویس «درخواست خرید» پس از بازنگری

قانون: وقتی مسئول انبار در داشبورد وضعیتِ قلمِ «ارجاع به خرید» (returned) را عوض
می‌کند — بازگشایی (reopen)، تامین از انبار یا «عدم موجودی» — آن قلم دیگر برای
خرید ارجاع نیست؛ نسخهٔ کپی‌اش از اقلامِ آمادهٔ ارسالِ خرید حذف می‌شود و اگر
پیش‌نویس خالی ماند خودِ پیش‌نویس هم پاک می‌شود. ارجاعِ دوباره از همان داشبورد،
یک کپیِ تازه (نه دوتایی) می‌سازد.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Unit, Product, ProductBrand, PurchaseDraft, PurchaseDraftItem,
    PurchaseItem, PurchaseRequest, OfficerAssignment,
    User,
)


class DraftAutoRemoveAfterReviewTest(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(code='arm', name='کافه خودحذفی', is_active=True)
        self.unit = Unit.objects.create(name='کیلوگرم', symbol='kg')
        self.officer = User.objects.create_user(
            username='arm_wh', password='x', role='warehouse', cafe=self.cafe)

        # دو محصول برای سنجشِ «پیش‌نویس با قلم‌های دیگر خالی نشود»
        self.product = Product.objects.create(
            name='چای', cafe=self.cafe, unit=self.unit,
            shelf_life_days=90, is_active=True, is_complete=True)
        self.brand = ProductBrand.objects.create(
            product=self.product, brand_name='احمد', trade_unit='کارتن', conversion_rate=1)

        today = timezone.localtime(timezone.now()).date()
        self.request1 = PurchaseRequest.objects.create(
            user=self.officer, cafe=self.cafe,
            status='pending_manager_approval', source='requester',
            delivery_date=today)
        self.item = PurchaseItem.objects.create(
            request=self.request1, product=self.product, name='چای',
            quantity=2, unit='کارتن')

        self.dash_url = reverse(
            'warehouse_officer_dashboard', kwargs={'cafe_code': self.cafe.code})

        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session.save()
        self.client.force_login(self.officer)

    def _refer_and_open(self):
        """وضعیت «ارجاع به خرید»: تخصیص returned + کپی در پیش‌نویس (دقیقاً مثل دکمه داشبورد)"""
        OfficerAssignment.objects.create(
            item=self.item, target_role='warehouse', status='returned',
            assigned_to=self.officer, assigned_by=self.officer)
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.officer)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, product_brand=self.brand,
            name='چای', quantity=2, unit='کارتن', source_item=self.item)
        return draft

    def test_reopen_purges_referred_item_and_empty_draft(self):
        """بازگشایی: کپیِ ارجاعی حذف، تخصیص با وضعیت reopened می‌ماند، پیش‌نویس خالی حذف شود"""
        draft = self._refer_and_open()
        resp = self.client.post(
            self.dash_url, {'action': 'reopen', 'item_id': str(self.item.id),
                            'reason': 'تست بازگشایی'})
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(PurchaseDraftItem.objects.filter(source_item=self.item).count(), 0)
        self.assertFalse(PurchaseDraft.objects.filter(id=draft.id).exists(),
                         'پیش‌نویسی که با حذفِ این قلم خالی شده باید پاک شود')
        assignment = self.item.officer_assignments.get()
        self.assertEqual(assignment.status, 'reopened',
                         'تخصیصِ بستهٔ (returned) باید با وضعیت بازگشایی‌شده بماند')

    def test_reopen_keeps_draft_with_other_items(self):
        """بازگشایی: فقط کپیِ همین قلم حذف شود؛ پیش‌نویس با قلم‌های دیگر سر جایش بماند"""
        draft = self._refer_and_open()
        PurchaseDraftItem.objects.create(
            draft=draft, name='دستمال کاغذی', quantity=1, unit='بسته')
        self.client.post(
            self.dash_url, {'action': 'reopen', 'item_id': str(self.item.id),
                            'reason': 'تست بازگشایی'})

        draft.refresh_from_db()
        self.assertEqual(draft.items.count(), 1)
        self.assertEqual(draft.items.first().name, 'دستمال کاغذی')
        self.assertFalse(PurchaseDraftItem.objects.filter(source_item=self.item).exists())

    def test_referral_again_after_reopen_creates_fresh_copy(self):
        """ارجاعِ دوباره بعد از بازگشایی: یک کپیِ تازه (نه دوتایی) ساخته شود"""
        self._refer_and_open()
        self.client.post(
            self.dash_url, {'action': 'reopen', 'item_id': str(self.item.id),
                            'reason': 'تست بازگشایی'})
        resp = self.client.post(
            self.dash_url, {'action': 'to_purchase', 'item_id': str(self.item.id)})
        self.assertEqual(resp.status_code, 302)

        copies = PurchaseDraftItem.objects.filter(source_item=self.item)
        self.assertEqual(copies.count(), 1, 'ارجاع دوباره فقط یک کپی تازه بسازد')
        self.assertEqual(copies.first().draft.items.count(), 1)
        self.assertEqual(self.item.officer_assignments.first().status, 'returned')

    def test_not_in_stock_removes_referred_copy(self):
        """«عدم موجودی» روی قلمِ ارجاعی هم باید کپیِ پیش‌نویس را حذف کند"""
        draft = self._refer_and_open()
        resp = self.client.post(
            self.dash_url, {'action': 'not_in_stock', 'item_id': str(self.item.id)})
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(PurchaseDraftItem.objects.filter(source_item=self.item).count(), 0)
        self.assertFalse(PurchaseDraft.objects.filter(id=draft.id).exists())

    def test_reopen_without_reason_succeeds_for_referred_item(self):
        """بازگشایی قلم ارجاع به خرید بدون وارد کردن دلیل موفقیت‌آمیز است و کالا به لیست برمی‌گردد"""
        draft = self._refer_and_open()
        resp = self.client.post(
            self.dash_url, {'action': 'reopen', 'item_id': str(self.item.id),
                            'reason': ''})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PurchaseDraftItem.objects.filter(source_item=self.item).count(), 0)
        self.assertFalse(PurchaseDraft.objects.filter(id=draft.id).exists())
        assignment = self.item.officer_assignments.get()
        self.assertEqual(assignment.status, 'reopened')

    def test_delete_draft_item_reopens_source_item(self):
        """حذف قلم ارجاعی از پیش‌نویس خرید، آن را به لیست انبار بازمی‌گرداند بدون نیاز به دلیل"""
        draft = self._refer_and_open()
        d_item = draft.items.first()
        draft_url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})
        resp = self.client.post(draft_url, {'action': 'delete_item', 'item_id': str(d_item.id)})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PurchaseDraftItem.objects.count(), 0)
        self.assertFalse(PurchaseDraft.objects.filter(id=draft.id).exists())
        assignment = self.item.officer_assignments.get()
        self.assertEqual(assignment.status, 'reopened')

    def test_warehouse_dashboard_tag_text_no_ceo_mention_for_referral(self):
        """در جدول کالاهای پردازش‌شده، برای ارجاع به خرید نباید 'برگشت به مدیرعامل' نوشته شود"""
        self._refer_and_open()
        resp = self.client.get(self.dash_url, follow=True)
        content = resp.content.decode('utf-8')
        self.assertIn('ارجاع به خرید', content)
        self.assertNotIn('ارجاع به خرید — برگشت به مدیرعامل', content)

    def test_reopen_not_in_stock_without_reason_succeeds(self):
        """بازگشت از عدم تامین به لیست انبار بدون نیاز به درج دلیل موفقیت‌آمیز است"""
        self.client.post(self.dash_url, {'action': 'not_in_stock', 'item_id': str(self.item.id)})
        resp = self.client.post(self.dash_url, {'action': 'reopen', 'item_id': str(self.item.id), 'reason': ''})
        self.assertEqual(resp.status_code, 302)
        assignment = self.item.officer_assignments.order_by('-id').first()
        self.assertEqual(assignment.status, 'reopened')

    def test_warehouse_dashboard_tag_text_no_ceo_mention_for_not_in_stock(self):
        """برای دکمه و برچسب عدم تامین نباید عبارت 'برگشت به مدیرعامل' نمایش داده شود"""
        self.client.post(self.dash_url, {'action': 'not_in_stock', 'item_id': str(self.item.id)})
        resp = self.client.get(self.dash_url, follow=True)
        content = resp.content.decode('utf-8')
        self.assertIn('عدم تامین', content)
        self.assertNotIn('عدم تامین — برگشت به مدیرعامل', content)
        self.assertNotIn('عدم موجودی — برگشت به مدیرعامل', content)


