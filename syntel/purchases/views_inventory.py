"""
Inventory, Warehouses, Stock Items, and Warehouse Vouchers Views.
تفکیک‌شده از purchases/views.py برای بهبود ساختار معماری و نگهداری‌پذیری.
"""
import json
import logging
from decimal import Decimal
from datetime import datetime, date, time

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Avg, Count, DecimalField, F, Max, Min, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .middleware import get_cafe_code
from .models import (
    ActivityLog,
    Cafe,
    Category,
    Department,
    Notification,
    Product,
    ProductBrand,
    StockItem,
    StockTransaction,
    Unit,
    User,
    Warehouse,
    WarehouseVoucher,
    WarehouseVoucherItem,
)
from .services.inventory import (
    InsufficientStockError,
    InventoryServiceError,
    ReopenNotAllowedError,
    StockScopeError,
    VoucherValidationError,
    apply_stock_delta,
    finalize_voucher,
    next_voucher_number,
    reopen_officer_assignments,
    reverse_voucher,
)
from .views_helpers import (
    _fmt2,
    _parse_decimal,
    cafe_view,
    custom_login_required,
    get_cafe_from_request,
    get_cafe_redirect_url,
    get_scoped_object_or_404,
    role_required,
)

logger = logging.getLogger(__name__)


class _InsufficientStock(Exception):
    """موجودی انبار برای کسر درخواستی کافی نیست"""


def _set_stock_quantity(stock_item_id, absolute):
    """تنظیم مطلق موجودی انبار (انبارگردانی / ثبت اولیه) — داخل atomic اجرا شود.

    بازگشت: (موجودی قبل, موجودی جدید)
    """
    row = StockItem.objects.filter(id=stock_item_id).first()
    prev_qty = row.quantity if row else Decimal('0')
    StockItem.objects.filter(id=stock_item_id).update(quantity=absolute)
    return prev_qty, absolute


# ---------------------------------------------------------------------------
# Stock Management Views (انبارداری)
# ---------------------------------------------------------------------------
@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'warehouse'])
def stock_list(request: HttpRequest) -> HttpResponse:
    """لیست موجودی‌های انبار - کارت به ازای هر (محصول، انبار) با تفکیک برند"""
    cafe = get_cafe_from_request(request)

    # همه انبارهای فعال کافه جاری
    warehouses_qs = Warehouse.objects.filter(is_active=True)
    if cafe:
        warehouses_qs = warehouses_qs.filter(cafe=cafe)

    # فیلتر انبار انتخاب‌شده
    warehouse_id = request.GET.get('warehouse')
    selected_warehouse = None
    if warehouse_id:
        selected_warehouse = warehouses_qs.filter(id=warehouse_id).first()

    stock_items = StockItem.objects.select_related(
        'product', 'unit', 'warehouse', 'product_brand', 'product__unit'
    ).order_by('warehouse__name', 'product__name')
    if selected_warehouse:
        stock_items = stock_items.filter(warehouse=selected_warehouse)
    elif cafe:
        stock_items = stock_items.filter(warehouse__cafe=cafe)

    # فیلتر بر اساس موجودی کم
    show_low_stock = request.GET.get('low_stock')
    if show_low_stock:
        stock_items = stock_items.filter(quantity__lte=models.F('min_quantity'))

    # جستجو (نام محصول، نام برند، دسته‌بندی)
    search_query = request.GET.get('search')
    if search_query:
        stock_items = stock_items.filter(
            Q(product__name__icontains=search_query) |
            Q(product__brands__brand_name__icontains=search_query) |
            Q(product_brand__brand_name__icontains=search_query) |
            Q(product__category__name__icontains=search_query)
        ).distinct()

    # آیتم‌های قدیمی ممکن است واحد نداشته باشند - واحد محصول را جایگزین کن
    for si in stock_items:
        if si.unit_id is None and si.product and si.product.unit_id:
            si.unit = si.product.unit

    # تجمیع به تفکیک (محصول، انبار) - یک کارت برای هر محصول
    groups_dict = {}
    for si in stock_items:
        key = (si.product_id, si.warehouse_id)
        group = groups_dict.setdefault(key, {
            'product': si.product,
            'warehouse': si.warehouse,
            'brand_items': [],
            'unbranded_items': [],
            'first_item': si,
            'total_quantity': Decimal('0'),
            'total_min_quantity': Decimal('0'),
        })
        group['total_quantity'] += si.quantity
        group['total_min_quantity'] += si.min_quantity
        if si.product_brand_id:
            group['brand_items'].append(si)
        else:
            group['unbranded_items'].append(si)

    groups_list = list(groups_dict.values())
    for g in groups_list:
        g['is_low_stock'] = g['total_quantity'] <= g['total_min_quantity']

    # بدون صفحه‌بندی: همه اقلام در یک صفحه و با اسکرول نمایش داده می‌شوند
    # (قبلاً Paginator با ۵۰ آیتم بود که کاربر را مجبور به رفتن به صفحه بعد می‌کرد؛
    #  طبق درخواست، همه محصولات در همان صفحه دیده می‌شوند.)
    # برای سازگاری با قالب، همچنان یک page_obj می‌فرستیم که همه اقلام را دارد.
    paginator = Paginator(groups_list, max(len(groups_list), 1))
    page_obj = paginator.get_page(1)

    return render(request, 'stock/stock_list.html', {
        'page_obj': page_obj,
        'search_query': search_query,
        'show_low_stock': show_low_stock,
        'warehouses': warehouses_qs,
        'selected_warehouse': selected_warehouse,
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'warehouse'])
def stock_transactions(request: HttpRequest, stock_id: int) -> HttpResponse:
    """تاریخچه تراکنش‌های یک موجودی"""
    stock_item = get_object_or_404(StockItem, id=stock_id)
    transactions = stock_item.transactions.select_related('created_by', 'stock_item__product_brand').all()

    # صفحه‌بندی
    paginator = Paginator(transactions, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'stock/stock_transactions.html', {
        'stock_item': stock_item,
        'page_obj': page_obj
    })




# ---------------------------------------------------------------------------
# Warehouse Management - مدیریت انبارها
# ---------------------------------------------------------------------------

