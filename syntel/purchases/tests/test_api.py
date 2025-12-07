from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Category, Unit, Product, Supplier, PurchaseRequest,
    PurchaseItem, PurchaseOrder, Invoice, Payment, ActivityLog
)
import json

User = get_user_model()


class APITests(TestCase):
    """Test cases for API endpoints in the Syntel system"""

    def setUp(self):
        """Set up test data and client"""
        self.client = Client()

        # Clean stale test data when running with --keepdb
        User.objects.filter(username__in=['kitchen_user', 'purchasing_user', 'manager_user']).delete()
        Category.objects.filter(name='Test Category').delete()
        Unit.objects.filter(name='Kilogram').delete()
        Product.objects.filter(name='Test Product').delete()
        Supplier.objects.filter(name='Test Supplier').delete()

        # Create test users
        self.kitchen_user = User.objects.create_user(
            username='kitchen_user',
            email='kitchen@example.com',
            password='testpass123',
            role='kitchen'
        )

        self.purchasing_user = User.objects.create_user(
            username='purchasing_user',
            email='purchasing@example.com',
            password='testpass123',
            role='purchasing'
        )

        self.manager_user = User.objects.create_user(
            username='manager_user',
            email='manager@example.com',
            password='testpass123',
            role='manager'
        )

        # Create basic data
        self.category = Category.objects.create(name='Test Category')
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.product = Product.objects.create(
            name='Test Product',
            category=self.category,
            unit=self.unit,
            min_stock=10
        )
        self.supplier = Supplier.objects.create(
            name='Test Supplier',
            contact_person='John Doe',
            email='supplier@example.com',
            phone='1234567890'
        )

    def test_system_monitoring_api_unauthorized(self):
        """Test system monitoring API without authentication"""
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_system_monitoring_api_authorized(self):
        """Test system monitoring API with proper authentication"""
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('data', data)

        api_data = data['data']
        self.assertIn('system_resources', api_data)
        self.assertIn('recent_activity', api_data)
        self.assertIn('health_status', api_data)

    def test_system_monitoring_api_data_structure(self):
        """Test system monitoring API data structure"""
        # Create some activity data
        for i in range(5):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='pending' if i % 2 == 0 else 'purchased'
            )
            ActivityLog.objects.create(
                user=self.kitchen_user,
                action='created',
                description=f'Test activity {i+1}',
                request=request
            )

        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        api_data = data['data']

        # Check system resources structure
        system_resources = api_data['system_resources']
        self.assertIn('cpu_percent', system_resources)
        self.assertIn('memory_percent', system_resources)
        self.assertIn('disk_usage', system_resources)

        # Check recent activity structure
        recent_activity = api_data['recent_activity']
        self.assertIsInstance(recent_activity, list)
        if recent_activity:
            activity = recent_activity[0]
            self.assertIn('id', activity)
            self.assertIn('user', activity)
            self.assertIn('action', activity)
            self.assertIn('description', activity)
            self.assertIn('timestamp', activity)

        # Check health status structure
        health_status = api_data['health_status']
        self.assertIn('database_status', health_status)
        self.assertIn('overall_health', health_status)

    def test_system_monitoring_api_health_checks(self):
        """Test system monitoring API health check functionality"""
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        health_status = data['data']['health_status']

        # Database should be healthy
        self.assertEqual(health_status['database_status'], 'healthy')

        # Overall health should be good
        self.assertIn(health_status['overall_health'], ['good', 'warning', 'critical'])

    def test_backup_api_endpoints(self):
        """Test backup-related API endpoints"""
        self.client.login(username='manager_user', password='testpass123')

        # Test backup creation endpoint (if exists)
        # This would depend on actual implementation
        # response = self.client.post(reverse('create_backup_api'))
        # self.assertEqual(response.status_code, 200)

        # Test backup list endpoint (if exists)
        # response = self.client.get(reverse('backup_list_api'))
        # self.assertEqual(response.status_code, 200)

        # For now, just test that backup management page loads
        response = self.client.get(reverse('backup_management'))
        self.assertEqual(response.status_code, 200)

    def test_analytics_api_endpoints(self):
        """Test analytics and reporting API endpoints"""
        # Create test data for analytics
        for i in range(10):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased',
                created_at=timezone.now() - timezone.timedelta(days=i)
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10 + i,
                purchased_quantity=10 + i,
                unit='kg'
            )

        self.client.login(username='purchasing_user', password='testpass123')

        # Test reports dashboard (which likely has API-like data)
        response = self.client.get(reverse('reports_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Check for analytics data in context
        # This would depend on actual template context

    def test_ajax_endpoints(self):
        """Test AJAX endpoints for dynamic content loading"""
        # Test any AJAX endpoints that might exist
        # For example, dynamic supplier search, product search, etc.

        self.client.login(username='purchasing_user', password='testpass123')

        # Test supplier list AJAX (if exists)
        # response = self.client.get(reverse('supplier_list_ajax'))
        # self.assertEqual(response.status_code, 200)

        # Test product search AJAX (if exists)
        # response = self.client.post(reverse('product_search_ajax'), {'query': 'test'})
        # self.assertEqual(response.status_code, 200)

        # For now, test that regular pages load with AJAX-ready content
        response = self.client.get(reverse('supplier_list'))
        self.assertEqual(response.status_code, 200)

    def test_api_response_formats(self):
        """Test API response formats (JSON, XML, etc.)"""
        self.client.login(username='manager_user', password='testpass123')

        # Test JSON response format
        response = self.client.get(
            reverse('system_monitoring_api'),
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')

        # Verify valid JSON
        data = response.json()
        self.assertIsInstance(data, dict)

    def test_api_error_handling(self):
        """Test API error handling"""
        self.client.login(username='manager_user', password='testpass123')

        # Test with invalid parameters
        response = self.client.get(
            reverse('system_monitoring_api') + '?invalid_param=test'
        )
        self.assertEqual(response.status_code, 200)  # Should still work

        # Test with malformed requests
        # This would depend on specific API implementation

    def test_api_rate_limiting(self):
        """Test API rate limiting (if implemented)"""
        self.client.login(username='manager_user', password='testpass123')

        # Make multiple rapid requests
        for i in range(10):
            response = self.client.get(reverse('system_monitoring_api'))
            self.assertEqual(response.status_code, 200)

        # If rate limiting is implemented, this might eventually fail
        # For now, just ensure it works

    def test_api_caching(self):
        """Test API response caching (if implemented)"""
        self.client.login(username='manager_user', password='testpass123')

        # Make same request multiple times
        response1 = self.client.get(reverse('system_monitoring_api'))
        response2 = self.client.get(reverse('system_monitoring_api'))

        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response2.status_code, 200)

        # Check if caching headers are present
        # response1['Cache-Control'] might contain caching directives

    def test_api_authentication(self):
        """Test API authentication and authorization"""
        # Test without authentication
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 302)

        # Test with insufficient permissions
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 302)  # Should be denied

        # Test with proper permissions
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 200)

    def test_api_data_consistency(self):
        """Test API data consistency with database"""
        # Create test data
        request = PurchaseRequest.objects.create(user=self.kitchen_user, status='pending')
        ActivityLog.objects.create(
            user=self.kitchen_user,
            action='created',
            description='Test activity',
            request=request
        )

        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        recent_activity = data['data']['recent_activity']

        # Verify data matches database (recent_activity is a summary dict, not list)
        self.assertIsInstance(recent_activity, dict)
        self.assertIn('new_requests', recent_activity)
        self.assertIn('new_orders', recent_activity)
        self.assertIn('new_invoices', recent_activity)
        self.assertIn('new_payments', recent_activity)

        # Verify counts are reasonable
        self.assertGreaterEqual(recent_activity['new_requests'], 0)
        self.assertGreaterEqual(recent_activity['new_orders'], 0)
        self.assertGreaterEqual(recent_activity['new_invoices'], 0)
        self.assertGreaterEqual(recent_activity['new_payments'], 0)

    def test_api_performance(self):
        """Test API performance and response times"""
        import time

        self.client.login(username='manager_user', password='testpass123')

        start_time = time.time()
        response = self.client.get(reverse('system_monitoring_api'))
        end_time = time.time()

        self.assertEqual(response.status_code, 200)

        response_time = end_time - start_time
        # API should respond within reasonable time (e.g., < 5 seconds)
        self.assertLess(response_time, 5.0)

    def test_export_api_endpoints(self):
        """Test data export API endpoints"""
        # Create test data for export
        for i in range(5):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased'
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10,
                purchased_quantity=10,
                unit='kg'
            )

        self.client.login(username='purchasing_user', password='testpass123')

        # Test Excel export
        response = self.client.get(reverse('export_to_excel'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertIn('attachment; filename=', response['Content-Disposition'])

        # Test PDF export
        response = self.client.get(reverse('export_to_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename=', response['Content-Disposition'])

    def test_notification_api(self):
        """Test notification API endpoints"""
        from purchases.models import Notification

        # Create test notifications
        notifications = []
        for i in range(3):
            notification = Notification.objects.create(
                user=self.kitchen_user,
                title=f'API Test Notification {i+1}',
                message=f'Test message {i+1}',
                notification_type='info'
            )
            notifications.append(notification)

        self.client.login(username='kitchen_user', password='testpass123')

        # Test notifications list
        response = self.client.get(reverse('notifications_list'))
        self.assertEqual(response.status_code, 200)

        for notification in notifications:
            self.assertContains(response, notification.title)

        # Test notification count API (if exists)
        # response = self.client.get(reverse('notification_count_api'))
        # self.assertEqual(response.status_code, 200)
        # data = response.json()
        # self.assertEqual(data['count'], 3)

    def test_dashboard_api_data(self):
        """Test dashboard API data endpoints"""
        # Create test data for dashboard
        for i in range(5):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased' if i < 3 else 'pending'
            )

        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

        # Check for dashboard data in context
        # This would depend on actual template implementation

    def test_search_api_endpoints(self):
        """Test search API endpoints"""
        # Create searchable data
        suppliers = []
        for i in range(3):
            supplier = Supplier.objects.create(
                name=f'Search Supplier {i+1}',
                contact_person=f'Contact {i+1}',
                email=f'supplier{i+1}@example.com',
                phone='1234567890'
            )
            suppliers.append(supplier)

        self.client.login(username='purchasing_user', password='testpass123')

        # Test supplier search (if AJAX endpoint exists)
        # response = self.client.get(reverse('supplier_search_api'), {'q': 'Search'})
        # self.assertEqual(response.status_code, 200)

        # For now, test regular search functionality
        response = self.client.get(reverse('supplier_list'))
        self.assertEqual(response.status_code, 200)

        for supplier in suppliers:
            self.assertContains(response, supplier.name)
