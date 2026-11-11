# -*- coding: utf-8 -*-
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe,
    Category,
    Department,
    OfficerAssignment,
    Product,
    ProductPriceEntry,
    PurchaseItem,
    PurchaseRequest,
    StockItem,
    StockTransaction,
    Unit,
    User,
    Warehouse,
)


class ConsumptionReportTests(TestCase):
    def setUp(self):
        self.cafe = Cafe.objects.create(name='بابا آدم', code='baba_adam', is_active=True)
        self.other_cafe = Cafe.objects.create(name='کافه دیگر', code='other_cafe', is_active=True)

        self.admin_user = User.objects.create_user(
            username='admin_baba',
            password='pw',
            role='admin',
            cafe=self.cafe,
        )

        self.dept_kitchen = Department.objects.create(name='آشپزخانه', cafe=self.cafe, is_active=True)
        self.dept_bar = Department.objects.create(name='بار', cafe=self.cafe, is_active=True)

        self.user_kitchen = User.objects.create_user(
            username='kitchen_req',
            password='pw',
            role='requester',
            department=self.dept_kitchen,
            cafe=self.cafe,
        )
        self.user_bar = User.objects.create_user(
            username='bar_req',
            password='pw',
            role='requester',
            department=self.dept_bar,
            cafe=self.cafe,
        )

        self.cat_dairy = Category.objects.create(name='لبنیات', cafe=self.cafe, is_active=True)
        self.cat_fruits = Category.objects.create(name='میوه و تره‌بار', cafe=self.cafe, is_active=True)

        self.unit_kg = Unit.objects.create(name='کیلوگرم', symbol='kg', cafe=self.cafe)
        self.unit_gram = Unit.objects.create(name='گرم', symbol='g', cafe=self.cafe)

        # محصول ۱: پنیر پیتزا (دارای آخرین قیمت واردشده در ProductPriceEntry: 400 تومان به ازای هر گرم = 400,000 هر کیلو)
        self.prod_cheese = Product.objects.create(
            name='پنیر پیتزا',
            category=self.cat_dairy,
            unit=self.unit_kg,
            smallest_unit='گرم',
            base_unit_price=Decimal('400'),
            cafe=self.cafe,
        )
        ProductPriceEntry.objects.create(
            product=self.prod_cheese,
            cafe=self.cafe,
            entered_amount=Decimal('400000'),
            entered_unit='کیلوگرم',
            price_per_smallest_unit=Decimal('400'),
            smallest_unit='گرم',
            recorded_by=self.admin_user,
        )

        # محصول ۲: انبه (دارای base_unit_price)
        self.prod_mango = Product.objects.create(
            name='انبه',
            category=self.cat_fruits,
            unit=self.unit_kg,
            smallest_unit='گرم',
            base_unit_price=Decimal('500'),
            cafe=self.cafe,
        )

        self.warehouse = Warehouse.objects.create(
            name='انبار مرکزی',
            code='WH-MAIN',
            cafe=self.cafe,
            is_active=True,
        )

        self.si_cheese = StockItem.objects.create(
            warehouse=self.warehouse,
            product=self.prod_cheese,
            unit=self.unit_kg,
            quantity=Decimal('50'),
        )
        self.si_mango = StockItem.objects.create(
            warehouse=self.warehouse,
            product=self.prod_mango,
            unit=self.unit_kg,
            quantity=Decimal('50'),
        )

        # ثبت تحویل ۵ کیلو پنیر به آشپزخانه و ۲ کیلو پنیر به بار
        pr_k = PurchaseRequest.objects.create(user=self.user_kitchen, cafe=self.cafe, status='purchased')
        pi_k = PurchaseItem.objects.create(
            request=pr_k, product=self.prod_cheese, name='پنیر پیتزا', quantity=Decimal('5'), unit='کیلوگرم'
        )
        OfficerAssignment.objects.create(
            item=pi_k, target_role='warehouse', status='fulfilled',
            stock_item=self.si_cheese, supplied_quantity=Decimal('5'),
        )
        self.tx_kitchen = StockTransaction.objects.create(
            stock_item=self.si_cheese,
            transaction_type='out',
            quantity=Decimal('5'),
            previous_quantity=Decimal('50'),
            new_quantity=Decimal('45'),
            unit=self.unit_kg,
            reference_type='officer_assignment',
            reference_id=pi_k.id,
            created_by=self.admin_user,
        )

        pr_b = PurchaseRequest.objects.create(user=self.user_bar, cafe=self.cafe, status='purchased')
        pi_b1 = PurchaseItem.objects.create(
            request=pr_b, product=self.prod_cheese, name='پنیر پیتزا', quantity=Decimal('2'), unit='کیلوگرم'
        )
        OfficerAssignment.objects.create(
            item=pi_b1, target_role='warehouse', status='fulfilled',
            stock_item=self.si_cheese, supplied_quantity=Decimal('2'),
        )
        self.tx_bar_cheese = StockTransaction.objects.create(
            stock_item=self.si_cheese,
            transaction_type='out',
            quantity=Decimal('2'),
            previous_quantity=Decimal('45'),
            new_quantity=Decimal('43'),
            unit=self.unit_kg,
            reference_type='officer_assignment',
            reference_id=pi_b1.id,
            created_by=self.admin_user,
        )

        pi_b2 = PurchaseItem.objects.create(
            request=pr_b, product=self.prod_mango, name='انبه', quantity=Decimal('3'), unit='کیلوگرم'
        )
        OfficerAssignment.objects.create(
            item=pi_b2, target_role='warehouse', status='fulfilled',
            stock_item=self.si_mango, supplied_quantity=Decimal('3'),
        )
        self.tx_bar_mango = StockTransaction.objects.create(
            stock_item=self.si_mango,
            transaction_type='out',
            quantity=Decimal('3'),
            previous_quantity=Decimal('50'),
            new_quantity=Decimal('47'),
            unit=self.unit_kg,
            reference_type='officer_assignment',
            reference_id=pi_b2.id,
            created_by=self.admin_user,
        )

        self.client.force_login(self.admin_user)

    def test_consumption_report_renders_sections_products_and_estimated_cost(self):
        url = reverse('consumption_report', kwargs={'cafe_code': self.cafe.code})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # بررسی تفکیک سکشن‌ها
        section_rows = {r['name']: r for r in res.context['section_rows']}
        self.assertIn('آشپزخانه', section_rows)
        self.assertIn('بار', section_rows)
        self.assertEqual(section_rows['آشپزخانه']['cnt'], 1)
        self.assertEqual(section_rows['بار']['cnt'], 2)

        # بررسی تخمین هزینه:
        # پنیر پیتزا: (5 + 2 = 7 کیلوگرم) * 400,000 = 2,800,000 تومان
        # انبه: 3 کیلوگرم * 500,000 = 1,500,000 تومان
        # کل = 4,300,000 تومان
        self.assertEqual(res.context['total_estimated_cost'], Decimal('4300000'))

        product_rows = {r['product_id']: r for r in res.context['product_rows']}
        cheese_row = product_rows[self.prod_cheese.id]
        self.assertEqual(cheese_row['qty'], Decimal('7'))
        self.assertEqual(cheese_row['estimated_cost'], Decimal('2800000'))
        sec_breakdown = {s['section']: s['qty'] for s in cheese_row['section_breakdown']}
        self.assertEqual(sec_breakdown['آشپزخانه'], Decimal('5'))
        self.assertEqual(sec_breakdown['بار'], Decimal('2'))

    def test_category_and_section_filters(self):
        url = reverse('consumption_report', kwargs={'cafe_code': self.cafe.code})

        # فیلتر دسته‌بندی میوه و تره‌بار -> فقط انبه
        res_cat = self.client.get(url, {'category': str(self.cat_fruits.id)})
        self.assertEqual(res_cat.status_code, 200)
        self.assertEqual(len(res_cat.context['product_rows']), 1)
        self.assertEqual(res_cat.context['product_rows'][0]['product_id'], self.prod_mango.id)
        self.assertEqual(res_cat.context['total_estimated_cost'], Decimal('1500000'))

        # فیلتر سکشن آشپزخانه -> فقط پنیر پیتزا (5 کیلوگرم = 2,000,000 تومان)
        res_sec = self.client.get(url, {'section': 'آشپزخانه'})
        self.assertEqual(res_sec.status_code, 200)
        self.assertEqual(len(res_sec.context['product_rows']), 1)
        self.assertEqual(res_sec.context['product_rows'][0]['qty'], Decimal('5'))
        self.assertEqual(res_sec.context['total_estimated_cost'], Decimal('2000000'))

    def test_reopened_assignments_are_excluded(self):
        # بازگشایی تحویل آشپزخانه
        StockTransaction.objects.create(
            stock_item=self.si_cheese,
            transaction_type='in',
            quantity=Decimal('5'),
            previous_quantity=Decimal('43'),
            new_quantity=Decimal('48'),
            unit=self.unit_kg,
            reference_type='officer_assignment_reopen',
            reference_id=self.tx_kitchen.reference_id,
            created_by=self.admin_user,
        )
        url = reverse('consumption_report', kwargs={'cafe_code': self.cafe.code})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        product_rows = {r['product_id']: r for r in res.context['product_rows']}
        # فقط ۲ کیلوگرم بار باقی می‌ماند
        self.assertEqual(product_rows[self.prod_cheese.id]['qty'], Decimal('2'))

    def test_product_delivery_history_api(self):
        # ثبت یک تحویل قدیمی‌تر در ۶۰ روز قبل برای بررسی scope=all در برابر scope=range
        old_tx = StockTransaction.objects.create(
            stock_item=self.si_cheese,
            transaction_type='out',
            quantity=Decimal('4'),
            previous_quantity=Decimal('54'),
            new_quantity=Decimal('50'),
            unit=self.unit_kg,
            reference_type='officer_assignment',
            reference_id=self.tx_kitchen.reference_id,
            created_by=self.admin_user,
        )
        StockTransaction.objects.filter(pk=old_tx.pk).update(
            created_at=timezone.now() - timedelta(days=60)
        )

        api_url = reverse('consumption_product_history_api', kwargs={
            'cafe_code': self.cafe.code,
            'product_id': self.prod_cheese.id,
        })
        res_range = self.client.get(api_url, {'scope': 'range'})
        self.assertEqual(res_range.status_code, 200)
        data_range = res_range.json()
        self.assertTrue(data_range['success'])
        self.assertEqual(data_range['delivery_count'], 2)
        self.assertEqual(data_range['total_qty'], '7')

        res_all = self.client.get(api_url, {'scope': 'all'})
        self.assertEqual(res_all.status_code, 200)
        data_all = res_all.json()
        self.assertEqual(data_all['delivery_count'], 3)
        self.assertEqual(data_all['total_qty'], '11')
        self.assertEqual(data_all['estimated_cost'], 4400000)

        # کالا از کافه دیگر -> 404
        other_prod = Product.objects.create(name='کالای غریبه', cafe=self.other_cafe)
        bad_url = reverse('consumption_product_history_api', kwargs={
            'cafe_code': self.cafe.code,
            'product_id': other_prod.id,
        })
        self.assertEqual(self.client.get(bad_url).status_code, 404)
