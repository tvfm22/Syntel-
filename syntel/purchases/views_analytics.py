
from datetime import timedelta, datetime
from decimal import Decimal
from typing import Tuple

from django.db.models import DecimalField, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response

from .models import Category, InvoiceItem, Product, PurchaseItem, Requester, Supplier
from .serializers_dashboard import (
	CategoryExpenseSerializer,
	DepartmentExpenseSerializer,
	ProductListResponseSerializer,
)
from .views import get_cafe_from_request


INVOICE_STATUS_INCLUDED = [
	# Include invoices that represent real financial activity
	'sent', 'approved', 'partially_paid', 'paid', 'overdue'
]


def _invoice_items_for_period(
    *,
    start: datetime,
    end: datetime,
    end_inclusive: bool = True,
    cafe=None,
) -> QuerySet:
	created_at_filter = {
		'invoice__created_at__gte': start,
		('invoice__created_at__lte' if end_inclusive else 'invoice__created_at__lt'): end,
		'invoice__status__in': INVOICE_STATUS_INCLUDED,
	}
	if cafe:
		created_at_filter['invoice__cafe'] = cafe
	return InvoiceItem.objects.filter(**created_at_filter)


def get_date_range(period: str = 'weekly') -> Tuple[datetime, datetime, datetime, datetime]:
	"""Return (current_start, current_end, previous_start, previous_end).
	
	بازه زمانی از امروز (انتهای روز) تا گذشته محاسبه می‌شود:
	- هفتگی: از 7 روز پیش تا امروز
	- ماهانه: از 30 روز پیش تا امروز
	- فصلی: از 90 روز پیش تا امروز
	- سالانه: از 365 روز پیش تا امروز
	
	بازه قبلی برای مقایسه نیز به همین ترتیب محاسبه می‌شود.
	"""
	now = timezone.now()
	# انتهای بازه فعلی = انتهای امروز (23:59:59)
	current_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

	if period == 'weekly':
		delta = timedelta(days=7)
	elif period == 'monthly':
		delta = timedelta(days=30)
	elif period == 'quarterly':
		delta = timedelta(days=90)
	elif period == 'yearly':
		delta = timedelta(days=365)
	else:
		delta = timedelta(days=7)

	# ابتدای بازه فعلی = از delta روز قبل (ابتدای روز)
	current_start = (now - delta).replace(hour=0, minute=0, second=0, microsecond=0)
	
	# بازه قبلی برای مقایسه
	previous_end = current_start - timedelta(microseconds=1)  # لحظه قبل از شروع بازه فعلی
	previous_start = (previous_end - delta).replace(hour=0, minute=0, second=0, microsecond=0)
	
	return current_start, current_end, previous_start, previous_end


