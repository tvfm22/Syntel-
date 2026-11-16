"""تست‌های انتخاب دسته‌بندی در فرم درخواست + قواعد کالای جدید.

1. category_products_api محصولات همان دسته را برمی‌گرداند (و زیردسته‌ها را).
2. کالای ناقص (is_complete=False) در autocomplete و category_products_api نمی‌آید.
3. quick_add_product بدون توضیحات رد می‌شود (هشدار «این کالای جدید را برای چه می‌خواهی؟»).
4. quick_add_product با توضیحات کالای ناقص می‌سازد و در دستهٔ «تحقیق و توسعه» می‌گذارد.
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

    def test_all_categories_endpoint_returns_all_complete_products_and_prioritizes_prefix(self):
        cat1 = Category.objects.create(name='میوه', cafe=self.cafe)
        cat2 = Category.objects.create(name='نانوایی', cafe=self.cafe)
        Product.objects.create(name='آناناس', category=cat1, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='نان تست', category=cat2, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='نان', category=cat2, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='نان ناقص', category=cat2, cafe=self.cafe, is_complete=False)

        url = reverse('all_category_products_api', kwargs={'cafe_code': 'cb'})
        all_names = [r['name'] for r in self.client.get(url).json()['results']]
        self.assertEqual(set(all_names), {'آناناس', 'نان تست', 'نان'})

        filtered_names = [r['name'] for r in self.client.get(url + '?q=نان').json()['results']]
        self.assertEqual(filtered_names, ['نان', 'نان تست', 'آناناس'])

    def test_category_search_prioritizes_prefix(self):
        cat = Category.objects.create(name='خوراکی', cafe=self.cafe)
        Product.objects.create(name='آناناس', category=cat, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='نان بربری', category=cat, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='نان', category=cat, cafe=self.cafe, is_complete=True)
        url = reverse('category_products_api', kwargs={'cafe_code': 'cb', 'category_id': cat.id})
        names = [r['name'] for r in self.client.get(url + '?q=نان').json()['results']]
        self.assertEqual(names, ['نان', 'نان بربری', 'آناناس'])

    def test_purchase_request_form_has_all_option_and_no_extra_search_box(self):
        resp = self.client.get(reverse('purchase_request', kwargs={'cafe_code': 'cb'}))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('<option value="all">همه کالاها</option>', html)
        self.assertNotIn('id="categoryBrowseSearch"', html)


@override_settings(SESSION_COOKIE_SAMESITE='Lax')
class TestQuickAddProductRules(CategoryBrowseBase):
    def test_description_required(self):
        url = reverse('quick_add_product', kwargs={'cafe_code': 'cb'})
        resp = self.client.post(url, data=json.dumps({'name': 'کالای تست'}), content_type='application/json')
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['error_code'], 'description_required')

    def test_creates_incomplete_in_rd_category(self):
        """دستهٔ انتخابی کاربر نادیده گرفته می‌شود: کالای تازه همیشه در
        «تحقیق و توسعه» ثبت می‌شود تا مدیریت بررسی و جابه‌جا کند."""
        from purchases.services.recipe_gate import RD_CATEGORY_NAME

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
        self.assertEqual(p.category.name, RD_CATEGORY_NAME)
        self.assertEqual(p.category.cafe, self.cafe)
        self.assertTrue(p.description)

    def test_incomplete_hidden_from_autocomplete(self):
        Product.objects.create(name='کالای ناقص فرم', cafe=self.cafe, is_complete=False)
        Product.objects.create(name='کالای کامل فرم', cafe=self.cafe, is_complete=True)
        url = reverse('products_autocomplete', kwargs={'cafe_code': 'cb'})
        names = [r['name'] for r in self.client.get(url + '?q=کالای').json()['results']]
        self.assertNotIn('کالای ناقص فرم', names)
        self.assertIn('کالای کامل فرم', names)

    def test_autocomplete_prioritizes_prefix_and_searches_name_only(self):
        cat_dairy = Category.objects.create(name='لبنیات و تخم مرغ', cafe=self.cafe)
        cat_bread = Category.objects.create(name='نانوایی', cafe=self.cafe)
        Product.objects.create(name='آناناس', category=cat_bread, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='نان باگت', category=cat_bread, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='نان', category=cat_bread, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='شیر پرچرب', category=cat_dairy, cafe=self.cafe, is_complete=True)
        Product.objects.create(name='تخم مرغ محلی', category=cat_dairy, cafe=self.cafe, is_complete=True)

        url = reverse('products_autocomplete', kwargs={'cafe_code': 'cb'})
        bread_names = [r['name'] for r in self.client.get(url, {'q': 'نان'}).json()['results']]
        self.assertEqual(bread_names, ['نان', 'نان باگت', 'آناناس'])

        egg_names = [r['name'] for r in self.client.get(url, {'q': 'تخم مرغ'}).json()['results']]
        self.assertEqual(egg_names, ['تخم مرغ محلی'])
        self.assertNotIn('شیر پرچرب', egg_names)


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
