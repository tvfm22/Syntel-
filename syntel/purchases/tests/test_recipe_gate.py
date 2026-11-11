"""قواعد اتصال رسپی منو ↔ مدیریت محصولات ↔ سفارش درخواست‌کننده.

1. کالای دستهٔ غذایی (requires_recipe روی خود یا والد) تا وقتی در هیچ سطر رسپی
   مصرف نشده، برای درخواست‌کننده در autocomplete و category API دیده نمی‌شود.
2. همان کالا بلافاصله بعد از اضافه‌شدن به رسپی (غذا یا نیمه‌آماده) ظاهر می‌شود.
3. کالای دستهٔ غیرغذایی بدون رسپی هم قابل سفارش است.
4. POST فرم‌های درخواست، کالای غذاییِ بدون رسپی را رد می‌کند (دور زدن UI ممکن نیست).
5. کالای تازهٔ درخواست‌کننده همیشه در دستهٔ «تحقیق و توسعه» همان کافه ثبت می‌شود.
6. لیست محصولات، نشان «تحقیق و توسعه» و وضعیت رسپی/سفارش را نشان می‌دهد.
"""
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from purchases.models import (
    Cafe,
    Category,
    KukerItem,
    KukerRecipeLine,
    Product,
    PurchaseItem,
    PurchaseRequest,
    User,
    WarehouseRequest,
)
from purchases.services.recipe_gate import (
    RD_CATEGORY_NAME,
    category_requires_recipe,
    get_or_create_rd_category,
    is_used_in_cogs_recipe,
    recipe_gate_error,
)


class RecipeGateBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cafe = Cafe.objects.create(code='rg', name='Cafe RG')
        cls.other_cafe = Cafe.objects.create(code='rg2', name='Cafe RG 2')

        # دستهٔ غذایی والد + زیردستهٔ بدون پرچم (وارثِ والد)
        cls.food = Category.objects.create(
            name='مواد غذایی', cafe=cls.cafe, requires_recipe=True)
        cls.dairy = Category.objects.create(
            name='لبنیات', cafe=cls.cafe, parent=cls.food)
        # دستهٔ غیرغذایی
        cls.cleaning = Category.objects.create(
            name='شوینده', cafe=cls.cafe, requires_recipe=False)

        cls.requester = User.objects.create_user(
            username='rg_req', password='x', role='requester', cafe=cls.cafe)
        cls.buyer = User.objects.create_user(
            username='rg_buyer', password='x', role='purchasing', cafe=cls.cafe)

        # کالاهای غذایی و غیرغذایی
        cls.milk = Product.objects.create(
            name='شیر رسپی', category=cls.dairy, cafe=cls.cafe, is_complete=True)
        cls.flour = Product.objects.create(
            name='آرد رسپی', category=cls.food, cafe=cls.cafe, is_complete=True)
        cls.detergent = Product.objects.create(
            name='مایع ظرفشویی', category=cls.cleaning, cafe=cls.cafe, is_complete=True)

        # اقلام منو: یک غذا و دو ماده اولیه
        cls.dish = KukerItem.objects.create(
            name='غذای رسپی', key='غذای رسپی', kind=KukerItem.KIND_DISH, cafe=cls.cafe)
        cls.flour_item = KukerItem.objects.create(
            name='آرد رسپی', key='آرد رسپی', kind=KukerItem.KIND_RAW,
            cafe=cls.cafe, product=cls.flour)
        cls.milk_item = KukerItem.objects.create(
            name='شیر رسپی', key='شیر رسپی', kind=KukerItem.KIND_RAW,
            cafe=cls.cafe, product=cls.milk)

    def setUp(self):
        self.client.force_login(self.requester)

    def url_autocomplete(self):
        return reverse('products_autocomplete', kwargs={'cafe_code': self.cafe.code})

    def url_category_products(self, category_id):
        return reverse('category_products_api', kwargs={
            'cafe_code': self.cafe.code, 'category_id': category_id})

    def url_purchase_request(self):
        return reverse('purchase_request', kwargs={'cafe_code': self.cafe.code})

    def url_warehouse_request(self):
        return reverse('warehouse_request_form', kwargs={'cafe_code': self.cafe.code})

    def add_recipe_line(self, material, item=None):
        return KukerRecipeLine.objects.create(
            item=item or self.dish, material=material, quantity=Decimal('1'), unit='عدد')


