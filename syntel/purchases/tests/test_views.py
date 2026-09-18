from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from purchases.models import (
    Cafe, Category, Unit, Product, Supplier, PurchaseRequest,
    PurchaseItem, PurchaseOrder, Invoice, Payment, PaymentLocation
)

# همهٔ URLهای داخل کافه با cafe_code پیشوند می‌شوند
CAFE_CODE = 'tc'

User = get_user_model()


class ViewTests(TestCase):
    """Test cases for all views in the Syntel system"""

    def setUp(self):
        """Set up test data and client"""
        self.client = Client()

        # Clean any leftovers when running with --keepdb to avoid unique violations
        User.objects.filter(username__in=['kitchen_user', 'purchasing_user', 'manager_user']).delete()
        Category.objects.filter(name='Test Category').delete()
        Unit.objects.filter(name='Kilogram').delete()
        Product.objects.filter(name='Test Product').delete()
        Supplier.objects.filter(name='Test Supplier').delete()

        # کافه: همهٔ کاربران و داده‌ها به یک کافه وصل‌اند (URLهای کافه‌محور)
        self.cafe = Cafe.objects.create(name='Test Cafe', code=CAFE_CODE, is_active=True)

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

    def test_login_view(self):
        """Test login functionality"""
        # Test GET request
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'login.html')

        # Test successful login
        response = self.client.post(reverse('login'), {
            'username': 'kitchen_user',
            'password': 'testpass123'
        })
        self.assertEqual(response.status_code, 302)  # Redirect after login

        # Test failed login
        response = self.client.post(reverse('login'), {
            'username': 'kitchen_user',
            'password': 'wrongpassword'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'نام کاربری یا رمز عبور اشتباه است')

    def test_logout_view(self):
        """Test logout functionality"""
        # Login first
        self.client.login(username='kitchen_user', password='testpass123')

        # Test logout
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 302)  # Redirect after logout

    def test_dashboard_view(self):
        """Test dashboard view for different user roles"""
        # Test kitchen user dashboard
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('dashboard', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard.html')
        self.assertContains(response, 'kitchen_user')

        # Test purchasing user dashboard
        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('dashboard', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)

        # Test manager dashboard — مدیر به داشبورد مالی هدایت میشود
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('dashboard', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('finance_dashboard', kwargs={'cafe_code': CAFE_CODE}))

    def test_unauthorized_access(self):
        """Test that unauthorized users cannot access restricted views"""
        # Test purchasing view without login
        response = self.client.get(reverse('purchase_requests_list', kwargs={'cafe_code': 'main'}))
        self.assertEqual(response.status_code, 302)  # Redirect to login

        # The cafe middleware needs an existing cafe + selected cafe in session
        cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.kitchen_user.cafe = cafe
        self.kitchen_user.save()
        session = self.client.session
        session['selected_cafe_id'] = cafe.id
        session['selected_cafe_code'] = cafe.code
        session.save()

        # Test manager view with kitchen user
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('backup_management', kwargs={'cafe_code': 'main'}))
        self.assertEqual(response.status_code, 302)  # Access denied

    def test_purchase_request_form(self):
        """Test purchase request form view"""
        self.client.login(username='kitchen_user', password='testpass123')

        # Test GET request
        response = self.client.get(reverse('purchase_request', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'purchase_request_form.html')

        # Test POST request with valid data (فرم با نام آزاد کالا کار میکند)
        response = self.client.post(reverse('purchase_request', kwargs={'cafe_code': CAFE_CODE}), {
            'product_name': [self.product.name],
            'item_quantity': ['5'],
            'item_description': ['Test description']
        })
        self.assertEqual(response.status_code, 302)  # Redirect to success page

        # Verify purchase request was created
        request = PurchaseRequest.objects.filter(user=self.kitchen_user).first()
        self.assertIsNotNone(request)
        self.assertEqual(request.items.count(), 1)

    def test_purchase_requests_list(self):
        """Test purchase requests list view"""
        # Create test data
        request = PurchaseRequest.objects.create(user=self.kitchen_user, status='pending', cafe=self.cafe)
        PurchaseItem.objects.create(
            request=request,
            product=self.product,
            name=self.product.name,
            quantity=5,
            unit='kg'
        )

        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('purchase_requests_list', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'purchase_requests_list.html')
        self.assertContains(response, 'pending')

    def test_purchase_request_update(self):
        """Test purchase request update functionality"""
        # Create test data
        request = PurchaseRequest.objects.create(user=self.kitchen_user, status='pending', cafe=self.cafe)
        item = PurchaseItem.objects.create(
            request=request,
            product=self.product,
            name=self.product.name,
            quantity=10,
            unit='kg'
        )

        self.client.login(username='purchasing_user', password='testpass123')

        # Test update with purchased quantities
        response = self.client.post(
            reverse('update_purchase_request',
                    kwargs={'cafe_code': CAFE_CODE, 'request_id': request.id}),
            {
                'item_id': [item.id],
                'purchased_quantity': ['8']
            }
        )
        self.assertEqual(response.status_code, 302)  # Redirect

        # Refresh from database
        item.refresh_from_db()
        self.assertEqual(item.purchased_quantity, 8.0)

    def test_reports_dashboard(self):
        """Test reports dashboard view"""
        # Create test data
        request = PurchaseRequest.objects.create(user=self.kitchen_user, status='purchased', cafe=self.cafe)
        PurchaseItem.objects.create(
            request=request,
            product=self.product,
            name=self.product.name,
            quantity=10,
            unit='kg',
            purchased_quantity=10
        )

        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('reports_dashboard', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'reports_dashboard.html')

    def test_excel_export(self):
        """Test Excel export functionality"""
        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('export_to_excel', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertIn('attachment; filename=', response['Content-Disposition'])

    def test_pdf_export(self):
        """Test PDF export functionality"""
        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('export_to_pdf', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename=', response['Content-Disposition'])

    def test_purchase_order_creation(self):
        """Test purchase order creation"""
        # Create approved request
        request = PurchaseRequest.objects.create(user=self.kitchen_user, status='approved', cafe=self.cafe)
        PurchaseItem.objects.create(
            request=request,
            product=self.product,
            name=self.product.name,
            quantity=5,
            unit='kg'
        )

        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.post(
            reverse('purchase_order_create',
                    kwargs={'cafe_code': CAFE_CODE, 'request_id': request.id}),
            {
                'supplier': self.supplier.id,
                'order_number': 'ORD-TEST-001',
                'notes': 'Test order'
            }
        )
        self.assertEqual(response.status_code, 302)  # Redirect

        # Verify order was created
        order = PurchaseOrder.objects.filter(request=request).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.supplier, self.supplier)

    def test_invoice_creation(self):
        """Test invoice creation"""
        # Create completed order
        request = PurchaseRequest.objects.create(user=self.kitchen_user, cafe=self.cafe)
        order = PurchaseOrder.objects.create(
            request=request,
            supplier=self.supplier,
            order_number='ORD-001',
            status='completed'
        )

        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.post(
            reverse('invoice_create',
                    kwargs={'cafe_code': CAFE_CODE, 'order_id': order.id}),
            {
                'invoice_number': 'INV-TEST-001',
                'total_amount': '1000.00',
                'due_date': '2025-12-31',
                'notes': 'Test invoice'
            }
        )
        self.assertEqual(response.status_code, 302)  # Redirect

        # Verify invoice was created
        invoice = Invoice.objects.filter(order=order).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.invoice_number, 'INV-TEST-001')

    def test_payment_creation(self):
        """Test payment creation"""
        # Create invoice
        request = PurchaseRequest.objects.create(user=self.kitchen_user, cafe=self.cafe)
        order = PurchaseOrder.objects.create(
            request=request,
            supplier=self.supplier,
            order_number='ORD-001'
        )
        invoice = Invoice.objects.create(
            order=order,
            invoice_number='INV-001',
            total_amount=1000.00,
            due_date=timezone.now().date() + timezone.timedelta(days=30),
            cafe=self.cafe,
        )

        payment_method = PaymentLocation.objects.create(name='Bank Transfer', cafe=self.cafe)

        self.client.login(username='manager_user', password='testpass123')
        response = self.client.post(reverse('payment_create', kwargs={'cafe_code': CAFE_CODE}), {
            'invoice': invoice.id,
            'amount': '500.00',
            'payment_date': timezone.now().date().isoformat(),
            'payment_location': payment_method.id,
            'reference_number': 'REF-001',
            'notes': 'Test payment'
        })
        self.assertEqual(response.status_code, 302)  # Redirect

        # Verify payment was created
        payment = Payment.objects.filter(invoice=invoice).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, 500.00)

    def test_backup_management_access(self):
        """Test backup management access control"""
        # The cafe middleware requires an existing cafe for cafe-scoped URLs
        cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.kitchen_user.cafe = cafe  # kitchen user belongs to this cafe
        self.kitchen_user.save()
        self.manager_user.cafe = cafe  # و مدیر هم — URL با cafe_code این کافه است
        self.manager_user.save()
        session = self.client.session
        session['selected_cafe_id'] = cafe.id
        session['selected_cafe_code'] = cafe.code
        session.save()

        # Test unauthorized access
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('backup_management', kwargs={'cafe_code': 'main'}))
        self.assertEqual(response.status_code, 302)  # Access denied

        # Test authorized access
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('backup_management', kwargs={'cafe_code': 'main'}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'backup_management.html')

    def test_system_monitoring_access(self):
        """Test system monitoring access control"""
        # Test unauthorized access
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 302)  # Access denied

        # Test authorized access
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'system_monitoring.html')

    def test_system_monitoring_api(self):
        """Test system monitoring API"""
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('system_monitoring_api', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('system_resources', data['data'])
        self.assertIn('recent_activity', data['data'])
        self.assertIn('health_status', data['data'])

    def test_finance_dashboard(self):
        """Test finance dashboard view"""
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('finance_dashboard', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'finance_dashboard.html')

    def test_supplier_list(self):
        """Test supplier list view"""
        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('supplier_list', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'supplier_list.html')
        self.assertContains(response, self.supplier.name)

    def test_product_list(self):
        """Test product list view"""
        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('product_list', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'product_list.html')
        self.assertContains(response, self.product.name)

    def test_user_management(self):
        """Test user management views"""
        self.client.login(username='manager_user', password='testpass123')

        # Test user list
        response = self.client.get(reverse('user_list', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'user_list.html')

        # Test user creation form
        response = self.client.get(reverse('user_create', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'user_form.html')

    def test_notifications(self):
        """Test notification system"""
        from purchases.models import Notification

        # Create test notification
        notification = Notification.objects.create(
            user=self.kitchen_user,
            title='Test Notification',
            message='This is a test',
            notification_type='info'
        )

        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('notifications_list', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'notifications_list.html')
        self.assertContains(response, 'Test Notification')

    def test_activity_log(self):
        """Test activity log functionality"""
        from purchases.models import ActivityLog

        # Create test activity
        request = PurchaseRequest.objects.create(user=self.kitchen_user, cafe=self.cafe)
        activity = ActivityLog.objects.create(
            user=self.kitchen_user,
            action='created',
            description='Test activity',
            request=request
        )
        self.assertEqual(activity.request_id, request.id)
        self.assertEqual(ActivityLog.objects.filter(request=request).count(), 1)

        # صفحهٔ مستقلی برای لاگ فعالیت وجود ندارد؛ آخرین فعالیتها از API
        # پایش سیستم (فقط مدیر) خوانده میشوند
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(
            reverse('system_monitoring_api', kwargs={'cafe_code': CAFE_CODE}))
        self.assertEqual(response.status_code, 200)

    def test_error_handling(self):
        """Test error handling in views"""
        # Test accessing non-existent objects
        self.client.login(username='purchasing_user', password='testpass123')

        response = self.client.get(reverse(
            'purchase_request_detail', kwargs={'cafe_code': CAFE_CODE, 'request_id': 99999}))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse(
            'purchase_order_detail', kwargs={'cafe_code': CAFE_CODE, 'order_id': 99999}))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse(
            'invoice_detail', kwargs={'cafe_code': CAFE_CODE, 'invoice_id': 99999}))
        self.assertEqual(response.status_code, 404)
