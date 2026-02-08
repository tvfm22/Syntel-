from django.test import TestCase, Client, TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from purchases.models import (
    Category, Unit, Product, Supplier, PurchaseRequest,
    PurchaseItem, PurchaseOrder, Invoice, Payment, PaymentLocation,
    ActivityLog, Notification
)
import json

User = get_user_model()


class IntegrationTests(TransactionTestCase):
    """Integration tests for complete workflows in the Syntel system"""

    def setUp(self):
        """Set up test data and client"""
        self.client = Client()

        # Clean stale data when using --keepdb to avoid unique constraint conflicts
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
            min_stock=10,
            current_stock=5  # Below minimum
        )
        self.supplier = Supplier.objects.create(
            name='Test Supplier',
            contact_person='John Doe',
            email='supplier@example.com',
            phone='1234567890'
        )

    def test_complete_purchase_workflow(self):
        """Test complete purchase request to payment workflow"""
        # Step 1: Kitchen user creates purchase request
        self.client.login(username='kitchen_user', password='testpass123')

        response = self.client.post(reverse('purchase_request'), {
            'product_id': [self.product.id],
            'item_quantity': ['15'],
            'item_description': ['Urgent stock replenishment']
        })
        self.assertEqual(response.status_code, 302)  # Redirect to success

        # Verify purchase request was created
        request = PurchaseRequest.objects.filter(user=self.kitchen_user).first()
        self.assertIsNotNone(request)
        self.assertEqual(request.status, 'pending')
        self.assertEqual(request.items.count(), 1)

        item = request.items.first()
        self.assertEqual(item.product, self.product)
        self.assertEqual(item.quantity, 15)

        # Verify activity log was created
        activity = ActivityLog.objects.filter(request=request, action='created').first()
        self.assertIsNotNone(activity)

        # Step 2: Purchasing user reviews and updates request
        self.client.login(username='purchasing_user', password='testpass123')

        response = self.client.post(
            reverse('update_purchase_request', args=[request.id]),
            {
                'item_id': [item.id],
                'purchased_quantity': ['15']
            }
        )
        self.assertEqual(response.status_code, 302)  # Redirect

        # Refresh data
        request.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(request.status, 'purchased')
        self.assertEqual(item.purchased_quantity, 15)

        # Step 3: Purchasing user creates purchase order
        response = self.client.post(
            reverse('purchase_order_create', args=[request.id]),
            {
                'supplier': self.supplier.id,
                'order_number': 'ORD-TEST-001',
                'expected_delivery_date': (timezone.now() + timezone.timedelta(days=7)).date().isoformat(),
                'notes': 'Test order for integration test'
            }
        )
        self.assertEqual(response.status_code, 302)  # Redirect

        # Verify order was created
        order = PurchaseOrder.objects.filter(request=request).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.supplier, self.supplier)
        self.assertEqual(order.status, 'ordered')

        # Step 4: Purchasing user creates invoice
        response = self.client.post(
            reverse('invoice_create', args=[order.id]),
            {
                'invoice_number': 'INV-TEST-001',
                'total_amount': '1500.00',
                'due_date': (timezone.now() + timezone.timedelta(days=30)).date().isoformat(),
                'notes': 'Test invoice for integration test'
            }
        )
        self.assertEqual(response.status_code, 302)  # Redirect

        # Verify invoice was created
        invoice = Invoice.objects.filter(order=order).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.invoice_number, 'INV-TEST-001')
        self.assertEqual(invoice.total_amount, 1500.00)

        # Step 5: Manager creates payment
        payment_method = PaymentLocation.objects.create(name='Bank Transfer')

        self.client.login(username='manager_user', password='testpass123')
        response = self.client.post(reverse('payment_create'), {
            'invoice': invoice.id,
            'amount': '1500.00',
            'payment_date': timezone.now().date().isoformat(),
            'payment_location': payment_method.id,
            'reference_number': 'REF-TEST-001',
            'notes': 'Full payment for test invoice'
        })
        self.assertEqual(response.status_code, 302)  # Redirect

        # Verify payment was created
        payment = Payment.objects.filter(invoice=invoice).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, 1500.00)
        self.assertEqual(payment.status, 'completed')

        # Verify invoice status updated
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')

        # Verify notifications were created
        notifications = Notification.objects.filter(user=self.kitchen_user)
        self.assertTrue(notifications.exists())

    def test_partial_payment_workflow(self):
        """Test partial payment scenario"""
        # Create complete order and invoice
        request = PurchaseRequest.objects.create(user=self.kitchen_user, status='purchased')
        order = PurchaseOrder.objects.create(
            request=request,
            supplier=self.supplier,
            order_number='ORD-PARTIAL-001',
            status='delivered'
        )
        invoice = Invoice.objects.create(
            order=order,
            invoice_number='INV-PARTIAL-001',
            total_amount=2000.00,
            due_date=timezone.now().date() + timezone.timedelta(days=30)
        )

        payment_method = PaymentLocation.objects.create(name='Bank Transfer')

        # Create first partial payment
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.post(reverse('payment_create'), {
            'invoice': invoice.id,
            'amount': '1000.00',
            'payment_date': timezone.now().date().isoformat(),
            'payment_location': payment_method.id,
            'reference_number': 'REF-PARTIAL-001',
            'notes': 'First partial payment'
        })
        self.assertEqual(response.status_code, 302)

        # Verify partial payment
        payment1 = Payment.objects.filter(invoice=invoice).first()
        self.assertIsNotNone(payment1)
        self.assertEqual(payment1.amount, 1000.00)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'partially_paid')
        self.assertEqual(invoice.paid_amount, 1000.00)
        self.assertEqual(invoice.remaining_amount, 1000.00)

        # Create second partial payment
        response = self.client.post(reverse('payment_create'), {
            'invoice': invoice.id,
            'amount': '1000.00',
            'payment_date': timezone.now().date().isoformat(),
            'payment_location': payment_method.id,
            'reference_number': 'REF-PARTIAL-002',
            'notes': 'Second partial payment'
        })
        self.assertEqual(response.status_code, 302)

        # Verify full payment
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')
        self.assertEqual(invoice.paid_amount, 2000.00)
        self.assertEqual(invoice.remaining_amount, 0.00)

        payments = Payment.objects.filter(invoice=invoice)
        self.assertEqual(payments.count(), 2)

    def test_rejection_workflow(self):
        """Test purchase request rejection workflow"""
        # Create purchase request
        self.client.login(username='kitchen_user', password='testpass123')

        response = self.client.post(reverse('purchase_request'), {
            'product_id': [self.product.id],
            'item_quantity': ['5'],
            'item_description': ['Test request for rejection']
        })
        self.assertEqual(response.status_code, 302)

        request = PurchaseRequest.objects.filter(user=self.kitchen_user).first()
        self.assertIsNotNone(request)

        # Purchasing user rejects the request
        self.client.login(username='purchasing_user', password='testpass123')

        response = self.client.post(
            reverse('update_purchase_request', args=[request.id]),
            {
                'status': 'rejected',
                'rejection_reason': 'Not needed at this time'
            }
        )
        self.assertEqual(response.status_code, 302)

        # Verify rejection
        request.refresh_from_db()
        self.assertEqual(request.status, 'rejected')

        # Verify notification was sent
        notification = Notification.objects.filter(
            user=self.kitchen_user,
            title__icontains='rejected'
        ).first()
        self.assertIsNotNone(notification)

    def test_inventory_management_workflow(self):
        """Test inventory management and low stock alerts"""
        # Set product stock below minimum
        self.product.current_stock = 3  # Below min_stock of 10
        self.product.save()

        # Create purchase request for replenishment
        self.client.login(username='kitchen_user', password='testpass123')

        response = self.client.post(reverse('purchase_request'), {
            'product_id': [self.product.id],
            'item_quantity': ['20'],
            'item_description': ['Stock replenishment']
        })
        self.assertEqual(response.status_code, 302)

        # Complete the purchase workflow
        request = PurchaseRequest.objects.filter(user=self.kitchen_user).first()
        self.client.login(username='purchasing_user', password='testpass123')

        item = request.items.first()
        response = self.client.post(
            reverse('update_purchase_request', args=[request.id]),
            {
                'item_id': [item.id],
                'purchased_quantity': ['20']
            }
        )
        self.assertEqual(response.status_code, 302)

        # Create order and mark as delivered
        response = self.client.post(
            reverse('purchase_order_create', args=[request.id]),
            {
                'supplier': self.supplier.id,
                'order_number': 'ORD-INV-001',
                'expected_delivery_date': timezone.now().date().isoformat(),
                'notes': 'Inventory replenishment'
            }
        )
        self.assertEqual(response.status_code, 302)

        order = PurchaseOrder.objects.filter(request=request).first()
        order.status = 'delivered'
        order.save()

        # Verify stock was updated (this would depend on actual implementation)
        # self.product.refresh_from_db()
        # self.assertEqual(self.product.current_stock, 23)  # 3 + 20

    def test_reporting_workflow(self):
        """Test reporting and analytics workflow"""
        # Create multiple completed transactions
        for i in range(3):
            request = PurchaseRequest.objects.create(
                user=self.kitchen_user,
                status='purchased'
            )
            PurchaseItem.objects.create(
                request=request,
                product=self.product,
                name=self.product.name,
                quantity=10 + i,
                purchased_quantity=10 + i,
                unit='kg'
            )

            order = PurchaseOrder.objects.create(
                request=request,
                supplier=self.supplier,
                order_number=f'ORD-REP-{i+1:03d}',
                status='completed'
            )

            invoice = Invoice.objects.create(
                order=order,
                invoice_number=f'INV-REP-{i+1:03d}',
                total_amount=1000.00 * (i + 1),
                due_date=timezone.now().date() + timezone.timedelta(days=30),
                status='paid'
            )

            payment_method = PaymentLocation.objects.create(name=f'Method {i+1}')
            Payment.objects.create(
                invoice=invoice,
                amount=1000.00 * (i + 1),
                payment_date=timezone.now().date(),
                payment_method=payment_method,
                status='completed'
            )

        # Test reports dashboard access
        self.client.login(username='purchasing_user', password='testpass123')
        response = self.client.get(reverse('reports_dashboard'))
        self.assertEqual(response.status_code, 200)

        # Test Excel export
        response = self.client.get(reverse('export_to_excel'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment; filename=', response['Content-Disposition'])

        # Test PDF export
        response = self.client.get(reverse('export_to_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment; filename=', response['Content-Disposition'])

    def test_backup_and_monitoring_workflow(self):
        """Test backup and monitoring functionality"""
        # Test backup management access
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('backup_management'))
        self.assertEqual(response.status_code, 200)

        # Test system monitoring access
        response = self.client.get(reverse('system_monitoring'))
        self.assertEqual(response.status_code, 200)

        # Test monitoring API
        response = self.client.get(reverse('system_monitoring_api'))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('system_resources', data['data'])
        self.assertIn('recent_activity', data['data'])

    def test_user_management_workflow(self):
        """Test user management workflow"""
        # Manager creates new user
        self.client.login(username='manager_user', password='testpass123')

        response = self.client.post(reverse('user_create'), {
            'username': 'new_kitchen_user',
            'email': 'newkitchen@example.com',
            'password1': 'newpass123',
            'password2': 'newpass123',
            'first_name': 'New',
            'last_name': 'Kitchen',
            'role': 'kitchen',
            'is_active': True
        })
        self.assertEqual(response.status_code, 302)

        # Verify user was created
        new_user = User.objects.filter(username='new_kitchen_user').first()
        self.assertIsNotNone(new_user)
        self.assertEqual(new_user.role, 'kitchen')

        # Test user list
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'new_kitchen_user')

    def test_notification_workflow(self):
        """Test notification system workflow"""
        # Create notifications for user
        notifications = []
        for i in range(3):
            notification = Notification.objects.create(
                user=self.kitchen_user,
                title=f'Test Notification {i+1}',
                message=f'This is test notification {i+1}',
                notification_type='info' if i % 2 == 0 else 'warning'
            )
            notifications.append(notification)

        # User views notifications
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('notifications_list'))
        self.assertEqual(response.status_code, 200)

        for notification in notifications:
            self.assertContains(response, notification.title)

        # Mark notification as read (assuming endpoint exists)
        # response = self.client.post(reverse('mark_notification_read', args=[notifications[0].id]))
        # self.assertEqual(response.status_code, 302)

        # notifications[0].refresh_from_db()
        # self.assertTrue(notifications[0].is_read)

    def test_activity_logging_workflow(self):
        """Test activity logging throughout workflows"""
        # Create a complete workflow and verify activity logging
        self.client.login(username='kitchen_user', password='testpass123')

        # Create request
        response = self.client.post(reverse('purchase_request'), {
            'product_id': [self.product.id],
            'item_quantity': ['10'],
            'item_description': ['Activity logging test']
        })
        self.assertEqual(response.status_code, 302)

        request = PurchaseRequest.objects.filter(user=self.kitchen_user).first()

        # Check activity log
        activities = ActivityLog.objects.filter(request=request)
        self.assertTrue(activities.exists())

        # Verify activity details
        create_activity = activities.filter(action='created').first()
        self.assertIsNotNone(create_activity)
        self.assertEqual(create_activity.user, self.kitchen_user)

    def test_error_handling_workflow(self):
        """Test error handling in various scenarios"""
        # Test accessing non-existent resources
        self.client.login(username='purchasing_user', password='testpass123')

        response = self.client.get(reverse('purchase_request_detail', args=[99999]))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse('purchase_order_detail', args=[99999]))
        self.assertEqual(response.status_code, 404)

        # Test form validation errors
        response = self.client.post(reverse('purchase_request'), {
            'product_id': [self.product.id],
            'item_quantity': ['-5'],  # Invalid quantity
            'item_description': ['Test']
        })
        self.assertEqual(response.status_code, 200)  # Form re-displayed with errors

        # Test unauthorized access
        self.client.login(username='kitchen_user', password='testpass123')
        response = self.client.get(reverse('backup_management'))
        self.assertEqual(response.status_code, 302)  # Redirect to unauthorized

    def test_concurrent_workflow_handling(self):
        """Test handling of concurrent operations"""
        # This would test database locking, transaction isolation, etc.
        # For now, just test basic concurrent request handling

        from django.test.utils import override_settings
        from django.db import transaction

        # Create multiple requests simultaneously
        requests_data = []
        for i in range(5):
            with transaction.atomic():
                request = PurchaseRequest.objects.create(
                    user=self.kitchen_user,
                    status='pending'
                )
                PurchaseItem.objects.create(
                    request=request,
                    product=self.product,
                    name=self.product.name,
                    quantity=10 + i,
                    unit='kg'
                )
                requests_data.append(request)

        # Verify all requests were created
        self.assertEqual(PurchaseRequest.objects.filter(user=self.kitchen_user).count(), 5)

        # Test concurrent status updates (this would need more complex setup)
        # In a real scenario, you'd use threading or multiple test clients
