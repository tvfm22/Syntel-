from __future__ import annotations

import io
import csv
import json
import os
import platform
import re
import secrets
import shutil
import zipfile
import django
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from pathlib import Path

import psutil
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from django.db.models import Q, Sum, F, Count
from django.db import connections, models, transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse, HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from . import drive_backup
from .services.recipe_gate import (
    PRODUCT_MANAGER_ROLES,
    RD_CATEGORY_NAME,
    assign_rd_category,
    bypasses_recipe_gate,
    filter_requestable_products,
    inactive_product_error,
    is_rd_product,
    recipe_gate_error,
    recipe_required_category_ids,
)
from .services.receipt_gate import (
    pending_receipt_count,
    receipt_gate_context,
    receipt_gate_message,
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
from .models import (
    ActivityLog,
    Cafe,
    Category,
    DEPARTMENT_CHOICES,
    Debt,
    DebtPayment,
    Department,
    Invoice,
    InvoiceIssue,
    InvoiceItem,
    KukerItem,
    KukerRecipeLine,
    Notification,
    OfficerAssignment,
    Payment,
    PaymentAccount,
    PaymentLocation,
    PaymentType,
    PriceHistory,
    Product,
    ProductPriceEntry,
    PurchaseDraft,
    PurchaseDraftItem,
    PurchaseItem,
    PurchaseOrder,
    PurchaseRequest,
    Requester,
    Supplier,
    SupplierEvaluation,
    Unit,
    StockItem,
    StockTransaction,
    StockRequest,
    StockRequestItem,
    Warehouse,
    WarehouseVoucher,
    WarehouseVoucherItem,
    WarehouseRequest,
    WarehouseRequestItem,
)
from .numbering import request_number as request_number_for
from .middleware import get_current_cafe, get_cafe_code

User = get_user_model()


# ---------------------------------------------------------------------------
# Cafe Helpers and Decorators
# ---------------------------------------------------------------------------

def cafe_view(view_func):
    """
    Decorator که پارامتر cafe_code را از URL می‌گیرد و نادیده می‌گیرد.
    اطلاعات کافه از middleware در request.cafe قرار دارد.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # حذف cafe_code از kwargs چون middleware آن را handle می‌کند
        kwargs.pop('cafe_code', None)
        return view_func(request, *args, **kwargs)
    return wrapper


def get_cafe_from_request(request):
    """گرفتن کافه فعلی از request یا session"""
    if hasattr(request, 'cafe') and request.cafe:
        return request.cafe
    
    cafe_id = request.session.get('selected_cafe_id')
    if cafe_id:
        try:
            return Cafe.objects.get(id=cafe_id)
        except Cafe.DoesNotExist:
            return None
    return None


# مسیر رسیدن به کافه برای هر مدل — هر مدلی که خارج از این فهرست است برای
# جستجوهای کافه‌محور «ممنوع» است (شکستِ بسته: fallback بی‌scope وجود ندارد).
CAFE_LOOKUP_PATHS = {
    'WarehouseVoucher':     'cafe',
    'WarehouseVoucherItem': 'voucher__cafe',
    'StockItem':            'warehouse__cafe',
    'StockTransaction':     'stock_item__warehouse__cafe',
    'Warehouse':            'cafe',
    'Product':              'cafe',
    'Unit':                 'cafe',
    'Category':             'cafe',
    'Department':           'cafe',
    'Supplier':             'cafe',
    'Requester':            'cafe',
    'Invoice':              'cafe',
    'InvoiceItem':          'invoice__cafe',
    'PurchaseRequest':      'cafe',
    'PurchaseItem':         'request__cafe',
    'WarehouseRequest':     'cafe',
    'WarehouseRequestItem': 'warehouse_request__cafe',
    'OfficerAssignment':    'item__request__cafe',
    'StockRequest':         'purchase_item__request__cafe',
    'StockRequestItem':     'stock_request__purchase_item__request__cafe',
    'PurchaseDraft':        'cafe',
    'PurchaseDraftItem':    'draft__cafe',
    # منو / بهای تمام شده
    'KukerItem':            'cafe',
    'MenuSection':          'cafe',
    'CogsReport':           'cafe',
    'KukerItemCostHistory': 'item__cafe',
}


def get_scoped_object_or_404(model, request, **lookup):
    """دریافت یک رکورد با قفلِ کافه‌محور — بدون کافهٔ فعال همیشه 404 (fail-closed).

    - کافه از request/session گرفته می‌شود؛ اگر نبود → Http404 (حتی اگر رکوردی
      با cafe=NULL موجود باشد — هیچ fallback خارج از scope نیست).
    - مدلی که در CAFE_LOOKUP_PATHS نیست → Http404 (ناشناخته = عمداً ممنوع).
    """
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    path = CAFE_LOOKUP_PATHS.get(model.__name__)
    if path is None:
        raise Http404(f'مدل {model.__name__} در CAFE_LOOKUP_PATHS نیست')
    lookup[path] = cafe
    return get_object_or_404(model, **lookup)


def filter_by_cafe(queryset, request):
    """فیلتر کردن queryset بر اساس کافهٔ فعلی — شکستِ بسته:

    بدون کافهٔ فعال یا مدلِ بدون مسیرِ کافه → queryset خالی، نه بازگشتِ بی‌scope.
    """
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return queryset.none()
    path = CAFE_LOOKUP_PATHS.get(queryset.model.__name__)
    if path is None:
        return queryset.none()
    return queryset.filter(**{path: cafe})


def get_scoped_locked_object_or_404(model, request, **lookup):
    """مثل get_scoped_object_or_404، ولی ردیف را با select_for_update قفل می‌کند.

    باید داخل transaction.atomic() صدا زده شود؛ بدون کافهٔ فعال یا مدلِ
    بدون مسیرِ کافه همیشه Http404 می‌دهد (fail-closed).
    """
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    path = CAFE_LOOKUP_PATHS.get(model.__name__)
    if path is None:
        raise Http404(f'مدل {model.__name__} در CAFE_LOOKUP_PATHS نیست')
    lookup[path] = cafe
    return get_object_or_404(model.objects.select_for_update(), **lookup)


def get_cafe_redirect_url(url_name, request, **kwargs):
    """ساخت URL با cafe_code"""
    cafe_code = get_cafe_code(request)
    if cafe_code:
        kwargs['cafe_code'] = cafe_code
    return redirect(url_name, **kwargs)


def get_cafe_url(url_name, request, **kwargs):
    """مثل get_cafe_redirect_url ولی رشتهٔ URL برمیگرداند (برای افزودن query string)"""
    cafe_code = get_cafe_code(request)
    if cafe_code:
        kwargs['cafe_code'] = cafe_code
    return reverse(url_name, kwargs=kwargs)


def cafe_context(request):
    """اضافه کردن اطلاعات کافه به context"""
    cafe = get_cafe_from_request(request)
    return {
        'current_cafe': cafe,
        'cafe_code': cafe.code if cafe else None,
        'can_switch_cafe': request.user.is_authenticated and request.user.can_switch_cafe,
        'all_cafes': Cafe.objects.filter(is_active=True) if request.user.is_authenticated and request.user.can_switch_cafe else [],
    }


# ---------------------------------------------------------------------------
# Helpers and decorators
# ---------------------------------------------------------------------------

def custom_login_required(view_func):
    """Wrapper around login_required to keep a single login URL."""
    return login_required(view_func, login_url="login")


def role_required(roles):
    """
    Simple role-based access control.
    
    کاربران با نقش 'admin' و superuser ها به همه صفحات دسترسی دارند.
    نقش دوم (secondary_role) هم در نظر گرفته می‌شود.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")
            
            # Admin و superuser به همه جا دسترسی دارند
            user_role = getattr(request.user, "role", None)
            user_secondary_role = getattr(request.user, "secondary_role", None)
            if not (user_role == 'admin' or request.user.is_superuser):
                # برای سایر کاربران، چک کردن نقش اصلی یا نقش دوم
                if roles and user_role not in roles:
                    # چک کردن نقش دوم
                    if not (user_secondary_role and user_secondary_role in roles):
                        return redirect("login")
            
            if not request.user.is_active:
                logout(request)
                return redirect("login")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def _parse_decimal(value):
    try:
        # جداکننده سه‌رقمی (کاما) را حذف کن — کاربران با کاما وارد می‌کنند
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return None


def _fmt2(value):
    """نمایش عددی موجودی: حداکثر ۲ رقم اعشار؛ رقم‌های صفرِ آخر حذف می‌شوند.

    هم‌معنای فیلتر قالبی |trim_decimal — برای رشته‌سازی اعداد در view ها
    (پیام‌ها و JSON) که از فیلتر قالب نمی‌گذرند:
    25.00 → «25»  |  12.50 → «12.5»  |  8.333… → «8.33»
    """
    try:
        d = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return format(d.normalize(), 'f')
    except (ValueError, TypeError, ArithmeticError):
        return str(value)


def _warehouse_request_status(statuses):
    """وضعیت نهایی درخواست انبار از وضعیت اقلام آن.

    نتیجهٔ مختلط (مثلاً بخشی از انبار و بخشی به خرید) «fulfilled» نیست؛
    وضعیت جداگانهٔ «partial» می‌گیرد تا با درخواست کاملاً تامین‌شده اشتباه نشود.
    """
    statuses = set(statuses)
    if not statuses:
        return 'pending'
    if statuses == {'from_stock'}:
        return 'approved_from_stock'
    if statuses == {'to_purchase'}:
        return 'approved_purchase'
    if statuses == {'declined'}:
        return 'declined'
    return 'partial'


def _parse_iso_date(value):
    """پارس تاریخ ISO (YYYY-MM-DD یا YYYY/MM/DD)؛ در صورت نامعتبر None"""
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None




# ---------------------------------------------------------------------------
# Authentication, Users, Departments & Requesters (extracted to views_accounts.py)
# ---------------------------------------------------------------------------
from .views_accounts import (
    _get_departments,
    _redirect_user_home,
    login_view,
    select_cafe,
    logout_view,
    department_list,
    department_create,
    department_update,
    department_delete,
    user_list,
    user_create,
    user_update,
    user_delete,
    user_reset_password,
    user_impersonate,
    user_impersonate_stop,
    requester_list,
    requester_create,
    requester_delete,
)


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["kitchen", "bar", "counter", "cashier", "facilities", "requester"])
def mobile_dashboard(request: HttpRequest) -> HttpResponse:
    """Simple mobile-friendly dashboard for department users"""
    cafe = get_cafe_from_request(request)
    # تعداد اقلامِ تحویل‌گرفته‌شده‌ای که هنوز «تایید دریافت» نشده‌اند (نشانِ بخش
    # «کالاهای دریافتی من» روی همین داشبورد)
    received_pending = [r for r in _received_delivery_rows(request.user, cafe)
                        if r['confirmed_at'] is None]
    return render(request, "mobile_dashboard.html", {
        "user": request.user,
        "cafe": cafe,
        "received_pending_count": len(received_pending),
    })


@custom_login_required
@role_required(["purchasing", "warehouse"])
def purchasing_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard for purchasing/warehouse manager to review requests by department"""
    # Count requests by role
    departments = [
        {'name': 'آشپزخانه', 'role': 'kitchen', 'icon': '🍳'},
        {'name': 'بار', 'role': 'bar', 'icon': '🍸'},
        {'name': 'کانتر', 'role': 'counter', 'icon': '🛎️'},
        {'name': 'انبار', 'role': 'store', 'icon': '📦'},
        {'name': 'صندوق', 'role': 'cashier', 'icon': '💰'},
        {'name': 'تاسیسات', 'role': 'facilities', 'icon': '🔧'},
    ]
    
    cafe = get_cafe_from_request(request)
    for dept in departments:
        qs = PurchaseRequest.objects.filter(user__role=dept['role'])
        if cafe:
            qs = qs.filter(cafe=cafe)
        dept['total'] = qs.count()
        dept['pending'] = qs.filter(status='pending').count()

    has_purchasing = request.user.has_purchasing_role
    has_warehouse = request.user.has_warehouse_role
    has_dual = request.user.has_dual_role

    cafe_code = cafe.code if cafe else 'local'

    return render(request, "purchasing_dashboard.html", {
        "user": request.user,
        "departments": departments,
        "has_purchasing": has_purchasing,
        "has_warehouse": has_warehouse,
        "has_dual": has_dual,
        "cafe_code": cafe_code,
    })


@custom_login_required
@role_required(["purchasing", "warehouse"])
def purchasing_requests_list(request: HttpRequest) -> HttpResponse:
    """List purchase/warehouse requests filtered by department role and status"""
    role = request.GET.get('role', '')
    status = request.GET.get('status', '')
    request_type = request.GET.get('type', 'purchase')  # 'purchase' or 'warehouse'

    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    if request_type == 'warehouse':
        requests = WarehouseRequest.objects.all().select_related('user').prefetch_related('items')
        if cafe:
            requests = requests.filter(cafe=cafe)
        if role:
            requests = requests.filter(user__role=role)
        if status == 'pending':
            requests = requests.filter(status='pending')
        requests = requests.order_by('-created_at')
    else:
        requests = PurchaseRequest.objects.all().select_related('user').prefetch_related('items')
        if cafe:
            requests = requests.filter(cafe=cafe)
        if role:
            requests = requests.filter(user__role=role)
        if status == 'pending':
            requests = requests.filter(status='pending')
        requests = requests.order_by('-created_at')

    # Get department name
    department_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'store': 'انبار',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    department_name = department_names.get(role, 'همه بخش‌ها')

    return render(request, "purchasing_requests_list.html", {
        "requests": requests,
        "role": role,
        "status": status,
        "department_name": department_name,
        "cafe_code": cafe_code,
        "request_type": request_type,
    })


@custom_login_required
@role_required(["purchasing", "warehouse"])
def process_purchase_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """Process purchase request items - set quantities and supply status"""
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    if request.method == "POST":
        # Process each item
        for item in purchase_request.items.all():
            purchased_qty = request.POST.get(f'purchased_qty_{item.id}', '')
            supply_status = request.POST.get(f'supply_status_{item.id}', 'pending')
            
            if purchased_qty:
                item.purchased_quantity = _parse_decimal(purchased_qty) or 0
            item.supply_status = supply_status
            item.save()

            # (جریان قدیمی «تامین از انبار» با سیستم حواله‌ها جایگزین شده و حذف گردید)

        # اگر گزینه «ارجاع به خرید» برای اقلامی که از لیست انبار آمده‌اند
        # این اقلام را به یک درخواست خرید جدید اضافه کن
        items_to_purchase = [
            item for item in purchase_request.items.all()
            if request.POST.get(f'supply_status_{item.id}') == 'purchased'
        ]

        # Update request status if all items are processed
        all_processed = all(
            item.supply_status != 'pending' 
            for item in purchase_request.items.all()
        )
        if all_processed:
            purchase_request.status = 'purchased'
            purchase_request.processed_by = request.user
            purchase_request.processed_at = timezone.now()
            purchase_request.save()
            messages.success(request, 'درخواست با موفقیت پردازش شد')
        
        return redirect('purchasing_requests_list', cafe_code=cafe_code)
    
    # Get stock items for search
    stock_items = StockItem.objects.select_related('product', 'unit', 'warehouse').filter(quantity__gt=0)
    if cafe:
        stock_items = stock_items.filter(warehouse__cafe=cafe)

    return render(request, "process_purchase_request.html", {
        "purchase_request": purchase_request,
        "stock_items": stock_items,
        "cafe_code": cafe_code,
    })


# ---------------------------------------------------------------------------
# Warehouse Request (درخواست از انبار) Views
# ---------------------------------------------------------------------------

@custom_login_required
def warehouse_request_form(request: HttpRequest) -> HttpResponse:
    """فرم ثبت درخواست از انبار توسط درخواست‌دهندگان (آشپزخانه، بار، تاسیسات و...)"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    # Receipt gate — same source of truth as the main goods-request form.
    receipt_gate = receipt_gate_context(request.user, cafe, cafe_code)

    if request.method == 'POST':
        product_names = request.POST.getlist('product_name')
        quantities = request.POST.getlist('item_quantity')
        units = request.POST.getlist('item_unit')
        descriptions = request.POST.getlist('item_description')
        product_ids = request.POST.getlist('item_product_id')

        items_data = []
        submitted_items = []   # برای برگرداندنِ همان ردیف‌ها به فرم بعد از خطا
        blocked = []          # کالاهای غذاییِ بدون رسپی — قابل سفارش نیستند
        for idx, name in enumerate(product_names):
            name = name.strip() if name else ''
            if not name:
                continue
            qty_raw = quantities[idx] if idx < len(quantities) else ''
            qty_val = _parse_decimal(qty_raw)
            unit_val = units[idx].strip() if idx < len(units) and units[idx] else 'عدد'
            desc_val = descriptions[idx] if idx < len(descriptions) else ''
            product_id_raw = product_ids[idx] if idx < len(product_ids) else ''
            submitted_items.append({
                'name': name,
                'quantity': str(qty_raw or ''),
                'unit': unit_val,
                'description': desc_val,
                'productId': product_id_raw,
            })
            if qty_val is None or qty_val <= 0:
                continue
            # محصول انتخاب‌شده از لیست پیشنهادها (اگر انتخاب نشده باشد با نام جستجو می‌شود)
            # شناسهٔ خامِ POST هرگز بدون قفلِ کافه واکشی نمی‌شود؛ در غیر این صورت
            # امکان وصل‌کردن محصولِ کافهٔ دیگر به درخواست همین کافه فراهم می‌شد.
            item_product = None
            if product_id_raw.isdigit():
                qs_p = Product.objects.filter(id=int(product_id_raw))
                if cafe is not None:
                    qs_p = qs_p.filter(cafe=cafe)
                candidate = qs_p.first()
                if candidate is not None and candidate.name.strip().lower() == name.lower():
                    item_product = candidate
            if item_product is None:
                item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else Product.objects.filter(name__iexact=name).first()
            if item_product is None and not bypasses_recipe_gate(request.user):
                blocked.append(
                    f'کالای «{name}» در فهرست محصولات ثبت نشده است. '
                    'لطفاً کالا را از فهرست انتخاب کنید یا به‌عنوان کالای جدید ثبت نمایید.'
                )
                continue
            inactive_err = inactive_product_error(item_product)
            if inactive_err:
                blocked.append(inactive_err)
                continue
            # قاعدهٔ رسپی: کالای غذاییِ بدون رسپی از این فرم هم قابل درخواست نیست.
            if item_product is not None and not bypasses_recipe_gate(request.user):
                gate_error = recipe_gate_error(item_product, cafe=cafe)
                if gate_error:
                    blocked.append(gate_error)
                    continue
            items_data.append((name, qty_val, unit_val, desc_val, item_product))

        if blocked:
            for blocked_message in blocked[:3]:
                messages.error(request, blocked_message)
            return render(request, 'warehouse_request_form.html', {
                'cafe_code': cafe_code, 'submitted_items': submitted_items,
                'receipt_gate': receipt_gate})

        if not items_data:
            messages.error(request, 'حداقل یک قلم کالا وارد کنید')
            return render(request, 'warehouse_request_form.html', {
                'cafe_code': cafe_code, 'submitted_items': submitted_items,
                'receipt_gate': receipt_gate})

        # Receipt gate — same rule as the main goods-request form, enforced on
        # the server so a crafted POST cannot slip past a disabled button.
        pending_receipts = pending_receipt_count(request.user, cafe)
        if pending_receipts:
            messages.error(request, receipt_gate_message(pending_receipts))
            return render(request, 'warehouse_request_form.html', {
                'cafe_code': cafe_code, 'submitted_items': submitted_items,
                'receipt_gate': receipt_gate})

        wh_request = WarehouseRequest.objects.create(
            user=request.user,
            cafe=cafe,
            status='pending',
        )
        for name, qty, unit, desc, item_product in items_data:
            WarehouseRequestItem.objects.create(
                warehouse_request=wh_request,
                product=item_product,
                name=name,
                quantity=qty,
                unit=unit,
                description=desc,
            )

        Notification.objects.create(
            recipient=request.user,
            title='درخواست از انبار ثبت شد',
            message='درخواست شما از انبار ثبت گردید.',
            notification_type='info',
            cafe=cafe,
        )
        return redirect('warehouse_request_success', cafe_code=cafe_code)

    return render(request, 'warehouse_request_form.html', {
        'cafe_code': cafe_code, 'submitted_items': [],
        'receipt_gate': receipt_gate})


@custom_login_required
def warehouse_request_success(request: HttpRequest) -> HttpResponse:
    """صفحه موفقیت پس از ثبت درخواست انبار"""
    return render(request, 'warehouse_request_success.html')


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def process_warehouse_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """پردازش درخواست انبار توسط مسئول خرید یا مسئول انبار"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    # موجودی‌های انبار برای جستجو — فقط همین کافه
    stock_items = (
        StockItem.objects.select_related('product', 'unit', 'warehouse')
        .filter(quantity__gt=0, warehouse__cafe=cafe)
    )

    if request.method == 'POST':
        try:
            with transaction.atomic():
                wh_request = get_scoped_locked_object_or_404(
                    WarehouseRequest, request, id=request_id)

                # قفل و بازبینی وضعیت بعد از قفل: درخواست فقط یک‌بار پردازش می‌شود
                if wh_request.status != 'pending':
                    messages.warning(request, 'این درخواست قبلاً پردازش شده است.')
                    return redirect('purchasing_requests_list', cafe_code=cafe_code)

                items = list(wh_request.items.select_for_update())
                if not items:
                    messages.error(request, 'این درخواست هیچ قلمی ندارد.')
                    return render(request, 'process_warehouse_request.html', {
                        'wh_request': wh_request,
                        'stock_items': stock_items,
                        'cafe_code': cafe_code,
                    })

                allowed_statuses = {'from_stock', 'to_purchase', 'declined'}
                planned = []          # (item, status, stock_item, qty)
                items_to_purchase = []
                has_error = False

                # فقط اعتبارسنجی؛ هیچ نوشتنی قبل از موفقیت کامل انجام نمی‌شود
                for item in items:
                    supply_status = request.POST.get(f'supply_status_{item.id}', '')
                    if supply_status not in allowed_statuses:
                        messages.error(
                            request,
                            f'وضعیت تامین قلم «{item.name}» معتبر نیست.')
                        has_error = True
                        continue

                    if supply_status == 'from_stock':
                        stock_item_id = request.POST.get(f'stock_item_{item.id}', '')
                        supplied_qty = _parse_decimal(
                            request.POST.get(f'supplied_qty_{item.id}', ''))
                        if not stock_item_id or supplied_qty is None or supplied_qty <= 0:
                            messages.error(
                                request,
                                f'برای قلم «{item.name}» موجودی انبار و مقدار معتبر انتخاب کنید.')
                            has_error = True
                            continue
                        try:
                            s_item = get_scoped_locked_object_or_404(
                                StockItem, request, id=int(stock_item_id))
                        except (Http404, ValueError, TypeError):
                            messages.error(request, 'موجودی انبار یافت نشد')
                            has_error = True
                            continue

                        if item.product_id is None or s_item.product_id != item.product_id:
                            messages.error(
                                request,
                                f'موجودی انبار با کالای «{item.name}» مطابقت ندارد')
                            has_error = True
                            continue
                        if supplied_qty > item.quantity:
                            messages.error(
                                request,
                                f'مقدار تامین‌شده برای «{item.name}» از مقدار درخواستی بیشتر است.')
                            has_error = True
                            continue
                        if supplied_qty > s_item.quantity:
                            messages.error(
                                request,
                                f'موجودی {s_item.product.name} در انبار ({s_item.quantity}) '
                                f'کمتر از مقدار درخواستی ({supplied_qty}) است!')
                            has_error = True
                            continue
                        planned.append((item, supply_status, s_item, supplied_qty))
                    elif supply_status == 'to_purchase':
                        planned.append((item, supply_status, None, None))
                        items_to_purchase.append(item)
                    else:  # declined
                        planned.append((item, supply_status, None, None))

                if has_error:
                    return render(request, 'process_warehouse_request.html', {
                        'wh_request': wh_request,
                        'stock_items': stock_items,
                        'cafe_code': cafe_code,
                    })

                # حالا نوشتن — همه در همان atomic
                for item, supply_status, s_item, supplied_qty in planned:
                    item.supply_status = supply_status
                    item.stock_item = s_item if supply_status == 'from_stock' else None
                    item.supplied_quantity = (
                        supplied_qty if supply_status == 'from_stock' else None)
                    item.save(update_fields=[
                        'supply_status', 'stock_item', 'supplied_quantity'])

                    if supply_status == 'from_stock':
                        apply_stock_delta(
                            s_item, -supplied_qty,
                            cafe=cafe, created_by=request.user,
                            reference_type='warehouse_request',
                            reference_id=wh_request.id,
                            description=f'تامین درخواست انبار {request_number_for(wh_request.id)} - {item.name}',
                        )

                # اگر اقلامی به خرید ارجاع شدند یک درخواست خرید جدید ایجاد می‌شود
                if items_to_purchase:
                    new_purchase = PurchaseRequest.objects.create(
                        user=wh_request.user,
                        cafe=wh_request.cafe,
                        status='pending',
                    )
                    wh_request.converted_to_purchase = new_purchase
                    new_purchase_items = []
                    for item in items_to_purchase:
                        pi = PurchaseItem.objects.create(
                            request=new_purchase,
                            product=item.product,
                            name=item.name,
                            quantity=item.quantity,
                            unit=item.unit,
                            description=item.description or '',
                        )
                        new_purchase_items.append(pi)
                    # همه درخواست‌های کالا مستقیماً برای مسئول انبار ارسال می‌شوند
                    _route_request_to_warehouse(
                        new_purchase, new_purchase_items, request.user,
                        notes='ارسال مستقیم از فرم درخواست کالا')
                    messages.info(request, f'{len(items_to_purchase)} قلم به لیست خرید ارجاع شد')

                # تعیین وضعیت نهایی درخواست انبار (نتیجهٔ مختلط → partial)
                wh_request.status = _warehouse_request_status(
                    [p[1] for p in planned])
                wh_request.processed_by = request.user
                wh_request.processed_at = timezone.now()
                wh_request.save(update_fields=[
                    'status', 'processed_by', 'processed_at', 'converted_to_purchase'])
        except (InsufficientStockError, StockScopeError) as exc:
            messages.error(request, f'خطا در تامین: {exc}')
            return render(request, 'process_warehouse_request.html', {
                'wh_request': wh_request,
                'stock_items': stock_items,
                'cafe_code': cafe_code,
            })

        messages.success(request, 'درخواست انبار با موفقیت پردازش شد')
        return redirect('purchasing_requests_list', cafe_code=cafe_code)

    wh_request = get_scoped_object_or_404(WarehouseRequest, request, id=request_id)
    return render(request, 'process_warehouse_request.html', {
        'wh_request': wh_request,
        'stock_items': stock_items,
        'cafe_code': cafe_code,
    })


@custom_login_required
def api_search_stock_items(request: HttpRequest) -> JsonResponse:
    """API جستجوی موجودی انبار برای autocomplete"""
    query = request.GET.get('q', '')
    cafe = get_cafe_from_request(request)

    qs = StockItem.objects.select_related('product', 'unit', 'warehouse').filter(quantity__gt=0)
    if cafe:
        qs = qs.filter(warehouse__cafe=cafe)
    if query:
        qs = qs.filter(product__name__icontains=query)

    results = []
    for si in qs[:20]:
        results.append({
            'id': si.id,
            'product_name': si.product.name,
            'quantity': float(si.quantity),
            'unit': si.unit.name if si.unit else '',
            'warehouse': si.warehouse.name if si.warehouse else '',
        })
    return JsonResponse({'results': results})


@custom_login_required
def warehouse_requests_list(request: HttpRequest) -> HttpResponse:
    """لیست درخواست‌های انبار برای کاربران درخواست‌دهنده"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    
    requests_qs = WarehouseRequest.objects.filter(user=request.user).prefetch_related('items').order_by('-created_at')
    if cafe:
        requests_qs = requests_qs.filter(cafe=cafe)
    
    return render(request, 'warehouse_requests_list.html', {
        'requests': requests_qs,
        'cafe_code': cafe_code,
    })


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def purchasing_warehouse_requests_list(request: HttpRequest) -> HttpResponse:
    """لیست درخواست‌های انبار برای مسئول خرید/انبار"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    role_filter = request.GET.get('role', '')
    status_filter = request.GET.get('status', '')

    qs = WarehouseRequest.objects.select_related('user').prefetch_related('items').order_by('-created_at')
    if cafe:
        qs = qs.filter(cafe=cafe)
    if role_filter:
        qs = qs.filter(user__role=role_filter)
    if status_filter == 'pending':
        qs = qs.filter(status='pending')

    department_names = {
        'kitchen': 'آشپزخانه', 'bar': 'بار', 'counter': 'کانتر',
        'store': 'انبار', 'cashier': 'صندوق', 'facilities': 'تاسیسات',
    }
    department_name = department_names.get(role_filter, 'همه بخش‌ها')

    return render(request, 'purchasing_warehouse_requests_list.html', {
        'requests': qs,
        'role': role_filter,
        'status': status_filter,
        'department_name': department_name,
        'cafe_code': cafe_code,
    })


@custom_login_required
@role_required(['manager', 'purchasing'])
def purchase_list_with_stock_supply(request: HttpRequest) -> HttpResponse:
    """لیست خرید با قابلیت تامین از انبار - مسئول خرید می‌تواند اقلام موجود در انبار را از انبار تامین کند"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    # اقلام درخواست خرید که هنوز در انتظار هستند — فقط همین کافه
    pending_items = filter_by_cafe(
        PurchaseItem.objects.filter(
            request__status='purchased',
            supply_status='pending',
        ).select_related('request', 'request__user', 'product'),
        request,
    ).order_by('-request__created_at')

    if request.method == 'POST':
        has_error = False
        items_processed = 0

        for item_id_str in request.POST.getlist('item_ids'):
            try:
                item_pk = int(item_id_str)
            except (ValueError, TypeError):
                continue
            # شناسهٔ قلم باید در همین کافه باشد؛ قلم کافهٔ دیگر → 404
            get_scoped_object_or_404(PurchaseItem, request, id=item_pk)
            action_for_item = request.POST.get(f'action_{item_pk}', '')
            if action_for_item != 'from_stock':
                continue

            # هر قلم می‌تواند از چند موجودی (برند/انبار) تامین شود
            stock_sids = request.POST.getlist(f'stock_sid_{item_pk}')
            stock_qtys = request.POST.getlist(f'stock_qty_{item_pk}')

            supply_rows = []  # (stock_item, base_deduction, trade_qty)
            item_has_error = False

            for sid_str, qty_str in zip(stock_sids, stock_qtys):
                if not sid_str or not qty_str:
                    continue
                trade_qty = _parse_decimal(qty_str)
                if trade_qty is None or trade_qty <= 0:
                    continue
                if not sid_str.isdigit():
                    messages.error(request, f'موجودی انبار برای قلم #{item_pk} یافت نشد')
                    item_has_error = True
                    break
                # موجودی فقط در محدودهٔ کافهٔ فعلی — نه با id خام از POST
                s_item = StockItem.objects.select_related('warehouse').filter(
                    id=int(sid_str), warehouse__cafe=cafe).first()
                if s_item is None:
                    messages.error(request, f'موجودی انبار برای قلم #{item_pk} یافت نشد')
                    item_has_error = True
                    break

                base_deduction = trade_qty

                if base_deduction > s_item.quantity:
                    unit_label = s_item.unit.name if s_item.unit else ''
                    messages.error(
                        request,
                        f'موجودی «{s_item.product.name}» ({_fmt2(s_item.quantity)} {unit_label}) '
                        f'کمتر از مقدار درخواستی ({_fmt2(trade_qty)}) است'
                    )
                    item_has_error = True
                    break

                supply_rows.append((s_item, base_deduction, trade_qty))

            if item_has_error:
                has_error = True
                continue
            if not supply_rows:
                messages.error(request, f'برای قلم #{item_pk} مقدار تامین را وارد کنید')
                has_error = True
                continue

            try:
                with transaction.atomic():
                    # قفل و بازبینی وضعیت: POST تکراری موجودی را دوباره کم نمی‌کند
                    item = get_scoped_locked_object_or_404(
                        PurchaseItem, request, id=item_pk)
                    if item.supply_status != 'pending':
                        messages.warning(
                            request,
                            f'قلم «{item.name}» قبلاً تامین شده است.')
                        continue

                    if item.product_id is None or any(
                            s_item.product_id != item.product_id
                            for s_item, _base, _trade in supply_rows):
                        messages.error(
                            request,
                            f'موجودی انبار با کالای «{item.name}» مطابقت ندارد')
                        has_error = True
                        continue

                    total_trade_supplied = sum(
                        (trade for _s, _b, trade in supply_rows), Decimal('0'))
                    if total_trade_supplied > item.quantity:
                        messages.error(
                            request,
                            f'مقدار تامین‌شده برای «{item.name}» از مقدار درخواستی بیشتر است.')
                        has_error = True
                        continue

                    # کسر اتمیک از موجودی هر ردیف انتخابی
                    total_base_supplied = Decimal('0')
                    for s_item, base_deduction, trade_qty in supply_rows:
                        desc = f'تامین لیست خرید - {item.name}'
                        desc += f' - درخواست {request_number_for(item.request.id)}'

                        apply_stock_delta(
                            s_item, -base_deduction,
                            cafe=cafe, created_by=request.user,
                            reference_type='purchase_item',
                            reference_id=item.id,
                            description=desc,
                        )
                        total_base_supplied += base_deduction

                    item.supply_status = 'from_stock'
                    item.purchased_quantity = total_base_supplied
                    item.save(update_fields=['supply_status', 'purchased_quantity'])
                    items_processed += 1
            except (InsufficientStockError, StockScopeError) as exc:
                messages.error(request, f'موجودی «{exc}» در این لحظه کافی نیست')
                has_error = True
                continue

        if not has_error and items_processed > 0:
            messages.success(request, f'{items_processed} قلم از انبار تامین شد')

        return redirect('purchase_list_with_stock_supply', cafe_code=cafe_code)

    # برای هر قلم، موجودی‌های مطابق همان محصول (به‌تفکیک برند/انبار)
    item_stock_options = {}
    for item in pending_items:
        if not item.product:
            continue
        matches = StockItem.objects.filter(
            product=item.product, quantity__gt=0,
            warehouse__cafe=cafe,
        ).select_related('warehouse', 'unit', 'product')
        item_stock_options[item.id] = list(matches)

    return render(request, 'purchase_list_with_stock_supply.html', {
        'pending_items': pending_items,
        'item_stock_options': item_stock_options,
        'cafe_code': cafe_code,
    })


