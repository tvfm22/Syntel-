from __future__ import annotations

from ..models import Category
from ..models import InvoiceItem
from ..models import Product
from ..models import PurchaseItem
from ..models import Requester
from ..models import Supplier
from ..serializers_dashboard import CategoryExpenseSerializer
from ..serializers_dashboard import DepartmentExpenseSerializer
from ..serializers_dashboard import ProductListResponseSerializer
from .analytics_helpers import HasAnalyticsRole
from .analytics_helpers import _dept_items_filter
from .analytics_helpers import _invoice_items_for_period
from .analytics_helpers import _normalize_period
from .analytics_helpers import _parse_int_param
from .analytics_helpers import _require_cafe_or_error
from .analytics_helpers import calculate_change_percent
from .analytics_helpers import calculate_growth_rate
from .analytics_helpers import get_cache_key
from .analytics_helpers import get_color_by_growth
from .analytics_helpers import get_date_range
from .helpers import _parse_iso_date
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import role_required
from ..middleware import get_cafe_code
from decimal import Decimal
from django.core.cache import cache
from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import F
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect
from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "ceo"])
def analytics_dashboard(request):
    """Redirect to line chart analytics as default"""
    cafe = getattr(request, 'cafe', None) or get_cafe_from_request(request)
    cafe_code = cafe.code if cafe is not None else (get_cafe_code(request) or 'local')
    return redirect('analytics_line_chart', cafe_code=cafe_code)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "ceo"])
def analytics_line_chart(request):
    """صفحه نمودار خطی روند هزینه‌ها"""
    return render(request, "analytics_line_chart.html")


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "ceo"])
def analytics_price_monitor(request):
    """صفحه نظارت بر قیمت در بخش تحلیل"""
    return render(request, "analytics_price_monitor.html")


@custom_login_required
@role_required(["purchasing", "manager", "ceo"])
def purchase_analytics(request):
    return render(request, "analytics_dashboard.html")


