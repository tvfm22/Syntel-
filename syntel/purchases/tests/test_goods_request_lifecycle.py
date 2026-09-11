# ─────────────────────────────────────────────────────────────────────────────
# درخواست کالا — چرخهٔ کاملِ درخواست‌دهنده تا تامین
#
# این فایل شکاف‌هایی را می‌پوشاند که هیچ تستی نداشتند:
#   - فرم ثبت «درخواست از انبار» + صفحهٔ موفقیت + دو صفحهٔ فهرست
#     (چهار URL که صفر ارجاع در تست‌ها داشتند)
#   - شاخه‌های تست‌نشدهٔ process_warehouse_request: ارجاع به خرید، عدم تامین،
#     و قلمی که با نام آزاد ساخته می‌شود
#   - تایید مدیر برای درخواستِ عادیِ درخواست‌دهنده (source='requester')
#   - process_purchase_request (صفر ارجاع در تست‌ها)
# ─────────────────────────────────────────────────────────────────────────────
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Notification, OfficerAssignment, Product, ProductBrand, PurchaseItem,
    PurchaseRequest, Unit, WarehouseRequest, WarehouseRequestItem,
)

User = get_user_model()

D = Decimal


def _set_cafe_session(client, cafe):
    session = client.session
    session['selected_cafe_id'] = cafe.id
    session['selected_cafe_code'] = cafe.code
    session.save()