def _build_available_products_json(cafe, product_ids=None):
    """محصولات دارای موجودی + انبارهای دارای موجودی برای هر محصول (JSON)

    اگر product_ids داده شود فقط همان کالاها (که در درخواست‌ها آمده‌اند) بررسی می‌شوند."""
    stock_qs = StockItem.objects.filter(quantity__gt=0).select_related(
        'product', 'warehouse', 'unit')
    if cafe:
        stock_qs = stock_qs.filter(warehouse__cafe=cafe)
    if product_ids is not None:
        stock_qs = stock_qs.filter(product_id__in=product_ids)
    stock_rows = list(stock_qs)

    products = {}
    for si in stock_rows:
        products.setdefault(si.product_id, si.product)
    product_list = sorted(products.values(), key=lambda p: p.name)

    groups = []
    for p in product_list:
        wh_map = {}
        for si in stock_rows:
            if si.product_id == p.id and si.warehouse_id not in wh_map:
                wh_map[si.warehouse_id] = si
        warehouses = []
        for wid in sorted(wh_map):
            si = wh_map[wid]
            unit_label = si.unit.name if si.unit else (p.unit.name if p.unit else '')
            warehouses.append({
                'warehouse': si.warehouse,
                'available': si.quantity,
                'unit_label': unit_label,
            })
        groups.append({'product': p, 'warehouses': warehouses})

    return json.dumps([
        {
            'id': g['product'].id,
            'name': g['product'].name,
            'warehouses': [
                {
                    'id': w['warehouse'].id,
                    'name': w['warehouse'].name,
                    'unit': w['unit_label'],
                    'available': _fmt2(w['available']),
                }
                for w in g['warehouses']
            ],
        }
        for g in groups
    ], ensure_ascii=False)


def _process_available_products_post(request, cafe):
    """پردازش فرم «تایید نهایی» کالاهای موجود در انبار؛
    در صورت موفقیت پیام‌ها ثبت شده و (False, []) برمی‌گرداند، وگرنه (True, submitted_rows)"""
    product_ids = request.POST.getlist('product_id')
    warehouse_ids = request.POST.getlist('warehouse_id')
    qtys = request.POST.getlist('qty')
    statuses = request.POST.getlist('status')

    has_error = False
    submitted_rows = []
    rows = []  # (product, warehouse, qty, status, stock_item)

    for i, pid_str in enumerate(product_ids):
        # محصول و انبار فقط در محدودهٔ کافهٔ فعلی — نه با id خام از POST
        try:
            product = Product.objects.get(id=int(pid_str), cafe=cafe)
            warehouse = Warehouse.objects.get(id=int(warehouse_ids[i]), cafe=cafe)
        except (Product.DoesNotExist, Warehouse.DoesNotExist, ValueError, IndexError):
            messages.error(request, 'محصول یا انبار انتخاب‌شده معتبر نیست')
            has_error = True
            continue

        qty = _parse_decimal(qtys[i] if i < len(qtys) else '')
        if qty is None or qty <= 0:
            messages.error(request, f'برای «{product.name}» مقدار معتبر وارد کنید')
            has_error = True
            continue

        status = statuses[i] if i < len(statuses) else 'from_stock'
        if status not in ('from_stock', 'to_purchase', 'not_supplied'):
            status = 'from_stock'

        stock_item = StockItem.objects.filter(
            product=product, warehouse=warehouse).first()

        if status == 'from_stock':
            if stock_item is None:
                messages.error(request, f'موجودی «{product.name}» در این انبار یافت نشد')
                has_error = True
                continue
            if qty > stock_item.quantity:
                unit_label = stock_item.unit.name if stock_item.unit else ''
                messages.error(
                    request,
                    f'موجودی «{product.name}» ({_fmt2(stock_item.quantity)} {unit_label}) '
                    f'کمتر از مقدار درخواستی ({_fmt2(qty)}) است'
                )
                has_error = True
                continue

        rows.append((product, warehouse, qty, status, stock_item))
        submitted_rows.append({
            'product': product,
            'warehouse': warehouse,
            'qty': qty,
            'status': status,
            'stock_item': stock_item,
        })

    if not has_error and rows:
      try:
        # ایجاد درخواست انبار — کل ثبت (درخواست + کسر موجودی + ارجاع به خرید) اتمیک است
        with transaction.atomic():
            wh_request = WarehouseRequest.objects.create(
                user=request.user,
                cafe=cafe,
                status='pending',
            )
            items_to_purchase = []
            for product, warehouse, qty, status, stock_item in rows:
                unit_label = product.unit.name if product.unit else ''
                wh_item = WarehouseRequestItem.objects.create(
                    warehouse_request=wh_request,
                    product=product,
                    name=product.name,
                    quantity=qty,
                    unit=unit_label or 'عدد',
                    description=warehouse.name,
                    supply_status=status,
                    stock_item=stock_item if status == 'from_stock' else None,
                    supplied_quantity=qty if status == 'from_stock' else None,
                )
                if status == 'from_stock':
                    desc = f'تامین کالای موجود در انبار - {product.name} - انبار {warehouse.name} - درخواست {request_number_for(wh_request.id)}'
                    apply_stock_delta(
                        stock_item, -qty,
                        cafe=cafe, created_by=request.user,
                        reference_type='warehouse_request',
                        reference_id=wh_request.id,
                        description=desc,
                    )
                elif status == 'to_purchase':
                    items_to_purchase.append(wh_item)

            # ارجاع اقلام «ارسال به لیست خرید» به درخواست خرید جدید
            if items_to_purchase:
                new_purchase = PurchaseRequest.objects.create(
                    user=request.user,
                    cafe=cafe,
                    status='pending',
                )
                wh_request.converted_to_purchase = new_purchase
                new_purchase_items = []
                for wh_item in items_to_purchase:
                    q = wh_item.quantity
                    pi = PurchaseItem.objects.create(
                        request=new_purchase,
                        product=wh_item.product,
                        name=wh_item.name,
                        quantity=q,
                        unit=wh_item.unit,
                        description=wh_item.description or '',
                    )
                    new_purchase_items.append(pi)
                # همه درخواست‌های کالا مستقیماً برای مسئول انبار ارسال می‌شوند
                _route_request_to_warehouse(
                    new_purchase, new_purchase_items, request.user,
                    notes='ارسال مستقیم از فرم درخواست کالا')
                messages.info(request, f'{len(items_to_purchase)} قلم به لیست خرید ارجاع شد')

            # تعیین وضعیت نهایی درخواست انبار (نتیجهٔ مختلط → partial)
            statuses = [
                'declined' if r[3] == 'not_supplied' else r[3]
                for r in rows
            ]
            wh_request.status = _warehouse_request_status(statuses)
            wh_request.processed_by = request.user
            wh_request.processed_at = timezone.now()
            wh_request.save()
      except (InsufficientStockError, StockScopeError) as exc:
        messages.error(request, f'موجودی «{exc}» در این لحظه کافی نیست؛ ثبت انجام نشد')
        return True, submitted_rows

      messages.success(request, f'{len(rows)} قلم ثبت شد؛ اقلام تامین از انبار از موجودی کسر گردید')
      return False, []
    elif not rows:
        messages.error(request, 'هیچ قلمی برای ثبت انتخاب نشده است')
        return True, submitted_rows
    return has_error, submitted_rows


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def warehouse_available_products(request: HttpRequest) -> HttpResponse:
    """کالاهای موجود در انبار - انتخاب محصول ← انبار، افزودن به جدول بررسی و تایید نهایی"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    submitted_rows = []
    if request.method == 'POST':
        has_error, submitted_rows = _process_available_products_post(request, cafe)
        if not has_error:
            return redirect('warehouse_available_products', cafe_code=cafe_code)

    return render(request, 'warehouse_available_products.html', {
        'products_json': _build_available_products_json(cafe),
        'submitted_rows': submitted_rows,
        'cafe_code': cafe_code,
    })


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def warehouse_dashboard(request: HttpRequest) -> HttpResponse:
    """داشبورد مسئول انبار (وقتی نقش warehouse دارد)"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    stock_items_low = StockItem.objects.select_related('product', 'unit', 'warehouse').filter(
        quantity__lte=F('min_quantity')
    )
    if cafe:
        stock_items_low = stock_items_low.filter(warehouse__cafe=cafe)

    return render(request, 'warehouse_dashboard.html', {
        'stock_items_low': stock_items_low[:10],
        'cafe_code': cafe_code,
        'user': request.user,
        'has_purchasing': request.user.has_purchasing_role,
        'has_warehouse': request.user.has_warehouse_role,
    })


@custom_login_required
@role_required(["manager", "purchasing"])
def purchase_lists_by_date(request: HttpRequest) -> HttpResponse:
    """Show purchase lists grouped by date and department"""
    from django.db.models import Count, Q
    from django.db.models.functions import TruncDate, Coalesce
    
    cafe = get_cafe_from_request(request)
    # Get all purchased requests with items
    # Use processed_at if available, otherwise fallback to created_at
    purchased_requests = PurchaseRequest.objects.filter(
        status='purchased'
    )
    if cafe:
        purchased_requests = purchased_requests.filter(cafe=cafe)
    purchased_requests = purchased_requests.annotate(
        date=TruncDate(Coalesce('processed_at', 'created_at'))
    ).values('date', 'user__role').annotate(
        count=Count('id')
    ).order_by('-date', 'user__role')
    
    # Group by date and role
    purchase_days = {}
    for req in purchased_requests:
        date_obj = req['date']
        if not date_obj:
            continue
        date_str = date_obj.strftime('%Y-%m-%d')  # Convert to string for URL
        role = req['user__role']
        
        if date_str not in purchase_days:
            purchase_days[date_str] = {
                'date_obj': date_obj,  # Keep object for display
                'departments': {}
            }
        
        role_names = {
            'kitchen': 'آشپزخانه',
            'bar': 'بار',
            'counter': 'کانتر',
            'cashier': 'صندوق',
            'facilities': 'تاسیسات',
        }
        
        purchase_days[date_str]['departments'][role] = {
            'name': role_names.get(role, role),
            'count': req['count']
        }
    
    return render(request, "purchase_lists_by_date.html", {
        "purchase_days": purchase_days,
    })


@custom_login_required
@role_required(["manager", "purchasing"])
def purchase_day_detail(request: HttpRequest) -> HttpResponse:
    """Show detailed purchase list for a specific date and department"""
    from datetime import datetime

    date_str = request.GET.get('date')
    role = request.GET.get('role')

    if not date_str or not role:
        return get_cafe_redirect_url('purchase_lists_by_date', request)
    
    # Parse date
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    cafe = get_cafe_from_request(request)
    # Get all purchased requests for this date and role
    requests = PurchaseRequest.objects.filter(
        status='purchased',
        user__role=role,
        processed_at__date=date_obj
    )
    if cafe:
        requests = requests.filter(cafe=cafe)
    requests = requests.prefetch_related('items', 'user', 'processed_by')
    
    # Collect all items
    all_items = []
    for req in requests:
        for item in req.items.all():
            # Calculate supplied quantity based on status
            if item.supply_status == 'purchased':
                supplied_quantity = item.purchased_quantity or 0
            elif item.supply_status == 'from_stock':
                supplied_quantity = item.quantity
            elif item.supply_status == 'not_supplied':
                supplied_quantity = 0
            else:  # pending
                supplied_quantity = 0
            
            new_difference = item.quantity - supplied_quantity
            
            all_items.append({
                'request': req,
                'item': item,
                'supplied_quantity': supplied_quantity,
                'new_difference': new_difference,
                'difference': (item.quantity - (item.purchased_quantity or 0)),  # Keep old for compatibility
                'display_difference': abs(new_difference)  # For display purposes
            })
    
    role_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    
    return render(request, "purchase_day_detail.html", {
        "date": date_obj,
        "date_str": date_str,
        "role": role,
        "role_name": role_names.get(role, role),
        "requests": requests,
        "all_items": all_items,
    })


@custom_login_required
@role_required(["manager", "purchasing"])
def export_purchase_day_pdf(request: HttpRequest) -> HttpResponse:
    """Export purchase day detail as PDF - Persian RTL minimal design"""
    from datetime import datetime
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import jdatetime
    import os
    import arabic_reshaper
    from bidi.algorithm import get_display

    date_str = request.GET.get('date')
    role = request.GET.get('role')

    if not date_str or not role:
        return get_cafe_redirect_url('purchase_lists_by_date', request)
    
    # Parse date
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    jalali_date = jdatetime.date.fromgregorian(date=date_obj)
    jalali_months = {
        1: 'فروردین', 2: 'اردیبهشت', 3: 'خرداد',
        4: 'تیر', 5: 'مرداد', 6: 'شهریور',
        7: 'مهر', 8: 'آبان', 9: 'آذر',
        10: 'دی', 11: 'بهمن', 12: 'اسفند'
    }
    jalali_str = f"{jalali_date.day} {jalali_months[jalali_date.month]} {jalali_date.year}"
    
    cafe = get_cafe_from_request(request)
    # Get all purchased requests for this date and role
    requests_qs = PurchaseRequest.objects.filter(
        status='purchased',
        user__role=role,
        processed_at__date=date_obj
    )
    if cafe:
        requests_qs = requests_qs.filter(cafe=cafe)
    requests_qs = requests_qs.prefetch_related('items', 'user', 'processed_by')
    
    role_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    role_name = role_names.get(role, role)
    
    # Collect all items
    all_items = []
    for req in requests_qs:
        for item in req.items.all():
            # Calculate supplied quantity based on status
            if item.supply_status == 'purchased':
                supplied_quantity = item.purchased_quantity or 0
            elif item.supply_status == 'from_stock':
                supplied_quantity = item.quantity
            elif item.supply_status == 'not_supplied':
                supplied_quantity = 0
            else:  # pending
                supplied_quantity = 0
            
            all_items.append(item)
    
    # Create PDF
    buffer = BytesIO()
    page_width, page_height = A4
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # Register Persian font (Vazir)
    font_name = 'Helvetica'
    try:
        from django.conf import settings
        font_path = os.path.join(settings.BASE_DIR, 'frontend', 'static', 'fonts', 'Vazir.ttf')
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('Vazir', font_path))
            font_name = 'Vazir'
    except:
        pass
    
    # Helper function to reshape Persian text for proper display
    def persian(text):
        """Reshape Persian text for correct display in PDF"""
        if not text:
            return ''
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)
    
    # Page margins
    margin = 2 * cm
    content_width = page_width - 2 * margin
    
    # Header
    y = page_height - margin
    
    # Title - centered
    c.setFont(font_name, 18)
    title = persian(f"لیست خرید {role_name}")
    title_width = c.stringWidth(title, font_name, 18)
    c.drawString((page_width - title_width) / 2, y, title)
    
    # Date - centered
    y -= 30
    c.setFont(font_name, 12)
    date_text = persian(jalali_str)
    date_width = c.stringWidth(date_text, font_name, 12)
    c.drawString((page_width - date_width) / 2, y, date_text)
    
    # Separator line
    y -= 20
    c.setStrokeColor(colors.Color(0.85, 0.85, 0.85))
    c.setLineWidth(0.5)
    c.line(margin, y, page_width - margin, y)
    
    # Table setup - columns from LEFT to RIGHT (RTL: rightmost is name, leftmost is diff)
    y -= 35
    col_widths = [2.5*cm, 3*cm, 3.5*cm, 3.5*cm, 4.5*cm]
    headers = ['تفاوت', 'وضعیت', 'تامین شده', 'درخواستی', 'نام کالا']
    
    # Header background
    c.setFillColor(colors.Color(0.93, 0.93, 0.93))
    c.rect(margin, y - 8, content_width, 28, fill=1, stroke=0)
    
    # Draw headers from LEFT to RIGHT
    c.setFillColor(colors.Color(0.2, 0.2, 0.2))
    c.setFont(font_name, 11)
    x = margin
    for i, header in enumerate(headers):
        header_text = persian(header)
        text_width = c.stringWidth(header_text, font_name, 11)
        col_center = x + col_widths[i] / 2
        c.drawString(col_center - text_width / 2, y + 5, header_text)
        x += col_widths[i]
    
    # Draw rows
    y -= 28
    status_map = {
        'purchased': 'خرید شده',
        'from_stock': 'از انبار',
        'not_supplied': 'تامین نشده',
        'pending': 'در انتظار'
    }
    
    row_height = 24
    
    for idx, item in enumerate(all_items):
        # Alternate row background
        if idx % 2 == 1:
            c.setFillColor(colors.Color(0.97, 0.97, 0.97))
            c.rect(margin, y - 6, content_width, row_height, fill=1, stroke=0)
        
        c.setFillColor(colors.Color(0.15, 0.15, 0.15))
        c.setFont(font_name, 10)
        
        # Calculate supplied quantity
        if item.supply_status == 'purchased':
            supplied_quantity = item.purchased_quantity or 0
        elif item.supply_status == 'from_stock':
            supplied_quantity = item.quantity
        elif item.supply_status == 'not_supplied':
            supplied_quantity = 0
        else:  # pending
            supplied_quantity = 0
        
        difference = item.quantity - supplied_quantity
        if item.supply_status == 'not_supplied':
            diff_text = '-'
        elif difference == 0:
            diff_text = '0'
        elif difference > 0:
            diff_text = f'{difference}-'
        else:
            diff_text = f'{abs(difference)}+'
        
        # Row data from LEFT to RIGHT (RTL: reversed so name appears on right)
        row_data = [
            diff_text,
            status_map.get(item.supply_status, item.supply_status),
            f"{supplied_quantity} {item.unit}",
            f"{item.quantity} {item.unit}",
            item.name
        ]
        
        x = margin
        for i, cell in enumerate(row_data):
            cell_text = persian(str(cell))
            text_width = c.stringWidth(cell_text, font_name, 10)
            col_center = x + col_widths[i] / 2
            c.drawString(col_center - text_width / 2, y + 4, cell_text)
            x += col_widths[i]
        
        y -= row_height
        
        # Check if we need a new page
        if y < margin + 5*cm:
            c.showPage()
            c.setFont(font_name, 10)
            y = page_height - margin - 30
    
    # Separator before signatures
    y -= 30
    c.setStrokeColor(colors.Color(0.85, 0.85, 0.85))
    c.line(margin, y, page_width - margin, y)
    
    # Signature section - Two signatures side by side
    y -= 50
    c.setFont(font_name, 11)
    c.setFillColor(colors.Color(0.3, 0.3, 0.3))
    c.setStrokeColor(colors.Color(0.7, 0.7, 0.7))
    
    # Right side: امضا مسئول خرید
    sig1_text = persian('امضا مسئول خرید:')
    sig1_width = c.stringWidth(sig1_text, font_name, 11)
    right_edge = page_width - margin
    c.drawString(right_edge - sig1_width, y, sig1_text)
    c.line(right_edge - sig1_width - 4.5*cm, y - 3, right_edge - sig1_width - 0.3*cm, y - 3)
    
    # Left side: امضا مسئول بخش
    sig2_text = persian('امضا مسئول بخش:')
    sig2_width = c.stringWidth(sig2_text, font_name, 11)
    c.drawString(margin + 4.5*cm + 0.3*cm, y, sig2_text)
    c.line(margin, y - 3, margin + 4.5*cm, y - 3)
    
    c.save()
    
    # Get PDF content
    pdf = buffer.getvalue()
    buffer.close()
    
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="purchase_list_{date_str}_{role}.pdf"'
    return response


@custom_login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """داشبورد مدیریت - برای کاربران مدیر"""
    from datetime import date, timedelta
    from django.db.models import Sum, Count, Q

    # اگر کاربر manager است، به داشبورد مالی redirect شود
    if request.user.role == 'manager':
        return get_cafe_redirect_url('finance_dashboard', request)

    # برای کاربران عادی - داشبورد ساده
    cafe = get_cafe_from_request(request)
    user_requests = PurchaseRequest.objects.all()
    if request.user.role == "kitchen":
        user_requests = user_requests.filter(user=request.user)
    if cafe:
        user_requests = user_requests.filter(cafe=cafe)
    context = {
        "user": request.user,
        "role": request.user.role,
        "requests": user_requests[:20],
    }
    return render(request, "dashboard.html", context)


@custom_login_required
@role_required(["admin", "accounting", "ceo", "manager"])
def finance_dashboard(request: HttpRequest) -> HttpResponse:
    from datetime import date
    
    # آمار اصلی
    cafe = get_cafe_from_request(request)
    invoice_qs = Invoice.objects.all()
    if cafe:
        invoice_qs = invoice_qs.filter(cafe=cafe)
    stats = {
        "invoices": invoice_qs.count(),
        "payments": Payment.objects.count(),
    }
    
    # آمار انبار
    stock_stats = {
        "total_items": StockItem.objects.count(),
        "low_stock_items": StockItem.objects.filter(quantity__lte=F('min_quantity')).count(),
        "total_stock_value": 0,
        "pending_stock_requests": StockRequest.objects.filter(status='pending').count(),
        "fulfilled_today": StockRequest.objects.filter(
            status='fulfilled',
            fulfilled_at__date=date.today()
        ).count(),
    }
    
    # موجودی‌های کم
    low_stock_items = StockItem.objects.select_related('product', 'unit').filter(
        quantity__lte=F('min_quantity')
    )[:10]
    
    # آخرین تراکنش‌های انبار
    recent_transactions = StockTransaction.objects.select_related(
        'stock_item__product',
        'created_by'
    ).order_by('-created_at')[:10]

    return render(request, "finance_dashboard.html", {
        "stats": stats,
        "stock_stats": stock_stats,
        "low_stock_items": low_stock_items,
        "recent_transactions": recent_transactions,
        "today": date.today(),
    })


@custom_login_required
@role_required(["finance_manager"])
def finance_manager_dashboard(request: HttpRequest) -> HttpResponse:
    """داشبورد اختصاصی مدیر مالی"""
    from datetime import date
    
    cafe = get_cafe_from_request(request)
    invoice_qs = Invoice.objects.all()
    supplier_qs = Supplier.objects.all()
    if cafe:
        invoice_qs = invoice_qs.filter(cafe=cafe)
        supplier_qs = supplier_qs.filter(cafe=cafe)
    stats = {
        "total_invoices": invoice_qs.count(),
        "total_payments": Payment.objects.count(),
        "pending_invoices": invoice_qs.filter(status='pending').count(),
        "unpaid_invoices": invoice_qs.filter(payment_status='unpaid').count(),
        "total_suppliers": supplier_qs.count(),
    }
    
    return render(request, "finance_manager_dashboard.html", {
        "stats": stats,
        "today": date.today(),
    })


# ---------------------------------------------------------------------------
# Purchase Requests
# ---------------------------------------------------------------------------


def _purchase_request_form_context(request, department='', **extra):
    """Context مشترکِ فرم درخواست کالا (GET و رندر دوباره پس از خطای اعتبارسنجی).

    دستهٔ «تحقیق و توسعه» عمداً از فهرست دسته‌ها حذف می‌شود: سبدِ مدیریتی است،
    برای درخواست‌کننده همیشه خالی است و انتخابش فقط گمراه‌کننده است.
    """
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    all_categories = (Category.objects.filter(is_active=True)
                      .exclude(name=RD_CATEGORY_NAME)
                      .select_related('parent').order_by('name'))
    if cafe:
        all_categories = all_categories.filter(cafe=cafe)

    context = {
        "department": department,
        "delivery_days": _next_delivery_days(),
        "all_categories": all_categories,
        "submitted_items": [],
        # قفل «تایید دریافت قبل از درخواست جدید» — هم بنر فرم، هم قفل سمت
        # سرور، هر دو این عدد را از services/receipt_gate میگیرند.
        "receipt_gate": receipt_gate_context(request.user, cafe, cafe_code),
    }
    context.update(extra)
    return context


@custom_login_required
@role_required(["kitchen", "bar", "counter", "store", "cashier", "facilities", "purchasing", "manager", "requester"])
def purchase_request_form(request):
    cafe = get_cafe_from_request(request)
    
    # Get department parameter if purchasing user wants to create request for specific department
    department = request.GET.get('department', '')
    
    if request.method == "POST":
        # Basic CSRF check for the explicit test case
        if "HTTP_X_CSRFTOKEN" in request.META and not request.META.get(
            "HTTP_X_CSRFTOKEN"
        ):
            return HttpResponseForbidden("CSRF token missing")

        # Get product names (free text input from user)
        product_names = request.POST.getlist("product_name")
        quantities = request.POST.getlist("item_quantity")
        units = request.POST.getlist("item_unit")
        descriptions = request.POST.getlist("item_description")
        product_ids = request.POST.getlist("item_product_id")

        # Get department from POST if provided
        selected_department = request.POST.get('department', department)

        items_data = []
        submitted_items = []   # برای برگرداندنِ همان ردیف‌ها به فرم بعد از خطا
        errors = []
        blocked = []          # کالاهای غذاییِ بدون رسپی — قابل سفارش نیستند
        for idx, name in enumerate(product_names):
            name = name.strip() if name else ""
            if not name:
                errors.append("product_name")
                continue
            qty_raw = quantities[idx] if idx < len(quantities) else ""
            qty_val = _parse_decimal(qty_raw)
            unit_val = units[idx].strip() if idx < len(units) and units[idx] else "عدد"
            desc_val = descriptions[idx] if idx < len(descriptions) else ""
            product_id_raw = product_ids[idx] if idx < len(product_ids) else ""
            submitted_items.append({
                'name': name,
                'quantity': str(qty_raw or ''),
                'unit': unit_val,
                'description': desc_val,
                'productId': product_id_raw,
            })
            if qty_val is None or qty_val <= 0 or qty_val > 99999999:
                errors.append("quantity")
                continue
            # محصول انتخاب‌شده از لیست پیشنهادها (اگر انتخاب نشده باشد با نام جستجو می‌شود)
            # شناسهٔ خامِ POST هرگز بدون قفلِ کافه واکشی نمی‌شود.
            item_product = None
            if product_id_raw.isdigit():
                qs_p = Product.objects.filter(id=int(product_id_raw))
                if cafe is not None:
                    qs_p = qs_p.filter(cafe=cafe)
                candidate = qs_p.first()
                if candidate is not None and candidate.name.strip().lower() == name.lower():
                    item_product = candidate
            if item_product is None:
                item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else Product.objects.filter(name__iexact=name).first()
            if item_product is None and not bypasses_recipe_gate(request.user):
                blocked.append(
                    f'کالای «{name}» در فهرست محصولات ثبت نشده است. '
                    'لطفاً کالا را از فهرست انتخاب کنید یا به‌عنوان کالای جدید ثبت نمایید.'
                )
                continue
            inactive_err = inactive_product_error(item_product)
            if inactive_err:
                blocked.append(inactive_err)
                continue
            # قاعدهٔ رسپی: کالای غذایی که در هیچ رسپیِ این کافه مصرف نمی‌شود،
            # حتی با فرستادن شناسهٔ خام هم قابل درخواست نیست.
            if item_product is not None and not bypasses_recipe_gate(request.user):
                gate_error = recipe_gate_error(item_product, cafe=cafe)
                if gate_error:
                    blocked.append(gate_error)
                    continue
            items_data.append((name, qty_val, unit_val, desc_val, item_product))

        if blocked:
            for blocked_message in blocked[:3]:
                messages.error(request, blocked_message)
            return render(
                request,
                "purchase_request_form.html",
                _purchase_request_form_context(
                    request, selected_department,
                    errors=["recipe_gate"], error_message="recipe_gate",
                    submitted_items=submitted_items),
                status=200,
            )

        if errors:
            return render(
                request,
                "purchase_request_form.html",
                _purchase_request_form_context(
                    request, selected_department,
                    errors=errors,
                    error_message="quantity" if "quantity" in errors else "product_name",
                    submitted_items=submitted_items),
                status=200,
            )

        # Receipt gate: a requester cannot open a new request while deliveries
        # from their earlier requests are still unconfirmed. Enforced here on
        # the server (the disabled button in the form is only a hint) and the
        # rows already typed in are kept for the re-render.
        pending_receipts = pending_receipt_count(request.user, cafe)
        if pending_receipts:
            messages.error(request, receipt_gate_message(pending_receipts))
            return render(
                request,
                "purchase_request_form.html",
                _purchase_request_form_context(
                    request, selected_department,
                    errors=["receipt_gate"], error_message="receipt_gate",
                    submitted_items=submitted_items),
                status=200,
            )

        cafe = get_cafe_from_request(request)
        # زمان تحویل: تاریخ انتخاب‌شده (۷ روز آینده)
        delivery_date = None
        delivery_raw = request.POST.get('delivery_date', '').strip()
        if delivery_raw:
            try:
                delivery_date = datetime.strptime(delivery_raw, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                delivery_date = None
        purchase_request = PurchaseRequest.objects.create(
            user=request.user, status="pending_manager_approval", cafe=cafe,
            delivery_date=delivery_date,
        )
        created_items = []
        for index, (name, qty, unit, desc, item_product) in enumerate(items_data, start=1):
            item = PurchaseItem.objects.create(
                request=purchase_request,
                product=item_product,
                name=name,
                quantity=qty,
                unit=unit,
                description=desc or "",
            )
            created_items.append(item)

        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action="created",
            description="Purchase request created",
        )
        Notification.objects.create(
            recipient=request.user,
            title="Request created",
            message="Your purchase request was submitted.",
            notification_type="info",
        )

        # ارسال مستقیم به مسئول انبار: هر قلم بلافاصله در صندوق مشترک مسئول انبار
        # قرار می‌گیرد (بدون مرحله تایید مدیرعامل/مدیر) و اعلان ارسال می‌شود.
        _route_request_to_warehouse(
            purchase_request, created_items, request.user,
            notes='ارسال مستقیم از فرم درخواست کالا')

        cafe_code = cafe.code if cafe else 'local'
        return redirect("purchase_request_success", cafe_code=cafe_code)

    return render(request, "purchase_request_form.html",
                  _purchase_request_form_context(request, department))


@custom_login_required
def purchase_request_success(request):
    return render(request, "purchase_request_success.html")


