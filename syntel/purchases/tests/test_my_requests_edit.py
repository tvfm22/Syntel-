"""تست بخش «درخواست‌های من»: بازطراحی قالب + ویرایش درخواست تا قبل از تعیین وضعیت توسط مسئول انبار."""
from django.contrib.messages import get_messages
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    User, Cafe, PurchaseRequest, PurchaseItem, OfficerAssignment, ActivityLog,
)


class MyRequestsListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(name='کافه تست', code='myreq')
        cls.requester = User.objects.create_user(
            username='myreq_owner', password='testpass123',
            role='requester', cafe=cls.cafe, is_active=True,
        )
        cls.other_requester = User.objects.create_user(
            username='myreq_other', password='testpass123',
            role='kitchen', cafe=cls.cafe, is_active=True,
        )
        cls.manager = User.objects.create_user(
            username='myreq_manager', password='testpass123',
            role='manager', cafe=cls.cafe, is_active=True,
        )

        # درخواست در انتظار (تخصیص انبار pending) → قابل ویرایش
        cls.req_pending = PurchaseRequest.objects.create(
            user=cls.requester, cafe=cls.cafe, status='pending_manager_approval')
        cls.item_a = PurchaseItem.objects.create(
            request=cls.req_pending, name='شیر', quantity=10, unit='عدد')
        OfficerAssignment.objects.create(
            item=cls.item_a, target_role='warehouse', status='pending')

        # درخواستی که مسئول انبار تامین کرده → قفل
        cls.req_decided = PurchaseRequest.objects.create(
            user=cls.requester, cafe=cls.cafe, status='purchased')
        cls.item_b = PurchaseItem.objects.create(
            request=cls.req_decided, name='نان', quantity=5, unit='بسته')
        OfficerAssignment.objects.create(
            item=cls.item_b, target_role='warehouse', status='fulfilled')

        # درخواست ردشده توسط مدیر → قفل
        cls.req_rejected = PurchaseRequest.objects.create(
            user=cls.requester, cafe=cls.cafe, status='rejected')
        cls.item_c = PurchaseItem.objects.create(
            request=cls.req_rejected, name='ماست', quantity=3, unit='عدد')
        OfficerAssignment.objects.create(
            item=cls.item_c, target_role='warehouse', status='pending')

        # درخواست متعلق به کاربر دیگر (نباید در لیست صاحب دیده شود)
        cls.req_other = PurchaseRequest.objects.create(
            user=cls.other_requester, cafe=cls.cafe, status='pending_manager_approval')
        PurchaseItem.objects.create(
            request=cls.req_other, name='پنیر', quantity=2, unit='کیلو')

    def login_as(self, user):
        c = Client()
        c.login(username=user.username, password='testpass123')
        return c

    def test_edit_chip_for_editable_request(self):
        c = self.login_as(self.requester)
        r = c.get(reverse('purchase_requests_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        # درخواست در انتظار: دکمه ویرایش دارد
        self.assertContains(
            r, reverse('edit_my_request', args=[self.cafe.code, self.req_pending.id]))
        # درخواست تعیین‌وضعیت‌شده و ردشده: فقط قفل
        self.assertNotContains(
            r, reverse('edit_my_request', args=[self.cafe.code, self.req_decided.id]))
        self.assertNotContains(
            r, reverse('edit_my_request', args=[self.cafe.code, self.req_rejected.id]))
        self.assertContains(r, 'act-locked')

    def test_list_shows_only_own_requests(self):
        c = self.login_as(self.requester)
        r = c.get(reverse('purchase_requests_list', args=[self.cafe.code]))
        listed_ids = [req.id for req in r.context['requests']]
        self.assertIn(self.req_pending.id, listed_ids)
        self.assertIn(self.req_decided.id, listed_ids)
        # درخواست متعلق به کاربر دیگر در لیست نیست
        self.assertNotIn(self.req_other.id, listed_ids)

    def test_manager_list_renders(self):
        c = self.login_as(self.manager)
        r = c.get(reverse('purchase_requests_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'requests-container')


class EditMyRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(name='کافه تست', code='myedit')
        cls.requester = User.objects.create_user(
            username='myedit_owner', password='testpass123',
            role='requester', cafe=cls.cafe, is_active=True,
        )
        cls.other_requester = User.objects.create_user(
            username='myedit_other', password='testpass123',
            role='kitchen', cafe=cls.cafe, is_active=True,
        )

        cls.req = PurchaseRequest.objects.create(
            user=cls.requester, cafe=cls.cafe, status='pending_manager_approval')
        cls.item_a = PurchaseItem.objects.create(
            request=cls.req, name='شیر', quantity=10, unit='عدد', description='کم‌چرب')
        cls.item_b = PurchaseItem.objects.create(
            request=cls.req, name='نان', quantity=5, unit='بسته')
        for item in (cls.item_a, cls.item_b):
            OfficerAssignment.objects.create(
                item=item, target_role='warehouse', status='pending')

        cls.req_locked = PurchaseRequest.objects.create(
            user=cls.requester, cafe=cls.cafe, status='purchased')
        cls.item_locked = PurchaseItem.objects.create(
            request=cls.req_locked, name='ماست', quantity=3, unit='عدد')
        OfficerAssignment.objects.create(
            item=cls.item_locked, target_role='warehouse', status='fulfilled')

    def login_as(self, user):
        c = Client()
        c.login(username=user.username, password='testpass123')
        return c

    def url(self, req):
        return reverse('edit_my_request', args=[self.cafe.code, req.id])

    def test_owner_can_open_edit_page(self):
        c = self.login_as(self.requester)
        r = c.get(self.url(self.req))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'شیر')
        self.assertContains(r, 'نان')
        self.assertContains(r, 'کم‌چرب')

    def test_non_owner_gets_403(self):
        c = self.login_as(self.other_requester)
        r = c.get(self.url(self.req))
        self.assertEqual(r.status_code, 403)

    def test_locked_request_edit_redirects(self):
        c = self.login_as(self.requester)
        r = c.get(self.url(self.req_locked))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            r.url, reverse('purchase_requests_list', args=[self.cafe.code]))

    def test_edit_updates_items_and_delivery(self):
        c = self.login_as(self.requester)
        r = c.post(self.url(self.req), {
            'item_id': [str(self.item_a.id), str(self.item_b.id)],
            'name': ['شیر پرچرب', 'نان سنگک'],
            'quantity': ['20', '8'],
            'unit': ['عدد', 'بسته'],
            'description': ['', 'تازه'],
            'delivery_date': '2099-01-01',
        })
        self.assertEqual(r.status_code, 302)
        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.req.refresh_from_db()
        self.assertEqual(self.item_a.name, 'شیر پرچرب')
        self.assertEqual(self.item_a.quantity, 20)
        self.assertEqual(self.item_b.description, 'تازه')
        self.assertEqual(self.req.delivery_date.isoformat(), '2099-01-01')
        # لاگ فعالیت ثبت شده
        self.assertTrue(ActivityLog.objects.filter(
            request=self.req, user=self.requester, action='updated').exists())

    def test_edit_adds_new_item_routed_to_warehouse(self):
        # فیکسچر این کلاس یک تحویل تامینشدهٔ تاییدنشده دارد؛ تا تایید نشود،
        # قفل «دریافت قبل از درخواست جدید» اجازهٔ افزودن قلم نمیدهد.
        PurchaseItem.objects.filter(pk=self.item_locked.pk).update(
            received_confirmed_at=timezone.now(),
            received_confirmed_by=self.requester)
        c = self.login_as(self.requester)
        r = c.post(self.url(self.req), {
            'item_id': [str(self.item_a.id), str(self.item_b.id), ''],
            'name': ['شیر', 'نان', 'پنیر'],
            'quantity': ['10', '5', '3'],
            'unit': ['عدد', 'بسته', 'کیلو'],
            'description': ['', '', ''],
        })
        self.assertEqual(r.status_code, 302)
        new_item = self.req.items.get(name='پنیر')
        self.assertEqual(new_item.quantity, 3)
        # قلم جدید به صندوق مسئول انبار رفت
        self.assertTrue(OfficerAssignment.objects.filter(
            item=new_item, target_role='warehouse', status='pending').exists())

    def test_edit_removes_item(self):
        c = self.login_as(self.requester)
        r = c.post(self.url(self.req), {
            'item_id': [str(self.item_a.id), str(self.item_b.id)],
            'name': ['شیر', 'نان'],
            'quantity': ['10', '5'],
            'unit': ['عدد', 'بسته'],
            'description': ['', ''],
            'remove_item_id': [str(self.item_b.id)],
        })
        self.assertEqual(r.status_code, 302)
        self.assertFalse(PurchaseItem.objects.filter(id=self.item_b.id).exists())
        self.assertTrue(PurchaseItem.objects.filter(id=self.item_a.id).exists())

    def test_edit_empty_name_renders_error(self):
        c = self.login_as(self.requester)
        r = c.post(self.url(self.req), {
            'item_id': [str(self.item_a.id)],
            'name': [''],
            'quantity': ['10'],
            'unit': ['عدد'],
            'description': [''],
        })
        self.assertEqual(r.status_code, 200)
        msgs = [m.message for m in get_messages(r.wsgi_request)]
        self.assertTrue(any('الزامی' in m for m in msgs))
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.name, 'شیر')

    def test_locked_request_post_is_rejected(self):
        c = self.login_as(self.requester)
        r = c.post(self.url(self.req_locked), {
            'item_id': [str(self.item_locked.id)],
            'name': ['ماست پرچرب'],
            'quantity': ['9'],
            'unit': ['عدد'],
            'description': [''],
        })
        self.assertEqual(r.status_code, 302)
        self.item_locked.refresh_from_db()
        self.assertEqual(self.item_locked.name, 'ماست')
