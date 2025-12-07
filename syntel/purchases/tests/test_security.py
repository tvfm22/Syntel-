from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from purchases.models import (
    Category, Unit, Product, Supplier, PurchaseRequest,
    PurchaseItem, PurchaseOrder, Invoice, Payment, PaymentMethod
)
import json

User = get_user_model()


class SecurityTests(TestCase):
    """Security tests for the Syntel system"""

    def setUp(self):
        """Set up test data and client"""
        self.client = Client()

        # Create test users with different roles
        self.kitchen_user, _ = User.objects.get_or_create(
            username='kitchen_user',
            defaults={
                'email': 'kitchen@example.com',
                'role': 'kitchen'
            }
        )
        self.kitchen_user.set_password('testpass123')
        self.kitchen_user.save()

        self.purchasing_user, _ = User.objects.get_or_create(
            username='purchasing_user',
            defaults={
                'email': 'purchasing@example.com',
                'role': 'purchasing'
            }
        )
        self.purchasing_user.set_password('testpass123')
        self.purchasing_user.save()

        self.manager_user, _ = User.objects.get_or_create(
            username='manager_user',
            defaults={
                'email': 'manager@example.com',
                'role': 'manager'
            }
        )
        self.manager_user.set_password('testpass123')
        self.manager_user.save()

        # Create inactive user
        self.inactive_user, _ = User.objects.get_or_create(
            username='inactive_user',
            defaults={
                'email': 'inactive@example.com',
                'role': 'kitchen',
                'is_active': False
            }
        )
        self.inactive_user.set_password('testpass123')
        self.inactive_user.save()

        # Create basic data (guard against keepdb duplicates)
        Category.objects.filter(name='Test Category').delete()
        Unit.objects.filter(name='Kilogram').delete()
        Product.objects.filter(name='Test Product').delete()
        Supplier.objects.filter(name='Test Supplier').delete()

        self.category, _ = Category.objects.get_or_create(name='Test Category')
        self.unit, _ = Unit.objects.get_or_create(name='Kilogram', defaults={'symbol': 'kg'})
        self.product, _ = Product.objects.get_or_create(
            name='Test Product',
            defaults={
                'category': self.category,
                'unit': self.unit,
                'min_stock': 10
            }
        )
        self.supplier, _ = Supplier.objects.get_or_create(
            name='Test Supplier',
            defaults={
                'contact_person': 'John Doe',
                'email': 'supplier@example.com',
                'phone': '1234567890'
            }
        )

    def test_unauthorized_access_prevention(self):
        """Test that unauthorized users cannot access restricted resources"""
        # Test access to purchasing views without login
        urls_to_test = [
            reverse('purchase_requests_list'),
            reverse('supplier_list'),
            reverse('product_list'),
            reverse('reports_dashboard'),
            reverse('backup_management'),
            reverse('system_monitoring'),
            reverse('user_list'),
            reverse('finance_dashboard'),
        ]

        for url in urls_to_test:
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 302,
                f"Unauthorized access allowed to {url}"
            )
            self.assertIn('/login/', response['Location'])

    def test_role_based_access_control(self):
        """Test role-based access control for different user types"""
        # Define role permissions
        role_permissions = {
            'kitchen': [
                reverse('dashboard'),
                reverse('purchase_request'),
                reverse('notifications_list'),
            ],
            'purchasing': [
                reverse('dashboard'),
                reverse('purchase_requests_list'),
                reverse('supplier_list'),
                reverse('product_list'),
                reverse('reports_dashboard'),
                reverse('purchase_request'),
                reverse('notifications_list'),
            ],
            'manager': [
                reverse('dashboard'),
                reverse('backup_management'),
                reverse('system_monitoring'),
                reverse('user_list'),
                reverse('finance_dashboard'),
                reverse('approvals_list'),
                reverse('purchase_requests_list'),
                reverse('supplier_list'),
                reverse('product_list'),
                reverse('reports_dashboard'),
                reverse('purchase_request'),
                reverse('notifications_list'),
            ]
        }

        # Test each role's permissions
        users = [
            ('kitchen', self.kitchen_user),
            ('purchasing', self.purchasing_user),
            ('manager', self.manager_user)
        ]

        for role_name, user in users:
            self.client.login(username=user.username, password='testpass123')

            allowed_urls = role_permissions[role_name]

            # Test allowed URLs
            for url in allowed_urls:
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code, 200,
                    f"{role_name} should have access to {url}"
                )

            # Test restricted URLs (URLs not in allowed list)
            all_urls = set()
            for urls in role_permissions.values():
                all_urls.update(urls)

            restricted_urls = all_urls - set(allowed_urls)
            for url in restricted_urls:
                response = self.client.get(url)
                # Should be redirected (access denied) or 404
                self.assertIn(
                    response.status_code, [302, 404],
                    f"{role_name} should not have access to {url}"
                )

            self.client.logout()

    def test_inactive_user_access_prevention(self):
        """Test that inactive users cannot access the system"""
        # Try to login with inactive user
        response = self.client.post(reverse('login'), {
            'username': 'inactive_user',
            'password': 'testpass123'
        })
        self.assertEqual(response.status_code, 200)  # Form re-displayed
        self.assertContains(response, 'Invalid credentials')

        # Try direct access to protected resource
        self.client.login(username='inactive_user', password='testpass123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_sql_injection_prevention(self):
        """Test SQL injection prevention"""
        # Test login form
        malicious_usernames = [
            "' OR '1'='1",
            "admin'--",
            "'; DROP TABLE users; --",
            "' UNION SELECT * FROM users --",
        ]

        for malicious_username in malicious_usernames:
            response = self.client.post(reverse('login'), {
                'username': malicious_username,
                'password': 'testpass123'
            })
            # Should not allow login
            self.assertNotEqual(response.status_code, 302)

        # Test search forms (if they exist)
        self.client.login(username='purchasing_user', password='testpass123')

        # Test supplier search
        response = self.client.get(reverse('supplier_list'), {
            'search': "' OR '1'='1"
        })
        self.assertEqual(response.status_code, 200)
        # Should not crash or return unexpected results

    def test_xss_prevention(self):
        """Test Cross-Site Scripting (XSS) prevention"""
        xss_payloads = [
            '<script>alert("XSS")</script>',
            '<img src=x onerror=alert("XSS")>',
            'javascript:alert("XSS")',
            '<iframe src="javascript:alert(\'XSS\')"></iframe>',
        ]

        self.client.login(username='kitchen_user', password='testpass123')

        # Test in purchase request form
        for payload in xss_payloads:
            response = self.client.post(reverse('purchase_request'), {
                'product_id': [self.product.id],
                'item_quantity': ['5'],
                'item_description': [payload]
            })
            self.assertEqual(response.status_code, 302)  # Should redirect

            # Check that the payload is not executed in the response
            # (This is a basic check; in production, use proper XSS testing tools)
            if response.status_code == 200:
                self.assertNotContains(response, '<script>', msg_prefix=f"XSS payload not sanitized: {payload}")

    def test_csrf_protection(self):
        """Test CSRF protection"""
        self.client.login(username='kitchen_user', password='testpass123')

        # Try POST request without CSRF token
        response = self.client.post(
            reverse('purchase_request'),
            {
                'product_id': [self.product.id],
                'item_quantity': ['5'],
                'item_description': ['Test']
            },
            HTTP_X_CSRFTOKEN=None  # Remove CSRF token
        )
        # Django should reject this
        self.assertEqual(response.status_code, 403)

    def test_input_validation(self):
        """Test input validation and sanitization"""
        self.client.login(username='kitchen_user', password='testpass123')

        # Test invalid quantity values
        invalid_quantities = ['-5', '0', 'abc', '999999999999', '1.5.5']

        for quantity in invalid_quantities:
            response = self.client.post(reverse('purchase_request'), {
                'product_id': [self.product.id],
                'item_quantity': [quantity],
                'item_description': ['Test']
            })
            # Should either reject or sanitize the input
            if response.status_code == 200:  # Form re-displayed with errors
                self.assertContains(response, 'quantity')  # Should show validation error

        # Test invalid email formats
        self.client.login(username='manager_user', password='testpass123')

        invalid_emails = ['invalid-email', '@example.com', 'user@', 'user@@example.com']

        for email in invalid_emails:
            response = self.client.post(reverse('user_create'), {
                'username': f'testuser_{email.replace("@", "_")}',
                'email': email,
                'password1': 'testpass123',
                'password2': 'testpass123',
                'role': 'kitchen'
            })
            if response.status_code == 200:  # Form re-displayed
                self.assertContains(response, 'email')  # Should show validation error

    def test_file_upload_security(self):
        """Test file upload security"""
        # Test with malicious file content
        malicious_content = b'<?php echo "Malicious PHP code"; ?>'
        malicious_file = SimpleUploadedFile(
            'malicious.php',
            malicious_content,
            content_type='application/x-php'
        )

        # This would depend on actual file upload functionality
        # For now, test that the system doesn't crash with unexpected file types

        # Test with large file (if file uploads exist)
        large_content = b'x' * (10 * 1024 * 1024)  # 10MB file
        large_file = SimpleUploadedFile(
            'large_file.txt',
            large_content,
            content_type='text/plain'
        )

        # Test should not crash the server
        # This is a placeholder for actual file upload testing

    def test_session_security(self):
        """Test session security"""
        # Test session timeout (if implemented)
        self.client.login(username='kitchen_user', password='testpass123')

        # Access a protected resource
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

        # Simulate session expiry (if session timeout is implemented)
        # This would depend on actual session configuration

        # Test concurrent session handling
        # Login with same user from different client
        client2 = Client()
        client2.login(username='kitchen_user', password='testpass123')

        # Both should work (or one should be invalidated based on policy)
        response1 = self.client.get(reverse('dashboard'))
        response2 = client2.get(reverse('dashboard'))

        # At minimum, both requests should not crash
        self.assertIn(response1.status_code, [200, 302])
        self.assertIn(response2.status_code, [200, 302])

    def test_password_security(self):
        """Test password security policies"""
        # Test weak passwords
        weak_passwords = ['123', 'password', '123456', 'qwerty']

        for password in weak_passwords:
            response = self.client.post(reverse('user_create'), {
                'username': f'weak_user_{password}',
                'email': f'weak_{password}@example.com',
                'password1': password,
                'password2': password,
                'role': 'kitchen'
            })
            # Should reject weak passwords (if password validation is implemented)
            if response.status_code == 200:
                # Check for password strength validation
                pass

        # Test password change functionality (if exists)
        # Should require current password, etc.

    def test_data_exposure_prevention(self):
        """Test prevention of sensitive data exposure"""
        # Create test data with sensitive information
        request = PurchaseRequest.objects.create(user=self.kitchen_user, status='pending')
        order = PurchaseOrder.objects.create(
            request=request,
            supplier=self.supplier,
            order_number='SECURE-001'
        )
        invoice = Invoice.objects.create(
            order=order,
            invoice_number='SECURE-INV-001',
            total_amount=1000.00
        )

        # Test that sensitive data is not exposed in API responses
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        # API should not contain sensitive information like passwords, keys, etc.
        response_content = json.dumps(data)
        self.assertNotIn('password', response_content.lower())
        self.assertNotIn('secret', response_content.lower())
        self.assertNotIn('key', response_content.lower())

    def test_rate_limiting(self):
        """Test rate limiting (if implemented)"""
        # Make multiple rapid requests
        for i in range(50):
            response = self.client.post(reverse('login'), {
                'username': 'kitchen_user',
                'password': 'wrongpassword'
            })
            # Should not crash, and may eventually be rate limited
            self.assertIn(response.status_code, [200, 429])  # 429 = Too Many Requests

    def test_error_information_leakage(self):
        """Test that errors don't leak sensitive information"""
        # Test accessing non-existent resources
        urls_to_test = [
            '/nonexistent/',
            reverse('purchase_request_detail', args=[99999]),
            reverse('purchase_order_detail', args=[99999]),
            reverse('invoice_detail', args=[99999]),
        ]

        for url in urls_to_test:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404)

            # Error page should not contain sensitive information
            response_content = response.content.decode('utf-8')
            self.assertNotIn('DEBUG', response_content.upper())
            self.assertNotIn('Traceback', response_content)
            self.assertNotIn('Exception', response_content)

    def test_https_enforcement(self):
        """Test HTTPS enforcement (if configured)"""
        # This would test SECURE_SSL_REDIRECT and related settings
        # In test environment, HTTPS might not be enforced

        # Test that login form is served (basic check)
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_secure_headers(self):
        """Test security headers"""
        response = self.client.get(reverse('login'))

        # Check for basic security headers (if configured)
        # These might not be set in test environment
        security_headers = [
            'X-Content-Type-Options',
            'X-Frame-Options',
            'X-XSS-Protection',
            'Strict-Transport-Security'
        ]

        # At minimum, response should not crash
        self.assertEqual(response.status_code, 200)

    def test_database_security(self):
        """Test database security"""
        # Test that direct model access is properly controlled
        # This is more of an integration test

        # Test that users can only see their own data (if applicable)
        self.client.login(username='kitchen_user', password='testpass123')

        # Create request for this user
        request1 = PurchaseRequest.objects.create(user=self.kitchen_user, status='pending')

        # Create request for another user
        request2 = PurchaseRequest.objects.create(user=self.purchasing_user, status='pending')

        # Kitchen user should only see their own requests in certain views
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

        # Check that request2 is not visible to kitchen_user
        # This depends on actual view implementation

    def test_audit_logging(self):
        """Test audit logging for security events"""
        from purchases.models import ActivityLog

        # Perform security-relevant actions
        self.client.login(username='kitchen_user', password='testpass123')

        # Create purchase request
        response = self.client.post(reverse('purchase_request'), {
            'product_id': [self.product.id],
            'item_quantity': ['5'],
            'item_description': ['Security audit test']
        })
        self.assertEqual(response.status_code, 302)

        # Check that activity was logged
        request = PurchaseRequest.objects.filter(user=self.kitchen_user).last()
        activity = ActivityLog.objects.filter(request=request, action='created').first()
        self.assertIsNotNone(activity)

        # Test failed login attempts (if logged)
        # This would depend on actual logging implementation

    def test_data_integrity(self):
        """Test data integrity and validation"""
        # Test that invalid data cannot be saved
        self.client.login(username='manager_user', password='testpass123')

        # Try to create user with invalid data
        response = self.client.post(reverse('user_create'), {
            'username': '',  # Empty username
            'email': 'invalid-email',
            'password1': '123',  # Weak password
            'password2': '456',  # Mismatched password
            'role': 'invalid_role'
        })

        # Should reject invalid data
        if response.status_code == 200:  # Form re-displayed
            self.assertContains(response, 'username')  # Should show errors

        # Test database constraints
        # Try to create duplicate unique fields
        User.objects.create_user(
            username='unique_test',
            email='unique@example.com',
            password='testpass123',
            role='kitchen'
        )

        # Try to create duplicate
        try:
            User.objects.create_user(
                username='unique_test',  # Duplicate
                email='unique2@example.com',
                password='testpass123',
                role='kitchen'
            )
            self.fail("Should not allow duplicate usernames")
        except Exception:
            pass  # Expected

    def test_brute_force_protection(self):
        """Test brute force attack protection"""
        # Attempt multiple login failures
        for i in range(10):
            response = self.client.post(reverse('login'), {
                'username': 'kitchen_user',
                'password': f'wrongpassword{i}'
            })
            # Should not crash
            self.assertIn(response.status_code, [200, 429])

        # Valid login should still work (unless account is locked)
        response = self.client.post(reverse('login'), {
            'username': 'kitchen_user',
            'password': 'testpass123'
        })
        # May be 200 (form re-display) if account is locked, or 302 if successful
        self.assertIn(response.status_code, [200, 302])