@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'warehouse'])
def warehouse_management(request):
    """صفحه مدیریت انبارها (کارت انبارها + تعریف انبار)"""
    from django.db.models import Count
    cafe = get_cafe_from_request(request)
    warehouses = Warehouse.objects.filter(cafe=cafe) if cafe else Warehouse.objects.none()
    # تعداد اقلام هر انبار برای نمایش روی کارت‌ها
    counts = dict(
        StockItem.objects.filter(warehouse__in=warehouses)
        .values('warehouse_id')
        .annotate(n=Count('id'))
        .values_list('warehouse_id', 'n')
    )
    return render(request, 'warehouse/warehouse_management.html', {
        'warehouses': warehouses,
        'item_counts': counts,
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['manager', 'admin', 'ceo'])
def warehouse_create(request):
    """ایجاد انبار جدید (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'متد نامعتبر'}, status=405)

    cafe = get_cafe_from_request(request)
    if not cafe:
        return JsonResponse({'success': False, 'error': 'کافه یافت نشد'}, status=400)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, Exception):
        data = request.POST

    name = data.get('name', '').strip()
    code = data.get('code', '').strip()
    description = data.get('description', '').strip()
    location = data.get('location', '').strip()
    manager_name = data.get('manager_name', '').strip()
    manager_phone = data.get('manager_phone', '').strip()

    if not name or not code:
        return JsonResponse({'success': False, 'error': 'نام و کد انبار الزامی است'}, status=400)

    if Warehouse.objects.filter(cafe=cafe, code=code).exists():
        return JsonResponse({'success': False, 'error': 'کد انبار تکراری است'}, status=400)

    warehouse = Warehouse.objects.create(
        cafe=cafe,
        name=name,
        code=code,
        description=description,
        location=location,
        manager_name=manager_name,
        manager_phone=manager_phone,
        is_active=True,
        is_supply_enabled=data.get('is_supply_enabled', True),
    )

    return JsonResponse({
        'success': True,
        'warehouse': {
            'id': warehouse.id,
            'name': warehouse.name,
            'code': warehouse.code,
            'description': warehouse.description,
            'location': warehouse.location,
            'manager_name': warehouse.manager_name,
            'manager_phone': warehouse.manager_phone,
            'is_active': warehouse.is_active,
            'is_supply_enabled': warehouse.is_supply_enabled,
        }
    })


@custom_login_required
@role_required(['manager', 'admin', 'ceo'])
def warehouse_edit(request, warehouse_id):
    """ویرایش انبار (AJAX)"""
    cafe = get_cafe_from_request(request)
    warehouse = get_scoped_object_or_404(Warehouse, request, id=warehouse_id)

    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'warehouse': {
                'id': warehouse.id,
                'name': warehouse.name,
                'code': warehouse.code,
                'description': warehouse.description,
                'location': warehouse.location,
                'manager_name': warehouse.manager_name,
                'manager_phone': warehouse.manager_phone,
                'is_active': warehouse.is_active,
                'is_supply_enabled': warehouse.is_supply_enabled,
            }
        })

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'متد نامعتبر'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, Exception):
        data = request.POST

    name = data.get('name', '').strip()
    code = data.get('code', '').strip()

    if not name or not code:
        return JsonResponse({'success': False, 'error': 'نام و کد انبار الزامی است'}, status=400)

    if Warehouse.objects.filter(cafe=cafe, code=code).exclude(id=warehouse_id).exists():
        return JsonResponse({'success': False, 'error': 'کد انبار تکراری است'}, status=400)

    warehouse.name = name
    warehouse.code = code
    warehouse.description = data.get('description', '').strip()
    warehouse.location = data.get('location', '').strip()
    warehouse.manager_name = data.get('manager_name', '').strip()
    warehouse.manager_phone = data.get('manager_phone', '').strip()
    warehouse.is_active = data.get('is_active', True)
    warehouse.is_supply_enabled = data.get('is_supply_enabled', True)
    warehouse.save()

    return JsonResponse({'success': True, 'message': 'انبار با موفقیت ویرایش شد'})


@custom_login_required
@role_required(['manager', 'admin', 'ceo'])
def warehouse_toggle_status(request, warehouse_id):
    """فعال/غیرفعال کردن انبار (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'متد نامعتبر'}, status=405)

    cafe = get_cafe_from_request(request)
    warehouse = get_scoped_object_or_404(Warehouse, request, id=warehouse_id)
    warehouse.is_active = not warehouse.is_active
    warehouse.save()

    return JsonResponse({
        'success': True,
        'is_active': warehouse.is_active,
        'message': 'انبار فعال شد' if warehouse.is_active else 'انبار غیرفعال شد'
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stock Category Views - مدیریت دسته‌بندی موجودی انبار
# ─────────────────────────────────────────────────────────────────────────────

@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def stock_by_category(request: HttpRequest) -> HttpResponse:
    """نمایش دسته‌بندی‌های محصولات انبار به صورت فلش کارت"""
    cafe = get_cafe_from_request(request)

    # دسته‌بندی‌های اصلی که محصولات انبارشده دارند
    categories_qs = Category.objects.filter(
        product__stock_items__isnull=False
    ).distinct()
    if cafe:
        categories_qs = categories_qs.filter(cafe=cafe)

    # فیلتر اقلام انبار بر اساس کافه و محاسبه اقلام با موجودی کم در پایگاه داده
    item_filter = Q(product__stock_items__isnull=False)
    if cafe:
        item_filter &= Q(product__stock_items__warehouse__cafe=cafe)
    low_filter = item_filter & Q(
        product__stock_items__quantity__lte=F('product__stock_items__min_quantity')
    )

    categories_annotated = categories_qs.annotate(
        total_items=Count('product__stock_items', filter=item_filter, distinct=True),
        low_stock=Count('product__stock_items', filter=low_filter, distinct=True),
    ).order_by('name')

    category_data = [
        {
            'id': cat.id,
            'name': cat.name,
            'description': cat.description,
            'total_items': cat.total_items,
            'low_stock': cat.low_stock,
        }
        for cat in categories_annotated
    ]

    # دسته‌بندی "بدون دسته" - محصولاتی که category ندارند (محاسبه در دیتابیس)
    no_cat_qs = StockItem.objects.filter(product__category__isnull=True)
    if cafe:
        no_cat_qs = no_cat_qs.filter(warehouse__cafe=cafe)
    no_cat_agg = no_cat_qs.aggregate(
        total=Count('id'),
        low=Count('id', filter=Q(quantity__lte=F('min_quantity'))),
    )
    no_cat_count = no_cat_agg['total'] or 0
    no_cat_low = no_cat_agg['low'] or 0

    return render(request, 'stock/stock_categories.html', {
        'category_data': category_data,
        'no_cat_count':  no_cat_count,
        'no_cat_low':    no_cat_low,
        'cafe_code':     cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def stock_category_items(request: HttpRequest, category_id: int) -> HttpResponse:
    """نمایش موجودی‌های انبار یک دسته‌بندی خاص"""
    cafe = get_cafe_from_request(request)

    if category_id == 0:
        # "بدون دسته‌بندی"
        category = None
        stock_items = StockItem.objects.select_related('product', 'unit', 'warehouse').filter(
            product__category__isnull=True
        )
    else:
        category = get_scoped_object_or_404(Category, request, id=category_id)
        stock_items = StockItem.objects.select_related('product', 'unit', 'warehouse').filter(
            product__category=category
        )

    if cafe:
        stock_items = stock_items.filter(warehouse__cafe=cafe)

    # فیلتر انبار
    warehouse_id = request.GET.get('warehouse')
    selected_warehouse = None
    warehouses_qs = Warehouse.objects.filter(is_active=True)
    if cafe:
        warehouses_qs = warehouses_qs.filter(cafe=cafe)
    if warehouse_id:
        selected_warehouse = warehouses_qs.filter(id=warehouse_id).first()
        if selected_warehouse:
            stock_items = stock_items.filter(warehouse=selected_warehouse)

    # جستجو
    search_q = request.GET.get('search', '').strip()
    if search_q:
        stock_items = stock_items.filter(product__name__icontains=search_q)

    # موجودی کم
    if request.GET.get('low_stock'):
        stock_items = [i for i in stock_items if i.is_low_stock]

    paginator = Paginator(stock_items, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'stock/stock_category_items.html', {
        'category':          category,
        'page_obj':          page_obj,
        'warehouses':        warehouses_qs,
        'selected_warehouse': selected_warehouse,
        'search_q':          search_q,
        'show_low_stock':    request.GET.get('low_stock'),
        'cafe_code':         cafe.code if cafe else '',
    })


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Warehouse Voucher Views - حواله‌های انبار (Prompt 6: UI کامل روی شِمای جدید)
#
# جریان: پیش‌نویس → ثبت → (تأیید برای تعدیل) → نهایی‌سازی → برگشت / لغو
#   - نوع‌های روتین: «ثبت و نهایی‌سازی» یک‌گامه (اثر فوری مثل قبل).
#   - تعدیل (نیازمند تأیید): ثبت → صفحهٔ تأیید (مدیر) → تأیید + نهایی‌سازی.
#   - برگشت: فقط حوالهٔ نهایی‌شده، با دلیلِ اجباری.
# ─────────────────────────────────────────────────────────────────────────────

VOUCHER_TYPE_LABELS = dict(WarehouseVoucher.VOUCHER_TYPE_CHOICES)
VOUCHER_STATUS_LABELS = dict(WarehouseVoucher.STATUS_CHOICES)

# کدام فیلدهای انبار برای هر نوع در فرم ایجاد نمایش داده می‌شود
VOUCHER_WAREHOUSE_FIELDS = {
    'receipt':    ['destination'],
    'issue':      ['source'],
    'return':     ['source'],
    'sale':       ['source'],
    'waste':      ['source'],
    'transfer':   ['source', 'destination'],
    'adjustment': ['source'],
    # «تحویل مستقیم»: انبارِ مقصد = محلِ عبور کالای تازه‌خرید (+ و بلافاصله −)
    'direct_delivery': ['destination'],
}

# بخش‌گیرنده فقط برای «تحویل مستقیم» انتخاب می‌شود
_VOUCHER_DEPARTMENT_TYPES = {'direct_delivery'}


def _parse_jalali_date(raw):
    """تبدیل تاریخ شمسی (۱۴۰۵/۰۸/۰۵) یا میلادی (2026-10-27) به date میلادی.

    مقدار خالی یا نامعتبر → None. تاریخ‌های اسلش‌دار همیشه شمسی در نظر
    گرفته می‌شوند (مثل سایر فرم‌های پروژه).
    """
    raw = (raw or '').strip()
    if not raw:
        return None
    if '/' in raw:
        parts = raw.split('/')
        if len(parts) != 3:
            return None
        try:
            import jdatetime
            year, month, day = (int(p) for p in parts)
            return jdatetime.date(year, month, day).togregorian()
        except (ValueError, TypeError):
            return None
    from django.utils.dateparse import parse_date
    return parse_date(raw)


def _format_jalali_date(value):
    """date/datetime میلادی → رشتهٔ شمسی YYYY/MM/DD برای مقدار باکس تاریخ."""
    import jdatetime
    if isinstance(value, datetime):
        value = (timezone.localtime(value).date()
                 if timezone.is_aware(value) else value.date())
    if not value:
        return ''
    j = jdatetime.date.fromgregorian(date=value)
    return f'{j.year}/{j.month:02d}/{j.day:02d}'


def _jalali_input_value(raw):
    """مقدار نمایشی باکس تاریخ: شمسی می‌ماند، میلادی به شمسی تبدیل می‌شود."""
    raw = (raw or '').strip()
    if not raw or '/' in raw:
        return raw
    parsed = _parse_jalali_date(raw)
    if parsed is None and ('T' in raw or ' ' in raw):
        # سازگاری با مقدار datetime-local فرم‌های بازِ قبل از تغییر
        try:
            parsed = datetime.fromisoformat(raw).date()
        except ValueError:
            parsed = None
    return _format_jalali_date(parsed) if parsed else raw


def _voucher_datetime_from_input(raw, default_time=None):
    """ورودی تاریخ فرم حواله → (datetime آگاه, معتبر؟)

    ورودی‌های پذیرفته‌شده: تاریخ شمسی ۱۴۰۵/۰۸/۰۵، تاریخ میلادی 2026-10-27 و
    datetime-local قدیمی 2026-10-27T10:30. ساعتِ تاریخ شمسی از default_time
    (ساعتِ حوالهٔ در حال ویرایش) یا ساعتِ اکنون گرفته می‌شود.
    """
    raw = (raw or '').strip()
    base_time = (default_time or timezone.localtime().time()).replace(microsecond=0)

    if not raw:
        return timezone.make_aware(datetime.combine(timezone.localdate(), base_time)), True

    # سازگاری با فرم‌های بازِ قبل از تغییر (datetime-local)
    if 'T' in raw or ' ' in raw:
        try:
            dt = datetime.fromisoformat(raw)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return dt, True
        except ValueError:
            return timezone.make_aware(datetime.combine(timezone.localdate(), base_time)), False

    parsed_date = _parse_jalali_date(raw)
    if parsed_date is None:
        return timezone.make_aware(datetime.combine(timezone.localdate(), base_time)), False
    return timezone.make_aware(datetime.combine(parsed_date, base_time)), True


def _voucher_form_options(cafe, warehouse_id):
    """گزینه‌های فرم حواله — فقط داده‌های کافهٔ فعلی"""
    products = (Product.objects.filter(cafe=cafe, is_active=True)
                .select_related('category', 'unit').order_by('category__name', 'name'))
    units = Unit.objects.filter(cafe=cafe)
    brands_by_product = {}
    for b in (ProductBrand.objects.filter(product__cafe=cafe, is_active=True)
              .select_related('product').order_by('brand_name')):
        brands_by_product.setdefault(b.product_id, []).append(
            {'id': b.id, 'name': b.brand_name})
    # موجودی‌ها: اگر انبار مشخص شده باشد فقط همان انبار، وگرنه همهٔ انبارهای
    # کافه — تا در صفحهٔ ایجاد (که هنوز انبار انتخاب نشده) فهرست «موجودی انبار»
    # خالی نباشد. در سمت کلاینت با انتخاب انبار، فهرست فیلتر می‌شود.
    stock_qs = StockItem.objects.filter(warehouse__cafe=cafe)
    if warehouse_id:
        stock_qs = stock_qs.filter(warehouse_id=warehouse_id)
    stock_items = (stock_qs.select_related('product', 'product_brand', 'unit', 'warehouse')
                   .order_by('warehouse__name', 'product__name'))
    return {
        'products': products,
        'units': units,
        'brands_json': json.dumps(brands_by_product, ensure_ascii=False),
        'stock_items': stock_items,
    }


def _parse_voucher_lines(request, cafe, voucher_type):
    """پارس قلم‌های فرم — هر شناسهٔ ارسالی (محصول/برند/موجودی/واحد) فقط در
    کافهٔ فعلی معتبر است؛ خروجی (خطوط معتبر، خطاها)"""
    product_ids = request.POST.getlist('product_id[]')
    brand_ids   = request.POST.getlist('product_brand_id[]')
    stock_ids   = request.POST.getlist('stock_item_id[]')
    unit_ids    = request.POST.getlist('unit_id[]')
    quantities  = request.POST.getlist('base_quantity[]')
    prices      = request.POST.getlist('unit_price[]')
    item_descs  = request.POST.getlist('item_description[]')

    lines = []
    errors = []
    for idx, pid in enumerate(product_ids):
        if not pid:
            continue
        try:
            product = Product.objects.get(id=int(pid), cafe=cafe)
        except (Product.DoesNotExist, ValueError, TypeError):
            errors.append(f'قلم {idx + 1}: محصول نامعتبر یا متعلق به کافهٔ دیگر است')
            continue

        brand = None
        if idx < len(brand_ids) and brand_ids[idx]:
            try:
                brand = ProductBrand.objects.select_related('product').get(
                    id=int(brand_ids[idx]), product__cafe=cafe)
            except (ProductBrand.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: برند نامعتبر یا متعلق به کافهٔ دیگر است')
                continue
            if brand.product_id != product.id:
                errors.append(f'قلم {idx + 1}: برند «{brand.brand_name}» متعلق به این محصول نیست')
                continue

        stock_item = None
        if idx < len(stock_ids) and stock_ids[idx]:
            try:
                stock_item = StockItem.objects.select_related('warehouse').get(
                    id=int(stock_ids[idx]), warehouse__cafe=cafe)
            except (StockItem.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: موجودی انبار نامعتبر یا متعلق به کافهٔ دیگر است')
                continue

        unit_obj = None
        if idx < len(unit_ids) and unit_ids[idx]:
            try:
                unit_obj = Unit.objects.get(id=int(unit_ids[idx]), cafe=cafe)
            except (Unit.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: واحد نامعتبر یا متعلق به کافهٔ دیگر است')
                continue
        if unit_obj is None:
            unit_obj = stock_item.unit if stock_item else product.unit

        def _dec(vals, default=None):
            s = (vals[idx] if idx < len(vals) else '').replace(',', '').strip()
            if not s:
                return default
            try:
                return Decimal(s)
            except Exception:
                return 'INVALID'

        base_qty = _dec(quantities)
        if base_qty == 'INVALID':
            errors.append(f'قلم {idx + 1}: مقدار پایه نامعتبر است')
            continue
        if base_qty is None:
            errors.append(f'قلم {idx + 1}: مقدار پایه الزامی است')
            continue
        if base_qty == 0:
            errors.append(f'قلم {idx + 1}: مقدار پایه نباید صفر باشد')
            continue
        if base_qty < 0 and voucher_type != 'adjustment':
            errors.append(f'قلم {idx + 1}: مقدار پایه باید مثبت باشد')
            continue

        price = _dec(prices, default=Decimal('0'))
        if price == 'INVALID':
            price = Decimal('0')

        lines.append({
            'product': product,
            'product_brand': brand,
            'stock_item': stock_item,
            'unit': unit_obj,
            'base_quantity': base_qty,
            'trade_quantity': None,
            'conversion_rate': brand.conversion_rate if brand else None,
            'unit_price': price,
            'description': (item_descs[idx] if idx < len(item_descs) else '').strip(),
        })
    return lines, errors


def _voucher_prefill_items(request, cafe):
    """اقلامِ ارسال‌شده در POST را برای نمایش دوبارهٔ فرم پس از خطا برمی‌گرداند؛
    بدون این کار با هر خطای اعتبارسنجی، همهٔ ردیف‌های واردشده پاک می‌شدند."""
    product_ids = request.POST.getlist('product_id[]')
    brand_ids = request.POST.getlist('product_brand_id[]')
    stock_ids = request.POST.getlist('stock_item_id[]')
    unit_ids = request.POST.getlist('unit_id[]')
    quantities = request.POST.getlist('base_quantity[]')
    prices = request.POST.getlist('unit_price[]')
    descs = request.POST.getlist('item_description[]')

    def at(vals, idx):
        return vals[idx] if idx < len(vals) else ''

    def as_int(value):
        value = (value or '').strip()
        return int(value) if value.isdigit() else None

    pids = [int(p) for p in product_ids if (p or '').strip().isdigit()]
    names = {str(p.id): p.name for p in Product.objects.filter(cafe=cafe, id__in=pids)}

    items = []
    for idx, pid in enumerate(product_ids):
        pid = (pid or '').strip()
        qty = at(quantities, idx)
        desc = at(descs, idx)
        if not pid and not qty and not desc:
            continue  # ردیف کاملاً خالی
        items.append({
            'product_id': as_int(pid),
            'product_name': names.get(pid, ''),
            'brand_id': as_int(at(brand_ids, idx)),
            'stock_id': as_int(at(stock_ids, idx)),
            'unit_id': as_int(at(unit_ids, idx)),
            'base': qty,
            'price': at(prices, idx),
            'desc': desc,
        })
    return items


def _render_voucher_form(request, cafe, voucher_type, warehouse, extra=None):
    """رندر فرم ایجاد/ویرایش حواله با گزینه‌های کافهٔ فعلی"""
    opts = _voucher_form_options(cafe, warehouse.id if warehouse else None)
    ctx = {
        'voucher_type': voucher_type,
        'type_label': VOUCHER_TYPE_LABELS.get(voucher_type, ''),
        'voucher_types_all': WarehouseVoucher.VOUCHER_TYPE_CHOICES,
        'warehouse': warehouse,
        'warehouses': Warehouse.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'warehouse_fields': VOUCHER_WAREHOUSE_FIELDS.get(voucher_type, []),
        'departments': Department.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'cafe_code': cafe.code if cafe else '',
        'products': opts['products'],
        'units': opts['units'],
        'brands_json': opts['brands_json'],
        'stock_items': opts['stock_items'],
        'prefill_items': [],
        'form_description': '',
        # تاریخ شمسیِ پیش‌فرضِ باکس تاریخ (همان ظاهر فاکتور نقدی)
        'form_date': _format_jalali_date(timezone.localdate()),
    }
    if extra:
        ctx.update(extra)
    return render(request, 'stock/voucher_create.html', ctx)


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def voucher_list(request: HttpRequest) -> HttpResponse:
    """لیست حواله‌های انبار با فیلترهای کامل (نوع، وضعیت، بازهٔ تاریخ، انبار
    مبدأ/مقصد، محصول، بخش، ایجادکننده، شماره)"""
    cafe = get_cafe_from_request(request)
    vouchers = WarehouseVoucher.objects.select_related(
        'source_warehouse', 'destination_warehouse', 'department',
        'created_by', 'approver',
    ).filter(cafe=cafe)

    f = {
        'type': request.GET.get('type'),
        'status': request.GET.get('status'),
        # مقدار خامِ شمسی برای نمایش در باکس تاریخ؛ میلادی هم سازگار است
        'date_from': _jalali_input_value(request.GET.get('date_from', '')),
        'date_to': _jalali_input_value(request.GET.get('date_to', '')),
        'source_warehouse': request.GET.get('source_warehouse'),
        'dest_warehouse': request.GET.get('dest_warehouse'),
        'product': request.GET.get('product'),
        'department': request.GET.get('department'),
        'creator': request.GET.get('creator'),
        'search': request.GET.get('search', '').strip(),
    }

    if f['type'] in VOUCHER_TYPE_LABELS:
        vouchers = vouchers.filter(voucher_type=f['type'])
    if f['status'] in VOUCHER_STATUS_LABELS:
        vouchers = vouchers.filter(status=f['status'])
    date_from = _parse_jalali_date(f['date_from'])
    if date_from:
        vouchers = vouchers.filter(date__date__gte=date_from)
    date_to = _parse_jalali_date(f['date_to'])
    if date_to:
        vouchers = vouchers.filter(date__date__lte=date_to)
    if f['source_warehouse']:
        vouchers = vouchers.filter(source_warehouse_id=f['source_warehouse'])
    if f['dest_warehouse']:
        vouchers = vouchers.filter(destination_warehouse_id=f['dest_warehouse'])
    if f['product']:
        vouchers = vouchers.filter(items__product_id=f['product'])
    if f['department']:
        # بخش گیرندهٔ مستقیمِ «تحویل مستقیم» یا بخشِ مرتبط با انبارِ مبدأ/مقصد
        dept_wh_ids = Department.objects.filter(cafe=cafe, id=f['department']).values_list('warehouse_id', flat=True)
        vouchers = vouchers.filter(
            Q(department_id=f['department'])
            | Q(source_warehouse_id__in=dept_wh_ids)
            | Q(destination_warehouse_id__in=dept_wh_ids))
    if f['creator']:
        vouchers = vouchers.filter(created_by_id=f['creator'])
    if f['search']:
        vouchers = vouchers.filter(voucher_number__icontains=f['search'])

    vouchers = vouchers.distinct().order_by('-created_at')
    paginator = Paginator(vouchers, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'stock/voucher_list.html', {
        'page_obj': page_obj,
        'warehouses': Warehouse.objects.filter(cafe=cafe),
        'departments': Department.objects.filter(cafe=cafe).order_by('name'),
        'creators': (User.objects.filter(created_warehouse_vouchers__cafe=cafe)
                     .distinct().order_by('username')),
        'products': Product.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'voucher_types': VOUCHER_TYPE_LABELS,
        'statuses': VOUCHER_STATUS_LABELS,
        'f': f,
        'advanced_active': any([
            f['date_from'], f['date_to'], f['source_warehouse'],
            f['dest_warehouse'], f['product'], f['department'], f['creator'],
        ]),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_create(request: HttpRequest, voucher_type: str) -> HttpResponse:
    """فرم یکپارچهٔ ایجاد حواله — همهٔ ۷ نوع در یک فرم؛ انتخاب نوع فیلدهای
    انبارِ مرتبط را نشان می‌دهد و اعتبارسنجی حساس به نوع در سرویس مرکزی است.

    - «ثبت پیش‌نویس» → وضعیت draft (قابل ویرایش/لغو)
    - «ثبت و نهایی‌سازی» → نوع‌های روتین همان لحظه نهایی می‌شوند (اثر فوری مثل
      قبل)؛ تعدیل «ثبت‌شده» می‌ماند تا مدیر تأییدش کند.
    """
    cafe = get_cafe_from_request(request)
    if voucher_type not in VOUCHER_TYPE_LABELS:
        raise Http404('نوع حواله نامعتبر است')

    if request.method == 'POST':
        action = request.POST.get('action', 'submit')
        try:
            src_id = int(request.POST.get('source_warehouse')) if request.POST.get('source_warehouse') else None
            dst_id = int(request.POST.get('dest_warehouse')) if request.POST.get('dest_warehouse') else None
        except (ValueError, TypeError):
            src_id = dst_id = None

        src_wh = get_scoped_object_or_404(Warehouse, request, id=src_id) if src_id else None
        dst_wh = get_scoped_object_or_404(Warehouse, request, id=dst_id) if dst_id else None

        # بخش گیرندهٔ «تحویل مستقیم» — فقط بخش‌های کافهٔ فعلی معتبرند
        dept = None
        if voucher_type in _VOUCHER_DEPARTMENT_TYPES:
            dept_id_str = request.POST.get('department', '').strip()
            if not dept_id_str or not dept_id_str.isdigit():
                messages.error(request, 'بخش گیرنده را انتخاب کنید')
                return _render_voucher_form(
                    request, cafe, voucher_type, src_wh or dst_wh,
                    {'form_errors': ['بخش گیرنده الزامی است'],
                     'prefill_items': _voucher_prefill_items(request, cafe),
                     'form_description': request.POST.get('description', ''),
                     'form_date': _jalali_input_value(request.POST.get('date', '')),
                     'form_source_wh': src_wh, 'form_dest_wh': dst_wh})
            dept = get_scoped_object_or_404(Department, request, id=int(dept_id_str))

        lines, errors = _parse_voucher_lines(request, cafe, voucher_type)

        # تاریخ فرم شمسی است؛ ساعتِ سند از لحظهٔ ثبت گرفته می‌شود
        date_str = request.POST.get('date', '').strip()
        vdate, date_ok = _voucher_datetime_from_input(date_str)
        if not date_ok:
            errors.append('تاریخ واردشده معتبر نیست — نمونهٔ درست: ۱۴۰۵/۰۸/۰۵')

        if errors:
            for e in errors:
                messages.error(request, e)
            return _render_voucher_form(request, cafe, voucher_type, src_wh or dst_wh,
                                        {'form_errors': errors,
                                         'selected_department': dept,
                                         'prefill_items': _voucher_prefill_items(request, cafe),
                                         'form_description': request.POST.get('description', ''),
                                         'form_date': _jalali_input_value(date_str),
                                         'form_source_wh': src_wh, 'form_dest_wh': dst_wh})

        description = request.POST.get('description', '').strip()

        try:
            with transaction.atomic():
                voucher = WarehouseVoucher(
                    cafe=cafe,
                    voucher_type=voucher_type,
                    status='draft' if action == 'draft' else 'submitted',
                    voucher_number=next_voucher_number(cafe.id, voucher_type),
                    source_warehouse=src_wh,
                    destination_warehouse=dst_wh,
                    department=dept,
                    date=vdate,
                    description=description,
                    created_by=request.user,
                )
                try:
                    voucher.full_clean(exclude=['voucher_number', 'status', 'created_by', 'approver', 'approved_at'])
                except ValidationError as exc:
                    raise VoucherValidationError(f'سند ناسازگار است: {exc}')
                voucher.save()

                for line in lines:
                    WarehouseVoucherItem.objects.create(voucher=voucher, **line)

                if action != 'draft':
                    if voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES:
                        # ثبت شد؛ نهایی‌سازی بعد از تأیید مدیر
                        pass
                    else:
                        finalize_voucher(voucher, finalized_by=request.user)
        except InventoryServiceError as exc:
            messages.error(request, f'حواله ثبت نشد: {exc}')
            return _render_voucher_form(request, cafe, voucher_type, src_wh or dst_wh,
                                        {'form_errors': [str(exc)],
                                         'prefill_items': _voucher_prefill_items(request, cafe),
                                         'form_description': request.POST.get('description', ''),
                                         'form_date': _jalali_input_value(request.POST.get('date', '')),
                                         'form_source_wh': src_wh, 'form_dest_wh': dst_wh,
                                         'selected_department': dept})

        if action == 'draft':
            messages.success(request, f'پیش‌نویس حواله {voucher.voucher_number} ثبت شد.')
        elif voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES:
            messages.success(request, f'حوالهٔ {voucher.voucher_number} ثبت شد و در انتظار تأیید مدیر است.')
        elif voucher_type == 'direct_delivery':
            _dept_name = dept.name if dept else '—'
            messages.success(
                request,
                f'حوالهٔ {voucher.voucher_number} نهایی شد؛ کالا به «{_dept_name}» تحویل داده شد '
                f'(در سابقهٔ موجودیِ انبار، ورود و خروجِ آن ثبت شده).')
        else:
            messages.success(request, f'حوالهٔ {voucher.voucher_number} نهایی‌سازی شد و اثر آن روی موجودی اعمال شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return _render_voucher_form(request, cafe, voucher_type, None)


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def voucher_detail(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """جزئیات حواله: سند، اقلام، اثر روی موجودی، خط زمانی، سندِ منبع و اقدامات"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    items = voucher.items.select_related(
        'product', 'product_brand', 'unit', 'stock_item__warehouse').all()
    movements = StockTransaction.objects.filter(voucher=voucher).select_related(
        'stock_item__product', 'stock_item__warehouse', 'created_by').order_by('created_at')

    is_manager = request.user.role in ('manager', 'admin', 'ceo')
    return render(request, 'stock/voucher_detail.html', {
        'voucher': voucher,
        'items': items,
        'movements': movements,
        'can_edit': (voucher.status == 'draft'
                     and (voucher.created_by_id == request.user.id or is_manager)),
        'can_cancel': voucher.status in ('draft', 'submitted'),
        'can_approve': (is_manager and voucher.status == 'submitted'
                        and (voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                             or voucher.requires_approval)),
        'can_reverse': (voucher.status == 'finalized' and not voucher.reversed_at
                        and not voucher.reverses_id),
        'tx_type_labels': dict(StockTransaction.TRANSACTION_TYPE_CHOICES),
        'approval_pending': (voucher.status == 'submitted'
                             and (voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                                  or voucher.requires_approval)),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_edit(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """ویرایش پیش‌نویس حواله — فقط draft و فقط توسط ایجادکننده (یا مدیر)"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    is_manager = request.user.role in ('manager', 'admin', 'ceo')
    if voucher.status != 'draft':
        messages.error(request, 'فقط پیش‌نویس قابل ویرایش است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    if voucher.created_by_id != request.user.id and not is_manager:
        raise Http404('فقط ایجادکنندهٔ پیش‌نویس می‌تواند آن را ویرایش کند')

    if request.method == 'POST':
        try:
            src_id = int(request.POST.get('source_warehouse')) if request.POST.get('source_warehouse') else None
            dst_id = int(request.POST.get('dest_warehouse')) if request.POST.get('dest_warehouse') else None
        except (ValueError, TypeError):
            src_id = dst_id = None
        src_wh = get_scoped_object_or_404(Warehouse, request, id=src_id) if src_id else None
        dst_wh = get_scoped_object_or_404(Warehouse, request, id=dst_id) if dst_id else None

        dept = voucher.department  # پیش‌فرض: نگه‌داشتن بخشِ قبلی پیش‌نویس
        if voucher.voucher_type in _VOUCHER_DEPARTMENT_TYPES:
            dept_id_str = request.POST.get('department', '').strip()
            if not dept_id_str or not dept_id_str.isdigit():
                messages.error(request, 'بخش گیرنده را انتخاب کنید')
                return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)
            dept = get_scoped_object_or_404(Department, request, id=int(dept_id_str))

        lines, errors = _parse_voucher_lines(request, cafe, voucher.voucher_type)
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)

        # تاریخ شمسی است؛ ساعتِ قبلیِ پیش‌نویس حفظ می‌شود
        date_str = request.POST.get('date', '').strip()
        previous_time = (timezone.localtime(voucher.date).time()
                         if timezone.is_aware(voucher.date) else voucher.date.time())
        vdate, date_ok = _voucher_datetime_from_input(date_str, default_time=previous_time)
        if not date_ok:
            messages.error(request, 'تاریخ واردشده معتبر نیست — نمونهٔ درست: ۱۴۰۵/۰۸/۰۵')
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)

        with transaction.atomic():
            voucher.source_warehouse = src_wh
            voucher.destination_warehouse = dst_wh
            voucher.department = dept
            voucher.date = vdate
            voucher.description = request.POST.get('description', '').strip()
            voucher.save()
            voucher.items.all().delete()
            for line in lines:
                WarehouseVoucherItem.objects.create(voucher=voucher, **line)

        messages.success(request, f'پیش‌نویس {voucher.voucher_number} به‌روزرسانی شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    items = voucher.items.select_related('product', 'product_brand', 'unit').all()
    return _render_voucher_form(request, cafe, voucher.voucher_type,
                                voucher.source_warehouse or voucher.destination_warehouse,
                                {'editing': True, 'edit_voucher': voucher,
                                 'edit_items': items,
                                 'form_date': _format_jalali_date(voucher.date),
                                 'edit_source_wh': voucher.source_warehouse,
                                 'edit_dest_wh': voucher.destination_warehouse,
                                 'selected_department': voucher.department})


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_cancel(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """لغو پیش‌نویس یا حوالهٔ ثبت‌شده (پیش از نهایی‌سازی)"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'متد نامعتبر'}, status=405)
    if voucher.status not in ('draft', 'submitted'):
        messages.error(request, 'فقط پیش‌نویس یا حوالهٔ ثبت‌شدهٔ نهایی‌نشده قابل لغو است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    voucher.status = 'cancelled'
    voucher.cancelled_at = timezone.now()
    voucher.save()
    messages.success(request, f'حوالهٔ {voucher.voucher_number} لغو شد.')
    return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)


@custom_login_required
@role_required(['manager', 'admin', 'ceo'])
def voucher_approve(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """تأیید و نهایی‌سازی حوالهٔ نیازمندِ تأیید (تعدیل) — اقدامِ مدیر = تأیید + اثر فوری"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    if voucher.status != 'submitted' or not (
            voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
            or voucher.requires_approval):
        messages.error(request, 'این حواله در وضعیت نیازمند تأیید نیست.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    if request.method == 'POST':
        try:
            with transaction.atomic():
                v = WarehouseVoucher.objects.select_for_update().get(pk=voucher.pk)
                v.status = 'approved'
                v.approver = request.user
                v.approved_at = timezone.now()
                v.save()
                finalize_voucher(v, finalized_by=request.user)
        except InventoryServiceError as exc:
            messages.error(request, f'تأیید انجام نشد: {exc}')
            return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
        messages.success(request, f'حوالهٔ {voucher.voucher_number} تأیید و نهایی‌سازی شد — اثر روی موجودی اعمال شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return render(request, 'stock/voucher_approve.html', {
        'voucher': voucher,
        'items': voucher.items.select_related('product', 'product_brand', 'unit').all(),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_reverse(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """برگشت حوالهٔ نهایی‌شده — دلیلِ اجباری؛ حوالهٔ آینه‌ای ساخته و نهایی می‌شود"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    if voucher.reverses_id:
        messages.error(request, 'حوالهٔ برگشتی قابل برگشت نیست — حوالهٔ اصلی را برگشت بزنید.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    if voucher.status != 'finalized':
        messages.error(request, 'فقط حوالهٔ نهایی‌شده قابل برگشت است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'دلیل برگشت الزامی است — بدون دلیل برگشت ثبت نمی‌شود.')
            return render(request, 'stock/voucher_reverse.html', {
                'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})
        try:
            reversal = reverse_voucher(voucher, created_by=request.user, description=reason)
        except InventoryServiceError as exc:
            messages.error(request, f'برگشت انجام نشد: {exc}')
            return render(request, 'stock/voucher_reverse.html', {
                'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})
        messages.success(request, f'حوالهٔ {voucher.voucher_number} برگشت خورد — حوالهٔ برگشتی {reversal.voucher.voucher_number} نهایی شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return render(request, 'stock/voucher_reverse.html', {
        'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})


# Warehouse Product Definition Views - تعریف کالا برای انبار
# ─────────────────────────────────────────────────────────────────────────────
@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def warehouse_inventory_check(request: HttpRequest, warehouse_id: int) -> HttpResponse:
    """صفحه انبارگردانی — خلاصهٔ حرکت موجودی از تراکنش‌ها (شِمای جدید):
    همهٔ انواع حرکت با محاسبه یکپارچه در پایگاه‌داده بدون N+1 Query"""
    cafe = get_cafe_from_request(request)
    warehouse = get_scoped_object_or_404(
        Warehouse, request, id=warehouse_id, is_active=True)

    stock_items = StockItem.objects.filter(
        warehouse=warehouse
    ).select_related(
        'product', 'product__category', 'unit'
    ).annotate(
        in_count=Count('transactions', filter=Q(transactions__transaction_type='in')),
        out_count=Count('transactions', filter=Q(transactions__transaction_type='out')),
        in_total=Coalesce(
            Sum('transactions__quantity', filter=Q(transactions__transaction_type='in')),
            Value(0, output_field=DecimalField(max_digits=10, decimal_places=4)),
        ),
        out_total=Coalesce(
            Sum('transactions__quantity', filter=Q(transactions__transaction_type='out')),
            Value(0, output_field=DecimalField(max_digits=10, decimal_places=4)),
        ),
        initial_qty=Coalesce(
            Sum('transactions__quantity', filter=Q(transactions__transaction_type='initial')),
            Value(0, output_field=DecimalField(max_digits=10, decimal_places=4)),
        ),
    ).order_by(
        'product__category__name', 'product__name'
    )

    items_data = [
        {
            'stock_item': si,
            'in_count': si.in_count,
            'out_count': si.out_count,
            'in_total': si.in_total,
            'out_total': si.out_total,
            'initial_qty': si.initial_qty,
            'system_qty': si.quantity,
        }
        for si in stock_items
    ]

    return render(request, 'stock/inventory_check.html', {
        'warehouse':  warehouse,
        'items_data': items_data,
        'cafe_code':  cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def warehouse_inventory_submit(request: HttpRequest, warehouse_id: int) -> HttpResponse:
    """ثبت انبارگردانی - تعدیل موجودی‌ها با یک حوالهٔ تعدیل (adjustment) از سرویس مرکزی.

    - هر ثبت انبارگردانی یک حوالهٔ تعدیل «تأییدشده» می‌سازد (خودِ اقدامِ
      انبارگردان = تأیید) و همان لحظه نهایی‌سازی می‌شود — اثرِ فوری مثل قبل.
    - دلتای هر قلم = موجودی فیزیکی − موجودی فعلی (علامت‌دار).
    - کل ثبت اتمیک است: اگر هر قلمی خطا بدهد (شناسه از انبار دیگر، مقدار
      نامعتبر، کمبود) هیچ‌چیز اعمال نمی‌شود — برخلاف پیاده‌سازی قبلی که
      خطا را برمی‌گرداند ولی تعدیل‌های قبلی را commit کرده بود.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'متد نامعتبر'}, status=405)

    cafe = get_cafe_from_request(request)
    warehouse = get_scoped_object_or_404(
        Warehouse, request, id=warehouse_id, is_active=True)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'داده نامعتبر'}, status=400)

    items = data.get('items', [])

    # کل انبارگردانی یک تراکنش اتمیک — همهٔ تعدیل‌ها با هم اعمال می‌شوند یا هیچ‌کدام
    try:
        with transaction.atomic():
            deltas = []
            for item in items:
                stock_item_id = item.get('stock_item_id')
                physical_qty_str = str(item.get('physical_qty', '')).strip()

                if not physical_qty_str:
                    continue

                try:
                    physical_qty = Decimal(physical_qty_str)
                except Exception as exc:
                    raise _InsufficientStock(
                        f'مقدار نامعتبر برای شناسه {stock_item_id}') from exc
                if physical_qty < 0:
                    raise _InsufficientStock(
                        f'مقدار منفی برای شناسه {stock_item_id} مجاز نیست')

                # موجودی انبار فقط در همین انبار جستجو می‌شود — شناسه‌ای از انبار
                # دیگر خطا می‌دهد و کل ثبت را برمی‌گرداند (رفع خطای انبارِ اشتباه)
                try:
                    si = StockItem.objects.select_related('product_brand', 'unit').get(
                        id=int(stock_item_id), warehouse=warehouse)
                except (StockItem.DoesNotExist, ValueError) as exc:
                    raise _InsufficientStock(
                        f'موجودی انبار با شناسه {stock_item_id} در انبار «{warehouse.name}» یافت نشد') from exc

                delta = (physical_qty - si.quantity).quantize(Decimal('0.0001'))
                if delta == 0:
                    continue  # تغییری لازم نیست — حوالهٔ تعدیل مقدار صفر را نمی‌پذیرد
                deltas.append((si, delta))

            if not deltas:
                return JsonResponse({'success': True, 'updated': 0,
                                     'message': 'موجودی‌ها تغییری نکردند'})

            voucher = WarehouseVoucher(
                cafe=cafe,
                voucher_type='adjustment',
                status='approved',
                approver=request.user,
                approved_at=timezone.now(),
                voucher_number=next_voucher_number(cafe.id, 'adjustment'),
                source_warehouse=warehouse,
                description=f'انبارگردانی انبار «{warehouse.name}» - {timezone.localtime():%Y-%m-%d %H:%M}',
                created_by=request.user,
            )
            voucher.save()

            for si, delta in deltas:
                WarehouseVoucherItem.objects.create(
                    voucher=voucher,
                    product=si.product,
                    product_brand=si.product_brand,
                    stock_item=si,
                    unit=si.unit,
                    base_quantity=delta,
                    description=f'انبارگردانی - تعدیل موجودی از {si.quantity} به {si.quantity + delta}',
                )

            # نهایی‌سازی از سرویس مرکزی: اعتبارسنجی کامل، قفل، و ثبت تراکنش‌های
            # تغییرناپذیر — هر خطایی کل ثبت را (حواله هم) برمی‌گرداند.
            finalize_voucher(voucher, finalized_by=request.user)
    except _InsufficientStock as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    except InventoryServiceError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    return JsonResponse({'success': True, 'updated': len(deltas),
                         'message': f'{len(deltas)} مورد موجودی به‌روزرسانی شد'})


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def api_product_stock_history(request: HttpRequest, warehouse_id: int, stock_item_id: int) -> JsonResponse:
    """API - سوابق حرکتِ موجودی یک محصول در یک انبار (شِمای جدید):

    همهٔ انواع حرکت از ردیف‌های تغییرناپذیر StockTransaction، با لینک به
    حوالهٔ مبدأ (شماره، نوع، وضعیت) — برای برگشتی‌ها به حوالهٔ برگشتی.
    """
    cafe = get_cafe_from_request(request)
    warehouse = get_scoped_object_or_404(Warehouse, request, id=warehouse_id)
    stock_item = get_scoped_object_or_404(
        StockItem, request, id=stock_item_id, warehouse=warehouse)

    txs = StockTransaction.objects.filter(stock_item=stock_item).select_related(
        'voucher', 'created_by').order_by('-created_at')[:100]

    def serialize_tx(tx):
        v = tx.voucher
        return {
            'id': tx.id,
            'date': tx.created_at.strftime('%Y-%m-%d %H:%M'),
            'transaction_type': tx.transaction_type,
            'type_display': dict(StockTransaction.TRANSACTION_TYPE_CHOICES).get(
                tx.transaction_type, tx.transaction_type),
            'quantity': _fmt2(tx.quantity),
            'prev_qty': _fmt2(tx.previous_quantity),
            'new_qty': _fmt2(tx.new_quantity),
            'unit_name': tx.unit.name if tx.unit_id else '',
            'description': tx.description or '',
            'created_by': (tx.created_by.get_full_name() or tx.created_by.username
                           if tx.created_by else '—'),
            'voucher': {
                'id': v.id,
                'voucher_number': v.voucher_number,
                'voucher_type': v.voucher_type,
                'type_display': VOUCHER_TYPE_LABELS.get(v.voucher_type, v.voucher_type),
                'status': v.status,
                'status_display': VOUCHER_STATUS_LABELS.get(v.status, v.status),
            } if v else None,
            'reference_type': tx.reference_type,
        }

    return JsonResponse({
        'success': True,
        'product_name': stock_item.product.name,
        'current_qty': _fmt2(stock_item.quantity),
        'unit_name': stock_item.unit.name if stock_item.unit else '',
        'movements': [serialize_tx(tx) for tx in txs],
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def stock_movements(request: HttpRequest) -> HttpResponse:
    """سوابق حرکت موجودی — همهٔ انواع حرکت با لینک به حوالهٔ مبدأ

    فیلترها: انبار، نوعِ تراکنش، نوعِ حواله، بازهٔ تاریخ، جستجوی شمارهٔ حواله.
    """
    cafe = get_cafe_from_request(request)
    txs = StockTransaction.objects.select_related(
        'stock_item__product', 'stock_item__product_brand',
        'stock_item__warehouse', 'voucher', 'created_by'
    ).filter(stock_item__warehouse__cafe=cafe)

    f = {
        'warehouse': request.GET.get('warehouse'),
        'tx_type': request.GET.get('tx_type'),
        'voucher_type': request.GET.get('voucher_type'),
        # مقدار خامِ شمسی برای نمایش در باکس تاریخ؛ میلادی هم سازگار است
        'date_from': _jalali_input_value(request.GET.get('date_from', '')),
        'date_to': _jalali_input_value(request.GET.get('date_to', '')),
        'search': request.GET.get('search', '').strip(),
    }
    if f['warehouse']:
        txs = txs.filter(stock_item__warehouse_id=f['warehouse'])
    if f['tx_type']:
        txs = txs.filter(transaction_type=f['tx_type'])
    if f['voucher_type']:
        txs = txs.filter(voucher__voucher_type=f['voucher_type'])
    date_from = _parse_jalali_date(f['date_from'])
    if date_from:
        txs = txs.filter(created_at__date__gte=date_from)
    date_to = _parse_jalali_date(f['date_to'])
    if date_to:
        txs = txs.filter(created_at__date__lte=date_to)
    if f['search']:
        txs = txs.filter(voucher__voucher_number__icontains=f['search'])

    txs = txs.order_by('-created_at')
    paginator = Paginator(txs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'stock/stock_movements.html', {
        'page_obj': page_obj,
        'warehouses': Warehouse.objects.filter(cafe=cafe),
        'tx_types': dict(StockTransaction.TRANSACTION_TYPE_CHOICES),
        'voucher_types': VOUCHER_TYPE_LABELS,
        'f': f,
        'cafe_code': cafe.code if cafe else '',
    })


