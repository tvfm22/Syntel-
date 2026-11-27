from __future__ import annotations

from ..models import Category
from ..models import Invoice
from ..models import InvoiceItem
from ..models import Payment
from ..models import Product
from ..models import Requester
from ..models import Supplier
from .analytics_helpers import HasAnalyticsRole
from .analytics_helpers import INVOICE_STATUS_INCLUDED
from .analytics_helpers import _dept_items_filter
from .analytics_helpers import _invoice_items_for_period
from .analytics_helpers import _normalize_granularity
from .analytics_helpers import _normalize_period
from .analytics_helpers import _parse_int_param
from .analytics_helpers import _require_cafe_or_error
from .analytics_helpers import get_cache_key
from .analytics_helpers import get_date_range
from .helpers import _parse_iso_date
from datetime import timedelta
from decimal import Decimal
from django.core.cache import cache
from django.db.models import DecimalField
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDate
from django.db.models.functions import TruncMonth
from django.db.models.functions import TruncWeek
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_expense_trend(request):
	"""
	API برگرداندن روند هزینه‌ها برای نمودار خطی
	فیلتر بر اساس:
	- period: بازه زمانی (weekly, monthly, quarterly, yearly)
	- department_id: فیلتر بر اساس بخش (اختیاری)
	- category_id: فیلتر بر اساس دسته‌بندی (اختیاری)
	- granularity: دقت زمانی (daily, weekly, monthly)
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	period = _normalize_period(request.GET.get('period'), 'monthly')
	granularity = _normalize_granularity(request.GET.get('granularity'), 'daily')

	department_id, dep_err = _parse_int_param(request.GET.get('department_id'), 'department_id')
	if dep_err:
		return dep_err
	category_id, cat_err = _parse_int_param(request.GET.get('category_id'), 'category_id')
	if cat_err:
		return cat_err

	department = None
	if department_id is not None:
		try:
			department = Requester.objects.get(id=department_id, cafe=cafe)
		except Requester.DoesNotExist:
			return Response({'error': 'بخش یافت نشد'}, status=404)

	category = None
	if category_id is not None:
		try:
			category = Category.objects.get(id=category_id, cafe=cafe)
		except Category.DoesNotExist:
			return Response({'error': 'دسته‌بندی یافت نشد'}, status=404)

	cache_key = get_cache_key(
		'expense_trend',
		period=period,
		dept=department_id if department_id is not None else 'all',
		cat=category_id if category_id is not None else 'all',
		granularity=granularity,
		cafe_id=cafe.id,
	)
	cached_data = cache.get(cache_key)
	if cached_data is not None:
		return Response(cached_data)

	start, end, prev_start, prev_end = get_date_range(period)

	items_qs = _invoice_items_for_period(start=start, end=end, cafe=cafe)
	if department is not None:
		items_qs = items_qs.filter(_dept_items_filter(department))
	if category is not None:
		items_qs = items_qs.filter(product__category=category)

	if granularity == 'weekly':
		trunc_func = TruncWeek
	elif granularity == 'monthly':
		trunc_func = TruncMonth
	else:
		trunc_func = TruncDate

	trend_data = items_qs.annotate(
		date=trunc_func('invoice__created_at')
	).values('date').annotate(
		total=Sum('line_total'),
		count=Sum('quantity')
	).order_by('date')

	labels = []
	values = []
	counts = []
	for item in trend_data:
		if item['date']:
			labels.append(item['date'].strftime('%Y-%m-%d'))
			values.append(float(item['total']) if item['total'] else 0)
			counts.append(float(item['count']) if item['count'] else 0)

	total_expense = sum(values)
	avg_expense = total_expense / len(values) if values else 0
	max_expense = max(values) if values else 0
	min_expense = min(values) if values else 0

	response_data = {
		'labels': labels,
		'datasets': [
			{
				'label': 'هزینه‌ها',
				'data': values,
				'borderColor': 'rgba(99, 102, 241, 1)',
				'backgroundColor': 'rgba(99, 102, 241, 0.1)',
				'fill': True,
				'tension': 0.4
			},
			{
				'label': 'تعداد اقلام',
				'data': counts,
				'borderColor': 'rgba(16, 185, 129, 1)',
				'backgroundColor': 'rgba(16, 185, 129, 0.1)',
				'fill': False,
				'tension': 0.4,
				'yAxisID': 'y1'
			}
		],
		'summary': {
			'total': total_expense,
			'average': avg_expense,
			'max': max_expense,
			'min': min_expense,
			'data_points': len(values)
		},
		'filters': {
			'period': period,
			'department': department.get_name_display() if department else 'همه بخش‌ها',
			'category': category.name if category else 'همه دسته‌ها',
			'granularity': granularity
		}
	}

	cache.set(cache_key, response_data, 60 * 5)
	return Response(response_data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_department_trend(request, dept_id):
	"""
	API برگرداندن روند هزینه‌های یک بخش خاص
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	try:
		department = Requester.objects.get(id=dept_id, cafe=cafe)
	except Requester.DoesNotExist:
		return Response({'error': 'بخش یافت نشد'}, status=404)

	period = _normalize_period(request.GET.get('period'), 'monthly')
	granularity = _normalize_granularity(request.GET.get('granularity'), 'daily')

	cache_key = get_cache_key('dept_trend', dept_id=dept_id, period=period, granularity=granularity, cafe_id=cafe.id)
	cached_data = cache.get(cache_key)
	if cached_data is not None:
		return Response(cached_data)

	start, end, prev_start, prev_end = get_date_range(period)

	items_qs = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(
		_dept_items_filter(department)
	)

	if granularity == 'weekly':
		trunc_func = TruncWeek
	elif granularity == 'monthly':
		trunc_func = TruncMonth
	else:
		trunc_func = TruncDate

	trend_data = items_qs.annotate(
		date=trunc_func('invoice__created_at')
	).values('date').annotate(
		total=Sum('line_total')
	).order_by('date')

	labels = []
	values = []
	for item in trend_data:
		if item['date']:
			labels.append(item['date'].strftime('%Y-%m-%d'))
			values.append(float(item['total']) if item['total'] else 0)

	response_data = {
		'department': {
			'id': department.id,
			'name': department.get_name_display()
		},
		'labels': labels,
		'values': values,
		'total': sum(values),
		'period': period,
		'granularity': granularity
	}

	cache.set(cache_key, response_data, 60 * 5)
	return Response(response_data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_comparison_trend(request):
	"""
	API برای مقایسه روند هزینه‌های بخش‌های مختلف
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	period = _normalize_period(request.GET.get('period'), 'monthly')
	granularity = _normalize_granularity(request.GET.get('granularity'), 'daily')

	cache_key = get_cache_key('comparison_trend', period=period, granularity=granularity, cafe_id=cafe.id)
	cached_data = cache.get(cache_key)
	if cached_data is not None:
		return Response(cached_data)

	start, end, prev_start, prev_end = get_date_range(period)

	if granularity == 'weekly':
		trunc_func = TruncWeek
	elif granularity == 'monthly':
		trunc_func = TruncMonth
	else:
		trunc_func = TruncDate

	requesters = Requester.objects.filter(cafe=cafe).order_by('name')

	colors = [
		'rgba(99, 102, 241, 1)',    # بنفش
		'rgba(16, 185, 129, 1)',    # سبز
		'rgba(239, 68, 68, 1)',     # قرمز
		'rgba(245, 158, 11, 1)',    # نارنجی
		'rgba(59, 130, 246, 1)',    # آبی
		'rgba(168, 85, 247, 1)',    # بنفش روشن
		'rgba(236, 72, 153, 1)',    # صورتی
	]

	datasets = []
	all_dates = set()

	for idx, req in enumerate(requesters):
		items_qs = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(
			_dept_items_filter(req)
		)

		trend_data = items_qs.annotate(
			date=trunc_func('invoice__created_at')
		).values('date').annotate(
			total=Sum('line_total')
		).order_by('date')

		dept_data = {}
		for item in trend_data:
			if item['date']:
				date_str = item['date'].strftime('%Y-%m-%d')
				all_dates.add(date_str)
				dept_data[date_str] = float(item['total']) if item['total'] else 0

		color = colors[idx % len(colors)]

		datasets.append({
			'department_id': req.id,
			'label': req.get_name_display(),
			'data': dept_data,
			'borderColor': color,
			'backgroundColor': color.replace('1)', '0.1)'),
			'fill': False,
			'tension': 0.4
		})

	sorted_dates = sorted(list(all_dates))
	for ds in datasets:
		ds['data'] = [ds['data'].get(d, 0) for d in sorted_dates]

	response_data = {
		'labels': sorted_dates,
		'datasets': datasets,
		'period': period,
		'granularity': granularity
	}

	cache.set(cache_key, response_data, 60 * 5)
	return Response(response_data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_invoice_subcategories(request):
	"""
	API برای دریافت لیست زیردسته‌بندی‌های یک دسته‌بندی اصلی
	GET /api/invoices/subcategories/?category_id=X
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err
	category_id, cat_err = _parse_int_param(request.GET.get('category_id'), 'category_id', required=True)
	if cat_err:
		return cat_err

	try:
		parent_category = Category.objects.get(id=category_id, is_active=True, cafe=cafe)
	except Category.DoesNotExist:
		return Response({'error': 'Category not found'}, status=404)

	subcategories = Category.objects.filter(
		parent=parent_category,
		is_active=True,
		cafe=cafe,
	).values('id', 'name').order_by('name')

	return Response({
		'parent_category': {
			'id': parent_category.id,
			'name': parent_category.name
		},
		'subcategories': list(subcategories)
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_invoice_suppliers(request):
	"""
	API برای دریافت لیست فروشندگان فیلتر شده بر اساس زیردسته و تاریخ
	GET /api/invoices/suppliers/?subcategory_id=Y&start_date=...&end_date=...
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	subcategory_id, sub_err = _parse_int_param(request.GET.get('subcategory_id'), 'subcategory_id', required=True)
	if sub_err:
		return sub_err

	start_date_raw = request.GET.get('start_date')
	end_date_raw = request.GET.get('end_date')
	start_date = _parse_iso_date(start_date_raw) if start_date_raw else None
	end_date = _parse_iso_date(end_date_raw) if end_date_raw else None
	if start_date_raw and start_date is None:
		return Response({'error': 'Invalid start_date'}, status=400)
	if end_date_raw and end_date is None:
		return Response({'error': 'Invalid end_date'}, status=400)

	invoice_items_qs = InvoiceItem.objects.filter(
		product__category_id=subcategory_id,
		product__category__cafe=cafe,
		invoice__cafe=cafe,
		invoice__status__in=INVOICE_STATUS_INCLUDED,
	)

	if start_date:
		invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__gte=start_date)
	if end_date:
		invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__lte=end_date)

	supplier_ids = invoice_items_qs.filter(
		invoice__supplier__isnull=False
	).values_list('invoice__supplier_id', flat=True).distinct()

	suppliers = list(
		Supplier.objects.filter(
			id__in=supplier_ids,
			status='active',
			cafe=cafe,
		).values('id', 'name', 'supplier_code').order_by('name')
	)

	return Response({
		'suppliers': suppliers,
		'count': len(suppliers)
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_product_purchase_history(request):
	"""
	API برای دریافت تاریخچه خرید یک محصول (برای نمایش و نمودار)
	GET /api/invoices/product-history/?product_id=Z&supplier_id=X&start_date=...&end_date=...
	"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	product_id, prod_err = _parse_int_param(request.GET.get('product_id'), 'product_id', required=True)
	if prod_err:
		return prod_err
	supplier_id, sup_err = _parse_int_param(request.GET.get('supplier_id'), 'supplier_id')
	if sup_err:
		return sup_err

	start_date_raw = request.GET.get('start_date')
	end_date_raw = request.GET.get('end_date')
	start_date = _parse_iso_date(start_date_raw) if start_date_raw else None
	end_date = _parse_iso_date(end_date_raw) if end_date_raw else None
	if start_date_raw and start_date is None:
		return Response({'error': 'Invalid start_date'}, status=400)
	if end_date_raw and end_date is None:
		return Response({'error': 'Invalid end_date'}, status=400)

	if not Product.objects.filter(id=product_id, cafe=cafe).exists():
		return Response({'error': 'Product not found'}, status=404)

	history_qs = InvoiceItem.objects.filter(
		product_id=product_id,
		invoice__cafe=cafe,
		invoice__status__in=INVOICE_STATUS_INCLUDED,
	).select_related('invoice', 'invoice__supplier')

	if supplier_id is not None:
		history_qs = history_qs.filter(invoice__supplier_id=supplier_id)
	if start_date:
		history_qs = history_qs.filter(invoice__invoice_date__gte=start_date)
	if end_date:
		history_qs = history_qs.filter(invoice__invoice_date__lte=end_date)

	all_purchases = history_qs.order_by('invoice__invoice_date', 'id')
	latest_purchases = history_qs.order_by('-invoice__invoice_date', '-id')[:3]

	history_data = []
	for item in latest_purchases:
		history_data.append({
			'quantity': float(item.quantity),
			'unit_price': float(item.unit_price),
			'line_total': float(item.line_total),
			'invoice_date': item.invoice.invoice_date.strftime('%Y-%m-%d'),
			'invoice_number': item.invoice.invoice_number,
			'supplier_name': item.invoice.supplier.name if item.invoice.supplier else item.invoice.store_name or 'نامشخص'
		})

	chart_data = []
	for item in all_purchases:
		chart_data.append({
			'quantity': float(item.quantity),
			'unit_price': float(item.unit_price),
			'line_total': float(item.line_total),
			'invoice_date': item.invoice.invoice_date.strftime('%Y-%m-%d'),
			'supplier_name': item.invoice.supplier.name if item.invoice.supplier else item.invoice.store_name or 'نامشخص'
		})

	return Response({
		'product_id': product_id,
		'supplier_id': supplier_id,
		'history': history_data,
		'chart_data': chart_data
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_category_analysis_stats(request):
	"""API برای دریافت آمار کلی تحلیل دسته‌بندی‌ها"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	category_id, cat_err = _parse_int_param(request.GET.get('category_id'), 'category_id')
	if cat_err:
		return cat_err
	supplier_id, sup_err = _parse_int_param(request.GET.get('supplier_id'), 'supplier_id')
	if sup_err:
		return sup_err
	days_raw = request.GET.get('days', '30')
	days, days_err = _parse_int_param(days_raw, 'days')
	if days_err:
		return days_err
	days = max(1, min(days or 30, 3650))

	end_date = timezone.now()
	start_date = end_date - timedelta(days=days)

	items_qs = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	)

	if category_id is not None:
		items_qs = items_qs.filter(product__category_id=category_id)
	if supplier_id is not None:
		items_qs = items_qs.filter(invoice__supplier_id=supplier_id)

	total_amount = items_qs.aggregate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	)['total']

	total_categories = items_qs.values('product__category').distinct().count()
	total_products = items_qs.values('product').distinct().count()

	top_category = None
	category_expenses = items_qs.values('product__category__id', 'product__category__name').annotate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	).order_by('-total').first()

	if category_expenses:
		top_category = {
			'id': category_expenses['product__category__id'],
			'name': category_expenses['product__category__name'] or 'نامشخص',
			'amount': float(category_expenses['total'])
		}

	return Response({
		'total_categories': total_categories,
		'total_products': total_products,
		'total_purchases': float(total_amount),
		'top_category': top_category
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_category_analysis_data(request):
	"""API برای دریافت داده‌های جزئی تحلیل دسته‌بندی‌ها"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	category_id, cat_err = _parse_int_param(request.GET.get('category_id'), 'category_id')
	if cat_err:
		return cat_err
	supplier_id, sup_err = _parse_int_param(request.GET.get('supplier_id'), 'supplier_id')
	if sup_err:
		return sup_err
	days, days_err = _parse_int_param(request.GET.get('days', '30'), 'days')
	if days_err:
		return days_err
	days = max(1, min(days or 30, 3650))

	end_date = timezone.now()
	start_date = end_date - timedelta(days=days)

	items_qs = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	)

	if category_id is not None:
		items_qs = items_qs.filter(product__category_id=category_id)
	if supplier_id is not None:
		items_qs = items_qs.filter(invoice__supplier_id=supplier_id)

	total_amount = items_qs.aggregate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	)['total'] or Decimal('0')

	categories_data = items_qs.values(
		'product__category__id',
		'product__category__name'
	).annotate(
		total_amount=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField())),
		total_qty=Coalesce(Sum('quantity'), Value(0, output_field=DecimalField())),
	).order_by('-total_amount')

	categories_list = []
	for cat_data in categories_data:
		cat_total = cat_data['total_amount'] or Decimal('0')
		total_qty = cat_data['total_qty'] or Decimal('0')

		percentage = float((cat_total / total_amount) * 100) if total_amount > 0 else 0
		avg_price = float(cat_total / total_qty) if total_qty > 0 else 0

		categories_list.append({
			'id': cat_data['product__category__id'],
			'name': cat_data['product__category__name'] or 'نامشخص',
			'total_amount': float(cat_total),
			'product_count': int(total_qty),
			'percentage': round(percentage, 1),
			'avg_price': round(avg_price, 0)
		})

	return Response({
		'categories': categories_list
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_management_category_expenses(request):
	"""API برای دریافت هزینه‌های دسته‌بندی‌ها در داشبورد مدیریت"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	period = _normalize_period(request.GET.get('period'), 'monthly')

	end_date = timezone.now()
	if period == 'weekly':
		start_date = end_date - timedelta(days=7)
	elif period == 'quarterly':
		start_date = end_date - timedelta(days=90)
	elif period == 'yearly':
		start_date = end_date - timedelta(days=365)
	else:
		start_date = end_date - timedelta(days=30)

	items_qs = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	)

	categories_data = items_qs.values(
		'product__category__id',
		'product__category__name'
	).annotate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	).order_by('-total')[:10]

	categories_list = []
	for cat_data in categories_data:
		if cat_data['product__category__name']:
			categories_list.append({
				'id': cat_data['product__category__id'],
				'name': cat_data['product__category__name'],
				'total': float(cat_data['total'])
			})

	return Response({
		'categories': categories_list,
		'period': period
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_management_price_increases(request):
	"""API برای دریافت محصولات با بالاترین رشد قیمت"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	period = _normalize_period(request.GET.get('period'), 'monthly')

	end_date = timezone.now()
	if period == 'weekly':
		start_date = end_date - timedelta(days=7)
	elif period == 'quarterly':
		start_date = end_date - timedelta(days=90)
	elif period == 'yearly':
		start_date = end_date - timedelta(days=365)
	else:
		start_date = end_date - timedelta(days=30)

	recent_items = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		product__isnull=False,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	).select_related('product', 'invoice').order_by('-invoice__invoice_date', '-id')

	price_changes = {}

	for item in recent_items:
		product_id = item.product_id
		if product_id in price_changes:
			continue
		product_name = item.product.name
		current_price = float(item.unit_price)

		previous_item = InvoiceItem.objects.filter(
			product_id=product_id,
			invoice__cafe=cafe,
			invoice__invoice_date__lt=start_date,
			invoice__status__in=INVOICE_STATUS_INCLUDED
		).order_by('-invoice__invoice_date', '-id').first()

		if previous_item:
			old_price = float(previous_item.unit_price)
			if old_price > 0:
				increase = ((current_price - old_price) / old_price) * 100
				if increase > 0:
					price_changes[product_id] = {
						'name': product_name,
						'old_price': old_price,
						'new_price': current_price,
						'increase_percentage': increase
					}

	top_increases = sorted(
		price_changes.values(),
		key=lambda x: x['increase_percentage'],
		reverse=True
	)[:5]

	return Response({
		'products': top_increases,
		'period': period
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_finance_stats(request):
	"""API برای دریافت آمار مالی در داشبورد مدیر مالی"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	invoice_qs = Invoice.objects.filter(cafe=cafe)
	supplier_qs = Supplier.objects.filter(cafe=cafe)
	payment_qs = Payment.objects.filter(
		Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe)
	).distinct()

	stats = {
		"total_invoices": invoice_qs.count(),
		"total_payments": payment_qs.count(),
		"pending_invoices": invoice_qs.filter(status__in=['draft', 'sent']).count(),
		"unpaid_invoices": invoice_qs.filter(payment_status='unpaid').count(),
		"total_suppliers": supplier_qs.count(),
	}

	return Response(stats)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_finance_recent_payments(request):
	"""API برای دریافت پرداخت‌های اخیر"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	recent_payments = Payment.objects.filter(
		Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe)
	).distinct().select_related('invoice', 'payment_location').order_by('-payment_date', '-id')[:10]

	payments_data = []
	for payment in recent_payments:
		payments_data.append({
			'id': payment.id,
			'amount': float(payment.amount),
			'payment_date': payment.payment_date.strftime('%Y-%m-%d'),
			'invoice_number': payment.invoice.invoice_number if payment.invoice else '-',
			'payment_type': payment.payment_location.name if payment.payment_location else 'نامشخص',
		})

	return Response({'payments': payments_data})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasAnalyticsRole])
def api_finance_pending_invoices(request):
	"""API برای دریافت فاکتورهای در انتظار پرداخت"""
	cafe, err = _require_cafe_or_error(request)
	if err:
		return err

	pending_invoices = Invoice.objects.filter(
		cafe=cafe,
		payment_status='unpaid'
	).select_related('supplier').order_by('-invoice_date', '-id')[:10]

	invoices_data = []
	for invoice in pending_invoices:
		supplier_name = (
			invoice.supplier.name
			if invoice.supplier
			else (invoice.store_name or '-')
		)
		invoices_data.append({
			'id': invoice.id,
			'invoice_number': invoice.invoice_number,
			'supplier_name': supplier_name,
			'total_amount': float(invoice.total_amount),
			'invoice_date': invoice.invoice_date.strftime('%Y-%m-%d'),
		})

	return Response({'invoices': invoices_data})
