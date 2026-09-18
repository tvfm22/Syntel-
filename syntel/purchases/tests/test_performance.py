from django.test import TestCase, Client, TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.core.management import call_command
from purchases.models import (
    Cafe, Category, Unit, Product, Supplier, PurchaseRequest,
    PurchaseItem, PurchaseOrder, Invoice, Payment, PaymentLocation,
    ActivityLog, Notification
)
import time
import psutil
import os

# همهٔ URLهای داخل کافه با cafe_code پیشوند می‌شوند
CAFE_CODE = 'tc'

User = get_user_model()


class PerformanceTests(TransactionTestCase):
    """Performance tests for the Syntel system"""

    def setUp(self):
        """Set up test data and client"""
        self.client = Client()

        # Clean stale data when using --keepdb
        User.objects.filter(username__in=['kitchen_user', 'purchasing_user', 'manager_user']).delete()
        Category.objects.filter(name='Test Category').delete()
        Unit.objects.filter(name='Kilogram').delete()
        Product.objects.filter(name='Test Product').delete()
        Supplier.objects.filter(name='Test Supplier').delete()

        # کافه: همهٔ کاربران و دادهها به یک کافه وصلاند (URLهای کافهمحور)
        self.cafe = Cafe.objects.create(name='Main', code=CAFE_CODE, is_active=True)

        # Create test users
        self.kitchen_user = User.objects.create_user(
            username='kitchen_user',
            email='kitchen@example.com',
            password='testpass123',
            role='kitchen',
            cafe=self.cafe,
        )

        self.purchasing_user = User.objects.create_user(
            username='purchasing_user',
            email='purchasing@example.com',
            password='testpass123',
            role='purchasing',
            cafe=self.cafe,
        )

        self.manager_user = User.objects.create_user(
            username='manager_user',
            email='manager@example.com',
            password='testpass123',
            role='manager',
            cafe=self.cafe,
        )

        # Create basic data
        self.category = Category.objects.create(name='Test Category', cafe=self.cafe)
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.product = Product.objects.create(
            name='Test Product',
            category=self.category,
            unit=self.unit,
            min_stock=10,
            cafe=self.cafe,
        )
        self.supplier = Supplier.objects.create(
            name='Test Supplier',
            contact_person='John Doe',
            email='supplier@example.com',
            phone='1234567890',
            cafe=self.cafe,
        )

        # URLهای کافهمحور به کافهٔ انتخابشده در نشست نیاز دارند
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()

    def _measure_response_time(self, url, method='get', data=None, user=None):
        """Helper method to measure response time"""
        if user:
            self.client.login(username=user.username, password='testpass123')

        start_time = time.time()

        if method.lower() == 'get':
            response = self.client.get(url)
        elif method.lower() == 'post':
            response = self.client.post(url, data or {})
        else:
            raise ValueError(f"Unsupported method: {method}")

        end_time = time.time()
        response_time = end_time - start_time

        return response, response_time

    def test_dashboard_load_performance(self):
        """Test dashboard loading performance"""
        # Create test data
        for i in range(50):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased',
                cafe=self.cafe,
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10,
                purchased_quantity=10,
                unit='kg'
            )

        # Test kitchen dashboard
        response, response_time = self._measure_response_time(
            reverse('dashboard', kwargs={'cafe_code': CAFE_CODE}),
            user=self.kitchen_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 2.0, "Dashboard should load in under 2 seconds")

        # Test purchasing dashboard
        response, response_time = self._measure_response_time(
            reverse('dashboard', kwargs={'cafe_code': CAFE_CODE}),
            user=self.purchasing_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 2.0, "Dashboard should load in under 2 seconds")

    def test_purchase_request_list_performance(self):
        """Test purchase request list performance with large dataset"""
        # Create large dataset
        for i in range(100):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user if i % 2 == 0 else self.purchasing_user,
                status='pending' if i % 3 == 0 else 'purchased',
                cafe=self.cafe,
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10 + i,
                purchased_quantity=10 + i if request.status == 'purchased' else 0,
                unit='kg'
            )

        response, response_time = self._measure_response_time(
            reverse('purchase_requests_list', kwargs={'cafe_code': CAFE_CODE}),
            user=self.purchasing_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 3.0, "Purchase requests list should load in under 3 seconds")

    def test_reports_generation_performance(self):
        """Test reports generation performance"""
        # Create test data for reports
        for i in range(200):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased',
                created_at=timezone.now() - timezone.timedelta(days=i % 30)
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10,
                purchased_quantity=10,
                unit='kg'
            )

            if i % 5 == 0:  # Create some orders and invoices
                order = PurchaseOrder.objects.create(
                    request=request,
                    supplier=self.supplier,
                    order_number=f'ORD-PERF-{i:03d}',
                    status='completed',
                )
                invoice = Invoice.objects.create(
                    order=order,
                    invoice_number=f'INV-PERF-{i:03d}',
                    total_amount=1000.00,
                    status='paid',
                    cafe=self.cafe,
                )
                payment_method = PaymentLocation.objects.create(name=f'Method {i}', cafe=self.cafe)
                Payment.objects.create(
                    invoice=invoice,
                    amount=1000.00,
                    payment_date=timezone.now().date(),
                    payment_location=payment_method,
                    status='completed'
                )

        # Test reports dashboard
        response, response_time = self._measure_response_time(
            reverse('reports_dashboard', kwargs={'cafe_code': CAFE_CODE}),
            user=self.purchasing_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 5.0, "Reports dashboard should load in under 5 seconds")

    def test_excel_export_performance(self):
        """Test Excel export performance"""
        # Create test data
        for i in range(500):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased',
                cafe=self.cafe,
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10,
                purchased_quantity=10,
                unit='kg'
            )

        response, response_time = self._measure_response_time(
            reverse('export_to_excel', kwargs={'cafe_code': CAFE_CODE}),
            user=self.purchasing_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 10.0, "Excel export should complete in under 10 seconds")

    def test_pdf_export_performance(self):
        """Test PDF export performance"""
        # Create test data
        for i in range(100):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased',
                cafe=self.cafe,
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10,
                purchased_quantity=10,
                unit='kg'
            )

        response, response_time = self._measure_response_time(
            reverse('export_to_pdf', kwargs={'cafe_code': CAFE_CODE}),
            user=self.purchasing_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 15.0, "PDF export should complete in under 15 seconds")

    def test_system_monitoring_api_performance(self):
        """Test system monitoring API performance"""
        # Create activity data
        for i in range(100):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='pending',
                cafe=self.cafe,
            )
            ActivityLog.objects.create(
                user=self.kitchen_user,
                action='created',
                description=f'Performance test activity {i+1}',
                request=request
            )

        response, response_time = self._measure_response_time(
            reverse('system_monitoring_api', kwargs={'cafe_code': CAFE_CODE}),
            user=self.manager_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 2.0, "Monitoring API should respond in under 2 seconds")

    def test_database_query_performance(self):
        """Test database query performance"""
        # Create large dataset
        users = [self.kitchen_user, self.purchasing_user]
        statuses = ['pending', 'purchased', 'approved', 'ordered']

        for i in range(1000):
            request = PurchaseRequest.objects.create(
                user=users[i % 2],
                status=statuses[i % 4],
                cafe=self.cafe,
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10,
                purchased_quantity=10 if request.status == 'purchased' else 0,
                unit='kg'
            )

        # Test complex queries
        start_time = time.time()

        # Query 1: Recent pending requests
        pending_requests = PurchaseRequest.objects.filter(
            status='pending',
            created_at__gte=timezone.now() - timezone.timedelta(days=7)
        ).select_related('user').order_by('-created_at')[:50]

        # Query 2: Purchase statistics
        from django.db.models import Sum, Count
        stats = PurchaseRequest.objects.filter(
            status='purchased'
        ).aggregate(
            total_requests=Count('id'),
            total_quantity=Sum('items__purchased_quantity')
        )

        # Query 3: User activity summary
        user_activity = PurchaseRequest.objects.values('user__username').annotate(
            request_count=Count('id'),
            total_quantity=Sum('items__purchased_quantity')
        ).order_by('-request_count')[:10]

        end_time = time.time()
        query_time = end_time - start_time

        self.assertLess(query_time, 5.0, "Complex queries should complete in under 5 seconds")
        self.assertGreater(len(list(pending_requests)), 0)
        self.assertIn('total_requests', stats)
        self.assertGreater(len(user_activity), 0)

    def test_concurrent_user_performance(self):
        """Test performance under concurrent user load"""
        # Create multiple users and test concurrent access
        users = []
        for i in range(10):
            user = User.objects.create_user(
                username=f'concurrent_user_{i}',
                email=f'concurrent{i}@example.com',
                password='testpass123',
                role='kitchen',
                cafe=self.cafe,
            )
            users.append(user)

        # Create shared data
        for i in range(50):
            request = PurchaseRequest.objects.create(
                user=users[i % len(users)],
                status='pending',
                cafe=self.cafe,
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=5,
                unit='kg'
            )

        # Test concurrent dashboard access (simulated)
        total_time = 0
        for user in users[:5]:  # Test first 5 users
            response, response_time = self._measure_response_time(
                reverse('dashboard', kwargs={'cafe_code': CAFE_CODE}),
                user=user
            )
            self.assertEqual(response.status_code, 200)
            total_time += response_time

        average_time = total_time / 5
        self.assertLess(average_time, 1.0, "Average dashboard load time should be under 1 second")

    def test_memory_usage_during_operations(self):
        """Test memory usage during heavy operations"""
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Perform memory-intensive operation
        for i in range(1000):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='pending',
                cafe=self.cafe,
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10,
                unit='kg'
            )

        # Check memory after operation
        after_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = after_memory - initial_memory

        # Memory increase should be reasonable (less than 50MB for 1000 objects)
        self.assertLess(memory_increase, 50.0, "Memory usage increase should be reasonable")

    def test_form_processing_performance(self):
        """Test form processing performance"""
        # Test purchase request form
        response, response_time = self._measure_response_time(
            reverse('purchase_request', kwargs={'cafe_code': CAFE_CODE}),
            method='get',
            user=self.kitchen_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 1.0, "Form loading should be under 1 second")

        # Test form submission
        response, response_time = self._measure_response_time(
            reverse('purchase_request', kwargs={'cafe_code': CAFE_CODE}),
            method='post',
            data={
                'product_name': [self.product.name],
                'item_quantity': ['10'],
                'item_description': ['Performance test']
            },
            user=self.kitchen_user
        )
        self.assertEqual(response.status_code, 302)  # Redirect
        self.assertLess(response_time, 2.0, "Form submission should complete in under 2 seconds")

    def test_search_performance(self):
        """Test search functionality performance"""
        # Create searchable data
        suppliers = []
        products = []
        for i in range(100):
            supplier = Supplier.objects.create(
                name=f'Search Supplier {i:03d}',
                contact_person=f'Contact {i:03d}',
                email=f'supplier{i:03d}@example.com',
                phone='1234567890',
                cafe=self.cafe,
            )
            suppliers.append(supplier)

            product = Product.objects.create(
                name=f'Search Product {i:03d}',
                category=self.category,
                unit=self.unit,
                min_stock=10,
                cafe=self.cafe,
            )
            products.append(product)

        # Test supplier list loading
        response, response_time = self._measure_response_time(
            reverse('supplier_list', kwargs={'cafe_code': CAFE_CODE}),
            user=self.purchasing_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 2.0, "Supplier list should load in under 2 seconds")

        # Test product list loading
        response, response_time = self._measure_response_time(
            reverse('product_list', kwargs={'cafe_code': CAFE_CODE}),
            user=self.purchasing_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 2.0, "Product list should load in under 2 seconds")

    def test_notification_performance(self):
        """Test notification system performance"""
        # Create many notifications
        for i in range(200):
            Notification.objects.create(
                recipient=self.kitchen_user,
                cafe=self.cafe,
                title=f'Performance Notification {i+1}',
                message=f'Test message {i+1}',
                notification_type='info'
            )

        # Test notifications list
        response, response_time = self._measure_response_time(
            reverse('notifications_list', kwargs={'cafe_code': CAFE_CODE}),
            user=self.kitchen_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 2.0, "Notifications list should load in under 2 seconds")

    def test_activity_log_performance(self):
        """Test activity log performance"""
        # Create many activity entries
        for i in range(500):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='pending',
                cafe=self.cafe,
            )
            ActivityLog.objects.create(
                user=self.kitchen_user,
                action='created',
                description=f'Performance activity {i+1}',
                request=request
            )

        # صفحهٔ مستقلی برای لاگ فعالیت وجود ندارد؛ آخرین فعالیتها از API
        # پایش سیستم خوانده میشوند (فقط مدیر)
        response, response_time = self._measure_response_time(
            reverse('system_monitoring_api', kwargs={'cafe_code': CAFE_CODE}),
            user=self.manager_user
        )
        self.assertEqual(response.status_code, 200)
        self.assertLess(response_time, 2.0, "Activity log should load in under 2 seconds")

    def test_bulk_operations_performance(self):
        """Test bulk operations performance"""
        # Create bulk data
        requests = []
        for i in range(50):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='pending',
                cafe=self.cafe,
            )
            requests.append(request)

        # Test bulk status update (if implemented)
        # This would depend on actual bulk operation implementation

        # For now, test individual updates
        total_time = 0
        for request in requests[:10]:  # Update first 10
            start_time = time.time()
            request.status = 'approved'
            request.save()
            end_time = time.time()
            total_time += (end_time - start_time)

        average_time = total_time / 10
        self.assertLess(average_time, 0.1, "Individual updates should be fast")

    def test_cache_performance(self):
        """Test caching performance (if implemented)"""
        # This would test caching mechanisms if they exist
        # For now, just test repeated requests

        response_times = []
        for i in range(5):
            response, response_time = self._measure_response_time(
                reverse('dashboard', kwargs={'cafe_code': CAFE_CODE}),
                user=self.purchasing_user
            )
            self.assertEqual(response.status_code, 200)
            response_times.append(response_time)

        # Check if response times improve (caching effect)
        # This is a simple check - in practice, caching might not be noticeable in tests
        average_time = sum(response_times) / len(response_times)
        self.assertLess(average_time, 1.0, "Average response time should be reasonable")

    def test_system_resource_monitoring(self):
        """Test system resource usage during operations"""
        # Monitor CPU and memory during heavy operations
        process = psutil.Process(os.getpid())

        cpu_before = psutil.cpu_percent(interval=1)
        memory_before = process.memory_info().rss / 1024 / 1024  # MB

        # Perform CPU-intensive operation
        for i in range(10000):
            # Simple calculation to use CPU
            result = i * i + i

        cpu_after = psutil.cpu_percent(interval=1)
        memory_after = process.memory_info().rss / 1024 / 1024  # MB

        # CPU usage should not be excessively high
        cpu_increase = cpu_after - cpu_before
        self.assertLess(cpu_increase, 50.0, "CPU usage increase should be reasonable")

        # Memory should not leak significantly
        memory_increase = memory_after - memory_before
        self.assertLess(memory_increase, 10.0, "Memory increase should be minimal")