def calculate_change_percent(current_value, previous_value):
	"""Calculate percentage change between two values."""
	current_value = current_value or Decimal('0.00')
	previous_value = previous_value or Decimal('0.00')

	if previous_value == 0:
		if current_value > 0:
			return Decimal('100.00')
		return Decimal('0.00')

	change = ((current_value - previous_value) / previous_value) * 100
	return Decimal(str(round(change, 2)))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def department_expenses_api(request) -> Response:
	"""Bubble data per department.

	X = sum of requested quantities (PurchaseItem.quantity)
	Y = sum of purchased quantities (InvoiceItem.quantity)
	value = sum of expenses (InvoiceItem.line_total)
	"""
	period = request.GET.get('period', 'weekly')
	current_start, current_end, previous_start, previous_end = get_date_range(period)
	cafe = get_cafe_from_request(request)

	requester_qs = Requester.objects.all()
	if cafe:
		requester_qs = requester_qs.filter(cafe=cafe)
	requesters = requester_qs.order_by('id')
	bubbles_data = []
	total_expense = Decimal('0.00')

	for requester in requesters:
		current_items_qs = _invoice_items_for_period(start=current_start, end=current_end, cafe=cafe).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		)
		previous_items_qs = _invoice_items_for_period(start=previous_start, end=previous_end, end_inclusive=False, cafe=cafe).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		)

		current_expense = current_items_qs.aggregate(
			total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField())
		)['total'] or Decimal('0.00')

		previous_expense = previous_items_qs.aggregate(
			total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField())
		)['total'] or Decimal('0.00')

		change_percent = calculate_change_percent(current_expense, previous_expense)

		requested_items_count = PurchaseItem.objects.filter(
			request__user__role=requester.name,
			request__created_at__gte=current_start,
			request__created_at__lte=current_end,
		).aggregate(total=Coalesce(Sum('quantity'), Value(0)))['total'] or 0

		purchased_items_count = current_items_qs.aggregate(
			total=Coalesce(Sum('quantity'), Value(0), output_field=DecimalField())
		)['total'] or Decimal('0.00')

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
@permission_classes([IsAuthenticated])
def category_expenses_api(request, department_id):
	"""Bubble data per category for a department."""
	period = request.GET.get('period', 'weekly')
	current_start, current_end, previous_start, previous_end = get_date_range(period)
	cafe = get_cafe_from_request(request)

	try:
		requester = Requester.objects.get(id=department_id)
	except Requester.DoesNotExist:
		return Response({'error': 'Department not found'}, status=404)

	category_qs = Category.objects.filter(is_active=True)
	if cafe:
		category_qs = category_qs.filter(cafe=cafe)
	categories = category_qs.order_by('id')
	bubbles_data = []
	total_expense = Decimal('0.00')

	for category in categories:
		current_items_qs = _invoice_items_for_period(start=current_start, end=current_end, cafe=cafe).filter(
			product__category=category
		).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		)
		previous_items_qs = _invoice_items_for_period(start=previous_start, end=previous_end, end_inclusive=False, cafe=cafe).filter(
			product__category=category
		).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		)

		current_expense = current_items_qs.aggregate(
			total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField())
		)['total'] or Decimal('0.00')

		previous_expense = previous_items_qs.aggregate(
			total=Coalesce(Sum('line_total'), Value(0), output_field=DecimalField())
		)['total'] or Decimal('0.00')

		change_percent = calculate_change_percent(current_expense, previous_expense)

		item_count = current_items_qs.aggregate(
			total=Coalesce(Sum('quantity'), Value(0), output_field=DecimalField())
		)['total'] or Decimal('0.00')

		request_count = PurchaseItem.objects.filter(
			request__user__role=requester.name,
			request__created_at__gte=current_start,
			request__created_at__lte=current_end,
			product__category=category,
		).aggregate(total=Coalesce(Sum('quantity'), Value(0)))['total'] or 0

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
@permission_classes([IsAuthenticated])
def product_list_api(request, department_id, category_id):
	"""Product list for a category in a department."""
	period = request.GET.get('period', 'weekly')
	current_start, current_end, previous_start, previous_end = get_date_range(period)
	cafe = get_cafe_from_request(request)

	try:
		requester = Requester.objects.get(id=department_id)
		category = Category.objects.get(id=category_id)
	except (Requester.DoesNotExist, Category.DoesNotExist):
		return Response({'error': 'Department or Category not found'}, status=404)

	product_qs = Product.objects.filter(category=category, is_active=True)
	if cafe:
		product_qs = product_qs.filter(cafe=cafe)
	products = product_qs.order_by('id')
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


# ============================================================================
# New Bubble Chart APIs with Caching
# ============================================================================

from django.core.cache import cache
from django.views.decorators.cache import cache_page

def calculate_growth_rate(current, previous):
	"""محاسبه نرخ رشد به درصد"""
	if previous == 0:
		return 100.0 if current > 0 else 0.0
	return ((current - previous) / previous) * 100


def get_color_by_growth(growth_rate):
	"""تولید رنگ بر اساس نرخ رشد"""
	if growth_rate > 0:
		# قرمز برای رشد
		intensity = min(growth_rate / 100, 1.0)
		green = int(99 - intensity * 99)
		blue = int(132 - intensity * 132)
		return f'rgba(255, {green}, {blue}, 0.8)'
	elif growth_rate < 0:
		# سبز برای کاهش
		intensity = min(abs(growth_rate) / 100, 1.0)
		red = int(75 - intensity * 75)
		blue = int(192 - intensity * 92)
		return f'rgba({red}, 192, {blue}, 0.8)'
	else:
		# خاکستری برای بدون تغییر
		return 'rgba(201, 203, 207, 0.8)'


