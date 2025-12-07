from django.test import TestCase
from django.contrib.auth import get_user_model
from purchases.forms import (
    PurchaseRequestForm, PurchaseItemForm
)
from purchases.models import (
    Category, Unit, Product, Supplier
)

User = get_user_model()


class FormTests(TestCase):
    """Test cases for all forms in the Syntel system"""

    def setUp(self):
        """Set up test data"""
        User.objects.filter(username='testuser').delete()
        Category.objects.filter(name='Test Category').delete()
        Unit.objects.filter(name='Kilogram').delete()
        Product.objects.filter(name='Test Product').delete()
        Supplier.objects.filter(name='Test Supplier').delete()

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
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
            role='kitchen'
        )

    def test_purchase_request_form_instantiation(self):
        """Test PurchaseRequestForm can be instantiated"""
        form = PurchaseRequestForm()
        # Since this form has no fields defined, just check it exists
        self.assertIsNotNone(form)

    def test_purchase_item_form_valid(self):
        """Test PurchaseItemForm with valid data"""
        form_data = {
            'name': 'Test Item',
            'quantity': 10,
            'unit': 'kg',
            'description': 'Test item description'
        }
        form = PurchaseItemForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_purchase_item_form_invalid(self):
        """Test PurchaseItemForm with invalid data"""
        # Test negative quantity
        form_data = {
            'name': 'Test Item',
            'quantity': -5,
            'unit': 'kg',
            'description': 'Test item description'
        }
        form = PurchaseItemForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('quantity', form.errors)

        # Test missing required fields
        form = PurchaseItemForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)
        self.assertIn('quantity', form.errors)

    def test_form_field_validation(self):
        """Test various field validation scenarios"""
        # Test that forms handle basic validation
        # Test with very large quantities (should be valid)
        form_data = {
            'name': 'Test Item',
            'quantity': 999999,
            'unit': 'kg',
            'description': 'Large quantity test'
        }
        form = PurchaseItemForm(data=form_data)
        self.assertTrue(form.is_valid())  # Should accept large quantities

        # Test with missing required fields
        form = PurchaseItemForm(data={'quantity': 10})
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)

    def test_form_clean_methods(self):
        """Test form instantiation and basic functionality"""
        # Test that forms can be instantiated and work
        form = PurchaseItemForm()
        self.assertIsNotNone(form)

        # Test valid form submission
        form_data = {
            'name': 'Test Item',
            'quantity': 10,
            'unit': 'kg',
            'description': 'Test description'
        }
        form = PurchaseItemForm(data=form_data)
        self.assertTrue(form.is_valid())