@custom_login_required
@role_required(["manager", "purchasing", "ceo"])
def financial_analytics(request):
    return render(request, "analytics_dashboard.html")


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "ceo"])
def supplier_analytics(request):
    return render(request, "analytics_dashboard.html")


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "ceo"])
def purchase_category_analysis(request):
    """
    صفحه تحلیل خرید بر اساس دسته‌بندی و فروشنده
    با فیلتر آبشاری: تاریخ -> دسته اصلی -> زیردسته -> فروشنده
    """
    cafe = get_cafe_from_request(request)

    start_date_raw = request.GET.get('start_date', '')
    end_date_raw = request.GET.get('end_date', '')
    category_raw = request.GET.get('category', '')
    subcategory_raw = request.GET.get('subcategory', '')
    supplier_raw = request.GET.get('supplier', '')

    start_date_obj = _parse_iso_date(start_date_raw)
    end_date_obj = _parse_iso_date(end_date_raw)
    category_id = int(category_raw) if str(category_raw).isdigit() else None
    subcategory_id = int(subcategory_raw) if str(subcategory_raw).isdigit() else None
    supplier_id = int(supplier_raw) if str(supplier_raw).isdigit() else None

    if cafe is None:
        main_categories = Category.objects.none()
        subcategories = Category.objects.none()
        suppliers = Supplier.objects.none()
    else:
        main_categories = Category.objects.filter(
            cafe=cafe,
            parent__isnull=True,
            is_active=True
        ).order_by('name')

        subcategories = []
        if category_id:
            subcategories = Category.objects.filter(
                cafe=cafe,
                parent_id=category_id,
                is_active=True
            ).order_by('name')

        if subcategory_id:
            invoice_items_qs = InvoiceItem.objects.filter(
                invoice__cafe=cafe,
                product__category_id=subcategory_id,
                invoice__status__in=['sent', 'approved', 'partially_paid', 'paid', 'overdue'],
                invoice__supplier__isnull=False
            )
            if start_date_obj:
                invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__gte=start_date_obj)
            if end_date_obj:
                invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__lte=end_date_obj)

            supplier_ids = invoice_items_qs.values_list('invoice__supplier_id', flat=True).distinct()
            suppliers = Supplier.objects.filter(
                cafe=cafe,
                id__in=supplier_ids,
                status='active'
            ).order_by('name')
        else:
            suppliers = Supplier.objects.filter(cafe=cafe, status='active').order_by('name')

    products_data = []
    summary = {
        'total_items': 0,
        'total_amount': Decimal('0'),
        'unique_products': 0,
        'unique_suppliers': 0
    }

    if cafe is not None and subcategory_id:
        invoice_items = InvoiceItem.objects.filter(
            invoice__cafe=cafe,
            product__category_id=subcategory_id,
            invoice__status__in=['sent', 'approved', 'partially_paid', 'paid', 'overdue']
        ).select_related('product', 'invoice', 'invoice__supplier')

        if start_date_obj:
            invoice_items = invoice_items.filter(invoice__invoice_date__gte=start_date_obj)
        if end_date_obj:
            invoice_items = invoice_items.filter(invoice__invoice_date__lte=end_date_obj)
        if supplier_id:
            invoice_items = invoice_items.filter(invoice__supplier_id=supplier_id)

        products_summary = invoice_items.values(
            'product__id',
            'product__name',
            'product__unit__name',
            'invoice__supplier__id',
            'invoice__supplier__name'
        ).annotate(
            total_quantity=Sum('quantity'),
            total_amount=Sum('line_total'),
            weighted_price_sum=Sum(F('quantity') * F('unit_price')),
            purchase_count=Count('id')
        ).order_by('-total_amount')

        products_data = list(products_summary)

        for product in products_data:
            if product['total_quantity'] and product['total_quantity'] > 0:
                product['weighted_avg_price'] = product['weighted_price_sum'] / product['total_quantity']
            else:
                product['weighted_avg_price'] = 0

        summary['total_items'] = invoice_items.count()
        summary['total_amount'] = invoice_items.aggregate(
            total=Sum('line_total')
        )['total'] or Decimal('0')
        summary['unique_products'] = len(products_data)
        summary['unique_suppliers'] = invoice_items.values('invoice__supplier').distinct().count()

        avg_result = invoice_items.aggregate(avg_price=Sum('unit_price'))
        count = invoice_items.count()
        if count > 0 and avg_result['avg_price']:
            summary['avg_price'] = avg_result['avg_price'] / count
        else:
            summary['avg_price'] = Decimal('0')

        distinct_units = {
            p.get('product__unit__name')
            for p in products_data
            if p.get('product__unit__name')
        }
        if len(distinct_units) > 1:
            summary['has_mixed_units'] = True
            summary['total_quantity'] = None
            summary['weighted_avg_price'] = None
        else:
            summary['has_mixed_units'] = False
            total_quantity = invoice_items.aggregate(total_qty=Sum('quantity'))['total_qty'] or Decimal('0')
            weighted_sum = invoice_items.aggregate(
                weighted=Sum(F('quantity') * F('unit_price'))
            )['weighted'] or Decimal('0')

            summary['total_quantity'] = total_quantity
            if total_quantity > 0:
                summary['weighted_avg_price'] = weighted_sum / total_quantity
            else:
                summary['weighted_avg_price'] = Decimal('0')

    context = {
        'main_categories': main_categories,
        'subcategories': subcategories,
        'suppliers': suppliers,
        'products_data': products_data,
        'summary': summary,
        'start_date': start_date_raw,
        'end_date': end_date_raw,
        'selected_category': category_raw,
        'selected_subcategory': subcategory_raw,
        'selected_supplier': supplier_raw,
    }

    return render(request, 'purchase_category_analysis.html', context)


