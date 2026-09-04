"""تست‌های انتخاب دسته‌بندی در فرم درخواست + قواعد کالای جدید.

1. category_products_api محصولات همان دسته را برمی‌گرداند (و زیردسته‌ها را).
2. کالای ناقص (is_complete=False) در autocomplete و category_products_api نمی‌آید.
3. quick_add_product بدون توضیحات رد می‌شود (هشدار «این کالای جدید را برای چه می‌خواهی؟»).
4. quick_add_product با توضیحات کالای ناقص می‌سازد و دسته را وصل می‌کند.
5. product_update کالای ناقص را کامل می‌کند.
"""
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from purchases.models import Cafe, Category, Product, User


class CategoryBrowseBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(code='cb', name='Cafe B')
        cls.user = User.objects.create_user(username='req1', password='x', role='requester', cafe=cls.cafe)

    def setUp(self):
        session = self.client.session
        session['cafe_id'] = self.cafe.id
        session.save()
        self.client.force_login(self.user)


@override_settings(SESSION_COOKIE_SAMESITE='Lax')
class TestCategoryProductsApi(CategoryBrowseBase):
    def test_returns_products_of_selected_category(self):
        cat = Category.objects.create(name='لبنیات', cafe=self.cafe)
        p = Product.objects.create(name='شیر', category=cat, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='مرغ', cafe=self.cafe, is_complete=True)  # دسته دیگر

        url = reverse('category_products_api', kwargs={'cafe_code': 'cb', 'category_id': cat.id})
        resp = self.client.get(url)
        data = resp.json()
        ids = [r['id'] for r in data['results']]
        self.assertIn(p.id, ids)
        self.assertNotIn('مرغ', [r['name'] for r in data['results']])

    def test_includes_subcategory_products(self):
        parent = Category.objects.create(name='مواد اولیه', cafe=self.cafe)
        child = Category.objects.create(name='آرد', parent=parent, cafe=self.cafe)
        p = Product.objects.create(name='نان', category=child, cafe=self.cafe, is_complete=True)
        url = reverse('category_products_api', kwargs={'cafe_code': 'cb', 'category_id': parent.id})
        ids = [r['id'] for r in self.client.get(url).json()['results']]
        self.assertIn(p.id, ids)

    def test_search_query_filters(self):
        cat = Category.objects.create(name='نوشیدنی', cafe=self.cafe)
        Product.objects.create(name='چای', category=cat, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='قهوه', category=cat, cafe=self.cafe, is_complete=True)
        url = reverse('category_products_api', kwargs={'cafe_code': 'cb', 'category_id': cat.id})
        names = [r['name'] for r in self.client.get(url + '?q=چای').json()['results']]
        self.assertIn('چای', names)
        self.assertNotIn('قهوه', names)

    def test_incomplete_product_hidden(self):
        cat = Category.objects.create(name='ادویه', cafe=self.cafe)
        Product.objects.create(name='زردچوبه', category=cat, cafe=self.cafe, is_complete=False)
        url = reverse('category_products_api', kwargs={'cafe_code': 'cb', 'category_id': cat.id})
        self.assertEqual(self.client.get(url).json()['results'], [])


@override_settings(SESSION_COOKIE_SAMESITE='Lax')
class TestQuickAddProductRules(CategoryBrowseBase):
    def test_description_required(self):
        url = reverse('quick_add_product', kwargs={'cafe_code': 'cb'})
        resp = self.client.post(url, data=json.dumps({'name': 'کالای تست'}), content_type='application/json')
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['error_code'], 'description_required')
        self.assertIn('برای چه', data['error'])

    def test_creates_incomplete_with_category(self):
        cat = Category.objects.create(name='سرویس', cafe=self.cafe)
        url = reverse('quick_add_product', kwargs={'cafe_code': 'cb'})
        resp = self.client.post(url, data=json.dumps({
            'name': 'بشقاب جدید', 'unit': 'عدد',
            'description': 'برای سرویس جدید کافه', 'category_id': cat.id,
        }), content_type='application/json')
        data = resp.json()
        self.assertTrue(data['success'])
        p = Product.objects.get(id=data['product']['id'])
        self.assertFalse(p.is_complete)
        self.assertEqual(p.category, cat)
        self.assertTrue(p.description)

    def test_incomplete_hidden_from_autocomplete(self):
        Product.objects.create(name='کالای ناقص فرم', cafe=self.cafe, is_complete=False)
        Product.objects.create(name='کالای کامل فرم', cafe=self.cafe, is_complete=True)
        url = reverse('products_autocomplete', kwargs={'cafe_code': 'cb'})
        names = [r['name'] for r in self.client.get(url + '?q=کالای').json()['results']]
        self.assertNotIn('کالای ناقص فرم', names)
        self.assertIn('کالای کامل فرم', names)


@override_settings(SESSION_COOKIE_SAMESITE='Lax')
class TestProductUpdateCompletes(CategoryBrowseBase):
    def test_update_marks_complete(self):
        # کاربر با نقش purchasing برای دسترسی به product_update
        buyer = User.objects.create_user(username='buyer1', password='x', role='purchasing', cafe=self.cafe)
        self.client.force_login(buyer)
        p = Product.objects.create(name='کالای از فرم', cafe=self.cafe, is_complete=False)
        url = reverse('product_update', kwargs={'cafe_code': 'cb', 'product_id': p.id})
        resp = self.client.post(url, data={
            'name': 'کالای از فرم', 'min_stock': 5, 'usage_location': 'general',
            'description': 'توضیح',
        })
        p.refresh_from_db()
        self.assertTrue(p.is_complete, 'product_update باید کالای ناقص را کامل کند')
