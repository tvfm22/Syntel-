from __future__ import annotations

from ..models import Category
from ..models import StockItem
from ..models import StockTransaction
from ..models import Warehouse
from ..models import WarehouseVoucher
from ..models import WarehouseVoucherItem
from ..services.inventory import InventoryServiceError
from ..services.inventory import finalize_voucher
from ..services.inventory import next_voucher_number
from ..services.inventory import save_voucher_with_number
from .helpers import VOUCHER_STATUS_LABELS
from .helpers import VOUCHER_TYPE_LABELS
from .helpers import _fmt2
from .helpers import _jalali_input_value
from .helpers import _parse_jalali_date
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from decimal import Decimal
from django.core.paginator import Paginator
from django.db import models
from django.db import transaction
from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import F
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
import json


class _InsufficientStock(Exception):
    """موجودی انبار برای کسر درخواستی کافی نیست"""


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'warehouse'])
def stock_list(request: HttpRequest) -> HttpResponse:
    """لیست موجودی‌های انبار - یک ردیف به ازای هر (محصول، انبار)"""
    cafe = get_cafe_from_request(request)

    # همه انبارهای فعال کافه جاری
    warehouses_qs = Warehouse.objects.filter(is_active=True)
    if cafe:
        warehouses_qs = warehouses_qs.filter(cafe=cafe)

    # فیلتر انبار انتخاب‌شده
    warehouse_id = (request.GET.get('warehouse') or '').strip()
    selected_warehouse = None
    if warehouse_id.isdigit():
        selected_warehouse = warehouses_qs.filter(id=int(warehouse_id)).first()

    stock_items = StockItem.objects.select_related(
        'product', 'unit', 'warehouse', 'product__unit', 'product__category'
    ).order_by('warehouse__name', 'product__name')
    if selected_warehouse:
        stock_items = stock_items.filter(warehouse=selected_warehouse)
    elif cafe:
        stock_items = stock_items.filter(warehouse__cafe=cafe)

    # فیلتر بر اساس موجودی کم
    show_low_stock = request.GET.get('low_stock')
    if show_low_stock:
        stock_items = stock_items.filter(quantity__lte=models.F('min_quantity'))

    # جستجو (نام محصول، دسته‌بندی)
    search_query = request.GET.get('search')
    if search_query:
        stock_items = stock_items.filter(
            Q(product__name__icontains=search_query) |
            Q(product__category__name__icontains=search_query)
        ).distinct()

    # آیتم‌های قدیمی ممکن است واحد نداشته باشند - واحد محصول را جایگزین کن
    for si in stock_items:
        if si.unit_id is None and si.product and si.product.unit_id:
            si.unit = si.product.unit

    # تجمیع به تفکیک (محصول، انبار) - یک ردیف برای هر محصول در هر انبار
    groups_dict = {}
    for si in stock_items:
        key = (si.product_id, si.warehouse_id)
        group = groups_dict.setdefault(key, {
            'product': si.product,
            'warehouse': si.warehouse,
            'first_item': si,
            'total_quantity': Decimal('0'),
            'total_min_quantity': Decimal('0'),
        })
        group['total_quantity'] += si.quantity
        group['total_min_quantity'] += si.min_quantity

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
    stock_item = get_scoped_object_or_404(StockItem, request, id=stock_id)
    transactions = stock_item.transactions.select_related('created_by').all()

    # صفحه‌بندی
    paginator = Paginator(transactions, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'stock/stock_transactions.html', {
        'stock_item': stock_item,
        'page_obj': page_obj
    })


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