def get_cache_key(prefix, **kwargs):
	"""تولید cache key یکتا"""
	key_parts = [prefix]
	for k, v in sorted(kwargs.items()):
		key_parts.append(f"{k}:{v}")
	return "_".join(key_parts)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_department_expenses(request):
	"""
	API برگرداندن هزینه‌های بخش‌ها برای نمودار حبابی
	با پشتیبانی از Cache
	"""
	period = request.GET.get('period', 'weekly')
	
	# دریافت کافه از request
	cafe = get_cafe_from_request(request)
	
	# بررسی cache با cafe_id
	cache_key = get_cache_key('dept_expenses', period=period, cafe_id=cafe.id if cafe else None)
	cached_data = cache.get(cache_key)
	
	if cached_data is not None:
		return Response(cached_data)
	
	# محاسبه داده‌ها
	start, end, prev_start, prev_end = get_date_range(period)
	
	# بهینه‌سازی: یک بار requesters را بگیر با فیلتر cafe
	requester_qs = Requester.objects.all()
	if cafe:
		requester_qs = requester_qs.filter(cafe=cafe)
	requesters = requester_qs.order_by('name')
	departments = []
	
	for req in requesters:
		# بهینه‌سازی: استفاده از select_related
		# دسترسی به requester از طریق InvoiceItem مستقیم یا از طریق مسیر invoice -> purchase_order -> request -> user -> role
		current_items = _invoice_items_for_period(start=start, end=end, cafe=cafe)
		current = current_items.filter(
			Q(requester=req) | 
			Q(invoice__purchase_order__request__user__role=req.name)
		).aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')
		
		previous_items = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False, cafe=cafe)
		previous = previous_items.filter(
			Q(requester=req) | 
			Q(invoice__purchase_order__request__user__role=req.name)
		).aggregate(
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


def _get_category_expense(category, department, start, end, include_subcategories=True, cafe=None):
	"""
	محاسبه هزینه یک دسته‌بندی با یا بدون زیردسته‌ها
	"""
	# گرفتن همه آیتم‌های فاکتور برای این بازه
	items = _invoice_items_for_period(start=start, end=end, cafe=cafe)
	
	if include_subcategories:
		# شامل زیردسته‌ها
		subcategory_ids = list(category.subcategories.values_list('id', flat=True))
		category_ids = [category.id] + subcategory_ids
		items = items.filter(product__category_id__in=category_ids)
	else:
		# فقط این دسته‌بندی
		items = items.filter(product__category=category)
	
	# فیلتر بر اساس بخش
	items = items.filter(
		Q(requester=department) | 
		Q(invoice__purchase_order__request__user__role=department.name)
	)
	
	return items.aggregate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	)['total'] or Decimal('0.00')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_department_categories(request, dept_id):
	"""
	API برگرداندن هزینه‌های دسته‌بندی‌های اصلی (بدون والد) برای یک بخش خاص
	با پشتیبانی از Cache
	"""
	try:
		department = Requester.objects.get(id=dept_id)
	except Requester.DoesNotExist:
		return Response({'error': 'بخش یافت نشد'}, status=404)
	
	period = request.GET.get('period', 'weekly')
	
	# بررسی cache
	cache_key = get_cache_key('dept_main_categories', dept_id=dept_id, period=period)
	cached_data = cache.get(cache_key)
	
	if cached_data is not None:
		return Response(cached_data)
	
	start, end, prev_start, prev_end = get_date_range(period)
	
	# فقط دسته‌بندی‌های اصلی (بدون والد)
	cafe = get_cafe_from_request(request)
	main_categories = Category.objects.filter(parent__isnull=True, is_active=True)
	if cafe:
		main_categories = main_categories.filter(cafe=cafe)
	main_categories = main_categories.order_by('name')
	categories = []
	
	for cat in main_categories:
		# محاسبه هزینه شامل زیردسته‌ها
		current = _get_category_expense(cat, department, start, end, include_subcategories=True, cafe=cafe)
		previous = _get_category_expense(cat, department, prev_start, prev_end, include_subcategories=True, cafe=cafe)
		
		# بررسی تعداد زیردسته‌ها
		subcategory_count = cat.subcategories.filter(is_active=True).count()
		
		# فقط دسته‌بندی‌هایی که هزینه دارند
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
	
	# ذخیره در cache
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
@permission_classes([IsAuthenticated])
def api_category_subcategories(request, cat_id):
	"""
	API برگرداندن زیردسته‌های یک دسته‌بندی اصلی
	"""
	try:
		parent_category = Category.objects.get(id=cat_id)
	except Category.DoesNotExist:
		return Response({'error': 'دسته‌بندی یافت نشد'}, status=404)
	
	period = request.GET.get('period', 'weekly')
	department_id = request.GET.get('department_id')
	
	# بررسی cache
	cache_key = get_cache_key('cat_subcategories', cat_id=cat_id, period=period, dept_id=department_id or 'all')
	cached_data = cache.get(cache_key)
	
	if cached_data is not None:
		return Response(cached_data)
	
	start, end, prev_start, prev_end = get_date_range(period)
	cafe = get_cafe_from_request(request)
	
	# گرفتن زیردسته‌ها
	subcategories = parent_category.subcategories.filter(is_active=True).order_by('name')
	
	# گرفتن department برای فیلتر
	department = None
	if department_id:
		try:
			department = Requester.objects.get(id=department_id)
		except Requester.DoesNotExist:
			pass
	
	subcats_data = []
	
	cafe = get_cafe_from_request(request)
	for subcat in subcategories:
		# محاسبه هزینه
		items = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(product__category=subcat)
		prev_items = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False, cafe=cafe).filter(product__category=subcat)
		
		if department:
			items = items.filter(
				Q(requester=department) | 
				Q(invoice__purchase_order__request__user__role=department.name)
			)
			prev_items = prev_items.filter(
				Q(requester=department) | 
				Q(invoice__purchase_order__request__user__role=department.name)
			)
		
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
	
	# همچنین محصولات مستقیم دسته‌بندی اصلی (بدون زیردسته)
	direct_items = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(product__category=parent_category)
	if department:
		direct_items = direct_items.filter(
			Q(requester=department) | 
			Q(invoice__purchase_order__request__user__role=department.name)
		)
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
@permission_classes([IsAuthenticated])
def api_category_products(request, cat_id):
	"""
	API برگرداندن لیست محصولات یک دسته‌بندی
	با پشتیبانی از Cache
	"""
	try:
		category = Category.objects.get(id=cat_id)
	except Category.DoesNotExist:
		return Response({'error': 'دسته‌بندی یافت نشد'}, status=404)
	
	period = request.GET.get('period', 'weekly')
	department_id = request.GET.get('department_id')
	
	# تولید cache_key
	cache_key = get_cache_key('cat_products', cat_id=cat_id, period=period, dept_id=department_id or 'all')
	
	start, end, prev_start, prev_end = get_date_range(period)
	cafe = get_cafe_from_request(request)
	
	# فیلتر بر اساس دسته‌بندی
	items_qs = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(
		product__category=category
	)
	
	# اگر بخش مشخص شده، فیلتر کن
	if department_id:
		try:
			department = Requester.objects.get(id=department_id)
			items_qs = items_qs.filter(
				Q(requester=department) | 
				Q(invoice__purchase_order__request__user__role=department.name)
			)
		except Requester.DoesNotExist:
			pass
	
	# گروه‌بندی بر اساس محصول
	products_data = []
	total_expense = Decimal('0.00')
	
	products = Product.objects.filter(category=category)
	
	for product in products:
		product_items = items_qs.filter(product=product)
		
		# محاسبه مجموع دوره فعلی
		expense = product_items.aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')
		
		quantity = product_items.aggregate(
			total=Coalesce(Sum('quantity'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')
		
		# محاسبه مجموع دوره قبلی برای نرخ رشد
		prev_items_qs = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False, cafe=cafe).filter(
			product__category=category,
			product=product
		)
		if department_id:
			try:
				department = Requester.objects.get(id=department_id)
				prev_items_qs = prev_items_qs.filter(
					Q(requester=department) | 
					Q(invoice__purchase_order__request__user__role=department.name)
				)
			except Requester.DoesNotExist:
				pass
		
		prev_expense = prev_items_qs.aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')
		
		if expense > 0 or prev_expense > 0:
			# محاسبه قیمت واحد میانگین
			unit_price = (expense / quantity) if quantity > 0 else Decimal('0.00')
			
			# محاسبه نرخ رشد
			growth_rate = calculate_growth_rate(float(expense), float(prev_expense))
			color = get_color_by_growth(growth_rate)
			
			# آخرین تاریخ خرید و فروشنده
			last_item = product_items.select_related('invoice__supplier').order_by('-invoice__created_at').first()
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
	
	# مرتب‌سازی بر اساس نرخ رشد (از بیشترین به کمترین)
	products_data.sort(key=lambda x: x['growth_rate'], reverse=True)
	
	# ذخیره در cache
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


# ============================================================================
# Line Chart API - روند هزینه‌ها
# ============================================================================

from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from collections import defaultdict

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_expense_trend(request):
	"""
	API برگرداندن روند هزینه‌ها برای نمودار خطی
	فیلتر بر اساس:
	- period: بازه زمانی (weekly, monthly, quarterly, yearly)
	- department_id: فیلتر بر اساس بخش (اختیاری)
	- category_id: فیلتر بر اساس دسته‌بندی (اختیاری)
	- granularity: دقت زمانی (daily, weekly, monthly)
	"""
	period = request.GET.get('period', 'monthly')
	department_id = request.GET.get('department_id')
	category_id = request.GET.get('category_id')
	granularity = request.GET.get('granularity', 'daily')
	
	# تولید cache_key
	cache_key = get_cache_key('expense_trend', period=period, dept=department_id or 'all', cat=category_id or 'all', granularity=granularity)
	cached_data = cache.get(cache_key)
	
	if cached_data is not None:
		return Response(cached_data)
	
	start, end, prev_start, prev_end = get_date_range(period)
	cafe = get_cafe_from_request(request)
	
	# فیلتر پایه
	items_qs = _invoice_items_for_period(start=start, end=end, cafe=cafe)
	
	# فیلتر بر اساس بخش
	department = None
	if department_id:
		try:
			department = Requester.objects.get(id=department_id)
			items_qs = items_qs.filter(
				Q(requester=department) | 
				Q(invoice__purchase_order__request__user__role=department.name)
			)
		except Requester.DoesNotExist:
			pass
	
	# فیلتر بر اساس دسته‌بندی
	category = None
	if category_id:
		try:
			category = Category.objects.get(id=category_id)
			items_qs = items_qs.filter(product__category=category)
		except Category.DoesNotExist:
			pass
	
	# انتخاب دقت زمانی برای گروه‌بندی
	if granularity == 'weekly':
		trunc_func = TruncWeek
	elif granularity == 'monthly':
		trunc_func = TruncMonth
	else:  # daily
		trunc_func = TruncDate
	
	# گروه‌بندی بر اساس تاریخ
	trend_data = items_qs.annotate(
		date=trunc_func('invoice__created_at')
	).values('date').annotate(
		total=Sum('line_total'),
		count=Sum('quantity')
	).order_by('date')
	
	# تبدیل به لیست برای JSON
	labels = []
	values = []
	counts = []
	
	for item in trend_data:
		if item['date']:
			labels.append(item['date'].strftime('%Y-%m-%d'))
			values.append(float(item['total']) if item['total'] else 0)
			counts.append(float(item['count']) if item['count'] else 0)
	
	# محاسبه خلاصه آماری
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
@permission_classes([IsAuthenticated])
def api_department_trend(request, dept_id):
	"""
	API برگرداندن روند هزینه‌های یک بخش خاص
	"""
	try:
		department = Requester.objects.get(id=dept_id)
	except Requester.DoesNotExist:
		return Response({'error': 'بخش یافت نشد'}, status=404)
	
	period = request.GET.get('period', 'monthly')
	granularity = request.GET.get('granularity', 'daily')
	
	cache_key = get_cache_key('dept_trend', dept_id=dept_id, period=period, granularity=granularity)
	cached_data = cache.get(cache_key)
	
	if cached_data is not None:
		return Response(cached_data)
	
	start, end, prev_start, prev_end = get_date_range(period)
	cafe = get_cafe_from_request(request)
	
	items_qs = _invoice_items_for_period(start=start, end=end, cafe=cafe).filter(
		Q(requester=department) | 
		Q(invoice__purchase_order__request__user__role=department.name)
	)
	
	# انتخاب دقت زمانی
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
@permission_classes([IsAuthenticated])
def api_comparison_trend(request):
	"""
	API برای مقایسه روند هزینه‌های بخش‌های مختلف
	"""
	period = request.GET.get('period', 'monthly')
	granularity = request.GET.get('granularity', 'daily')
	cafe = get_cafe_from_request(request)
	
	cache_key = get_cache_key('comparison_trend', period=period, granularity=granularity, cafe_id=cafe.id if cafe else None)
	cached_data = cache.get(cache_key)
	
	if cached_data is not None:
		return Response(cached_data)
	
	start, end, prev_start, prev_end = get_date_range(period)
	
	# انتخاب دقت زمانی
	if granularity == 'weekly':
		trunc_func = TruncWeek
	elif granularity == 'monthly':
		trunc_func = TruncMonth
	else:
		trunc_func = TruncDate
	
	requester_qs = Requester.objects.all()
	if cafe:
		requester_qs = requester_qs.filter(cafe=cafe)
	requesters = requester_qs.order_by('name')
	
	# رنگ‌های ثابت برای هر بخش
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
			Q(requester=req) | 
			Q(invoice__purchase_order__request__user__role=req.name)
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
	
	# مرتب‌سازی تاریخ‌ها
	sorted_dates = sorted(list(all_dates))
	
	# تکمیل داده‌های ناقص با صفر
	for ds in datasets:
		values = []
		for date in sorted_dates:
			values.append(ds['data'].get(date, 0))
		ds['data'] = values
	
	response_data = {
		'labels': sorted_dates,
		'datasets': datasets,
		'period': period,
		'granularity': granularity
	}
	
	cache.set(cache_key, response_data, 60 * 5)
	
	return Response(response_data)


@api_view(['GET'])
@permission_classes([AllowAny])
def api_invoice_subcategories(request):
	"""
	API برای دریافت لیست زیردسته‌بندی‌های یک دسته‌بندی اصلی
	GET /api/invoices/subcategories/?category_id=X
	"""
	if not request.user.is_authenticated:
		return Response({'error': 'Authentication required'}, status=401)
	category_id = request.GET.get('category_id')
	
	if not category_id:
		return Response({'error': 'category_id is required'}, status=400)
	
	try:
		parent_category = Category.objects.get(id=category_id, is_active=True)
	except Category.DoesNotExist:
		return Response({'error': 'Category not found'}, status=404)
	
	# دریافت زیردسته‌ها
	subcategories = Category.objects.filter(
		parent=parent_category,
		is_active=True
	).values('id', 'name').order_by('name')
	
	return Response({
		'parent_category': {
			'id': parent_category.id,
			'name': parent_category.name
		},
		'subcategories': list(subcategories)
	})


@api_view(['GET'])
@permission_classes([AllowAny])
def api_invoice_suppliers(request):
	"""
	API برای دریافت لیست فروشندگان فیلتر شده بر اساس زیردسته و تاریخ
	GET /api/invoices/suppliers/?subcategory_id=Y&start_date=...&end_date=...
	"""
	if not request.user.is_authenticated:
		return Response({'error': 'Authentication required'}, status=401)
	from .models import Supplier
	from django.db.models import Count
	
	subcategory_id = request.GET.get('subcategory_id')
	start_date = request.GET.get('start_date')
	end_date = request.GET.get('end_date')
	
	if not subcategory_id:
		return Response({'error': 'subcategory_id is required'}, status=400)
	
	# فیلتر اولیه: فاکتورهایی که محصولاتشان در این زیردسته هستند
	invoice_items_qs = InvoiceItem.objects.filter(
		product__category_id=subcategory_id,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	)
	
	# اعمال فیلتر تاریخ
	if start_date:
		invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__gte=start_date)
	if end_date:
		invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__lte=end_date)
	
	# دریافت فروشندگان منحصر به فرد
	supplier_ids = invoice_items_qs.filter(
		invoice__supplier__isnull=False
	).values_list('invoice__supplier_id', flat=True).distinct()
	
	cafe = get_cafe_from_request(request)
	suppliers = Supplier.objects.filter(
		id__in=supplier_ids,
		status='active'
	)
	if cafe:
		suppliers = suppliers.filter(cafe=cafe)
	suppliers = suppliers.values('id', 'name', 'supplier_code').order_by('name')
	
	return Response({
		'suppliers': list(suppliers),
		'count': len(suppliers)
	})