@custom_login_required
def purchase_requests_list(request):
    cafe = get_cafe_from_request(request)
    # For mobile users, show only their own requests
    if request.user.role in ['kitchen', 'bar', 'counter', 'cashier', 'facilities', 'requester']:
        requests_qs = PurchaseRequest.objects.filter(user=request.user)
        if cafe:
            requests_qs = requests_qs.filter(cafe=cafe)
        requests_qs = requests_qs.prefetch_related("items").order_by("-created_at")
        requests_list = list(requests_qs)

        # پرچم «قابل ویرایش»: تا وقتی مسئول انبار برای اقلامِ درخواست وضعیتی ثبت
        # نکرده باشد (تامین/عدم موجودی/ارجاع به خرید)، صاحب درخواست می‌تواند آن را
        # ویرایش کند. درخواست ردشده توسط مدیر نیز قفل است.
        warehouse_decided_ids = _warehouse_decided_request_ids([r.id for r in requests_list])
        for req in requests_list:
            req.can_edit = req.status != 'rejected' and req.id not in warehouse_decided_ids

        # گروه‌بندی درخواست‌ها بر اساس روز: ۷ روز گذشته هر روز یک لیست جداگانه،
        # درخواست‌های قدیمی‌تر زیر یک بخش «قبلی» نمایش داده می‌شوند
        from datetime import timedelta
        weekday_names = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']
        # created_at is stored in UTC (USE_TZ=True); group by the cafe's local
        # calendar day so "today"/"yesterday" match what users actually see.
        today = timezone.localdate()
        week_start = today - timedelta(days=6)
        grouped_by_day = {}
        older_requests = []
        for req in requests_list:
            created_at = req.created_at
            if timezone.is_aware(created_at):
                day = timezone.localtime(created_at).date()
            else:
                day = created_at.date()
            if day >= week_start:
                grouped_by_day.setdefault(day, []).append(req)
            else:
                older_requests.append(req)
        day_groups = [
            (day, weekday_names[day.weekday()], grouped_by_day[day])
            for day in sorted(grouped_by_day, reverse=True)
        ]

        context = {
            "requests": requests_list,
            "day_groups": day_groups,
            "older_requests": older_requests,
            "today": today,
            "yesterday": today - timedelta(days=1),
            "is_mobile_user": True,
        }
        return render(request, "purchase_requests_list.html", context)
    
    # For purchasing/manager users
    selected_department = request.GET.get("department") or ""
    requests_qs = PurchaseRequest.objects.select_related("user__department").prefetch_related("items")
    if cafe:
        requests_qs = requests_qs.filter(cafe=cafe)
    requests_qs = requests_qs.order_by("-created_at")
    if selected_department:
        # Try filtering by department name first (for requester users)
        requests_qs = requests_qs.filter(
            models.Q(user__department__name=selected_department) |
            models.Q(user__role=selected_department)
        )

    # Build department list from Department model + any old roles with pending requests
    department_list = Department.objects.filter(is_active=True, cafe=cafe).values_list('name', 'name')

    context = {
        "requests": list(requests_qs),
        "departments": department_list,
        "selected_department": selected_department,
        "is_mobile_user": False,
    }
    
    if request.headers.get('HX-Request'):
        return render(request, "partials/requests_table_body.html", context)
        
    return render(request, "purchase_requests_list.html", context)


@custom_login_required
def purchase_request_detail(request, request_id):
    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.prefetch_related("items", "activities")
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
    purchase_request = get_object_or_404(pr_qs, id=request_id)
    
    # Check permissions: mobile users can only see their own requests
    if request.user.role in ['kitchen', 'bar', 'counter', 'cashier', 'facilities', 'requester']:
        if purchase_request.user != request.user:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("شما مجاز به مشاهده این درخواست نیستید")

    context = {
        "purchase_request": purchase_request,
        "is_mobile_user": request.user.role in ['kitchen', 'bar', 'counter', 'cashier', 'facilities', 'requester'],
    }
    return render(request, "purchase_request_detail.html", context)


def _received_delivery_rows(user, cafe):
    """اقلامِ تحویل‌گرفتهٔ یک کاربر از انبار — با مقدار و واحدِ هر تحویل.

    منبعِ «تحویل»: تخصیص‌های تامین‌شدهٔ مسئول انبار (OfficerAssignment.status='fulfilled')
    روی اقلامِ درخواستِ خودِ کاربر. مقدارِ تامین‌شده در واحدِ پایه ذخیره می‌شود تا
    مقدارِ نمایش‌داده‌شده همان چیزی باشد که تحویل داده شده. بازگشاییِ قلم
    تخصیص‌ها را پاک می‌کند، پس این فهرست همیشه وضعیتِ جاری است.
    """
    assigns = (OfficerAssignment.objects
               .filter(target_role='warehouse', status='fulfilled',
                       item__request__user=user)
               .select_related('item', 'item__product', 'item__request',
                               'stock_item', 'stock_item__unit',
                               'stock_item__warehouse')
               .order_by('item__request__created_at', 'item_id', 'id'))
    if cafe:
        assigns = assigns.filter(item__request__cafe=cafe)

    rows = {}
    for a in assigns:
        item = a.item
        row = rows.get(item.id)
        if row is None:
            row = rows[item.id] = {
                'item': item,
                'name': item.product.name if item.product else item.name,
                'requested_qty': _fmt2(item.quantity),
                'unit': item.unit or '',
                'request': item.request,
                'deliveries': [],
                'confirmed_at': item.received_confirmed_at,
                'confirmed_by': item.received_confirmed_by,
            }
        si = a.stock_item
        qty = a.supplied_quantity if a.supplied_quantity is not None else Decimal('0')
        unit_label = (((si.unit.name or si.unit.symbol) if si and si.unit else '')
                      or row['unit'])
        row['deliveries'].append({
            'qty': _fmt2(qty),
            'unit': unit_label,
            'warehouse': si.warehouse.name if si and si.warehouse else '',
        })
    return list(rows.values())


@custom_login_required
def my_received_items(request: HttpRequest) -> HttpResponse:
    """«کالاهای دریافتی من» — اقلامی که از انبار به این کاربر تحویل شده است.

    درخواست‌دهنده مقدارِ تحویل‌شده را می‌بیند و «تایید دریافت» را ثبت می‌کند.
    این تایید فقط اعلامِ وصول است: هیچ اثر انبار/مالی ندارد و کسرِ موجودی همان‌جا
    که مسئول انبار کالا را تامین می‌کند انجام شده است (به تایید درخواست‌دهنده
    وابسته نیست).
    """
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    rows = _received_delivery_rows(request.user, cafe)
    pending = [r for r in rows if r['confirmed_at'] is None]
    confirmed = [r for r in rows if r['confirmed_at'] is not None]
    pending.sort(key=lambda r: r['item'].id, reverse=True)
    confirmed.sort(key=lambda r: (r['confirmed_at'], r['item'].id), reverse=True)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        pending_ids = {r['item'].id for r in pending}
        if action == 'confirm_all':
            targets = sorted(pending_ids)
        else:
            raw_id = request.POST.get('item_id', '')
            targets = [int(raw_id)] if raw_id.isdigit() and int(raw_id) in pending_ids else []
        if not targets:
            messages.warning(request, 'قلمی برای تایید دریافت پیدا نشد (یا قبلاً تایید شده است).')
        else:
            updated = (PurchaseItem.objects
                       .filter(id__in=targets, received_confirmed_at__isnull=True)
                       .update(received_confirmed_at=timezone.now(),
                               received_confirmed_by=request.user))
            if updated == 1:
                messages.success(request, 'تایید دریافت ثبت شد.')
            else:
                messages.success(request, f'تایید دریافت {updated} قلم ثبت شد.')
        return redirect('my_received_items', cafe_code=cafe_code)

    return render(request, "my_received_items.html", {
        "pending_rows": pending,
        "confirmed_rows": confirmed,
        "pending_count": len(pending),
        "cafe_code": cafe_code,
    })


@custom_login_required
@role_required(["purchasing", "manager", "ceo"])
def update_purchase_request(request, request_id):
    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.all()
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
    purchase_request = get_object_or_404(pr_qs, id=request_id)

    if request.method == "POST":
        item_ids = request.POST.getlist("item_id")
        purchased_quantities = request.POST.getlist("purchased_quantity")
        for idx, item_id in enumerate(item_ids):
            try:
                item = PurchaseItem.objects.get(id=item_id, request=purchase_request)
            except PurchaseItem.DoesNotExist:
                continue
            qty_val = _parse_decimal(
                purchased_quantities[idx] if idx < len(purchased_quantities) else ""
            )
            if qty_val is not None and qty_val >= 0:
                item.purchased_quantity = qty_val
                item.save()

        purchase_request.status = request.POST.get("status") or "purchased"
        purchase_request.save()
        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action="updated",
            description="Purchase request updated",
        )
        return get_cafe_redirect_url("purchase_request_detail", request, request_id=purchase_request.id)

    items = purchase_request.items.all()
    return render(
        request,
        "purchase_request_update.html",
        {"purchase_request": purchase_request, "items": items},
    )


def _warehouse_decided_request_ids(request_ids):
    """شناسهٔ درخواست‌هایی که مسئول انبار برای دست‌کم یکی از اقلامشان وضعیتی ثبت کرده است

    وضعیتِ «تعیین‌شده» یعنی تخصیصِ اقلام از حالت در انتظار خارج شده باشد
    (تامین از انبار، عدم موجودی یا ارجاع به خرید).
    """
    request_ids = [rid for rid in request_ids if rid]
    if not request_ids:
        return set()
    return set(
        OfficerAssignment.objects
        .filter(item__request_id__in=request_ids, target_role='warehouse')
        .exclude(status='pending')
        .values_list('item__request_id', flat=True)
    )


def _request_editable_by_owner(req):
    """آیا صاحب درخواست می‌تواند آن را ویرایش کند؟

    تا قبل از «تعیین وضعیت توسط مسئول انبار» ویرایش آزاد است؛ درخواست ردشده
    توسط مدیر نیز قفل است.
    """
    if req.status == 'rejected':
        return False
    return req.id not in _warehouse_decided_request_ids([req.id])


def _recipe_gate_for_name(request, cafe, name):
    """گیتِ رسپی و وضعیت فعال‌بودن برای فرم‌هایی که فقط «نام» می‌فرستند، نه شناسهٔ محصول.

    نام با کالای موجود تطبیق داده می‌شود؛ اگر غیرفعال باشد یا کالای غذاییِ بدون
    رسپی باشد، پیام خطا برمی‌گردد. نامِ ناشناخته (کالای بیرون از فهرست) آزاد است.
    """
    if not name:
        return None
    product = (Product.objects.filter(name__iexact=name, cafe=cafe).first()
               if cafe else Product.objects.filter(name__iexact=name).first())
    inactive_err = inactive_product_error(product)
    if inactive_err:
        return inactive_err
    if bypasses_recipe_gate(request.user):
        return None
    return recipe_gate_error(product, cafe=cafe)


@custom_login_required
def edit_my_request(request, request_id):
    """ویرایش درخواست از بخش «درخواست‌های من» — فقط تا قبل از تعیین وضعیت توسط مسئول انبار.

    صاحب درخواست می‌تواند اقلام (نام/مقدار/واحد/توضیح) را ویرایش کند، قلم حذف
    یا قلم جدید اضافه کند. اقلام جدید مثل فرم ثبت، مستقیم به صندوق مسئول انبار
    می‌روند.
    """
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    receipt_gate = receipt_gate_context(request.user, cafe, cafe_code)
    pr_qs = PurchaseRequest.objects.prefetch_related('items')
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
    purchase_request = get_object_or_404(pr_qs, id=request_id)

    if purchase_request.user != request.user:
        return HttpResponseForbidden("فقط صاحب درخواست می‌تواند آن را ویرایش کند")

    if not _request_editable_by_owner(purchase_request):
        messages.error(
            request,
            "این درخواست توسط مسئول انبار تعیین وضعیت شده و دیگر قابل ویرایش نیست.")
        return redirect('purchase_requests_list', cafe_code=cafe_code)

    def _render(rows, selected_delivery, error_message=None):
        return render(request, "my_request_edit.html", {
            "purchase_request": purchase_request,
            "form_rows": rows,
            "delivery_days": _next_delivery_days(),
            "selected_delivery": selected_delivery,
            "receipt_gate": receipt_gate,
        })

    if request.method == "POST":
        item_ids = request.POST.getlist("item_id")
        names = request.POST.getlist("name")
        quantities = request.POST.getlist("quantity")
        units = request.POST.getlist("unit")
        descriptions = request.POST.getlist("description")
        removed_ids = {int(x) for x in request.POST.getlist("remove_item_id") if x.isdigit()}

        if not item_ids:
            messages.error(request, "درخواست باید دست‌کم یک قلم داشته باشد.")
            return _render([], request.POST.get("delivery_date", "").strip())

        # قفل «تایید دریافت قبل از درخواست جدید»: قلم تازهای که از صفحهٔ ویرایش
        # اضافه شود درست مثل ثبت درخواست تازه، مستقیم به صندوق مسئول انبار
        # میرود. ویرایش اقلام موجود (مثلاً اصلاح مقدار) همچنان آزاد است.
        # بررسی قبل از هرگونه ذخیره/حذف انجام میشود تا تغییرات نیمهکاره نماند.
        pending_receipts = pending_receipt_count(request.user, cafe)
        if pending_receipts:
            known_ids = set(purchase_request.items.values_list('id', flat=True))
            adds_new_item = False
            for idx, raw_id in enumerate(item_ids):
                row_name = (names[idx] if idx < len(names) else '').strip()
                if not row_name:
                    continue  # ردیف خالی دستنخورده
                if raw_id.isdigit() and int(raw_id) in known_ids:
                    continue  # قلم موجود
                adds_new_item = True
                break
            if adds_new_item:
                messages.error(request, receipt_gate_message(pending_receipts))
                rows = [{
                    "id": raw_id,
                    "name": (names[i] if i < len(names) else '').strip(),
                    "quantity": quantities[i] if i < len(quantities) else '',
                    "unit": (units[i] if i < len(units) else '').strip(),
                    "description": (descriptions[i] if i < len(descriptions) else '').strip(),
                    "removed": raw_id.isdigit() and int(raw_id) in removed_ids,
                } for i, raw_id in enumerate(item_ids)]
                return _render(rows, request.POST.get("delivery_date", "").strip())

        # فقط اقلامی حذف می‌شوند که برای مسئول انبار وضعیتی نگرفته‌اند
        # (محافظ در برابر ویرایش همزمان توسط مسئول انبار)
        locked_item_ids = set(
            OfficerAssignment.objects
            .filter(item__request=purchase_request, target_role='warehouse')
            .exclude(status='pending')
            .values_list('item_id', flat=True)
        )
        removable_ids = removed_ids - locked_item_ids
        if removed_ids & locked_item_ids:
            messages.warning(
                request,
                "برخی اقلام انتخاب‌شده برای حذف، توسط مسئول انبار تعیین وضعیت شده‌اند و حذف نشدند.")
        purchase_request.items.filter(id__in=removable_ids).delete()
        # ردیف‌های prefetch شده را از کش مدل هم حذف کن تا با رکورد حذف‌شده کار نشود
        if hasattr(purchase_request, '_prefetched_objects_cache'):
            purchase_request._prefetched_objects_cache.pop('items', None)
        existing = {item.id: item for item in purchase_request.items.all()}

        posted_rows = []
        new_items = []
        error_message = None
        for idx, raw_id in enumerate(item_ids):
            name = (names[idx] if idx < len(names) else "").strip()
            qty = _parse_decimal(quantities[idx] if idx < len(quantities) else "")
            unit = (units[idx] if idx < len(units) else "").strip()
            desc = (descriptions[idx] if idx < len(descriptions) else "").strip()
            posted_rows.append({
                "id": raw_id,
                "name": name,
                "quantity": quantities[idx] if idx < len(quantities) else "",
                "unit": unit,
                "description": desc,
                "removed": raw_id in removed_ids,
            })
            if not name:
                error_message = "نام همهٔ اقلام الزامی است"
                break
            if qty is None or qty <= 0 or qty > 99999999:
                error_message = f"مقدار «{name}» باید عددی بزرگ‌تر از صفر باشد"
                break
            if raw_id.isdigit() and int(raw_id) in removable_ids:
                continue  # ردیف حذف‌شده — پردازش نمی‌شود
            if raw_id.isdigit() and int(raw_id) in existing:
                item = existing[int(raw_id)]
                if (item.name or '').strip() != name:
                    # عوض‌کردن نامِ یک ردیف با کالای غذاییِ بدون رسپی، همان
                    # دور زدنِ فرم ثبت است — انبار بر اساس همین نام تامین می‌کند.
                    gate_error = _recipe_gate_for_name(request, cafe, name)
                    if gate_error:
                        error_message = gate_error
                        break
                item.name = name
                item.quantity = qty
                if unit:
                    item.unit = unit
                item.description = desc
                item.save()
            else:
                item_product = None
                if cafe:
                    item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first()
                inactive_err = inactive_product_error(item_product)
                if inactive_err:
                    error_message = inactive_err
                    break
                if item_product is not None and not bypasses_recipe_gate(request.user):
                    gate_error = recipe_gate_error(item_product, cafe=cafe)
                    if gate_error:
                        error_message = gate_error
                        break
                new_items.append(PurchaseItem(
                    request=purchase_request, product=item_product,
                    name=name, quantity=qty, unit=unit or "عدد",
                    description=desc or "",
                ))

        if error_message:
            messages.error(request, error_message)
            return _render(posted_rows, request.POST.get("delivery_date", "").strip())

        if new_items:
            for item in new_items:
                item.save()
            # اقلام جدید مثل فرم ثبت، مستقیم به صندوق مسئول انبار می‌روند
            _route_request_to_warehouse(
                purchase_request, new_items, request.user,
                notes='قلم اضافه‌شده هنگام ویرایش درخواست توسط درخواست‌دهنده')

        # زمان تحویل (اختیاری — در صورت انتخاب، جایگزین می‌شود)
        delivery_raw = request.POST.get('delivery_date', '').strip()
        if delivery_raw:
            try:
                purchase_request.delivery_date = datetime.strptime(delivery_raw, '%Y-%m-%d').date()
                purchase_request.delivery_immediate = False
                purchase_request.save(update_fields=['delivery_date', 'delivery_immediate'])
            except (ValueError, TypeError):
                pass

        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action="updated",
            description="ویرایش درخواست توسط صاحب آن (بخش درخواست‌های من)",
        )
        messages.success(request, "درخواست با موفقیت ویرایش شد.")
        return redirect('purchase_requests_list', cafe_code=cafe_code)

    form_rows = [{
        "id": item.id,
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "description": item.description,
        "removed": False,
    } for item in purchase_request.items.all()]
    return _render(
        form_rows,
        purchase_request.delivery_date.isoformat() if purchase_request.delivery_date else "",
    )


# ---------------------------------------------------------------------------
# Reporting and export
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager"])
def reports_dashboard(request):
    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.all()
    inv_qs = Invoice.objects.all()
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
        inv_qs = inv_qs.filter(cafe=cafe)
    stats = {
        "total_requests": pr_qs.count(),
        "completed_orders": PurchaseOrder.objects.filter(status="completed").count(),
        "paid_invoices": inv_qs.filter(status="paid").count(),
    }
    return render(request, "reports_dashboard.html", {"stats": stats})


@custom_login_required
@role_required(["purchasing", "manager"])
def export_to_excel(request):
    cafe = get_cafe_from_request(request)
    output = io.BytesIO()
    output.write(b"Request ID,Status\n")
    pr_qs = PurchaseRequest.objects.all()
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
    for pr in pr_qs[:500]:
        output.write(f"{pr.id},{pr.status}\n".encode("utf-8"))

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="reports.xlsx"'
    return response


@custom_login_required
@role_required(["purchasing", "manager"])
def export_to_pdf(request):
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="reports.pdf"'
    response.write(b"%PDF-1.4\n%PDF placeholder for tests\n")
    return response


@custom_login_required
@role_required(["purchasing", "manager"])
def export_enhanced_excel(request):
    # Reuse base export for simplicity
    return export_to_excel(request)


@custom_login_required
@role_required(["purchasing", "manager"])
def export_analytics_to_pdf(request, analytics_type):
    return export_to_pdf(request)


# ---------------------------------------------------------------------------
# Backup, Export/Import, System Health & Monitoring (extracted to views_backups.py)
# ---------------------------------------------------------------------------
from .views_backups import (
    _human_size,
    _snapshot_db_before_restore,
    _is_sqlite_engine,
    _EXCLUDED_FOLDER_BACKUP_DIRS,
    _build_data_folder_zip,
    _TempBackupFile,
    run_auto_backup_if_due,
    trigger_auto_backup_async,
    backup_management,
    backup_database,
    download_database_file,
    restore_database_file,
    download_backup_file,
    delete_backup_file,
    _drive_back_to_backup,
    drive_connect,
    drive_callback,
    drive_save_config,
    drive_disconnect,
    drive_backup_now,
    export_data_folder,
    export_data_json,
    export_data_csv,
    import_data_json,
    create_migration_script,
    run_migration_script,
    system_health_check,
    system_monitoring,
    system_monitoring_api,
)

# ---------------------------------------------------------------------------
# Purchase List Edit and Confirm
# ---------------------------------------------------------------------------

@custom_login_required
@role_required(["manager"])
def purchase_list_edit_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """Edit a purchased request - allows manager to modify items"""
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    
    if request.method == 'POST':
        # Update each item
        for item in purchase_request.items.all():
            purchased_qty_key = f'purchased_quantity_{item.id}'
            supply_status_key = f'supply_status_{item.id}'
            
            if purchased_qty_key in request.POST:
                try:
                    item.purchased_quantity = _parse_decimal(request.POST.get(purchased_qty_key, '')) or 0
                except (ValueError, TypeError):
                    item.purchased_quantity = 0
            
            if supply_status_key in request.POST:
                item.supply_status = request.POST.get(supply_status_key, 'pending')
            
            item.save()
        
        # Get the date and role for redirect
        date_str = request.POST.get('date', '')
        role = request.POST.get('role', '')

        if date_str and role:
            return redirect(f"{get_cafe_url('purchase_day_detail', request)}?date={date_str}&role={role}")
        return get_cafe_redirect_url('purchase_lists_by_date', request)

    # GET request - show edit form
    date_str = request.GET.get('date', '')
    role = request.GET.get('role', '')
    
    return render(request, "purchase_list_edit_request.html", {
        "purchase_request": purchase_request,
        "date_str": date_str,
        "role": role,
    })


@custom_login_required
@role_required(["manager"])
def purchase_list_confirm_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """Confirm a purchased request by manager"""
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    
    if request.method == 'POST':
        # Toggle confirmation status
        if purchase_request.manager_confirmed:
            purchase_request.manager_confirmed = False
            purchase_request.manager_confirmed_at = None
            purchase_request.manager_confirmed_by = None
        else:
            purchase_request.manager_confirmed = True
            purchase_request.manager_confirmed_at = timezone.now()
            purchase_request.manager_confirmed_by = request.user
        
        purchase_request.save()

        # Get the date and role for redirect
        date_str = request.POST.get('date', '')
        role = request.POST.get('role', '')

        if date_str and role:
            return redirect(f"{get_cafe_url('purchase_day_detail', request)}?date={date_str}&role={role}")
        return get_cafe_redirect_url('purchase_lists_by_date', request)

    return get_cafe_redirect_url('purchase_lists_by_date', request)


# ---------------------------------------------------------------------------
# Products, units, categories, payment types
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager"])
def product_list(request):
    cafe = get_cafe_from_request(request)
    products = Product.objects.all()
    if cafe:
        products = products.filter(cafe=cafe)

    # قیمت واحد کوچک‌ترین واحد + اینکه کالا در رسپی منو مصرف می‌شود یا نه
    products = products.select_related('category', 'unit', 'cogs_item')

    # جستجو بر اساس نام محصول
    search = request.GET.get('search', '')
    if search:
        products = products.filter(name__icontains=search)

    # فیلتر بر اساس دسته‌بندی
    category_id = request.GET.get('category', '')
    if category_id:
        products = products.filter(category_id=category_id)

    # فیلتر بر اساس وضعیت
    is_active = request.GET.get('is_active', '')
    if is_active == 'true':
        products = products.filter(is_active=True)
    elif is_active == 'false':
        products = products.filter(is_active=False)

    # فیلتر بر اساس قیمت‌گذاری (همه / فقط قیمت‌دار / فقط بی‌قیمت)
    pricing = request.GET.get('pricing', '')
    if pricing == 'priced':
        products = products.filter(base_unit_price__isnull=False)
    elif pricing == 'unpriced':
        products = products.filter(base_unit_price__isnull=True)

    products = list(products)
    # مصرف‌شدن در رسپی: یک کوئری برای همه، نه یکی برای هر محصول.
    # (getattr لازم است — دسترسی مستقیم به OneToOne معکوس خطا می‌دهد.)
    linked = {p.pk: getattr(p, 'cogs_item', None) for p in products}
    item_ids = [item.pk for item in linked.values() if item is not None]
    if item_ids:
        recipe_lines = KukerRecipeLine.objects.filter(material_id__in=item_ids)
        if cafe is not None:
            # همان قاعدهٔ درخواست‌کننده: فقط رسپیِ همین کافه معتبر است
            recipe_lines = recipe_lines.filter(item__cafe=cafe)
        used_material_ids = set(
            recipe_lines.values_list('material_id', flat=True))
    else:
        used_material_ids = set()

    # دسته‌های غذایی — برای نشان‌دادن «فعال/مخفی در سفارش» در لیست کالاها
    food_category_ids = recipe_required_category_ids(cafe)
    for product in products:
        item = linked[product.pk]
        product.cogs_item_pk = item.pk if item else None
        product.cogs_used_in_recipe = bool(item and item.pk in used_material_ids)
        product.is_food_category = product.category_id in food_category_ids
        product.is_rd_category = is_rd_product(product)

    # فیلتر «وضعیت رسپی»: راهِ رسیدن به کالاهایی که برای درخواست‌کننده مخفی
    # شده‌اند، وگرنه پیدا کردنشان فقط با اسکرولِ کل لیست ممکن بود.
    recipe_filter = request.GET.get('recipe', '')
    if recipe_filter == 'used':
        products = [p for p in products if p.cogs_used_in_recipe]
    elif recipe_filter == 'missing':
        products = [p for p in products
                    if p.is_food_category and not p.cogs_used_in_recipe]
    elif recipe_filter == 'free':
        products = [p for p in products if not p.is_food_category]

    categories = Category.objects.all()
    if cafe:
        categories = categories.filter(cafe=cafe)
    return render(request, "product_list.html", {
        "products": products,
        "categories": categories,
        "search": search,
        "selected_category": category_id,
        "selected_status": is_active,
        "selected_pricing": pricing,
        "selected_recipe": recipe_filter,
        "unit_choices": PRICE_UNIT_CHOICES,
        "recipe_categories": [c.name for c in categories if c.requires_recipe],
    })


