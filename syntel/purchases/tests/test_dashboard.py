import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from purchases.models import Product, PriceHistory, Category, Supplier, User
from decimal import Decimal
from django.utils import timezone

@pytest.mark.django_db
class TestDashboardAPI:
    @pytest.fixture
    def api_client(self):
        return APIClient()

    @pytest.fixture
    def user(self):
        return User.objects.create_user(username='testuser', password='password', role='manager')

    @pytest.fixture
    def setup_data(self):
        category = Category.objects.create(name='Test Category')
        supplier = Supplier.objects.create(name='Test Supplier')
        product = Product.objects.create(name='Test Product', category=category)
        
        # Create history
        PriceHistory.objects.create(
            product=product,
            price=Decimal('10000'),
            date=timezone.now() - timezone.timedelta(days=2),
            supplier=supplier,
            percent_change=0
        )
        PriceHistory.objects.create(
            product=product,
            price=Decimal('11000'),
            date=timezone.now(),
            supplier=supplier,
            percent_change=10
        )
        return product, category, supplier

    def test_dashboard_stats(self, api_client, user, setup_data):
        api_client.force_authenticate(user=user)
        url = reverse('api_dashboard_stats')
        response = api_client.get(url)
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['increased_count'] == 1
        assert response.data['decreased_count'] == 0
        assert response.data['avg_change'] == 10.0

    def test_product_changes_list(self, api_client, user, setup_data):
        api_client.force_authenticate(user=user)
        url = reverse('api_product_changes')
        response = api_client.get(url)
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]['name'] == 'Test Product'
        assert float(response.data[0]['percent_change']) == 10.0

    def test_high_increase_products(self, api_client, user, setup_data):
        api_client.force_authenticate(user=user)
        url = reverse('api_high_increase')
        response = api_client.get(url)
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert float(response.data[0]['percent_change']) > 5

    def test_price_history(self, api_client, user, setup_data):
        product, _, _ = setup_data
        api_client.force_authenticate(user=user)
        url = reverse('api_price_history', kwargs={'pk': product.id})
        response = api_client.get(url)
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_filters(self, api_client, user, setup_data):
        api_client.force_authenticate(user=user)
        url = reverse('api_dashboard_filters')
        response = api_client.get(url)
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['categories']) > 0
        assert len(response.data['suppliers']) > 0
