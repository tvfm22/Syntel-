"""سنگ‌آزمایش: کارت درخواستی که همه اقلامش ثبت شده به پایین لیست داشبورد مسئول انبار می‌رود."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Unit, Product, PurchaseRequest, PurchaseItem,
    OfficerAssignment, Warehouse, StockItem,
)

User = get_user_model()


class OfficerDashboardCardSortTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.officer = User.objects.create_user(
            username='wo_sort_officer', password='testpass123', role='warehouse')

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()
        self.officer.cafe = self.cafe
        self.officer.save()

        self.ceo = User.objects.create_user(
            username='ceo_sorter', password='testpass123', role='manager', cafe=self.cafe)
        self.warehouse = Warehouse.objects.create(name='WH1', cafe=self.cafe)
        self.prod_old = Product.objects.create(name='old prod', unit=self.unit)
        self.prod_new = Product.objects.create(name='new prod', unit=self.unit)
        StockItem.objects.create(product=self.prod_old, warehouse=self.warehouse,
                                 quantity=Decimal('50'), unit=self.unit)
        StockItem.objects.create(product=self.prod_new, warehouse=self.warehouse,
                                 quantity=Decimal('50'), unit=self.unit)

    def test_fully_processed_request_moves_to_bottom(self):
        req_old = PurchaseRequest.objects.create(
            user=self.ceo, cafe=self.cafe, status='pending',
            created_at=timezone.now() - timedelta(days=2))
        PurchaseItem.objects.create(request=req_old, product=self.prod_old,
                                    name='old prod', quantity=Decimal('5'), unit='kg')
        req_new = PurchaseRequest.objects.create(
            user=self.ceo, cafe=self.cafe, status='pending',
            created_at=timezone.now() - timedelta(days=1))
        PurchaseItem.objects.create(request=req_new, product=self.prod_new,
                                    name='new prod', quantity=Decimal('3'), unit='kg')

        # قلم درخواست قدیمی تامین شده → کارت آن باید پایین برود
        OfficerAssignment.objects.create(
            item=PurchaseItem.objects.get(request=req_old),
            target_role='warehouse', status='fulfilled',
            assigned_by=self.officer, assigned_to=self.officer,
            supplied_quantity=Decimal('5'))

        self.client.force_login(self.officer)
        resp = self.client.get(reverse('warehouse_officer_dashboard', args=[self.cafe.code]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        idx_old = body.find('درخواست #{}'.format(req_old.id))
        idx_new = body.find('درخواست #{}'.format(req_new.id))
        self.assertTrue(idx_old != -1 and idx_new != -1)
        self.assertLess(idx_new, idx_old,
                        'درخواستِ پردازش‌شده باید پایین‌تر از درخواستِ در انتظار باشد')
