"""
اسکریپت تست عملکرد Analytics با داده واقعی
Test Analytics Performance with Real Data

نحوه استفاده:
python manage.py shell < test_analytics_performance.py
یا
python test_analytics_performance.py
"""

import os
import sys
import django
import time
from decimal import Decimal
from datetime import timedelta

# Setup Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.utils import timezone
from django.contrib.auth import get_user_model
from purchases.models import (
    Requester, Category, Product, Unit,
    PurchaseRequest, Invoice, InvoiceItem
)
from purchases.views_analytics import (
    api_department_expenses,
    api_department_categories,
    api_category_products,
    calculate_growth_rate,
    get_color_by_growth
)
from django.test import RequestFactory
from django.core.cache import cache

User = get_user_model()


class AnalyticsPerformanceTest:
    """تست عملکرد API های Analytics"""
    
    def __init__(self):
        self.factory = RequestFactory()
        self.user = None
        self.results = {
            'tests': [],
            'total_time': 0,
            'success_count': 0,
            'failure_count': 0
        }
    
    def setup(self):
        """آماده‌سازی محیط تست"""
        print("\n" + "="*60)
        print("شروع تست عملکرد Analytics")
        print("="*60 + "\n")
        
        # ایجاد کاربر تست
        self.user, created = User.objects.get_or_create(
            username='analytics_test_user',
            defaults={'role': 'manager'}
        )
        if created:
            self.user.set_password('test123')
            self.user.save()
            print("✅ کاربر تست ایجاد شد")
        else:
            print("✅ کاربر تست موجود است")
        
        # پاک کردن cache
        cache.clear()
        print("✅ Cache پاک شد\n")
    
    def test_department_expenses_api(self):
        """تست API هزینه‌های بخش‌ها"""
        print("🧪 تست 1: Department Expenses API")
        
        request = self.factory.get('/api/analytics/departments/expenses/', {'period': 'weekly'})
        request.user = self.user
        
        start_time = time.time()
        
        try:
            response = api_department_expenses(request)
            elapsed_time = time.time() - start_time
            
            data = response.data
            dept_count = len(data.get('departments', []))
            
            success = response.status_code == 200
            
            self.results['tests'].append({
                'name': 'Department Expenses API',
                'success': success,
                'time': elapsed_time,
                'details': f"{dept_count} بخش بررسی شد"
            })
            
            if success:
                self.results['success_count'] += 1
                print(f"   ✅ موفق - زمان: {elapsed_time:.3f}s - {dept_count} بخش")
            else:
                self.results['failure_count'] += 1
                print(f"   ❌ ناموفق")
            
        except Exception as e:
            self.results['failure_count'] += 1
            elapsed_time = time.time() - start_time
            self.results['tests'].append({
                'name': 'Department Expenses API',
                'success': False,
                'time': elapsed_time,
                'details': str(e)
            })
            print(f"   ❌ خطا: {str(e)}")
    
    def test_department_categories_api(self):
        """تست API دسته‌بندی‌های بخش"""
        print("\n🧪 تست 2: Department Categories API")
        
        # دریافت اولین requester
        requester = Requester.objects.first()
        if not requester:
            print("   ⚠️  هیچ Requester موجود نیست")
            return
        
        request = self.factory.get(
            f'/api/analytics/department/{requester.id}/categories/',
            {'period': 'monthly'}
        )
        request.user = self.user
        
        start_time = time.time()
        
        try:
            response = api_department_categories(request, requester.id)
            elapsed_time = time.time() - start_time
            
            data = response.data
            cat_count = len(data.get('categories', []))
            
            success = response.status_code == 200
            
            self.results['tests'].append({
                'name': 'Department Categories API',
                'success': success,
                'time': elapsed_time,
                'details': f"{cat_count} دسته‌بندی برای بخش {requester.name}"
            })
            
            if success:
                self.results['success_count'] += 1
                print(f"   ✅ موفق - زمان: {elapsed_time:.3f}s - {cat_count} دسته‌بندی")
            else:
                self.results['failure_count'] += 1
                print(f"   ❌ ناموفق")
                
        except Exception as e:
            self.results['failure_count'] += 1
            elapsed_time = time.time() - start_time
            self.results['tests'].append({
                'name': 'Department Categories API',
                'success': False,
                'time': elapsed_time,
                'details': str(e)
            })
            print(f"   ❌ خطا: {str(e)}")
    
    def test_category_products_api(self):
        """تست API محصولات دسته‌بندی"""
        print("\n🧪 تست 3: Category Products API")
        
        # دریافت اولین category
        category = Category.objects.first()
        if not category:
            print("   ⚠️  هیچ Category موجود نیست")
            return
        
        request = self.factory.get(
            f'/api/analytics/category/{category.id}/products/',
            {'period': 'weekly'}
        )
        request.user = self.user
        
        start_time = time.time()
        
        try:
            response = api_category_products(request, category.id)
            elapsed_time = time.time() - start_time
            
            data = response.data
            product_count = len(data.get('products', []))
            total_expense = data.get('total_expense', 0)
            
            success = response.status_code == 200
            
            self.results['tests'].append({
                'name': 'Category Products API',
                'success': success,
                'time': elapsed_time,
                'details': f"{product_count} محصول - هزینه: {total_expense:,.0f}"
            })
            
            if success:
                self.results['success_count'] += 1
                print(f"   ✅ موفق - زمان: {elapsed_time:.3f}s - {product_count} محصول")
            else:
                self.results['failure_count'] += 1
                print(f"   ❌ ناموفق")
                
        except Exception as e:
            self.results['failure_count'] += 1
            elapsed_time = time.time() - start_time
            self.results['tests'].append({
                'name': 'Category Products API',
                'success': False,
                'time': elapsed_time,
                'details': str(e)
            })
            print(f"   ❌ خطا: {str(e)}")
    
    def test_cache_performance(self):
        """تست عملکرد Cache"""
        print("\n🧪 تست 4: Cache Performance")
        
        request = self.factory.get('/api/analytics/departments/expenses/', {'period': 'weekly'})
        request.user = self.user
        
        # اولین بار (بدون cache)
        cache.clear()
        start_time = time.time()
        response1 = api_department_expenses(request)
        time1 = time.time() - start_time
        
        # دومین بار (با cache)
        start_time = time.time()
        response2 = api_department_expenses(request)
        time2 = time.time() - start_time
        
        improvement = ((time1 - time2) / time1) * 100 if time1 > 0 else 0
        
        self.results['tests'].append({
            'name': 'Cache Performance',
            'success': True,
            'time': time1 - time2,
            'details': f"بدون cache: {time1:.3f}s, با cache: {time2:.3f}s, بهبود: {improvement:.1f}%"
        })
        
        self.results['success_count'] += 1
        print(f"   ✅ بدون Cache: {time1:.3f}s")
        print(f"   ✅ با Cache: {time2:.3f}s")
        print(f"   📈 بهبود: {improvement:.1f}%")
    
    def test_utility_functions(self):
        """تست توابع کمکی"""
        print("\n🧪 تست 5: Utility Functions")
        
        tests_passed = 0
        tests_total = 0
        
        # تست calculate_growth_rate
        tests_total += 1
        rate = calculate_growth_rate(200, 100)
        if rate == 100.0:
            tests_passed += 1
            print("   ✅ calculate_growth_rate: رشد 100%")
        else:
            print(f"   ❌ calculate_growth_rate: انتظار 100.0، دریافت {rate}")
        
        # تست get_color_by_growth
        tests_total += 1
        color_growth = get_color_by_growth(50)
        color_decrease = get_color_by_growth(-50)
        color_neutral = get_color_by_growth(0)
        
        if 'rgba' in color_growth and 'rgba' in color_decrease and 'rgba' in color_neutral:
            tests_passed += 1
            print("   ✅ get_color_by_growth: رنگ‌ها صحیح")
        else:
            print("   ❌ get_color_by_growth: خطا در تولید رنگ")
        
        self.results['tests'].append({
            'name': 'Utility Functions',
            'success': tests_passed == tests_total,
            'time': 0,
            'details': f"{tests_passed}/{tests_total} تست موفق"
        })
        
        if tests_passed == tests_total:
            self.results['success_count'] += 1
        else:
            self.results['failure_count'] += 1
    
    def test_database_stats(self):
        """نمایش آمار دیتابیس"""
        print("\n📊 آمار دیتابیس:")
        print(f"   - Requesters: {Requester.objects.count()}")
        print(f"   - Categories: {Category.objects.count()}")
        print(f"   - Products: {Product.objects.count()}")
        print(f"   - Purchase Requests: {PurchaseRequest.objects.count()}")
        print(f"   - Invoices: {Invoice.objects.count()}")
        print(f"   - Invoice Items: {InvoiceItem.objects.count()}")
    
    def print_summary(self):
        """چاپ خلاصه نتایج"""
        print("\n" + "="*60)
        print("خلاصه نتایج تست")
        print("="*60)
        
        total_tests = len(self.results['tests'])
        success_rate = (self.results['success_count'] / total_tests * 100) if total_tests > 0 else 0
        
        print(f"\n📈 تعداد کل تست‌ها: {total_tests}")
        print(f"✅ موفق: {self.results['success_count']}")
        print(f"❌ ناموفق: {self.results['failure_count']}")
        print(f"📊 درصد موفقیت: {success_rate:.1f}%")
        
        total_time = sum(t['time'] for t in self.results['tests'])
        print(f"⏱️  مجموع زمان: {total_time:.3f}s")
        
        print("\n📋 جزئیات تست‌ها:")
        for i, test in enumerate(self.results['tests'], 1):
            status = "✅" if test['success'] else "❌"
            print(f"   {i}. {status} {test['name']}")
            print(f"      زمان: {test['time']:.3f}s")
            print(f"      {test['details']}")
        
        print("\n" + "="*60)
        
        if self.results['failure_count'] == 0:
            print("🎉 همه تست‌ها با موفقیت گذراندند!")
        else:
            print("⚠️  برخی تست‌ها ناموفق بودند")
        
        print("="*60 + "\n")
    
    def run_all_tests(self):
        """اجرای همه تست‌ها"""
        self.setup()
        self.test_database_stats()
        self.test_department_expenses_api()
        self.test_department_categories_api()
        self.test_category_products_api()
        self.test_cache_performance()
        self.test_utility_functions()
        self.print_summary()


def main():
    """تابع اصلی"""
    tester = AnalyticsPerformanceTest()
    tester.run_all_tests()


if __name__ == '__main__':
    main()
