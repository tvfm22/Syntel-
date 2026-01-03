"""
Test Analytics Bubble Chart APIs
تست API های نمودار حبابی
"""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone

from purchases.models import (
    Requester, Category, Product, Unit, 
    PurchaseRequest, Invoice, InvoiceItem
)

User = get_user_model()


class AnalyticsBubbleChartAPITests(TestCase):
    """تست‌های API نمودارهای حبابی"""
    
    def setUp(self):
        """آماده‌سازی داده‌های تست"""
        # ایجاد کاربر
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            role='manager'
        )
        
        # ایجاد client و login
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')
        
        # ایجاد Requester (بخش)
        self.requester1 = Requester.objects.create(name='انبار')
        self.requester2 = Requester.objects.create(name='فروش')
        
        # ایجاد Unit
        self.unit = Unit.objects.create(name='عدد')
        
        # ایجاد Category
        self.category1 = Category.objects.create(name='لوازم برقی')
        self.category2 = Category.objects.create(name='لوازم اداری')
        
        # ایجاد Product
        self.product1 = Product.objects.create(
            name='لامپ LED',
            category=self.category1,
            unit=self.unit
        )
        self.product2 = Product.objects.create(
            name='خودکار',
            category=self.category2,
            unit=self.unit
        )
        
        # ایجاد Purchase Request
        self.request1 = PurchaseRequest.objects.create(
            requester=self.requester1,
            user=self.user,
            description='تست'
        )
        
        # ایجاد Invoice
        now = timezone.now()
        self.invoice_current = Invoice.objects.create(
            purchase_request=self.request1,
            invoice_number='INV-001',
            status='paid',
            created_at=now
        )
        
        self.invoice_previous = Invoice.objects.create(
            purchase_request=self.request1,
            invoice_number='INV-002',
            status='paid',
            created_at=now - timedelta(days=14)
        )
        
        # ایجاد Invoice Items - دوره فعلی
        InvoiceItem.objects.create(
            invoice=self.invoice_current,
            product=self.product1,
            quantity=10,
            unit_price=Decimal('50000'),
            total_amount=Decimal('500000')
        )
        
        # ایجاد Invoice Items - دوره قبل
        InvoiceItem.objects.create(
            invoice=self.invoice_previous,
            product=self.product1,
            quantity=5,
            unit_price=Decimal('40000'),
            total_amount=Decimal('200000')
        )
    
    def test_department_expenses_api(self):
        """تست API هزینه بخش‌ها"""
        url = '/api/analytics/departments/expenses/'
        response = self.client.get(url, {'period': 'weekly'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertIn('departments', data)
        self.assertIsInstance(data['departments'], list)
        
        if len(data['departments']) > 0:
            dept = data['departments'][0]
            self.assertIn('id', dept)
            self.assertIn('name', dept)
            self.assertIn('current_expense', dept)
            self.assertIn('previous_expense', dept)
            self.assertIn('growth_rate', dept)
            self.assertIn('color', dept)
    
    def test_department_categories_api(self):
        """تست API دسته‌بندی‌های یک بخش"""
        url = f'/api/analytics/department/{self.requester1.id}/categories/'
        response = self.client.get(url, {'period': 'weekly'})
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertIn('department', data)
        self.assertIn('categories', data)
        self.assertEqual(data['department']['id'], self.requester1.id)
    
    def test_category_products_api(self):
        """تست API محصولات یک دسته‌بندی"""
        url = f'/api/analytics/category/{self.category1.id}/products/'
        response = self.client.get(url, {
            'period': 'weekly',
            'department_id': self.requester1.id
        })
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertIn('category', data)
        self.assertIn('products', data)
        self.assertIn('total_expense', data)
    
    def test_invalid_department_id(self):
        """تست با شناسه بخش نامعتبر"""
        url = '/api/analytics/department/9999/categories/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 404)
    
    def test_invalid_category_id(self):
        """تست با شناسه دسته‌بندی نامعتبر"""
        url = '/api/analytics/category/9999/products/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 404)
    
    def test_different_periods(self):
        """تست دوره‌های زمانی مختلف"""
        url = '/api/analytics/departments/expenses/'
        
        periods = ['weekly', 'monthly', 'quarterly', 'yearly']
        
        for period in periods:
            response = self.client.get(url, {'period': period})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn('departments', data)
    
    def test_unauthenticated_access(self):
        """تست دسترسی بدون احراز هویت"""
        self.client.logout()
        
        url = '/api/analytics/departments/expenses/'
        response = self.client.get(url)
        
        # باید به صفحه login redirect شود یا 401/403 برگردد
        self.assertIn(response.status_code, [302, 401, 403])
    
    def test_growth_rate_calculation(self):
        """تست محاسبه نرخ رشد"""
        from purchases.views_analytics import calculate_growth_rate
        
        # رشد 100%
        rate = calculate_growth_rate(200, 100)
        self.assertEqual(rate, 100.0)
        
        # کاهش 50%
        rate = calculate_growth_rate(50, 100)
        self.assertEqual(rate, -50.0)
        
        # بدون تغییر
        rate = calculate_growth_rate(100, 100)
        self.assertEqual(rate, 0.0)
        
        # قبلی صفر
        rate = calculate_growth_rate(100, 0)
        self.assertEqual(rate, 100.0)
        
        # هر دو صفر
        rate = calculate_growth_rate(0, 0)
        self.assertEqual(rate, 0.0)
    
    def test_color_by_growth(self):
        """تست تولید رنگ بر اساس رشد"""
        from purchases.views_analytics import get_color_by_growth
        
        # رشد مثبت - باید قرمز باشد
        color = get_color_by_growth(50)
        self.assertIn('255', color)  # Red channel
        
        # کاهش منفی - باید سبز باشد
        color = get_color_by_growth(-50)
        self.assertIn('192', color)  # Green channel
        
        # بدون تغییر - باید خاکستری باشد
        color = get_color_by_growth(0)
        self.assertIn('201', color)


class AnalyticsUtilsTests(TestCase):
    """تست توابع کمکی"""
    
    def test_get_date_range(self):
        """تست محاسبه بازه زمانی"""
        from purchases.views_analytics import get_date_range
        
        # هفتگی
        start, end, prev_start, prev_end = get_date_range('weekly')
        delta = end - start
        self.assertEqual(delta.days, 7)
        
        # ماهانه
        start, end, prev_start, prev_end = get_date_range('monthly')
        delta = end - start
        self.assertEqual(delta.days, 30)
        
        # فصلی
        start, end, prev_start, prev_end = get_date_range('quarterly')
        delta = end - start
        self.assertEqual(delta.days, 90)
        
        # سالانه
        start, end, prev_start, prev_end = get_date_range('yearly')
        delta = end - start
        self.assertEqual(delta.days, 365)


# اجرای تست‌ها
if __name__ == '__main__':
    import unittest
    unittest.main()