class _GoodsRequestBase(TestCase):
    """زیرساخت مشترک: دو کافه، واحد/محصول/برند در هر کدام"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='kitchen_a', password='testpass123', role='kitchen')
        self.cafe_a = Cafe.objects.create(name='A', code='a', is_active=True)
        self.cafe_b = Cafe.objects.create(name='B', code='b', is_active=True)
        self.user.cafe = self.cafe_a
        self.user.save()
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='kitchen_a', password='testpass123')

        self.unit_a = Unit.objects.create(
            name='Kilogram', symbol='kg', cafe=self.cafe_a)
        self.unit_b = Unit.objects.create(
            name='Gram', symbol='g', cafe=self.cafe_b)
        self.product_a = Product.objects.create(
            name='Sugar', unit=self.unit_a, cafe=self.cafe_a, min_stock=1)
        self.product_b = Product.objects.create(
            name='Tea', unit=self.unit_b, cafe=self.cafe_b, min_stock=1)
        self.brand_a = ProductBrand.objects.create(
            product=self.product_a, brand_name='BrandX', conversion_rate=D('2'))
        self.brand_b = ProductBrand.objects.create(
            product=self.product_b, brand_name='BrandY', conversion_rate=D('3'))

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'a', **kwargs})


# ═══════════════════════════════════════════════════════════════════════════
# ۱. فرم ثبت درخواست از انبار
# ═══════════════════════════════════════════════════════════════════════════
class WarehouseRequestFormTests(_GoodsRequestBase):
    """فرم «درخواست از انبار» — پیش از این هیچ تستی نداشت"""

    def _post(self, **overrides):
        data = {
            'product_name': ['Sugar'],
            'item_quantity': ['5'],
            'item_unit': ['Kilogram'],
            'item_description': ['برای آشپزخانه'],
            'item_product_id': [str(self.product_a.id)],
            'item_brand_id': [''],
        }
        data.update(overrides)
        return self.client.post(self._url('warehouse_request_form'), data=data)

    def test_get_renders_form(self):
        r = self.client.get(self._url('warehouse_request_form'))
        self.assertEqual(r.status_code, 200)

    def test_single_item_request_is_created(self):
        r = self._post()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, self._url('warehouse_request_success'))

        req = WarehouseRequest.objects.get()
        self.assertEqual(req.user, self.user)
        self.assertEqual(req.cafe, self.cafe_a)
        self.assertEqual(req.status, 'pending')

        item = req.items.get()
        self.assertEqual(item.name, 'Sugar')
        self.assertEqual(item.quantity, D('5'))
        self.assertEqual(item.unit, 'Kilogram')
        self.assertEqual(item.description, 'برای آشپزخانه')
        self.assertEqual(item.product_id, self.product_a.id)
        self.assertIsNone(item.product_brand_id)

    def test_multi_item_request_is_created(self):
        r = self._post(
            product_name=['Sugar', 'Flour'],
            item_quantity=['5', '2.5'],
            item_unit=['Kilogram', 'Kilogram'],
            item_description=['a', 'b'],
            item_product_id=[str(self.product_a.id), ''],
            item_brand_id=['', ''],
        )
        self.assertEqual(r.status_code, 302)
        req = WarehouseRequest.objects.get()
        self.assertEqual(req.items.count(), 2)
        quantities = sorted(i.quantity for i in req.items.all())
        self.assertEqual(quantities, [D('2.5'), D('5')])

    def test_confirmation_notification_is_sent(self):
        self._post()
        note = Notification.objects.get(recipient=self.user)
        self.assertIn('درخواست', note.title)
        self.assertEqual(note.cafe, self.cafe_a)

    def test_empty_rows_are_skipped(self):
        """ردیف‌های خالی فرم قلم نمی‌سازند"""
        r = self._post(
            product_name=['Sugar', '', '   '],
            item_quantity=['5', '3', '4'],
            item_unit=['Kilogram', 'Kilogram', 'Kilogram'],
            item_description=['a', 'b', 'c'],
            item_product_id=[str(self.product_a.id), '', ''],
            item_brand_id=['', '', ''],
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WarehouseRequest.objects.get().items.count(), 1)

    def test_zero_or_invalid_quantity_rows_are_skipped(self):
        """مقدار صفر/نامعتبر قلم نمی‌شود — نه اینکه خطا بدهد"""
        for bad in ('0', '-3', 'abc', ''):
            with self.subTest(quantity=bad):
                WarehouseRequest.objects.all().delete()
                r = self._post(item_quantity=[bad])
                self.assertEqual(r.status_code, 200)  # هیچ قلمی → فرم با خطا
                self.assertEqual(WarehouseRequest.objects.count(), 0)

    def test_all_rows_empty_is_rejected(self):
        r = self._post(product_name=[''], item_quantity=[''])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseRequest.objects.count(), 0)
        self.assertContains(r, 'حداقل یک قلم کالا وارد کنید')

    def test_missing_unit_defaults_to_piece(self):
        r = self._post(item_unit=[''])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WarehouseRequest.objects.get().items.get().unit, 'عدد')

    def test_free_text_name_matches_product_in_same_cafe(self):
        """نامِ آزادِ منطبق با محصولِ همین کافه به همان محصول وصل می‌شود"""
        r = self._post(product_name=['sugar'], item_product_id=[''])
        self.assertEqual(r.status_code, 302)
        item = WarehouseRequest.objects.get().items.get()
        self.assertEqual(item.product_id, self.product_a.id)  # iexact

    def test_free_text_name_does_not_match_other_cafe_product(self):
        """نامِ محصولِ کافهٔ دیگر به محصولِ آن کافه وصل نمی‌شود"""
        r = self._post(product_name=['Tea'], item_product_id=[''])
        self.assertEqual(r.status_code, 302)
        item = WarehouseRequest.objects.get().items.get()
        self.assertIsNone(item.product_id)   # کافهٔ A محصولی به نام Tea ندارد
        self.assertEqual(item.name, 'Tea')   # ولی به‌عنوان نام آزاد ثبت می‌شود

    def test_foreign_product_id_does_not_leak_across_cafes(self):
        """شناسهٔ خامِ محصولِ کافهٔ دیگر نباید به محصولِ آن کافه وصل شود

        شناسه‌ای که از POST می‌آید فقط در محدودهٔ کافهٔ فعلی جستجو می‌شود؛ پس
        محصولِ کافهٔ B وصل نمی‌شود. قلم به‌صورت «نام آزاد» ثبت می‌شود تا
        درخواست کاربر از بین نرود (رفتار ۱۴۰۵-۰۶-۲۰).
        """
        r = self._post(product_name=['Tea'],
                       item_product_id=[str(self.product_b.id)])
        self.assertEqual(r.status_code, 302)
        item = WarehouseRequest.objects.get().items.get()
        self.assertIsNone(item.product_id)
        self.assertEqual(item.name, 'Tea')   # به‌عنوان نام آزاد ثبت می‌شود

    def test_brand_from_other_product_is_ignored(self):
        """برند باید به همان محصول تعلق داشته باشد؛ برندِ محصول دیگر ثبت نمی‌شود"""
        r = self._post(item_brand_id=[str(self.brand_b.id)])
        self.assertEqual(r.status_code, 302)
        item = WarehouseRequest.objects.get().items.get()
        self.assertEqual(item.product_id, self.product_a.id)
        self.assertIsNone(item.product_brand_id)

    def test_own_brand_is_recorded(self):
        r = self._post(item_brand_id=[str(self.brand_a.id)])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            WarehouseRequest.objects.get().items.get().product_brand_id,
            self.brand_a.id)


class WarehouseRequestListTests(_GoodsRequestBase):
    """دو صفحهٔ فهرست درخواست‌های انبار — پیش از این هیچ تستی نداشتند"""

    def setUp(self):
        super().setUp()
        self.own = WarehouseRequest.objects.create(
            user=self.user, cafe=self.cafe_a, status='pending')
        WarehouseRequestItem.objects.create(
            warehouse_request=self.own, product=self.product_a,
            name='Sugar', quantity=D('5'), unit='Kilogram')

        other_user = User.objects.create_user(
            username='bar_a', password='testpass123', role='bar', cafe=self.cafe_a)
        self.other_users_request = WarehouseRequest.objects.create(
            user=other_user, cafe=self.cafe_a, status='pending')

        b_user = User.objects.create_user(
            username='kitchen_b', password='testpass123', role='kitchen',
            cafe=self.cafe_b)
        self.foreign_request = WarehouseRequest.objects.create(
            user=b_user, cafe=self.cafe_b, status='pending')

    def test_requester_list_shows_only_own_requests(self):
        r = self.client.get(self._url('warehouse_requests_list'))
        self.assertEqual(r.status_code, 200)
        ids = [w.id for w in r.context['requests']]
        self.assertEqual(ids, [self.own.id])
        self.assertNotIn(self.other_users_request.id, ids)
        self.assertNotIn(self.foreign_request.id, ids)

    def test_purchasing_list_requires_purchasing_role(self):
        """درخواست‌دهندهٔ آشپزخانه به فهرست مسئول خرید راه ندارد"""
        r = self.client.get(self._url('purchasing_warehouse_requests_list'))
        self.assertEqual(r.status_code, 302)  # role_required → ریدایرکت به login

    def test_purchasing_list_shows_cafe_requests(self):
        officer = User.objects.create_user(
            username='purch_a', password='testpass123', role='purchasing',
            cafe=self.cafe_a)
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='purch_a', password='testpass123')
        r = self.client.get(self._url('purchasing_warehouse_requests_list'))
        self.assertEqual(r.status_code, 200)
        ids = [w.id for w in r.context['requests']]
        self.assertIn(self.own.id, ids)
        self.assertIn(self.other_users_request.id, ids)   # همان کافه
        self.assertNotIn(self.foreign_request.id, ids)    # کافهٔ دیگر

    def test_purchasing_list_status_filter(self):
        officer = User.objects.create_user(
            username='purch2', password='testpass123', role='purchasing',
            cafe=self.cafe_a)
        self.other_users_request.status = 'fulfilled'
        self.other_users_request.save()
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='purch2', password='testpass123')
        r = self.client.get(self._url('purchasing_warehouse_requests_list'),
                            {'status': 'pending'})
        ids = [w.id for w in r.context['requests']]
        self.assertIn(self.own.id, ids)
        self.assertNotIn(self.other_users_request.id, ids)


# ═══════════════════════════════════════════════════════════════════════════
# ۲. پردازش درخواست انبار — شاخه‌های تست‌نشده
# ═══════════════════════════════════════════════════════════════════════════
class ProcessWarehouseRequestBranchTests(_GoodsRequestBase):
    """شاخه‌های ارجاع به خرید / عدم تامین / قلمِ آزاد در process_warehouse_request"""

    def setUp(self):
        super().setUp()
        self.officer = User.objects.create_user(
            username='purch_wh', password='testpass123', role='purchasing',
            cafe=self.cafe_a)
        self.wh_request = WarehouseRequest.objects.create(
            user=self.user, cafe=self.cafe_a, status='pending')
        self.item = WarehouseRequestItem.objects.create(
            warehouse_request=self.wh_request, product=self.product_a,
            name='Sugar', quantity=D('10'), unit='Kilogram')

    def _post(self, **data):
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='purch_wh', password='testpass123')
        return self.client.post(
            self._url('process_warehouse_request', request_id=self.wh_request.id),
            data={'action': 'submit', **data})

    def test_to_purchase_creates_new_purchase_request(self):
        """«ارجاع به خرید»: درخواست خرید جدید + مسیر مستقیم به مسئول انبار"""
        r = self._post(**{f'supply_status_{self.item.id}': 'to_purchase'})
        self.assertEqual(r.status_code, 302)

        self.wh_request.refresh_from_db()
        self.assertEqual(self.wh_request.status, 'approved_purchase')
        new_purchase = self.wh_request.converted_to_purchase
        self.assertIsNotNone(new_purchase)
        self.assertEqual(new_purchase.cafe, self.cafe_a)
        self.assertEqual(new_purchase.status, 'pending')
        self.assertEqual(new_purchase.items.count(), 1)
        self.assertEqual(new_purchase.items.get().name, 'Sugar')
        self.assertEqual(new_purchase.items.get().quantity, 10)
        # ارسال مستقیم به انبار: تخصیص مشترک در انتظار ساخته می‌شود
        self.assertTrue(OfficerAssignment.objects.filter(
            item__request=new_purchase, target_role='warehouse',
            status='pending', assigned_to__isnull=True).exists())

    def test_not_supplied_marks_declined(self):
        r = self._post(**{f'supply_status_{self.item.id}': 'declined'})
        self.assertEqual(r.status_code, 302)
        self.wh_request.refresh_from_db()
        self.assertEqual(self.wh_request.status, 'declined')
        self.item.refresh_from_db()
        self.assertEqual(self.item.supply_status, 'declined')

    def test_mixed_from_stock_and_to_purchase_is_fulfilled(self):
        """قلم مختلط: یکی از انبار و یکی ارجاع → وضعیت «تامین شده»"""
        second = WarehouseRequestItem.objects.create(
            warehouse_request=self.wh_request, product=self.product_a,
            name='Flour', quantity=D('3'), unit='Kilogram')
        stock = self._make_stock(D('50'))
        r = self._post(**{
            f'supply_status_{self.item.id}': 'from_stock',
            f'stock_item_{self.item.id}': str(stock.id),
            f'supplied_qty_{self.item.id}': '4',
            f'supply_status_{second.id}': 'to_purchase',
        })
        self.assertEqual(r.status_code, 302)
        self.wh_request.refresh_from_db()
        self.assertEqual(self.wh_request.status, 'fulfilled')
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, D('46'))

    def test_from_stock_without_stock_item_is_rejected(self):
        """«از انبار» بدون انتخاب موجودی → فرم با خطا، بدون هیچ نوشتنی"""
        from purchases.models import StockItem, StockTransaction
        stock = self._make_stock(D('50'))
        r = self._post(**{
            f'supply_status_{self.item.id}': 'from_stock',
            f'stock_item_{self.item.id}': '',
            f'supplied_qty_{self.item.id}': '4',
        })
        self.assertEqual(r.status_code, 200)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, D('50'))
        self.assertEqual(StockTransaction.objects.count(), 0)
        self.wh_request.refresh_from_db()
        self.assertEqual(self.wh_request.status, 'pending')

    def test_free_text_item_without_product_can_be_referred_to_purchase(self):
        """قلمِ بدون محصول (نام آزاد) هم می‌تواند به خرید ارجاع شود"""
        free = WarehouseRequestItem.objects.create(
            warehouse_request=self.wh_request, product=None,
            name='دستمال کاغذی', quantity=D('2'), unit='عدد')
        r = self._post(**{
            f'supply_status_{self.item.id}': 'declined',
            f'supply_status_{free.id}': 'to_purchase',
        })
        self.assertEqual(r.status_code, 302)
        new_purchase = WarehouseRequest.objects.get(
            id=self.wh_request.id).converted_to_purchase
        names = [i.name for i in new_purchase.items.all()]
        self.assertEqual(names, ['دستمال کاغذی'])

    def _make_stock(self, qty):
        from purchases.models import StockItem, Warehouse
        wh = Warehouse.objects.create(
            cafe=self.cafe_a, code=f'wh{StockItem.objects.count()}', name='انبار',
            is_active=True)
        return StockItem.objects.create(
            product=self.product_a, warehouse=wh, quantity=qty, unit=self.unit_a)


# ═══════════════════════════════════════════════════════════════════════════
# ۳. تایید مدیر برای درخواستِ عادیِ درخواست‌دهنده
# ═══════════════════════════════════════════════════════════════════════════
class ManagerApprovalOfRequesterRequestTests(_GoodsRequestBase):
    """تایید مدیر فقط برای مسیر درخواست خریدِ انبار تست شده بود؛ درخواستِ عادی نه"""

    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user(
            username='manager_a', password='testpass123', role='manager',
            cafe=self.cafe_a)
        self.purchase_request = PurchaseRequest.objects.create(
            user=self.user, cafe=self.cafe_a, status='pending_manager_approval')
        PurchaseItem.objects.create(
            request=self.purchase_request, product=self.product_a, name='Sugar',
            quantity=5, unit='Kilogram')

    def _login_manager(self):
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='manager_a', password='testpass123')

    def test_pending_list_shows_requester_request(self):
        self._login_manager()
        r = self.client.get(self._url('manager_pending_requests'))
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.purchase_request.id,
                      [q.id for q in r.context['requests']])

    def test_approve_moves_to_pending_and_records_manager(self):
        self._login_manager()
        r = self.client.post(
            self._url('manager_approve_request',
                      request_id=self.purchase_request.id))
        self.assertEqual(r.status_code, 302)
        self.purchase_request.refresh_from_db()
        self.assertEqual(self.purchase_request.status, 'pending')
        self.assertTrue(self.purchase_request.manager_confirmed)
        self.assertEqual(self.purchase_request.manager_confirmed_by, self.manager)
        self.assertIsNotNone(self.purchase_request.manager_confirmed_at)

    def test_approve_is_idempotent_guarded(self):
        """تایید دوباره: وضعیت دیگر pending_manager_approval نیست → هشدار، بدون اثر"""
        self._login_manager()
        self.client.post(self._url('manager_approve_request',
                                   request_id=self.purchase_request.id))
        first_confirmed_at = PurchaseRequest.objects.get(
            id=self.purchase_request.id).manager_confirmed_at
        r = self.client.post(self._url('manager_approve_request',
                                       request_id=self.purchase_request.id))
        self.assertEqual(r.status_code, 302)
        self.purchase_request.refresh_from_db()
        self.assertEqual(self.purchase_request.manager_confirmed_at,
                         first_confirmed_at)  # دست‌نخورده

    def test_requester_cannot_open_manager_pending_list(self):
        r = self.client.get(self._url('manager_pending_requests'))
        self.assertEqual(r.status_code, 302)

    def test_manager_cannot_reach_other_cafe_url_at_all(self):
        """مدیرِ کافهٔ B با URL کافهٔ A: میان‌افزار پیش از view رد می‌کند → 404

        (لایهٔ اولِ محافظت. تستِ لایهٔ دوم — گاردِ داخلِ view — پایین‌تر.)
        """
        User.objects.create_user(
            username='manager_b', password='testpass123', role='manager',
            cafe=self.cafe_b)
        _set_cafe_session(self.client, self.cafe_b)
        self.client.login(username='manager_b', password='testpass123')
        r = self.client.post(
            self._url('manager_approve_request',
                      request_id=self.purchase_request.id))
        self.assertEqual(r.status_code, 404)
        self.purchase_request.refresh_from_db()
        self.assertEqual(self.purchase_request.status,
                         'pending_manager_approval')  # تایید نشد

    def test_view_guard_blocks_request_from_another_cafe(self):
        """گاردِ داخلِ view: مدیر در URL کافهٔ خودش، شناسهٔ درخواست کافهٔ دیگر

        میان‌افزار اینجا مانع نیست (کافهٔ URL با کافهٔ مدیر می‌خواند)؛ تنها
        چیزی که جلوی تایید را می‌گیرد مقایسهٔ `purchase_request.cafe != cafe`
        در ابتدای view است.
        """
        User.objects.create_user(
            username='manager_b2', password='testpass123', role='manager',
            cafe=self.cafe_b)
        _set_cafe_session(self.client, self.cafe_b)
        self.client.login(username='manager_b2', password='testpass123')
        # درخواست مالِ کافهٔ A است؛ مدیر B آن را از URL کافهٔ خودش صدا می‌زند
        r = self.client.post(
            reverse('manager_approve_request',
                    kwargs={'cafe_code': 'b',
                            'request_id': self.purchase_request.id}))
        self.assertEqual(r.status_code, 302)  # ریدایرکت با پیام «دسترسی غیرمجاز»
        self.purchase_request.refresh_from_db()
        self.assertEqual(self.purchase_request.status,
                         'pending_manager_approval')
        self.assertFalse(self.purchase_request.manager_confirmed)

    def test_pending_list_is_cafe_scoped(self):
        foreign = PurchaseRequest.objects.create(
            user=self.user, cafe=self.cafe_b, status='pending_manager_approval')
        self._login_manager()
        r = self.client.get(self._url('manager_pending_requests'))
        ids = [q.id for q in r.context['requests']]
        self.assertIn(self.purchase_request.id, ids)
        self.assertNotIn(foreign.id, ids)


    def test_foreign_product_id_does_not_leak_across_cafes(self):
        """شناسهٔ خامِ محصولِ کافهٔ دیگر در فرم «درخواست کالا» هم وصل نمی‌شود

        همان نشتِ فرمِ انبار در این نقطهٔ ورود هم وجود داشت (هر دو از یک
        الگوی مشترک کپی شده بودند)؛ این تست بازگشتِ آن را می‌گیرد.
        """
        r = self.client.post(self._url('purchase_request'), data={
            'product_name': ['Tea'],
            'item_quantity': ['5'],
            'item_unit': ['Kilogram'],
            'item_description': [''],
            'item_product_id': [str(self.product_b.id)],
            'item_brand_id': [''],
        })
        self.assertEqual(r.status_code, 302)
        # درخواستِ تازه‌ساخته، جدا از درخواستِ از پیش موجودِ fixture
        pr = PurchaseRequest.objects.exclude(id=self.purchase_request.id).get()
        self.assertEqual(pr.cafe, self.cafe_a)
        item = pr.items.get()
        self.assertIsNone(item.product_id)
        self.assertEqual(item.name, 'Tea')


# ═══════════════════════════════════════════════════════════════════════════
# ۴. پردازش درخواست خرید (واحد خرید) — پیش از این هیچ تستی نداشت
# ═══════════════════════════════════════════════════════════════════════════
class ProcessPurchaseRequestTests(_GoodsRequestBase):
    """process_purchase_request — نقطهٔ ورودیِ مسئول خرید که تست نداشت"""

    def setUp(self):
        super().setUp()
        self.officer = User.objects.create_user(
            username='purchaser', password='testpass123', role='purchasing',
            cafe=self.cafe_a)
        self.purchase_request = PurchaseRequest.objects.create(
            user=self.user, cafe=self.cafe_a, status='pending')
        self.item = PurchaseItem.objects.create(
            request=self.purchase_request, product=self.product_a, name='Sugar',
            quantity=5, unit='Kilogram', supply_status='pending')

    def _login(self):
        _set_cafe_session(self.client, self.cafe_a)
        self.client.login(username='purchaser', password='testpass123')

    def test_get_renders(self):
        self._login()
        r = self.client.get(self._url('process_purchase_request',
                                      request_id=self.purchase_request.id))
        self.assertEqual(r.status_code, 200)

    def test_all_items_processed_marks_request_purchased(self):
        self._login()
        r = self.client.post(
            self._url('process_purchase_request',
                      request_id=self.purchase_request.id),
            data={f'purchased_qty_{self.item.id}': '5',
                  f'supply_status_{self.item.id}': 'purchased'})
        self.assertEqual(r.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.purchased_quantity, 5)
        self.assertEqual(self.item.supply_status, 'purchased')
        self.purchase_request.refresh_from_db()
        self.assertEqual(self.purchase_request.status, 'purchased')
        self.assertEqual(self.purchase_request.processed_by, self.officer)
        self.assertIsNotNone(self.purchase_request.processed_at)

    def test_unprocessed_item_keeps_request_open(self):
        """قلمِ در انتظار: درخواست «خریداری‌شده» نمی‌شود"""
        second = PurchaseItem.objects.create(
            request=self.purchase_request, product=self.product_a, name='Flour',
            quantity=2, unit='Kilogram', supply_status='pending')
        self._login()
        self.client.post(
            self._url('process_purchase_request',
                      request_id=self.purchase_request.id),
            data={f'purchased_qty_{self.item.id}': '5',
                  f'supply_status_{self.item.id}': 'purchased',
                  f'supply_status_{second.id}': 'pending'})
        self.purchase_request.refresh_from_db()
        self.assertEqual(self.purchase_request.status, 'pending')

    def test_no_stock_mutation_happens_here(self):
        """این نقطهٔ ورود موجودی را تغییر نمی‌دهد — فقط وضعیت اقلام را"""
        from purchases.models import StockItem, StockTransaction, Warehouse
        wh = Warehouse.objects.create(
            cafe=self.cafe_a, code='whX', name='انبار', is_active=True)
        stock = StockItem.objects.create(
            product=self.product_a, warehouse=wh, quantity=D('50'),
            unit=self.unit_a)
        self._login()
        self.client.post(
            self._url('process_purchase_request',
                      request_id=self.purchase_request.id),
            data={f'purchased_qty_{self.item.id}': '5',
                  f'supply_status_{self.item.id}': 'purchased'})
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, D('50'))
        self.assertEqual(StockTransaction.objects.count(), 0)
