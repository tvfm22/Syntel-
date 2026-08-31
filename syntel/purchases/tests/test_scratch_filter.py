# -*- coding: utf-8 -*-
"""Scratch: فیلتر پس از ایجاد حواله از فرم یکپارچه"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Product, StockItem, Unit, Warehouse, WarehouseVoucher,
)

User = get_user_model()


class ScratchFilter(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='store_s', password='testpass123', role='store')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.user.cafe = self.cafe
        self.user.save()
        s = self.client.session
        s['selected_cafe_id'] = self.cafe.id
        s['selected_cafe_code'] = self.cafe.code
        s.save()
        self.client.login(username='store_s', password='testpass123')
        self.unit = Unit.objects.create(name='kg', symbol='kg', cafe=self.cafe)
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe, code='whA', name='انبار A', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=1)
        self.stock_a = StockItem.objects.create(
            product=self.product, warehouse=self.wh_a, quantity=10, unit=self.unit)

    def _create_post(self, voucher_type, source=None, dest=None, qty='5', stock_item=None):
        data = {
            'action': 'submit',
            'product_id[]': [str(self.product.id)],
            'product_brand_id[]': [''],
            'stock_item_id[]': [str(stock_item.id) if stock_item else ''],
            'unit_id[]': [str(self.unit.id)],
            'base_quantity[]': [qty],
            'unit_price[]': ['1000'],
            'item_description[]': ['قلم آزمایشی'],
        }
        if source:
            data['source_warehouse'] = str(source.id)
        if dest:
            data['dest_warehouse'] = str(dest.id)
        return self.client.post(
            reverse('voucher_create', kwargs={'cafe_code': 'main',
                                              'voucher_type': voucher_type}),
            data=data)

    def test_filter_after_form_create(self):
        r1 = self._create_post('receipt', dest=self.wh_a, qty='1')
        print('CREATE RECEIPT:', r1.status_code)
        r2 = self._create_post('issue', source=self.wh_a, qty='1',
                               stock_item=self.stock_a)
        print('CREATE ISSUE:', r2.status_code)
        print('VOUCHERS:', list(WarehouseVoucher.objects.values(
            'id', 'voucher_type', 'voucher_number', 'status')))
        r = self.client.get(reverse('voucher_list', kwargs={'cafe_code': 'main'}),
                            {'type': 'receipt'})
        content = r.content.decode('utf-8')
        print('STATUS:', r.status_code)
        print('RCV-00001 in:', 'RCV-00001' in content)
        print('ISS-00001 in:', 'ISS-00001' in content)
        i = content.find('ISS-00001')
        if i != -1:
            print('ISS context:', content[max(0, i - 250):i + 120].replace('\n', ' '))
        self.assertTrue(True)