class RecipeGateHelperTests(RecipeGateBase):
    def test_food_category_inherits_from_parent(self):
        self.assertTrue(category_requires_recipe(self.dairy))
        self.assertTrue(category_requires_recipe(self.food))
        self.assertFalse(category_requires_recipe(self.cleaning))
        self.assertFalse(category_requires_recipe(None))

    def test_is_used_in_cogs_recipe_false_before_recipe(self):
        self.assertFalse(is_used_in_cogs_recipe(self.milk))

    def test_is_used_in_cogs_recipe_true_after_recipe(self):
        self.add_recipe_line(self.milk_item)
        self.assertTrue(is_used_in_cogs_recipe(self.milk))

    def test_is_used_in_cogs_recipe_via_prep(self):
        prep = KukerItem.objects.create(
            name='سس رسپی', key='سس رسپی', kind=KukerItem.KIND_PREP, cafe=self.cafe)
        self.add_recipe_line(self.milk_item, item=prep)
        self.assertTrue(is_used_in_cogs_recipe(self.milk))

    def test_is_used_in_cogs_recipe_ignores_other_cafe(self):
        other_item = KukerItem.objects.create(
            name='غذای کافه دیگر', key='غذای کافه دیگر',
            kind=KukerItem.KIND_DISH, cafe=self.other_cafe)
        KukerRecipeLine.objects.create(
            item=other_item, material=self.milk_item,
            quantity=Decimal('1'), unit='عدد')
        self.assertFalse(
            is_used_in_cogs_recipe(self.milk),
            'رسپیِ کافهٔ دیگر نباید کالای این کافه را فعال کند')

    def test_recipe_gate_error_only_for_unused_food(self):
        self.assertIsNone(recipe_gate_error(None))
        self.assertIsNone(recipe_gate_error(self.detergent))
        self.assertIn('در هیچ رسپی', recipe_gate_error(self.milk))
        self.add_recipe_line(self.milk_item)
        self.assertIsNone(recipe_gate_error(self.milk))


class RequesterAutocompleteTests(RecipeGateBase):
    def test_unused_food_product_hidden(self):
        data = self.client.get(self.url_autocomplete(), {'q': 'شیر'}).json()
        self.assertEqual([r['name'] for r in data['results']], [])

    def test_food_product_visible_after_recipe_added(self):
        self.add_recipe_line(self.milk_item)
        data = self.client.get(self.url_autocomplete(), {'q': 'شیر'}).json()
        self.assertIn('شیر رسپی', [r['name'] for r in data['results']])

    def test_child_of_food_category_hidden(self):
        """زیردستهٔ بدون پرچم، پرچم را از والد به ارث می‌برد."""
        data = self.client.get(self.url_autocomplete(), {'q': 'شیر'}).json()
        self.assertEqual(data['results'], [])

    def test_non_food_product_visible_without_recipe(self):
        data = self.client.get(self.url_autocomplete(), {'q': 'مایع'}).json()
        self.assertIn('مایع ظرفشویی', [r['name'] for r in data['results']])

    def test_purchasing_user_sees_unused_food_product(self):
        self.client.force_login(self.buyer)
        data = self.client.get(self.url_autocomplete(), {'q': 'شیر'}).json()
        self.assertIn('شیر رسپی', [r['name'] for r in data['results']])


