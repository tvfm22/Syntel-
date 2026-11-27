from __future__ import annotations

from ..models import Category
from ..models import PriceHistory
from ..models import Product
from ..models import Supplier
from ..serializers_dashboard import CategorySerializer
from ..serializers_dashboard import PriceHistorySerializer
from ..serializers_dashboard import ProductChangeSerializer
from ..serializers_dashboard import SupplierSerializer
from .helpers import custom_login_required
from .helpers import resolve_api_cafe
from .helpers import role_required
from datetime import timedelta
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import OuterRef
from django.db.models import Subquery
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from rest_framework import generics
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
import hashlib
import openpyxl


DASHBOARD_ALLOWED_ROLES = frozenset({
    'manager', 'ceo', 'finance_manager', 'accounting', 'purchasing', 'admin',
})


class HasDashboardRole(permissions.BasePermission):
    """اجازهٔ دسترسی فقط برای نقش‌های مجاز مدیریت/مالی/خرید"""
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if user.is_superuser:
            return True
        return (
            getattr(user, 'role', None) in DASHBOARD_ALLOWED_ROLES
            or getattr(user, 'secondary_role', None) in DASHBOARD_ALLOWED_ROLES
        )


class CafeScopedDashboardMixin:
    """اعمال قفلِ بستهٔ کافه برای تمام APIهای داشبورد قیمت (شامل TokenAuth)."""
    permission_classes = [permissions.IsAuthenticated, HasDashboardRole]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        cafe, err_status, err_msg = resolve_api_cafe(request)
        if err_status == 404:
            raise NotFound(err_msg)
        if err_status:
            raise PermissionDenied(err_msg)
        self.cafe = cafe


def _parse_pos_int(raw, param_name):
    if raw is None or str(raw).strip() == '':
        return None
    s = str(raw).strip()
    if not s.isdigit():
        raise ValidationError({param_name: 'مقدار عددی معتبر نیست.'})
    return int(s)


def _apply_product_change_filters(queryset, query_params):
    category_id = _parse_pos_int(query_params.get('category'), 'category')
    supplier_id = query_params.get('store')
    days = _parse_pos_int(query_params.get('days'), 'days')

    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)

    if supplier_id:
        if supplier_id == 'cash_stores':
            queryset = queryset.filter(supplier_notes__icontains='فروشگاه نقدی')
        else:
            sup_int = _parse_pos_int(supplier_id, 'store')
            queryset = queryset.filter(supplier_id=sup_int)

    if days is not None:
        start_date = timezone.now() - timedelta(days=min(days, 3650))
        queryset = queryset.filter(last_update__gte=start_date)

    return queryset


class DashboardFiltersAPIView(CafeScopedDashboardMixin, APIView):
    def get(self, request):
        cafe = self.cafe
        categories = Category.objects.filter(is_active=True, cafe=cafe)
        suppliers = Supplier.objects.filter(status='active', cafe=cafe)

        suppliers_list = list(SupplierSerializer(suppliers, many=True).data)

        cash_stores_count = Supplier.objects.filter(
            status='active',
            cafe=cafe,
            notes__icontains='فروشگاه نقدی',
        ).count()

        if cash_stores_count > 0:
            suppliers_list.insert(0, {
                'id': 'cash_stores',
                'name': f'فروشگاه‌های نقدی ({cash_stores_count})',
                'supplier_code': 'CASH_GROUP',
            })

        return Response({
            'categories': CategorySerializer(categories, many=True).data,
            'suppliers': suppliers_list,
        })


class ProductChangeListAPIView(CafeScopedDashboardMixin, generics.ListAPIView):
    serializer_class = ProductChangeSerializer

    def get_queryset(self):
        cafe = self.cafe
        latest_history = PriceHistory.objects.filter(
            product=OuterRef('pk')
        ).order_by('-date')

        queryset = Product.objects.select_related('category').annotate(
            current_price=Subquery(latest_history.values('price')[:1]),
            percent_change=Subquery(latest_history.values('percent_change')[:1]),
            last_update=Subquery(latest_history.values('date')[:1]),
            supplier_name=Subquery(latest_history.values('supplier__name')[:1]),
            supplier_id=Subquery(latest_history.values('supplier__id')[:1]),
            supplier_notes=Subquery(latest_history.values('supplier__notes')[:1]),
            previous_price=Subquery(latest_history.values('price')[1:2]),
        ).filter(cafe=cafe, current_price__isnull=False)

        queryset = _apply_product_change_filters(queryset, self.request.query_params)
        return queryset.order_by('-percent_change')


