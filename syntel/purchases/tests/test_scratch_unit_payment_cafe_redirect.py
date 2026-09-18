"""Scratch verification: NoReverseMatch on unit/payment-type/category admin pages (2026-09-17).

Reported bug: POST /baba_adam/units/2/update/ → NoReverseMatch for 'unit_list'
(URL pattern is cafe-prefixed: '<cafe_code>/units/').

Root cause: post-save redirects used bare `redirect("unit_list")` while
unit_list/payment_type_list/category_list/requester_list/invoice_issue_list/
notifications_list/backup_management are all cafe-prefixed URL names.

Covers:
1. unit update/create/delete POST now redirect to the cafe-prefixed list (302, no crash).
2. Same for payment_type and category.
3. Notification mark-read/delete redirects no longer crash.
4. Cross-cafe unit/category/payment-type edit+delete is 404 (tenant scoping).
5. Unit/Category lists are scoped to the current cafe.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from purchases.models import (
    Cafe, Category, Notification, PaymentType, Unit,
)

User = get_user_model()


class UnitPaymentTypeCafeRedirectTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafeA = Cafe.objects.create(name='Cafe A', code='ca', is_active=True)
        self.cafeB = Cafe.objects.create(name='Cafe B', code='cb', is_active=True)
        self.managerA = User.objects.create_user(
            username='mgr_a', password='pass12345', role='manager', cafe=self.cafeA)
        self.managerB = User.objects.create_user(
            username='mgr_b', password='pass12345', role='manager', cafe=self.cafeB)
        self.unitA = Unit.objects.create(name='کیلوگرم A', symbol='kg', cafe=self.cafeA)
        self.unitB = Unit.objects.create(name='کیلوگرم B', symbol='kg', cafe=self.cafeB)
        self.payA = PaymentType.objects.create(name='نقدی A', cafe=self.cafeA)
        self.payB = PaymentType.objects.create(name='نقدی B', cafe=self.cafeB)
        self.catA = Category.objects.create(name='دسته A', cafe=self.cafeA)
        self.catB = Category.objects.create(name='دسته B', cafe=self.cafeB)

    def login(self, user, cafe_code):
        self.client.login(username=user.username, password='pass12345')
        session = self.client.session
        session['selected_cafe_id'] = user.cafe.id
        session['selected_cafe_code'] = cafe_code
        session.save()

    # ── 1. unit redirects (the reported crash) ───────────────────────
    def test_unit_update_post_redirects_with_cafe_code(self):
        self.login(self.managerA, 'ca')
        resp = self.client.post(
            reverse('unit_update', kwargs={'cafe_code': 'ca', 'unit_id': self.unitA.id}),
            {'name': 'کیلوگرم', 'symbol': 'kg'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('unit_list', kwargs={'cafe_code': 'ca'}))

    def test_unit_create_post_redirects_with_cafe_code(self):
        self.login(self.managerA, 'ca')
        resp = self.client.post(reverse('unit_create', kwargs={'cafe_code': 'ca'}),
                                {'name': 'لیتر', 'symbol': 'L'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('unit_list', kwargs={'cafe_code': 'ca'}))
        # واحد جدید به کافه جاری وصل میشود
        self.assertEqual(Unit.objects.get(name='لیتر').cafe, self.cafeA)

    def test_unit_delete_post_redirects_with_cafe_code(self):
        self.login(self.managerA, 'ca')
        resp = self.client.post(
            reverse('unit_delete', kwargs={'cafe_code': 'ca', 'unit_id': self.unitA.id}))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('unit_list', kwargs={'cafe_code': 'ca'}))

    # ── 2. payment type / category redirects ─────────────────────────
    def test_payment_type_post_redirects_with_cafe_code(self):
        self.login(self.managerA, 'ca')
        for name, kwargs in (('payment_type_update', {'payment_type_id': self.payA.id}),
                             ('payment_type_delete', {'payment_type_id': self.payA.id})):
            with self.subTest(view=name):
                resp = self.client.post(
                    reverse(name, kwargs={'cafe_code': 'ca', **kwargs}), {'name': 'نقدی'})
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp['Location'],
                                 reverse('payment_type_list', kwargs={'cafe_code': 'ca'}))

    def test_payment_type_create_links_to_current_cafe(self):
        self.login(self.managerA, 'ca')
        resp = self.client.post(reverse('payment_type_create', kwargs={'cafe_code': 'ca'}),
                                {'name': 'کارتی'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PaymentType.objects.get(name='کارتی').cafe, self.cafeA)

    def test_category_post_redirects_with_cafe_code(self):
        self.login(self.managerA, 'ca')
        resp = self.client.post(
            reverse('category_update', kwargs={'cafe_code': 'ca', 'category_id': self.catA.id}),
            {'name': 'دسته ویرایششده', 'description': ''})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('category_list', kwargs={'cafe_code': 'ca'}))

    # ── 3. other bare redirects that crashed the same way ────────────
    def test_notification_redirects_do_not_crash(self):
        self.login(self.managerA, 'ca')
        notif = Notification.objects.create(
            recipient=self.managerA, title='t', message='m')
        for url_name in ('mark_notification_read', 'delete_notification'):
            with self.subTest(view=url_name):
                resp = self.client.get(
                    reverse(url_name, kwargs={'cafe_code': 'ca', 'notification_id': notif.id}))
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp['Location'],
                                 reverse('notifications_list', kwargs={'cafe_code': 'ca'}))

    # ── 4. tenant scoping ───────────────────────────────────────────
    def test_cross_cafe_unit_edit_and_delete_404(self):
        self.login(self.managerA, 'ca')
        resp = self.client.get(
            reverse('unit_update', kwargs={'cafe_code': 'ca', 'unit_id': self.unitB.id}))
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post(
            reverse('unit_delete', kwargs={'cafe_code': 'ca', 'unit_id': self.unitB.id}))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Unit.objects.filter(id=self.unitB.id).exists())

    def test_cross_cafe_category_and_payment_type_404(self):
        self.login(self.managerA, 'ca')
        resp = self.client.post(
            reverse('category_delete', kwargs={'cafe_code': 'ca', 'category_id': self.catB.id}))
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post(
            reverse('payment_type_delete',
                    kwargs={'cafe_code': 'ca', 'payment_type_id': self.payB.id}))
        self.assertEqual(resp.status_code, 404)

    # ── 5. list scoping ──────────────────────────────────────────────
    def test_unit_and_category_lists_scoped_to_current_cafe(self):
        self.login(self.managerA, 'ca')
        resp = self.client.get(reverse('unit_list', kwargs={'cafe_code': 'ca'}))
        self.assertContains(resp, 'کیلوگرم A')
        self.assertNotContains(resp, 'کیلوگرم B')
        resp = self.client.get(reverse('category_list', kwargs={'cafe_code': 'ca'}))
        self.assertContains(resp, 'دسته A')
        self.assertNotContains(resp, 'دسته B')