def _parse_bool_flag(raw, default=False):
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = str(raw).strip().lower()
    if s in ('1', 'true', 'yes', 'on'):
        return True
    if s in ('0', 'false', 'no', 'off', ''):
        return False
    return default


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
        is_supply_enabled=_parse_bool_flag(data.get('is_supply_enabled'), default=True),
        allow_negative_stock=_parse_bool_flag(data.get('allow_negative_stock'), default=False),
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
            'allow_negative_stock': warehouse.allow_negative_stock,
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
                'allow_negative_stock': warehouse.allow_negative_stock,
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
    warehouse.is_active = _parse_bool_flag(data.get('is_active'), default=True)
    warehouse.is_supply_enabled = _parse_bool_flag(data.get('is_supply_enabled'), default=True)
    if 'allow_negative_stock' in data:
        warehouse.allow_negative_stock = _parse_bool_flag(
            data.get('allow_negative_stock'), default=warehouse.allow_negative_stock
        )
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
    if not isinstance(data, dict):
        return JsonResponse({'success': False, 'error': 'داده نامعتبر'}, status=400)

    items = data.get('items', [])
    if not isinstance(items, list):
        return JsonResponse({'success': False, 'error': 'فهرست اقلام نامعتبر است'}, status=400)

    # اعتبارسنجی اولیه و یکتاسازی شناسه‌های تکراری پیش از قفل‌کردن
    parsed_by_id = {}
    for item in items:
        if not isinstance(item, dict):
            return JsonResponse({'success': False, 'error': 'قالب قلم نامعتبر است'}, status=400)
        stock_item_id_raw = item.get('stock_item_id')
        physical_qty_str = str(item.get('physical_qty', '')).strip()
        if not physical_qty_str:
            continue

        try:
            si_id = int(stock_item_id_raw)
        except (ValueError, TypeError):
            return JsonResponse(
                {'success': False, 'error': f'شناسه نامعتبر: {stock_item_id_raw}'},
                status=400,
            )

        try:
            physical_qty = Decimal(physical_qty_str)
        except Exception:
            return JsonResponse(
                {'success': False, 'error': f'مقدار نامعتبر برای شناسه {si_id}'},
                status=400,
            )
        if not physical_qty.is_finite() or physical_qty < 0:
            return JsonResponse(
                {'success': False, 'error': f'مقدار منفی یا نامعتبر برای شناسه {si_id} مجاز نیست'},
                status=400,
            )
        parsed_by_id[si_id] = physical_qty

    if not parsed_by_id:
        return JsonResponse({'success': True, 'updated': 0, 'message': 'موجودی‌ها تغییری نکردند'})

    # کل انبارگردانی یک تراکنش اتمیک با قفل ردیف‌ها به ترتیب id
    try:
        with transaction.atomic():
            locked_items = list(
                StockItem.objects.select_for_update()
                .select_related('unit', 'product')
                .filter(id__in=sorted(parsed_by_id.keys()), warehouse=warehouse)
                .order_by('id')
            )
            locked_map = {si.id: si for si in locked_items}
            deltas = []
            for si_id in sorted(parsed_by_id.keys()):
                si = locked_map.get(si_id)
                if si is None:
                    raise _InsufficientStock(
                        f'موجودی انبار با شناسه {si_id} در انبار «{warehouse.name}» یافت نشد')
                physical_qty = parsed_by_id[si_id]
                delta = (physical_qty - si.quantity).quantize(Decimal('0.0001'))
                if delta == 0:
                    continue
                deltas.append((si, delta))

            if not deltas:
                return JsonResponse({'success': True, 'updated': 0,
                                     'message': 'موجودی‌ها تغییری نکردند'})

            is_approver = bool(
                getattr(request.user, 'is_superuser', False)
                or getattr(request.user, 'role', None) in ('manager', 'admin', 'ceo')
                or getattr(request.user, 'secondary_role', None) in ('manager', 'admin', 'ceo')
            )

            voucher = WarehouseVoucher(
                cafe=cafe,
                voucher_type='adjustment',
                status='approved' if is_approver else 'submitted',
                approver=request.user if is_approver else None,
                approved_at=timezone.now() if is_approver else None,
                voucher_number=next_voucher_number(cafe.id, 'adjustment'),
                source_warehouse=warehouse,
                description=f'انبارگردانی انبار «{warehouse.name}» - {timezone.localtime():%Y-%m-%d %H:%M}',
                created_by=request.user,
            )
            save_voucher_with_number(voucher, cafe.id, 'adjustment')

            for si, delta in deltas:
                WarehouseVoucherItem.objects.create(
                    voucher=voucher,
                    product=si.product,
                    stock_item=si,
                    unit=si.unit,
                    base_quantity=delta,
                    description=f'انبارگردانی - تعدیل موجودی از {si.quantity} به {si.quantity + delta}',
                )

            if is_approver:
                finalize_voucher(voucher, finalized_by=request.user)
    except _InsufficientStock as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    except InventoryServiceError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    if not is_approver:
        return JsonResponse({
            'success': True,
            'updated': len(deltas),
            'pending_approval': True,
            'voucher_id': voucher.id,
            'voucher_number': voucher.voucher_number,
            'message': f'سند انبارگردانی {voucher.voucher_number} با {len(deltas)} قلم ثبت شد و در انتظار تأیید مدیر است',
        })

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
        'stock_item__product',
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
    if f['warehouse'] and str(f['warehouse']).strip().isdigit():
        txs = txs.filter(stock_item__warehouse_id=int(f['warehouse']))
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
