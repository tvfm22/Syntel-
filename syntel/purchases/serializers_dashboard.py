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
    supplier_notes = serializers.CharField(read_only=True)
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        # اگر فروشنده فروشگاه نقدی است، نام نمایشی را تغییر دهیم
        if data.get('supplier_notes') and 'فروشگاه نقدی' in data['supplier_notes']:
            # نام واقعی فروشگاه را نگه می‌داریم
            data['supplier_display_name'] = 'فروشگاه نقدی'
        else:
            data['supplier_display_name'] = data.get('supplier_name', '-')
        return data

    class Meta:
        model = Product
        fields = ['id', 'name', 'category_name', 'current_price', 'previous_price', 'percent_change', 'last_update', 'supplier_name', 'supplier_notes']


# Serializers for Bubble Chart Analytics
class BubbleDataSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(max_length=200)
    value = serializers.DecimalField(max_digits=15, decimal_places=2)
    change_percent = serializers.DecimalField(max_digits=6, decimal_places=2)
    previous_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    item_count = serializers.IntegerField(required=False)
    request_count = serializers.IntegerField(required=False)


class DepartmentExpenseSerializer(serializers.Serializer):
    bubbles = BubbleDataSerializer(many=True)
    total_expense = serializers.DecimalField(max_digits=15, decimal_places=2)
    period = serializers.CharField(max_length=50)


class CategoryExpenseSerializer(serializers.Serializer):
    department_id = serializers.IntegerField()
    department_name = serializers.CharField(max_length=200)
    bubbles = BubbleDataSerializer(many=True)
    total_expense = serializers.DecimalField(max_digits=15, decimal_places=2)
    period = serializers.CharField(max_length=50)


class ProductDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(max_length=200)
    category_name = serializers.CharField(max_length=200)
    total_expense = serializers.DecimalField(max_digits=15, decimal_places=2)
    change_percent = serializers.DecimalField(max_digits=6, decimal_places=2)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit = serializers.CharField(max_length=50)
    average_price = serializers.DecimalField(max_digits=12, decimal_places=2)


class ProductListResponseSerializer(serializers.Serializer):
    category_id = serializers.IntegerField()
    category_name = serializers.CharField(max_length=200)
    products = ProductDetailSerializer(many=True)
    total_expense = serializers.DecimalField(max_digits=15, decimal_places=2)
    period = serializers.CharField(max_length=50)
