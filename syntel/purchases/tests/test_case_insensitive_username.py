"""تست‌های غیرحساس بودن نام کاربری به حروف بزرگ و کوچک هنگام ثبت، ویرایش و ورود."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import Cafe

User = get_user_model()


class CaseInsensitiveUsernameTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafe = Cafe.objects.create(name='کافه تست', code='testcafe', is_active=True)
        self.user = User.objects.create_user(
            username='kitchen_user',
            password='testpass123',
            role='requester',
            cafe=self.cafe,
        )
        self.mixed_user = User.objects.create_user(
            username='AliReza',
            password='testpass123',
            role='warehouse',
            cafe=self.cafe,
        )
        self.manager = User.objects.create_user(
            username='ManagerOne',
            password='testpass123',
            role='manager',
            cafe=self.cafe,
        )

    def test_login_case_insensitive_and_trimmed(self):
        login_url = reverse('login')
        for variant in ('KITCHEN_USER', 'Kitchen_User', '  kItChEn_UsEr  '):
            c = Client()
            resp = c.post(login_url, {'username': variant, 'password': 'testpass123'})
            self.assertEqual(resp.status_code, 302, f'ورود با {variant!r} باید موفق شود')
            self.assertEqual(int(c.session['_auth_user_id']), self.user.id)

        # کاربر ثبت‌شده با حروف بزرگ/کوچک مخلوط هم با حروف کوچک وارد می‌شود
        c2 = Client()
        resp2 = c2.post(login_url, {'username': 'alireza', 'password': 'testpass123'})
        self.assertEqual(resp2.status_code, 302)
        self.assertEqual(int(c2.session['_auth_user_id']), self.mixed_user.id)

    def test_login_wrong_password_and_inactive_fail(self):
        login_url = reverse('login')
        resp_bad = self.client.post(login_url, {'username': 'KITCHEN_USER', 'password': 'wrong'})
        self.assertEqual(resp_bad.status_code, 200)
        self.assertContains(resp_bad, 'نام کاربری یا رمز عبور اشتباه است')

        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        resp_inactive = self.client.post(login_url, {'username': 'KITCHEN_USER', 'password': 'testpass123'})
        self.assertEqual(resp_inactive.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_user_create_blocks_case_only_duplicate(self):
        self.client.login(username='managerone', password='testpass123')
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        resp = self.client.post(
            reverse('user_create', args=['testcafe']),
            {
                'username': 'Kitchen_User',
                'password': 'newpass123',
                'confirm_password': 'newpass123',
                'role': 'requester',
                'cafe_id': str(self.cafe.id),
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'قبلاً استفاده شده است')
        self.assertEqual(User.objects.filter(username__iexact='kitchen_user').count(), 1)

    def test_user_update_blocks_other_user_case_duplicate_but_allows_own_recase(self):
        self.client.login(username='MANAGERONE', password='testpass123')
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        # تغییر نام کاربری خود کاربر به حروف دیگر مجاز است
        resp_own = self.client.post(
            reverse('user_update', args=['testcafe', self.user.id]),
            {
                'username': 'Kitchen_User',
                'role': 'requester',
                'cafe_id': str(self.cafe.id),
                'is_active': 'on',
            },
        )
        self.assertEqual(resp_own.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'Kitchen_User')

        # تغییر نام کاربری یک کاربر به نام کاربری کاربر دیگر با تفاوت حروف بزرگ/کوچک مسدود است
        resp_dup = self.client.post(
            reverse('user_update', args=['testcafe', self.mixed_user.id]),
            {
                'username': 'KITCHEN_USER',
                'role': 'warehouse',
                'cafe_id': str(self.cafe.id),
                'is_active': 'on',
            },
        )
        self.assertEqual(resp_dup.status_code, 200)
        self.assertContains(resp_dup, 'قبلاً استفاده شده است')
        self.mixed_user.refresh_from_db()
        self.assertEqual(self.mixed_user.username, 'AliReza')

    def test_login_form_disables_autocapitalize(self):
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'autocapitalize="none"')
