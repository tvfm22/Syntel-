"""Scratch verification tests for the 5 requested features (2026-08-27).

1. No stock error/message for requesters on the request page (JS gate + no server block).
2. Product.show_sub_products: default shows product by main name to requesters;
   when checked, brand names appear as separate selectable rows.
3. Delivery date: collapsible box; next 6 days excluding Saturdays; default tomorrow.
4. Cafe-change section hidden for requester users in the layout.
5. My Requests: last week's requests grouped in separate per-day lists.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from purchases.models import Cafe, Category, Unit, Product, ProductBrand, PurchaseRequest, PurchaseItem
from purchases import views

User = get_user_model()


class FeatureVerifyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.requester = User.objects.create_user(
            username='req_user', password='testpass123', role='requester', cafe=self.cafe)
        self.purchasing = User.objects.create_user(
            username='buy_user', password='testpass123', role='purchasing', cafe=self.cafe)
        self.category = Category.objects.create(name='Cat', cafe=self.cafe)
        self.unit = Unit.objects.create(name='Kg', symbol='kg', cafe=self.cafe)
        self.product = Product.objects.create(
            name='روغن', category=self.category, unit=self.unit, cafe=self.cafe,
            usage_location='general')
        self.brand = ProductBrand.objects.create(product=self.product, brand_name='دمس')
        # middleware context
        for u in (self.requester, self.purchasing):
            session = self.client.session
            session['selected_cafe_id'] = self.cafe.id
            session['selected_cafe_code'] = self.cafe.code
            session.save()

    def login(self, user):
        self.client.login(username=user.username, password='testpass123')

    # ── Feature 3: delivery days ─────────────────────────────────────
    def test_next_delivery_days_six_days_no_saturday(self):
        days = views._next_delivery_days()
        self.assertEqual(len(days), 6)
        dates = [d for d, _ in days]
        tomorrow = timezone.now().date() + timedelta(days=1)
        # starts from tomorrow (if tomorrow is not Saturday) and is strictly increasing
        self.assertGreaterEqual(dates[0], tomorrow)
        for a, b in zip(dates, dates[1:]):
            self.assertGreater(b, a)
        # no Saturdays (Python weekday 5)
        self.assertTrue(all(d.weekday() != 5 for d in dates))

    def test_request_form_context_delivery_days(self):
        self.login(self.requester)
        resp = self.client.get(reverse('purchase_request', kwargs={'cafe_code': 'main'}))
        self.assertEqual(resp.status_code, 200)
        days = resp.context['delivery_days']
        self.assertEqual(len(days), 6)
        self.assertTrue(all(d.weekday() != 5 for d, _ in days))

    # ── Feature 2: show_sub_products in autocomplete ─────────────────
    def test_autocomplete_requester_flag_off_shows_main_name(self):
        self.login(self.requester)
        resp = self.client.get(reverse('products_autocomplete', kwargs={'cafe_code': 'main'}), {'q': 'روغن'})
        results = resp.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'روغن')
        self.assertEqual(results[0]['label'], 'روغن')
        self.assertIsNone(results[0]['product_brand_id'])

    def test_autocomplete_requester_flag_on_shows_brands(self):
        self.product.show_sub_products = True
        self.product.save()
        self.login(self.requester)
        resp = self.client.get(reverse('products_autocomplete', kwargs={'cafe_code': 'main'}), {'q': 'روغن'})
        results = resp.json()['results']
        self.assertEqual(len(results), 1)  # 1 active brand
        self.assertEqual(results[0]['label'], 'روغن - دمس')
        self.assertEqual(results[0]['product_brand_id'], self.brand.id)

    def test_autocomplete_purchasing_always_sees_brands(self):
        # flag off, but purchasing user still gets brand rows (unchanged behavior)
        self.login(self.purchasing)
        resp = self.client.get(reverse('products_autocomplete', kwargs={'cafe_code': 'main'}), {'q': 'روغن'})
        results = resp.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['product_brand_id'], self.brand.id)

    # ── Feature 2 (form side): product_create saves the flag ─────────
    def test_product_create_saves_show_sub_products(self):
        self.login(self.purchasing)
        resp = self.client.post(reverse('product_create', kwargs={'cafe_code': 'main'}), {
            'name': 'شیر',
            'category': self.category.id,
            'unit': self.unit.id,
            'usage_location': 'general',
            'show_sub_products': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        p = Product.objects.get(name='شیر')
        self.assertTrue(p.show_sub_products)

    def test_product_update_saves_show_sub_products(self):
        self.login(self.purchasing)
        resp = self.client.post(reverse('product_update', kwargs={'cafe_code': 'main', 'product_id': self.product.id}), {
            'name': 'روغن',
            'category': self.category.id,
            'unit': self.unit.id,
            'usage_location': 'general',
            'min_stock': '0',
            'show_sub_products': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.product.refresh_from_db()
        self.assertTrue(self.product.show_sub_products)

    # ── Feature 5: My Requests grouped per day ───────────────────────
    def _make_request(self, user, days_ago, status='pending_manager_approval'):
        req = PurchaseRequest.objects.create(user=user, cafe=self.cafe, status=status,
                                             delivery_date=timezone.now().date() + timedelta(days=1))
        PurchaseItem.objects.create(request=req, product=self.product, name='روغن',
                                    quantity=1, unit='عدد')
        PurchaseRequest.objects.filter(id=req.id).update(
            created_at=timezone.now() - timedelta(days=days_ago))
        return req

    def test_my_requests_day_groups(self):
        self.login(self.requester)
        today_req = self._make_request(self.requester, 0)
        old_req = self._make_request(self.requester, 2)
        very_old = self._make_request(self.requester, 10)

        resp = self.client.get(reverse('purchase_requests_list', kwargs={'cafe_code': 'main'}))
        self.assertEqual(resp.status_code, 200)
        day_groups = resp.context['day_groups']
        older = resp.context['older_requests']
        # two distinct days within the last 7 → 2 groups; 10-day-old request → older
        self.assertEqual(len(day_groups), 2)
        self.assertEqual([r.id for r in older], [very_old.id])
        all_grouped = [r.id for _, _, reqs in day_groups for r in reqs]
        self.assertEqual(sorted(all_grouped), sorted([today_req.id, old_req.id]))
        # template renders per-day sections
        self.assertContains(resp, 'day-section')
        self.assertContains(resp, 'امروز')
        self.assertContains(resp, 'درخواست‌های قبلی')

    # ── Feature 4: cafe-change hidden for requesters ─────────────────
    def test_cafe_switch_hidden_for_requester(self):
        self.login(self.requester)
        resp = self.client.get(reverse('purchase_requests_list', kwargs={'cafe_code': 'main'}))
        self.assertEqual(resp.status_code, 200)
        # the div must not render (CSS class selector still exists in <style>)
        self.assertNotContains(resp, 'class="sidebar-switch-cafe"')

    def test_cafe_switch_shown_for_purchasing(self):
        self.login(self.purchasing)
        resp = self.client.get(reverse('purchase_requests_list', kwargs={'cafe_code': 'main'}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="sidebar-switch-cafe"')

    # ── Feature 1: requester gets no stock messaging on request page ─
    def test_request_form_requester_skips_stock_check(self):
        self.login(self.requester)
        resp = self.client.get(reverse('purchase_request', kwargs={'cafe_code': 'main'}))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        # IS_REQUESTER constant must be true for this user
        self.assertIn('const IS_REQUESTER = true', content)
        # collapsible delivery box is present with a preselected default
        self.assertIn('deliverySection', content)
        self.assertIn('delivery-toggle', content)

    def test_request_form_purchasing_keeps_stock_check(self):
        self.login(self.purchasing)
        resp = self.client.get(reverse('purchase_request', kwargs={'cafe_code': 'main'}))
        content = resp.content.decode('utf-8')
        self.assertIn('const IS_REQUESTER = false', content)