class CategoryProductsApiTests(RecipeGateBase):
    def test_unused_food_product_excluded(self):
        data = self.client.get(self.url_category_products(self.dairy.id)).json()
        self.assertEqual(data['results'], [])

    def test_food_product_included_after_recipe(self):
        self.add_recipe_line(self.milk_item)
        data = self.client.get(self.url_category_products(self.dairy.id)).json()
        self.assertIn('شیر رسپی', [r['name'] for r in data['results']])

    def test_non_food_product_included(self):
        data = self.client.get(self.url_category_products(self.cleaning.id)).json()
        self.assertIn('مایع ظرفشویی', [r['name'] for r in data['results']])


class PurchaseRequestPostTests(RecipeGateBase):
    def post_item(self, product, name=None, **extra):
        payload = {
            'product_name': [name or product.name],
            'item_quantity': ['2'],
            'item_unit': ['عدد'],
            'item_description': [''],
            'item_product_id': [str(product.id)],
            'item_brand_id': [''],
        }
        payload.update(extra)
        return self.client.post(self.url_purchase_request(), payload)

    def test_blocks_unused_food_product(self):
        r = self.post_item(self.milk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PurchaseRequest.objects.count(), 0)
        self.assertContains(r, 'در هیچ رسپی منو مصرف نمی‌شود')

    def test_allows_food_product_in_recipe(self):
        self.add_recipe_line(self.milk_item)
        r = self.post_item(self.milk)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PurchaseRequest.objects.count(), 1)

    def test_allows_non_food_product(self):
        r = self.post_item(self.detergent)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PurchaseRequest.objects.count(), 1)

    def test_cannot_bypass_by_free_text_name(self):
        """تایپ نام کالا به‌جای انتخاب از فهرست هم راه دور زدن نیست."""
        r = self.client.post(self.url_purchase_request(), {
            'product_name': ['شیر رسپی'],
            'item_quantity': ['2'],
            'item_unit': ['عدد'],
            'item_description': [''],
            'item_product_id': [''],
            'item_brand_id': [''],
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PurchaseRequest.objects.count(), 0)

    def test_purchasing_user_not_blocked(self):
        self.client.force_login(self.buyer)
        r = self.post_item(self.milk)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PurchaseRequest.objects.count(), 1)

    def test_blocked_submission_returns_rows_to_form(self):
        """رد شدن نباید کل فرم را خالی کند — ردیف‌ها برمی‌گردند."""
        r = self.client.post(self.url_purchase_request(), {
            'product_name': ['مایع ظرفشویی', 'شیر رسپی'],
            'item_quantity': ['2', '3'],
            'item_unit': ['عدد', 'کیلوگرم'],
            'item_description': ['', 'برای تست'],
            'item_product_id': [str(self.detergent.id), str(self.milk.id)],
            'item_brand_id': ['', ''],
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="submittedItemsData"')
        rows = r.context['submitted_items']
        self.assertEqual([row['name'] for row in rows], ['مایع ظرفشویی', 'شیر رسپی'])
        self.assertEqual(rows[1]['quantity'], '3')
        self.assertEqual(rows[1]['unit'], 'کیلوگرم')
        self.assertEqual(rows[1]['description'], 'برای تست')

    def test_get_form_seed_is_empty(self):
        r = self.client.get(self.url_purchase_request())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['submitted_items'], [])


class WarehouseRequestPostTests(RecipeGateBase):
    def post_item(self, product):
        return self.client.post(self.url_warehouse_request(), {
            'product_name': [product.name],
            'item_quantity': ['2'],
            'item_unit': ['عدد'],
            'item_description': [''],
            'item_product_id': [str(product.id)],
            'item_brand_id': [''],
        })

    def test_blocks_unused_food_product(self):
        r = self.post_item(self.milk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseRequest.objects.count(), 0)
        self.assertContains(r, 'در هیچ رسپی منو مصرف نمی‌شود')

    def test_allows_food_product_in_recipe(self):
        self.add_recipe_line(self.milk_item)
        r = self.post_item(self.milk)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WarehouseRequest.objects.count(), 1)

    def test_allows_non_food_product(self):
        r = self.post_item(self.detergent)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WarehouseRequest.objects.count(), 1)

    def test_blocked_submission_returns_rows_to_form(self):
        r = self.client.post(self.url_warehouse_request(), {
            'product_name': ['مایع ظرفشویی', 'شیر رسپی'],
            'item_quantity': ['2', '3'],
            'item_unit': ['عدد', 'کیلوگرم'],
            'item_description': ['', ''],
            'item_product_id': [str(self.detergent.id), str(self.milk.id)],
            'item_brand_id': ['', ''],
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="submittedItemsData"')
        self.assertEqual([row['name'] for row in r.context['submitted_items']],
                         ['مایع ظرفشویی', 'شیر رسپی'])

    def test_get_form_seed_is_empty(self):
        r = self.client.get(self.url_warehouse_request())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['submitted_items'], [])


class QuickAddProductRdTests(RecipeGateBase):
    def url(self):
        return reverse('quick_add_product', kwargs={'cafe_code': self.cafe.code})

    def post(self, name, **extra):
        payload = {'name': name, 'unit': 'عدد', 'description': 'برای تست درخواست'}
        payload.update(extra)
        return self.client.post(
            self.url(), data=json.dumps(payload), content_type='application/json')

    def test_creates_rd_category_and_assigns(self):
        data = self.post('کالای تازه درخواستی').json()
        self.assertTrue(data['success'])
        product = Product.objects.get(id=data['product']['id'])
        self.assertEqual(product.category.name, RD_CATEGORY_NAME)
        self.assertEqual(product.category.cafe, self.cafe)
        self.assertFalse(product.category.requires_recipe)
        self.assertFalse(product.is_complete)

    def test_overrides_selected_category(self):
        data = self.post('کالای با دسته', category_id=self.cleaning.id).json()
        self.assertTrue(data['success'])
        product = Product.objects.get(id=data['product']['id'])
        self.assertEqual(
            product.category.name, RD_CATEGORY_NAME,
            'دستهٔ انتخابی کاربر باید به نفع «تحقیق و توسعه» نادیده گرفته شود')

    def test_rd_category_is_per_cafe(self):
        rd_a = get_or_create_rd_category(self.cafe)
        rd_b = get_or_create_rd_category(self.other_cafe)
        self.assertNotEqual(rd_a.id, rd_b.id)
        self.assertEqual(rd_a.cafe_id, self.cafe.id)
        self.assertEqual(rd_b.cafe_id, self.other_cafe.id)

    def test_rd_category_reused_not_duplicated(self):
        first = get_or_create_rd_category(self.cafe)
        second = get_or_create_rd_category(self.cafe)
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            Category.objects.filter(name=RD_CATEGORY_NAME, cafe=self.cafe).count(), 1)

    def test_new_product_is_requestable_immediately(self):
        """کالای «تحقیق و توسعه» غیرغذایی است، پس بدون رسپی هم قابل سفارش می‌ماند."""
        data = self.post('کالای قابل سفارش').json()
        product = Product.objects.get(id=data['product']['id'])
        self.assertIsNone(recipe_gate_error(product))


