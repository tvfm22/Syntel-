"""Tests for selecting multiple request sections (بخش‌های درخواست) per product."""
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import Cafe, Category, Department, KukerItem, KukerRecipeLine, Product, Unit

User = get_user_model()


class ProductMultiSectionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cafe = Cafe.objects.create(name='کافه تست', code='testcafe', is_active=True)
        self.other_cafe = Cafe.objects.create(name='کافه دیگر', code='othercafe', is_active=True)

        self.dep_kitchen = Department.objects.create(name='آشپزخانه', cafe=self.cafe)
        self.dep_bar = Department.objects.create(name='بار', cafe=self.cafe)
        self.dep_hall = Department.objects.create(name='سالن', cafe=self.cafe)
        self.dep_other_cafe = Department.objects.create(name='بخش کافه دیگر', cafe=self.other_cafe)

        self.cat = Category.objects.create(name='ملزومات', cafe=self.cafe, requires_recipe=False)
        self.unit = Unit.objects.create(name='عدد', symbol='عدد', cafe=self.cafe)

        self.manager = User.objects.create_user(
            username='mgr_multi', password='testpass123', role='manager', cafe=self.cafe,
        )
        self.req_kitchen = User.objects.create_user(
            username='req_kitchen', password='testpass123', role='requester',
            cafe=self.cafe, department=self.dep_kitchen,
        )
        self.req_bar = User.objects.create_user(
            username='req_bar', password='testpass123', role='requester',
            cafe=self.cafe, department=self.dep_bar,
        )
        self.req_hall = User.objects.create_user(
            username='req_hall', password='testpass123', role='requester',
            cafe=self.cafe, department=self.dep_hall,
        )

    def test_product_form_renders_multi_section_checkboxes(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('product_create', kwargs={'cafe_code': self.cafe.code}))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('name="departments"', html)
        self.assertIn('name="usage_general"', html)
        self.assertIn('id="newSectionNameInput"', html)
        self.assertIn('id="btnAddSection"', html)
        self.assertIn('آشپزخانه', html)
        self.assertIn('بار', html)
        self.assertIn('سالن', html)
        self.assertNotIn('بخش کافه دیگر', html)

    def test_purchasing_user_can_quick_add_department_from_product_form(self):
        purchasing = User.objects.create_user(
            username='buyer_sec', password='testpass123', role='purchasing', cafe=self.cafe,
        )
        self.client.force_login(purchasing)
        r = self.client.post(
            reverse('quick_add_department', kwargs={'cafe_code': self.cafe.code}),
            data=json.dumps({'name': 'پشتیبانی'}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['department']['name'], 'پشتیبانی')
        self.assertTrue(Department.objects.filter(cafe=self.cafe, name='پشتیبانی', is_active=True).exists())

    def test_create_product_with_multiple_sections(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            reverse('product_create', kwargs={'cafe_code': self.cafe.code}),
            {
                'name': 'دستمال کاغذی',
                'category': str(self.cat.id),
                'unit': str(self.unit.id),
                'departments_present': '1',
                'departments': [str(self.dep_kitchen.id), str(self.dep_bar.id)],
                'is_active_present': '1',
                'is_active': 'on',
            },
        )
        self.assertEqual(r.status_code, 302)
        p = Product.objects.get(cafe=self.cafe, name='دستمال کاغذی')
        self.assertEqual(
            set(p.departments.values_list('id', flat=True)),
            {self.dep_kitchen.id, self.dep_bar.id},
        )
        self.assertEqual(p.usage_location, 'multi')
        self.assertEqual(p.request_sections_display, 'آشپزخانه، بار')

    def test_create_product_requires_at_least_one_section_when_form_submitted(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            reverse('product_create', kwargs={'cafe_code': self.cafe.code}),
            {
                'name': 'کالای بدون بخش',
                'category': str(self.cat.id),
                'unit': str(self.unit.id),
                'departments_present': '1',
                'is_active_present': '1',
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'حداقل یک بخش درخواست')
        self.assertFalse(Product.objects.filter(cafe=self.cafe, name='کالای بدون بخش').exists())

    def test_cross_cafe_department_rejected(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            reverse('product_create', kwargs={'cafe_code': self.cafe.code}),
            {
                'name': 'کالای کراس کافه',
                'category': str(self.cat.id),
                'unit': str(self.unit.id),
                'departments_present': '1',
                'departments': [str(self.dep_other_cafe.id)],
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Product.objects.filter(cafe=self.cafe, name='کالای کراس کافه').exists())

    def test_update_product_switches_between_multi_sections_and_general(self):
        self.client.force_login(self.manager)
        p = Product.objects.create(
            name='لیوان', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='general', is_active=True, is_complete=True,
        )
        # Switch to two sections
        r = self.client.post(
            reverse('product_update', kwargs={'cafe_code': self.cafe.code, 'product_id': p.id}),
            {
                'name': 'لیوان',
                'category': str(self.cat.id),
                'unit': str(self.unit.id),
                'departments_present': '1',
                'departments': [str(self.dep_bar.id), str(self.dep_hall.id)],
                'is_active_present': '1',
                'is_active': 'on',
            },
        )
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(
            set(p.departments.values_list('id', flat=True)),
            {self.dep_bar.id, self.dep_hall.id},
        )
        self.assertEqual(p.usage_location, 'multi')

        # Edit page should pre-select those two sections and not general
        edit_resp = self.client.get(
            reverse('product_update', kwargs={'cafe_code': self.cafe.code, 'product_id': p.id})
        )
        self.assertEqual(edit_resp.context['selected_department_ids'], {self.dep_bar.id, self.dep_hall.id})
        self.assertFalse(edit_resp.context['is_general_selected'])

        # Switch back to general
        r2 = self.client.post(
            reverse('product_update', kwargs={'cafe_code': self.cafe.code, 'product_id': p.id}),
            {
                'name': 'لیوان',
                'category': str(self.cat.id),
                'unit': str(self.unit.id),
                'departments_present': '1',
                'usage_general': '1',
                'is_active_present': '1',
                'is_active': 'on',
            },
        )
        self.assertEqual(r2.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.departments.count(), 0)
        self.assertEqual(p.usage_location, 'general')

    def test_requester_autocomplete_and_category_browse_respect_multi_sections(self):
        p_shared = Product.objects.create(
            name='اسپری مشترک', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='multi', is_active=True, is_complete=True,
        )
        p_shared.departments.set([self.dep_kitchen, self.dep_bar])

        p_general = Product.objects.create(
            name='اسپری عمومی', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='general', is_active=True, is_complete=True,
        )

        p_hall_only = Product.objects.create(
            name='اسپری سالن', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='سالن', is_active=True, is_complete=True,
        )
        p_hall_only.departments.set([self.dep_hall])

        auto_url = reverse('products_autocomplete', kwargs={'cafe_code': self.cafe.code})
        cat_url = reverse('category_products_api', kwargs={'cafe_code': self.cafe.code, 'category_id': self.cat.id})

        # Kitchen requester sees shared + general, NOT hall_only
        self.client.force_login(self.req_kitchen)
        k_auto = {r['name'] for r in self.client.get(auto_url, {'q': 'اسپری'}).json()['results']}
        k_cat = {r['name'] for r in self.client.get(cat_url).json()['results']}
        self.assertEqual(k_auto, {'اسپری مشترک', 'اسپری عمومی'})
        self.assertEqual(k_cat, {'اسپری مشترک', 'اسپری عمومی'})

        # Bar requester also sees shared + general
        self.client.force_login(self.req_bar)
        b_auto = {r['name'] for r in self.client.get(auto_url, {'q': 'اسپری'}).json()['results']}
        self.assertEqual(b_auto, {'اسپری مشترک', 'اسپری عمومی'})

        # Hall requester sees hall_only + general, NOT shared (kitchen+bar)
        self.client.force_login(self.req_hall)
        h_auto = {r['name'] for r in self.client.get(auto_url, {'q': 'اسپری'}).json()['results']}
        self.assertEqual(h_auto, {'اسپری سالن', 'اسپری عمومی'})

    def test_product_list_displays_and_filters_by_department(self):
        p_multi = Product.objects.create(
            name='فویل آلومینیوم', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='multi', is_active=True, is_complete=True,
        )
        p_multi.departments.set([self.dep_kitchen, self.dep_bar])

        p_gen = Product.objects.create(
            name='مایع ظرفشویی', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='general', is_active=True, is_complete=True,
        )

        self.client.force_login(self.manager)
        list_url = reverse('product_list', kwargs={'cafe_code': self.cafe.code})

        r_all = self.client.get(list_url)
        self.assertContains(r_all, 'بخش‌های درخواست')
        self.assertContains(r_all, 'آشپزخانه')
        self.assertContains(r_all, 'بار')

        # Filter by bar -> includes p_multi, excludes p_gen
        r_bar = self.client.get(list_url, {'department': str(self.dep_bar.id)})
        self.assertContains(r_bar, 'فویل آلومینیوم')
        self.assertNotContains(r_bar, 'مایع ظرفشویی')

        # Filter by hall -> excludes p_multi and p_gen
        r_hall = self.client.get(list_url, {'department': str(self.dep_hall.id)})
        self.assertNotContains(r_hall, 'فویل آلومینیوم')
        self.assertNotContains(r_hall, 'مایع ظرفشویی')

        # Filter by general -> includes p_gen, excludes p_multi
        r_gen = self.client.get(list_url, {'department': 'general'})
        self.assertContains(r_gen, 'مایع ظرفشویی')
        self.assertNotContains(r_gen, 'فویل آلومینیوم')

    def test_cogs_raw_material_create_and_consumption_support_multi_sections(self):
        admin_user = User.objects.create_user(
            username='admin_cogs', password='testpass123', role='admin', cafe=self.cafe,
        )
        self.client.force_login(admin_user)
        r = self.client.post(
            reverse('cogs_raw_material_create', kwargs={'cafe_code': self.cafe.code}),
            {
                'name': 'شکر قهوه‌ای',
                'base_unit': 'گرم',
                'usage_locations': ['آشپزخانه', 'بار'],
            },
        )
        self.assertEqual(r.status_code, 200, r.content)
        p = Product.objects.get(cafe=self.cafe, name='شکر قهوه‌ای')
        self.assertEqual(
            set(p.departments.values_list('name', flat=True)),
            {'آشپزخانه', 'بار'},
        )
        self.assertEqual(p.usage_location, 'multi')

        # COGS manage items renders both sections
        items_html = self.client.get(
            reverse('cogs_manage_items', kwargs={'cafe_code': self.cafe.code})
        ).content.decode('utf-8')
        self.assertIn('آشپزخانه، بار', items_html)

        # Add another item belonging to سالن so سالن is a valid filter option
        self.client.post(
            reverse('cogs_raw_material_create', kwargs={'cafe_code': self.cafe.code}),
            {
                'name': 'لیوان سالن',
                'base_unit': 'عدد',
                'usage_locations': ['سالن'],
            },
        )
        raw_hall = KukerItem.objects.get(cafe=self.cafe, name='لیوان سالن')
        raw_hall.price = 20
        raw_hall.save(update_fields=['price'])

        # COGS consumption calc filters by either section
        dish = KukerItem.objects.create(
            cafe=self.cafe, name='کیک تست', key='کیکتست',
            kind=KukerItem.KIND_DISH, out_qty=1, out_unit='پرس',
        )
        raw = KukerItem.objects.get(cafe=self.cafe, name='شکر قهوه‌ای')
        raw.price = 10
        raw.save(update_fields=['price'])
        KukerRecipeLine.objects.create(item=dish, material=raw, quantity=50, unit='گرم')
        KukerRecipeLine.objects.create(item=dish, material=raw_hall, quantity=2, unit='عدد')

        calc_url = reverse('cogs_consumption_calc', kwargs={'cafe_code': self.cafe.code})
        calc_bar = self.client.get(calc_url, {f'qty__{dish.pk}': '1', 'dept': 'بار'})
        self.assertEqual(calc_bar.status_code, 200)
        self.assertEqual([r['item']['name'] for r in calc_bar.context['raw_rows']], ['شکر قهوه‌ای'])

        calc_hall = self.client.get(calc_url, {f'qty__{dish.pk}': '1', 'dept': 'سالن'})
        self.assertEqual(calc_hall.status_code, 200)
        self.assertEqual([r['item']['name'] for r in calc_hall.context['raw_rows']], ['لیوان سالن'])

    def test_unassigned_section_filter_and_requester_isolation(self):
        p_none = Product.objects.create(
            name='کالای بدون بخش', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='none', is_active=False, is_complete=True,
        )
        p_none_active = Product.objects.create(
            name='کالای بدون بخش فعال', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='none', is_active=True, is_complete=True,
        )
        p_gen = Product.objects.create(
            name='کالای عمومی', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='general', is_active=True, is_complete=True,
        )
        p_bar = Product.objects.create(
            name='کالای بار', category=self.cat, unit=self.unit, cafe=self.cafe,
            usage_location='بار', is_active=True, is_complete=True,
        )
        p_bar.departments.set([self.dep_bar])

        self.assertEqual(p_none.request_section_labels, ['بدون بخش'])

        # Manager product list filter for department=none
        self.client.force_login(self.manager)
        list_url = reverse('product_list', kwargs={'cafe_code': self.cafe.code})
        r_none = self.client.get(list_url, {'department': 'none'})
        self.assertEqual(r_none.status_code, 200)
        self.assertContains(r_none, 'کالای بدون بخش')
        self.assertContains(r_none, 'کالای بدون بخش فعال')
        self.assertNotContains(r_none, 'کالای عمومی')
        self.assertNotContains(r_none, 'کالای بار')

        # Even if active, usage_location='none' is never exposed to requesters
        auto_url = reverse('products_autocomplete', kwargs={'cafe_code': self.cafe.code})
        self.client.force_login(self.req_bar)
        bar_names = {r['name'] for r in self.client.get(auto_url, {'q': 'کالای'}).json()['results']}
        self.assertEqual(bar_names, {'کالای عمومی', 'کالای بار'})

    def test_import_catalog_csv_command(self):
        from django.core.management import call_command

        baba_cafe = Cafe.objects.create(name='کافه رستوران بابا آدم', code='baba_adam', is_active=True)
        call_command('import_catalog_csv', cafe_code='baba_adam')
        # Idempotent second run should not duplicate anything
        call_command('import_catalog_csv', cafe_code='baba_adam')

        self.assertEqual(Product.objects.filter(cafe=baba_cafe).count(), 331)
        self.assertEqual(Product.objects.filter(cafe=baba_cafe, is_active=True).count(), 304)
        self.assertEqual(Product.objects.filter(cafe=baba_cafe, is_active=False).count(), 27)
        self.assertEqual(Category.objects.filter(cafe=baba_cafe).count(), 9)
        self.assertEqual(Category.objects.filter(cafe=baba_cafe, parent__isnull=False).count(), 0)
        self.assertEqual(Category.objects.filter(cafe=baba_cafe, requires_recipe=True).count(), 0)
        self.assertEqual(Department.objects.filter(cafe=baba_cafe).count(), 6)

        # Verify logical units
        unit_expectations = {
            'فیله گوساله': 'کیلوگرم',
            'ردکارنت': 'گرم',
            'قارچ دکمه ای': 'گرم',
            'آووکادو': 'عدد',
            'تخم مرغ': 'عدد',
            'سیروپ کارامل': 'لیتر',
            'تست بریاش (بریوش)': 'عدد',
            'برنج هاشمی': 'کیلوگرم',
            'آرد نول': 'کیلوگرم',
            'عدس': 'کیلوگرم',
            'زعفران': 'گرم',
        }
        for prod_name, expected_unit in unit_expectations.items():
            prod = Product.objects.get(cafe=baba_cafe, name=prod_name)
            self.assertEqual(prod.unit.name, expected_unit, prod_name)

        # Verify section permissions and requester ordering visibility
        dep_bar = Department.objects.get(cafe=baba_cafe, name='بار')
        dep_farangi = Department.objects.get(cafe=baba_cafe, name='فرنگی')
        req_bar = User.objects.create_user(
            username='ba_bar', password='x', role='requester', cafe=baba_cafe, department=dep_bar,
        )
        req_farangi = User.objects.create_user(
            username='ba_farangi', password='x', role='requester', cafe=baba_cafe, department=dep_farangi,
        )

        auto_url = reverse('products_autocomplete', kwargs={'cafe_code': baba_cafe.code})
        self.client.force_login(req_bar)
        syrup_names = {r['name'] for r in self.client.get(auto_url, {'q': 'سیروپ کارامل'}).json()['results']}
        self.assertIn('سیروپ کارامل', syrup_names)

        self.client.force_login(req_farangi)
        syrup_farangi = {r['name'] for r in self.client.get(auto_url, {'q': 'سیروپ کارامل'}).json()['results']}
        self.assertNotIn('سیروپ کارامل', syrup_farangi)

        # Inactive no-section item is hidden from everyone
        self.client.force_login(req_bar)
        no_sec = {r['name'] for r in self.client.get(auto_url, {'q': 'دوغ'}).json()['results']}
        self.assertEqual(no_sec, set())
