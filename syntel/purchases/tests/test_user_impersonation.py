"""تست قابلیت «ورود با این کاربر» (جانشینی) از صفحه مدیریت کاربران.

جریان: مدیر/ادمین از لیست کاربران روی «ورود» می‌زند → سشن به کاربر هدف
تغییر می‌کند → بنر بازگشت در همه صفحات نمایش داده می‌شود → با دکمه
«بازگشت به حساب اصلی» به حساب مدیر برمی‌گردد.
"""
from django.test import TestCase, Client
from django.urls import reverse

from purchases.models import User, Cafe


class UserImpersonationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(name='کافه تست', code='imptest')
        cls.other_cafe = Cafe.objects.create(name='کافه دیگر', code='impother')

        cls.manager = User.objects.create_user(
            username='imp_manager', password='testpass123',
            role='manager', cafe=cls.cafe, is_active=True,
        )
        cls.admin = User.objects.create_user(
            username='imp_admin', password='testpass123',
            role='admin', cafe=cls.cafe, is_active=True,
        )
        cls.kitchen = User.objects.create_user(
            username='imp_kitchen', password='testpass123',
            role='kitchen', cafe=cls.cafe, is_active=True,
        )
        cls.requester = User.objects.create_user(
            username='imp_requester', password='testpass123',
            role='requester', cafe=cls.cafe, is_active=True,
        )
        cls.inactive = User.objects.create_user(
            username='imp_inactive', password='testpass123',
            role='kitchen', cafe=cls.cafe, is_active=False,
        )
        # کاربر بدون کافه (غیر ادمینی) — نباید قابل ورود باشد
        cls.cafeless = User.objects.create_user(
            username='imp_cafeless', password='testpass123',
            role='kitchen', cafe=None, is_active=True,
        )

    def login_as(self, user):
        c = Client()
        c.login(username=user.username, password='testpass123')
        return c

    # ------------------------------------------------------------------
    # دکمه «ورود» در لیست کاربران
    # ------------------------------------------------------------------

    def test_login_as_button_shown_for_other_users(self):
        c = self.login_as(self.manager)
        r = c.get(reverse('user_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'login-as')
        # برای خودِ کاربر نمایش داده نمی‌شود
        self.assertNotContains(
            r,
            reverse('user_impersonate', args=[self.cafe.code, self.manager.id]),
        )

    def test_impersonate_requires_post(self):
        c = self.login_as(self.manager)
        url = reverse('user_impersonate', args=[self.cafe.code, self.kitchen.id])
        r = c.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.session['_auth_user_id'], str(self.manager.id))

    # ------------------------------------------------------------------
    # ورود با حساب کاربر
    # ------------------------------------------------------------------

    def test_manager_can_login_as_user(self):
        c = self.login_as(self.manager)
        url = reverse('user_impersonate', args=[self.cafe.code, self.kitchen.id])
        r = c.post(url)
        self.assertEqual(r.status_code, 302)
        # سشن به کاربر هدف تغییر کرده
        self.assertEqual(c.session['_auth_user_id'], str(self.kitchen.id))
        # حساب اصلی برای بازگشت ذخیره شده
        self.assertEqual(c.session['impersonator_id'], self.manager.id)
        # هدف: داشبورد پیش‌فرض نقش آشپزخانه
        self.assertEqual(r.url, reverse('mobile_dashboard', args=[self.cafe.code]))

    def test_admin_can_login_as_user(self):
        c = self.login_as(self.admin)
        url = reverse('user_impersonate', args=[self.cafe.code, self.kitchen.id])
        r = c.post(url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.session['_auth_user_id'], str(self.kitchen.id))
        self.assertEqual(c.session['impersonator_id'], self.admin.id)

    def test_requester_cannot_login_as_user(self):
        c = self.login_as(self.requester)
        url = reverse('user_impersonate', args=[self.cafe.code, self.kitchen.id])
        r = c.post(url)
        # به صفحه لاگین هدایت می‌شود و سشن تغییری نمی‌کند
        self.assertEqual(c.session['_auth_user_id'], str(self.requester.id))

    def test_inactive_user_cannot_be_impersonated(self):
        c = self.login_as(self.manager)
        url = reverse('user_impersonate', args=[self.cafe.code, self.inactive.id])
        r = c.post(url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.session['_auth_user_id'], str(self.manager.id))
        self.assertNotIn('impersonator_id', c.session)

    def test_cannot_login_as_self(self):
        c = self.login_as(self.manager)
        url = reverse('user_impersonate', args=[self.cafe.code, self.manager.id])
        r = c.post(url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.session['_auth_user_id'], str(self.manager.id))
        self.assertNotIn('impersonator_id', c.session)

    def test_cafeless_non_admin_user_cannot_be_impersonated(self):
        c = self.login_as(self.manager)
        url = reverse('user_impersonate', args=[self.cafe.code, self.cafeless.id])
        r = c.post(url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.session['_auth_user_id'], str(self.manager.id))
        self.assertNotIn('impersonator_id', c.session)

    # ------------------------------------------------------------------
    # بنر بازگشت + خروج از جانشینی
    # ------------------------------------------------------------------

    def test_impersonation_banner_on_pages(self):
        c = self.login_as(self.manager)
        c.post(reverse('user_impersonate', args=[self.cafe.code, self.kitchen.id]))
        r = c.get(reverse('mobile_dashboard', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'بازگشت به حساب اصلی')

    def test_no_banner_for_normal_session(self):
        c = self.login_as(self.manager)
        r = c.get(reverse('user_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'بازگشت به حساب اصلی')

    def test_stop_impersonation_returns_to_manager(self):
        c = self.login_as(self.manager)
        c.post(reverse('user_impersonate', args=[self.cafe.code, self.kitchen.id]))
        r = c.post(reverse('user_impersonate_stop', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 302)
        # به حساب مدیر برگشته
        self.assertEqual(c.session['_auth_user_id'], str(self.manager.id))
        # اطلاعات جانشینی پاک شده
        self.assertNotIn('impersonator_id', c.session)
        self.assertNotIn('impersonator_cafe_code', c.session)
        # به لیست کاربران هدایت شده
        self.assertEqual(r.url, reverse('user_list', args=[self.cafe.code]))

    def test_stop_impersonation_cleans_banner(self):
        c = self.login_as(self.manager)
        c.post(reverse('user_impersonate', args=[self.cafe.code, self.kitchen.id]))
        c.post(reverse('user_impersonate_stop', args=[self.cafe.code]))
        r = c.get(reverse('user_list', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'بازگشت به حساب اصلی')

    def test_stop_without_impersonation_goes_to_login(self):
        c = self.login_as(self.manager)
        r = c.post(reverse('user_impersonate_stop', args=[self.cafe.code]))
        self.assertEqual(r.status_code, 302)
        # بدون اطلاعات جانشینی، به صفحه لاگین هدایت می‌شود
        self.assertEqual(c.session['_auth_user_id'], str(self.manager.id))
