from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from purchases.models import (
    Category, Unit, Product, PaymentType, PaymentLocation,
    Supplier, SupplierEvaluation, PurchaseRequest, PurchaseItem,
    PurchaseOrder, PurchaseOrderItem, Invoice, InvoiceItem,
    Payment, ActivityLog, Notification
)

User = get_user_model()


class ModelTests(TestCase):
    """Test cases for all models in the Syntel system"""

    def setUp(self):
        """Set up test data"""
        User.objects.filter(username='testuser').delete()
        Category.objects.filter(name='Test Category').delete()
        Unit.objects.filter(name='Kilogram').delete()
        Product.objects.filter(name='Test Product').delete()
        Supplier.objects.filter(name='Test Supplier').delete()

        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
            role='purchasing'
        )

        # Create manager user
        self.manager = User.objects.create_user(
            username='manager',
            email='manager@example.com',
            password='testpass123',
            role='manager'
        )

        # Create basic data
        self.category = Category.objects.create(
            name='Test Category',
            description='Test category description'
        )

        self.unit = Unit.objects.create(
            name='Kilogram',
            symbol='kg'
        )

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
            phone='1234567890',
            status='active'
        )

    def test_user_creation(self):
        """Test user model creation and role assignment"""
        self.assertEqual(self.user.username, 'testuser')
        self.assertEqual(self.user.role, 'purchasing')
        self.assertEqual(self.user.get_role_display(), 'خرید')
        self.assertTrue(self.user.check_password('testpass123'))

    def test_category_creation(self):
        """Test category model"""
        self.assertEqual(self.category.name, 'Test Category')
        self.assertEqual(self.category.description, 'Test category description')
        self.assertTrue(self.category.is_active)

    def test_unit_creation(self):
        """Test unit model"""
        self.assertEqual(self.unit.name, 'Kilogram')
        self.assertEqual(self.unit.symbol, 'kg')
        self.assertTrue(self.unit.is_active)

    def test_product_creation(self):
        """Test product model"""
        self.assertEqual(self.product.name, 'Test Product')
        self.assertEqual(self.product.category, self.category)
        self.assertEqual(self.product.unit, self.unit)
        self.assertEqual(self.product.min_stock, 10)
        self.assertTrue(self.product.is_active)

    def test_supplier_creation(self):
        """Test supplier model"""
        self.assertEqual(self.supplier.name, 'Test Supplier')
        self.assertEqual(self.supplier.contact_person, 'John Doe')
        self.assertEqual(self.supplier.email, 'supplier@example.com')
        self.assertEqual(self.supplier.status, 'active')
        self.assertIsNotNone(self.supplier.created_at)

    def test_purchase_request_creation(self):
        """Test purchase request model"""
        request = PurchaseRequest.objects.create(
            user=self.user,
            status='pending'
        )

        self.assertEqual(request.user, self.user)
        self.assertEqual(request.status, 'pending')
        self.assertIsNotNone(request.created_at)

    def test_purchase_item_creation(self):
        """Test purchase item model"""
        request = PurchaseRequest.objects.create(user=self.user)
        item = PurchaseItem.objects.create(
            request=request,
            product=self.product,
            name='Test Item',
            quantity=5,
            unit='kg',
            description='Test description'
        )

        self.assertEqual(item.request, request)
        self.assertEqual(item.product, self.product)
        self.assertEqual(item.quantity, 5)
        self.assertEqual(item.unit, 'kg')

    def test_purchase_order_creation(self):
        """Test purchase order model"""
        order = PurchaseOrder.objects.create(
            request=PurchaseRequest.objects.create(user=self.user),
            supplier=self.supplier,
            order_number='ORD-001',
            status='pending'
        )

        self.assertEqual(order.supplier, self.supplier)
        self.assertEqual(order.order_number, 'ORD-001')
        self.assertEqual(order.status, 'pending')

    def test_invoice_creation(self):
        """Test invoice model"""
        order = PurchaseOrder.objects.create(
            request=PurchaseRequest.objects.create(user=self.user),
            supplier=self.supplier,
            order_number='ORD-001'
        )

        invoice = Invoice.objects.create(
            order=order,
            invoice_number='INV-001',
            total_amount=1000.00,
            due_date=timezone.now().date() + timedelta(days=30),
            payment_status='unpaid'
        )

        self.assertEqual(invoice.order, order)
        self.assertEqual(invoice.invoice_number, 'INV-001')
        self.assertEqual(invoice.total_amount, 1000.00)
        self.assertEqual(invoice.payment_status, 'unpaid')

    def test_payment_creation(self):
        """Test payment model"""
        order = PurchaseOrder.objects.create(
            request=PurchaseRequest.objects.create(user=self.user),
            supplier=self.supplier,
            order_number='ORD-001'
        )

        invoice = Invoice.objects.create(
            order=order,
            invoice_number='INV-001',
            total_amount=1000.00,
            due_date=timezone.now().date() + timedelta(days=30)
        )

        payment_method = PaymentLocation.objects.create(
            name='Bank Transfer',
            is_active=True
        )

        payment = Payment.objects.create(
            invoice=invoice,
            amount=500.00,
            payment_date=timezone.now().date(),
            payment_location=payment_method,
            reference_number='REF-001',
            created_by=self.user
        )

        self.assertEqual(payment.invoice, invoice)
        self.assertEqual(payment.amount, 500.00)
        self.assertEqual(payment.payment_location, payment_method)
        self.assertEqual(payment.created_by, self.user)

    def test_activity_log_creation(self):
        """Test activity log model"""
        activity = ActivityLog.objects.create(
            user=self.user,
            action='created',
            description='Test activity',
            request=PurchaseRequest.objects.create(user=self.user)
        )

        self.assertEqual(activity.user, self.user)
        self.assertEqual(activity.action, 'created')
        self.assertEqual(activity.description, 'Test activity')

    def test_notification_creation(self):
        """Test notification model"""
        notification = Notification.objects.create(
            user=self.user,
            title='Test Notification',
            message='This is a test notification',
            notification_type='info'
        )

        self.assertEqual(notification.user, self.user)
        self.assertEqual(notification.title, 'Test Notification')
        self.assertEqual(notification.notification_type, 'info')
        self.assertFalse(notification.is_read)

    def test_supplier_evaluation_creation(self):
        """Test supplier evaluation model"""
        evaluation = SupplierEvaluation.objects.create(
            supplier=self.supplier,
            evaluator=self.user,
            rating=4.5,
            comments='Good supplier',
            evaluation_date=timezone.now().date()
        )

        self.assertEqual(evaluation.supplier, self.supplier)
        self.assertEqual(evaluation.evaluator, self.user)
        self.assertEqual(evaluation.rating, 4.5)

    def test_model_str_methods(self):
        """Test string representations of models"""
        self.assertEqual(str(self.category), 'Test Category')
        self.assertEqual(str(self.unit), 'Kilogram')
        self.assertEqual(str(self.product), 'Test Product')
        self.assertEqual(str(self.supplier), 'Test Supplier')

        request = PurchaseRequest.objects.create(user=self.user)
        self.assertIn('testuser', str(request))

        order = PurchaseOrder.objects.create(
            request=request,
            supplier=self.supplier,
            order_number='ORD-001'
        )
        self.assertEqual(str(order), 'ORD-001')

        invoice = Invoice.objects.create(
            order=order,
            invoice_number='INV-001',
            total_amount=1000.00,
            due_date=timezone.now().date()
        )
        self.assertEqual(str(invoice), 'INV-001')

    def test_model_relationships(self):
        """Test model relationships and foreign keys"""
        # Create a complete purchase workflow
        request = PurchaseRequest.objects.create(user=self.user, status='approved')

        # Create purchase items
        item1 = PurchaseItem.objects.create(
            request=request,
            product=self.product,
            name=self.product.name,
            quantity=10,
            unit='kg'
        )

        # Create purchase order
        order = PurchaseOrder.objects.create(
            request=request,
            supplier=self.supplier,
            order_number='ORD-001',
            status='completed'
        )

        # Create order items
        order_item = PurchaseOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=10,
            unit_price=50.00
        )

        # Create invoice
        invoice = Invoice.objects.create(
            order=order,
            invoice_number='INV-001',
            total_amount=500.00,
            due_date=timezone.now().date() + timedelta(days=30),
            payment_status='paid'
        )

        # Create invoice items
        invoice_item = InvoiceItem.objects.create(
            invoice=invoice,
            product=self.product,
            description=self.product.name,
            quantity=10,
            unit_price=50.00,
            total_price=500.00
        )

        # Test relationships
        self.assertEqual(request.items.count(), 1)
        self.assertEqual(order.request, request)
        self.assertEqual(invoice.order, order)
        self.assertEqual(invoice.invoice_items.count(), 1)

    def test_data_integrity(self):
        """Test data integrity constraints"""
        # Test unique constraints
        with self.assertRaises(Exception):
            Product.objects.create(
                name='Test Product',  # Duplicate name
                category=self.category,
                unit=self.unit
            )

        with self.assertRaises(Exception):
            Supplier.objects.create(
                name='Test Supplier',  # Duplicate name
                contact_person='Jane Doe',
                email='jane@example.com'
            )

    def test_model_validation(self):
        """Test model field validation"""
        # Test email validation
        supplier = Supplier(
            name='Valid Supplier',
            contact_person='Valid Person',
            email='invalid-email',  # Invalid email
            phone='1234567890'
        )

        with self.assertRaises(Exception):
            supplier.full_clean()  # Should raise validation error

        # Test positive amount validation
        payment_method = PaymentLocation.objects.create(name='Test Method')

        payment = Payment(
            invoice=Invoice.objects.create(
                order=PurchaseOrder.objects.create(
                    request=PurchaseRequest.objects.create(user=self.user),
                    supplier=self.supplier,
                    order_number='ORD-002'
                ),
                invoice_number='INV-002',
                total_amount=100.00,
                due_date=timezone.now().date()
            ),
            amount=-100.00,  # Negative amount should be invalid
            payment_date=timezone.now().date(),
            payment_location=payment_method
        )

        with self.assertRaises(Exception):
            payment.full_clean()
