from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import generics, permissions
from django.db import models
from django.db.models import Subquery, OuterRef, F
from django.utils import timezone
from django.http import HttpResponse
from datetime import timedelta
import openpyxl
from openpyxl.styles import Font, PatternFill
from .models import Product, PriceHistory, Category, Supplier, Cafe
from .serializers_dashboard import (
    ProductChangeSerializer, 
    PriceHistorySerializer,
    CategorySerializer,
    SupplierSerializer
)
from .views import get_cafe_from_request

class DashboardFiltersAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        cafe = get_cafe_from_request(request)
        categories = Category.objects.filter(is_active=True)
        suppliers = Supplier.objects.filter(status='active')
        if cafe:
            categories = categories.filter(cafe=cafe)
            suppliers = suppliers.filter(cafe=cafe)
        
        # ساخت لیست فروشندگان با گزینه فروشگاه‌های نقدی
        suppliers_list = list(SupplierSerializer(suppliers, many=True).data)
        
        # اضافه کردن گزینه "فروشگاه‌های نقدی"
        cash_stores_qs = Supplier.objects.filter(
            status='active',
            notes__icontains='فروشگاه نقدی'
        )
        if cafe:
            cash_stores_qs = cash_stores_qs.filter(cafe=cafe)
        cash_stores_count = cash_stores_qs.count()
        
        if cash_stores_count > 0:
            suppliers_list.insert(0, {
                'id': 'cash_stores',
                'name': f'فروشگاه‌های نقدی ({cash_stores_count})',
                'supplier_code': 'CASH_GROUP'
            })
        
        return Response({
            'categories': CategorySerializer(categories, many=True).data,
            'suppliers': suppliers_list
        })

class ProductChangeListAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductChangeSerializer

    def get_queryset(self):
        cafe = get_cafe_from_request(self.request)
        latest_history = PriceHistory.objects.filter(
            product=OuterRef('pk')
        ).order_by('-date')

        queryset = Product.objects.annotate(
            current_price=Subquery(latest_history.values('price')[:1]),
            percent_change=Subquery(latest_history.values('percent_change')[:1]),
            last_update=Subquery(latest_history.values('date')[:1]),
            supplier_name=Subquery(latest_history.values('supplier__name')[:1]),
            supplier_id=Subquery(latest_history.values('supplier__id')[:1]),
            supplier_notes=Subquery(latest_history.values('supplier__notes')[:1]),
            previous_price=Subquery(latest_history.values('price')[1:2])
        ).filter(current_price__isnull=False)
        
        # Filter by cafe
        if cafe:
            queryset = queryset.filter(cafe=cafe)

        # Filters
        category_id = self.request.query_params.get('category')
        supplier_id = self.request.query_params.get('store')
        days = self.request.query_params.get('days')

        if category_id:
            queryset = queryset.filter(category_id=category_id)
        
        if supplier_id:
            # فیلتر ویژه برای فروشگاه‌های نقدی
            if supplier_id == 'cash_stores':
                # فیلتر محصولاتی که از فروشگاه‌های نقدی خریداری شده‌اند
                queryset = queryset.filter(supplier_notes__icontains='فروشگاه نقدی')
            else:
                # Filter by the supplier of the latest price history
                queryset = queryset.filter(supplier_id=supplier_id)

        if days:
            start_date = timezone.now() - timedelta(days=int(days))
            queryset = queryset.filter(last_update__gte=start_date)

        return queryset.order_by('-percent_change')

class HighIncreaseProductsAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductChangeSerializer

    def get_queryset(self):
        cafe = get_cafe_from_request(self.request)
        latest_history = PriceHistory.objects.filter(
            product=OuterRef('pk')
        ).order_by('-date')

        queryset = Product.objects.annotate(
            current_price=Subquery(latest_history.values('price')[:1]),
            percent_change=Subquery(latest_history.values('percent_change')[:1]),
            last_update=Subquery(latest_history.values('date')[:1]),
            supplier_name=Subquery(latest_history.values('supplier__name')[:1]),
            supplier_id=Subquery(latest_history.values('supplier__id')[:1]),
            supplier_notes=Subquery(latest_history.values('supplier__notes')[:1]),
            previous_price=Subquery(latest_history.values('price')[1:2])
        ).filter(
            percent_change__gt=5
        )
        
        # Filter by cafe
        if cafe:
            queryset = queryset.filter(cafe=cafe)
        
        # Apply filters
        category_id = self.request.query_params.get('category')
        supplier_id = self.request.query_params.get('store')
        days = self.request.query_params.get('days')
        
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        
        if supplier_id:
            if supplier_id == 'cash_stores':
                queryset = queryset.filter(supplier_notes__icontains='فروشگاه نقدی')
            else:
                queryset = queryset.filter(supplier_id=supplier_id)
        
        if days:
            start_date = timezone.now() - timedelta(days=int(days))
            queryset = queryset.filter(last_update__gte=start_date)
        
        return queryset.order_by('-percent_change')

class PriceHistoryAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PriceHistorySerializer

    def get_queryset(self):
        product_id = self.kwargs['pk']
        return PriceHistory.objects.filter(product_id=product_id).order_by('date')

from django.core.cache import cache

class DashboardStatsAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        cafe = get_cafe_from_request(request)
        # Get filter parameters
        category_id = request.query_params.get('category')
        supplier_id = request.query_params.get('store')
        days = request.query_params.get('days')
        
        # Create cache key based on filters
        cafe_code = cafe.code if cafe else 'all'
        cache_key = f'dashboard_stats_{cafe_code}_{category_id}_{supplier_id}_{days}'
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(cached_data)

        latest_history = PriceHistory.objects.filter(
            product=OuterRef('pk')
        ).order_by('-date')
        
        products = Product.objects.annotate(
            percent_change=Subquery(latest_history.values('percent_change')[:1]),
            last_update=Subquery(latest_history.values('date')[:1]),
            supplier_name=Subquery(latest_history.values('supplier__name')[:1])
        ).filter(percent_change__isnull=False)
        
        # Filter by cafe
        if cafe:
            products = products.filter(cafe=cafe)
        
        # Apply filters
        if category_id:
            products = products.filter(category_id=category_id)
        
        if supplier_id:
            products = products.filter(supplier_name=Supplier.objects.get(id=supplier_id).name)
        
        if days:
            start_date = timezone.now() - timedelta(days=int(days))
            products = products.filter(last_update__gte=start_date)
        
        increased = products.filter(percent_change__gt=0).count()
        decreased = products.filter(percent_change__lt=0).count()
        
        avg_change = products.aggregate(avg=models.Avg('percent_change'))['avg'] or 0
        
        max_increase = products.order_by('-percent_change').first()
        max_increase_data = None
        if max_increase:
            max_increase_data = {
                'name': max_increase.name,
                'percent': max_increase.percent_change
            }

        data = {
            'increased_count': increased,
            'decreased_count': decreased,
            'avg_change': avg_change,
            'max_increase': max_increase_data
        }
        
        # Cache for 5 minutes (shorter cache for filtered results)
        cache.set(cache_key, data, 60 * 5)
        
        return Response(data)

class DashboardExportAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Reuse the logic from ProductChangeListAPIView to get filtered data
        view = ProductChangeListAPIView()
        view.request = request
        queryset = view.get_queryset()

        # Create Excel workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Price Changes"
        ws.sheet_view.rightToLeft = True

        # Headers
        headers = ['نام محصول', 'دسته‌بندی', 'فروشگاه', 'قیمت قبلی', 'قیمت فعلی', 'درصد تغییر', 'تاریخ بروزرسانی']
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")

        # Data
        for row_num, product in enumerate(queryset, 2):
            ws.cell(row=row_num, column=1, value=product.name)
            ws.cell(row=row_num, column=2, value=product.category.name if product.category else '-')
            ws.cell(row=row_num, column=3, value=product.supplier_name or '-')
            ws.cell(row=row_num, column=4, value=product.previous_price)
            ws.cell(row=row_num, column=5, value=product.current_price)
            ws.cell(row=row_num, column=6, value=f"{product.percent_change}%")
            ws.cell(row=row_num, column=7, value=product.last_update.strftime('%Y-%m-%d %H:%M') if product.last_update else '-')

        # Response
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=price_changes_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def price_dashboard_view(request):
    return render(request, 'price_dashboard.html')

@login_required
def price_test_view(request):
    """صفحه تست برای بررسی داده‌های قیمت"""
    from django.db.models import Subquery, OuterRef
    
    latest_history = PriceHistory.objects.filter(
        product=OuterRef('pk')
    ).order_by('-date')
    
    products = Product.objects.annotate(
        current_price=Subquery(latest_history.values('price')[:1]),
        percent_change=Subquery(latest_history.values('percent_change')[:1]),
        last_update=Subquery(latest_history.values('date')[:1])
    ).filter(current_price__isnull=False).order_by('-percent_change')[:10]
    
    all_products = Product.objects.annotate(
        percent_change=Subquery(latest_history.values('percent_change')[:1])
    ).filter(percent_change__isnull=False)
    
    increased = all_products.filter(percent_change__gt=0).count()
    decreased = all_products.filter(percent_change__lt=0).count()
    avg_change = all_products.aggregate(avg=models.Avg('percent_change'))['avg'] or 0
    
    high_increase = products.filter(percent_change__gt=5)[:10]
    
    context = {
        'products': products,
        'products_count': all_products.count(),
        'increased_count': increased,
        'decreased_count': decreased,
        'avg_change': avg_change,
        'high_increase_products': high_increase
    }
    
    return render(request, 'price_test.html', context)