@api_view(['GET'])
@permission_classes([AllowAny])
def api_product_purchase_history(request):
	"""
	API برای دریافت تاریخچه خرید یک محصول (برای نمایش و نمودار)
	GET /api/invoices/product-history/?product_id=Z&supplier_id=X&start_date=...&end_date=...
	"""
	if not request.user.is_authenticated:
		return Response({'error': 'Authentication required'}, status=401)
	product_id = request.GET.get('product_id')
	supplier_id = request.GET.get('supplier_id')
	start_date = request.GET.get('start_date')
	end_date = request.GET.get('end_date')
	
	if not product_id:
		return Response({'error': 'product_id is required'}, status=400)
	
	# فیلتر اولیه
	history_qs = InvoiceItem.objects.filter(
		product_id=product_id,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	).select_related('invoice', 'invoice__supplier')
	
	# فیلتر براساس فروشنده (اگر ارسال شده باشد)
	if supplier_id:
		history_qs = history_qs.filter(invoice__supplier_id=supplier_id)
	
	# اعمال فیلتر تاریخ
	if start_date:
		history_qs = history_qs.filter(invoice__invoice_date__gte=start_date)
	if end_date:
		history_qs = history_qs.filter(invoice__invoice_date__lte=end_date)
	
	# همه خریدها برای نمودار (مرتب شده از قدیمی به جدید)
	all_purchases = history_qs.order_by('invoice__invoice_date')
	
	# 3 آخرین خرید برای نمایش
	latest_purchases = history_qs.order_by('-invoice__invoice_date')[:3]
	
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
	
	# تمام داده‌ها برای نمودار
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
@permission_classes([IsAuthenticated])
def api_category_analysis_stats(request):
	"""API برای دریافت آمار کلی تحلیل دسته‌بندی‌ها"""
	cafe = get_cafe_from_request(request)
	if not cafe:
		return Response({'error': 'Cafe not found'}, status=400)
	
	# دریافت فیلترها
	category_id = request.GET.get('category_id')
	supplier_id = request.GET.get('supplier_id')
	days = int(request.GET.get('days', 30))
	
	# محاسبه بازه زمانی
	end_date = timezone.now()
	start_date = end_date - timedelta(days=days)
	
	# فیلتر اصلی
	items_qs = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	)
	
	# اعمال فیلترها
	if category_id:
		items_qs = items_qs.filter(product__category_id=category_id)
	if supplier_id:
		items_qs = items_qs.filter(invoice__supplier_id=supplier_id)
	
	# محاسبه آمار
	total_amount = items_qs.aggregate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	)['total']
	
	# تعداد دسته‌بندی های منحصر به فرد
	total_categories = items_qs.values('product__category').distinct().count()
	
	# تعداد محصولات منحصر به فرد
	total_products = items_qs.values('product').distinct().count()
	
	# پرهزینه‌ترین دسته
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
@permission_classes([IsAuthenticated])
def api_category_analysis_data(request):
	"""API برای دریافت داده‌های جزئی تحلیل دسته‌بندی‌ها"""
	cafe = get_cafe_from_request(request)
	if not cafe:
		return Response({'error': 'Cafe not found'}, status=400)
	
	# دریافت فیلترها
	category_id = request.GET.get('category_id')
	supplier_id = request.GET.get('supplier_id')
	days = int(request.GET.get('days', 30))
	
	# محاسبه بازه زمانی
	end_date = timezone.now()
	start_date = end_date - timedelta(days=days)
	
	# فیلتر اصلی
	items_qs = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	)
	
	# اعمال فیلترها
	if category_id:
		items_qs = items_qs.filter(product__category_id=category_id)
	if supplier_id:
		items_qs = items_qs.filter(invoice__supplier_id=supplier_id)
	
	# محاسبه کل مبلغ برای درصد
	total_amount = items_qs.aggregate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	)['total'] or Decimal('0')
	
	# گروه‌بندی بر اساس دسته‌بندی
	categories_data = items_qs.values(
		'product__category__id',
		'product__category__name'
	).annotate(
		total_amount=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField())),
		product_count=Sum('quantity'),
		item_count=Sum(Value(1, output_field=DecimalField()))
	).order_by('-total_amount')
	
	# آماده‌سازی داده‌ها
	categories_list = []
	for cat_data in categories_data:
		cat_total = cat_data['total_amount'] or Decimal('0')
		product_count = cat_data['product_count'] or Decimal('0')
		item_count = cat_data['item_count'] or Decimal('1')
		
		# محاسبه درصد
		percentage = 0
		if total_amount > 0:
			percentage = float((cat_total / total_amount) * 100)
		
		# محاسبه میانگین قیمت
		avg_price = 0
		if item_count > 0:
			avg_price = float(cat_total / item_count)
		
		categories_list.append({
			'id': cat_data['product__category__id'],
			'name': cat_data['product__category__name'] or 'نامشخص',
			'total_amount': float(cat_total),
			'product_count': int(product_count),
			'percentage': round(percentage, 1),
			'avg_price': round(avg_price, 0)
		})
	
	return Response({
		'categories': categories_list
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_management_category_expenses(request):
	"""API برای دریافت هزینه‌های دسته‌بندی‌ها در داشبورد مدیریت"""
	cafe = get_cafe_from_request(request)
	if not cafe:
		return Response({'error': 'Cafe not found'}, status=400)
	
	# دریافت دوره زمانی
	period = request.GET.get('period', 'monthly')
	
	# محاسبه بازه زمانی
	end_date = timezone.now()
	if period == 'monthly':
		start_date = end_date - timedelta(days=30)
	elif period == 'quarterly':
		start_date = end_date - timedelta(days=90)
	elif period == 'yearly':
		start_date = end_date - timedelta(days=365)
	else:
		start_date = end_date - timedelta(days=30)
	
	# فیلتر اصلی
	items_qs = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	)
	
	# گروه‌بندی بر اساس دسته‌بندی
	categories_data = items_qs.values(
		'product__category__id',
		'product__category__name'
	).annotate(
		total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
	).order_by('-total')[:10]  # فقط 10 دسته برتر
	
	# آماده‌سازی داده‌ها
	categories_list = []
	for cat_data in categories_data:
		if cat_data['product__category__name']:  # فقط دسته‌هایی که نام دارند
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
@permission_classes([IsAuthenticated])
def api_management_price_increases(request):
	"""API برای دریافت محصولات با بالاترین رشد قیمت"""
	cafe = get_cafe_from_request(request)
	if not cafe:
		return Response({'error': 'Cafe not found'}, status=400)
	
	# دریافت دوره زمانی
	period = request.GET.get('period', 'monthly')
	
	# محاسبه بازه زمانی
	end_date = timezone.now()
	if period == 'monthly':
		start_date = end_date - timedelta(days=30)
	elif period == 'quarterly':
		start_date = end_date - timedelta(days=90)
	elif period == 'yearly':
		start_date = end_date - timedelta(days=365)
	else:
		start_date = end_date - timedelta(days=30)
	
	# دریافت محصولاتی که در این بازه خریداری شده‌اند
	recent_items = InvoiceItem.objects.filter(
		invoice__cafe=cafe,
		invoice__invoice_date__gte=start_date,
		invoice__invoice_date__lte=end_date,
		invoice__status__in=INVOICE_STATUS_INCLUDED
	).select_related('product', 'invoice')
	
	# محاسبه تغییرات قیمت برای هر محصول
	price_changes = {}
	
	for item in recent_items:
		product_id = item.product.id
		product_name = item.product.name
		current_price = float(item.unit_price)
		
		if product_id not in price_changes:
			# یافتن آخرین خرید قبلی (قبل از بازه فعلی)
			previous_item = InvoiceItem.objects.filter(
				product_id=product_id,
				invoice__cafe=cafe,
				invoice__invoice_date__lt=start_date,
				invoice__status__in=INVOICE_STATUS_INCLUDED
			).order_by('-invoice__invoice_date').first()
			
			if previous_item:
				old_price = float(previous_item.unit_price)
				if old_price > 0:
					increase = ((current_price - old_price) / old_price) * 100
					if increase > 0:  # فقط افزایش قیمت
						price_changes[product_id] = {
							'name': product_name,
							'old_price': old_price,
							'new_price': current_price,
							'increase_percentage': increase
						}
	
	# مرتب‌سازی و انتخاب 5 مورد برتر
	top_increases = sorted(
		price_changes.values(),
		key=lambda x: x['increase_percentage'],
		reverse=True
	)[:5]
	
	return Response({
		'products': top_increases,
		'period': period
	})


# ---------------------------------------------------------------------------
# Finance Manager Dashboard APIs
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_finance_stats(request):
	"""API برای دریافت آمار مالی در داشبورد مدیر مالی"""
	cafe = get_cafe_from_request(request)
	if not cafe:
		return Response({'error': 'Cafe not found'}, status=400)
	
	invoice_qs = Invoice.objects.filter(cafe=cafe)
	supplier_qs = Supplier.objects.filter(cafe=cafe)
	
	stats = {
		"total_invoices": invoice_qs.count(),
		"total_payments": Payment.objects.filter(invoice__cafe=cafe).count(),
		"pending_invoices": invoice_qs.filter(status='pending').count(),
		"unpaid_invoices": invoice_qs.filter(payment_status='unpaid').count(),
		"total_suppliers": supplier_qs.count(),
	}
	
	return Response(stats)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_finance_recent_payments(request):
	"""API برای دریافت پرداخت‌های اخیر"""
	cafe = get_cafe_from_request(request)
	if not cafe:
		return Response({'error': 'Cafe not found'}, status=400)
	
	recent_payments = Payment.objects.filter(
		invoice__cafe=cafe
	).select_related('invoice', 'payment_type').order_by('-payment_date')[:10]
	
	payments_data = []
	for payment in recent_payments:
		payments_data.append({
			'id': payment.id,
			'amount': float(payment.amount),
			'payment_date': payment.payment_date.strftime('%Y-%m-%d'),
			'invoice_number': payment.invoice.invoice_number,
			'payment_type': payment.payment_type.name if payment.payment_type else 'نامشخص',
		})
	
	return Response({'payments': payments_data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_finance_pending_invoices(request):
	"""API برای دریافت فاکتورهای در انتظار پرداخت"""
	cafe = get_cafe_from_request(request)
	if not cafe:
		return Response({'error': 'Cafe not found'}, status=400)
	
	pending_invoices = Invoice.objects.filter(
		cafe=cafe,
		payment_status='unpaid'
	).select_related('supplier').order_by('-invoice_date')[:10]
	
	invoices_data = []
	for invoice in pending_invoices:
		invoices_data.append({
			'id': invoice.id,
			'invoice_number': invoice.invoice_number,
			'supplier_name': invoice.supplier.name,
			'total_amount': float(invoice.total_amount),
			'invoice_date': invoice.invoice_date.strftime('%Y-%m-%d'),
		})
	
	return Response({'invoices': invoices_data})