def _parse_conversion_rate(value):
    """تبدیل مقدار ورودی «میزان بسته‌بندی» به عدد اعشاری - خالی به None تبدیل می‌شود"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


# واحدهایی که کاربر می‌تواند قیمت را با آن وارد کند (قیمت همیشه به کوچک‌ترین
# واحد نرمال می‌شود: کیلوگرم→گرم، لیتر→میل)
PRICE_UNIT_CHOICES = ['کیلوگرم', 'لیتر', 'گرم', 'میل', 'عدد', 'پرس', 'بسته', 'اسلایس']


def _apply_product_price(product, request, price_field='base_unit_price', unit_field='price_unit'):
    """قیمت وارد‌شده در فرم را نرمال و روی محصول + ماده اولیه منو اعمال می‌کند.

    قیمت خالی یعنی «دست نزن» — مقدار قبلی حفظ می‌شود.
    Returns ``(linked KukerItem or None, price_changed)``.
    """
    from .services.product_sync import sync_product

    raw_price = request.POST.get(price_field)
    if raw_price is None or str(raw_price).strip() == '':
        return None, False
    price = _parse_conversion_rate(raw_price)
    if price is None or price < 0:
        return None, False
    old_price = product.base_unit_price
    unit = (request.POST.get(unit_field) or product.smallest_unit or '').strip()[:20]
    # sync_product خودش نرمال‌سازی و هم‌گام‌سازی با ماده اولیه منو را انجام می‌دهد
    item = sync_product(product, price=price, unit=unit)
    return item, (product.base_unit_price != old_price)


def _snapshot_costs_after_price_change(request, cafe, item_name):
    """بهای غذاهایی که این ماده اولیه را مصرف می‌کنند دوباره ثبت می‌شود."""
    from .services.menu_cogs import record_cost_snapshots_for_cafe

    cafe = cafe or get_cafe_from_request(request)
    if cafe is None:
        return
    record_cost_snapshots_for_cafe(cafe, note=f'تغییر قیمت «{item_name}»')


def _next_delivery_days():
    """روزهای قابل انتخاب زمان تحویل: ۷ روز آینده (از فردا) فقط یکشنبه تا پنجشنبه
    — جمعه و شنبه نمایش داده نمی‌شوند — به‌صورت (تاریخ، نام روز هفته)"""
    from datetime import timedelta
    from django.utils import timezone as _tz
    weekday_names = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']
    today = _tz.localtime(_tz.now()).date()  # ساعت تهران
    days = []
    for offset in range(1, 8):  # ۷ روز آینده از فردا
        d = today + timedelta(days=offset)
        if d.weekday() in (4, 5):  # جمعه و شنبه قابل سفارش نیستند
            continue
        days.append((d, weekday_names[d.weekday()]))
    return days


def _parse_int_or_none(value):
    """تبدیل مقدار ورودی عدد صحیح (مثل عمر مفید) - خالی یا نامعتبر به None تبدیل می‌شود"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _render_product_form(request, product=None, mode='create', error='', form_data=None):
    """رندر مشترک فرم محصول"""
    cafe = get_cafe_from_request(request)
    categories = Category.objects.all()
    if cafe:
        categories = categories.filter(cafe=cafe)
    departments_qs = Department.objects.filter(is_active=True, cafe=cafe)
    cogs_item = getattr(product, 'cogs_item', None) if product is not None else None
    return render(request, "product_form.html", {
        "product": product,
        "mode": mode,
        "categories": categories,
        "units": Unit.objects.all(),
        "departments_qs": departments_qs,
        "departments": DEPARTMENT_CHOICES,
        "error": error,
        "form_data": form_data,
        "cafe_code": get_cafe_code(request) or 'local',
        "price_units": PRICE_UNIT_CHOICES,
        "cogs_item": cogs_item,
        "cogs_used_in_recipe": bool(cogs_item and cogs_item.is_used_in_recipe),
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def product_create(request):
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            return _render_product_form(request, error='نام محصول الزامی است.', form_data=request.POST)
        # Check for duplicate product name in same cafe
        existing = Product.objects.filter(name__iexact=name)
        if cafe:
            existing = existing.filter(cafe=cafe)
        if existing.exists():
            return _render_product_form(
                request,
                error=f'محصولی با نام «{name}» قبلاً ثبت شده است.',
                form_data=request.POST,
            )
        category = Category.objects.filter(id=request.POST.get("category") or None).first()
        unit = Unit.objects.filter(id=request.POST.get("unit") or None).first()
        selected_product = Product.objects.create(
            name=name,
            category=category,
            unit=unit,
            min_stock=int(request.POST.get("min_stock") or 0),
            shelf_life_days=_parse_int_or_none(request.POST.get("shelf_life_days")),
            usage_location=request.POST.get("usage_location", "general"),
            is_active=("is_active" in request.POST),
            cafe=cafe,
        )
        # قیمت مواد غذایی: هم روی محصول و هم روی ماده اولیه منو ثبت می‌شود
        _item, price_changed = _apply_product_price(selected_product, request)
        if price_changed:
            _snapshot_costs_after_price_change(request, cafe, selected_product.name)
        messages.success(request, f'محصول «{name}» ایجاد شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')

    return _render_product_form(request)


@custom_login_required
@role_required(["purchasing", "manager"])
def product_update(request, product_id):
    """ویرایش اطلاعات پایه محصول (نام، دسته‌بندی، واحد، حداقل موجودی، ...)"""
    cafe = get_cafe_from_request(request)
    product = get_object_or_404(Product, id=product_id)
    if request.method == "POST":
        new_name = (request.POST.get("name") or "").strip()
        if not new_name:
            return _render_product_form(request, product=product, mode='edit_product',
                                        error='نام محصول الزامی است.', form_data=request.POST)
        existing = Product.objects.filter(name__iexact=new_name).exclude(id=product.id)
        if cafe:
            existing = existing.filter(cafe=cafe)
        if existing.exists():
            return _render_product_form(request, product=product, mode='edit_product',
                                        error=f'محصولی با نام «{new_name}» قبلاً ثبت شده است.', form_data=request.POST)
        product.name = new_name
        product.category = Category.objects.filter(id=request.POST.get("category") or None).first() or product.category
        product.unit = Unit.objects.filter(id=request.POST.get("unit") or None).first() or product.unit
        product.min_stock = int(request.POST.get("min_stock") or product.min_stock)
        # خالی بودن فیلد عمر مفید یعنی پاک شدن آن
        product.shelf_life_days = _parse_int_or_none(request.POST.get("shelf_life_days"))
        product.usage_location = request.POST.get("usage_location", product.usage_location)
        product.description = request.POST.get("description", product.description)
        if "is_active_present" in request.POST:
            product.is_active = ("is_active" in request.POST)
        # تکمیل اطلاعات کالای ناقص (ساخته‌شده از فرم درخواست): با اولین ویرایش کامل می‌شود
        # و از این پس در پیشنهادهای درخواست نمایش داده می‌شود
        if not product.is_complete:
            product.is_complete = True
        product.save()
        # قیمت واحد کوچک‌ترین واحد — هم‌گام با ماده اولیه منو (در صورت وجود)
        _item, price_changed = _apply_product_price(product, request)
        if price_changed:
            _snapshot_costs_after_price_change(request, cafe, product.name)
        messages.success(request, 'محصول با موفقیت ویرایش شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')
    return _render_product_form(request, product=product, mode='edit_product')


@custom_login_required
@role_required(["purchasing", "manager"])
def product_toggle_active(request, product_id):
    """تغییر سریع وضعیت فعال/غیرفعال محصول در سفارش‌گذاری."""
    cafe_code = get_cafe_code(request) or 'local'
    product = get_scoped_object_or_404(Product, request, id=product_id)
    if request.method == "POST":
        product.is_active = not product.is_active
        product.save(update_fields=['is_active', 'updated_at'])
        state_label = 'فعال' if product.is_active else 'غیرفعال'
        messages.success(request, f'محصول «{product.name}» در سفارش‌گذاری {state_label} شد.')
    from urllib.parse import urlencode
    kept_params = {
        key: request.POST.get(f'filter_{key}') or request.GET.get(key)
        for key in ('search', 'category', 'is_active', 'pricing', 'recipe')
        if (request.POST.get(f'filter_{key}') or request.GET.get(key))
    }
    target = reverse("product_list", kwargs={"cafe_code": cafe_code})
    if kept_params:
        target = f"{target}?{urlencode(kept_params)}"
    return redirect(target)


@custom_login_required
@role_required(["purchasing", "manager"])
def product_delete(request, product_id):
    cafe_code = get_cafe_code(request) or 'local'
    product = get_object_or_404(Product, id=product_id)
    if request.method == "POST":
        product.delete()
        return redirect("product_list", cafe_code=cafe_code)
    return render(request, "product_confirm_delete.html", {"product": product})


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_list(request):
    units = filter_by_cafe(Unit.objects.all(), request)
    return render(request, "unit_list.html", {"units": units})


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        symbol = request.POST.get("symbol", "")
        if name:
            Unit.objects.create(name=name, symbol=symbol, cafe=get_cafe_from_request(request))
            return get_cafe_redirect_url("unit_list", request)
    return render(request, "unit_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_update(request, unit_id):
    # فقط واحد کافه جاری قابل ویرایش است — واحد کافه دیگر ۴۰۴ میشود
    unit = get_scoped_object_or_404(Unit, request, id=unit_id)
    if request.method == "POST":
        unit.name = request.POST.get("name", unit.name)
        unit.symbol = request.POST.get("symbol", unit.symbol)
        unit.save()
        return get_cafe_redirect_url("unit_list", request)
    return render(request, "unit_form.html", {"unit": unit})


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_delete(request, unit_id):
    unit = get_scoped_object_or_404(Unit, request, id=unit_id)
    if request.method == "POST":
        unit.delete()
        return get_cafe_redirect_url("unit_list", request)
    return render(request, "unit_confirm_delete.html", {"unit": unit})


@custom_login_required
@role_required(["purchasing", "manager"])
def category_list(request):
    categories = filter_by_cafe(Category.objects.all(), request)
    return render(request, "category_list.html", {"categories": categories})


def _category_parent_choices(cafe, exclude_id=None):
    """دسته‌های والد قابل انتخاب (همه کافه جاری، بدون خود دسته)"""
    parents = Category.objects.filter(parent__isnull=True, is_active=True)
    if exclude_id:
        parents = parents.exclude(id=exclude_id)
    if cafe:
        parents = parents.filter(cafe=cafe)
    return parents


@custom_login_required
@role_required(["purchasing", "manager"])
def category_create(request):
    """ایجاد دسته‌بندی جدید — شامل تعیین «نیازمند استفاده در رسپی»"""
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        error = None
        if not name:
            error = 'نام دسته‌بندی الزامی است.'
        elif Category.objects.filter(name__iexact=name, cafe=cafe).exists():
            error = f'دسته‌بندی «{name}» در این کافه از قبل وجود دارد.'
        if error:
            return render(request, "category_form.html", {
                "category": None,
                "parent_categories": _category_parent_choices(cafe),
                "error": error,
                "form_data": request.POST,
            })
        parent_id = request.POST.get("parent")
        category = Category.objects.create(
            name=name,
            description=(request.POST.get("description") or "").strip(),
            parent=Category.objects.filter(id=parent_id, cafe=cafe).first() if parent_id else None,
            requires_recipe=request.POST.get("requires_recipe") == "on",
            is_active=True,
            cafe=cafe,
        )
        messages.success(request, f'دسته‌بندی «{category.name}» ایجاد شد.')
        return get_cafe_redirect_url("category_list", request)

    return render(request, "category_form.html", {
        "category": None,
        "parent_categories": _category_parent_choices(cafe),
        "form_data": {"requires_recipe": "on"},
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def category_update(request, category_id):
    cafe = get_cafe_from_request(request)
    # فقط دستهٔ کافه جاری قابل ویرایش است — دستهٔ کافه دیگر ۴۰۴ میشود
    category = get_scoped_object_or_404(Category, request, id=category_id)
    if request.method == "POST":
        category.name = request.POST.get("name", category.name)
        category.description = request.POST.get("description", category.description)
        parent_id = request.POST.get("parent")
        if parent_id:
            category.parent = Category.objects.filter(id=parent_id, cafe=cafe).first()
        else:
            category.parent = None
        category.requires_recipe = request.POST.get("requires_recipe") == "on"
        category.save()
        return get_cafe_redirect_url("category_list", request)

    # Get all parent categories except the current one and its descendants
    parent_categories = Category.objects.filter(
        parent__isnull=True,
        is_active=True
    ).exclude(id=category_id)
    if cafe:
        parent_categories = parent_categories.filter(cafe=cafe)

    return render(request, "category_form.html", {
        "category": category,
        "parent_categories": parent_categories
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def category_delete(request, category_id):
    category = get_scoped_object_or_404(Category, request, id=category_id)
    if request.method == "POST":
        category.delete()
        return get_cafe_redirect_url("category_list", request)
    return render(request, "category_confirm_delete.html", {"category": category})


# ---------------------------------------------------------------------------
# Department (بخش) Management — از صفحه مدیریت کاربران
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Purchase Orders
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_orders_list(request):
    orders = PurchaseOrder.objects.select_related("supplier", "request").all()
    return render(request, "purchase_orders_list.html", {"orders": orders})


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_create(request, request_id):
    cafe = get_cafe_from_request(request)
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    suppliers = Supplier.objects.all()
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    if request.method == "POST":
        supplier = Supplier.objects.filter(id=request.POST.get("supplier")).first()
        order_number = request.POST.get("order_number") or f"ORD-{timezone.now().strftime('%Y%m%d%H%M%S')}"
        order = PurchaseOrder.objects.create(
            request=purchase_request,
            supplier=supplier,
            order_number=order_number,
            status="ordered",
            created_by=request.user,
            delivery_date=request.POST.get("expected_delivery_date") or None,
            notes=request.POST.get("notes", ""),
        )
        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action="updated",
            description="Purchase order created",
        )
        return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)
    return render(
        request,
        "purchase_order_form.html",
        {"purchase_request": purchase_request, "suppliers": suppliers},
    )


def purchase_order_detail(request, order_id):
    order = get_object_or_404(PurchaseOrder, id=order_id)
    return render(request, "purchase_order_detail.html", {"order": order})


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_update(request, order_id):
    order = get_object_or_404(PurchaseOrder, id=order_id)
    if request.method == "POST":
        order.status = request.POST.get("status", order.status)
        order.save()
        return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)
    return render(request, "purchase_order_form.html", {"order": order})


def _set_order_status(request, order_id, status):
    order = get_object_or_404(PurchaseOrder, id=order_id)
    order.status = status
    order.save()
    return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_send(request, order_id):
    return _set_order_status(request, order_id, "sent")


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_confirm(request, order_id):
    return _set_order_status(request, order_id, "confirmed")


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_mark_in_transit(request, order_id):
    return _set_order_status(request, order_id, "in_transit")


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_deliver(request, order_id):
    return _set_order_status(request, order_id, "delivered")


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_inspect(request, order_id):
    return _set_order_status(request, order_id, "inspected")


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_complete(request, order_id):
    order = get_object_or_404(PurchaseOrder.objects.select_related('request'), id=order_id)
    order.status = "completed"
    order.save()
    # تکمیل سفارش خرید = پایان مسیر «درخواست خرید» مسئول انبار: درخواستِ مرتبط
    # «خریداری شده» می‌شود تا در فهرست‌های در انتظار باقی نماند
    pr = order.request
    if pr and pr.source == 'warehouse_purchase' and pr.status in ('pending', 'pending_manager_approval'):
        pr.status = 'purchased'
        pr.save(update_fields=['status'])
        ActivityLog.objects.create(
            user=request.user,
            request=pr,
            action='updated',
            description=f'تکمیل سفارش خرید {order.order_number} — درخواست {request_number_for(pr.id)} خریداری شد')
    return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_cancel(request, order_id):
    return _set_order_status(request, order_id, "cancelled")


@custom_login_required
@role_required(["purchasing", "manager"])
def approved_requests_for_orders(request):
    cafe = get_cafe_from_request(request)
    # جریان جدید: درخواست خریدِ مسئول انبار که مدیرعامل تایید کرده
    # (source=warehouse_purchase و status=pending) اینجا می‌آید تا واحد خرید
    # «سفارش خرید» ثبت کند. باقی‌ماندهٔ جریان قدیمی (approved/purchased) هم
    # برای سازگاری نمایش داده می‌شود.
    approved_requests = PurchaseRequest.objects.filter(
        Q(source='warehouse_purchase', status='pending')
        | Q(status__in=['approved', 'purchased'], source='requester'))
    if cafe:
        approved_requests = approved_requests.filter(cafe=cafe)
    return render(
        request,
        "approved_requests_for_orders.html",
        {"requests": approved_requests},
    )


# ---------------------------------------------------------------------------
# Invoices, Payments & Suppliers (extracted to views_invoicing.py)
# ---------------------------------------------------------------------------
from .views_invoicing import (
    payment_type_list,
    payment_type_create,
    payment_type_update,
    payment_type_delete,
    invoices_list,
    all_invoices_list,
    invoice_register,
    invoice_create,
    invoice_detail,
    invoice_update,
    invoice_send,
    finance_manager_edited_invoices,
    invoice_cancel,
    invoice_delete,
    invoice_bulk_payment,
    invoice_bulk_delete,
    completed_orders_for_invoices,
    payment_list,
    payment_create,
    payment_detail,
    payment_update,
    payment_delete,
    payment_location_list,
    payment_location_create,
    payment_location_update,
    supplier_list,
    supplier_select,
    _supplier_form_data,
    supplier_create,
    supplier_detail,
    supplier_update,
    supplier_delete,
    invoice_issue_list,
    invoice_issue_create,
    invoice_issue_delete,
    supplier_activate,
    supplier_deactivate,
    supplier_evaluation_list,
    supplier_evaluation_create,
    supplier_evaluation_all_list,
    supplier_evaluation_create_general,
    supplier_performance_report,
)


# ---------------------------------------------------------------------------
# Debts & CEO Payments (extracted to views_debts.py)
# ---------------------------------------------------------------------------
from .views_debts import (
    debt_list,
    ceo_debt_payments,
    debt_pay,
    debt_pay_offlist,
    debt_edit,
    debt_delete,
    debt_payment_edit,
    debt_payment_delete,
    mobile_debt_dashboard,
    payment_accounts,
)


# ---------------------------------------------------------------------------
# Additional reports and analytics
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_reports(request):
    return render(request, "purchase_reports.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_analytics(request):
    return render(request, "payment_analytics.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def inventory_reports(request):
    return render(request, "inventory_reports.html")


# ---------------------------------------------------------------------------
# Consumption Report (گزارش مصرف) — بخش انبار و مدیریت
# ---------------------------------------------------------------------------

def _parse_consumption_date(request, prefix, fallback):
    """خواندن تاریخ از پارامترهای GET گزارش مصرف:
    ۱) `start_date` / `end_date` (به صورت شمسی `1405/08/10` یا میلادی `2026-11-01`)
    ۲) `start_year`, `start_month`, `start_day` (یا `end_*`) از انتخابگر شمسی
    ۳) مقدار پیش‌فرض `fallback`
    """
    import jdatetime

    raw = (request.GET.get(f'{prefix}_date') or '').strip()
    if raw:
        parsed = _parse_jalali_date(raw)
        if parsed is not None:
            return parsed

    y_raw = (request.GET.get(f'{prefix}_year') or '').strip()
    m_raw = (request.GET.get(f'{prefix}_month') or '').strip()
    d_raw = (request.GET.get(f'{prefix}_day') or '').strip()
    if y_raw.isdigit() and m_raw.isdigit() and d_raw.isdigit():
        try:
            return jdatetime.date(int(y_raw), int(m_raw), int(d_raw)).togregorian()
        except (ValueError, TypeError):
            pass

    return fallback


def _build_product_pricing_map(products, cafe=None):
    """محاسبه آخرین قیمت واردشده برای فهرست محصولات.

    منابع قیمت (به ترتیب اولویت):
    ۱) `ProductPriceEntry`: آخرین قیمت ثبت‌شده توسط کاربر (`price_per_smallest_unit`, `smallest_unit`, `entered_amount`, `entered_unit`)
    ۲) `Product.base_unit_price` و `KukerItem(kind='raw')`: قیمت پایه محصول در بهای تمام شده
       (با تشخیص هوشمند واحد از روی خطوط رسپی وقتی `smallest_unit` خالی است)
    ۳) `PriceHistory`: آخرین رکورد تاریخچه قیمت خرید
    ۴) `InvoiceItem`: آخرین ردیف فاکتور خرید تایید/ثبت شده
    """
    from .services.units import smallest_unit_of, unit_family

    product_list = [p for p in products if p is not None]
    if not product_list:
        return {}

    pids = [p.id for p in product_list]

    # ۱) آخرین ProductPriceEntry برای هر محصول
    latest_entry = {}
    entries_qs = ProductPriceEntry.objects.filter(product_id__in=pids).order_by('product_id', '-recorded_at', '-id')
    if cafe:
        entries_qs = entries_qs.filter(cafe=cafe)
    for entry in entries_qs:
        if entry.product_id not in latest_entry:
            latest_entry[entry.product_id] = entry

    # ۲) KukerItem و واحد رسپی‌ها برای تشخیص واحد قیمت‌های ایمپورت‌شده از اکسل
    kuker_map = {}
    kuker_qs = KukerItem.objects.filter(product_id__in=pids, kind=KukerItem.KIND_RAW)
    if cafe:
        kuker_qs = kuker_qs.filter(cafe=cafe)
    for ki in kuker_qs:
        kuker_map[ki.product_id] = ki

    recipe_units = {}
    if kuker_map:
        k_ids = [ki.id for ki in kuker_map.values()]
        for mat_id, u in KukerRecipeLine.objects.filter(material_id__in=k_ids).values_list('material_id', 'unit'):
            u_clean = (u or '').strip()
            if u_clean:
                recipe_units.setdefault(mat_id, set()).add(u_clean)

    # ۳) آخرین PriceHistory برای هر محصول
    latest_ph = {}
    for ph in PriceHistory.objects.filter(product_id__in=pids).order_by('product_id', '-date', '-id'):
        if ph.product_id not in latest_ph and ph.price and ph.price > 0:
            latest_ph[ph.product_id] = ph

    # ۴) آخرین InvoiceItem برای هر محصول
    latest_inv = {}
    inv_qs = (InvoiceItem.objects
              .filter(product_id__in=pids, unit_price__gt=0)
              .exclude(invoice__status='cancelled')
              .select_related('invoice')
              .order_by('product_id', '-invoice__invoice_date', '-id'))
    if cafe:
        inv_qs = inv_qs.filter(invoice__cafe=cafe)
    for ii in inv_qs:
        if ii.product_id not in latest_inv:
            latest_inv[ii.product_id] = ii

    pricing_map = {}
    for p in product_list:
        p_unit = (p.unit.name if p.unit_id and p.unit else '').strip()
        p_smallest = (p.smallest_unit or '').strip()
        entry = latest_entry.get(p.id)
        ki = kuker_map.get(p.id)
        ph = latest_ph.get(p.id)
        inv = latest_inv.get(p.id)

        info = {
            'has_price': False,
            'price_Basis_amount': None,   # قیمت به ازای ۱ واحدِ basis_unit
            'basis_unit': '',             # واحدی که price_Basis_amount بر اساس آن است (مثلاً گرم یا کیلوگرم)
            'display_price': None,        # قیمت نمایشی برای کاربر
            'display_unit': '',           # واحد نمایشی قیمت (مثلاً هر کیلوگرم / هر گرم / هر عدد)
            'entered_text': '',           # توضیح تکمیلی آخرین قیمت واردشده
            'source_label': '',
        }

        if entry is not None and entry.price_per_smallest_unit is not None and entry.price_per_smallest_unit > 0:
            b_unit = (entry.smallest_unit or p_smallest or smallest_unit_of(entry.entered_unit) or p_unit or 'واحد').strip()
            info['has_price'] = True
            info['price_Basis_amount'] = Decimal(str(entry.price_per_smallest_unit))
            info['basis_unit'] = b_unit
            info['display_price'] = Decimal(str(entry.entered_amount or entry.price_per_smallest_unit))
            info['display_unit'] = (entry.entered_unit or b_unit).strip()
            info['source_label'] = 'ثبت قیمت کالا'
        elif (p.base_unit_price is not None and p.base_unit_price > 0) or (ki and ki.price is not None and ki.price > 0):
            raw_price = Decimal(str(p.base_unit_price if (p.base_unit_price is not None and p.base_unit_price > 0) else ki.price))
            b_unit = p_smallest or (ki.out_unit if ki else '') or ''
            if not b_unit and ki:
                r_units = recipe_units.get(ki.id, set())
                if 'گرم' in r_units:
                    b_unit = 'گرم'
                elif 'میل' in r_units:
                    b_unit = 'میل'
                elif len(r_units) == 1:
                    b_unit = next(iter(r_units))
            if not b_unit:
                b_unit = smallest_unit_of(p_unit) or p_unit or 'واحد'
            info['has_price'] = True
            info['price_Basis_amount'] = raw_price
            info['basis_unit'] = b_unit
            info['display_price'] = raw_price
            info['display_unit'] = b_unit
            info['source_label'] = 'قیمت پایه کالا'
        elif ph is not None:
            b_unit = p_unit or p_smallest or 'واحد'
            info['has_price'] = True
            info['price_Basis_amount'] = Decimal(str(ph.price))
            info['basis_unit'] = b_unit
            info['display_price'] = Decimal(str(ph.price))
            info['display_unit'] = b_unit
            info['source_label'] = 'تاریخچه خرید'
        elif inv is not None:
            b_unit = p_unit or p_smallest or 'واحد'
            info['has_price'] = True
            info['price_Basis_amount'] = Decimal(str(inv.unit_price))
            info['basis_unit'] = b_unit
            info['display_price'] = Decimal(str(inv.unit_price))
            info['display_unit'] = b_unit
            info['source_label'] = 'آخرین فاکتور خرید'

        pricing_map[p.id] = info

    return pricing_map


def _estimate_consumption_cost(qty, tx_unit_name, price_info):
    """محاسبه تخمین قیمت مصرف برای مقدار `qty` با واحد `tx_unit_name` بر اساس `price_info`.

    همچنین قیمتِ معادلِ هر ۱ واحدِ مصرفی (`unit_price_for_tx_unit`) را برمی‌گرداند تا
    در جدول و مودال به زبانِ همان واحدِ تحویلی (مثلاً کیلوگرم یا گرم) قابل فهم باشد.
    """
    from .services.units import unit_family

    if not price_info or not price_info.get('has_price') or price_info.get('price_Basis_amount') is None:
        return None, None, ''

    qty_dec = Decimal(str(qty or 0))
    basis_price = Decimal(str(price_info['price_Basis_amount']))
    basis_unit = (price_info.get('basis_unit') or '').strip()
    tx_unit = (tx_unit_name or basis_unit or 'واحد').strip()

    fam_tx = unit_family(tx_unit)
    fam_basis = unit_family(basis_unit)

    factor = Decimal('1')
    if fam_tx in ('kg', 'liter') and fam_basis in ('gram', 'mil'):
        factor = Decimal('1000')
    elif fam_tx in ('gram', 'mil') and fam_basis in ('kg', 'liter'):
        factor = Decimal('0.001')

    unit_price_in_tx_unit = (basis_price * factor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    estimated_cost = (qty_dec * basis_price * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return estimated_cost, unit_price_in_tx_unit, tx_unit


def _collect_consumption_deliveries(cafe, start_dt=None, end_dt=None, product_id=None):
    """استخراج و نرمال‌سازی تمامی تحویل‌های خروجی انبار (`StockTransaction` با `transaction_type='out'`).

    - تخصیص‌های بازگشایی‌شده (`officer_assignment_reopen`) و حواله‌های برگشت‌خورده (`reversed`)
      به صورت خودکار فیلتر می‌شوند تا فقط تحویل‌های واقعی محاسبه شوند.
    - بخش تحویل‌گیرنده (`section_name`, `department_id`) برای همه مسیرها (`officer_assignment`,
      `purchase_item`, `warehouse_request`, `stock_request`, `warehouse_voucher`) استخراج می‌شود.
    """
    qs = StockTransaction.objects.filter(transaction_type='out').select_related(
        'stock_item__product__category__parent',
        'stock_item__product__unit',
        'stock_item__warehouse',
        'unit',
        'voucher__department',
        'voucher__destination_warehouse',
        'voucher__created_by__department',
        'created_by__department',
    )
    if cafe is not None:
        qs = qs.filter(stock_item__warehouse__cafe=cafe)
    if start_dt is not None:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt is not None:
        qs = qs.filter(created_at__lte=end_dt)
    if product_id is not None:
        qs = qs.filter(stock_item__product_id=product_id)

    txs = list(qs.order_by('-created_at', '-id'))
    if not txs:
        return [], {}

    # شناسایی تخصیص‌های بازگشایی‌شده تا از گزارش مصرف حذف شوند
    oa_ref_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type == 'officer_assignment' and tx.reference_id
    }
    reopened_item_ids = set()
    if oa_ref_ids:
        reopen_qs = StockTransaction.objects.filter(
            reference_type='officer_assignment_reopen',
            reference_id__in=oa_ref_ids,
        )
        if cafe is not None:
            reopen_qs = reopen_qs.filter(stock_item__warehouse__cafe=cafe)
        reopened_item_ids.update(reopen_qs.values_list('reference_id', flat=True))

        # اگر تمام تخصیص‌های انبار یک قلم در وضعیت reopened باشند
        active_oa_item_ids = set(
            OfficerAssignment.objects.filter(
                item_id__in=oa_ref_ids,
                target_role='warehouse',
            ).exclude(status='reopened').values_list('item_id', flat=True)
        )
        all_oa_item_ids = set(
            OfficerAssignment.objects.filter(
                item_id__in=oa_ref_ids,
                target_role='warehouse',
            ).values_list('item_id', flat=True)
        )
        reopened_item_ids.update(all_oa_item_ids - active_oa_item_ids)

    # واکشی یکجای رکوردهای مرجع (PurchaseItem, WarehouseRequest, StockRequest)
    pi_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type in ('officer_assignment', 'purchase_item') and tx.reference_id
    }
    wh_req_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type == 'warehouse_request' and tx.reference_id
    }
    sreq_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type == 'stock_request' and tx.reference_id
    }

    pi_map = {}
    if pi_ids:
        pi_map = {
            pi.id: pi for pi in PurchaseItem.objects.filter(id__in=pi_ids)
            .select_related('request__user__department')
        }

    wh_req_map = {}
    if wh_req_ids:
        wh_req_map = {
            wr.id: wr for wr in WarehouseRequest.objects.filter(id__in=wh_req_ids)
            .select_related('user__department')
        }

    sreq_map = {}
    if sreq_ids:
        sreq_map = {
            sr.id: sr for sr in StockRequest.objects.filter(id__in=sreq_ids)
            .select_related('purchase_item__request__user__department', 'requested_by__department')
        }

    # نگاشت نام بخش به Department.id در کافه فعلی
    dept_by_name = {}
    if cafe is not None:
        for d in Department.objects.filter(cafe=cafe):
            dept_by_name[d.name.strip()] = d.id

    products_seen = {}
    for tx in txs:
        if tx.stock_item_id and tx.stock_item and tx.stock_item.product_id:
            products_seen[tx.stock_item.product_id] = tx.stock_item.product

    pricing_map = _build_product_pricing_map(products_seen.values(), cafe=cafe)

    deliveries = []
    for tx in txs:
        # ۱) حذف تخصیص‌های بازگشایی‌شده
        if tx.reference_type == 'officer_assignment' and tx.reference_id in reopened_item_ids:
            continue

        # ۲) حذف حواله‌های برگشت‌خورده یا معکوس‌کننده
        voucher = tx.voucher
        if voucher is not None:
            if voucher.status == 'reversed' or voucher.reverses_id is not None:
                continue
            # حواله انتقال بین انبارها مصرف نهایی سکشن نیست مگر مقصد بخش باشد
            if voucher.voucher_type == 'transfer' and not voucher.department_id:
                continue

        si = tx.stock_item
        product = si.product if si else None
        if product is None:
            continue

        # تعیین واحد تراکنش
        unit_name = ''
        if tx.unit_id and tx.unit:
            unit_name = tx.unit.name
        elif si and si.unit_id and si.unit:
            unit_name = si.unit.name
        elif product.unit_id and product.unit:
            unit_name = product.unit.name
        elif product.smallest_unit:
            unit_name = product.smallest_unit
        unit_name = (unit_name or 'عدد').strip()

        # تعیین بخش تحویل‌گیرنده و درخواست‌دهنده
        section_name = 'نامشخص'
        dept_id = None
        requester_name = '—'
        ref_label = 'تحویل انبار'

        if tx.reference_type in ('officer_assignment', 'purchase_item') and tx.reference_id:
            pi = pi_map.get(tx.reference_id)
            if pi and pi.request:
                req_user = pi.request.user
                section_name = _item_section_name(req_user)
                dept_id = getattr(req_user, 'department_id', None)
                requester_name = (req_user.get_full_name() or req_user.username) if req_user else '—'
                ref_label = f'درخواست {request_number_for(pi.request_id)}'
        elif tx.reference_type == 'warehouse_request' and tx.reference_id:
            wr = wh_req_map.get(tx.reference_id)
            if wr:
                req_user = wr.user
                section_name = _item_section_name(req_user)
                dept_id = getattr(req_user, 'department_id', None)
                requester_name = (req_user.get_full_name() or req_user.username) if req_user else '—'
                ref_label = f'درخواست انبار {request_number_for(wr.id)}'
        elif tx.reference_type == 'stock_request' and tx.reference_id:
            sr = sreq_map.get(tx.reference_id)
            if sr:
                req_user = (sr.purchase_item.request.user
                            if (sr.purchase_item and sr.purchase_item.request)
                            else sr.requested_by)
                section_name = _item_section_name(req_user)
                dept_id = getattr(req_user, 'department_id', None)
                requester_name = (req_user.get_full_name() or req_user.username) if req_user else '—'
                ref_label = f'تامین انبار {sr.id}'
        elif voucher is not None:
            if voucher.department_id and voucher.department:
                section_name = voucher.department.name
                dept_id = voucher.department_id
            elif voucher.voucher_type == 'waste':
                section_name = 'ضایعات'
            elif voucher.voucher_type == 'sale':
                section_name = 'فروش'
            elif voucher.voucher_type == 'return':
                section_name = 'مرجوعی به تامین‌کننده'
            elif voucher.created_by:
                section_name = _item_section_name(voucher.created_by)
                dept_id = getattr(voucher.created_by, 'department_id', None)
            else:
                section_name = 'حواله خروج'
            if voucher.created_by:
                requester_name = voucher.created_by.get_full_name() or voucher.created_by.username
            ref_label = f'حواله {voucher.voucher_number}'
        else:
            if product.usage_location and product.usage_location != 'general':
                section_name = _item_section_name(type('U', (), {'role': product.usage_location, 'department': None})())
            elif tx.created_by:
                section_name = _item_section_name(tx.created_by)
                dept_id = getattr(tx.created_by, 'department_id', None)

        if not dept_id and section_name in dept_by_name:
            dept_id = dept_by_name[section_name]

        officer_name = (
            (tx.created_by.get_full_name() or tx.created_by.username)
            if tx.created_by else '—'
        )
        warehouse_name = (
            si.warehouse.name
            if (si and si.warehouse_id and si.warehouse) else '—'
        )

        cat = product.category
        cat_id = cat.id if cat else None
        parent_cat_id = cat.parent_id if (cat and cat.parent_id) else None
        if cat:
            cat_name = f'{cat.parent.name} / {cat.name}' if cat.parent else cat.name
        else:
            cat_name = 'بدون دسته‌بندی'

        p_price_info = pricing_map.get(product.id) or {}
        est_cost, unit_price_tx, price_unit_tx = _estimate_consumption_cost(
            tx.quantity, unit_name, p_price_info
        )

        local_dt = timezone.localtime(tx.created_at) if timezone.is_aware(tx.created_at) else tx.created_at
        jalali_date = _format_jalali_date(local_dt)
        time_str = local_dt.strftime('%H:%M')

        deliveries.append({
            'id': tx.id,
            'product_id': product.id,
            'product_name': product.name,
            'category_id': cat_id,
            'parent_category_id': parent_cat_id,
            'category_name': cat_name,
            'warehouse_name': warehouse_name,
            'section_name': section_name,
            'department_id': dept_id,
            'requester_name': requester_name,
            'officer_name': officer_name,
            'ref_label': ref_label,
            'description': tx.description or '',
            'quantity': tx.quantity,
            'unit_name': unit_name,
            'estimated_cost': est_cost,
            'unit_price': unit_price_tx,
            'price_unit': price_unit_tx,
            'created_at': local_dt,
            'date': local_dt.date(),
            'jalali_date': jalali_date,
            'time_str': time_str,
            'jalali_datetime': f'{jalali_date} - {time_str}',
        })

    return deliveries, pricing_map


@custom_login_required
@role_required(['warehouse', 'manager', 'admin', 'ceo', 'store', 'purchasing'])
def consumption_report(request):
    """گزارش جامع مصرف کالاها — تفکیک تحویلی هر سکشن، فیلتر بازه زمانی و دسته‌بندی،
    تخمین قیمت بر اساس آخرین قیمت واردشده و مشاهده سوابق تحویلی هر کالا در تاریخ‌های مختلف.
    """
    import jdatetime

    cafe = get_cafe_from_request(request)
    today = timezone.localdate()

    # ۱) بازه زمانی (پشتیبانی از تاریخ شمسی، میلادی و انتخابگر روز/ماه/سال؛ پیش‌فرض: ۳۰ روز اخیر)
    start_date = _parse_consumption_date(request, 'start', today - timedelta(days=30))
    end_date = _parse_consumption_date(request, 'end', today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))
    span_days = (end_date - start_date).days + 1

    # ۲) پارامترهای فیلتر: سکشن (بخش)، دسته‌بندی کالا، جستجو
    selected_section = (request.GET.get('section') or '').strip()
    department_param = (request.GET.get('department') or '').strip()
    selected_department_id = int(department_param) if department_param.isdigit() else None
    if not selected_section and department_param and not department_param.isdigit():
        selected_section = department_param

    selected_category = (request.GET.get('category') or '').strip()
    selected_category_id = int(selected_category) if selected_category.isdigit() else None
    search_query = (request.GET.get('q') or request.GET.get('search') or '').strip()

    # اگر department با شناسه عددی آمده، نام بخش متناظر را هم پیدا کن
    departments = list(Department.objects.filter(is_active=True, cafe=cafe).order_by('name')) if cafe else []
    dept_id_to_name = {d.id: d.name for d in departments}
    if selected_department_id and selected_department_id in dept_id_to_name and not selected_section:
        selected_section = dept_id_to_name[selected_department_id]

    # دسته‌بندی‌های کافه (شامل زیردسته‌ها هنگام فیلتر کردن دسته والد)
    categories_qs = Category.objects.filter(is_active=True).select_related('parent').order_by('parent__name', 'name')
    if cafe:
        categories_qs = categories_qs.filter(cafe=cafe)
    categories = list(categories_qs)

    allowed_category_ids = None
    if selected_category == 'none':
        allowed_category_ids = {None}
    elif selected_category_id is not None:
        child_ids = {c.id for c in categories if c.parent_id == selected_category_id}
        allowed_category_ids = {selected_category_id} | child_ids

    # ۳) استخراج تمام تحویل‌های بازه زمانی
    all_deliveries, pricing_map = _collect_consumption_deliveries(cafe, start_dt=start_dt, end_dt=end_dt)

    # گردآوری فهرست تمام سکشن‌های موجود (بخش‌های فعال کافه + سکشن‌های دارای تحویل در بازه)
    section_options = []
    seen_sections = set()
    for d in departments:
        if d.name not in seen_sections:
            seen_sections.add(d.name)
            section_options.append({'name': d.name, 'id': d.id})
    for row in all_deliveries:
        sname = row['section_name']
        if sname and sname not in seen_sections:
            seen_sections.add(sname)
            section_options.append({'name': sname, 'id': row['department_id']})

    # ۴) اعمال فیلتر دسته‌بندی و جستجو ابتدا برای محاسبه خلاصه سکشن‌ها، سپس فیلتر سکشن
    deliveries_after_cat_search = []
    for row in all_deliveries:
        if allowed_category_ids is not None and row['category_id'] not in allowed_category_ids:
            continue
        if search_query:
            q_low = search_query.lower()
            if (q_low not in row['product_name'].lower()
                    and q_low not in (row['category_name'] or '').lower()
                    and q_low not in (row['section_name'] or '').lower()):
                continue
        deliveries_after_cat_search.append(row)

    # خلاصه تحویلی هر سکشن (مقدار تحویلی هر سکشن، تعداد تحویل، تنوع کالا و تخمین هزینه هر سکشن)
    section_buckets = {}
    for row in deliveries_after_cat_search:
        sname = row['section_name']
        b = section_buckets.setdefault(sname, {
            'name': sname,
            'department_id': row['department_id'],
            'delivery_count': 0,
            'product_ids': set(),
            'estimated_cost': Decimal('0'),
            'priced_count': 0,
            'by_unit': {},
            'top_products': {},
        })
        b['delivery_count'] += 1
        b['product_ids'].add(row['product_id'])
        u = row['unit_name']
        b['by_unit'][u] = b['by_unit'].get(u, Decimal('0')) + row['quantity']
        if row['estimated_cost'] is not None:
            b['estimated_cost'] += row['estimated_cost']
            b['priced_count'] += 1
        tp = b['top_products'].setdefault(row['product_id'], {
            'name': row['product_name'],
            'qty': Decimal('0'),
            'unit': u,
        })
        tp['qty'] += row['quantity']

    total_deliveries_all_sections = sum((b['delivery_count'] for b in section_buckets.values()), 0)
    total_cost_all_sections = sum((b['estimated_cost'] for b in section_buckets.values()), Decimal('0'))

    section_rows = []
    for sname, b in section_buckets.items():
        unit_parts = [f"{_fmt2(qty)} {u}" for u, qty in b['by_unit'].items()]
        top_items = sorted(b['top_products'].values(), key=lambda x: x['qty'], reverse=True)[:4]
        if total_cost_all_sections > 0 and b['estimated_cost'] > 0:
            share_pct = int(round((b['estimated_cost'] / total_cost_all_sections) * 100))
        elif total_deliveries_all_sections > 0:
            share_pct = int(round((b['delivery_count'] / total_deliveries_all_sections) * 100))
        else:
            share_pct = 0
        section_rows.append({
            'name': sname,
            'department_id': b['department_id'],
            'cnt': b['delivery_count'],
            'product_count': len(b['product_ids']),
            'qty': sum(b['by_unit'].values(), Decimal('0')),
            'unit_summary': ' ، '.join(unit_parts),
            'unit_breakdown': [{'unit': u, 'qty': _fmt2(q)} for u, q in b['by_unit'].items()],
            'estimated_cost': b['estimated_cost'],
            'has_cost': b['priced_count'] > 0,
            'share_pct': max(0, min(100, share_pct)),
            'top_items': [{'name': ti['name'], 'qty': _fmt2(ti['qty']), 'unit': ti['unit']} for ti in top_items],
            'is_selected': (selected_section == sname) or (
                selected_department_id is not None and b['department_id'] == selected_department_id
            ),
        })
    section_rows.sort(key=lambda r: (r['estimated_cost'], r['cnt'], r['qty']), reverse=True)

    # ۵) اعمال فیلتر سکشن روی تحویل‌ها برای جدول کالاها و شاخص‌های کل
    filtered_deliveries = []
    for row in deliveries_after_cat_search:
        if selected_section and row['section_name'] != selected_section:
            if not (selected_department_id and row['department_id'] == selected_department_id):
                continue
        elif selected_department_id and not selected_section:
            if row['department_id'] != selected_department_id:
                continue
        filtered_deliveries.append(row)

    # ۶) تجمیع مصرف به تفکیک هر کالا (به همراه تفکیک تحویل به هر سکشن، تخمین قیمت و سوابق تحویل)
    product_groups = {}
    for row in filtered_deliveries:
        pid = row['product_id']
        p_info = pricing_map.get(pid) or {}
        g = product_groups.setdefault(pid, {
            'product_id': pid,
            'product_name': row['product_name'],
            'category_id': row['category_id'],
            'category_name': row['category_name'],
            'unit_name': row['unit_name'],
            'qty': Decimal('0'),
            'cnt': 0,
            'estimated_cost': Decimal('0'),
            'has_price': bool(p_info.get('has_price')),
            'unit_price': row['unit_price'],
            'price_unit': row['price_unit'] or p_info.get('display_unit') or row['unit_name'],
            'display_price': p_info.get('display_price'),
            'display_unit': p_info.get('display_unit') or row['unit_name'],
            'price_source': p_info.get('source_label') or '',
            'sections_map': {},
            'history': [],
        })
        g['qty'] += row['quantity']
        g['cnt'] += 1
        if row['estimated_cost'] is not None:
            g['estimated_cost'] += row['estimated_cost']
        if g['unit_price'] is None and row['unit_price'] is not None:
            g['unit_price'] = row['unit_price']
            g['price_unit'] = row['price_unit']

        sec_entry = g['sections_map'].setdefault(row['section_name'], {
            'section': row['section_name'],
            'qty': Decimal('0'),
            'cnt': 0,
            'cost': Decimal('0'),
        })
        sec_entry['qty'] += row['quantity']
        sec_entry['cnt'] += 1
        if row['estimated_cost'] is not None:
            sec_entry['cost'] += row['estimated_cost']

        g['history'].append({
            'id': row['id'],
            'jalali_date': row['jalali_date'],
            'time_str': row['time_str'],
            'jalali_datetime': row['jalali_datetime'],
            'section_name': row['section_name'],
            'requester_name': row['requester_name'],
            'officer_name': row['officer_name'],
            'warehouse_name': row['warehouse_name'],
            'ref_label': row['ref_label'],
            'description': row['description'],
            'quantity': _fmt2(row['quantity']),
            'quantity_raw': str(row['quantity']),
            'unit_name': row['unit_name'],
            'unit_price': _fmt2(row['unit_price']) if row['unit_price'] is not None else None,
            'estimated_cost': int(row['estimated_cost']) if row['estimated_cost'] is not None else None,
        })

    product_rows = []
    product_histories_payload = {}
    total_estimated_cost = Decimal('0')
    priced_products_count = 0

    for pid, g in product_groups.items():
        if g['has_price']:
            total_estimated_cost += g['estimated_cost']
            priced_products_count += 1

        sec_list = sorted(
            [
                {
                    'section': s['section'],
                    'qty': s['qty'],
                    'qty_fmt': _fmt2(s['qty']),
                    'cnt': s['cnt'],
                    'cost': int(s['cost']) if g['has_price'] else None,
                    'share_pct': int(round((s['qty'] / g['qty']) * 100)) if g['qty'] > 0 else 0,
                }
                for s in g['sections_map'].values()
            ],
            key=lambda x: x['qty'],
            reverse=True,
        )
        effective_unit_price = g['unit_price'] if g['unit_price'] is not None else g['display_price']
        effective_price_unit = g['price_unit'] if g['unit_price'] is not None else g['display_unit']

        row_dict = {
            'product_id': pid,
            'product_name': g['product_name'],
            'category_id': g['category_id'],
            'category_name': g['category_name'],
            'unit_name': g['unit_name'],
            'qty': g['qty'],
            'qty_fmt': _fmt2(g['qty']),
            'cnt': g['cnt'],
            'has_price': g['has_price'],
            'unit_price': effective_unit_price,
            'unit_price_fmt': _fmt2(effective_unit_price) if effective_unit_price is not None else None,
            'price_unit': effective_price_unit,
            'price_source': g['price_source'],
            'estimated_cost': g['estimated_cost'] if g['has_price'] else None,
            'section_breakdown': sec_list,
            'last_delivery_jalali': g['history'][0]['jalali_datetime'] if g['history'] else '—',
            # کلیدهای سازگاری با قالب‌های قدیمی
            'stock_item__product_id': pid,
            'stock_item__product__name': g['product_name'],
            'stock_item__product__unit__name': g['unit_name'],
        }
        product_rows.append(row_dict)

        product_histories_payload[str(pid)] = {
            'product_id': pid,
            'product_name': g['product_name'],
            'category_name': g['category_name'],
            'unit_name': g['unit_name'],
            'total_qty': _fmt2(g['qty']),
            'delivery_count': g['cnt'],
            'has_price': g['has_price'],
            'unit_price': _fmt2(effective_unit_price) if effective_unit_price is not None else None,
            'price_unit': effective_price_unit,
            'price_source': g['price_source'],
            'estimated_cost': int(g['estimated_cost']) if g['has_price'] else None,
            'sections': sec_list,
            'records': g['history'],
        }

    # مرتب‌سازی کالاها: بیشترین هزینه تخمینی و سپس بیشترین مقدار مصرف
    product_rows.sort(
        key=lambda r: (
            r['estimated_cost'] if r['estimated_cost'] is not None else Decimal('-1'),
            r['qty'],
        ),
        reverse=True,
    )

    total_qty = sum((r['qty'] for r in product_rows), Decimal('0'))
    txn_count = len(filtered_deliveries)
    product_count = len(product_rows)
    active_sections_count = len({r['section_name'] for r in filtered_deliveries})

    # بازه‌های زمانی آماده (چیپ‌های انتخاب سریع)
    sj = jdatetime.date.fromgregorian(date=start_date)
    ej = jdatetime.date.fromgregorian(date=end_date)
    today_j = jdatetime.date.fromgregorian(date=today)
    first_of_jmonth = jdatetime.date(today_j.year, today_j.month, 1).togregorian()

    presets = [
        {
            'label': 'امروز',
            'start': today.isoformat(),
            'end': today.isoformat(),
            'active': start_date == today and end_date == today,
        },
        {
            'label': '۷ روز اخیر',
            'start': (today - timedelta(days=7)).isoformat(),
            'end': today.isoformat(),
            'active': start_date == (today - timedelta(days=7)) and end_date == today,
        },
        {
            'label': 'ماه جاری',
            'start': first_of_jmonth.isoformat(),
            'end': today.isoformat(),
            'active': start_date == first_of_jmonth and end_date == today,
        },
        {
            'label': '۳۰ روز اخیر',
            'start': (today - timedelta(days=30)).isoformat(),
            'end': today.isoformat(),
            'active': start_date == (today - timedelta(days=30)) and end_date == today,
        },
        {
            'label': '۹۰ روز اخیر',
            'start': (today - timedelta(days=90)).isoformat(),
            'end': today.isoformat(),
            'active': start_date == (today - timedelta(days=90)) and end_date == today,
        },
    ]

    start_jalali = f'{sj.year}/{sj.month:02d}/{sj.day:02d}'
    end_jalali = f'{ej.year}/{ej.month:02d}/{ej.day:02d}'

    return render(request, 'reports/consumption_report.html', {
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'start_jalali': start_jalali,
        'end_jalali': end_jalali,
        'start_year': sj.year, 'start_month': sj.month, 'start_day': sj.day,
        'end_year': ej.year, 'end_month': ej.month, 'end_day': ej.day,
        'selected_department': selected_department_id or '',
        'selected_section': selected_section,
        'selected_category': selected_category,
        'search_query': search_query,
        'has_active_filters': bool(selected_section or selected_department_id or selected_category or search_query),
        'total_qty': total_qty,
        'txn_count': txn_count,
        'product_count': product_count,
        'priced_products_count': priced_products_count,
        'unpriced_products_count': product_count - priced_products_count,
        'total_estimated_cost': total_estimated_cost,
        'section_count': active_sections_count,
        'section_total': sum((r['qty'] for r in section_rows), Decimal('0')),
        'section_rows': section_rows,
        'section_options': section_options,
        'product_rows': product_rows,
        'product_histories': product_histories_payload,
        'departments': departments,
        'categories': categories,
        'presets': presets,
        'span_days': span_days,
    })


@custom_login_required
@role_required(['warehouse', 'manager', 'admin', 'ceo', 'store', 'purchasing'])
def consumption_product_history_api(request, product_id):
    """API سوابق تحویلی یک کالا در تاریخ‌های مختلف (برای مودال جزئیات تحویل کالا).

    امکان دریافت سوابق در بازه انتخابی یا کل تاریخچه تحویل آن کالا در کافه فعلی (`scope=all`).
    """
    cafe = get_cafe_from_request(request)
    product = get_scoped_object_or_404(Product, request, id=product_id)

    scope = (request.GET.get('scope') or 'range').strip()
    today = timezone.localdate()
    start_dt = None
    end_dt = None
    start_jalali = ''
    end_jalali = ''

    if scope != 'all':
        start_date = _parse_consumption_date(request, 'start', today - timedelta(days=30))
        end_date = _parse_consumption_date(request, 'end', today)
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))
        start_jalali = _format_jalali_date(start_date)
        end_jalali = _format_jalali_date(end_date)

    section_filter = (request.GET.get('section') or '').strip()
    deliveries, pricing_map = _collect_consumption_deliveries(
        cafe, start_dt=start_dt, end_dt=end_dt, product_id=product.id
    )
    if section_filter:
        deliveries = [d for d in deliveries if d['section_name'] == section_filter]

    if not pricing_map:
        pricing_map = _build_product_pricing_map([product], cafe=cafe)
    p_info = pricing_map.get(product.id) or {}

    total_qty = Decimal('0')
    total_cost = Decimal('0')
    sections_map = {}
    records = []

    p_unit = (product.unit.name if product.unit_id and product.unit else product.smallest_unit or 'عدد').strip()

    for row in deliveries:
        total_qty += row['quantity']
        if row['estimated_cost'] is not None:
            total_cost += row['estimated_cost']
        p_unit = row['unit_name'] or p_unit

        sec_entry = sections_map.setdefault(row['section_name'], {
            'section': row['section_name'],
            'qty': Decimal('0'),
            'cnt': 0,
            'cost': Decimal('0'),
        })
        sec_entry['qty'] += row['quantity']
        sec_entry['cnt'] += 1
        if row['estimated_cost'] is not None:
            sec_entry['cost'] += row['estimated_cost']

        records.append({
            'id': row['id'],
            'jalali_date': row['jalali_date'],
            'time_str': row['time_str'],
            'jalali_datetime': row['jalali_datetime'],
            'section_name': row['section_name'],
            'requester_name': row['requester_name'],
            'officer_name': row['officer_name'],
            'warehouse_name': row['warehouse_name'],
            'ref_label': row['ref_label'],
            'description': row['description'],
            'quantity': _fmt2(row['quantity']),
            'unit_name': row['unit_name'],
            'unit_price': _fmt2(row['unit_price']) if row['unit_price'] is not None else None,
            'estimated_cost': int(row['estimated_cost']) if row['estimated_cost'] is not None else None,
        })

    sections_list = sorted(
        [
            {
                'section': s['section'],
                'qty': _fmt2(s['qty']),
                'cnt': s['cnt'],
                'cost': int(s['cost']) if p_info.get('has_price') else None,
                'share_pct': int(round((s['qty'] / total_qty) * 100)) if total_qty > 0 else 0,
            }
            for s in sections_map.values()
        ],
        key=lambda x: x['share_pct'],
        reverse=True,
    )

    _, unit_price_in_p_unit, price_unit_label = _estimate_consumption_cost(
        Decimal('1'), p_unit, p_info
    )
    effective_unit_price = unit_price_in_p_unit if unit_price_in_p_unit is not None else p_info.get('display_price')
    effective_price_unit = price_unit_label or p_info.get('display_unit') or p_unit

    return JsonResponse({
        'success': True,
        'product_id': product.id,
        'product_name': product.name,
        'category_name': product.category.name if product.category else 'بدون دسته‌بندی',
        'unit_name': p_unit,
        'scope': scope,
        'start_jalali': start_jalali,
        'end_jalali': end_jalali,
        'total_qty': _fmt2(total_qty),
        'delivery_count': len(records),
        'has_price': bool(p_info.get('has_price')),
        'unit_price': _fmt2(effective_unit_price) if effective_unit_price is not None else None,
        'price_unit': effective_price_unit,
        'price_source': p_info.get('source_label') or '',
        'estimated_cost': int(total_cost) if p_info.get('has_price') else None,
        'sections': sections_list,
        'records': records,
    }, json_dumps_params={'ensure_ascii': False})



@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "ceo"])
def analytics_dashboard(request):
    """Redirect to line chart analytics as default"""
    cafe_code = request.cafe.code
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
    
    # دریافت فیلترها از URL
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    category_id = request.GET.get('category', '')
    subcategory_id = request.GET.get('subcategory', '')
    supplier_id = request.GET.get('supplier', '')
    
    # دسته‌بند‌های اصلی (بدون والد) - بدون فیلتر cafe برای نمایش همه
    main_categories = Category.objects.filter(
        parent__isnull=True,
        is_active=True
    ).order_by('name')
    
    # زیردسته‌ها (اگر دسته اصلی انتخاب شده) - بدون فیلتر cafe
    subcategories = []
    if category_id:
        subcategories = Category.objects.filter(
            parent_id=category_id,
            is_active=True
        ).order_by('name')
    
    # فروشندگان فیلتر شده بر اساس زیردسته
    if subcategory_id:
        # فقط فروشندگانی که محصولات این زیردسته را فروخته‌اند
        invoice_items_qs = InvoiceItem.objects.filter(
            product__category_id=subcategory_id,
            invoice__status__in=['sent', 'approved', 'partially_paid', 'paid', 'overdue'],
            invoice__supplier__isnull=False
        )
        
        # اعمال فیلتر تاریخ
        if start_date:
            invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__gte=start_date)
        if end_date:
            invoice_items_qs = invoice_items_qs.filter(invoice__invoice_date__lte=end_date)
        
        # دریافت فروشندگان منحصر به فرد
        supplier_ids = invoice_items_qs.values_list('invoice__supplier_id', flat=True).distinct()
        suppliers = Supplier.objects.filter(
            id__in=supplier_ids,
            status='active'
        ).order_by('name')
    else:
        #اگر زیردسته انتخاب نشده، همه فروشندگان فعال را نمایش بده - بدون فیلتر cafe
        suppliers = Supplier.objects.filter(status='active').order_by('name')
    
    # محصولات و داده‌های فیلتر شده
    products_data = []
    summary = {
        'total_items': 0,
        'total_amount': Decimal('0'),
        'unique_products': 0,
        'unique_suppliers': 0
    }
    
    if subcategory_id:  # فقط زمانی که زیردسته انتخاب شده
        # Query اولیه
        invoice_items = InvoiceItem.objects.filter(
            product__category_id=subcategory_id,
            invoice__status__in=['sent', 'approved', 'partially_paid', 'paid', 'overdue']
        ).select_related('product', 'invoice', 'invoice__supplier')
        
        # اعمال فیلترهای تاریخ
        if start_date:
            invoice_items = invoice_items.filter(invoice__invoice_date__gte=start_date)
        if end_date:
            invoice_items = invoice_items.filter(invoice__invoice_date__lte=end_date)
        
        # اعمال فیلتر فروشنده
        if supplier_id:
            invoice_items = invoice_items.filter(invoice__supplier_id=supplier_id)
        
        # گروه‌بندی بر اساس محصول و فروشنده
        from django.db.models import Count, F, ExpressionWrapper
        
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
        
        # محاسبه میانگین وزنی برای هر محصول
        for product in products_data:
            if product['total_quantity'] and product['total_quantity'] > 0:
                product['weighted_avg_price'] = product['weighted_price_sum'] / product['total_quantity']
            else:
                product['weighted_avg_price'] = 0
        
        # محاسبه خلاصه
        summary['total_items'] = invoice_items.count()
        summary['total_amount'] = invoice_items.aggregate(
            total=Sum('line_total')
        )['total'] or Decimal('0')
        summary['unique_products'] = len(products_data)
        summary['unique_suppliers'] = invoice_items.values('invoice__supplier').distinct().count()
        
        # محاسبه میانگین کل (average of all unit prices)
        avg_result = invoice_items.aggregate(avg_price=Sum('unit_price'))
        count = invoice_items.count()
        if count > 0 and avg_result['avg_price']:
            summary['avg_price'] = avg_result['avg_price'] / count
        else:
            summary['avg_price'] = Decimal('0')
        
        # محاسبه میانگین وزنی (weighted average: sum(quantity * unit_price) / sum(quantity))
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
        # مقادیر فیلتر برای نگه داشتن در فرم
        'start_date': start_date,
        'end_date': end_date,
        'selected_category': category_id,
        'selected_subcategory': subcategory_id,
        'selected_supplier': supplier_id,
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


