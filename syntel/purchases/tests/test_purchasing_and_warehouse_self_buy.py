from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse

from purchases.models import (
    Cafe, Category, Unit, Product, Department,
    PurchaseRequest, PurchaseItem, OfficerAssignment,
    PurchaseDraft, PurchaseDraftItem,
)

User = get_user_model()


class PurchasingAndWarehouseSelfBuyTests(TestCase):
    """آزمون منوی مسئول خرید، سوابق خرید، گیت تایید مدیرعامل و انتخاب/خودخریدی مسئول انبار."""

    def setUp(self):
        self.client = Client()
        self.cafe = Cafe.objects.create(name='کافه تست', code='testcafe', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        self.dept_kitchen = Department.objects.create(
            name='آشپزخانه صبحانه', cafe=self.cafe, is_active=True)
        self.category = Category.objects.create(name='لبنیات', cafe=self.cafe, is_active=True)
        self.unit = Unit.objects.create(name='کیلوگرم', symbol='kg')

        self.requester = User.objects.create_user(
            username='req_user', password='p1', role='requester',
            cafe=self.cafe, department=self.dept_kitchen, first_name='سارا')
        self.warehouse = User.objects.create_user(
            username='wh_user', password='p1', role='warehouse',
            cafe=self.cafe, first_name='امید', last_name='انباردار')
        self.purchasing = User.objects.create_user(
            username='buy_user', password='p1', role='purchasing',
            cafe=self.cafe, first_name='مهدی', last_name='خریدار')
        self.other_purchasing = User.objects.create_user(
            username='buy_user2', password='p1', role='purchasing',
            cafe=self.cafe, first_name='رضا', last_name='خریدار۲')
        self.ceo = User.objects.create_user(
            username='ceo_user', password='p1', role='ceo',
            cafe=self.cafe, first_name='مدیر', last_name='عامل')

        self.product_cheese = Product.objects.create(
            name='پنیر صبحانه', category=self.category, unit=self.unit,
            cafe=self.cafe, is_complete=True, min_stock=1)
        self.product_butter = Product.objects.create(
            name='کره محلی', category=self.category, unit=self.unit,
            cafe=self.cafe, is_complete=True, min_stock=1)
        self.product_new = Product.objects.create(
            name='زعفران آزمایشی جدید', category=self.category, unit=self.unit,
            cafe=self.cafe, is_complete=False, min_stock=1)

    def _create_requester_item(self, product, qty='3', name_override=None):
        req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending')
        item = PurchaseItem.objects.create(
            request=req,
            product=product,
            name=name_override or product.name,
            quantity=Decimal(qty),
            unit=product.unit.name,
        )
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse',
            assigned_to=self.warehouse, assigned_by=self.requester, status='pending')
        return req, item

    def test_purchasing_sidebar_only_has_my_list_and_history(self):
        self.client.force_login(self.purchasing)
        url = reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')

        self.assertIn('لیست خرید من', html)
        self.assertIn('سوابق لیست خرید', html)
        self.assertIn(reverse('purchase_officer_history', kwargs={'cafe_code': self.cafe.code}), html)

        # منوهای غیرمرتبط نباید برای مسئول خرید نمایش داده شوند
        for forbidden_url in (
            reverse('purchase_orders_list', kwargs={'cafe_code': self.cafe.code}),
            reverse('approved_requests_for_orders', kwargs={'cafe_code': self.cafe.code}),
            reverse('supplier_list', kwargs={'cafe_code': self.cafe.code}),
            reverse('purchase_reports', kwargs={'cafe_code': self.cafe.code}),
        ):
            self.assertNotIn(forbidden_url, html)

    def test_purchase_officer_history_shows_purchased_and_returned(self):
        req1, item1 = self._create_requester_item(self.product_cheese, '4')
        req2, item2 = self._create_requester_item(self.product_butter, '2')
        a1 = OfficerAssignment.objects.create(
            item=item1, target_role='purchasing',
            assigned_to=self.purchasing, assigned_by=self.ceo, status='pending')
        a2 = OfficerAssignment.objects.create(
            item=item2, target_role='purchasing',
            assigned_to=self.purchasing, assigned_by=self.ceo, status='pending')

        self.client.force_login(self.purchasing)
        dash_url = reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        hist_url = reverse('purchase_officer_history', kwargs={'cafe_code': self.cafe.code})

        self.client.post(dash_url, {'action': 'purchased', 'item_id': str(a1.id)}, follow=True)
        self.client.post(dash_url, {'action': 'return', 'item_id': str(a2.id)}, follow=True)

        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(a1.status, 'purchased')
        self.assertIsNotNone(a1.completed_at)
        self.assertEqual(a2.status, 'returned')
        self.assertIsNotNone(a2.completed_at)

        resp_dash = self.client.get(dash_url)
        self.assertEqual(resp_dash.context['rows'], [])
        self.assertNotContains(resp_dash, 'پنیر صبحانه')
        self.assertNotContains(resp_dash, 'کره محلی')

        resp_all = self.client.get(hist_url)
        self.assertEqual(resp_all.status_code, 200)
        self.assertContains(resp_all, 'پنیر صبحانه')
        self.assertContains(resp_all, 'کره محلی')

        resp_p = self.client.get(hist_url + '?status=purchased')
        self.assertContains(resp_p, 'پنیر صبحانه')
        self.assertNotContains(resp_p, 'کره محلی')

        resp_r = self.client.get(hist_url + '?status=returned')
        self.assertContains(resp_r, 'کره محلی')
        self.assertNotContains(resp_r, 'پنیر صبحانه')

    def test_ceo_pending_items_hidden_until_approved_then_assigned(self):
        # کالای جدید (is_complete=False) نیاز به تایید مدیرعامل دارد
        _, src_item = self._create_requester_item(self.product_new, '1')

        from purchases.models import PurchaseDraftItem

        self.client.force_login(self.warehouse)
        wh_url = reverse('warehouse_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        draft_url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})
        self.client.post(wh_url, {'action': 'to_purchase', 'item_id': str(src_item.id)}, follow=True)
        draft_item = PurchaseDraftItem.objects.get(source_item=src_item)

        resp = self.client.post(draft_url, {
            'action': 'send',
            'has_selection_ui': '1',
            'selected_item_ids': [str(draft_item.id)],
            'officer_user_id': str(self.purchasing.id),
        })
        self.assertEqual(resp.status_code, 302)
        # مصرف پیام‌های سشن مسئول انبار پیش از تغییر کاربر در همان کلاینت تست
        self.client.get(draft_url)

        ceo_req = PurchaseRequest.objects.get(
            source='warehouse_purchase', status='pending_manager_approval')
        self.assertEqual(ceo_req.intended_purchasing_officer_id, self.purchasing.id)
        self.assertEqual(
            OfficerAssignment.objects.filter(target_role='purchasing', item__request=ceo_req).count(),
            0,
        )

        # حتی اگر به صورت دستی یک تخصیص هم زیر درخواست در انتظار مدیرعامل ساخته شود، نباید نمایش داده شود
        self.client.force_login(self.purchasing)
        po_url = reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        self.assertNotContains(self.client.get(po_url), 'زعفران آزمایشی جدید')

        # تایید توسط مدیرعامل
        self.client.force_login(self.ceo)
        approve_url = reverse(
            'manager_approve_request',
            kwargs={'cafe_code': self.cafe.code, 'request_id': ceo_req.id},
        )
        self.client.post(approve_url)

        ceo_req.refresh_from_db()
        self.assertEqual(ceo_req.status, 'pending')
        self.assertEqual(
            OfficerAssignment.objects.filter(
                target_role='purchasing', assigned_to=self.purchasing, item__request=ceo_req
            ).count(),
            1,
        )

        # اکنون در صفحه مسئول خرید دیده می‌شود
        self.client.force_login(self.purchasing)
        resp_after = self.client.get(po_url)
        self.assertContains(resp_after, 'زعفران آزمایشی جدید')
        self.assertContains(resp_after, 'آشپزخانه صبحانه')

    def test_ceo_pending_item_with_direct_assignment_still_blocked_in_query_and_post(self):
        # گارد عمقی: اگر تخصیصی روی درخواست pending_manager_approval وجود داشته باشد نمایش و پردازش نمی‌شود
        ceo_req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending_manager_approval')
        item = PurchaseItem.objects.create(
            request=ceo_req, product=self.product_cheese,
            name='پنیر تست قفل‌شده', quantity=Decimal('2'), unit='کیلوگرم')
        a = OfficerAssignment.objects.create(
            item=item, target_role='purchasing',
            assigned_to=self.purchasing, assigned_by=self.warehouse, status='pending')

        self.client.force_login(self.purchasing)
        po_url = reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        self.assertNotContains(self.client.get(po_url), 'پنیر تست قفل‌شده')

        self.client.post(po_url, {'action': 'purchased', 'item_id': str(a.id)})
        a.refresh_from_db()
        self.assertEqual(a.status, 'pending')

    def test_warehouse_dashboard_select_items_to_officer_and_self_buy(self):
        from purchases.models import PurchaseDraftItem

        _, item_cheese = self._create_requester_item(self.product_cheese, '5')
        _, item_butter = self._create_requester_item(self.product_butter, '2')

        self.client.force_login(self.warehouse)
        wh_url = reverse('warehouse_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        draft_url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})
        my_buy_url = reverse('warehouse_my_purchases', kwargs={'cafe_code': self.cafe.code})
        my_hist_url = reverse('warehouse_my_purchases_history', kwargs={'cafe_code': self.cafe.code})

        # داشبورد اصلی انبار نباید فرم انتخاب و ارسال مسئول خرید داشته باشد
        wh_page = self.client.get(wh_url, follow=True)
        self.assertEqual(wh_page.status_code, 200)
        self.assertContains(wh_page, 'خریدهای من')
        self.assertNotContains(wh_page, 'wo-bulk-send-form')
        self.assertNotContains(wh_page, 'خرید توسط خودم')

        # ارسال اقلام از داشبورد انبار به لیست خرید مسئول انبار
        self.client.post(wh_url, {'action': 'to_purchase', 'item_id': str(item_cheese.id)})
        self.client.post(wh_url, {'action': 'to_purchase', 'item_id': str(item_butter.id)})
        d_cheese = PurchaseDraftItem.objects.get(source_item=item_cheese)
        d_butter = PurchaseDraftItem.objects.get(source_item=item_butter)

        draft_page = self.client.get(draft_url)
        self.assertEqual(draft_page.status_code, 200)
        self.assertContains(draft_page, 'خرید توسط خودم')

        # ارسال فقط پنیر برای مسئول خرید دوم از صفحه لیست خرید
        self.client.post(draft_url, {
            'action': 'send',
            'has_selection_ui': '1',
            'selected_item_ids': [str(d_cheese.id)],
            'officer_user_id': str(self.other_purchasing.id),
        })

        # ارسال کره با گزینه «خرید توسط خودم» → ریدایرکت به خریدهای من
        resp_self = self.client.post(draft_url, {
            'action': 'send',
            'has_selection_ui': '1',
            'selected_item_ids': [str(d_butter.id)],
            'officer_user_id': 'self',
        })
        self.assertRedirects(resp_self, my_buy_url)

        # پنیر در لیست خرید مسئول خرید دوم است و کره نیست
        self.client.force_login(self.other_purchasing)
        po_resp = self.client.get(reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code}))
        self.assertContains(po_resp, 'پنیر صبحانه')
        self.assertNotContains(po_resp, 'کره محلی')

        # کره در صفحه «خریدهای من» مسئول انبار است و میانبرهای اضافه حذف شده‌اند
        self.client.force_login(self.warehouse)
        my_resp = self.client.get(my_buy_url)
        self.assertEqual(my_resp.status_code, 200)
        self.assertContains(my_resp, 'کره محلی')
        self.assertContains(my_resp, 'آشپزخانه صبحانه')
        self.assertContains(my_resp, 'سوابق خریدهای من')
        self.assertNotContains(my_resp, '← داشبورد انبار')
        self.assertNotContains(my_resp, '+ درخواست خرید')
        self.assertNotContains(my_resp, 'class="ph-tab active"')
        self.assertNotContains(my_resp, 'پنیر صبحانه')

        # در سوابق خریدهای من هم میانبر اضافه وجود ندارد
        hist_resp = self.client.get(my_hist_url)
        self.assertEqual(hist_resp.status_code, 200)
        self.assertContains(hist_resp, 'خریدهای من')
        self.assertNotContains(hist_resp, '← داشبورد انبار')
        self.assertNotContains(hist_resp, '+ درخواست خرید')

        # تکمیل خرید توسط مسئول انبار و مشاهده در سوابق
        self_assign = OfficerAssignment.objects.get(
            target_role='purchasing', assigned_to=self.warehouse, status='pending')
        self.client.post(my_buy_url, {'action': 'purchased', 'item_id': str(self_assign.id)}, follow=True)
        resp_after_buy = self.client.get(my_buy_url)
        self.assertEqual(resp_after_buy.context['rows'], [])
        self.assertNotContains(resp_after_buy, 'کره محلی')
        self.assertContains(self.client.get(my_hist_url), 'کره محلی')

    def test_warehouse_purchase_draft_partial_selection_and_self_buy(self):
        draft = PurchaseDraft.objects.create(cafe=self.cafe, created_by=self.warehouse)
        d1 = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product_cheese, name=self.product_cheese.name,
            quantity=Decimal('2'), unit='کیلوگرم')
        d2 = PurchaseDraftItem.objects.create(
            draft=draft, product=self.product_butter, name=self.product_butter.name,
            quantity=Decimal('3'), unit='کیلوگرم')

        self.client.force_login(self.warehouse)
        draft_url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})
        my_buy_url = reverse('warehouse_my_purchases', kwargs={'cafe_code': self.cafe.code})

        # ارسال فقط قلم اول با «خرید توسط خودم»
        resp = self.client.post(draft_url, {
            'action': 'send',
            'has_selection_ui': '1',
            'selected_item_ids': [str(d1.id)],
            'officer_user_id': 'self',
        })
        self.assertRedirects(resp, my_buy_url)

        # قلم دوم هنوز در پیش‌نویس باقی مانده است
        draft.refresh_from_db()
        self.assertEqual(list(draft.items.values_list('id', flat=True)), [d2.id])
        self.assertContains(self.client.get(my_buy_url), 'پنیر صبحانه')
        self.assertNotContains(self.client.get(my_buy_url), 'کره محلی')

        # ارسال قلم دوم به مسئول خرید
        resp2 = self.client.post(draft_url, {
            'action': 'send',
            'has_selection_ui': '1',
            'selected_item_ids': [str(d2.id)],
            'officer_user_id': str(self.purchasing.id),
        })
        self.assertRedirects(resp2, draft_url)
        self.assertFalse(PurchaseDraft.objects.filter(id=draft.id).exists())

        self.client.force_login(self.purchasing)
        po_resp = self.client.get(reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code}))
        self.assertContains(po_resp, 'کره محلی')
        self.assertNotContains(po_resp, 'پنیر صبحانه')

    def test_same_product_merged_with_separate_sections_and_draft_add_sums(self):
        dept_bar = Department.objects.create(
            name='بار سرد', cafe=self.cafe, is_active=True)
        bar_user = User.objects.create_user(
            username='bar_req', password='p1', role='requester',
            cafe=self.cafe, department=dept_bar, first_name='علی')

        r_kitchen = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending')
        i_kitchen = PurchaseItem.objects.create(
            request=r_kitchen, product=self.product_cheese, name='پنیر صبحانه',
            quantity=Decimal('4'), unit='کیلوگرم', description='توضیح آشپزخانه')

        r_bar = PurchaseRequest.objects.create(
            user=bar_user, cafe=self.cafe, status='pending')
        i_bar = PurchaseItem.objects.create(
            request=r_bar, product=self.product_cheese, name='پنیر صبحانه',
            quantity=Decimal('2.5'), unit='کیلوگرم', description='توضیح بار')

        OfficerAssignment.objects.create(
            item=i_kitchen, target_role='purchasing', assigned_to=self.purchasing, status='pending')
        OfficerAssignment.objects.create(
            item=i_bar, target_role='purchasing', assigned_to=self.purchasing, status='pending')

        self.client.force_login(self.purchasing)
        po_url = reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        resp = self.client.get(po_url)
        self.assertEqual(resp.status_code, 200)
        # هر دو بخش با توضیحات متفاوت در یک کارت جمع شده و سکشن هر بخش جداگانه نوشته شده است
        rows = resp.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['total_qty'], 6.5)
        section_names = [s['name'] for s in rows[0]['sections']]
        self.assertIn('آشپزخانه صبحانه', section_names)
        self.assertIn('بار سرد', section_names)
        self.assertContains(resp, '6.5')

        # همچنین در پیش‌نویس خرید، افزودن مجدد همان کالا با قبلی جمع می‌شود
        self.client.force_login(self.warehouse)
        draft_url = reverse('warehouse_purchase_draft', kwargs={'cafe_code': self.cafe.code})
        self.client.post(draft_url, {
            'action': 'add_item', 'product_id': str(self.product_butter.id),
            'new_qty': '3', 'unit': 'کیلوگرم',
        })
        self.client.post(draft_url, {
            'action': 'add_item', 'product_id': str(self.product_butter.id),
            'new_qty': '4', 'unit': 'کیلوگرم',
        })
        manual_items = PurchaseDraftItem.objects.filter(
            draft__cafe=self.cafe, draft__created_by=self.warehouse,
            product=self.product_butter, source_item__isnull=True)
        self.assertEqual(manual_items.count(), 1)
        self.assertEqual(manual_items.first().quantity, Decimal('7'))

    def test_return_to_ceo_removed_and_not_purchased_recorded(self):
        _, item = self._create_requester_item(self.product_cheese, '3')
        a = OfficerAssignment.objects.create(
            item=item, target_role='purchasing',
            assigned_to=self.purchasing, assigned_by=self.warehouse, status='pending')

        self.client.force_login(self.purchasing)
        po_url = reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code})
        hist_url = reverse('purchase_officer_history', kwargs={'cafe_code': self.cafe.code})

        resp = self.client.get(po_url)
        self.assertNotContains(resp, 'بازگشت به مدیرعامل')
        self.assertContains(resp, 'خریداری نشده')

        self.client.post(po_url, {'action': 'not_purchased', 'item_id': str(a.id)}, follow=True)
        a.refresh_from_db()
        self.assertEqual(a.status, 'not_purchased')
        self.assertIsNotNone(a.completed_at)

        resp_hist = self.client.get(hist_url + '?status=not_purchased')
        self.assertContains(resp_hist, 'پنیر صبحانه')
        self.assertContains(resp_hist, 'خریداری نشده')

    def test_warehouse_purchase_status_page_shows_purchased_and_not_purchased_with_filters(self):
        _, i_bought = self._create_requester_item(self.product_cheese, '4')
        _, i_not_bought = self._create_requester_item(self.product_butter, '2')

        a_bought = OfficerAssignment.objects.create(
            item=i_bought, target_role='purchasing',
            assigned_to=self.warehouse, assigned_by=self.warehouse, status='pending')
        a_not_bought = OfficerAssignment.objects.create(
            item=i_not_bought, target_role='purchasing',
            assigned_to=self.purchasing, assigned_by=self.warehouse, status='pending')

        self.client.force_login(self.warehouse)
        self.client.post(
            reverse('warehouse_my_purchases', kwargs={'cafe_code': self.cafe.code}),
            {'action': 'purchased', 'item_id': str(a_bought.id)},
        )
        self.client.force_login(self.purchasing)
        self.client.post(
            reverse('purchase_officer_dashboard', kwargs={'cafe_code': self.cafe.code}),
            {'action': 'not_purchased', 'item_id': str(a_not_bought.id)},
        )

        # مسئول انبار گزینه «وضعیت خرید» را می‌بیند و هر دو وضعیت قابل فیلتر است
        self.client.force_login(self.warehouse)
        wh_dash = self.client.get(
            reverse('warehouse_officer_dashboard', kwargs={'cafe_code': self.cafe.code}), follow=True)
        self.assertContains(wh_dash, 'وضعیت خرید')

        status_url = reverse('purchase_orders_status', kwargs={'cafe_code': self.cafe.code})
        r_all = self.client.get(status_url)
        self.assertEqual(r_all.status_code, 200)
        self.assertContains(r_all, 'پنیر صبحانه')
        self.assertContains(r_all, 'کره محلی')
        self.assertContains(r_all, 'امید انباردار')
        self.assertContains(r_all, 'مهدی خریدار')

        r_p = self.client.get(status_url + '?status=purchased')
        self.assertContains(r_p, 'پنیر صبحانه')
        self.assertNotContains(r_p, 'کره محلی')

        r_np = self.client.get(status_url + '?status=not_purchased')
        self.assertContains(r_np, 'کره محلی')
        self.assertNotContains(r_np, 'پنیر صبحانه')

        r_by_wh = self.client.get(status_url + f'?officer={self.warehouse.id}')
        self.assertContains(r_by_wh, 'پنیر صبحانه')
        self.assertNotContains(r_by_wh, 'کره محلی')

        # فیلتر عقب/جلو کردن تاریخ و همه تاریخ‌ها
        from datetime import timedelta
        from django.utils import timezone

        _, i_old = self._create_requester_item(self.product_new, '3')
        a_old = OfficerAssignment.objects.create(
            item=i_old, target_role='purchasing',
            assigned_to=self.purchasing, assigned_by=self.warehouse, status='purchased',
        )
        yesterday_dt = timezone.now() - timedelta(days=1)
        OfficerAssignment.objects.filter(id=a_old.id).update(
            assigned_at=yesterday_dt,
            completed_at=yesterday_dt,
        )
        yesterday_str = timezone.localdate(yesterday_dt).strftime('%Y-%m-%d')

        r_today = self.client.get(status_url)
        self.assertContains(r_today, 'روز بعد')
        self.assertContains(r_today, 'همه تاریخ‌ها')
        self.assertContains(r_today, 'پنیر صبحانه')
        self.assertNotContains(r_today, 'زعفران آزمایشی جدید')

        r_yesterday = self.client.get(status_url + f'?day={yesterday_str}')
        self.assertContains(r_yesterday, 'زعفران آزمایشی جدید')
        self.assertContains(r_yesterday, 'امروز')
        self.assertNotContains(r_yesterday, 'پنیر صبحانه')

        r_all_days = self.client.get(status_url + '?day=all')
        self.assertContains(r_all_days, 'پنیر صبحانه')
        self.assertContains(r_all_days, 'زعفران آزمایشی جدید')
