
from datetime import timedelta
from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import Category, InvoiceItem, Product, PurchaseItem, Requester
from .serializers_dashboard import (
	CategoryExpenseSerializer,
	DepartmentExpenseSerializer,
	ProductListResponseSerializer,
)


INVOICE_STATUS_INCLUDED = [
	# Include invoices that represent real financial activity
	'sent', 'approved', 'partially_paid', 'paid', 'overdue'
]


def _invoice_items_for_period(*, start, end, end_inclusive=True):
	created_at_filter = {
		'invoice__created_at__gte': start,
		('invoice__created_at__lte' if end_inclusive else 'invoice__created_at__lt'): end,
		'invoice__status__in': INVOICE_STATUS_INCLUDED,
	}
	return InvoiceItem.objects.filter(**created_at_filter)


def get_date_range(period='weekly'):
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
def department_expenses_api(request):
	"""Bubble data per department.

	X = sum of requested quantities (PurchaseItem.quantity)
	Y = sum of purchased quantities (InvoiceItem.quantity)
	value = sum of expenses (InvoiceItem.line_total)
	"""
	period = request.GET.get('period', 'weekly')
	current_start, current_end, previous_start, previous_end = get_date_range(period)

	requesters = Requester.objects.all().order_by('id')
	bubbles_data = []
	total_expense = Decimal('0.00')

	for requester in requesters:
		current_items_qs = _invoice_items_for_period(start=current_start, end=current_end).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		)
		previous_items_qs = _invoice_items_for_period(start=previous_start, end=previous_end, end_inclusive=False).filter(
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

	try:
		requester = Requester.objects.get(id=department_id)
	except Requester.DoesNotExist:
		return Response({'error': 'Department not found'}, status=404)

	categories = Category.objects.filter(is_active=True).order_by('id')
	bubbles_data = []
	total_expense = Decimal('0.00')

	for category in categories:
		current_items_qs = _invoice_items_for_period(start=current_start, end=current_end).filter(
			product__category=category
		).filter(
			Q(requester=requester) |
			Q(requester__isnull=True, invoice__purchase_order__request__user__role=requester.name)
		)
		previous_items_qs = _invoice_items_for_period(start=previous_start, end=previous_end, end_inclusive=False).filter(
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

	try:
		requester = Requester.objects.get(id=department_id)
		category = Category.objects.get(id=category_id)
	except (Requester.DoesNotExist, Category.DoesNotExist):
		return Response({'error': 'Department or Category not found'}, status=404)

	products = Product.objects.filter(category=category, is_active=True).order_by('id')
	products_data = []
	total_expense = Decimal('0.00')

	for product in products:
		current_items_qs = _invoice_items_for_period(start=current_start, end=current_end).filter(
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

		previous_expense = _invoice_items_for_period(start=previous_start, end=previous_end, end_inclusive=False).filter(
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
	
	# بررسی cache
	cache_key = get_cache_key('dept_expenses', period=period)
	cached_data = cache.get(cache_key)
	
	if cached_data is not None:
		return Response(cached_data)
	
	# محاسبه داده‌ها
	start, end, prev_start, prev_end = get_date_range(period)
	
	# بهینه‌سازی: یک بار requesters را بگیر
	requesters = Requester.objects.all().order_by('name')
	departments = []
	
	for req in requesters:
		# بهینه‌سازی: استفاده از select_related
		# دسترسی به requester از طریق InvoiceItem مستقیم یا از طریق مسیر invoice -> purchase_order -> request -> user -> role
		current_items = _invoice_items_for_period(start=start, end=end)
		current = current_items.filter(
			Q(requester=req) | 
			Q(invoice__purchase_order__request__user__role=req.name)
		).aggregate(
			total=Coalesce(Sum('line_total'), Value(0, output_field=DecimalField()))
		)['total'] or Decimal('0.00')
		
		previous_items = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False)
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


def _get_category_expense(category, department, start, end, include_subcategories=True):
	"""
	محاسبه هزینه یک دسته‌بندی با یا بدون زیردسته‌ها
	"""
	# گرفتن همه آیتم‌های فاکتور برای این بازه
	items = _invoice_items_for_period(start=start, end=end)
	
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
	main_categories = Category.objects.filter(parent__isnull=True, is_active=True).order_by('name')
	categories = []
	
	for cat in main_categories:
		# محاسبه هزینه شامل زیردسته‌ها
		current = _get_category_expense(cat, department, start, end, include_subcategories=True)
		previous = _get_category_expense(cat, department, prev_start, prev_end, include_subcategories=True)
		
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
	
	for subcat in subcategories:
		# محاسبه هزینه
		items = _invoice_items_for_period(start=start, end=end).filter(product__category=subcat)
		prev_items = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False).filter(product__category=subcat)
		
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
	direct_items = _invoice_items_for_period(start=start, end=end).filter(product__category=parent_category)
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
	
	# فیلتر بر اساس دسته‌بندی
	items_qs = _invoice_items_for_period(start=start, end=end).filter(
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
		prev_items_qs = _invoice_items_for_period(start=prev_start, end=prev_end, end_inclusive=False).filter(
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