@custom_login_required
def test_filter_page(request):
    """صفحه تست فیلترها"""
    return render(request, "test_filter.html")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@custom_login_required
def notifications_list(request, cafe_code=None):
    notifications = Notification.objects.filter(recipient=request.user)
    return render(request, "notifications_list.html", {"notifications": notifications})


@custom_login_required
def notification_detail(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    return render(request, "notification_detail.html", {"notification": notification})


@custom_login_required
def mark_notification_read(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.is_read = True
    notification.save()
    return get_cafe_redirect_url("notifications_list", request)


@custom_login_required
def delete_notification(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.delete()
    return get_cafe_redirect_url("notifications_list", request)


@custom_login_required
def get_unread_notifications_count(request):
    count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({"count": count})


@custom_login_required
def get_recent_notifications(request):
    notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:5]
    data = [
        {
            "title": n.title,
            "message": n.message,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
        }
        for n in notifications
    ]
    return JsonResponse({"notifications": data})


# ---------------------------------------------------------------------------
# Error handling utilities (fallback)
# ---------------------------------------------------------------------------


def handler404(request, exception=None):
    raise Http404()


@custom_login_required
def search_supplier_products(request):
    """Search for products used in previous invoices for a specific supplier."""
    supplier_id = request.GET.get('supplier_id')
    query = request.GET.get('q', '').strip()
    
    if not supplier_id:
        return JsonResponse({'results': []})
        
    # Search in InvoiceItems linked to Invoices from this supplier
    items = InvoiceItem.objects.filter(
        invoice__supplier_id=supplier_id,
        description__icontains=query
    ).values_list('description', flat=True).distinct()[:10]
    
    return JsonResponse({'results': list(items)})


@custom_login_required
def search_products_autocomplete(request):
    """Autocomplete API for products with their categories.
    Filters products by the requesting user's department (usage_location)."""
    query = request.GET.get('q', '').strip()

    if not query or len(query) < 2:
        return JsonResponse({'results': []})

    cafe = get_cafe_from_request(request)
    products = Product.objects.filter(
        models.Q(name__icontains=query) | models.Q(category__name__icontains=query),
        is_active=True,
        # کالاهای ناقص (تازه ساخته‌شده از فرم درخواست) تا تکمیل اطلاعات در مدیریت محصولات،
        # در پیشنهادهای درخواست‌های بعدی نمایش داده نمی‌شوند
        is_complete=True,
    ).select_related('category', 'category__parent', 'unit')
    if cafe:
        products = products.filter(cafe=cafe)

    # Filter by user's department (usage_location)
    user_role = request.user.role
    # Roles that see ALL products (managers, purchasing, warehouse, etc.)
    unrestricted_roles = PRODUCT_MANAGER_ROLES
    if not bypasses_recipe_gate(request.user):
        # درخواست‌کننده کالای غذاییِ بدون رسپی را نمی‌بیند — کالاهای غیرغذایی
        # (شوینده، مصرفی، ...) و کالاهای داخل رسپی دست‌نخورده می‌مانند.
        products = filter_requestable_products(products, cafe=cafe)
    if user_role not in unrestricted_roles:
        if user_role == 'requester':
            # For requester users, filter by their assigned department name
            dep_name = request.user.department.name if request.user.department else None
            if dep_name:
                # Show products matching the department name OR general products
                # Also handle backward compatibility with old English keys
                old_key_map = {
                    'آشپزخانه': 'kitchen', 'بار': 'bar', 'کانتر': 'counter',
                    'صندوق': 'cashier', 'تاسیسات': 'facilities',
                }
                old_key = old_key_map.get(dep_name, None)
                q_filter = models.Q(usage_location='general') | models.Q(usage_location=dep_name)
                if old_key:
                    q_filter = q_filter | models.Q(usage_location=old_key)
                products = products.filter(q_filter)
            else:
                # No department - show general only
                products = products.filter(usage_location='general')
        else:
            # Old-style roles (backward compatibility) - filter by role value
            products = products.filter(
                models.Q(usage_location=user_role) | models.Q(usage_location='general')
            )

    products = products[:15]

    results = []
    for product in products:
        category_display = ''
        if product.category:
            if product.category.parent:
                category_display = f"{product.category.parent.name} > {product.category.name}"
            else:
                category_display = product.category.name

        results.append({
            'id': product.id,
            'name': product.name,
            'category': category_display,
            'category_id': product.category_id,
            'label': product.name,
            'unit': product.unit.name if product.unit else '',
        })

    return JsonResponse({'results': results})


@custom_login_required
def category_products_api(request: HttpRequest, category_id: int = None) -> JsonResponse:
    """API محصولات فعالِ یک دسته‌بندی برای فرم درخواست خرید.

    با انتخاب دسته‌بندی، محصولات همان دسته (کامل شده) در همان صفحه قابل انتخاب می‌شوند
    و جستجوی کاربر روی همین محصولات اعمال می‌شود.
    """
    if category_id is None:
        category_id = request.GET.get('category_id', '')
    query = request.GET.get('q', '').strip()

    cafe = get_cafe_from_request(request)
    products = Product.objects.filter(is_active=True, is_complete=True).select_related(
        'category', 'category__parent', 'unit')
    if cafe:
        products = products.filter(cafe=cafe)

    if category_id and str(category_id).isdigit():
        # محصولات دسته انتخاب‌شده + زیردسته‌های آن
        cat = Category.objects.filter(id=int(category_id), is_active=True).first()
        if not cat:
            return JsonResponse({'results': []})
        cat_ids = [cat.id] + list(cat.subcategories.filter(is_active=True).values_list('id', flat=True))
        products = products.filter(category_id__in=cat_ids)
    else:
        return JsonResponse({'results': []})

    if query:
        products = products.filter(name__icontains=query)

    user_role = request.user.role
    unrestricted_roles = PRODUCT_MANAGER_ROLES
    if not bypasses_recipe_gate(request.user):
        # همان قاعدهٔ autocomplete: کالای غذاییِ بدون رسپی برای درخواست‌کننده
        # حتی با انتخاب دسته هم نمایش داده نمی‌شود.
        products = filter_requestable_products(products, cafe=cafe)
    if user_role not in unrestricted_roles:
        if user_role == 'requester':
            dep_name = request.user.department.name if request.user.department else None
            if dep_name:
                old_key_map = {
                    'آشپزخانه': 'kitchen', 'بار': 'bar', 'کانتر': 'counter',
                    'صندوق': 'cashier', 'تاسیسات': 'facilities',
                }
                old_key = old_key_map.get(dep_name, None)
                q_filter = models.Q(usage_location='general') | models.Q(usage_location=dep_name)
                if old_key:
                    q_filter = q_filter | models.Q(usage_location=old_key)
                products = products.filter(q_filter)
            else:
                products = products.filter(usage_location='general')
        else:
            products = products.filter(
                models.Q(usage_location=user_role) | models.Q(usage_location='general')
            )

    products = products.order_by('name')[:50]

    results = []
    for product in products:
        category_display = ''
        if product.category:
            if product.category.parent:
                category_display = f"{product.category.parent.name} > {product.category.name}"
            else:
                category_display = product.category.name
        results.append({
            'id': product.id,
            'name': product.name,
            'category': category_display,
            'category_id': product.category_id,
            'label': product.name,
            'unit': product.unit.name if product.unit else '',
        })

    return JsonResponse({'results': results})


@custom_login_required
def check_product_availability(request: HttpRequest) -> JsonResponse:
    """بررسی موجودی انبار برای محصول انتخاب‌شده هنگام ثبت درخواست کالا."""
    product_id = request.GET.get('product_id')
    qty = _parse_decimal(request.GET.get('qty', '') or 0)

    product = Product.objects.filter(id=product_id).first()
    if not product:
        return JsonResponse({'error': 'محصول یافت نشد'}, status=404)

    cafe = get_cafe_from_request(request)
    stock_qs = StockItem.objects.filter(product=product, quantity__gt=0).select_related(
        'warehouse', 'unit')
    if cafe:
        stock_qs = stock_qs.filter(warehouse__cafe=cafe)
    stock_rows = list(stock_qs)

    total_qty = sum((s.quantity for s in stock_rows), Decimal('0'))
    unit_label = (
        rows_unit
        if (rows_unit := (stock_rows[0].unit.name if stock_rows and stock_rows[0].unit else ''))
        else (product.unit.name if product.unit else '')
    )
    warehouses = sorted({s.warehouse.name for s in stock_rows if s.warehouse})
    available = qty is not None and qty > 0 and total_qty >= qty

    return JsonResponse({
        'product_id': product.id,
        'qty': _fmt2(qty),
        'available': available,
        'stock_trade': _fmt2(total_qty),
        'unit_label': unit_label,
        'warehouses': warehouses,
    }, json_dumps_params={'ensure_ascii': False})


@custom_login_required
def search_categories_autocomplete(request):
    """Autocomplete API for categories with subcategories"""
    query = request.GET.get('q', '').strip()
    parent_id = request.GET.get('parent_id')
    
    if not query or len(query) < 2:
        return JsonResponse({'results': []})
    
    cafe = get_cafe_from_request(request)
    categories = Category.objects.filter(
        name__icontains=query,
        is_active=True
    ).select_related('parent')
    if cafe:
        categories = categories.filter(cafe=cafe)
    
    if parent_id:
        categories = categories.filter(parent_id=parent_id)
    
    categories = categories[:15]
    
    results = []
    for cat in categories:
        display_name = cat.full_path if cat.parent else cat.name
        results.append({
            'id': cat.id,
            'name': cat.name,
            'parent_id': cat.parent_id,
            'full_path': display_name,
            'label': display_name
        })
    
    return JsonResponse({'results': results})


# ---------------------------------------------------------------------------
# Inventory, Stock & Vouchers (extracted to views_inventory.py)
# ---------------------------------------------------------------------------
from .views_inventory import (
    _InsufficientStock,
    _set_stock_quantity,
    stock_list,
    stock_transactions,
    warehouse_management,
    warehouse_create,
    warehouse_edit,
    warehouse_toggle_status,
    stock_by_category,
    stock_category_items,
    _parse_jalali_date,
    _format_jalali_date,
    _jalali_input_value,
    _voucher_datetime_from_input,
    _voucher_form_options,
    _parse_voucher_lines,
    _voucher_prefill_items,
    _render_voucher_form,
    voucher_list,
    voucher_create,
    voucher_detail,
    voucher_edit,
    voucher_cancel,
    voucher_approve,
    voucher_reverse,
    warehouse_inventory_check,
    warehouse_inventory_submit,
    api_product_stock_history,
    stock_movements,
)


def management_dashboard(request):
    """
    داشبورد مدیریت با نمودار دایره‌ای هزینه‌ها و تحلیل رشد قیمت
    فقط برای مدیران و مدیرعامل
    """
    cafe = get_cafe_from_request(request)
    
    context = {
        'cafe_code': cafe.code if cafe else None,
        'active_page': 'management_dashboard',
    }

    return render(request, 'management_dashboard.html', context)


@custom_login_required
@role_required(["manager", "ceo"])
def manager_pending_requests(request):
    """
    لیست درخواست‌های در انتظار تایید مدیر
    """
    cafe = get_cafe_from_request(request)
    
    requests_qs = PurchaseRequest.objects.filter(
        status='pending_manager_approval'
    ).select_related('user', 'cafe').prefetch_related('items').order_by('-created_at')
    
    if cafe:
        requests_qs = requests_qs.filter(cafe=cafe)
    
    # فیلتر بر اساس دپارتمان
    department_filter = request.GET.get('department', '')
    if department_filter:
        requests_qs = requests_qs.filter(user__role=department_filter)
    
    context = {
        'requests': requests_qs,
        'departments': User.ROLE_CHOICES,
        'selected_department': department_filter,
        'pending_count': requests_qs.count(),
    }
    
    return render(request, 'manager_pending_requests.html', context)


def _manager_purchase_request_or_404(request, request_id, lock=False):
    """درخواست را با کافهٔ فعلی محدود می‌کند؛ بدون کافه فقط سوپرادمین.

    مدیر/مدیرعاملِ بدون کافه (cafe=None) می‌تواند از کافهٔ خودِ رکورد استفاده
    کند؛ سایر کاربران بدون کافه → 404. هیچ fallback بی‌scope برای کاربر عادی
    وجود ندارد.
    """
    cafe = get_cafe_from_request(request)
    if cafe is not None:
        if lock:
            return get_scoped_locked_object_or_404(
                PurchaseRequest, request, id=request_id)
        return get_scoped_object_or_404(
            PurchaseRequest, request, id=request_id)

    user = request.user
    if user.is_superuser or (
            user.cafe_id is None and user.role in ('manager', 'ceo')):
        qs = PurchaseRequest.objects.all()
        if lock:
            qs = qs.select_for_update()
        return get_object_or_404(qs, id=request_id)

    raise Http404('کافهٔ فعال یافت نشد')


@custom_login_required
@role_required(["manager", "ceo"])
def manager_approve_request(request, request_id):
    """
    تایید درخواست توسط مدیر
    """
    purchase_request = _manager_purchase_request_or_404(request, request_id)
    cafe = get_cafe_from_request(request)
    default_code = (
        cafe.code if cafe
        else (purchase_request.cafe.code if purchase_request.cafe else 'local')
    )

    if purchase_request.status != 'pending_manager_approval':
        messages.warning(request, 'این درخواست قبلاً پردازش شده است')
        return redirect('manager_pending_requests', cafe_code=default_code)

    if request.method == 'POST':
        with transaction.atomic():
            purchase_request = _manager_purchase_request_or_404(
                request, request_id, lock=True)
            if purchase_request.status != 'pending_manager_approval':
                messages.warning(request, 'این درخواست قبلاً پردازش شده است')
                return redirect('manager_pending_requests', cafe_code=default_code)

            # تایید درخواست — برای درخواست خریدِ مسئول انبار، تایید یعنی راه بازشدن
            # به سمت واحد خرید برای «ثبت سفارش خرید» (درخواست با source=warehouse_purchase
            # و status=pending در فهرست «درخواست‌های تاییدشده» واحد خرید ظاهر می‌شود)
            purchase_request.status = 'pending'
            purchase_request.manager_confirmed = True
            purchase_request.manager_confirmed_by = request.user
            purchase_request.manager_confirmed_at = timezone.now()
            purchase_request.save(update_fields=[
                'status', 'manager_confirmed',
                'manager_confirmed_by', 'manager_confirmed_at'])

            ActivityLog.objects.create(
                user=request.user,
                request=purchase_request,
                action='approve_request',
                description=f'تایید درخواست {request_number_for(purchase_request.id)} توسط {request.user.get_full_name() or request.user.username}'
            )

            if purchase_request.source == 'warehouse_purchase':
                messages.success(request, f'درخواست خرید {request_number_for(purchase_request.id)} تایید شد و برای ثبت سفارش خرید در دسترس واحد خرید قرار گرفت')
            else:
                messages.success(request, f'درخواست کالا {request_number_for(purchase_request.id)} با موفقیت تایید شد و به واحد خرید ارسال شد')

        # برگشت به صفحه قبلی فقط اگر مقصد محلی و مجاز باشد
        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()},
                require_https=request.is_secure()):
            return redirect(next_url)
        return redirect('manager_pending_requests', cafe_code=default_code)

    return redirect('manager_pending_requests', cafe_code=default_code)


