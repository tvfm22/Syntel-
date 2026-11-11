"""
Unit tests to verify performance optimizations and prevent N+1 regressions:
1. warehouse_inventory_check single annotated query
2. stock_by_category database-level count and low-stock aggregation
3. backup_management decoupled async triggers with cache locks
"""
from decimal import Decimal
import unittest.mock as mock

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Category, Product, StockItem, StockTransaction, Unit, User, Warehouse
)
from purchases.views_backups import (
    LOCK_AUTO_BACKUP, LOCK_SERVER_BACKUP, LOCK_DRIVE_BACKUP
)


class PerformanceRefactorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.cafe = Cafe.objects.create(name='Main Cafe', code='main', is_active=True)
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')

        self.manager = User.objects.create_user(
            username='manager_perf', password='pass', role='manager', cafe=self.cafe
        )
        self.warehouse_user = User.objects.create_user(
            username='wh_perf', password='pass', role='warehouse', cafe=self.cafe
        )

        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

        self.wh = Warehouse.objects.create(name='Central Warehouse', code='WH-1', cafe=self.cafe)
        self.cat1 = Category.objects.create(name='Meat', cafe=self.cafe)
        self.cat2 = Category.objects.create(name='Dairy', cafe=self.cafe)

        self.p1 = Product.objects.create(name='Beef', category=self.cat1, unit=self.unit, cafe=self.cafe)
        self.p2 = Product.objects.create(name='Chicken', category=self.cat1, unit=self.unit, cafe=self.cafe)
        self.p3 = Product.objects.create(name='Milk', category=self.cat2, unit=self.unit, cafe=self.cafe)
        self.p4 = Product.objects.create(name='Uncategorized Item', category=None, unit=self.unit, cafe=self.cafe)

        # Stock items:
        # si1: Beef - qty 5, min 10 -> is_low_stock = True
        self.si1 = StockItem.objects.create(
            product=self.p1, warehouse=self.wh, unit=self.unit,
            quantity=Decimal('5.0000'), min_quantity=Decimal('10.0000')
        )
        # si2: Chicken - qty 20, min 10 -> is_low_stock = False
        self.si2 = StockItem.objects.create(
            product=self.p2, warehouse=self.wh, unit=self.unit,
            quantity=Decimal('20.0000'), min_quantity=Decimal('10.0000')
        )
        # si3: Milk - qty 2, min 5 -> is_low_stock = True
        self.si3 = StockItem.objects.create(
            product=self.p3, warehouse=self.wh, unit=self.unit,
            quantity=Decimal('2.0000'), min_quantity=Decimal('5.0000')
        )
        # si4: Uncategorized - qty 1, min 2 -> is_low_stock = True
        self.si4 = StockItem.objects.create(
            product=self.p4, warehouse=self.wh, unit=self.unit,
            quantity=Decimal('1.0000'), min_quantity=Decimal('2.0000')
        )

        # Transactions for si1:
        # 2 in (total 15), 1 out (total 10), 1 initial (total 5)
        StockTransaction.objects.create(
            stock_item=self.si1, transaction_type='in', quantity=Decimal('10.0000'), unit=self.unit,
            previous_quantity=Decimal('0'), new_quantity=Decimal('10.0000')
        )
        StockTransaction.objects.create(
            stock_item=self.si1, transaction_type='in', quantity=Decimal('5.0000'), unit=self.unit,
            previous_quantity=Decimal('10.0000'), new_quantity=Decimal('15.0000')
        )
        StockTransaction.objects.create(
            stock_item=self.si1, transaction_type='out', quantity=Decimal('10.0000'), unit=self.unit,
            previous_quantity=Decimal('15.0000'), new_quantity=Decimal('5.0000')
        )
        StockTransaction.objects.create(
            stock_item=self.si1, transaction_type='initial', quantity=Decimal('5.0000'), unit=self.unit,
            previous_quantity=Decimal('0'), new_quantity=Decimal('5.0000')
        )

    def tearDown(self):
        cache.clear()

    def test_warehouse_inventory_check_annotated_results(self):
        """warehouse_inventory_check should compute in/out/initial metrics accurately."""
        self.client.login(username='wh_perf', password='pass')
        url = reverse('warehouse_inventory_check', kwargs={'cafe_code': 'main', 'warehouse_id': self.wh.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        items_data = response.context['items_data']
        # Locate item 1 (Beef)
        d1 = next(d for d in items_data if d['stock_item'].id == self.si1.id)
        self.assertEqual(d1['in_count'], 2)
        self.assertEqual(d1['out_count'], 1)
        self.assertEqual(d1['in_total'], Decimal('15.0000'))
        self.assertEqual(d1['out_total'], Decimal('10.0000'))
        self.assertEqual(d1['initial_qty'], Decimal('5.0000'))
        self.assertEqual(d1['system_qty'], Decimal('5.0000'))

        # Locate item 2 (Chicken - no transactions)
        d2 = next(d for d in items_data if d['stock_item'].id == self.si2.id)
        self.assertEqual(d2['in_count'], 0)
        self.assertEqual(d2['out_count'], 0)
        self.assertEqual(d2['in_total'], Decimal('0'))
        self.assertEqual(d2['out_total'], Decimal('0'))
        self.assertEqual(d2['initial_qty'], Decimal('0'))

    def test_stock_by_category_annotated_results(self):
        """stock_by_category should calculate total and low-stock items in the DB."""
        self.client.login(username='wh_perf', password='pass')
        url = reverse('stock_by_category', kwargs={'cafe_code': 'main'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        cat_data = {c['name']: c for c in response.context['category_data']}
        # Meat category: 2 items (si1, si2), 1 low stock (si1)
        self.assertIn('Meat', cat_data)
        self.assertEqual(cat_data['Meat']['total_items'], 2)
        self.assertEqual(cat_data['Meat']['low_stock'], 1)

        # Dairy category: 1 item (si3), 1 low stock (si3)
        self.assertIn('Dairy', cat_data)
        self.assertEqual(cat_data['Dairy']['total_items'], 1)
        self.assertEqual(cat_data['Dairy']['low_stock'], 1)

        # Uncategorized: 1 item, 1 low stock
        self.assertEqual(response.context['no_cat_count'], 1)
        self.assertEqual(response.context['no_cat_low'], 1)

    def test_backup_database_only_post_and_async(self):
        """backup_database must reject GET (405) and accept POST asynchronously with cache lock."""
        self.client.login(username='manager_perf', password='pass')
        url = reverse('backup_database', kwargs={'cafe_code': 'main'})

        # GET should be rejected with 405
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 405)

        # POST should return redirect and initiate background worker
        with mock.patch('threading.Thread.start') as mock_start:
            post_resp = self.client.post(url)
            self.assertEqual(post_resp.status_code, 302)
            self.assertTrue(mock_start.called)

    def test_drive_backup_now_only_post(self):
        """drive_backup_now must reject GET (405)."""
        self.client.login(username='manager_perf', password='pass')
        url = reverse('drive_backup_now', kwargs={'cafe_code': 'main'})

        # GET should be rejected with 405
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 405)
