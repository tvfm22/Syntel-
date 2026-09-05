"""تست ارسال مستقیم درخواست کالا به مسئول انبار.

۱. POST به purchase_request → هر قلم تخصیص انبار مشترکِ در انتظار می‌گیرد
۲. وضعیت درخواست از «pending_manager_approval» به «pending» می‌رود
۳. مسئول انبار قلم را در داشبورد خودش می‌بیند (صندوق مشترک)
۴. درخواست از لیست «در انتظار تایید مدیر» حذف می‌شود
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from purchases.models import Cafe, OfficerAssignment, Product, PurchaseRequest, User


class DirectToWarehouseBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(code='dw', name='Direct Warehouse Cafe')
        cls.requester = User.objects.create_user(
            username='req_dw', password='x', role='requester', cafe=cls.cafe)
        cls.officer = User.objects.create_user(
            username='wh_dw', password='x', role='warehouse', cafe=cls.cafe)
        cls.manager = User.objects.create_user(
            username='mgr_dw', password='x', role='manager', cafe=cls.cafe)
        cls.product = Product.objects.create(name='کالای مستقیم', cafe=cls.cafe, is_complete=True)

    def setUp(self):
        session = self.client.session
        session['cafe_id'] = self.cafe.id
        session.save()
        self.client.force_login(self.requester)


@override_settings(SESSION_COOKIE_SAMESITE='Lax')
class TestDirectToWarehouse(DirectToWarehouseBase):
    def _post_request(self):
        url = reverse('purchase_request', kwargs={'cafe_code': 'dw'})
        return self.client.post(url, data={
            'product_name': ['کالای مستقیم'],
            'item_quantity': ['3'],
            'item_unit': ['عدد'],
            'item_description': ['برای تست'],
            'item_product_id': [str(self.product.id)],
        })

    def test_items_get_pending_warehouse_assignment(self):
        resp = self._post_request()
        self.assertEqual(resp.status_code, 302)
        req = PurchaseRequest.objects.order_by('-id').first()
        self.assertEqual(req.source, 'requester')
        assigns = OfficerAssignment.objects.filter(item__request=req)
        self.assertEqual(assigns.count(), 1)
        a = assigns.first()
        self.assertEqual(a.target_role, 'warehouse')
        self.assertIsNone(a.assigned_to)  # صندوق مشترک
        self.assertEqual(a.status, 'pending')

    def test_request_status_becomes_pending(self):
        self._post_request()
        req = PurchaseRequest.objects.order_by('-id').first()
        self.assertEqual(req.status, 'pending')

    def test_warehouse_officer_sees_item_in_dashboard(self):
        self._post_request()
        self.client.force_login(self.officer)
        url = reverse('warehouse_officer_dashboard', kwargs={'cafe_code': 'dw'})
        # GET بدون delivery به ‎?delivery=<امروز> ریدایرکت می‌شود؛ با follow دنبال می‌کنیم
        resp = self.client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'کالای مستقیم')

    def test_request_not_in_manager_pending_list(self):
        self._post_request()
        self.client.force_login(self.manager)
        url = reverse('manager_pending_requests', kwargs={'cafe_code': 'dw'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'کالای مستقیم')
