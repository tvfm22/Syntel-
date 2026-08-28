"""Scratch verification tests: complete removal of global (سراسری) departments (2026-08-27).

1. Every department belongs to exactly one cafe; cafe=None impossible (model + migration).
2. department_list shows only the current cafe's departments.
3. Cross-cafe edit/delete blocked (404) — even for cafe-less admin roles (CEO).
4. user_create/user_update: department must belong to the user's cafe.
5. quick_add_department creates under the current cafe.
6. product form department dropdown scoped to current cafe.
"""
import json

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, Client
from django.urls import reverse

from purchases.models import Cafe, Department

User = get_user_model()


class DepartmentSeparationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafeA = Cafe.objects.create(name='Cafe A', code='ca', is_active=True)
        self.cafeB = Cafe.objects.create(name='Cafe B', code='cb', is_active=True)
        self.managerA = User.objects.create_user(
            username='mgr_a', password='testpass123', role='manager', cafe=self.cafeA)
        self.managerB = User.objects.create_user(
            username='mgr_b', password='testpass123', role='manager', cafe=self.cafeB)
        # ادمین بدون کافه: به همه کافه‌ها دسترسی دارد ولی نباید بخش کافه دیگر را ویرایش کند
        self.super_admin = User.objects.create_user(
            username='boss', password='testpass123', role='admin', cafe=None)
        self.deptA = Department.objects.create(name='آشپزخانه A', cafe=self.cafeA)
        self.deptB = Department.objects.create(name='آشپزخانه B', cafe=self.cafeB)

    def login(self, user):
        self.client.login(username=user.username, password='testpass123')

    # ── 1. model-level: cafe is required ─────────────────────────────
    def test_department_without_cafe_rejected(self):
        with self.assertRaises(IntegrityError):
            Department.objects.create(name='بدون کافه')

    # ── 2. department_list scoped to current cafe ────────────────────
    def test_department_list_only_current_cafe(self):
        self.login(self.managerA)
        resp = self.client.get(reverse('department_list', kwargs={'cafe_code': 'ca'}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'آشپزخانه A')
        self.assertNotContains(resp, 'آشپزخانه B')

    def test_department_list_other_cafe(self):
        self.login(self.managerB)
        resp = self.client.get(reverse('department_list', kwargs={'cafe_code': 'cb'}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'آشپزخانه B')
        self.assertNotContains(resp, 'آشپزخانه A')

    def test_department_list_no_global_badge(self):
        self.login(self.managerA)
        resp = self.client.get(reverse('department_list', kwargs={'cafe_code': 'ca'}))
        self.assertNotContains(resp, 'سراسری')

    # ── 3. cross-cafe edit/delete blocked ────────────────────────────
    def test_cross_cafe_edit_blocked_even_for_super_admin(self):
        # ادمین (cafe=None) به همه کافه‌ها دسترسی دارد؛ ولی نباید بخش کافه دیگر را ویرایش کند
        self.login(self.super_admin)
        resp = self.client.get(reverse('department_update', kwargs={'cafe_code': 'cb', 'department_id': self.deptA.id}))
        self.assertEqual(resp.status_code, 404)

    def test_cross_cafe_delete_blocked(self):
        self.login(self.super_admin)
        resp = self.client.post(reverse('department_delete', kwargs={'cafe_code': 'cb', 'department_id': self.deptA.id}))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Department.objects.filter(id=self.deptA.id).exists())

    # ── 4. user_create / user_update department ownership ────────────
    def test_user_create_department_of_other_cafe_rejected(self):
        self.login(self.managerA)
        resp = self.client.post(reverse('user_create', kwargs={'cafe_code': 'ca'}), {
            'username': 'req_b',
            'password1': 'testpass123',
            'password2': 'testpass123',
            'role': 'requester',
            'cafe_id': self.cafeB.id,       # کاربر در کافه B
            'department_id': self.deptA.id,  # ولی بخش کافه A
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'بخش انتخاب‌شده متعلق به کافه این کاربر نیست')
        self.assertFalse(User.objects.filter(username='req_b').exists())

    def test_user_create_department_of_same_cafe_ok(self):
        self.login(self.managerA)
        resp = self.client.post(reverse('user_create', kwargs={'cafe_code': 'ca'}), {
            'username': 'req_a',
            'password1': 'testpass123',
            'password2': 'testpass123',
            'role': 'requester',
            'cafe_id': self.cafeA.id,
            'department_id': self.deptA.id,
        })
        self.assertEqual(resp.status_code, 302)
        u = User.objects.get(username='req_a')
        self.assertEqual(u.department_id, self.deptA.id)

    def test_user_update_cannot_assign_other_cafe_department(self):
        self.login(self.managerA)
        user = User.objects.create_user(
            username='req_a', password='testpass123', role='requester',
            cafe=self.cafeA, department=self.deptA)
        resp = self.client.post(reverse('user_update', kwargs={'cafe_code': 'ca', 'user_id': user.id}), {
            'username': 'req_a',
            'role': 'requester',
            'cafe_id': 'keep',
            'department_id': self.deptB.id,  # بخش کافه B
            'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'بخش انتخاب‌شده متعلق به کافه این کاربر نیست')
        user.refresh_from_db()
        self.assertEqual(user.department_id, self.deptA.id)

    # ── 5. quick_add_department creates under current cafe ───────────
    def test_quick_add_department_uses_current_cafe(self):
        self.login(self.managerB)
        resp = self.client.post(
            reverse('quick_add_department', kwargs={'cafe_code': 'cb'}),
            data=json.dumps({'name': 'قلیون بار'}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['success'])
        d = Department.objects.get(id=data['department']['id'])
        self.assertEqual(d.cafe_id, self.cafeB.id)
        self.assertIsNone(Department.objects.filter(name='قلیون بار', cafe__isnull=True).first())

    def test_quick_add_department_name_unique_per_cafe(self):
        self.login(self.managerA)
        # نام یکسان در کافه B وجود دارد؛ در کافه A باید جدید ساخته شود
        resp = self.client.post(
            reverse('quick_add_department', kwargs={'cafe_code': 'ca'}),
            data=json.dumps({'name': 'آشپزخانه B'}),
            content_type='application/json')
        data = json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['department']['name'], 'آشپزخانه B')
        self.assertEqual(Department.objects.filter(name='آشپزخانه B', cafe=self.cafeA).count(), 1)

    # ── 6. product form dropdown scoped to current cafe ──────────────
    def test_product_form_departments_scoped(self):
        self.login(self.managerA)
        resp = self.client.get(reverse('product_create', kwargs={'cafe_code': 'ca'}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'آشپزخانه A')
        self.assertNotContains(resp, 'آشپزخانه B')