class EditMyRequestTests(RecipeGateBase):
    """ویرایش «درخواست‌های من» هم راه دور زدن نباشد."""

    def setUp(self):
        super().setUp()
        self.pr = PurchaseRequest.objects.create(
            user=self.requester, cafe=self.cafe, status='pending_manager_approval')
        self.line = PurchaseItem.objects.create(
            request=self.pr, name='دستمال کاغذی', quantity=Decimal('2'), unit='عدد')

    def url(self):
        return reverse('edit_my_request', kwargs={
            'cafe_code': self.cafe.code, 'request_id': self.pr.id})

    def test_blocks_new_row_with_unused_food_product(self):
        r = self.client.post(self.url(), {
            'item_id': [str(self.line.id), ''],
            'name': ['دستمال کاغذی', 'شیر رسپی'],
            'quantity': ['2', '3'],
            'unit': ['عدد', 'عدد'],
            'description': ['', ''],
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'در هیچ رسپی منو مصرف نمی‌شود')
        self.assertEqual(self.pr.items.count(), 1)

    def test_allows_new_row_with_food_product_in_recipe(self):
        self.add_recipe_line(self.milk_item)
        r = self.client.post(self.url(), {
            'item_id': [str(self.line.id), ''],
            'name': ['دستمال کاغذی', 'شیر رسپی'],
            'quantity': ['2', '3'],
            'unit': ['عدد', 'عدد'],
            'description': ['', ''],
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.pr.items.count(), 2)

    def test_blocks_renaming_row_to_unused_food_product(self):
        r = self.client.post(self.url(), {
            'item_id': [str(self.line.id)],
            'name': ['شیر رسپی'],
            'quantity': ['2'],
            'unit': ['عدد'],
            'description': [''],
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'در هیچ رسپی منو مصرف نمی‌شود')
        self.line.refresh_from_db()
        self.assertEqual(self.line.name, 'دستمال کاغذی')

    def test_quantity_edit_still_works(self):
        r = self.client.post(self.url(), {
            'item_id': [str(self.line.id)],
            'name': ['دستمال کاغذی'],
            'quantity': ['7'],
            'unit': ['عدد'],
            'description': [''],
        })
        self.assertEqual(r.status_code, 302)
        self.line.refresh_from_db()
        self.assertEqual(self.line.quantity, Decimal('7'))


class ProductListBadgeTests(RecipeGateBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.buyer)

    def url(self):
        return reverse('product_list', kwargs={'cafe_code': self.cafe.code})

    def test_blocked_food_product_badge(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="p-recipe is-blocked"')
        self.assertContains(r, 'بدون رسپی (مخفی در سفارش)')

    def test_active_food_product_badge(self):
        self.add_recipe_line(self.milk_item)
        r = self.client.get(self.url())
        self.assertContains(r, 'class="p-recipe is-used"')
        self.assertContains(r, 'در رسپی (فعال در سفارش)')

    def test_non_food_product_badge(self):
        r = self.client.get(self.url())
        self.assertContains(r, 'class="p-recipe is-na"')
        self.assertContains(r, 'بدون نیاز به رسپی')

    def test_recipe_filter_missing(self):
        names = [p.name for p in self.client.get(self.url(), {'recipe': 'missing'}).context['products']]
        self.assertIn('شیر رسپی', names)
        self.assertIn('آرد رسپی', names)
        self.assertNotIn('مایع ظرفشویی', names)

    def test_recipe_filter_used(self):
        self.add_recipe_line(self.milk_item)
        names = [p.name for p in self.client.get(self.url(), {'recipe': 'used'}).context['products']]
        self.assertEqual(names, ['شیر رسپی'])

    def test_recipe_filter_free(self):
        names = [p.name for p in self.client.get(self.url(), {'recipe': 'free'}).context['products']]
        self.assertEqual(names, ['مایع ظرفشویی'])

    def test_recipe_filter_unknown_value_ignored(self):
        names = [p.name for p in self.client.get(self.url(), {'recipe': 'xyz'}).context['products']]
        self.assertEqual(len(names), 3)

    def test_rd_badge(self):
        rd = get_or_create_rd_category(self.cafe)
        Product.objects.create(
            name='کالای تحقیق', category=rd, cafe=self.cafe, is_complete=False)
        r = self.client.get(self.url())
        # نام کلاس در بلوک <style> هم هست؛ پس خودِ تگ بررسی می‌شود
        self.assertContains(r, '<span class="p-chip-rd"')
        self.assertContains(r, RD_CATEGORY_NAME)