@custom_login_required
@role_required(["manager", "ceo"])
def manager_reject_request(request, request_id):
    """
    رد درخواست توسط مدیر
    """
    purchase_request = _manager_purchase_request_or_404(request, request_id)
    cafe = get_cafe_from_request(request)
    default_code = (
        cafe.code if cafe
        else (purchase_request.cafe.code if purchase_request.cafe else 'local')
    )

    if purchase_request.status != 'pending_manager_approval':
        messages.warning(request, 'این درخواست قبلاً پردازش شده است')
        return redirect('manager_pending_requests', cafe_code=default_code)

    if request.method == 'POST':
        rejection_reason = request.POST.get('rejection_reason', '')
        with transaction.atomic():
            purchase_request = _manager_purchase_request_or_404(
                request, request_id, lock=True)
            if purchase_request.status != 'pending_manager_approval':
                messages.warning(request, 'این درخواست قبلاً پردازش شده است')
                return redirect('manager_pending_requests', cafe_code=default_code)

            purchase_request.status = 'rejected'
            purchase_request.rejection_reason = rejection_reason
            purchase_request.manager_confirmed = False
            purchase_request.manager_confirmed_by = request.user
            purchase_request.manager_confirmed_at = timezone.now()
            purchase_request.save(update_fields=[
                'status', 'rejection_reason', 'manager_confirmed',
                'manager_confirmed_by', 'manager_confirmed_at'])

            ActivityLog.objects.create(
                user=request.user,
                request=purchase_request,
                action='reject_request',
                description=f'رد درخواست کالا {request_number_for(purchase_request.id)} توسط {request.user.get_full_name() or request.user.username}. دلیل: {rejection_reason}'
            )

            messages.success(request, f'درخواست کالا {request_number_for(purchase_request.id)} رد شد')

        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()},
                require_https=request.is_secure()):
            return redirect(next_url)
        return redirect('manager_pending_requests', cafe_code=default_code)

    return redirect('manager_pending_requests', cafe_code=default_code)


# ---------------------------------------------------------------------------  
# CEO Dashboard
# ---------------------------------------------------------------------------


# ═══════════════════════════════════════════════════════════════════════════
# جریان جدید: تخصیص اقلام درخواست کالا به مسئول خرید / مسئول انبار
#
#  - کالاهایی که در انبار موجودی دارند → مستقیم برای مسئول انبار (بدون تایید مدیر)
#  - کالاهای ناموجود → برای مدیرعامل → مدیرعامل به مسئول خرید/انبار مشخص ارسال می‌کند
#  - مسئول خرید فقط اقلام ارسالی مدیرعامل را می‌بیند (قابل خرید / بازگشت به مدیرعامل)
# ═══════════════════════════════════════════════════════════════════════════

ASSIGNMENT_ACTIVE_STATUSES = ('pending', 'purchased', 'fulfilled')
ASSIGNMENT_RETURNED_STATUSES = ('returned', 'not_in_stock')
ASSIGNMENT_TERMINAL_STATUSES = ('purchased', 'fulfilled')


def _item_section_name(user):
    """نام بخشِ قلم درخواست بر اساس کاربر درخواست‌دهنده"""
    if user and user.role == 'requester' and user.department:
        return user.department.name
    dept_names = {
        'kitchen': 'آشپزخانه', 'bar': 'بار', 'counter': 'کانتر',
        'cashier': 'صندوق', 'facilities': 'تاسیسات', 'store': 'انبار',
        'purchasing': 'خرید', 'warehouse': 'انبارداری', 'manager': 'مدیر',
        'admin': 'ادمین', 'ceo': 'مدیرعامل', 'accounting': 'حسابداری',
        'finance_manager': 'مدیر مالی',
    }
    return dept_names.get(getattr(user, 'role', ''), getattr(user, 'role', '—'))


def _product_key(item, include_description=False):
    """کلید ادغام کالا: شناسه محصول اگر موجود باشد وگرنه نام کوچک‌شده.

    اگر include_description=True باشد، توضیحات قلم هم جزئی از کلید می‌شود تا
    اقلام همنام با توضیحات متفاوت به‌عنوان گزینه‌های جدا نمایش داده شوند.
    """
    if item.product_id:
        key = f"product_{item.product_id}"
    else:
        key = (item.name or '').strip().lower()
    if include_description:
        desc = (getattr(item, 'description', '') or '').strip()
        if desc:
            key += f"|desc:{desc}"
    return key


def _sync_request_status(request_obj, keep_queue=False):
    """همگام‌سازی وضعیت درخواست بر اساس وضعیت تخصیص‌های اقلامش

    keep_queue=True (مسیرهای ویرایش/حذف اقلام): درخواستی که در صف مدیرعامل است با
    ویرایش اقلامش از «داشبورد مدیرعامل» ناپدید نمی‌شود؛ تنها خودِ مدیرعامل با
    تایید/رد کردن آن را از صف خارج می‌کند. مسیرهای گردش‌کار عادی (مثل ارسال مستقیم
    فرم درخواست کالا به انبار) پیش‌فرض False را می‌فرستند و مثل قبل وضعیت را
    همگام می‌کنند.
    """
    if keep_queue and request_obj.status == 'pending_manager_approval':
        return
    items = list(request_obj.items.all())
    if not items:
        return
    assignments = OfficerAssignment.objects.filter(item__request=request_obj)
    by_item = {a.item_id: a.status for a in assignments}
    statuses = [by_item.get(i.id) for i in items]
    real = [s for s in statuses if s]
    if real and all(s in ASSIGNMENT_TERMINAL_STATUSES for s in real) and len(real) == len(items):
        request_obj.status = 'purchased'
    elif real and any(s in ASSIGNMENT_TERMINAL_STATUSES for s in real):
        request_obj.status = 'partial'
    elif real:
        request_obj.status = 'pending'
    else:
        request_obj.status = 'pending_manager_approval'
    request_obj.save(update_fields=['status'])


def _item_in_stock(item, cafe, stocked_product_ids=None):
    """آیا این کالا در انبارهای کافه موجودی دارد؟"""
    if not item.product_id:
        return False
    if stocked_product_ids is not None:
        return item.product_id in stocked_product_ids
    qs = StockItem.objects.filter(product_id=item.product_id, quantity__gt=0)
    if cafe:
        qs = qs.filter(warehouse__cafe=cafe)
    return qs.exists()


def _officer_users(cafe, target_role):
    """کاربران دارای نقش خرید/انبار (نقش اصلی یا دوم) در این کافه"""
    from django.db.models import Q
    qs = User.objects.filter(is_active=True).filter(
        Q(role=target_role) | Q(secondary_role=target_role)
    ).order_by('username')
    if cafe:
        qs = qs.filter(cafe=cafe)
    return qs


def _officer_can_role(user, target_role):
    """آیا این کاربر نقش خرید/انبار را دارد (اصلی یا دوم)؟"""
    return user.role == target_role or user.secondary_role == target_role


def _officer_role_label(user):
    """برچسب نمایشی سه‌گانه: مسئول خرید / مسئول انبار / مسئول خرید و انبار"""
    has_p = user.role == 'purchasing' or user.secondary_role == 'purchasing'
    has_w = user.role == 'warehouse' or user.secondary_role == 'warehouse'
    if has_p and has_w:
        return 'مسئول خرید و انبار'
    if has_p:
        return 'مسئول خرید'
    if has_w:
        return 'مسئول انبار'
    return '—'


def _visible_assignments_q(user, target_role):
    """تخصیص‌های در انتظار: مخصوص این کاربر یا مشترک (assigned_to=None)"""
    return OfficerAssignment.objects.filter(
        target_role=target_role,
        status='pending',
    ).filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))


def _assign_item(item, target_role, officer, assigned_by, status='pending', notes=''):
    """ایجاد تخصیص جدید برای یک قلم"""
    return OfficerAssignment.objects.create(
        item=item,
        target_role=target_role,
        assigned_to=officer,
        assigned_by=assigned_by,
        status=status,
        notes=notes,
    )


def _notify_officer(officer, item, target_role, cafe):
    """نوتیفیکیشن برای مسئول خرید/انبار"""
    role_label = 'خرید' if target_role == 'purchasing' else 'انبار'
    if officer:
        Notification.objects.create(
            recipient=officer,
            title=f'قلم جدید در لیست {role_label}',
            message=f'کالای «{item.name}» برای شما در لیست مسئول {role_label} ارسال شد.',
            notification_type='info',
            cafe=cafe,
        )


def _route_request_to_warehouse(purchase_request, items, assigned_by, notes='ارسال مستقیم از فرم درخواست کالا'):
    """ارسال مستقیم اقلام یک درخواست به مسئول انبار.

    هر قلم تخصیص انبار مشترک (assigned_to=None) در انتظار می‌گیرد تا در صندوق
    مشترک مسئولان انبار کافه ظاهر شود، برای هر مسئول اعلان ارسال می‌شود و
    وضعیت درخواست همگام می‌گردد.
    """
    cafe = purchase_request.cafe
    items = list(items)
    if not items:
        return
    with transaction.atomic():
        for item in items:
            _assign_item(item, 'warehouse', None, assigned_by, status='pending', notes=notes)
        for officer in _officer_users(cafe, 'warehouse'):
            for item in items:
                _notify_officer(officer, item, 'warehouse', cafe)
        _sync_request_status(purchase_request)


# ═══════════════════════════════════════════════════════════════════════════
# داشبورد مسئول خرید — فقط اقلام ارسالی مدیرعامل
# ═══════════════════════════════════════════════════════════════════════════

@custom_login_required
@role_required(['purchasing', 'warehouse', 'manager', 'ceo'])
def purchase_officer_dashboard(request: HttpRequest) -> HttpResponse:
    """لیست خرید مسئول خرید: اقلامی که مدیرعامل برای او (یا مشترک) ارسال کرده است.

    اقلام مشترک بین بخش‌ها جمع می‌شوند (با ذکر سهم هر بخش)؛ اقلام همنام با توضیحات
    متفاوت در ردیف‌های جدا نمایش داده می‌شوند تا هر گزینه جدا خریداری شود.
    مسئول خرید هر قلم را یا «خریداری شده» می‌کند یا به لیست مدیرعامل برمی‌گرداند.

    برای مدیرعامل/مدیر (نقش «خرید توسط خودم») همین صفحه «لیست خرید من» است:
    فقط اقلامِ تخصیص‌یافته به خودش دیده می‌شود (بدون سهم مشترک مسئولان خرید).
    """
    from collections import defaultdict

    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    # نقش «خرید توسط خودم»: مدیرعامل/مدیرِ بدون نقش خرید، خریدِ خودش را انجام می‌دهد
    is_self_buy = (
        request.user.role in ('manager', 'ceo')
        and not request.user.has_purchasing_role
        and not request.user.has_warehouse_role
    )

    if not (request.user.role == 'admin' or request.user.is_superuser
            or request.user.has_purchasing_role or is_self_buy):
        return redirect('warehouse_officer_dashboard', cafe_code=cafe_code)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        item_id = request.POST.get('item_id', '')
        try:
            assignment_pk = int(item_id)
        except (ValueError, TypeError):
            messages.error(request, 'قلم درخواست یافت نشد')
            return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

        with transaction.atomic():
            # شناسهٔ خام از POST فقط در همین کافه معتبر است؛ کافهٔ دیگر → 404
            assignment = get_scoped_locked_object_or_404(
                OfficerAssignment, request, id=assignment_pk,
                target_role='purchasing')

            if assignment.status != 'pending':
                messages.warning(request, 'این قلم قبلاً پردازش شده است.')
                return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

            if not (assignment.assigned_to_id == request.user.id
                    or assignment.assigned_to_id is None):
                messages.error(request, 'این قلم به شما تخصیص داده نشده است.')
                return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

            if action == 'purchased':
                assignment.status = 'purchased'
                assignment.notes = (assignment.notes or '') + f' خریداری شد توسط {request.user.get_full_name() or request.user.username}.'
                assignment.save(update_fields=['status', 'notes'])
                _sync_request_status(assignment.item.request)
                messages.success(request, f'«{assignment.item.name}» خریداری شد.')
            elif action == 'return':
                assignment.status = 'returned'
                assignment.notes = (assignment.notes or '') + f' قابل خرید نبود — بازگشت به مدیرعامل توسط {request.user.get_full_name() or request.user.username}.'
                assignment.save(update_fields=['status', 'notes'])
                _sync_request_status(assignment.item.request)
                messages.info(request, f'«{assignment.item.name}» به لیست مدیرعامل بازگشت.')
        return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

    # اقلام در انتظار مسئول خرید؛ برای «خرید توسط خودم» فقط تخصیص‌های خودِ مدیرعامل
    assignments_qs = (
        OfficerAssignment.objects
        .filter(target_role='purchasing', status='pending')
        .select_related('item', 'item__request', 'item__request__user', 'item__product', 'assigned_to')
    )
    if is_self_buy:
        assignments_qs = assignments_qs.filter(assigned_to=request.user)
    else:
        assignments_qs = assignments_qs.filter(
            Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
        )
    assignments = filter_by_cafe(
        assignments_qs.order_by('assigned_at'), request)

    rows_map = defaultdict(lambda: {
        'name': '', 'unit': '', 'total_qty': 0,
        'sections': defaultdict(float),
        'assignments': [],
        'descriptions': [],
    })
    for a in assignments:
        item = a.item
        # اقلام همنام با توضیحات متفاوت، ردیف‌های جدا می‌شوند
        key = _product_key(item, include_description=True)
        row = rows_map[key]
        row['name'] = item.product.name if item.product else item.name
        row['unit'] = item.unit
        row['total_qty'] += float(item.quantity or 0)
        dept = _item_section_name(item.request.user)
        row['sections'][dept] += float(item.quantity or 0)
        row['assignments'].append(a)
        if item.description:
            row['descriptions'].append({'dept': dept, 'text': item.description})

    rows = []
    for key, data in rows_map.items():
        rows.append({
            'name': data['name'],
            'unit': data['unit'],
            'total_qty': round(data['total_qty'], 2),
            'sections': [{'name': d, 'qty': round(q, 2)} for d, q in sorted(data['sections'].items())],
            'assignments': data['assignments'],
            'descriptions': data['descriptions'],
        })
    rows.sort(key=lambda r: (-len(r['sections']), -r['total_qty']))

    return render(request, 'purchase_officer_dashboard.html', {
        'rows': rows,
        'total_items': sum(len(r['assignments']) for r in rows),
        'cafe_code': cafe_code,
        'user': request.user,
        'is_self_buy': is_self_buy,
    })


# ═══════════════════════════════════════════════════════════════════════════
# داشبورد مسئول انبار — کالاهای موجود در انبار، بدون نیاز به تایید مدیر
# ═══════════════════════════════════════════════════════════════════════════

