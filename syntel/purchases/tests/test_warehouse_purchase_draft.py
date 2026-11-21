"""تست صفحه «درخواست خرید» مسئول انبار

جریان:
- ارجاع به خرید در داشبورد انبار → قلم به پیش‌نویس صفحه درخواست خرید می‌رود
- ویرایش/حذف قلم پیش‌نویس، افزودن کالای جدید
- «ارسال» → PurchaseRequest با source=warehouse_purchase که تخصیص انبار نمی‌گیرد
  و برای تایید مدیرعامل می‌رود (status=pending_manager_approval)
- درخواست ارسال‌شده فقط تا قبل از تایید مدیرعامل قابل ویرایش/حذف قلم است
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Cafe, Category, Unit, Product, PurchaseRequest,
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
        # (ریدایرکت POST بدون day به ‎?day=<امروز> هم هست؛ follow تا مقصد نهایی)
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
            'new_qty': '7', 'unit': 'Kilogram',
        })
        self.assertEqual(PurchaseDraftItem.objects.count(), 1)
        d_item = PurchaseDraftItem.objects.first()
        self.assertEqual(d_item.quantity, 7)
        self.assertEqual(d_item.unit, 'Kilogram')

    def test_foreign_product_id_does_not_leak_across_cafes(self):
        """شناسهٔ خامِ محصولِ کافهٔ دیگر نباید به پیش‌نویس این کافه وصل شود

        قلم با «نام آزاد» ثبت می‌شود تا کار مسئول انبار از بین نرود — ولی
        هیچ‌گاه به محصولِ کافهٔ دیگر وصل نمی‌شود.
        """
        other_cafe = Cafe.objects.create(name='کافه دیگر', code='other', is_active=True)
        other_unit = Unit.objects.create(name='Gram', symbol='g', cafe=other_cafe)
        foreign_product = Product.objects.create(
            name='محصول کافه دیگر', unit=other_unit, cafe=other_cafe)

        self._login_as_warehouse()
        resp = self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']), {
            'action': 'add_item', 'product_id': str(foreign_product.id),
            'name': 'کالای واردشده', 'quantity': '3', 'unit': 'عدد',
        })
        self.assertEqual(resp.status_code, 302)
        d_item = PurchaseDraftItem.objects.get()
        self.assertIsNone(d_item.product_id)
        self.assertEqual(d_item.name, 'کالای واردشده')

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

    def test_send_creates_request_for_ceo_approval(self):
        """کالای مشمول (مثلاً ارزش بالای ۴۰۰ میلیون ریال) برای تایید مدیرعامل ارسال می‌شود"""
        self._login_as_warehouse()
        self.product.base_unit_price = Decimal('100000000')  # 100M * 5 = 500M > 400M
        self.product.save(update_fields=['base_unit_price'])
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product,
            name='محصول تست', quantity=5, unit='kg')
        resp = self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']), {
            'action': 'send',
        })
        self.assertRedirects(resp, reverse('warehouse_purchase_draft', args=['testcafe']))
        # درخواست ساخته شد و برای تایید مدیرعامل رفت (بدون تخصیص به خودش)
        self.assertEqual(PurchaseRequest.objects.count(), 2)  # اصلی + جدید
        new_req = PurchaseRequest.objects.filter(source='warehouse_purchase').first()
        self.assertIsNotNone(new_req)
        self.assertEqual(new_req.status, 'pending_manager_approval')
        self.assertEqual(new_req.user, self.wo)
        self.assertEqual(new_req.items.count(), 1)
        self.assertEqual(new_req.items.first().quantity, 5)
        # درخواست خریدِ مسئول انبار زمان تحویل ندارد — تاریخِ خودِ درخواست (created_at) زمان ثبت است
        self.assertIsNone(new_req.delivery_date)
        self.assertFalse(new_req.delivery_immediate)
        # هیچ تخصیصی ساخته نشد — درخواست به داشبورد انبار برنمی‌گردد
        self.assertEqual(OfficerAssignment.objects.filter(item__request=new_req).count(), 0)
        # پیش‌نویس حذف شد
        self.assertEqual(PurchaseDraft.objects.count(), 0)
        self.assertEqual(PurchaseDraftItem.objects.count(), 0)

    def test_send_empty_draft_rejected(self):
        self._login_as_warehouse()
        resp = self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                                {'action': 'send'})
        self.assertEqual(PurchaseRequest.objects.filter(source='warehouse_purchase').count(), 0)

    def test_edit_and_delete_sent_request_items(self):
        """درخواست ارسال‌شده تا قبل از تایید مدیرعامل قابل ویرایش/حذف قلم است"""
        self._login_as_warehouse()
        new_req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending_manager_approval',
            source='warehouse_purchase')
        sent_item = PurchaseItem.objects.create(
            request=new_req, product=self.product, name='محصول تست', quantity=4, unit='kg')
        # ویرایش مقدار
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'edit_sent', 'request_id': new_req.id,
                          'item_id': sent_item.id, 'quantity': '12'})
        sent_item.refresh_from_db()
        self.assertEqual(sent_item.quantity, 12)
        # حذف آخرین قلم → درخواست حذف نمی‌شود و در صف مدیرعامل می‌ماند
        # (هیچ چیزی از داشبورد مدیرعامل ناپدید نمی‌شود)
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'delete_sent', 'request_id': new_req.id,
                          'item_id': sent_item.id})
        new_req.refresh_from_db()
        self.assertEqual(new_req.status, 'pending_manager_approval')
        self.assertEqual(new_req.items.count(), 0)

    def test_cannot_edit_approved_request(self):
        """بعد از تایید مدیرعامل (status=pending) دیگر قابل ویرایش نیست"""
        self._login_as_warehouse()
        req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending', source='warehouse_purchase')
        sent_item = PurchaseItem.objects.create(
            request=req, product=self.product, name='محصول تست', quantity=4, unit='kg')
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'edit_sent', 'request_id': req.id,
                          'item_id': sent_item.id, 'quantity': '99'})
        sent_item.refresh_from_db()
        self.assertEqual(sent_item.quantity, 4)  # تغییری نکرد

    def test_cannot_delete_item_after_ceo_approval(self):
        """بعد از تایید مدیرعامل (status=pending) قلم حذف نمی‌شود — درخواست فقط-خواندنی است"""
        self._login_as_warehouse()
        req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending', source='warehouse_purchase')
        sent_item = PurchaseItem.objects.create(
            request=req, product=self.product, name='محصول تست', quantity=4, unit='kg')
        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']),
                         {'action': 'delete_sent', 'request_id': req.id,
                          'item_id': sent_item.id})
        self.assertEqual(PurchaseItem.objects.filter(id=sent_item.id).count(), 1)  # قلم ماند
        self.assertEqual(PurchaseRequest.objects.filter(id=req.id).count(), 1)    # درخواست ماند

    def test_sent_section_limits_to_last_week(self):
        """بخش «درخواست‌های ارسال‌شده» فقط درخواست‌های یک هفتهٔ گذشته را نشان می‌دهد"""
        self._login_as_warehouse()
        # درخواست قدیمی‌تر از یک هفته (در انتظار تایید — نباید نمایش داده شود)
        old_awaiting = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending_manager_approval',
            source='warehouse_purchase')
        PurchaseItem.objects.create(
            request=old_awaiting, product=self.product, name='قلم قدیمی', quantity=1, unit='kg')
        PurchaseRequest.objects.filter(id=old_awaiting.id).update(
            created_at=timezone.now() - timedelta(days=8))
        # درخواست‌های یک هفتهٔ اخیر (هر سه گروه — باید نمایش داده شوند)
        recent_awaiting = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending_manager_approval',
            source='warehouse_purchase')
        PurchaseItem.objects.create(
            request=recent_awaiting, product=self.product, name='قلم تازه ۱', quantity=2, unit='kg')
        recent_approved = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending', source='warehouse_purchase')
        PurchaseItem.objects.create(
            request=recent_approved, product=self.product, name='قلم تازه ۲', quantity=3, unit='kg')

        resp = self.client.get(reverse('warehouse_purchase_draft', args=['testcafe']))
        self.assertContains(resp, f'درخواست {recent_awaiting.request_number}')
        self.assertContains(resp, f'درخواست {recent_approved.request_number}')
        self.assertNotContains(resp, f'درخواست {old_awaiting.request_number}')  # قدیمی‌تر از یک هفته — نمایش داده نشد

    def test_ceo_approval_sends_request_to_purchase_order_queue(self):
        """تایید مدیرعامل: درخواست خریدِ انبار در فهرست «ثبت سفارش خرید» واحد خرید ظاهر می‌شود"""
        wo_req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending_manager_approval',
            source='warehouse_purchase')
        PurchaseItem.objects.create(
            request=wo_req, product=self.product, name='محصول تست', quantity=4, unit='kg')

        # تایید توسط مدیرعامل
        self.client.login(username='ceo_test', password='testpass123')
        resp = self.client.post(
            reverse('manager_approve_request', args=['testcafe', wo_req.id]), follow=True)
        self.assertEqual(resp.status_code, 200)
        wo_req.refresh_from_db()
        self.assertEqual(wo_req.status, 'pending')
        self.assertTrue(wo_req.manager_confirmed)
        # تایید، تخصیص انبار نمی‌سازد (به داشبورد انبار برنمی‌گردد)
        self.assertEqual(OfficerAssignment.objects.filter(item__request=wo_req).count(), 0)

        # مسئول خرید می‌بیندش در فهرست «تایید مدیرعامل — ثبت سفارش خرید»
        po = User.objects.create_user(
            username='po_test', password='testpass123', role='purchasing', cafe=self.cafe)
        self.client.login(username='po_test', password='testpass123')
        resp = self.client.get(reverse('approved_requests_for_orders', args=['testcafe']))
        self.assertContains(resp, str(wo_req.id))

    def test_requester_role_cannot_access(self):
        self.client.login(username='req1', password='testpass123')
        resp = self.client.get(reverse('warehouse_purchase_draft', args=['testcafe']))
        self.assertEqual(resp.status_code, 302)  # ریدایرکت (به login یا داشبورد خرید)

    def test_normal_product_sends_directly_to_purchasing(self):
        """کالای عادی (تعریف‌شده، کامل، زیر ۴۰۰ میلیون و زیر ۳ برابر میانگین) مستقیم به واحد خرید می‌رود"""
        self._login_as_warehouse()
        self.product.base_unit_price = Decimal('80000000')
        self.product.smallest_unit = 'kg'
        self.product.save(update_fields=['base_unit_price', 'smallest_unit'])
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        # 5 * 80M = 400M (دقیقاً مرزی، بیش از ۴۰۰ میلیون نیست)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product,
            name='محصول تست', quantity=5, unit='kg')
        resp = self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']), {
            'action': 'send',
        })
        self.assertRedirects(resp, reverse('warehouse_purchase_draft', args=['testcafe']))
        direct_req = PurchaseRequest.objects.filter(source='warehouse_purchase').first()
        self.assertIsNotNone(direct_req)
        self.assertEqual(direct_req.status, 'pending')
        self.assertFalse(direct_req.manager_confirmed)

        # در فهرست واحد خرید ظاهر می‌شود
        po = User.objects.create_user(
            username='po_direct', password='testpass123', role='purchasing', cafe=self.cafe)
        self.client.login(username='po_direct', password='testpass123')
        po_resp = self.client.get(reverse('approved_requests_for_orders', args=['testcafe']))
        self.assertContains(po_resp, str(direct_req.id))

    def test_unlisted_and_incomplete_and_rd_products_need_ceo(self):
        """کالای تعریف‌نشده، کالای ناقص و کالای تحقیق و توسعه باید به مدیرعامل بروند"""
        self._login_as_warehouse()
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        incomplete_prod = Product.objects.create(
            name='کالای جدید کاربر', category=self.category, unit=self.unit,
            cafe=self.cafe, is_complete=False)
        rd_cat = Category.objects.create(name='تحقیق و توسعه', cafe=self.cafe)
        rd_prod = Product.objects.create(
            name='کالای R&D', category=rd_cat, unit=self.unit,
            cafe=self.cafe, is_complete=True)

        PurchaseDraftItem.objects.create(draft=draft, product=None, name='کالای آزاد', quantity=1, unit='kg')
        PurchaseDraftItem.objects.create(draft=draft, product=incomplete_prod, name=incomplete_prod.name, quantity=1, unit='kg')
        PurchaseDraftItem.objects.create(draft=draft, product=rd_prod, name=rd_prod.name, quantity=1, unit='kg')

        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']), {'action': 'send'})
        ceo_req = PurchaseRequest.objects.filter(source='warehouse_purchase', status='pending_manager_approval').first()
        self.assertIsNotNone(ceo_req)
        self.assertEqual(ceo_req.items.count(), 3)
        self.assertFalse(PurchaseRequest.objects.filter(source='warehouse_purchase', status='pending').exists())

    def test_high_quantity_vs_historical_avg_unit_normalized(self):
        """مقدار بیش از ۳ برابر میانگین خرید قبلی (با تبدیل kg/گرم) نیاز به تایید مدیرعامل دارد"""
        self._login_as_warehouse()
        # سابقهٔ خرید قبلی: 2000 گرم = 2 کیلوگرم، خرید شده
        hist_req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='completed')
        PurchaseItem.objects.create(
            request=hist_req, product=self.product, name=self.product.name,
            quantity=Decimal('2000'), unit='گرم', purchased_quantity=Decimal('2000'))

        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        # 6 کیلوگرم = ۳ برابر میانگین (نباید نیاز به مدیرعامل داشته باشد)
        d_ok = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name='محصول تست', quantity=6, unit='kg')
        # 6.5 کیلوگرم > ۳ برابر میانگین (باید نیاز به مدیرعامل داشته باشد)
        d_high = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name='محصول تست', quantity=Decimal('6.5'), unit='kg')

        from purchases.services.purchase_approval import evaluate_draft_items_ceo_status
        eval_map = evaluate_draft_items_ceo_status([d_ok, d_high], cafe=self.cafe)
        self.assertFalse(eval_map[d_ok.id]['needs_ceo'])
        self.assertTrue(eval_map[d_high.id]['needs_ceo'])

    def test_send_splits_mixed_draft_into_ceo_and_direct_requests(self):
        """ارسال سبد ترکیبی باید اقلام عادی و نیازمند تایید مدیرعامل را در دو درخواست جدا ثبت کند"""
        self._login_as_warehouse()
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name='کالای عادی', quantity=2, unit='kg')
        PurchaseDraftItem.objects.create(
            draft=draft, product=None, name='کالای تعریف‌نشده', quantity=1, unit='kg')

        self.client.post(reverse('warehouse_purchase_draft', args=['testcafe']), {'action': 'send'})
        direct_req = PurchaseRequest.objects.filter(source='warehouse_purchase', status='pending').first()
        ceo_req = PurchaseRequest.objects.filter(source='warehouse_purchase', status='pending_manager_approval').first()
        self.assertIsNotNone(direct_req)
        self.assertIsNotNone(ceo_req)
        self.assertEqual(list(direct_req.items.values_list('name', flat=True)), ['کالای عادی'])
        self.assertEqual(list(ceo_req.items.values_list('name', flat=True)), ['کالای تعریف‌نشده'])

    def test_edit_draft_and_sent_items_full_fields(self):
        """ویرایش کامل نام/کالا، مقدار، واحد و توضیحات هم در پیش‌نویس و هم در درخواست در انتظار تایید مدیرعامل"""
        self._login_as_warehouse()
        gram_unit = Unit.objects.create(name='Gram', symbol='گرم')
        other_prod = Product.objects.create(
            name='کالای دوم', category=self.category, unit=gram_unit, cafe=self.cafe)

        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        d_item = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name='محصول تست', quantity=2, unit='kg')

        url = reverse('warehouse_purchase_draft', args=['testcafe'])
        self.client.post(url, {
            'action': 'edit_item',
            'item_id': d_item.id,
            'product_id': other_prod.id,
            'name': 'کالای دوم',
            'quantity': '750',
            'unit': 'گرم',
            'description': 'بسته جدید',
        })
        d_item.refresh_from_db()
        self.assertEqual(d_item.product_id, other_prod.id)
        self.assertEqual(d_item.name, 'کالای دوم')
        self.assertEqual(d_item.quantity, Decimal('750'))
        self.assertEqual(d_item.unit, 'گرم')
        self.assertEqual(d_item.description, 'بسته جدید')

        # همچنین edit_sent روی درخواست در انتظار تایید مدیرعامل
        ceo_req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending_manager_approval',
            source='warehouse_purchase')
        s_item = PurchaseItem.objects.create(
            request=ceo_req, product=None, name='کالای قبلی', quantity=1, unit='kg')
        self.client.post(url, {
            'action': 'edit_sent',
            'request_id': ceo_req.id,
            'item_id': s_item.id,
            'product_id': other_prod.id,
            'name': 'کالای دوم',
            'quantity': '300',
            'unit': 'گرم',
            'description': 'ویرایش شد',
        })
        s_item.refresh_from_db()
        self.assertEqual(s_item.product_id, other_prod.id)
        self.assertEqual(s_item.name, 'کالای دوم')
        self.assertEqual(s_item.quantity, Decimal('300'))
        self.assertEqual(s_item.unit, 'گرم')
        self.assertEqual(s_item.description, 'ویرایش شد')

    def test_page_renders_search_box_edit_modal_and_routing_badges(self):
        """بررسی وجود سرچ باکس کالا مشابه درخواست کالا، دکمه ویرایش، مودال ویرایش و نشانگر تایید مدیرعامل"""
        self._login_as_warehouse()
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.wo)
        PurchaseDraftItem.objects.create(
            draft=draft, product=self.product, name='محصول تست', quantity=1, unit='kg')
        PurchaseDraftItem.objects.create(
            draft=draft, product=None, name='کالای آزاد', quantity=1, unit='kg')
        ceo_req = PurchaseRequest.objects.create(
            user=self.wo, cafe=self.cafe, status='pending_manager_approval',
            source='warehouse_purchase')
        PurchaseItem.objects.create(
            request=ceo_req, product=None, name='قلم ارسالی', quantity=2, unit='kg')

        resp = self.client.get(reverse('warehouse_purchase_draft', args=['testcafe']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="entryProductName"')
        self.assertContains(resp, 'id="entryCategorySelect"')
        self.assertContains(resp, 'همه کالاها')
        self.assertContains(resp, 'id="editItemModal"')
        self.assertContains(resp, 'id="addProductModal"')
        self.assertContains(resp, 'نیاز به تایید مدیرعامل')
        self.assertContains(resp, 'ارسال مستقیم به واحد خرید')
        self.assertContains(resp, 'ویرایش')
        self.assertContains(resp, 'draft-item-row')
        self.assertContains(resp, 'id="draftItemSearch"')
        self.assertNotContains(resp, 'class="hint-box"')

    def test_cafe_switch_hidden_for_warehouse_officer(self):
        """قابلیت تغییر کافه در داشبورد و صفحه درخواست خرید مسئول انبار نمایش داده نشود"""
        self._login_as_warehouse()
        dash_resp = self.client.get(reverse('warehouse_officer_dashboard', args=['testcafe']), follow=True)
        self.assertEqual(dash_resp.status_code, 200)
        self.assertNotContains(dash_resp, 'class="sidebar-switch-cafe"')

        draft_resp = self.client.get(reverse('warehouse_purchase_draft', args=['testcafe']))
        self.assertEqual(draft_resp.status_code, 200)
        self.assertNotContains(draft_resp, 'class="sidebar-switch-cafe"')
