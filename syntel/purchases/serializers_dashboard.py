from rest_framework import serializers
from .models import Product, PriceHistory, Category, Supplier

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']

class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name']

class PriceHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceHistory
        fields = ['id', 'price', 'date', 'percent_change', 'invoice_id', 'supplier_id']

class ProductChangeSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    current_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    previous_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    percent_change = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)
    last_update = serializers.DateTimeField(read_only=True)
    supplier_name = serializers.CharField(read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'name', 'category_name', 'current_price', 'previous_price', 'percent_change', 'last_update', 'supplier_name']