@custom_login_required
@role_required(["purchasing", "manager"])
def inventory_analytics(request):
    return render(request, "analytics_dashboard.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def executive_summary(request):
    return render(request, "analytics_dashboard.html")


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def department_expenses_api(request) -> Response:
	"""Bubble data per department."""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	period = _normalize_period(request.GET.get('period'), 'weekly')
	current_start, current_end, previous_start, previous_end = get_date_range(period)

	requesters = list(Requester.objects.filter(cafe=cafe).order_by('id'))
	req_by_id = {r.id: r for r in requesters}
	req_by_name = {}
	for r in requesters:
		req_by_name[r.name] = r
		disp = r.get_name_display()
		if disp:
			req_by_name.setdefault(disp, r)

	def _resolve_requester_id(requester_id, user_role, dept_name):
		if requester_id in req_by_id:
			return requester_id
		if user_role in req_by_name:
			return req_by_name[user_role].id
		if dept_name in req_by_name:
			return req_by_name[dept_name].id
		return None

	current_totals = {r.id: Decimal('0.00') for r in requesters}
	purchased_counts = {r.id: Decimal('0.00') for r in requesters}
	current_rows = _invoice_items_for_period(start=current_start, end=current_end, cafe=cafe).values(
		'requester_id',
		'invoice__purchase_order__request__user__role',
		'invoice__purchase_order__request__user__department__name',
	).annotate(
		total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField()),
		qty=Coalesce(Sum('quantity'), Value(0), output_field=DecimalField()),
	)
	for row in current_rows:
		rid = _resolve_requester_id(
			row['requester_id'],
			row['invoice__purchase_order__request__user__role'],
			row['invoice__purchase_order__request__user__department__name'],
		)
		if rid is not None:
			current_totals[rid] += row['total'] or Decimal('0.00')
			purchased_counts[rid] += row['qty'] or Decimal('0.00')

	previous_totals = {r.id: Decimal('0.00') for r in requesters}
	previous_rows = _invoice_items_for_period(
		start=previous_start, end=previous_end, end_inclusive=False, cafe=cafe
	).values(
		'requester_id',
		'invoice__purchase_order__request__user__role',
		'invoice__purchase_order__request__user__department__name',
	).annotate(
		total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField()),
	)
	for row in previous_rows:
		rid = _resolve_requester_id(
			row['requester_id'],
			row['invoice__purchase_order__request__user__role'],
			row['invoice__purchase_order__request__user__department__name'],
		)
		if rid is not None:
			previous_totals[rid] += row['total'] or Decimal('0.00')

	requested_counts = {r.id: 0 for r in requesters}
	req_rows = PurchaseItem.objects.filter(
		request__cafe=cafe,
		request__created_at__gte=current_start,
		request__created_at__lte=current_end,
	).values(
		'request__user__role',
		'request__user__department__name',
	).annotate(
		total=Coalesce(Sum('quantity'), Value(0), output_field=DecimalField()),
	)
	for row in req_rows:
		rid = _resolve_requester_id(
			None,
			row['request__user__role'],
			row['request__user__department__name'],
		)
		if rid is not None:
			requested_counts[rid] += int(row['total'] or 0)

	bubbles_data = []
	total_expense = Decimal('0.00')
	for requester in requesters:
		current_expense = current_totals[requester.id]
		previous_expense = previous_totals[requester.id]
		purchased_items_count = purchased_counts[requester.id]
		requested_items_count = requested_counts[requester.id]
		change_percent = calculate_change_percent(current_expense, previous_expense)

		if current_expense > 0 or previous_expense > 0 or requested_items_count > 0 or purchased_items_count > 0:
			bubbles_data.append({
				'id': requester.id,
				'name': requester.get_name_display(),
				'value': current_expense,
				'change_percent': change_percent,
				'previous_value': previous_expense,
				'item_count': int(purchased_items_count),
				'request_count': int(requested_items_count),
			})
			total_expense += current_expense

	response_data = {
		'bubbles': bubbles_data,
		'total_expense': total_expense,
		'period': period,
	}

	serializer = DepartmentExpenseSerializer(data=response_data)
	if serializer.is_valid():
		return Response(serializer.data)
	return Response(serializer.errors, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def category_expenses_api(request, department_id):
	"""Bubble data per category for a department."""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	period = _normalize_period(request.GET.get('period'), 'weekly')
	current_start, current_end, previous_start, previous_end = get_date_range(period)

	try:
		requester = Requester.objects.get(id=department_id, cafe=cafe)
	except Requester.DoesNotExist:
		return Response({'error': 'Department not found'}, status=404)

	dept_filter = _dept_items_filter(requester)
	dept_names = [n for n in (requester.name, requester.get_name_display()) if n]

	current_by_cat = {}
	item_count_by_cat = {}
	for row in _invoice_items_for_period(start=current_start, end=current_end, cafe=cafe).filter(
		dept_filter, product__category__isnull=False
	).values('product__category_id').annotate(
		total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField()),
		qty=Coalesce(Sum('quantity'), Value(0), output_field=DecimalField()),
	):
		cid = row['product__category_id']
		current_by_cat[cid] = row['total'] or Decimal('0.00')
		item_count_by_cat[cid] = row['qty'] or Decimal('0.00')

	previous_by_cat = {
		row['product__category_id']: row['total'] or Decimal('0.00')
		for row in _invoice_items_for_period(
			start=previous_start, end=previous_end, end_inclusive=False, cafe=cafe
		).filter(
			dept_filter, product__category__isnull=False
		).values('product__category_id').annotate(
			total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField()),
		)
	}

	request_count_by_cat = {
		row['product__category_id']: int(row['total'] or 0)
		for row in PurchaseItem.objects.filter(
			request__cafe=cafe,
			request__created_at__gte=current_start,
			request__created_at__lte=current_end,
			product__category__isnull=False,
		).filter(
			Q(request__user__role=requester.name)
			| Q(request__user__department__name__in=dept_names)
		).values('product__category_id').annotate(
			total=Coalesce(Sum('quantity'), Value(0), output_field=DecimalField()),
		)
	}

	categories = Category.objects.filter(is_active=True, cafe=cafe).order_by('id')
	bubbles_data = []
	total_expense = Decimal('0.00')

	for category in categories:
		current_expense = current_by_cat.get(category.id, Decimal('0.00'))
		previous_expense = previous_by_cat.get(category.id, Decimal('0.00'))
		item_count = item_count_by_cat.get(category.id, Decimal('0.00'))
		request_count = request_count_by_cat.get(category.id, 0)
		change_percent = calculate_change_percent(current_expense, previous_expense)

		if current_expense > 0 or previous_expense > 0 or request_count > 0 or item_count > 0:
			bubbles_data.append({
				'id': category.id,
				'name': category.name,
				'value': current_expense,
				'change_percent': change_percent,
				'previous_value': previous_expense,
				'item_count': int(item_count),
				'request_count': int(request_count),
			})
			total_expense += current_expense

	response_data = {
		'department_id': department_id,
		'department_name': requester.get_name_display(),
		'bubbles': bubbles_data,
		'total_expense': total_expense,
		'period': period,
	}

	serializer = CategoryExpenseSerializer(data=response_data)
	if serializer.is_valid():
		return Response(serializer.data)
	return Response(serializer.errors, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def product_list_api(request, department_id, category_id):
	"""Product list for a category in a department."""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	period = _normalize_period(request.GET.get('period'), 'weekly')
	current_start, current_end, previous_start, previous_end = get_date_range(period)

	try:
		requester = Requester.objects.get(id=department_id, cafe=cafe)
		category = Category.objects.get(id=category_id, cafe=cafe)
	except (Requester.DoesNotExist, Category.DoesNotExist):
		return Response({'error': 'Department or Category not found'}, status=404)

	products = Product.objects.filter(category=category, is_active=True, cafe=cafe).select_related('unit').order_by('id')
	products_data = []
	total_expense = Decimal('0.00')

	for product in products:
		current_items_qs = _invoice_items_for_period(start=current_start, end=current_end, cafe=cafe).filter(
			product=product
		).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		)

		current_data = current_items_qs.aggregate(
			total_expense=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField()),
			total_quantity=Coalesce(Sum('quantity'), Value(0), output_field=DecimalField()),
		)

		current_expense = current_data['total_expense'] or Decimal('0.00')
		current_quantity = current_data['total_quantity'] or Decimal('0.00')

		previous_expense = _invoice_items_for_period(start=previous_start, end=previous_end, end_inclusive=False, cafe=cafe).filter(
			product=product
		).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		).aggregate(
			total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField())
		)['total'] or Decimal('0.00')

		change_percent = calculate_change_percent(current_expense, previous_expense)
		average_price = (current_expense / current_quantity) if current_quantity > 0 else Decimal('0.00')

		if current_expense > 0 or previous_expense > 0:
			products_data.append({
				'id': product.id,
				'name': product.name,
				'category_name': category.name,
				'total_expense': float(current_expense),
				'change_percent': float(change_percent),
				'quantity': float(current_quantity),
				'unit': product.unit.name if getattr(product, 'unit', None) else '-',
				'average_price': float(average_price),
			})
			total_expense += current_expense

	products_data.sort(key=lambda x: x['total_expense'], reverse=True)

	response_data = {
		'category_id': category_id,
		'category_name': category.name,
		'products': products_data,
		'total_expense': total_expense,
		'period': period,
	}

	serializer = ProductListResponseSerializer(data=response_data)
	if serializer.is_valid():
		return Response(serializer.data)
	return Response(serializer.errors, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_department_expenses(request):
	"""
	API برگرداندن هزینه‌های بخش‌ها برای نمودار حبابی
	با پشتیبانی از Cache
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	period = _normalize_period(request.GET.get('period'), 'weekly')

	# بررسی cache با cafe_id
	cache_key = get_cache_key('dept_expenses', period=period, cafe_id=cafe.id)
	cached_data = cache.get(cache_key)

	if cached_data is not None:
		return Response(cached_data)

	# محاسبه داده‌ها
	start, end, prev_start, prev_end = get_date_range(period)

	requesters = Requester.objects.filter(cafe=cafe).order_by('name')
	departments = []
	current_items_base = _invoice_items_for_period(start=start, end=end, cafe=cafe)
	previous_items_base = _invoice_items_for_period(
		start=prev_start, end=prev_end, end_inclusive=False, cafe=cafe
	)

	for req in requesters:
		dept_filter = _dept_items_filter(req)
		current = current_items_base.filter(dept_filter).aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')

		previous = previous_items_base.filter(dept_filter).aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')

		# محاسبه رشد
		growth_rate = calculate_growth_rate(float(current), float(previous))
		color = get_color_by_growth(growth_rate)

		departments.append({
			'id': req.id,
			'name': req.get_name_display(),
			'current_expense': float(current),
			'previous_expense': float(previous),
			'growth_rate': round(growth_rate, 2),
			'color': color
		})

	# ذخیره در cache برای 5 دقیقه
	response_data = {'departments': departments}
	cache.set(cache_key, response_data, 60 * 5)

	return Response(response_data)


def _get_category_expense(category, department, start, end, include_subcategories=True, cafe=None, end_inclusive=True):
	"""
	محاسبه هزینه یک دسته‌بندی با یا بدون زیردسته‌ها
	"""
	items = _invoice_items_for_period(start=start, end=end, end_inclusive=end_inclusive, cafe=cafe)

	if include_subcategories:
		subcategory_ids = list(category.subcategories.values_list('id', flat=True))
		category_ids = [category.id] + subcategory_ids
		items = items.filter(product__category_id__in=category_ids)
	else:
		items = items.filter(product__category=category)

	items = items.filter(_dept_items_filter(department))

	return items.aggregate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	)['total'] or Decimal('0.00')


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_department_categories(request, dept_id):
	"""
	API برگرداندن هزینه‌های دسته‌بندی‌های اصلی (بدون والد) برای یک بخش خاص
	با پشتیبانی از Cache
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	try:
		department = Requester.objects.get(id=dept_id, cafe=cafe)
	except Requester.DoesNotExist:
		return Response({'error': 'بخش یافت نشد'}, status=404)

	period = _normalize_period(request.GET.get('period'), 'weekly')

	cache_key = get_cache_key('dept_main_categories', dept_id=dept_id, period=period, cafe_id=cafe.id)
	cached_data = cache.get(cache_key)

	if cached_data is not None:
		return Response(cached_data)

	start, end, prev_start, prev_end = get_date_range(period)

	main_categories = Category.objects.filter(
		parent__isnull=True, is_active=True, cafe=cafe
	).order_by('name')
	categories = []

	for cat in main_categories:
		current = _get_category_expense(
			cat, department, start, end, include_subcategories=True, cafe=cafe, end_inclusive=True
		)
		previous = _get_category_expense(
			cat, department, prev_start, prev_end, include_subcategories=True, cafe=cafe, end_inclusive=False
		)

		subcategory_count = cat.subcategories.filter(is_active=True, cafe=cafe).count()

		if current > 0 or previous > 0:
			growth_rate = calculate_growth_rate(float(current), float(previous))
			color = get_color_by_growth(growth_rate)

			categories.append({
				'id': cat.id,
				'name': cat.name,
				'current_expense': float(current),
				'previous_expense': float(previous),
				'growth_rate': round(growth_rate, 2),
				'color': color,
				'has_subcategories': subcategory_count > 0,
				'subcategory_count': subcategory_count
			})

	response_data = {
		'department': {
			'id': department.id,
			'name': department.get_name_display()
		},
		'categories': categories
	}
	cache.set(cache_key, response_data, 60 * 5)

	return Response(response_data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_category_subcategories(request, cat_id):
	"""
	API برگرداندن زیردسته‌های یک دسته‌بندی اصلی
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	try:
		parent_category = Category.objects.get(id=cat_id, cafe=cafe)
	except Category.DoesNotExist:
		return Response({'error': 'دسته‌بندی یافت نشد'}, status=404)

	period = _normalize_period(request.GET.get('period'), 'weekly')
	department_id, dep_err = _parse_int_param(request.GET.get('department_id'), 'department_id')
	if dep_err:
		return dep_err

	department = None
	if department_id is not None:
		try:
			department = Requester.objects.get(id=department_id, cafe=cafe)
		except Requester.DoesNotExist:
			return Response({'error': 'بخش یافت نشد'}, status=404)

	cache_key = get_cache_key(
		'cat_subcategories',
		cat_id=cat_id,
		period=period,
		dept_id=department_id if department_id is not None else 'all',
		cafe_id=cafe.id,
	)
	cached_data = cache.get(cache_key)

	if cached_data is not None:
		return Response(cached_data)

	start, end, prev_start, prev_end = get_date_range(period)

	subcategories = parent_category.subcategories.filter(is_active=True, cafe=cafe).order_by('name')
	subcats_data = []

	for subcat in subcategories:
		items = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(product__category=subcat)
		prev_items = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False, cafe=cafe).filter(product__category=subcat)

		if department:
			dept_filter = _dept_items_filter(department)
			items = items.filter(dept_filter)
			prev_items = prev_items.filter(dept_filter)

		current = items.aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')

		previous = prev_items.aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')

		if current > 0 or previous > 0:
			growth_rate = calculate_growth_rate(float(current), float(previous))
			color = get_color_by_growth(growth_rate)

			subcats_data.append({
				'id': subcat.id,
				'name': subcat.name,
				'current_expense': float(current),
				'previous_expense': float(previous),
				'growth_rate': round(growth_rate, 2),
				'color': color
			})

	direct_items = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(product__category=parent_category)
	if department:
		direct_items = direct_items.filter(_dept_items_filter(department))
	direct_expense = direct_items.aggregate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	)['total'] or Decimal('0.00')

	response_data = {
		'parent_category': {
			'id': parent_category.id,
			'name': parent_category.name
		},
		'subcategories': subcats_data,
		'has_direct_products': float(direct_expense) > 0,
		'direct_expense': float(direct_expense)
	}
	cache.set(cache_key, response_data, 60 * 5)

	return Response(response_data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_category_products(request, cat_id):
	"""
	API برگرداندن لیست محصولات یک دسته‌بندی
	با پشتیبانی از Cache
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	try:
		category = Category.objects.get(id=cat_id, cafe=cafe)
	except Category.DoesNotExist:
		return Response({'error': 'دسته‌بندی یافت نشد'}, status=404)

	period = _normalize_period(request.GET.get('period'), 'weekly')
	department_id, dep_err = _parse_int_param(request.GET.get('department_id'), 'department_id')
	if dep_err:
		return dep_err

	department = None
	if department_id is not None:
		try:
			department = Requester.objects.get(id=department_id, cafe=cafe)
		except Requester.DoesNotExist:
			return Response({'error': 'بخش یافت نشد'}, status=404)

	cache_key = get_cache_key(
		'cat_products',
		cat_id=cat_id,
		period=period,
		dept_id=department_id if department_id is not None else 'all',
		cafe_id=cafe.id,
	)
	cached_data = cache.get(cache_key)
	if cached_data is not None:
		return Response(cached_data)

	start, end, prev_start, prev_end = get_date_range(period)

	items_qs = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(
		product__category=category
	)
	prev_base_qs = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False, cafe=cafe).filter(
		product__category=category
	)

	if department is not None:
		dept_filter = _dept_items_filter(department)
		items_qs = items_qs.filter(dept_filter)
		prev_base_qs = prev_base_qs.filter(dept_filter)

	products_data = []
	total_expense = Decimal('0.00')

	products = Product.objects.filter(category=category, cafe=cafe)

	for product in products:
		product_items = items_qs.filter(product=product)

		expense = product_items.aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')

		quantity = product_items.aggregate(
			total=Coalesce(Sum('quantity'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')

		prev_expense = prev_base_qs.filter(product=product).aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')

		if expense > 0 or prev_expense > 0:
			unit_price = (expense / quantity) if quantity > 0 else Decimal('0.00')
			growth_rate = calculate_growth_rate(float(expense), float(prev_expense))
			color = get_color_by_growth(growth_rate)

			last_item = product_items.select_related('invoice__supplier').order_by('-invoice__created_at', '-id').first()
			last_date = last_item.invoice.created_at if last_item else None
			supplier_name = last_item.invoice.supplier.name if last_item and last_item.invoice and last_item.invoice.supplier else '-'

			products_data.append({
				'id': product.id,
				'name': product.name,
				'quantity': float(quantity),
				'unit_price': float(unit_price),
				'total_price': float(expense),
				'previous_price': float(prev_expense),
				'growth_rate': round(growth_rate, 2),
				'color': color,
				'supplier': supplier_name,
				'date': last_date.strftime('%Y-%m-%d') if last_date else ''
			})

			total_expense += expense

	products_data.sort(key=lambda x: x['growth_rate'], reverse=True)

	response_data = {
		'category': {
			'id': category.id,
			'name': category.name
		},
		'products': products_data,
		'total_expense': float(total_expense)
	}
	cache.set(cache_key, response_data, 60 * 5)

	return Response(response_data)
