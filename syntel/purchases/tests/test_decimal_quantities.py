"""تست ذخیره و نمایش مقدار اعشاری در درخواست‌ها (بدون رند و بدون صفر اضافی).

قواعد نمایش: اگر عدد با اعشار وارد شود، تا ۲ رقم اعشار نمایش داده می‌شود؛
اگراعدادی صحیح باشد، اعشاری نمایش داده نمی‌شود (مثلاً 3 نه 3.00 یا 3.0000).
"""
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse

from purchases.models import (
    User, Cafe, Product, PurchaseRequest, PurchaseItem, OfficerAssignment,
)


class DecimalQuantityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(name='کافه تست', code='deq')
        for pname in ('شیر', 'نان', 'پنیر'):
            Product.objects.create(name=pname, cafe=cls.cafe, is_active=True, is_complete=True)
        cls.requester = User.objects.create_user(
            username='deq_owner', password='testpass123',
            role='requester', cafe=cls.cafe, is_active=True,
        )
        cls.officer = User.objects.create_user(
            username='deq_officer', password='testpass123',
            role='warehouse', cafe=cls.cafe, is_active=True,
        )

    def login_as(self, user):
        c = Client()
        c.login(username=user.username, password='testpass123')
        return c

    def test_purchase_request_keeps_decimals(self):
        c = self.login_as(self.requester)
        r = c.post(reverse('purchase_request', args=[self.cafe.code]), {
            'product_name': ['شیر', 'نان', 'پنیر'],
            'item_quantity': ['2.5', '3', '0.75'],
            'item_unit': ['کیلو', 'عدد', 'کیلو'],
            'item_description': ['', '', ''],
            'item_product_id': ['', '', ''],
            'item_brand_id': ['', '', ''],
        })
        # فرم درخواست موفقیت را روی صفحه success نشان می‌دهد
        self.assertEqual(r.status_code, 302)
        req = PurchaseRequest.objects.get(user=self.requester)
        qty_by_name = {i.name: i.quantity for i in req.items.all()}
        self.assertEqual(qty_by_name['شیر'], Decimal('2.5000'))
        self.assertEqual(qty_by_name['نان'], Decimal('3.0000'))
        self.assertEqual(qty_by_name['پنیر'], Decimal('0.7500'))

    def test_detail_page_shows_decimals_cleanly(self):
        c = self.login_as(self.requester)
        c.post(reverse('purchase_request', args=[self.cafe.code]), {
            'product_name': ['شیر'],
            'item_quantity': ['2.5'],
            'item_unit': ['کیلو'],
            'item_description': [''],
            'item_product_id': [''],
            'item_brand_id': [''],
        })
        req = PurchaseRequest.objects.get(user=self.requester)
        r = c.get(reverse('purchase_request_detail',
                          args=[self.cafe.code, req.id]))
        self.assertEqual(r.status_code, 200)
        # اعشار بدون صفر اضافی
        self.assertContains(r, '2.5')
        self.assertNotContains(r, '2.5000')
        self.assertNotContains(r, '2.50')

    def test_int_shows_without_decimals(self):
        c = self.login_as(self.requester)
        c.post(reverse('purchase_request', args=[self.cafe.code]), {
            'product_name': ['نان'],
            'item_quantity': ['3'],
            'item_unit': ['عدد'],
            'item_description': [''],
            'item_product_id': [''],
            'item_brand_id': [''],
        })
        req = PurchaseRequest.objects.get(user=self.requester)
        r = c.get(reverse('purchase_request_detail',
                          args=[self.cafe.code, req.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'مقدار')
        self.assertNotContains(r, '3.00')
        self.assertNotContains(r, '3.000')
        # مقدار صحیح بدون اعشار
        item = req.items.first()
        from purchases.templatetags.custom_filters import trim_decimal
        self.assertEqual(trim_decimal(item.quantity), '3')

    def test_officer_dashboard_shows_requested_qty_cleanly(self):
        c = self.login_as(self.requester)
        c.post(reverse('purchase_request', args=[self.cafe.code]), {
            'product_name': ['شیر'],
            'item_quantity': ['2.5'],
            'item_unit': ['کیلو'],
            'item_description': [''],
            'item_product_id': [''],
            'item_brand_id': [''],
        })
        c_o = self.login_as(self.officer)
        r = c_o.get(reverse('warehouse_officer_dashboard', args=[self.cafe.code]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '2.5 کیلو')
        self.assertNotContains(r, '2.5000')

    def test_my_request_edit_keeps_decimals(self):
        c = self.login_as(self.requester)
        c.post(reverse('purchase_request', args=[self.cafe.code]), {
            'product_name': ['شیر'],
            'item_quantity': ['2'],
            'item_unit': ['کیلو'],
            'item_description': [''],
            'item_product_id': [''],
            'item_brand_id': [''],
        })
        req = PurchaseRequest.objects.get(user=self.requester)
        item = req.items.first()
        # مطمئن شو قلم هنوز قفل نشده (تخصیص pending دارد)
        r = c.post(reverse('edit_my_request', args=[self.cafe.code, req.id]), {
            'item_id': [str(item.id)],
            'name': ['شیر کم‌چرب'],
            'quantity': ['1.25'],
            'unit': ['کیلو'],
            'description': [''],
        })
        self.assertEqual(r.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('1.2500'))
        # فرم ویرایش مقدار را بدون اعشار اضافی نشان می‌دهد
        r = c.get(reverse('edit_my_request', args=[self.cafe.code, req.id]))
        self.assertContains(r, 'value="1.25"')
        self.assertNotContains(r, '1.2500')

    def test_purchased_quantity_keeps_decimals(self):
        req = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending_manager_approval')
        item = PurchaseItem.objects.create(
            request=req, name='شیر', quantity=Decimal('2.5'), unit='کیلو')
        OfficerAssignment.objects.create(
            item=item, target_role='warehouse', status='pending')
        # مقدار خریداری‌شده هم اعشاری می‌گیرد
        item.purchased_quantity = Decimal('3.75')
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.purchased_quantity, Decimal('3.7500'))