class HighIncreaseProductsAPIView(CafeScopedDashboardMixin, generics.ListAPIView):
    serializer_class = ProductChangeSerializer

    def get_queryset(self):
        cafe = self.cafe
        latest_history = PriceHistory.objects.filter(
            product=OuterRef('pk')
        ).order_by('-date')

        queryset = Product.objects.select_related('category').annotate(
            current_price=Subquery(latest_history.values('price')[:1]),
            percent_change=Subquery(latest_history.values('percent_change')[:1]),
            last_update=Subquery(latest_history.values('date')[:1]),
            supplier_name=Subquery(latest_history.values('supplier__name')[:1]),
            supplier_id=Subquery(latest_history.values('supplier__id')[:1]),
            supplier_notes=Subquery(latest_history.values('supplier__notes')[:1]),
            previous_price=Subquery(latest_history.values('price')[1:2]),
        ).filter(
            cafe=cafe,
            percent_change__gt=5,
        )

        queryset = _apply_product_change_filters(queryset, self.request.query_params)
        return queryset.order_by('-percent_change')


class PriceHistoryAPIView(CafeScopedDashboardMixin, generics.ListAPIView):
    serializer_class = PriceHistorySerializer

    def get_queryset(self):
        cafe = self.cafe
        product_id = self.kwargs['pk']
        if not Product.objects.filter(pk=product_id, cafe=cafe).exists():
            return PriceHistory.objects.none()
        return PriceHistory.objects.filter(product_id=product_id).order_by('date')


class DashboardStatsAPIView(CafeScopedDashboardMixin, APIView):
    def get(self, request):
        cafe = self.cafe
        category_id = _parse_pos_int(request.query_params.get('category'), 'category')
        supplier_id = request.query_params.get('store')
        days = _parse_pos_int(request.query_params.get('days'), 'days')

        if supplier_id and supplier_id != 'cash_stores':
            supplier_int = _parse_pos_int(supplier_id, 'store')
        else:
            supplier_int = None

        raw_key = f"cafe={cafe.id}|cat={category_id}|store={supplier_id or ''}|days={days}"
        cache_key = f"dashboard_stats_{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:32]}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(cached_data)

        latest_history = PriceHistory.objects.filter(
            product=OuterRef('pk')
        ).order_by('-date')

        products = Product.objects.annotate(
            percent_change=Subquery(latest_history.values('percent_change')[:1]),
            last_update=Subquery(latest_history.values('date')[:1]),
            supplier_name=Subquery(latest_history.values('supplier__name')[:1]),
            supplier_id=Subquery(latest_history.values('supplier__id')[:1]),
            supplier_notes=Subquery(latest_history.values('supplier__notes')[:1]),
        ).filter(cafe=cafe, percent_change__isnull=False)

        if category_id is not None:
            products = products.filter(category_id=category_id)

        if supplier_id:
            if supplier_id == 'cash_stores':
                products = products.filter(supplier_notes__icontains='فروشگاه نقدی')
            else:
                if Supplier.objects.filter(id=supplier_int, cafe=cafe).exists():
                    products = products.filter(supplier_id=supplier_int)
                else:
                    products = products.none()

        if days is not None:
            start_date = timezone.now() - timedelta(days=min(days, 3650))
            products = products.filter(last_update__gte=start_date)

        increased = products.filter(percent_change__gt=0).count()
        decreased = products.filter(percent_change__lt=0).count()
        avg_change = products.aggregate(avg=models.Avg('percent_change'))['avg'] or 0

        max_increase = products.order_by('-percent_change').first()
        max_increase_data = None
        if max_increase:
            max_increase_data = {
                'name': max_increase.name,
                'percent': max_increase.percent_change,
            }

        data = {
            'increased_count': increased,
            'decreased_count': decreased,
            'avg_change': avg_change,
            'max_increase': max_increase_data,
        }

        cache.set(cache_key, data, 60 * 5)
        return Response(data)


class DashboardExportAPIView(CafeScopedDashboardMixin, APIView):
    def get(self, request):
        view = ProductChangeListAPIView()
        view.request = request
        view.cafe = self.cafe
        queryset = view.get_queryset()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Price Changes"
        ws.sheet_view.rightToLeft = True

        headers = ['نام محصول', 'دسته‌بندی', 'فروشگاه', 'قیمت قبلی', 'قیمت فعلی', 'درصد تغییر', 'تاریخ بروزرسانی']
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")

        for row_num, product in enumerate(queryset, 2):
            ws.cell(row=row_num, column=1, value=product.name)
            ws.cell(row=row_num, column=2, value=product.category.name if product.category else '-')
            ws.cell(row=row_num, column=3, value=product.supplier_name or '-')
            ws.cell(row=row_num, column=4, value=product.previous_price)
            ws.cell(row=row_num, column=5, value=product.current_price)
            ws.cell(row=row_num, column=6, value=f"{product.percent_change}%")
            ws.cell(row=row_num, column=7, value=product.last_update.strftime('%Y-%m-%d %H:%M') if product.last_update else '-')

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=price_changes_{timezone.localtime().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response


@custom_login_required
@role_required(list(DASHBOARD_ALLOWED_ROLES))
def price_dashboard_view(request):
    return render(request, 'price_dashboard.html')