@custom_login_required
@role_required(['purchasing', 'warehouse'])
@transaction.atomic
def warehouse_officer_dashboard(request: HttpRequest) -> HttpResponse:
    """صفحه مسئول انبار: کالاهای درخواستی که در انبار موجودی دارند، مستقیم و
    بدون تایید مدیرعامل ارسال می‌شوند. مسئول انبار می‌تواند تامین کند،
    به درخواست خرید ارجاع دهد یا «عدم موجودی» اعلام کند."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    if not (request.user.role == 'admin' or request.user.is_superuser or request.user.has_warehouse_role):
        return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

    # ── فیلترها: روزِ «ثبت درخواست» (هر درخواست در روز ثبتی که داشته قرار
    # می‌گیرد، نه روز تحویل درخواست‌شده) + بخش درخواست‌دهنده ──
    # در ریدایرکت‌ها حفظ می‌شوند تا بعد از هر ثبت همان فیلتر باقی بماند
    from urllib.parse import urlencode
    day_filter = request.GET.get('day', '')
    dept = request.GET.get('department', '')
    dash_params = {}
    if day_filter:
        dash_params['day'] = day_filter
    if dept:
        dash_params['department'] = dept
    dash_url = reverse('warehouse_officer_dashboard', args=[cafe_code])
    if dash_params:
        dash_url += '?' + urlencode(dash_params)

    # پیش‌فرض URL: تاریخ همان روز — بدون پارامتر day به ‎?day=<امروز> ریدایرکت
    # می‌شود تا پیش‌فرض صفحه همیشه «امروز» باشد و فردا خودبه‌خود «فردای روز قبل» شود؛
    # پارامتر صریح در URL دست‌نخورده می‌ماند (فقط GET؛ POSTها بی‌تغییر)
    if not day_filter and request.method == 'GET':
        _params = {'day': timezone.localtime(timezone.now()).date().isoformat()}
        if dept:
            _params['department'] = dept
        return redirect(
            reverse('warehouse_officer_dashboard', args=[cafe_code]) + '?' + urlencode(_params))

    # ── پردازش فرم ──
    if request.method == 'POST':
        action = request.POST.get('action', '')
        item_id = request.POST.get('item_id', '')
        try:
            item_pk = int(item_id)
        except (ValueError, TypeError):
            messages.error(request, 'قلم درخواست یافت نشد')
            return redirect(dash_url)

        # شناسهٔ خام فقط در کافهٔ فعلی معتبر است؛ کافهٔ دیگر → 404
        item = get_scoped_locked_object_or_404(PurchaseItem, request, id=item_pk)

        # قلمی که در لیست خرید ثبت و برای مدیرعامل ارسال شده، دیگر در داشبورد
        # قابل تغییر وضعیت نیست (تا وقتی مدیرعامل آن درخواست خرید را رد نکرده باشد)
        if _item_sent_to_ceo(item) is not None and action in (
                'fulfill', 'to_purchase', 'not_in_stock', 'reopen'):
            messages.error(
                request,
                f'«{item.name}» در لیست خرید ثبت شده و برای تایید مدیرعامل ارسال شده است — '
                'از داشبورد انبار قابل تغییر وضعیت نیست.')
            return redirect(dash_url)

        # تخصیص موجود (در انتظار) یا ساخت تخصیص جدید — با قفل ردیف
        assignment = item.officer_assignments.select_for_update().filter(
            target_role='warehouse', status='pending'
        ).filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True)).first()

        # اگر قبلاً نتیجهٔ نهایی ثبت شده، همین POST دوباره اجرا نمی‌شود.
        done_assigns = item.officer_assignments.select_for_update().filter(
            target_role='warehouse',
            status__in=('fulfilled', 'returned', 'not_in_stock'),
        ).filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))
        if action in ('fulfill', 'to_purchase') and done_assigns.exists():
            messages.warning(request, 'این قلم قبلاً پردازش شده است.')
            return redirect(dash_url)
        if action == 'not_in_stock' and done_assigns.filter(
                status__in=('fulfilled', 'not_in_stock')).exists():
            messages.warning(request, 'این قلم قبلاً پردازش شده است.')
            return redirect(dash_url)
        # «عدم موجودی» روی قلمِ ارجاع‌شده همان تخصیص returned را به روز می‌کند
        if action == 'not_in_stock' and assignment is None:
            assignment = done_assigns.order_by('-assigned_at').first()

        if action == 'fulfill':
            # ردیف‌های انتخابی: ردیف اول (stock_item_id/qty) + ردیف‌های پویای JS (stock_item_id_X/qty_X)
            stock_ids = request.POST.getlist("stock_item_id")
            qtys = request.POST.getlist("qty")
            extra_rows = []
            i = 1
            while f"stock_item_id_{i}" in request.POST:
                extra_rows.append((request.POST.get(f"stock_item_id_{i}", ""), request.POST.get(f"qty_{i}", "")))
                i += 1
            all_rows = list(zip(stock_ids, qtys)) + extra_rows

            # تجمیع ردیف‌های تکراری (همان انبار چند بار انتخاب شده) تا از یک موجودی دوبار کم نشود
            merged = {}
            order = []
            for sid, qty_str in all_rows:
                if not sid:
                    continue
                qty = _parse_decimal(qty_str)
                if sid not in merged:
                    merged[sid] = {'qty': qty, 'order': len(order)}
                    order.append(sid)
                elif qty is not None:
                    merged[sid]['qty'] = (merged[sid]['qty'] or 0) + qty

            has_error = False
            validated = []  # (stock_item, qty)
            for sid in order:
                qty = merged[sid]['qty']
                # موجودی فقط در محدودهٔ کافهٔ فعلی — نه با id خام از POST
                if not str(sid).isdigit():
                    stock_item = None
                else:
                    stock_item = StockItem.objects.filter(
                        id=int(sid), warehouse__cafe=cafe).first()

                if stock_item is None or qty is None or qty <= 0:
                    messages.error(request, f'برای «{item.name}» موجودی انبار و مقدار معتبر انتخاب کنید')
                    has_error = True
                    break
                if stock_item.product_id != item.product_id:
                    messages.error(request, f'موجودی انبار با کالای «{item.name}» مطابقت ندارد')
                    has_error = True
                    break
                base_deduction = qty
                if base_deduction > stock_item.quantity:
                    messages.error(request,
                        f'موجودی «{stock_item.product.name}» ({stock_item.quantity}) کمتر از مقدار درخواستی است')
                    has_error = True
                    break
                validated.append((stock_item, base_deduction, qty))

            if has_error:
                return redirect(dash_url)

            if not validated:
                messages.error(request, f'برای «{item.name}» موجودی انبار و مقدار معتبر انتخاب کنید')
                return redirect(dash_url)

            total_trade = sum((qty for _si, _base, qty in validated), Decimal('0'))
            if total_trade > item.quantity:
                messages.error(
                    request,
                    f'مقدار تامین‌شده برای «{item.name}» از مقدار درخواستی بیشتر است.')
                return redirect(dash_url)

            try:
                with transaction.atomic():
                    for idx, (stock_item, base_deduction, qty) in enumerate(validated):
                        # کسر اتمیک از موجودی انبار
                        apply_stock_delta(
                            stock_item, -base_deduction,
                            cafe=cafe, created_by=request.user,
                            reference_type='officer_assignment',
                            reference_id=item.id,
                            description=f'تامین کالا توسط مسئول انبار - {item.name} - درخواست {request_number_for(item.request_id)}',
                        )
                        if idx == 0:
                            # ردیف اول: تخصیص در انتظار قبلی (یا تخصیص جدید) تکمیل می‌شود
                            if assignment is None:
                                assignment = _assign_item(item, 'warehouse', request.user, request.user)
                            assignment.status = 'fulfilled'
                            assignment.stock_item = stock_item
                            assignment.supplied_quantity = base_deduction
                            assignment.notes = (assignment.notes or '') + f' تامین شد: {qty} از انبار {stock_item.warehouse.name if stock_item.warehouse else ""}.'
                            assignment.save()
                        else:
                            # هر برند اضافه، تخصیص جداگانه می‌گیرد (مدل فقط یک StockItem در هر تخصیص دارد)
                            extra_assignment = _assign_item(
                                item, 'warehouse', request.user, request.user, status='fulfilled')
                            extra_assignment.stock_item = stock_item
                            extra_assignment.supplied_quantity = base_deduction
                            extra_assignment.notes = f'تامین شد: {qty} از انبار {stock_item.warehouse.name if stock_item.warehouse else ""}.'
                            extra_assignment.save()
            except (InsufficientStockError, StockScopeError) as exc:
                messages.error(request, f'موجودی «{exc}» در این لحظه کافی نیست؛ ثبت نشد')
                return redirect(dash_url)
            _remove_from_purchase_drafts(item)
            _sync_request_status(item.request)
            messages.success(request, f'«{item.name}» از انبار تامین شد.')
            return redirect(dash_url)

        elif action in ('to_purchase', 'not_in_stock'):
            if assignment is None:
                assignment = _assign_item(item, 'warehouse', request.user, request.user)
            if action == 'to_purchase':
                # ارجاع به خرید: قلم به صفحه «درخواست خرید» (پیش‌نویس) می‌رود تا
                # مسئول انبار لیست را تکمیل کند و بعد برای تایید مدیرعامل بفرستد
                assignment.status = 'returned'
                assignment.notes = (assignment.notes or '') + ' ارجاع به درخواست خرید.'
                assignment.save()
                _sync_request_status(item.request)
                draft = _get_or_create_purchase_draft(cafe, request.user)
                PurchaseDraftItem.objects.get_or_create(
                    draft=draft,
                    source_item=item,
                    defaults={
                        'product': item.product,
                        'name': item.product.name if item.product else item.name,
                        'quantity': item.quantity,
                        'unit': item.unit,
                        'description': item.description or '',
                    },
                )
                messages.info(request, f'«{item.product.name if item.product else item.name}» به صفحه «درخواست خرید» اضافه شد (از منوی کنار قابل مشاهده است).')
                return redirect(dash_url)
            else:
                assignment.status = 'not_in_stock'
                assignment.notes = (assignment.notes or '') + ' عدم امکان تأمین ثبت شد.'
                assignment.save()
                _remove_from_purchase_drafts(item)
                _sync_request_status(item.request)
                messages.warning(request, f'برای «{item.name}» عدم امکان تأمین ثبت شد.')
            return redirect(dash_url)

        elif action == 'reopen':
            # بازگشایی فقط با دلیل و با ردیف جبرانی؛ هیچ سند یا تخصیصی حذف نمی‌شود
            reason = (request.POST.get('reason') or '').strip()
            try:
                reopen_officer_assignments(
                    item, cafe=cafe, reopened_by=request.user, reason=reason)
            except (ReopenNotAllowedError, StockScopeError,
                    InsufficientStockError) as exc:
                messages.error(request, f'بازگشایی ناموفق بود: {exc}')
                return redirect(dash_url)
            # قلمی که قبلاً «ارجاع به خرید» شده بود دیگر ارجاع نیست — از پیش‌نویس خرید حذف شود
            _removed = _remove_from_purchase_drafts(item)
            _sync_request_status(item.request)
            if _removed:
                msg = f'«{item.name}» از لیست خرید حذف و به لیست فعال بازگردانده شد.'
            else:
                msg = f'«{item.name}» بازگشایی شد — دوباره از اول ثبت کنید.'
            messages.info(request, msg)
            return redirect(dash_url)

    # ── درخواست‌های این کافه (پیش‌فرض: فقط امروز؛ فیلتر روزِ ثبت روی همین پایه) ──
    cutoff_dt = timezone.localtime(timezone.now()) - timedelta(days=45)
    requests_qs = (
        PurchaseRequest.objects
        .select_related('user', 'user__department')
        .prefetch_related(
            'items', 'items__product', 'items__product__unit',
            'items__officer_assignments')
        .filter(created_at__gte=cutoff_dt, cafe=cafe)
        .order_by('-created_at')
    )

    # ── فیلتر روزِ ثبت: هر درخواست در روزِ «ثبت» خودش (created_at به وقت محلی)
    # قرار می‌گیرد، نه در روز تحویلِ درخواست‌شده؛ زمان تحویلِ درخواست‌شده فقط روی
    # کارت نمایش داده می‌شود. مرز روز صریح محاسبه می‌شود تا درخواستِ ساعاتِ
    # پایانیِ روزِ قبل به روز بعد نیفتد. «امروز» بر پایهٔ ساعت محلی تهران (نه UTC)
    now_local = timezone.localtime(timezone.now())
    today = now_local.date()
    if day_filter:
        try:
            ddate = datetime.strptime(day_filter, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            ddate = None
        if ddate is None:
            requests_qs = requests_qs.none()
    else:
        # بدون پارامتر GET (مثلاً POST بدون ریدایرکت): همان پیش‌فرض — امروز
        ddate = today
    if ddate is not None:
        day_start = timezone.make_aware(datetime.combine(ddate, datetime.min.time()))
        requests_qs = requests_qs.filter(
            created_at__gte=day_start, created_at__lt=day_start + timedelta(days=1))

    # فیلتر بخش درخواست‌دهنده
    if dept:
        requests_qs = requests_qs.filter(
            Q(user__department__name=dept) | Q(user__role=dept))

    requests_list = list(requests_qs)

    # محصولات دارای موجودی برای ارسال خودکار (بدون تخصیص مدیرعامل)
    stocked_product_ids = set(
        StockItem.objects.filter(
            quantity__gt=0, warehouse__cafe=cafe
        ).values_list('product_id', flat=True))

    # ── موجودی فعلی هر محصول (برای ستون «موجودی فعلی») ──
    scope_pids = sorted({item.product_id for req in requests_list for item in req.items.all() if item.product_id})
    stock_map = {}  # product_id -> {'trade': Decimal, 'unit': str}
    if scope_pids:
        sis = StockItem.objects.filter(
            product_id__in=scope_pids, quantity__gt=0,
            warehouse__cafe=cafe,
        ).select_related('unit')
        for si in sis:
            unit = (si.unit.name or si.unit.symbol) if si.unit else ''
            entry = stock_map.get(si.product_id)
            if entry is None:
                stock_map[si.product_id] = {'trade': si.quantity, 'unit': unit}
            else:
                entry['trade'] += si.quantity

    def item_stock_info(item):
        """موجودی فعلی یک قلم برای نمایش کنار مقدار درخواستی"""
        if not item.product_id:
            return None

        def _with_verdict(trade, unit):
            # فقط نمایشی: قضاوت «موجودی کافی/ناکافی» برای پرهیز از مقایسه ذهنی اعداد
            try:
                trade_d = Decimal(str(trade))
            except Exception:
                trade_d = Decimal('0')
            try:
                qty_d = Decimal(str(item.quantity or 0))
            except Exception:
                qty_d = Decimal('0')
            enough = trade_d >= qty_d
            short = qty_d - trade_d if not enough else Decimal('0')
            return {'trade': _fmt2(trade_d), 'unit': unit, 'has': trade_d > 0,
                    'enough': enough, 'short': _fmt2(short) if short > 0 else None}

        entry = stock_map.get(item.product_id)
        if not entry:
            unit = item.product.unit.name if item.product and item.product.unit else item.unit
            return _with_verdict(0, unit)
        return _with_verdict(entry['trade'], entry['unit'] or item.unit)

    # ── دسته‌بندی اقلام هر درخواست ──
    #  ۱) قابل تامین: تخصیص در انتظار مسئول انبار یا ارسال خودکار (موجودی دارد)
    #  ۲) ثبت‌شده: تامین شده / عدم موجودی — کمرنگ با قابلیت بازگشایی
    #  ۳) ارجاع به خرید: قلم به پیش‌نویس صفحه «درخواست خرید» می‌رود
    request_cards = []
    for req in requests_list:
        # درخواست‌های خریدِ خودِ مسئول انبار (source=warehouse_purchase) دیگر به
        # داشبورد انبار برنمی‌گردند — مسیرشان: ارسال ← تایید مدیرعامل ← ثبت سفارش خرید
        if req.source == 'warehouse_purchase':
            continue
        visible, done = [], []
        for item in req.items.all():
            # تخصیص‌های reopened فقط سابقه‌اند؛ مانع نمایش مجدد قلم نمی‌شوند
            assigns = [
                a for a in item.officer_assignments.all()
                if a.status != 'reopened'
            ]
            reopened_assigns = [
                a for a in item.officer_assignments.all()
                if a.target_role == 'warehouse' and a.status == 'reopened'
            ]
            my_pending = [a for a in assigns if a.target_role == 'warehouse' and a.status == 'pending'
                          and (a.assigned_to_id == request.user.id or a.assigned_to_id is None)]
            if my_pending:
                visible.append({'item': item, 'assignment': my_pending[0], 'stock': item_stock_info(item)})
            elif not assigns and (reopened_assigns or _item_in_stock(item, cafe, stocked_product_ids)):
                visible.append({'item': item, 'assignment': None, 'stock': item_stock_info(item)})
            else:
                my_done = [a for a in assigns if a.target_role == 'warehouse'
                           and a.status in ('fulfilled', 'not_in_stock', 'returned')
                           and (a.assigned_to_id == request.user.id or a.assigned_to_id is None)]
                if my_done:
                    sent_copy = _item_sent_to_ceo(item)
                    done.append({'item': item, 'assignment': my_done[0],
                                 'locked': sent_copy is not None,
                                 'locked_request_id': sent_copy.request_id if sent_copy else None})
        if visible or done:
            request_cards.append({'request': req, 'visible': visible, 'done': done})

    # ── کارت‌های «تمام‌شده» (همهٔ اقلامشان ثبت‌شده) به انتهای لیست منتقل می‌شوند ──
    request_cards.sort(key=lambda c: 0 if c['visible'] else 1)

    # ── گزینه‌های تامین از انبار برای اقلام قابل تامین (بر اساس ردیف‌های موجودی) ──
    product_ids = sorted({v['item'].product_id for card in request_cards for v in card['visible'] if v['item'].product_id})
    stock_options = {}
    stock_meta = {}
    if product_ids:
        sis = StockItem.objects.filter(
            product_id__in=product_ids, quantity__gt=0,
            warehouse__cafe=cafe,
        ).select_related('warehouse', 'unit').order_by('warehouse__name')
        for si in sis:
            unit_label = (si.unit.name or si.unit.symbol) if si.unit else ''
            stock_options.setdefault(si.product_id, []).append({
                'id': si.id,
                'warehouse_name': si.warehouse.name if si.warehouse else '',
                'default_selected': False,
                'available': _fmt2(si.quantity),
                'unit_label': unit_label,
                'qty': _fmt2(si.quantity),
            })
        for pid, opts in stock_options.items():
            hide_select = len(opts) == 1
            if hide_select:
                opts[0]['default_selected'] = True
            stock_meta[pid] = {'hide_select': hide_select}

    # ── ناوبری روزِ ثبت (همان الگوی صفحه «همه بخش‌ها»): روز قبل/بعد + انتخابگر ──
    selected_nav_date = None
    if day_filter:
        try:
            selected_nav_date = datetime.strptime(day_filter, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            selected_nav_date = None
    if selected_nav_date is None:
        # پیش‌فرض ناوبری: امروز (صفحه پیش‌فرض، درخواست‌های همان روز را نشان می‌دهد)
        selected_nav_date = today
    date_str = selected_nav_date.isoformat()
    prev_date = selected_nav_date - timedelta(days=1)
    next_date = selected_nav_date + timedelta(days=1)

    # ── آمار خلاصه برای کارت‌های بالای داشبورد (بر روی خروجی فیلترشده) ──
    visible_count = sum(len(card['visible']) for card in request_cards)
    done_count = sum(len(card['done']) for card in request_cards)
    enough_count = sum(1 for card in request_cards for v in card['visible']
                       if v['stock'] and v['stock'].get('enough'))
    short_count = sum(1 for card in request_cards for v in card['visible']
                      if v['stock'] and v['stock']['has'] and not v['stock'].get('enough'))
    no_stock_count = sum(1 for card in request_cards for v in card['visible']
                         if v['stock'] and not v['stock']['has'])

    # ── فهرست بخش‌ها برای فیلتر بالا: بخش‌های فعال کافه جاری + نقش‌های قدیمی بدون بخش ──
    departments_list = []  # [(کلید فیلتر، برچسب نمایش)]
    seen = set()
    for d in Department.objects.filter(is_active=True, cafe=cafe):
        if d.name not in seen:
            seen.add(d.name)
            departments_list.append((d.name, d.name))
    for req in requests_list:
        u = req.user
        if not u:
            continue
        if u.department_id:
            key, label = u.department.name, u.department.name
        else:
            key, label = u.role or '', u.get_role_display()
        if key and key not in seen:
            seen.add(key)
            departments_list.append((key, label))
    if dept and dept not in seen:
        departments_list.append((dept, dept))
    departments_list.sort(key=lambda x: x[1])

    return render(request, 'warehouse_officer_dashboard.html', {
        'request_cards': request_cards,
        'stats': {
            'pending': visible_count,
            'done': done_count,
            'enough': enough_count,
            'short': short_count,
            'no_stock': no_stock_count,
        },
        'stock_options': stock_options,
        'stock_meta': stock_meta,
        'selected_day': day_filter,
        'selected_date': selected_nav_date,
        'today_str': today.isoformat(),
        'date_str': date_str,
        'prev_date': prev_date,
        'next_date': next_date,
        'departments': departments_list,
        'selected_department': dept,
        'cafe_code': cafe_code,
        'user': request.user,
    })


# ═══════════════════════════════════════════════════════════════════════════
# صفحه درخواست خرید مسئول انبار — سبد ارجاع‌شده‌ها + ارسال به تایید مدیرعامل
# ═══════════════════════════════════════════════════════════════════════════

def _get_or_create_purchase_draft(cafe, user):
    """پیش‌نویس باز مسئول انبار در این کافه (یا ساخت آن)"""
    draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=user).first()
    if draft is None:
        draft = PurchaseDraft.objects.create(cafe=cafe, created_by=user)
    return draft


def _item_sent_to_ceo(item):
    """درخواست خریدِ ارسال‌شده‌ای که این قلم در آن کپی شده و هنوز در جریان است.

    وقتی مسئول انبار پیش‌نویس «درخواست خرید» را ارسال می‌کند، اقلامِ پیش‌نویس به یک
    درخواست خرید (source=warehouse_purchase) کپی می‌شوند و برای تایید به مدیرعامل
    می‌روند. تا وقتی آن درخواست رد نشده باشد، قلمِ مبدأ در داشبورد انبار قابل تغییر
    وضعیت نیست (تخصیص/عدم موجودی/بازگشایی/ارجاع مجدد). ردِ مدیرعامل قفل را باز می‌کند.
    """
    return (item.purchase_copies
            .filter(request__source='warehouse_purchase')
            .exclude(request__status='rejected')
            .select_related('request')
            .order_by('-id')
            .first())


def _remove_from_purchase_drafts(item):
    """حذف خودکارِ قلمِ ارجاع‌شده از پیش‌نویس «درخواست خرید» پس از بازنگری مسئول انبار.

    وقتی مسئول انبار در داشبورد وضعیتِ قلمِ «ارجاع به خرید» (returned) را عوض می‌کند
    (بازگشایی / تامین از انبار / عدم موجودی)، آن قلم دیگر برای خرید ارجاع نیست؛
    نسخهٔ کپی‌اش در اقلامِ آمادهٔ ارسالِ خرید حذف می‌شود و اگر پیش‌نویس خالی شد،
    خود پیش‌نویس هم پاک می‌شود. تعداد قلم‌های حذف‌شده را برمی‌گرداند.
    """
    draft_ids = set()
    removed = 0
    for di in PurchaseDraftItem.objects.filter(source_item=item):
        draft_ids.add(di.draft_id)
        di.delete()
        removed += 1
    for did in draft_ids:
        draft = PurchaseDraft.objects.filter(id=did).first()
        if draft is not None and not draft.items.exists():
            draft.delete()
    return removed


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def warehouse_purchase_draft(request: HttpRequest) -> HttpResponse:
    """صفحه «درخواست خرید» مسئول انبار:

    - کالاهایی که در داشبورد انبار «ارجاع به خرید» شده‌اند، اول در این صفحه
      (پیش‌نویس) قرار می‌گیرند و قابل ویرایش/حذف/افزودن هستند.
    - با «ارسال» یک درخواست خرید ساخته می‌شود که برای تایید به مدیرعامل
      می‌رود (بدون تخصیص به مسئول انبار — خودِ فرستنده نباید اقدام کند).
    - درخواست‌های ارسال‌شده تا قبل از تایید مدیرعامل در همین صفحه قابل
      ویرایش و حذف هستند.
    """
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    if not (request.user.role == 'admin' or request.user.is_superuser or request.user.has_warehouse_role):
        return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

    # ── پردازش فرم‌ها ──
    if request.method == 'POST':
        action = request.POST.get('action', '')

        # محصولات فعال برای «افزودن کالا» (همه اکشن‌ها به جز send به این صفحه برمی‌گردند)
        product_choices = Product.objects.filter(is_active=True).order_by('name')
        if cafe:
            product_choices = product_choices.filter(Q(cafe=cafe) | Q(cafe__isnull=True))

        if action == 'send':
            draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
            draft_items = list(draft.items.select_related('product')) if draft else []
            if not draft_items:
                messages.error(request, 'پیش‌نویس خالی است — چیزی برای ارسال نیست')
                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

            with transaction.atomic():
                # درخواست خریدِ مسئول انبار زمان تحویل ندارد — تاریخِ خودِ درخواست
                # (created_at) همان زمان ثبت است و همین‌جا نمایش داده می‌شود.
                new_request = PurchaseRequest.objects.create(
                    user=request.user,
                    cafe=cafe,
                    status='pending_manager_approval',
                    source='warehouse_purchase',
                )
                for d_item in draft_items:
                    PurchaseItem.objects.create(
                        request=new_request,
                        product=d_item.product,
                        name=d_item.name,
                        quantity=d_item.quantity,
                        unit=d_item.unit,
                        description=d_item.description or '',
                        # پیوند به قلم اصلیِ درخواست‌دهنده: تا این درخواست خرید در
                        # جریان باشد (رد نشده باشد)، قفلِ تغییر وضعیتِ آن قلم در
                        # داشبورد انبار با همین پیوند تشخیص داده می‌شود
                        source_item=d_item.source_item,
                    )
                draft.items.all().delete()
                draft.delete()
            ActivityLog.objects.create(
                user=request.user,
                request=new_request,
                action='created',
                description=f'درخواست خرید توسط مسئول انبار ارسال شد و در انتظار تایید مدیرعامل است ({len(draft_items)} قلم)',
            )
            # جریان جدید: درخواست خریدِ مسئول انبار تخصیص انبار نمی‌سازد (به
            # داشبورد خودش برنمی‌گردد) — برای تایید به مدیرعامل می‌رود و پس از
            # تایید، واحد خرید از فهرست «درخواست‌های تاییدشده» سفارش خرید ثبت می‌کند.
            messages.success(request, f'درخواست خرید با {len(draft_items)} قلم برای تایید مدیرعامل ارسال شد.')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'add_item':
            draft = _get_or_create_purchase_draft(cafe, request.user)
            # فرم این صفحه با «new_name» پست می‌کند؛ «name» هم برای سازگاری پذیرفته می‌شود
            name = (request.POST.get('name', '') or request.POST.get('new_name', '') or '').strip()
            product_id = request.POST.get('product_id', '')
            # فرم قالب با «quantity» پست می‌کند؛ «new_qty» هم پذیرفته می‌شود
            qty = _parse_decimal(request.POST.get('quantity', '') or request.POST.get('new_qty', ''))
            unit = (request.POST.get('unit', '') or 'عدد').strip()
            description = (request.POST.get('description', '') or '').strip()

            item_product = None
            # شناسهٔ خامِ POST فقط در محدودهٔ کافهٔ فعلی — نه یک واکشی سراسری.
            if product_id.isdigit() and cafe is not None:
                item_product = Product.objects.filter(id=int(product_id), is_active=True, cafe=cafe).first()
            if item_product is None and name:
                item_product = (Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe
                                else Product.objects.filter(name__iexact=name).first())

            if item_product is None and not name:
                messages.error(request, 'نام کالا را وارد کنید')
            elif inactive_product_error(item_product):
                messages.error(request, inactive_product_error(item_product))
            elif qty is None or qty <= 0 or qty > 99999999:
                messages.error(request, 'مقدار باید عدد مثبت باشد')
            else:
                PurchaseDraftItem.objects.create(
                    draft=draft,
                    product=item_product,
                    name=item_product.name if item_product else name,
                    quantity=qty,
                    unit=unit or (item_product.unit.name if item_product and item_product.unit else 'عدد'),
                    description=description,
                )
                messages.success(request, f'«{item_product.name if item_product else name}» به پیش‌نویس اضافه شد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'edit_item':
            d_item = PurchaseDraftItem.objects.filter(
                id=request.POST.get('item_id', '') or -1, draft__cafe=cafe,
                draft__created_by=request.user).select_related('draft').first()
            qty = _parse_decimal(request.POST.get('quantity', ''))
            if d_item is None:
                messages.error(request, 'قلم پیش‌نویس یافت نشد')
            elif qty is None or qty <= 0 or qty > 99999999:
                messages.error(request, 'مقدار باید عدد مثبت باشد')
            else:
                d_item.quantity = qty
                new_desc = request.POST.get('description')
                if new_desc is not None:
                    d_item.description = new_desc.strip()
                d_item.save()
                messages.success(request, f'«{d_item.name}» ویرایش شد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'delete_item':
            d_item = PurchaseDraftItem.objects.filter(
                id=request.POST.get('item_id', '') or -1, draft__cafe=cafe,
                draft__created_by=request.user).first()
            if d_item:
                name = d_item.name
                source_item = d_item.source_item
                d_item.delete()
                if source_item:
                    try:
                        reopen_officer_assignments(
                            source_item, cafe=cafe, reopened_by=request.user,
                            reason='حذف از لیست خرید و بازگشت به لیست انبار')
                        _sync_request_status(source_item.request)
                    except Exception:
                        pass
                draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
                if draft and not draft.items.exists():
                    draft.delete()
                msg = f'«{name}» از پیش‌نویس حذف شد'
                if source_item:
                    msg += ' و به لیست انبار بازگشت'
                messages.info(request, msg)
            else:
                messages.error(request, 'قلم پیش‌نویس یافت نشد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'discard_draft':
            draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
            if draft:
                has_source = False
                for di in list(draft.items.filter(source_item__isnull=False)):
                    has_source = True
                    try:
                        reopen_officer_assignments(
                            di.source_item, cafe=cafe, reopened_by=request.user,
                            reason='حذف از لیست خرید و بازگشت به لیست انبار')
                        _sync_request_status(di.source_item.request)
                    except Exception:
                        pass
                draft.delete()
                msg = 'پیش‌نویس حذف شد'
                if has_source:
                    msg += ' و اقلام ارجاعی به لیست انبار بازگشتند'
                messages.info(request, msg)
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action in ('edit_sent', 'delete_sent'):
            # ویرایش/حذف اقلام درخواست‌های ارسال‌شده فقط تا قبل از تایید مدیرعامل.
            # بعد از تایید مدیرعامل، درخواست به واحد خرید (ثبت سفارش خرید) می‌رود
            # و دیگر در این صفحه تغییرپذیر نیست.
            req = PurchaseRequest.objects.filter(
                id=request.POST.get('request_id', '') or -1,
                source='warehouse_purchase',
                status='pending_manager_approval',
            ).select_related('user').first()
            if req is None or (cafe and req.cafe_id != cafe.id):
                messages.error(request, 'درخواست یافت نشد یا دیگر قابل ویرایش نیست (پس از تایید مدیرعامل)')
                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

            if action == 'edit_sent':
                item = req.items.filter(id=request.POST.get('item_id', '') or -1).first()
                qty = _parse_decimal(request.POST.get('quantity', ''))
                if item is None:
                    messages.error(request, 'قلم یافت نشد')
                elif qty is None or qty <= 0 or qty > 99999999:
                    messages.error(request, 'مقدار باید عدد مثبت باشد')
                else:
                    item.quantity = qty
                    desc = request.POST.get('description')
                    if desc is not None:
                        item.description = desc.strip()
                    item.save()
                    messages.success(request, f'«{item.product.name if item.product else item.name}» ویرایش شد')
            else:  # delete_sent
                item = req.items.filter(id=request.POST.get('item_id', '') or -1).first()
                if item is None:
                    messages.error(request, 'قلم یافت نشد')
                else:
                    name = item.product.name if item.product else item.name
                    item.delete()
                    if not req.items.exists():
                        # درخواست خالی‌شده حذف نمی‌شود: در صف مدیرعامل می‌ماند تا
                        # خودِ مدیرعامل آن را تعیین‌تکلیف کند (تایید/رد).
                        messages.info(
                            request,
                            f'«{name}» حذف شد — درخواست اکنون بدون قلم است و از صف '
                            'مدیرعامل حذف نمی‌شود؛ برای حذف کامل، از مدیرعامل بخواهید آن را رد کند.'
                        )
                    else:
                        messages.info(request, f'«{name}» از درخواست حذف شد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

    # ── پیش‌نویس جاری ──
    draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).select_related('cafe').first()
    draft_items = list(draft.items.select_related(
        'product', 'product__unit', 'source_item__request') if draft else [])

    # ── درخواست‌های ارسال‌شده ──
    # جریان جدید: درخواست خرید مسئول انبار پس از «ارسال» برای تایید مدیرعامل
    # می‌رود (status=pending_manager_approval) و تا قبل از تایید در همین صفحه
    # قابل ویرایش است. بعد از تایید، در انتظار ثبت سفارش خرید توسط واحد خرید
    # است (نمایشی) و اگر رد شود دلیلش اینجا دیده می‌شود (نمایشی).
    # بخش ارسال‌شده فقط درخواست‌های یک هفتهٔ گذشته را نشان می‌دهد؛ درخواست‌های
    # قدیمی‌تر (حتی در انتظار تایید) در این صفحه نمایش داده نمی‌شوند.
    sent_cutoff = timezone.now() - timedelta(days=7)
    sent_base = (PurchaseRequest.objects
                 .filter(source='warehouse_purchase', created_at__gte=sent_cutoff)
                 .select_related('user')
                 .prefetch_related('items', 'items__product', 'items__product__unit'))
    if cafe:
        sent_base = sent_base.filter(cafe=cafe)
    sent_requests = list(sent_base.filter(status='pending_manager_approval').order_by('-created_at')[:30])
    # تاییدشده توسط مدیرعامل — در انتظار ثبت سفارش خرید در واحد خرید (فقط نمایش)
    approved_sent_requests = list(sent_base.filter(status='pending').order_by('-created_at')[:15])
    # ردشده توسط مدیرعامل (فقط نمایش)
    rejected_sent_requests = list(sent_base.filter(status='rejected').order_by('-created_at')[:10])

    # محصولات فعال برای فرم افزودن
    products = Product.objects.filter(is_active=True).order_by('name')
    if cafe:
        products = products.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
    products = list(products[:500])

    # ── اطلاعات هر قلم پیش‌نویس: ماندگاری (تعریف‌شده هنگام ثبت محصول) + موجودی انبار ──
    draft_item_infos = {}
    scope_ids = sorted({d.product_id for d in draft_items if d.product_id})
    stock_map = {}  # product_id -> {'trade': Decimal, 'unit': str}
    if scope_ids:
        sis = StockItem.objects.filter(product_id__in=scope_ids, quantity__gt=0).select_related('unit')
        if cafe:
            sis = sis.filter(warehouse__cafe=cafe)
        for si in sis:
            unit = (si.unit.name or si.unit.symbol) if si.unit else ''
            entry = stock_map.get(si.product_id)
            if entry is None:
                stock_map[si.product_id] = {'trade': si.quantity, 'unit': unit}
            else:
                entry['trade'] += si.quantity
    for d in draft_items:
        if not d.product_id:
            continue
        shelf = d.product.shelf_life_days or None
        entry = stock_map.get(d.product_id)
        total = entry['trade'] if entry else Decimal('0')
        unit = (entry['unit'] if entry else '') or ((d.product.unit.name if d.product.unit else '') or d.unit)
        draft_item_infos[d.id] = {
            'shelf_life': shelf,
            'has_stock': total > 0,
            # قالب با |trim_decimal نمایش می‌دهد: حداکثر ۲ رقم اعشار، صفرها حذف (25 → «25»، 8.333… → «8.33»)
            'stock_total': total,
            'stock_unit': unit,
        }

    # ── زمان درخواستیِ تحویل هر قلم (فقط نمایش) ──
    # قلمی که با «ارجاع به خرید» از درخواستِ کاربر آمده همان delivery_date درخواستِ
    # اصلی را نشان می‌دهد — حتی اگر آن تاریخ گذشته باشد. قلمِ دستی: «—».
    draft_requested_delivery = {}
    for d in draft_items:
        if d.source_item_id and d.source_item:
            src_req = d.source_item.request
            draft_requested_delivery[d.id] = {
                'date': src_req.delivery_date,
                'immediate': bool(src_req.delivery_immediate),
            }

    return render(request, 'warehouse_purchase_draft.html', {
        'draft': draft,
        'draft_items': draft_items,
        'draft_item_infos': draft_item_infos,
        'draft_requested_delivery': draft_requested_delivery,
        'sent_requests': sent_requests,
        'approved_sent_requests': approved_sent_requests,
        'rejected_sent_requests': rejected_sent_requests,
        'products': products,
        'cafe_code': cafe_code,
        'user': request.user,
    })


# ═══════════════════════════════════════════════════════════════════════════
# وضعیت سفارش‌های خرید (مدیرعامل) — چه کالایی به کدام مسئول ارسال شده
# ═══════════════════════════════════════════════════════════════════════════

@custom_login_required
@role_required(['manager', 'ceo'])
def purchase_orders_status(request: HttpRequest) -> HttpResponse:
    """صفحه وضعیت سفارش‌های خرید در داشبورد مدیرعامل:
    با انتخاب مسئول خرید/انبار می‌توان دید چه کالاهایی برای چه کسی ارسال شده و
    وضعیت تخصیص‌ها قابل ویرایش (تغییر وضعیت، جابجایی بین مسئولان، حذف) است."""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    if request.method == 'POST':
        assignment_id = request.POST.get('assignment_id', '')
        action = request.POST.get('action', '')
        try:
            assignment = OfficerAssignment.objects.get(id=int(assignment_id))
        except (OfficerAssignment.DoesNotExist, ValueError, TypeError):
            assignment = None

        if assignment and cafe and assignment.item.request.cafe_id != cafe.id:
            assignment = None

        if assignment:
            if action == 'status':
                new_status = request.POST.get('status', '')
                if new_status in dict(OfficerAssignment.STATUS_CHOICES):
                    assignment.status = new_status
                    assignment.save()
                    _sync_request_status(assignment.item.request)
                    messages.success(request, f'وضعیت «{assignment.item.name}» به‌روز شد.')
            elif action == 'reassign':
                officer_id = request.POST.get('officer_id', '')
                officer = User.objects.filter(id=int(officer_id), is_active=True).first() if officer_id else None
                if officer and _officer_can_role(officer, assignment.target_role):
                    assignment.assigned_to = officer
                    assignment.save()
                    messages.success(request, f'«{assignment.item.name}» به {officer.get_full_name() or officer.username} واگذار شد.')
                else:
                    messages.error(request, 'مسئول انتخاب‌شده این نقش را ندارد.')
            elif action == 'delete':
                item_name = assignment.item.name
                assignment.delete()
                _sync_request_status(assignment.item.request)
                messages.info(request, f'تخصیص «{item_name}» حذف شد (به لیست مدیرعامل بازگشت).')
        return redirect('purchase_orders_status', cafe_code=cafe_code)

    # ── فیلترها ──
    officer_filter = request.GET.get('officer', '')
    status_filter = request.GET.get('status', '')
    role_filter = request.GET.get('role', '')

    assignments = (
        OfficerAssignment.objects
        .select_related('item', 'item__request', 'item__request__user', 'assigned_to', 'assigned_by')
        .order_by('-assigned_at')
    )
    if cafe:
        assignments = assignments.filter(item__request__cafe=cafe)
    if officer_filter:
        assignments = assignments.filter(assigned_to_id=int(officer_filter) if officer_filter.isdigit() else -1)
    if status_filter:
        assignments = assignments.filter(status=status_filter)
    if role_filter in ('purchasing', 'warehouse'):
        assignments = assignments.filter(target_role=role_filter)

    all_officers = []
    for role in ('purchasing', 'warehouse'):
        all_officers.extend(_officer_users(cafe, role))

    return render(request, 'purchase_orders_status.html', {
        'assignments': assignments,
        'all_officers': all_officers,
        'officer_filter': officer_filter,
        'status_filter': status_filter,
        'role_filter': role_filter,
        'status_choices': OfficerAssignment.STATUS_CHOICES,
        'cafe_code': cafe_code,
        'user': request.user,
    })


# ---------------------------------------------------------------------------
# All-departments requests view
# ---------------------------------------------------------------------------

@custom_login_required
@role_required(["purchasing", "warehouse", "manager", "ceo"])
def all_departments_requests(request):
    """Aggregated view of every department's purchase/warehouse requests.

    Similar products requested by multiple departments are merged so the
    CEO/manager can see the total amount needed at a glance, with the quantity
    each section requested. Each item is selectable and can be sent to a
    specific purchase/warehouse/dual officer; once sent, rows fade out.

    جریان جدید: بخش «کالا های موجود در انبار» از این صفحه حذف شد — آن اقلام
    مستقیم به داشبورد مسئول انبار می‌روند (بدون تایید مدیر).
    """
    from collections import defaultdict
    from datetime import date, timedelta
    from urllib.parse import urlencode

    request_type = request.GET.get('type', 'purchase')
    date_str = request.GET.get('date', '')
    section_filter = request.GET.get('section', '').strip()
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    # ── پردازش فرم: ارسال به مسئول / حذف قلم / ویرایش قلم ──
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if request_type == 'purchase':
            if action == 'send':
                item_ids = [i for i in request.POST.get('item_ids', '').split(',') if i.strip().isdigit()]
                officer_id = request.POST.get('officer_user_id', '')
                officer = None
                if officer_id.strip().isdigit():
                    officer = User.objects.filter(id=int(officer_id), is_active=True).first()
                # «خرید توسط خودم»: مدیرعامل/مدیر قلم را برای خودش نگه می‌دارد
                # (به «لیست خرید من» خودش می‌رود، نه به مسئول خرید)
                is_self_buy = (
                    officer is not None
                    and officer.id == request.user.id
                    and request.user.role in ('manager', 'ceo')
                )
                if not officer or (not is_self_buy and not (officer.has_purchasing_role or officer.has_warehouse_role)):
                    messages.error(request, 'لطفاً یک مسئول خرید/انبار معتبر انتخاب کنید')
                elif not item_ids:
                    messages.error(request, 'هیچ قلمی برای ارسال انتخاب نشده است')
                else:
                    items = PurchaseItem.objects.filter(id__in=item_ids)
                    if cafe:
                        items = items.filter(request__cafe=cafe)
                    target_role = 'purchasing' if (is_self_buy or officer.has_purchasing_role) else 'warehouse'
                    sent = skipped = 0
                    affected_request_ids = set()
                    for item in items:
                        if item.officer_assignments.filter(status__in=ASSIGNMENT_ACTIVE_STATUSES).exists():
                            skipped += 1
                            continue
                        _assign_item(item, target_role, officer, request.user)
                        if not is_self_buy:
                            _notify_officer(officer, item, target_role, cafe)
                        sent += 1
                        affected_request_ids.add(item.request_id)
                    for rid in affected_request_ids:
                        _sync_request_status(PurchaseRequest.objects.get(id=rid))
                    if sent:
                        if is_self_buy:
                            messages.success(
                                request,
                                f'{sent} قلم به «لیست خرید من» شما ارسال شد (خرید توسط خودم).'
                            )
                        else:
                            messages.success(
                                request,
                                f'{sent} قلم به {officer.get_full_name() or officer.username} ({_officer_role_label(officer)}) ارسال شد.'
                            )
                    if skipped:
                        messages.info(request, f'{skipped} قلم قبلاً ارسال شده بودند و نادیده گرفته شدند.')
            elif action == 'delete_item':
                item_id = request.POST.get('item_id', '')
                if item_id.strip().isdigit():
                    item = PurchaseItem.objects.filter(id=int(item_id)).first()
                    if item and (not cafe or item.request.cafe_id == cafe.id):
                        request_obj = item.request
                        item_name = item.name or item.product.name if item.product else (item.name or '')
                        item.delete()
                        _sync_request_status(request_obj, keep_queue=True)
                        if not request_obj.items.exists():
                            if request_obj.status == 'pending_manager_approval':
                                # درخواست در صف مدیرعامل است: حذف نمی‌شود تا از
                                # داشبورد مدیرعامل ناپدید نشود.
                                messages.info(
                                    request,
                                    f'«{item_name}» حذف شد — درخواست بدون قلم ماند و در '
                                    'صف مدیرعامل باقی می‌ماند (مدیرعامل آن را تایید/رد کند).'
                                )
                            else:
                                request_obj.delete()
                                messages.info(request, f'«{item_name}» حذف شد (درخواست خالی بود و حذف شد).')
                        else:
                            messages.info(request, f'«{item_name}» حذف شد.')
            elif action == 'edit_item':
                item_id = request.POST.get('item_id', '')
                if item_id.strip().isdigit():
                    item = PurchaseItem.objects.filter(id=int(item_id)).first()
                    if item and (not cafe or item.request.cafe_id == cafe.id):
                        return redirect('update_purchase_request', cafe_code=cafe_code, request_id=item.request_id)
            elif action == 'edit_section':
                # ویرایش درجا: مقدار جدید کل بخش در همان صفحه — قلم‌های بخش به یک قلم تبدیل می‌شوند
                ids = [i for i in request.POST.get('item_ids', '').split(',') if i.strip().isdigit()]
                try:
                    new_qty = _parse_decimal(request.POST.get('new_qty', ''))
                except (TypeError, ValueError, ArithmeticError):
                    new_qty = None
                items = list(PurchaseItem.objects.filter(id__in=ids)) if ids else []
                if cafe:
                    items = [it for it in items if it.request.cafe_id == cafe.id]
                if new_qty is None or new_qty <= 0 or new_qty > 99999999:
                    messages.error(request, 'مقدار جدید نامعتبر است (باید عدد مثبت باشد)')
                elif not items:
                    messages.error(request, 'قلمی برای ویرایش پیدا نشد')
                else:
                    unsent = [it for it in items
                              if not it.officer_assignments.filter(status__in=ASSIGNMENT_ACTIVE_STATUSES).exists()]
                    if not unsent:
                        messages.error(request, 'همه قلم‌های این بخش قبلاً ارسال شده‌اند و قابل ویرایش نیستند')
                    else:
                        name = unsent[0].name or (unsent[0].product.name if unsent[0].product else '')
                        target = unsent[0]
                        target.quantity = new_qty
                        target.save()
                        affected = {target.request_id}
                        for it in unsent[1:]:
                            affected.add(it.request_id)
                            it.delete()
                        kept_in_queue = False
                        for rid in affected:
                            req = PurchaseRequest.objects.filter(id=rid).first()
                            if req:
                                _sync_request_status(req, keep_queue=True)
                                if not req.items.exists():
                                    if req.status == 'pending_manager_approval':
                                        # درخواست در صف مدیرعامل است: حذف نمی‌شود تا
                                        # از داشبورد مدیرعامل ناپدید نشود.
                                        kept_in_queue = True
                                    else:
                                        req.delete()
                        messages.success(request, f'«{name}» در این بخش به {new_qty} {target.unit or ""} تغییر کرد')
                        if kept_in_queue:
                            messages.info(request, 'درخواست خالی‌شده در صف مدیرعامل باقی ماند (از داشبورد مدیرعامل حذف نمی‌شود).')
            elif action == 'delete_section':
                # حذف همه قلم‌های یک بخش در ردیف کالا (فقط قلم‌های ارسال‌نشده)
                ids = [i for i in request.POST.get('item_ids', '').split(',') if i.strip().isdigit()]
                items = list(PurchaseItem.objects.filter(id__in=ids)) if ids else []
                if cafe:
                    items = [it for it in items if it.request.cafe_id == cafe.id]
                if not items:
                    messages.error(request, 'قلمی برای حذف پیدا نشد')
                else:
                    name = items[0].name or (items[0].product.name if items[0].product else '')
                    unsent = [it for it in items
                              if not it.officer_assignments.filter(status__in=ASSIGNMENT_ACTIVE_STATUSES).exists()]
                    sent_skipped = len(items) - len(unsent)
                    affected = {it.request_id for it in unsent}
                    for it in unsent:
                        it.delete()
                    kept_in_queue = False
                    for rid in affected:
                        req = PurchaseRequest.objects.filter(id=rid).first()
                        if req:
                            _sync_request_status(req, keep_queue=True)
                            if not req.items.exists():
                                if req.status == 'pending_manager_approval':
                                    # درخواست در صف مدیرعامل است: حذف نمی‌شود تا
                                    # از داشبورد مدیرعامل ناپدید نشود.
                                    kept_in_queue = True
                                else:
                                    req.delete()
                    if unsent:
                        messages.info(request, f'«{name}» حذف شد ({len(unsent)} قلم)')
                    if sent_skipped:
                        messages.info(request, f'{sent_skipped} قلم قبلاً ارسال شده بودند و حذف نشدند')
                    if kept_in_queue:
                        messages.info(request, 'درخواست خالی‌شده در صف مدیرعامل باقی ماند (از داشبورد مدیرعامل حذف نمی‌شود).')
        redirect_url = reverse('all_departments_requests', kwargs={'cafe_code': cafe_code})
        params = {'type': request_type}
        if date_str:
            params['date'] = date_str
        if section_filter:
            params['section'] = section_filter
        return redirect(redirect_url + '?' + urlencode(params))

    # Determine selected date (default to today)
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            selected_date = datetime.now().date()
    else:
        selected_date = datetime.now().date()
        date_str = selected_date.isoformat()

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    DEPT_NAMES = {
        'kitchen':   'آشپزخانه',
        'bar':       'بار',
        'counter':   'کانتر',
        'cashier':   'صندوق',
        'facilities':'تاسیسات',
        'store':     'انبار',
        'purchasing':'خرید',
        'warehouse': 'انبارداری',
        'manager':   'مدیر',
        'admin':     'ادمین',
        'ceo':       'مدیرعامل',
        'accounting':'حسابداری',
        'finance_manager': 'مدیر مالی',
    }

    if request_type == 'warehouse':
        requests_qs = (
            WarehouseRequest.objects
            .select_related('user', 'user__cafe')
            .prefetch_related('items', 'items__product')
        )
    else:
        requests_qs = (
            PurchaseRequest.objects
            .select_related('user', 'user__cafe')
            .prefetch_related('items', 'items__product', 'items__officer_assignments')
            # درخواست خریدِ مسئول انبار بعد از تایید مدیرعامل برای «ثبت سفارش خرید»
            # به واحد خرید می‌رود — این صفحه فقط اقلام «درخواست کالا» را پخش می‌کند
            .exclude(source='warehouse_purchase')
        )

    if cafe:
        requests_qs = requests_qs.filter(cafe=cafe)

    # Filter by selected date
    requests_qs = requests_qs.filter(created_at__date=selected_date)

    requests_list = list(requests_qs.order_by('-created_at'))

    # ---- Merge similar products (used for the legacy warehouse type) ----
    product_map = defaultdict(lambda: {
        'display_name': '',
        'total_qty': 0.0,
        'unit': '',
        'departments': defaultdict(float),
    })

    for req in requests_list:
        if getattr(req.user, 'role', '') == 'requester' and req.user.department:
            dept_name = req.user.department.name
        else:
            dept_name = DEPT_NAMES.get(getattr(req.user, 'role', ''), getattr(req.user, 'role', '—'))
        for item in req.items.all():
            # Normalise key: use product id when available, else lower-cased name
            if hasattr(item, 'product') and item.product_id:
                key = f"product_{item.product_id}"
                display_name = item.product.name
            else:
                raw_name = (item.name if hasattr(item, 'name') else '') or ''
                key = raw_name.strip().lower()
                display_name = raw_name.strip().title()

            unit_name = ''
            if hasattr(item, 'unit') and item.unit:
                # unit is a CharField on PurchaseItem/WarehouseRequestItem
                if hasattr(item.unit, 'name'):
                    unit_name = item.unit.name   # FK to Unit
                else:
                    unit_name = str(item.unit)   # plain string

            qty = float(item.quantity) if item.quantity else 0.0

            product_map[key]['display_name'] = display_name
            product_map[key]['total_qty'] += qty
            if not product_map[key]['unit'] and unit_name:
                product_map[key]['unit'] = unit_name
            product_map[key]['departments'][dept_name] += qty

    merged_products = []
    for _key, data in product_map.items():
        dept_list = [
            {'name': d, 'qty': round(q, 2)}
            for d, q in sorted(data['departments'].items())
        ]
        merged_products.append({
            'name': data['display_name'],
            'total_qty': round(data['total_qty'], 2),
            'unit': data['unit'],
            'departments': dept_list,
            'dept_count': len(dept_list),
        })
    # Sort: products wanted by more departments first, then by total qty
    merged_products.sort(key=lambda x: (-x['dept_count'], -x['total_qty']))

    # ---- Rows for the purchase type (aggregated, per-section breakdown) ----
    rows = []
    row_sections_names = []
    if request_type == 'purchase':
        rows_map = defaultdict(lambda: {
            'name': '', 'unit': '', 'total_qty': 0.0,
            'sections': defaultdict(list),   # dept -> [entry]
            'total_items': 0, 'sent_items': 0,
        })
        for req in requests_list:
            dept_name = _item_section_name(req.user)
            for item in req.items.all():
                # کالای مشترک بین چند بخش در یک ردیف جمع می‌شود؛ سهم هر بخش
                # در همان ردیف (تگ بخش + مقدار) نمایش داده می‌شود
                key = _product_key(item)
                row = rows_map[key]
                row['name'] = item.product.name if item.product else (item.name or '').strip()
                if not row['unit']:
                    row['unit'] = item.unit or ''
                qty = float(item.quantity or 0)
                row['total_qty'] += qty
                assigns = list(item.officer_assignments.all())
                has_active = any(a.status in ASSIGNMENT_ACTIVE_STATUSES for a in assigns)
                has_returned = any(a.status in ASSIGNMENT_RETURNED_STATUSES for a in assigns)
                row['total_items'] += 1
                if has_active:
                    row['sent_items'] += 1
                row['sections'][dept_name].append({
                    'item_id': item.id,
                    'request_id': item.request_id,
                    'name': row['name'],
                    'qty': qty,
                    'unit': item.unit or '',
                    'sent': has_active,
                    'returned': has_returned,
                })

        for key, data in rows_map.items():
            sections = []
            for dept, entries in data['sections'].items():
                item_ids = ','.join(str(e['item_id']) for e in entries)
                sections.append({
                    'dept': dept,
                    'qty': round(sum(e['qty'] for e in entries), 2),
                    'item_ids': item_ids,
                    'entries': entries,
                    'all_sent': all(e['sent'] for e in entries),
                    'any_sent': any(e['sent'] for e in entries),
                    'any_returned': any(e['returned'] for e in entries),
                })
            sections.sort(key=lambda s: s['dept'])
            # همه قلم‌های ردیف با هم — یک باکس انتخاب برای کل کالا
            all_item_ids = ','.join(str(e['item_id']) for s in sections for e in s['entries'])
            rows.append({
                'name': data['name'],
                'unit': data['unit'],
                'total_qty': round(data['total_qty'], 2),
                'all_item_ids': all_item_ids,
                'sections': sections,
                'total_items': data['total_items'],
                'sent_items': data['sent_items'],
                'all_sent': data['sent_items'] > 0 and data['sent_items'] == data['total_items'],
                'any_sent': data['sent_items'] > 0,
                'dept_count': len(sections),
            })
        # Sort: unsent rows first, then by how many sections requested, then qty
        rows.sort(key=lambda r: (r['all_sent'], -r['dept_count'], -r['total_qty']))
        row_sections_names = sorted({
            dept for r in rows for s in r['sections'] for dept in [s['dept']]
        })

        # ── فیلتر بخش: فقط همان بخش نمایش داده می‌شود ──
        if section_filter:
            filtered = []
            for r in rows:
                secs = [s for s in r['sections'] if s['dept'] == section_filter]
                if not secs:
                    continue
                qty = sum(s['qty'] for s in secs)
                total_items = sum(len(s['entries']) for s in secs)
                sent_items = sum(1 for s in secs for e in s['entries'] if e['sent'])
                filtered.append({
                    **r,
                    'sections': secs,
                    'total_qty': round(qty, 2),
                    'total_items': total_items,
                    'sent_items': sent_items,
                    'all_sent': sent_items > 0 and sent_items == total_items,
                    'any_sent': sent_items > 0,
                    'dept_count': 1,
                    'all_item_ids': ','.join(str(e['item_id']) for s in secs for e in s['entries']),
                })
            filtered.sort(key=lambda r: (r['all_sent'], -r['total_qty']))
            rows = filtered

    # ── مسئولان خرید/انبار برای فرم ارسال ──
    officer_users = []
    seen_ids = set()
    for role in ('purchasing', 'warehouse'):
        for u in _officer_users(cafe, role):
            if u.id not in seen_ids:
                seen_ids.add(u.id)
                officer_users.append({'user': u, 'label': _officer_role_label(u)})

    active_departments = len(set(
        getattr(r.user, 'role', '') for r in requests_list if r.user
    ))
    pending_count = sum(1 for r in requests_list if r.status == 'pending')

    return render(request, 'all_departments_requests.html', {
        'requests': requests_list,
        'merged_products': merged_products,
        'request_type': request_type,
        'total_requests': len(requests_list),
        'pending_count': pending_count,
        'active_departments': active_departments,
        'cafe_code': cafe_code,
        'selected_date': selected_date,
        'prev_date': prev_date,
        'next_date': next_date,
        'date_str': date_str,
        'all_departments': Department.objects.filter(is_active=True, cafe=cafe).order_by('name'),
        # جریان جدید (نوع purchase)
        'rows': rows,
        'row_sections': row_sections_names,
        'section_filter': section_filter,
        'officer_users': officer_users,
    })


# ---------------------------------------------------------------------------
# مدیریت درخواست‌ها (داشبورد ادمین) — لیست خرید ارسالی به مسئولان خرید + CSV
# ---------------------------------------------------------------------------

@custom_login_required
@role_required(['admin', 'manager', 'ceo'])
def request_management_dashboard(request: HttpRequest) -> HttpResponse:
    """صفحه «مدیریت درخواست‌ها» در داشبورد ادمین.

    - لیست خرید: اقلام درخواستی هر روز که برای مسئولان خرید ارسال شده‌اند؛
      با فیلتر تاریخ و جابه‌جایی روز قبل/بعد (مثل صفحه «درخواست کالا» مدیرعامل).
    - اقلامی که به داشبورد مدیرعامل برگشت خورده‌اند نمایش داده نمی‌شوند.
    - لیست انبار: بخش در تسک بعدی؛ فعلاً جای‌خالی نمایش داده می‌شود.
    """
    from datetime import datetime, timedelta

    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    tab = request.GET.get('tab', 'purchase')  # purchase | warehouse

    # تاریخ انتخاب‌شده (پیش‌فرض: امروز)
    date_str = request.GET.get('date', '')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            selected_date = datetime.now().date()
    else:
        selected_date = datetime.now().date()
        date_str = selected_date.isoformat()

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    # ── لیست خرید: اقلام ارسالی به مسئولان خرید در روز انتخاب‌شده ──
    assignments = (
        OfficerAssignment.objects
        .filter(
            target_role='purchasing',
            item__request__created_at__date=selected_date,
        )
        .exclude(status__in=ASSIGNMENT_RETURNED_STATUSES)
        .select_related('item', 'item__request', 'item__request__user', 'assigned_to')
        .order_by('item__request__created_at', 'item__request_id', 'item_id')
    )
    if cafe:
        assignments = assignments.filter(item__request__cafe=cafe)

    rows = []
    for a in assignments:
        item = a.item
        req = item.request
        requester = req.user if req.user else None
        rows.append({
            'item_id': item.id,
            'request_number': req.request_number,
            'name': item.product.name if item.product else item.name,
            'quantity': item.quantity,
            'unit': item.unit,
            'section': _item_section_name(requester),
            'requester': (requester.get_full_name() or requester.username) if requester else '—',
            'officer': (a.assigned_to.get_full_name() or a.assigned_to.username) if a.assigned_to else 'همه مسئولان خرید',
            'status': a.status,
            'status_display': a.get_status_display(),
            'assigned_at': a.assigned_at,
        })

    # ── لیست انبار: اقلام تحویل‌شده از انبار در روز انتخاب‌شده ──
    wh_rows = []
    if tab == 'warehouse':
        from collections import defaultdict
        wh_assignments = (
            OfficerAssignment.objects
            .filter(
                target_role='warehouse',
                status='fulfilled',
                item__request__created_at__date=selected_date,
            )
            .select_related(
                'item', 'item__request', 'item__request__user',
                'stock_item',
            )
            .order_by('item__request__created_at', 'item__request_id', 'item_id')
        )
        if cafe:
            wh_assignments = wh_assignments.filter(item__request__cafe=cafe)

        # هر قلم درخواست یک ردیف؛ چند تخصیص تحویلی با هم جمع می‌شوند
        wh_map = defaultdict(lambda: {
            'name': '', 'unit': '', 'requested_qty': 0.0, 'supplied_qty': 0.0,
            'section': '', 'requester': '', 'description': '',
        })
        for a in wh_assignments:
            item = a.item
            req = item.request
            requester = req.user if req.user else None
            row = wh_map[item.id]
            row['name'] = item.product.name if item.product else item.name
            row['unit'] = item.unit or ''
            row['requested_qty'] = float(item.quantity or 0)
            row['supplied_qty'] += float(a.supplied_quantity or 0)
            row['section'] = _item_section_name(requester)
            row['requester'] = (requester.get_full_name() or requester.username) if requester else '—'
            if item.description and item.description not in row['description']:
                row['description'] = item.description

        wh_rows = []
        for item_id, data in wh_map.items():
            wh_rows.append({
                'item_id': item_id,
                **data,
                'supplied_qty': round(data['supplied_qty'], 2),
            })
        wh_rows.sort(key=lambda r: (r['section'], r['name']))

    return render(request, 'request_management.html', {
        'tab': tab,
        'rows': rows,
        'total_items': len(rows),
        'wh_rows': wh_rows,
        'wh_total_items': len(wh_rows),
        'cafe_code': cafe_code,
        'selected_date': selected_date,
        'prev_date': prev_date,
        'next_date': next_date,
        'date_str': date_str,
        'user': request.user,
    })


@custom_login_required
@role_required(['admin', 'manager', 'ceo'])
def request_management_csv(request: HttpRequest) -> HttpResponse:
    """دانلود CSV درخواست‌های خرید روز انتخاب‌شده (اقلام ارسالی به مسئولان خرید).

    ستون‌ها: کالای درخواست‌شده، مقدار، واحد، بخش درخواست‌دهنده، درخواست‌دهنده،
    مسئول خرید، وضعیت. اقلام برگشت‌خورده به داشبورد مدیرعامل در فایل نیستند.
    """
    from datetime import datetime
    import csv

    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    date_str = request.GET.get('date', '')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            selected_date = datetime.now().date()
    else:
        selected_date = datetime.now().date()

    assignments = (
        OfficerAssignment.objects
        .filter(
            target_role='purchasing',
            item__request__created_at__date=selected_date,
        )
        .exclude(status__in=ASSIGNMENT_RETURNED_STATUSES)
        .select_related('item', 'item__request', 'item__request__user', 'assigned_to')
        .order_by('item__request__created_at', 'item__request_id', 'item_id')
    )
    if cafe:
        assignments = assignments.filter(item__request__cafe=cafe)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchase_requests_{selected_date.isoformat()}.csv"'
    response.write('﻿')  # BOM برای باز شدن صحیح در اکسل

    writer = csv.writer(response)
    writer.writerow(['کالای درخواست‌شده', 'مقدار', 'واحد', 'بخش درخواست‌دهنده', 'درخواست‌دهنده', 'مسئول خرید', 'وضعیت'])
    for a in assignments:
        item = a.item
        req = item.request
        requester = req.user if req.user else None
        writer.writerow([
            item.product.name if item.product else item.name,
            item.quantity,
            item.unit,
            _item_section_name(requester),
            (requester.get_full_name() or requester.username) if requester else '—',
            (a.assigned_to.get_full_name() or a.assigned_to.username) if a.assigned_to else 'همه مسئولان خرید',
            a.get_status_display(),
        ])

    return response


@custom_login_required
@role_required(['admin', 'manager', 'ceo'])
def request_management_warehouse_pdf(request: HttpRequest) -> HttpResponse:
    """خروجی PDF «درخواست کالا از انبار» برای روز انتخاب‌شده.

    - برگه A5 افقی (لنداسکیپ)
    - بالای برگه: عنوان، نام کافه و تاریخ روز درخواست‌ها
    - جدول: ردیف / مرکز هزینه / کالا (نام عمومی) / شرح کالا (نام تجاری) /
      مقدار و واحد درخواستی / مقدار و واحد تحویلی / توضیحات
      (مقدار تحویلی چند برند تجاری با هم جمع شده و از موجودی انبار کم شده است)
    - پایین برگه: محل امضای درخواست‌کننده، تحویل‌گیرنده و مسئول انبار
    """
    from datetime import datetime
    from io import BytesIO
    import os
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Table, TableStyle
    import jdatetime
    import arabic_reshaper
    from bidi.algorithm import get_display

    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    date_str = request.GET.get('date', '')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            selected_date = datetime.now().date()
    else:
        selected_date = datetime.now().date()

    # اقلام تحویل‌شده از انبار
    from collections import defaultdict
    wh_assignments = (
        OfficerAssignment.objects
        .filter(
            target_role='warehouse',
            status='fulfilled',
            item__request__created_at__date=selected_date,
        )
        .select_related(
            'item', 'item__request', 'item__request__user',
            'stock_item',
        )
        .order_by('item__request__created_at', 'item__request_id', 'item_id')
    )
    if cafe:
        wh_assignments = wh_assignments.filter(item__request__cafe=cafe)

    wh_map = defaultdict(lambda: {
        'name': '', 'unit': '', 'requested_qty': 0.0, 'supplied_qty': 0.0,
        'section': '', 'description': '',
    })
    for a in wh_assignments:
        item = a.item
        req = item.request
        requester = req.user if req.user else None
        row = wh_map[item.id]
        row['name'] = item.product.name if item.product else item.name
        row['unit'] = item.unit or ''
        row['requested_qty'] = float(item.quantity or 0)
        row['supplied_qty'] += float(a.supplied_quantity or 0)
        row['section'] = _item_section_name(requester)
        if item.description and item.description not in row['description']:
            row['description'] = item.description

    rows = []
    for item_id, data in wh_map.items():
        rows.append({
            'item_id': item_id,
            **data,
            'supplied_qty': round(data['supplied_qty'], 2),
        })
    rows.sort(key=lambda r: (r['section'], r['name']))

    # ── ساخت PDF — A5 افقی ──
    buffer = BytesIO()
    page_width, page_height = A5[1], A5[0]  # landscape
    c = canvas.Canvas(buffer, pagesize=(page_width, page_height))

    # فونت فارسی
    font_name = 'Helvetica'
    try:
        from django.conf import settings
        font_path = os.path.join(settings.BASE_DIR, 'frontend', 'static', 'fonts', 'Vazir.ttf')
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('Vazir', font_path))
            font_name = 'Vazir'
    except Exception:
        pass

    def persian(text):
        """بازآرایی متن فارسی برای نمایش صحیح در PDF"""
        if not text:
            return ''
        return get_display(arabic_reshaper.reshape(str(text)))

    margin = 1.2 * cm
    content_width = page_width - 2 * margin

    # ── بالای برگه (راست به چپ): عنوان وسط + تاریخ شمسی گوشه چپ همان ردیف ──
    jalali = jdatetime.date.fromgregorian(date=selected_date)
    jalali_months = {
        1: 'فروردین', 2: 'اردیبهشت', 3: 'خرداد', 4: 'تیر',
        5: 'مرداد', 6: 'شهریور', 7: 'مهر', 8: 'آبان',
        9: 'آذر', 10: 'دی', 11: 'بهمن', 12: 'اسفند',
    }
    jalali_str = f"{jalali.day} {jalali_months[jalali.month]} {jalali.year}"

    cafe_name = cafe.name if cafe else ''
    title = persian(f'درخواست کالا از انبار ({cafe_name})' if cafe_name else 'درخواست کالا از انبار')
    title_y = page_height - margin
    c.setFont(font_name, 14)
    c.drawString((page_width - c.stringWidth(title, font_name, 14)) / 2, title_y, title)
    # فقط تاریخ شمسی، بدون کلمهٔ «تاریخ» — گوشه چپ روبه‌روی عنوان
    c.setFont(font_name, 10)
    c.drawString(margin, title_y, persian(jalali_str))
    y = title_y - 0.9 * cm  # شروع جدول

    # ── جدول (راست به چپ: اولین ستون سمت راست صفحه است) ──
    logical_cols = ['ردیف', 'مرکز هزینه', 'کالا',
                    'مقدار و واحد درخواستی', 'مقدار و واحد تحویلی', 'توضیحات']
    header_rev = list(reversed(logical_cols))
    data = [[persian(h) for h in header_rev]]
    for i, r in enumerate(rows, start=1):
        row = [
            persian(str(i)),
            persian(r['section']),
            persian(r['name']),
            persian(f"{r['requested_qty']:.2f} {r['unit']}".strip()),
            persian(f"{r['supplied_qty']:.2f} {r['unit']}".strip()),
            persian(r['description']),
        ]
        data.append(list(reversed(row)))

    # ── عرض ستون‌ها: اندازهٔ بزرگترین رشتهٔ همان ستون، نه کمتر از مقدار
    # پیش‌فرض، با سقف برای بیرون نزدن از صفحه؛ اگر جا اضافه بود همان مقادیر قبلی ──
    fixed_cm = [0.9, 3.0, 4.0, 2.8, 2.8, 4.2]
    fixed_rev = list(reversed(fixed_cm))
    col_widths = []
    for col in range(len(header_rev)):
        longest = 0.0
        for cell in data:
            for line in str(cell[col]).split('\n'):
                longest = max(longest, c.stringWidth(line, font_name, 7.5))
        w = max(fixed_rev[col] * cm, longest + 0.35 * cm)
        col_widths.append(min(w, fixed_rev[col] * cm * 1.9))  # سقف هر ستون
    if sum(col_widths) > content_width:
        factor = content_width / sum(col_widths)
        col_widths = [w * factor for w in col_widths]

    # ── ارتفاع ردیف‌ها ──
    sign_area_top = page_height * 0.12 + 0.4 * cm  # جدول بالای نوار ۸۸٪ پایانی
    avail_height = y - sign_area_top
    base_row = avail_height / 13  # ۱۲ ردیف کالا + ۱ ردیف هدر
    row_heights = [base_row] * (len(rows) + 1)

    table_style = TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef0ff')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cccccc')),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),   # راست به چپ
        ('ALIGN', (1, 0), (2, -1), 'CENTER'),
        ('ALIGN', (5, 0), (5, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ])
    table = Table(data, colWidths=col_widths, rowHeights=row_heights, repeatRows=1)
    table.setStyle(table_style)
    table_h = sum(row_heights)
    if table_h > avail_height:
        # محدودیت بیرون نزدن: ارتفاع ردیف‌ها به تناسب کم می‌شود (با حداقل)
        factor = avail_height / table_h
        row_heights = [max(h * factor, 0.55 * cm) for h in row_heights]
        table = Table(data, colWidths=col_widths, rowHeights=row_heights, repeatRows=1)
        table.setStyle(table_style)
        table_h = sum(row_heights)
    pieces = [table]
    if table_h > avail_height:
        # با حداقل ارتفاع هم جا نشد → جدول به صفحهٔ دوم می‌رود
        pieces = table.split(content_width, avail_height) or [table]

    for pi, piece in enumerate(pieces):
        if pi > 0:
            c.showPage()
        pw, ph = piece.wrap(0, 0)
        piece.drawOn(c, margin, max(sign_area_top, y - ph))

    # ── امضاها (آخرین صفحه): پایین‌تر از ۸۸٪ ارتفاع برگه، بدون باکس، جدا از هم ──
    c.setFont(font_name, 9)
    sig_y = page_height * 0.09  # داخل نوار پایینی (پایین‌تر از خط ۸۸٪)
    sig_labels = ['امضای درخواست‌کننده', 'امضای تحویل‌گیرنده', 'امضای مسئول انبار']
    third = content_width / 3
    for idx, label in enumerate(sig_labels):
        # راست به چپ: امضای اول سمت راست
        x_right = margin + content_width - idx * third - 0.4 * cm
        c.drawRightString(x_right, sig_y, persian(label))

    c.showPage()
    c.save()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="warehouse_request_{selected_date.isoformat()}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Quick-add product API (used from within purchase/warehouse request forms)
# ---------------------------------------------------------------------------

@custom_login_required
def quick_add_product(request):
    """Create a new Product on-the-fly from the request form.

    POST JSON body: {"name": "...", "unit": "...", "description": "..."}
    Returns JSON: {"success": true, "product": {"id": ..., "name": "...", "unit": "..."}}

    کالای ساخته‌شده همیشه در دستهٔ «تحقیق و توسعه» کافه قرار می‌گیرد تا مدیریت
    آن را بررسی و در صورت تایید به دستهٔ واقعی منتقل کند.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'درخواست نامعتبر است'}, status=400)

    name = data.get('name', '').strip()
    unit_name = data.get('unit', 'عدد').strip()
    usage_location = data.get('usage_location', '').strip()
    description = data.get('description', '').strip()

    if not name:
        return JsonResponse({'success': False, 'error': 'نام کالا الزامی است'})

    # توضیحات برای کالای جدید اجباری است
    if not description:
        return JsonResponse({
            'success': False,
            'error': 'توضیحات اجباری است — لطفاً توضیح کوتاهی بنویس.',
            'error_code': 'description_required',
        })

    cafe = get_cafe_from_request(request)

    # Auto-detect usage_location from user's department if not provided
    if not usage_location:
        if request.user.role == 'requester' and request.user.department:
            usage_location = request.user.department.name
        else:
            usage_location = 'general'

    # Return existing product if same name already exists
    existing_qs = Product.objects.filter(name__iexact=name)
    if cafe:
        existing_qs = existing_qs.filter(cafe=cafe)
    if existing_qs.exists():
        p = existing_qs.select_related('category', 'unit').first()
        inactive_err = inactive_product_error(p)
        if inactive_err:
            return JsonResponse({'success': False, 'error': inactive_err})
        if not bypasses_recipe_gate(request.user):
            gate_err = recipe_gate_error(p, cafe=cafe)
            if gate_err:
                return JsonResponse({'success': False, 'error': gate_err})
        if p.category_id is None and not p.is_complete:
            assign_rd_category(p, cafe=cafe)
        return JsonResponse({'success': True, 'product': {
            'id': p.id,
            'name': p.name,
            'unit': p.unit.name if p.unit else unit_name,
            'is_complete': p.is_complete,
            'category_id': p.category_id,
            'category_name': p.category.name if p.category else '',
        }})

    # Find or create the unit
    unit_obj = None
    if unit_name:
        unit_qs = Unit.objects.filter(name=unit_name)
        if cafe:
            unit_qs = unit_qs.filter(cafe=cafe)
        unit_obj = unit_qs.first()
        if not unit_obj:
            unit_obj = Unit.objects.create(name=unit_name, cafe=cafe)

    product = Product.objects.create(
        name=name,
        unit=unit_obj,
        usage_location=usage_location,
        description=description,
        # کالای جدید ناقص است تا مدیریت محصولات فیلدهای دیگر (دسته‌بندی، برند، ...) را تکمیل کند
        is_complete=False,
        cafe=cafe,
        is_active=True,
    )

    # دستهٔ انتخابیِ کاربر عمداً نادیده گرفته می‌شود: هر کالایی که درخواست‌کننده
    # بیرون از فهرست تعریف می‌کند، تا بررسی مدیریت در «تحقیق و توسعه» می‌ماند.
    rd_category = assign_rd_category(product, cafe=cafe)

    return JsonResponse({'success': True, 'product': {
        'id': product.id,
        'name': product.name,
        'unit': product.unit.name if product.unit else unit_name,
        'is_complete': product.is_complete,
        'description': product.description,
        'category_id': rd_category.id if rd_category else None,
        'category_name': rd_category.name if rd_category else '',
    }})


@custom_login_required
@role_required(["manager"])
def quick_add_department(request):
    """Create a new Department on-the-fly from the user form.

    POST JSON body: {"name": "..."}
    Returns JSON: {"success": true, "department": {"id": ..., "name": "..."}}
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'درخواست نامعتبر است'}, status=400)

    name = data.get('name', '').strip()

    if not name:
        return JsonResponse({'success': False, 'error': 'نام بخش الزامی است'})

    cafe = get_cafe_from_request(request)

    # بدون کافه جاری امکان ساخت بخش نیست (بخش سراسری وجود ندارد)
    if not cafe:
        return JsonResponse({'success': False, 'error': 'ابتدا کافه را انتخاب کنید'})

    # Return existing department if same name already exists
    existing_qs = Department.objects.filter(name__iexact=name, cafe=cafe)
    if existing_qs.exists():
        d = existing_qs.first()
        return JsonResponse({'success': True, 'department': {
            'id': d.id,
            'name': d.name,
        }})

    department = Department.objects.create(
        name=name,
        cafe=cafe,
        is_active=True,
    )

    return JsonResponse({'success': True, 'department': {
        'id': department.id,
        'name': department.name,
    }})
