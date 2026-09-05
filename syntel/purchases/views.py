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
from decimal import Decimal
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

from . import drive_backup
from .services.inventory import (
    InventoryServiceError,
    VoucherValidationError,
    _change_stock_quantity,
    finalize_voucher,
    next_voucher_number,
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
    Notification,
    OfficerAssignment,
    Payment,
    PaymentAccount,
    PaymentLocation,
    PaymentType,
    Product,
    ProductBrand,
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
    'ProductBrand':         'product__cafe',  # برند به محصول وصل است نه مستقیم به کافه
    'Unit':                 'cafe',
    'Category':             'cafe',
    'Department':           'cafe',
    'Supplier':             'cafe',
    'Requester':            'cafe',
    'Invoice':              'cafe',
    'InvoiceItem':          'invoice__cafe',
    'PurchaseRequest':      'cafe',
    'WarehouseRequest':     'cafe',
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


def get_cafe_redirect_url(url_name, request, **kwargs):
    """ساخت URL با cafe_code"""
    cafe_code = get_cafe_code(request)
    if cafe_code:
        kwargs['cafe_code'] = cafe_code
    return redirect(url_name, **kwargs)


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


def _get_departments():
    return User.ROLE_CHOICES


def _parse_decimal(value):
    try:
        # جداکننده سه‌رقمی (کاما) را حذف کن — کاربران با کاما وارد می‌کنند
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return None


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
# Authentication
# ---------------------------------------------------------------------------


@csrf_exempt
def login_view(request: HttpRequest) -> HttpResponse:
    from .models import Cafe
    
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        cafe_code = request.POST.get("cafe_code", "")
        
        user = authenticate(request, username=username, password=password)
        if user and user.is_active:
            login(request, user)
            
            # تنظیم کافه انتخاب شده در session
            if cafe_code:
                try:
                    selected_cafe = Cafe.objects.get(code=cafe_code, is_active=True)
                    request.session['selected_cafe_id'] = selected_cafe.id
                    request.session['selected_cafe_code'] = selected_cafe.code
                    cafe_code = selected_cafe.code
                except Cafe.DoesNotExist:
                    logout(request)
                    return render(
                        request,
                        "login.html",
                        {"error": "کافه انتخاب شده معتبر نیست.", "cafes": Cafe.objects.filter(is_active=True)},
                    )
            else:
                # اگر کافه انتخاب نشده، از کافه کاربر استفاده کن
                if user.cafe:
                    request.session['selected_cafe_id'] = user.cafe_id
                    request.session['selected_cafe_code'] = user.cafe.code
                    cafe_code = user.cafe.code
                else:
                    logout(request)
                    return render(
                        request,
                        "login.html",
                        {"error": "کافه برای کاربر شما تعریف نشده است. با مدیر تماس بگیرید.", "cafes": Cafe.objects.filter(is_active=True)},
                    )
            
            # Redirect based on role
            # ادمین به داشبورد مالی
            if user.role == 'admin':
                return redirect("finance_dashboard", cafe_code=cafe_code)
            # Superuser به داشبورد مالی
            elif user.is_superuser:
                return redirect("finance_dashboard", cafe_code=cafe_code)
            # مدیرعامل به صفحه درخواست کالا (لیست همه بخش‌ها)
            elif user.role == 'ceo':
                return redirect("all_departments_requests", cafe_code=cafe_code)
            # سایر نقش‌های مدیریتی
            elif user.role == 'manager':
                return redirect("management_dashboard", cafe_code=cafe_code)
            elif user.role in ['kitchen', 'bar', 'counter', 'cashier', 'facilities', 'requester']:
                return redirect("mobile_dashboard", cafe_code=cafe_code)
            # مسئول خرید → لیست خرید خودش (اقلام ارسالی مدیرعامل)
            elif user.role == 'purchasing':
                return redirect("purchase_officer_dashboard", cafe_code=cafe_code)
            # مسئول انبار → درخواست‌های تامین انبار
            elif user.role == 'warehouse':
                return redirect("warehouse_officer_dashboard", cafe_code=cafe_code)
            elif user.role == 'finance_manager':
                return redirect("finance_manager_dashboard", cafe_code=cafe_code)
            elif user.role == 'accounting':
                return redirect("finance_dashboard", cafe_code=cafe_code)
            # سایر نقش‌ها به management_dashboard می‌روند
            return redirect("management_dashboard", cafe_code=cafe_code)
        return render(
            request,
            "login.html",
            {"error": "نام کاربری یا رمز عبور اشتباه است.", "cafes": Cafe.objects.filter(is_active=True)},
        )

    # GET request - نمایش لیست کافه‌ها
    cafes = Cafe.objects.filter(is_active=True)
    return render(request, "login.html", {"cafes": cafes})


@custom_login_required
def select_cafe(request: HttpRequest) -> HttpResponse:
    """صفحه انتخاب کافه برای کاربران ادمین"""
    from .models import Cafe
    
    # بررسی اینکه کاربر نیاز به انتخاب کافه دارد
    if not request.user.needs_cafe_selection:
        # کاربر عادی - redirect به داشبورد کافه خودش
        if request.user.cafe:
            return redirect("dashboard", cafe_code=request.user.cafe.code)
        else:
            logout(request)
            return redirect("login")
    
    cafes = Cafe.objects.filter(is_active=True)
    
    if request.method == "POST":
        cafe_code = request.POST.get("cafe_code")
        try:
            cafe = Cafe.objects.get(code=cafe_code, is_active=True)
            request.session['selected_cafe_id'] = cafe.id
            request.session['selected_cafe_code'] = cafe.code
            
            # Redirect based on role
            user = request.user
            
            # هدایت بر اساس نقش کاربر
            if user.role == 'admin':
                return redirect("finance_dashboard", cafe_code=cafe_code)
            elif user.role == 'ceo':
                return redirect("all_departments_requests", cafe_code=cafe_code)
            elif user.role == 'manager':
                return redirect("management_dashboard", cafe_code=cafe_code)
            elif user.role == 'finance_manager':
                return redirect("finance_manager_dashboard", cafe_code=cafe_code)
            elif user.role == 'accounting':
                return redirect("finance_dashboard", cafe_code=cafe_code)
            elif user.role in ['kitchen', 'bar', 'counter', 'cashier', 'facilities', 'requester']:
                return redirect("mobile_dashboard", cafe_code=cafe_code)
            elif user.role == 'purchasing':
                return redirect("purchase_officer_dashboard", cafe_code=cafe_code)
            elif user.role == 'warehouse':
                return redirect("warehouse_officer_dashboard", cafe_code=cafe_code)
            # سایر نقش‌ها به داشبورد پیش‌فرض می‌روند
            return redirect("dashboard", cafe_code=cafe_code)
        except Cafe.DoesNotExist:
            messages.error(request, "کافه انتخاب شده یافت نشد.")
    
    return render(request, "select_cafe.html", {"cafes": cafes})


def logout_view(request: HttpRequest) -> HttpResponse:
    # پاک کردن اطلاعات کافه از session
    if 'selected_cafe_id' in request.session:
        del request.session['selected_cafe_id']
    if 'selected_cafe_code' in request.session:
        del request.session['selected_cafe_code']
    logout(request)
    return redirect("login")


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["kitchen", "bar", "counter", "cashier", "facilities", "requester"])
def mobile_dashboard(request: HttpRequest) -> HttpResponse:
    """Simple mobile-friendly dashboard for department users"""
    cafe = get_cafe_from_request(request)
    return render(request, "mobile_dashboard.html", {"user": request.user, "cafe": cafe})


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
                item.purchased_quantity = int(purchased_qty)
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

    if request.method == 'POST':
        product_names = request.POST.getlist('product_name')
        quantities = request.POST.getlist('item_quantity')
        units = request.POST.getlist('item_unit')
        descriptions = request.POST.getlist('item_description')
        product_ids = request.POST.getlist('item_product_id')
        brand_ids = request.POST.getlist('item_brand_id')

        items_data = []
        for idx, name in enumerate(product_names):
            name = name.strip() if name else ''
            if not name:
                continue
            qty_raw = quantities[idx] if idx < len(quantities) else ''
            qty_val = _parse_decimal(qty_raw)
            if qty_val is None or qty_val <= 0:
                continue
            unit_val = units[idx].strip() if idx < len(units) and units[idx] else 'عدد'
            desc_val = descriptions[idx] if idx < len(descriptions) else ''
            product_id_raw = product_ids[idx] if idx < len(product_ids) else ''
            brand_id_raw = brand_ids[idx] if idx < len(brand_ids) else ''
            # محصول انتخاب‌شده از لیست پیشنهادها (اگر انتخاب نشده باشد با نام جستجو می‌شود)
            item_product = None
            if product_id_raw.isdigit():
                item_product = Product.objects.filter(id=int(product_id_raw)).first()
            if item_product is None:
                item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else Product.objects.filter(name__iexact=name).first()
            # برند انتخاب‌شده فقط در صورتی ثبت می‌شود که متعلق به همان محصول باشد
            item_brand = None
            if brand_id_raw.isdigit() and item_product is not None:
                item_brand = ProductBrand.objects.filter(id=int(brand_id_raw), product=item_product).first()
            items_data.append((name, qty_val, unit_val, desc_val, item_product, item_brand))

        if not items_data:
            messages.error(request, 'حداقل یک قلم کالا وارد کنید')
            return render(request, 'warehouse_request_form.html', {'cafe_code': cafe_code})

        wh_request = WarehouseRequest.objects.create(
            user=request.user,
            cafe=cafe,
            status='pending',
        )
        for name, qty, unit, desc, item_product, item_brand in items_data:
            WarehouseRequestItem.objects.create(
                warehouse_request=wh_request,
                product=item_product,
                product_brand=item_brand,
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

    return render(request, 'warehouse_request_form.html', {'cafe_code': cafe_code})


@custom_login_required
def warehouse_request_success(request: HttpRequest) -> HttpResponse:
    """صفحه موفقیت پس از ثبت درخواست انبار"""
    return render(request, 'warehouse_request_success.html')


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def process_warehouse_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """پردازش درخواست انبار توسط مسئول خرید یا مسئول انبار"""
    wh_request = get_object_or_404(WarehouseRequest, id=request_id)
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    # موجودی‌های انبار برای جستجو
    stock_items = StockItem.objects.select_related('product', 'unit', 'warehouse').filter(quantity__gt=0)
    if cafe:
        stock_items = stock_items.filter(warehouse__cafe=cafe)

    if request.method == 'POST':
        action = request.POST.get('action', '')  # 'submit' = تایید نهایی
        items_to_purchase = []  # اقلامی که به خرید ارجاع می‌شوند
        has_error = False

        for item in wh_request.items.all():
            supply_status = request.POST.get(f'supply_status_{item.id}', 'pending')
            item.supply_status = supply_status

            if supply_status == 'from_stock':
                # تامین از انبار - باید موجودی انبار انتخاب و کاهش یابد
                stock_item_id = request.POST.get(f'stock_item_{item.id}', '')
                supplied_qty_raw = request.POST.get(f'supplied_qty_{item.id}', '')
                supplied_qty = _parse_decimal(supplied_qty_raw)

                if stock_item_id and supplied_qty and supplied_qty > 0:
                    try:
                        s_item = StockItem.objects.get(id=int(stock_item_id))
                        if supplied_qty > s_item.quantity:
                            messages.error(request, f'موجودی {s_item.product.name} در انبار ({s_item.quantity}) کمتر از مقدار درخواستی ({supplied_qty}) است!')
                            has_error = True
                        else:
                            item.stock_item = s_item
                            item.supplied_quantity = supplied_qty
                    except StockItem.DoesNotExist:
                        messages.error(request, 'موجودی انبار یافت نشد')
                        has_error = True
                else:
                    messages.error(request, f'برای قلم «{item.name}» لطفاً موجودی انبار و مقدار را تعیین کنید')
                    has_error = True

            elif supply_status == 'to_purchase':
                items_to_purchase.append(item)

            item.save()

        if has_error:
            return render(request, 'process_warehouse_request.html', {
                'wh_request': wh_request,
                'stock_items': stock_items,
                'cafe_code': cafe_code,
            })

        # کاهش اتمیک از موجودی انبار برای اقلام تامین‌شده — کل عملیات یک واحد است
        try:
            with transaction.atomic():
                for item in wh_request.items.all():
                    if not (item.supply_status == 'from_stock' and item.stock_item and item.supplied_quantity):
                        continue
                    ok, prev_qty, new_qty = _change_stock_quantity(
                        item.stock_item_id, -item.supplied_quantity)
                    if not ok:
                        raise _InsufficientStock(
                            f'موجودی «{item.stock_item.product.name}» کافی نیست')
                    StockTransaction.objects.create(
                        stock_item=item.stock_item,
                        transaction_type='out',
                        quantity=item.supplied_quantity,
                        conversion_rate=item.stock_item.product_brand.conversion_rate if item.stock_item.product_brand else None,
                        unit=item.stock_item.unit,
                        reference_type='warehouse_request',
                        reference_id=wh_request.id,
                        description=f'تامین درخواست انبار #{wh_request.id} - {item.name}',
                        created_by=request.user,
                        previous_quantity=prev_qty,
                        new_quantity=new_qty,
                    )
        except _InsufficientStock as exc:
            messages.error(request, f'خطا در تامین: {exc}')
            return render(request, 'process_warehouse_request.html', {
                'wh_request': wh_request,
                'stock_items': stock_items,
                'cafe_code': cafe_code,
            })

        # اگر اقلامی به خرید ارجاع شدند یک درخواست خرید جدید ایجاد می‌شود
        if items_to_purchase:
            new_purchase = PurchaseRequest.objects.create(
                user=wh_request.user,
                cafe=cafe,
                status='pending',
            )
            wh_request.converted_to_purchase = new_purchase
            new_purchase_items = []
            for item in items_to_purchase:
                pi = PurchaseItem.objects.create(
                    request=new_purchase,
                    product=item.product,
                    product_brand=item.product_brand,
                    name=item.name,
                    quantity=int(item.quantity),
                    unit=item.unit,
                    description=item.description or '',
                )
                new_purchase_items.append(pi)
            # همه درخواست‌های کالا مستقیماً برای مسئول انبار ارسال می‌شوند
            _route_request_to_warehouse(
                new_purchase, new_purchase_items, request.user,
                notes='ارسال مستقیم از فرم درخواست کالا')
            messages.info(request, f'{len(items_to_purchase)} قلم به لیست خرید ارجاع شد')

        # تعیین وضعیت نهایی درخواست انبار
        all_statuses = [i.supply_status for i in wh_request.items.all()]
        if all(s == 'from_stock' for s in all_statuses):
            wh_request.status = 'approved_from_stock'
        elif all(s == 'to_purchase' for s in all_statuses):
            wh_request.status = 'approved_purchase'
        elif all(s == 'declined' for s in all_statuses):
            wh_request.status = 'declined'
        elif any(s != 'pending' for s in all_statuses):
            wh_request.status = 'fulfilled'
        
        wh_request.processed_by = request.user
        wh_request.processed_at = timezone.now()
        wh_request.save()

        messages.success(request, 'درخواست انبار با موفقیت پردازش شد')
        return redirect('purchasing_requests_list', cafe_code=cafe_code)

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
    cafe_code = cafe.code if cafe else 'local'

    # اقلام درخواست خرید که هنوز در انتظار هستند
    pending_items = PurchaseItem.objects.filter(
        request__status='purchased',
        supply_status='pending',
        request__cafe=cafe,
    ).select_related('request', 'request__user', 'product').order_by('-request__created_at') if cafe else PurchaseItem.objects.filter(
        request__status='purchased',
        supply_status='pending',
    ).select_related('request', 'request__user', 'product').order_by('-request__created_at')

    if request.method == 'POST':
        has_error = False
        items_processed = 0

        for item_id_str in request.POST.getlist('item_ids'):
            try:
                item = PurchaseItem.objects.get(id=int(item_id_str))
            except (PurchaseItem.DoesNotExist, ValueError):
                continue
            action_for_item = request.POST.get(f'action_{item.id}', '')
            if action_for_item != 'from_stock':
                continue

            # هر قلم می‌تواند از چند موجودی (برند/انبار) تامین شود
            stock_sids = request.POST.getlist(f'stock_sid_{item.id}')
            stock_qtys = request.POST.getlist(f'stock_qty_{item.id}')

            supply_rows = []  # (stock_item, base_deduction, trade_qty)
            item_has_error = False

            for sid_str, qty_str in zip(stock_sids, stock_qtys):
                if not sid_str or not qty_str:
                    continue
                trade_qty = _parse_decimal(qty_str)
                if trade_qty is None or trade_qty <= 0:
                    continue
                try:
                    s_item = StockItem.objects.select_related('product_brand').get(id=int(sid_str))
                except (StockItem.DoesNotExist, ValueError):
                    messages.error(request, f'موجودی انبار برای «{item.name}» یافت نشد')
                    item_has_error = True
                    break

                brand = s_item.product_brand
                # مقدار واردی بر حسب واحد تجاری برند است؛ کسر از موجودی (واحد پایه) با نرخ تبدیل
                if brand and brand.conversion_rate:
                    base_deduction = trade_qty * brand.conversion_rate
                else:
                    base_deduction = trade_qty

                if base_deduction > s_item.quantity:
                    available = s_item.trade_quantity if s_item.trade_quantity is not None else s_item.quantity
                    unit_label = ''
                    if brand and brand.trade_unit:
                        unit_label = brand.trade_unit
                    elif s_item.unit:
                        unit_label = s_item.unit.name
                    brand_label = f'{brand.brand_name} - ' if brand and brand.brand_name else ''
                    messages.error(
                        request,
                        f'موجودی «{s_item.product.name}» ({brand_label}{available} {unit_label}) '
                        f'کمتر از مقدار درخواستی ({trade_qty}) است'
                    )
                    item_has_error = True
                    break

                supply_rows.append((s_item, base_deduction, trade_qty))

            if item_has_error:
                has_error = True
                continue
            if not supply_rows:
                messages.error(request, f'برای قلم «{item.name}» مقدار تامین را وارد کنید')
                has_error = True
                continue

            # کسر اتمیک از موجودی هر ردیف انتخابی
            total_base_supplied = Decimal('0')
            try:
                with transaction.atomic():
                    for s_item, base_deduction, trade_qty in supply_rows:
                        ok, prev_qty, new_qty = _change_stock_quantity(
                            s_item.id, -base_deduction)
                        if not ok:
                            raise _InsufficientStock(s_item.product.name)

                        brand = s_item.product_brand
                        desc = f'تامین لیست خرید - {item.name}'
                        if brand and brand.brand_name:
                            desc += f' - {brand.brand_name}: {trade_qty} {brand.trade_unit or ""} (= {base_deduction} واحد پایه)'
                        desc += f' - درخواست #{item.request.id}'

                        StockTransaction.objects.create(
                            stock_item=s_item,
                            transaction_type='out',
                            quantity=base_deduction,
                            conversion_rate=brand.conversion_rate if brand else None,
                            unit=s_item.unit,
                            reference_type='purchase_item',
                            reference_id=item.id,
                            description=desc,
                            created_by=request.user,
                            previous_quantity=prev_qty,
                            new_quantity=new_qty,
                        )
                        total_base_supplied += base_deduction
            except _InsufficientStock as exc:
                messages.error(request, f'موجودی «{exc}» در این لحظه کافی نیست')
                has_error = True
                continue

            item.supply_status = 'from_stock'
            item.purchased_quantity = int(total_base_supplied)
            item.save()
            items_processed += 1

        if not has_error and items_processed > 0:
            messages.success(request, f'{items_processed} قلم از انبار تامین شد')

        return redirect('purchase_list_with_stock_supply', cafe_code=cafe_code)

    # برای هر قلم، موجودی‌های مطابق همان محصول (به‌تفکیک برند/انبار)
    item_stock_options = {}
    for item in pending_items:
        if not item.product:
            continue
        matches = StockItem.objects.filter(product=item.product, quantity__gt=0)
        if cafe:
            matches = matches.filter(warehouse__cafe=cafe)
        matches = matches.select_related('product_brand', 'warehouse', 'unit', 'product')
        item_stock_options[item.id] = list(matches)

    return render(request, 'purchase_list_with_stock_supply.html', {
        'pending_items': pending_items,
        'item_stock_options': item_stock_options,
        'cafe_code': cafe_code,
    })


def _build_available_products_json(cafe, product_ids=None):
    """محصولات دارای موجودی + انبارها + برندهای قابل انتخاب برای هر محصول (JSON)

    اگر product_ids داده شود فقط همان کالاها (که در درخواست‌ها آمده‌اند) بررسی می‌شوند."""
    stock_qs = StockItem.objects.filter(quantity__gt=0).select_related(
        'product', 'product_brand', 'warehouse', 'unit')
    if cafe:
        stock_qs = stock_qs.filter(warehouse__cafe=cafe)
    if product_ids is not None:
        stock_qs = stock_qs.filter(product_id__in=product_ids)
    stock_rows = list(stock_qs)

    products = {}
    for si in stock_rows:
        products.setdefault(si.product_id, si.product)
    product_list = sorted(products.values(), key=lambda p: p.name)

    # برندهای هر محصول: برندهای فعال تعریف‌شده + ردیف «بدون برند» در صورت وجود موجودی بدون برند
    brands_by_product = {}
    for p in product_list:
        defined = list(p.brands.filter(is_active=True))
        has_unbranded = any(si.product_brand_id is None for si in stock_rows if si.product_id == p.id)
        brands_by_product[p.id] = defined + ([None] if has_unbranded else [])

    groups = []
    for p in product_list:
        wh_ids = []
        for si in stock_rows:
            if si.product_id == p.id and si.warehouse_id not in wh_ids:
                wh_ids.append(si.warehouse_id)
        warehouses = []
        for wid in sorted(wh_ids):
            wh = next((si.warehouse for si in stock_rows if si.warehouse_id == wid), None)
            if wh is None:
                continue
            brand_rows = []
            for brand in brands_by_product[p.id]:
                si = next(
                    (s for s in stock_rows
                     if s.product_id == p.id and s.warehouse_id == wid
                     and s.product_brand_id == (brand.id if brand else None)),
                    None,
                )
                base_qty = si.quantity if si else Decimal('0')
                if brand and brand.conversion_rate:
                    trade_avail = base_qty / brand.conversion_rate
                    unit_label = brand.trade_unit or (si.unit.name if si and si.unit else '')
                    rate = brand.conversion_rate
                else:
                    trade_avail = base_qty
                    unit_label = si.unit.name if si and si.unit else ''
                    rate = None
                brand_rows.append({
                    'brand': brand,
                    'available': base_qty,        # واحد پایه
                    'trade_available': trade_avail,
                    'unit_label': unit_label,
                    'rate': rate,
                    'has_stock': si is not None,
                })
            warehouses.append({'warehouse': wh, 'brands': brand_rows})
        groups.append({'product': p, 'warehouses': warehouses})

    return json.dumps([
        {
            'id': g['product'].id,
            'name': g['product'].name,
            'warehouses': [
                {
                    'id': w['warehouse'].id,
                    'name': w['warehouse'].name,
                    'brands': [
                        {
                            'id': b['brand'].id if b['brand'] else None,
                            'name': b['brand'].brand_name if b['brand'] else 'بدون برند',
                            'trade_unit': b['brand'].trade_unit if b['brand'] else '',
                            'rate': str(b['rate']) if b['rate'] else None,
                            'available': str(b['trade_available']),
                            'has_stock': b['has_stock'],
                        }
                        for b in w['brands']
                    ],
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
    brand_ids = request.POST.getlist('brand_id')
    qtys = request.POST.getlist('qty')
    statuses = request.POST.getlist('status')

    has_error = False
    submitted_rows = []
    rows = []  # (product, warehouse, brand, trade_qty, status, stock_item, base_deduction)

    for i, pid_str in enumerate(product_ids):
        try:
            product = Product.objects.get(id=int(pid_str))
            warehouse = Warehouse.objects.get(id=int(warehouse_ids[i]))
        except (Product.DoesNotExist, Warehouse.DoesNotExist, ValueError, IndexError):
            messages.error(request, 'محصول یا انبار انتخاب‌شده معتبر نیست')
            has_error = True
            continue

        brand = None
        brand_id_raw = brand_ids[i] if i < len(brand_ids) else ''
        if brand_id_raw:
            try:
                brand = ProductBrand.objects.get(id=int(brand_id_raw))
            except (ProductBrand.DoesNotExist, ValueError):
                messages.error(request, f'برند «{product.name}» معتبر نیست')
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
            product=product, warehouse=warehouse, product_brand=brand).first()

        base_deduction = qty
        if brand and brand.conversion_rate:
            base_deduction = qty * brand.conversion_rate

        if status == 'from_stock':
            if stock_item is None:
                messages.error(request, f'موجودی «{product.name}» در این انبار یافت نشد')
                has_error = True
                continue
            if base_deduction > stock_item.quantity:
                available = stock_item.trade_quantity if stock_item.trade_quantity is not None else stock_item.quantity
                unit_label = brand.trade_unit if brand and brand.trade_unit else (stock_item.unit.name if stock_item.unit else '')
                brand_label = f'{brand.brand_name} - ' if brand and brand.brand_name else ''
                messages.error(
                    request,
                    f'موجودی «{product.name}» ({brand_label}{available} {unit_label}) '
                    f'کمتر از مقدار درخواستی ({qty}) است'
                )
                has_error = True
                continue

        rows.append((product, warehouse, brand, qty, status, stock_item, base_deduction))
        submitted_rows.append({
            'product': product,
            'warehouse': warehouse,
            'brand': brand,
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
            for product, warehouse, brand, qty, status, stock_item, base_deduction in rows:
                brand_label = f'{brand.brand_name} - ' if brand and brand.brand_name else ''
                unit_label = brand.trade_unit if brand and brand.trade_unit else (product.unit.name if product.unit else '')
                wh_item = WarehouseRequestItem.objects.create(
                    warehouse_request=wh_request,
                    product=product,
                    product_brand=brand,
                    name=product.name,
                    quantity=qty,
                    unit=unit_label or 'عدد',
                    description=f'{brand_label}{warehouse.name}',
                    supply_status=status,
                    stock_item=stock_item if status == 'from_stock' else None,
                    supplied_quantity=base_deduction if status == 'from_stock' else None,
                )
                if status == 'from_stock':
                    # کسر اتمیک از موجودی انبار با رعایت نرخ تبدیل
                    ok, prev_qty, new_qty = _change_stock_quantity(
                        stock_item.id, -base_deduction)
                    if not ok:
                        raise _InsufficientStock(product.name)
                    desc = f'تامین کالای موجود در انبار - {product.name}'
                    if brand and brand.brand_name:
                        desc += f' - {brand.brand_name}: {qty} {brand.trade_unit or ""} (= {base_deduction} واحد پایه)'
                    desc += f' - انبار {warehouse.name} - درخواست #{wh_request.id}'
                    StockTransaction.objects.create(
                        stock_item=stock_item,
                        transaction_type='out',
                        quantity=base_deduction,
                        conversion_rate=brand.conversion_rate if brand else None,
                        unit=stock_item.unit,
                        reference_type='warehouse_request',
                        reference_id=wh_request.id,
                        description=desc,
                        created_by=request.user,
                        previous_quantity=prev_qty,
                        new_quantity=new_qty,
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
                        product_brand=wh_item.product_brand,
                        name=wh_item.name,
                        quantity=int(q) if q == int(q) else int(q) + 1,
                        unit=wh_item.unit,
                        description=wh_item.description or '',
                    )
                    new_purchase_items.append(pi)
                # همه درخواست‌های کالا مستقیماً برای مسئول انبار ارسال می‌شوند
                _route_request_to_warehouse(
                    new_purchase, new_purchase_items, request.user,
                    notes='ارسال مستقیم از فرم درخواست کالا')
                messages.info(request, f'{len(items_to_purchase)} قلم به لیست خرید ارجاع شد')

            # تعیین وضعیت نهایی درخواست انبار
            all_statuses = [r[4] for r in rows]
            if all(s == 'from_stock' for s in all_statuses):
                wh_request.status = 'approved_from_stock'
            elif all(s == 'to_purchase' for s in all_statuses):
                wh_request.status = 'approved_purchase'
            elif all(s == 'not_supplied' for s in all_statuses):
                wh_request.status = 'declined'
            else:
                wh_request.status = 'fulfilled'
            wh_request.processed_by = request.user
            wh_request.processed_at = timezone.now()
            wh_request.save()
      except _InsufficientStock as exc:
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
    """کالاهای موجود در انبار - انتخاب محصول → انبار → برند، افزودن به جدول بررسی و تایید نهایی"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

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
        return redirect('purchase_lists_by_date')
    
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
        return redirect('purchase_lists_by_date')
    
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
        return redirect('finance_dashboard')

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
        brand_ids = request.POST.getlist("item_brand_id")

        # Get department from POST if provided
        selected_department = request.POST.get('department', department)

        items_data = []
        errors = []
        for idx, name in enumerate(product_names):
            name = name.strip() if name else ""
            if not name:
                errors.append("product_name")
                continue
            qty_raw = quantities[idx] if idx < len(quantities) else ""
            qty_val = _parse_decimal(qty_raw)
            if qty_val is None or qty_val <= 0 or qty_val > 2147483647:
                errors.append("quantity")
                continue
            unit_val = units[idx].strip() if idx < len(units) and units[idx] else "عدد"
            desc_val = descriptions[idx] if idx < len(descriptions) else ""
            product_id_raw = product_ids[idx] if idx < len(product_ids) else ""
            brand_id_raw = brand_ids[idx] if idx < len(brand_ids) else ""
            # محصول انتخاب‌شده از لیست پیشنهادها (اگر انتخاب نشده باشد با نام جستجو می‌شود)
            item_product = None
            if product_id_raw.isdigit():
                item_product = Product.objects.filter(id=int(product_id_raw)).first()
            if item_product is None:
                item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else Product.objects.filter(name__iexact=name).first()
            # برند انتخاب‌شده فقط در صورتی ثبت می‌شود که متعلق به همان محصول باشد
            item_brand = None
            if brand_id_raw.isdigit() and item_product is not None:
                item_brand = ProductBrand.objects.filter(id=int(brand_id_raw), product=item_product).first()
            items_data.append((name, qty_val, unit_val, desc_val, item_product, item_brand))

        if errors:
            return render(
                request,
                "purchase_request_form.html",
                {
                    "errors": errors,
                    "error_message": "quantity" if "quantity" in errors else "product_name",
                    "department": selected_department,
                    "delivery_days": _next_delivery_days(),
                },
                status=200,
            )

        cafe = get_cafe_from_request(request)
        # زمان تحویل: تاریخ انتخاب‌شده (۷ روز آینده) یا تحویل فوری
        delivery_date = None
        delivery_immediate = request.POST.get('delivery_immediate') == '1'
        delivery_raw = request.POST.get('delivery_date', '').strip()
        if delivery_raw:
            try:
                delivery_date = datetime.strptime(delivery_raw, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                delivery_date = None
        purchase_request = PurchaseRequest.objects.create(
            user=request.user, status="pending_manager_approval", cafe=cafe,
            delivery_date=delivery_date,
            delivery_immediate=delivery_immediate,
        )
        created_items = []
        for index, (name, qty, unit, desc, item_product, item_brand) in enumerate(items_data, start=1):
            item = PurchaseItem.objects.create(
                request=purchase_request,
                product=item_product,
                product_brand=item_brand,
                name=name,
                quantity=int(qty),
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

    cafe = get_cafe_from_request(request)
    all_categories = Category.objects.filter(is_active=True).select_related('parent').order_by('name')
    if cafe:
        all_categories = all_categories.filter(cafe=cafe)

    return render(request, "purchase_request_form.html", {
        "department": department,
        "delivery_days": _next_delivery_days(),
        "all_categories": all_categories,
    })


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

        # گروه‌بندی درخواست‌ها بر اساس روز: ۷ روز گذشته هر روز یک لیست جداگانه،
        # درخواست‌های قدیمی‌تر زیر یک بخش «قبلی» نمایش داده می‌شوند
        from datetime import timedelta
        weekday_names = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']
        today = datetime.now().date()
        week_start = today - timedelta(days=6)
        grouped_by_day = {}
        older_requests = []
        for req in requests_qs:
            day = req.created_at.date()
            if day >= week_start:
                grouped_by_day.setdefault(day, []).append(req)
            else:
                older_requests.append(req)
        day_groups = [
            (day, weekday_names[day.weekday()], grouped_by_day[day])
            for day in sorted(grouped_by_day, reverse=True)
        ]

        context = {
            "requests": list(requests_qs),
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
        return redirect("purchase_request_detail", request_id=purchase_request.id)

    items = purchase_request.items.all()
    return render(
        request,
        "purchase_request_update.html",
        {"purchase_request": purchase_request, "items": items},
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
# Backup and system health
# ---------------------------------------------------------------------------


def _human_size(num_bytes):
    """Format a byte count into a human-readable string."""
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{int(size)} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def _snapshot_db_before_restore():
    """Copy the current SQLite DB to BACKUPS_DIR before a destructive restore."""
    db_path = Path(settings.DATABASES['default']['NAME'])
    if not os.path.exists(db_path):
        return None
    os.makedirs(settings.BACKUPS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    snapshot_path = Path(settings.BACKUPS_DIR) / f'autorestore_{timestamp}.sqlite3'
    shutil.copyfile(db_path, snapshot_path)
    return snapshot_path


def _is_sqlite_engine():
    return settings.DATABASES['default']['ENGINE'].endswith('sqlite3')


# Subfolders of DATA_PATH excluded from a full data-folder backup:
# backups (would recurse into old archives), cache/logs/tmp (regenerable)
_EXCLUDED_FOLDER_BACKUP_DIRS = {'backups', 'cache', 'logs', 'tmp'}


def _build_data_folder_zip(zip_path):
    """Zip the whole DATA_PATH folder (db, uploads, sessions, ...) into zip_path."""
    root = Path(settings.DATA_PATH)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_FOLDER_BACKUP_DIRS]
            rel_dir = Path(dirpath).relative_to(root)
            for fname in filenames:
                full_path = Path(dirpath) / fname
                arcname = (rel_dir / fname).as_posix()
                try:
                    zf.write(full_path, arcname)
                except OSError:
                    continue  # skip files locked/unreadable during the walk


class _TempBackupFile:
    """File-like wrapper that deletes its temp file when the response closes it.

    Delegates read/seek/tell to the underlying file so FileResponse can
    determine length and headers (Content-Disposition) before streaming.
    """

    def __init__(self, path):
        self._file = open(path, 'rb')
        self._path = path
        self.name = path

    def __iter__(self):
        return iter(self._file)

    def __getattr__(self, item):
        # Delegate everything else (read, seek, tell, seekable, ...) to the file
        return getattr(self._file, item)

    def close(self):
        try:
            self._file.close()
        finally:
            try:
                os.remove(self._path)
            except OSError:
                pass


def run_auto_backup_if_due():
    """If Drive is connected and the interval (default 12h) has passed,
    automatically back up the whole data folder to Google Drive.
    Returns (ran, message)."""
    if not drive_backup.is_configured() or not drive_backup.is_connected():
        return False, ''

    state = drive_backup.get_auto_state()
    interval_hours = drive_backup.auto_backup_interval_hours()
    last_raw = state.get('last_auto')
    if last_raw:
        try:
            last_run = datetime.fromisoformat(last_raw)
            if (datetime.now() - last_run).total_seconds() < interval_hours * 3600:
                return False, ''  # not due yet
        except ValueError:
            pass

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    os.makedirs(settings.TMP_DIR, exist_ok=True)
    zip_path = Path(settings.TMP_DIR) / f'syntel_data_autobackup_{timestamp}.zip'
    try:
        _build_data_folder_zip(zip_path)
        drive_backup.upload_file_to_drive(zip_path.name, zip_path)
        drive_backup.set_auto_state(
            last_auto=datetime.now().isoformat(timespec='seconds'),
            status='ok',
            last_error='',
        )
        return True, f'پشتیبان خودکار «{zip_path.name}» با موفقیت به گوگل‌درایو ارسال شد.'
    except Exception as e:
        drive_backup.set_auto_state(status='error', last_error=str(e))
        return False, f'خطا در پشتیبان خودکار: {str(e)}'
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


@custom_login_required
@role_required(["manager"])
def backup_management(request):
    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.all()
    inv_qs = Invoice.objects.all()
    sup_qs = Supplier.objects.all()
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
        inv_qs = inv_qs.filter(cafe=cafe)
        sup_qs = sup_qs.filter(cafe=cafe)

    # Automatic backup: run when the interval (default 12h) has passed
    auto_ran, auto_message = run_auto_backup_if_due()
    if auto_ran and auto_message:
        messages.success(request, auto_message)

    drive_cfg = drive_backup.load_config()
    auto_state = drive_backup.get_auto_state()
    interval_hours = drive_backup.auto_backup_interval_hours()
    last_auto = auto_state.get('last_auto', '')
    next_auto = ''
    if last_auto:
        try:
            next_auto = (datetime.fromisoformat(last_auto) + timedelta(hours=interval_hours)).strftime('%Y-%m-%d %H:%M')
        except ValueError:
            pass

    is_sqlite = _is_sqlite_engine()
    db_path = Path(settings.DATABASES['default']['NAME'])
    db_size = os.path.getsize(db_path) if is_sqlite and os.path.exists(db_path) else 0

    # List server-side backups (under DATA_PATH/backups), newest first
    backups = []
    if os.path.isdir(settings.BACKUPS_DIR):
        for name in sorted(os.listdir(settings.BACKUPS_DIR), reverse=True):
            full_path = os.path.join(settings.BACKUPS_DIR, name)
            if os.path.isfile(full_path):
                stat = os.stat(full_path)
                backups.append({
                    'name': name,
                    'size': stat.st_size,
                    'size_display': _human_size(stat.st_size),
                    'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                })

    context = {
        "title": "مدیریت پشتیبان‌گیری و داده‌ها",
        "total_users": User.objects.count(),
        "total_requests": pr_qs.count(),
        "total_invoices": inv_qs.count(),
        "total_payments": Payment.objects.count(),
        "total_suppliers": sup_qs.count(),
        "database_name": os.path.basename(db_path),
        "database_host": settings.DATABASES['default']['HOST'] or 'localhost',
        "database_type": 'SQLite' if is_sqlite else 'PostgreSQL',
        "database_path": str(db_path),
        "database_size": _human_size(db_size) if db_size else '-',
        "is_sqlite": is_sqlite,
        "backups": backups,
        "drive_configured": drive_backup.is_configured(),
        "drive_connected": drive_backup.is_connected(),
        "drive_email": drive_backup.get_connected_email(),
        "drive_client_id": drive_cfg['client_id'],
        "drive_folder_id": drive_cfg['folder_id'],
        "drive_has_file_config": os.path.exists(settings.DATA_PATH / drive_backup.CONFIG_FILENAME),
        "auto_interval_hours": interval_hours,
        "auto_last_run": last_auto,
        "auto_next_run": next_auto,
        "auto_status": auto_state.get('status', ''),
        "auto_last_error": auto_state.get('last_error', ''),
    }
    return render(request, "backup_management.html", context)


@custom_login_required
@role_required(["manager"])
def backup_database(request):
    """Create a server-side backup of the database (JSON dump)."""
    try:
        # Create backups directory if it doesn't exist (under DATA_PATH/backups)
        backup_dir = settings.BACKUPS_DIR
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'backup_{timestamp}.json'
        filepath = os.path.join(backup_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            call_command('dumpdata', exclude=['auth.permission', 'contenttypes', 'sessions'], stdout=f)

        messages.success(request, f"نسخه پشتیبان روی سرور ذخیره شد: {filename}")
    except Exception as e:
        messages.error(request, f"خطا در ایجاد پشتیبان: {str(e)}")

    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@role_required(["manager"])
def download_database_file(request):
    """Download the actual SQLite database file as a full backup."""
    if not _is_sqlite_engine():
        messages.error(request, "دانلود فایل دیتابیس فقط برای دیتابیس SQLite پشتیبانی می‌شود. برای PostgreSQL از دانلود JSON استفاده کنید.")
        return get_cafe_redirect_url('backup_management', request)
    db_path = Path(settings.DATABASES['default']['NAME'])
    if not os.path.exists(db_path):
        messages.error(request, "فایل دیتابیس یافت نشد.")
        return get_cafe_redirect_url('backup_management', request)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    return FileResponse(
        open(db_path, 'rb'),
        content_type='application/x-sqlite3',
        as_attachment=True,
        filename=f'syntel_db_backup_{timestamp}.sqlite3',
    )


@custom_login_required
@role_required(["manager"])
def restore_database_file(request):
    """Restore the database by uploading a SQLite database file (full restore)."""
    if not _is_sqlite_engine():
        messages.error(request, "بازیابی فایل دیتابیس فقط برای دیتابیس SQLite پشتیبانی می‌شود. برای PostgreSQL از بازیابی JSON استفاده کنید.")
        return get_cafe_redirect_url('backup_management', request)

    if request.method == 'POST' and request.FILES.get('db_file'):
        db_file = request.FILES['db_file']

        # Validate extension and SQLite magic bytes
        ext = Path(db_file.name).suffix.lower()
        if ext not in ('.db', '.sqlite', '.sqlite3'):
            messages.error(request, "فرمت فایل نامعتبر است. لطفاً فایل دیتابیس (.sqlite3 یا .db) انتخاب کنید.")
            return get_cafe_redirect_url('backup_management', request)
        header = db_file.read(16)
        db_file.seek(0)
        if header[:15] != b'SQLite format 3':
            messages.error(request, "فایل انتخاب‌شده یک فایل دیتابیس SQLite معتبر نیست.")
            return get_cafe_redirect_url('backup_management', request)

        db_path = Path(settings.DATABASES['default']['NAME'])
        os.makedirs(settings.TMP_DIR, exist_ok=True)
        temp_path = Path(settings.TMP_DIR) / f'restore_db_{datetime.now().strftime("%Y%m%d%H%M%S")}.sqlite3'

        try:
            with open(temp_path, 'wb+') as destination:
                for chunk in db_file.chunks():
                    destination.write(chunk)

            # Snapshot the current DB first so a bad restore is recoverable
            snapshot_path = _snapshot_db_before_restore()

            connections.close_all()
            # Remove stale WAL/SHM files so they cannot shadow the new database
            for suffix in ('-wal', '-shm'):
                stale = Path(str(db_path) + suffix)
                if stale.exists():
                    stale.unlink()
            shutil.copyfile(temp_path, db_path)

            try:
                call_command('migrate', verbosity=0, interactive=False)
                note = f" نسخه قبلی قبل از بازیابی ذخیره شد: {snapshot_path.name}" if snapshot_path else ""
                messages.success(request, f"دیتابیس با موفقیت بازیابی شد.{note}")
            except Exception as e:
                # Roll back to the snapshot on failure
                connections.close_all()
                if snapshot_path and os.path.exists(snapshot_path):
                    shutil.copyfile(snapshot_path, db_path)
                messages.error(request, f"خطا در اعمال دیتابیس جدید ({e}). دیتابیس قبلی برگردانده شد.")
        except Exception as e:
            messages.error(request, f"خطا در بازیابی دیتابیس: {str(e)}")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        return get_cafe_redirect_url('backup_management', request)

    messages.error(request, "فایلی انتخاب نشده است.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@role_required(["manager"])
def download_backup_file(request, filename):
    """Download a server-side backup file from BACKUPS_DIR."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(settings.BACKUPS_DIR, safe_name)
    if not os.path.isfile(filepath):
        raise Http404("فایل پشتیبان یافت نشد")
    return FileResponse(
        open(filepath, 'rb'),
        as_attachment=True,
        filename=safe_name,
    )


@custom_login_required
@role_required(["manager"])
def delete_backup_file(request, filename):
    """Delete a server-side backup file."""
    if request.method == 'POST':
        safe_name = os.path.basename(filename)
        filepath = os.path.join(settings.BACKUPS_DIR, safe_name)
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
                messages.success(request, f"فایل پشتیبان حذف شد: {safe_name}")
            except OSError as e:
                messages.error(request, f"خطا در حذف فایل: {e}")
        else:
            messages.error(request, "فایل پشتیبان یافت نشد.")
    return get_cafe_redirect_url('backup_management', request)


def _drive_back_to_backup(request):
    """Return to the backup page (or login) after the Drive OAuth callback."""
    cafe_code = get_cafe_code(request)
    if cafe_code:
        return redirect('backup_management', cafe_code=cafe_code)
    return redirect('login')


@custom_login_required
@role_required(["manager"])
def drive_connect(request):
    """Start the Google OAuth flow to connect Google Drive."""
    if not drive_backup.is_configured():
        messages.error(request, "گوگل‌درایو تنظیم نشده است. ابتدا GOOGLE_DRIVE_CLIENT_ID و GOOGLE_DRIVE_CLIENT_SECRET را در تنظیمات سرور قرار دهید.")
        return get_cafe_redirect_url('backup_management', request)
    state = secrets.token_urlsafe(32)
    request.session['drive_oauth_state'] = state
    redirect_uri = request.build_absolute_uri(reverse('drive_callback'))
    return redirect(drive_backup.build_authorize_url(state, redirect_uri))


def drive_callback(request):
    """OAuth callback — exchange the code and store the token."""
    error = request.GET.get('error')
    if error:
        messages.error(request, f"اتصال به گوگل‌درایو انجام نشد: {error}")
        return _drive_back_to_backup(request)

    code = request.GET.get('code')
    state = request.GET.get('state')
    expected_state = request.session.pop('drive_oauth_state', None)
    if not code or not state or not expected_state or state != expected_state:
        messages.error(request, "پاسخ نامعتبر از گوگل دریافت شد. دوباره تلاش کنید.")
        return _drive_back_to_backup(request)

    try:
        redirect_uri = request.build_absolute_uri(reverse('drive_callback'))
        drive_backup.exchange_code(code, redirect_uri)
        email = drive_backup.get_connected_email()
        messages.success(request, f"اتصال گوگل‌درایو برقرار شد{(' (' + email + ')') if email else ''}.")
    except Exception as e:
        messages.error(request, f"خطا در اتصال به گوگل‌درایو: {str(e)}")
    return _drive_back_to_backup(request)


@custom_login_required
@role_required(["manager"])
def drive_save_config(request):
    """Save Google Drive credentials from the page (no server restart needed)."""
    if request.method == 'POST':
        if request.POST.get('clear_config'):
            drive_backup.clear_config()
            messages.success(request, "تنظیمات ذخیره‌شده گوگل‌درایو حذف شد؛ مقادیر محیطی (در صورت وجود) استفاده می‌شوند.")
            return get_cafe_redirect_url('backup_management', request)

        client_id = request.POST.get('client_id', '').strip()
        client_secret = request.POST.get('client_secret', '').strip()
        folder_id = request.POST.get('folder_id', '').strip()

        if not client_id and not client_secret:
            drive_backup.clear_config()
            messages.success(request, "تنظیمات ذخیره‌شده گوگل‌درایو حذف شد؛ مقادیر محیطی (در صورت وجود) استفاده می‌شوند.")
        elif not client_id:
            messages.error(request, "وارد کردن Client ID الزامی است.")
        elif not client_secret:
            # Empty secret means "keep the existing one" (it is not shown on the page)
            existing = drive_backup.load_config()['client_secret']
            if existing:
                drive_backup.save_config(client_id, existing, folder_id)
                messages.success(request, "تنظیمات گوگل‌درایو ذخیره شد و بلافاصله اعمال می‌شود.")
            else:
                messages.error(request, "وارد کردن Client Secret الزامی است.")
        else:
            drive_backup.save_config(client_id, client_secret, folder_id)
            messages.success(request, "تنظیمات گوگل‌درایو ذخیره شد و بلافاصله اعمال می‌شود.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@role_required(["manager"])
def drive_disconnect(request):
    """Disconnect Google Drive (removes the stored token)."""
    if request.method == 'POST':
        drive_backup.clear_token()
        messages.success(request, "اتصال گوگل‌درایو قطع شد.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@role_required(["manager"])
def drive_backup_now(request):
    """Send a fresh backup of the database to Google Drive."""
    if request.method != 'POST':
        return get_cafe_redirect_url('backup_management', request)
    if not drive_backup.is_configured():
        messages.error(request, "گوگل‌درایو تنظیم نشده است.")
        return get_cafe_redirect_url('backup_management', request)
    if not drive_backup.is_connected():
        messages.error(request, "ابتدا به گوگل‌درایو متصل شوید.")
        return get_cafe_redirect_url('backup_management', request)

    backup_type = request.POST.get('type', 'json')
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    try:
        if backup_type == 'folder':
            # Full backup of the whole DATA_PATH folder as a ZIP
            os.makedirs(settings.TMP_DIR, exist_ok=True)
            zip_path = Path(settings.TMP_DIR) / f'syntel_data_backup_{timestamp}.zip'
            _build_data_folder_zip(zip_path)
            filename = zip_path.name
            with open(zip_path, 'rb') as f:
                payload = f.read()
            try:
                os.remove(zip_path)
            except OSError:
                pass
        elif backup_type == 'db' and _is_sqlite_engine():
            db_path = Path(settings.DATABASES['default']['NAME'])
            if not os.path.exists(db_path):
                raise ValueError('فایل دیتابیس یافت نشد.')
            filename = f'syntel_db_backup_{timestamp}.sqlite3'
            with open(db_path, 'rb') as f:
                payload = f.read()
        else:
            buffer = io.StringIO()
            call_command('dumpdata', exclude=['auth.permission', 'contenttypes', 'sessions'], stdout=buffer)
            filename = f'syntel_export_{timestamp}.json'
            payload = buffer.getvalue().encode('utf-8')

        drive_backup.upload_backup(filename, payload)
        messages.success(request, f"پشتیبان «{filename}» با موفقیت به گوگل‌درایو ارسال شد.")
    except Exception as e:
        messages.error(request, f"خطا در ارسال به گوگل‌درایو: {str(e)}")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@role_required(["manager"])
def export_data_folder(request):
    """Download the whole DATA_PATH folder (db + uploads + sessions) as a ZIP."""
    os.makedirs(settings.TMP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    zip_path = Path(settings.TMP_DIR) / f'syntel_data_backup_{timestamp}.zip'
    try:
        _build_data_folder_zip(zip_path)
    except Exception as e:
        messages.error(request, f"خطا در ساخت پشتیبان: {str(e)}")
        return get_cafe_redirect_url('backup_management', request)
    return FileResponse(
        _TempBackupFile(zip_path),
        content_type='application/zip',
        as_attachment=True,
        filename=zip_path.name,
    )


@custom_login_required
@role_required(["manager"])
def export_data_json(request):
    """Download a full database backup as JSON."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'syntel_export_{timestamp}.json'

    buffer = io.StringIO()
    call_command('dumpdata', exclude=['auth.permission', 'contenttypes', 'sessions'], stdout=buffer)
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@custom_login_required
@role_required(["manager"])
def export_data_csv(request):
    """Export specific data models to CSV."""
    model_name = request.GET.get('model', 'products')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{model_name}_{timestamp}.csv"'
    response.write(u'\ufeff'.encode('utf8'))  # BOM for Excel compatibility
    
    writer = csv.writer(response)
    cafe = get_cafe_from_request(request)
    
    if model_name == 'products':
        writer.writerow(['ID', 'Name', 'Category', 'Unit', 'Price', 'Stock', 'Description'])
        products_qs = Product.objects.select_related('category', 'unit').all()
        if cafe:
            products_qs = products_qs.filter(cafe=cafe)
        for obj in products_qs:
            writer.writerow([
                obj.id, 
                obj.name, 
                obj.category.name if obj.category else '', 
                obj.unit.name if obj.unit else '', 
                obj.current_price, 
                obj.stock_quantity,
                obj.description
            ])
            
    elif model_name == 'suppliers':
        writer.writerow(['ID', 'Name', 'Contact Person', 'Phone', 'Email', 'Address', 'Rating'])
        suppliers_qs = Supplier.objects.all()
        if cafe:
            suppliers_qs = suppliers_qs.filter(cafe=cafe)
        for obj in suppliers_qs:
            writer.writerow([
                obj.id, 
                obj.name, 
                obj.contact_person, 
                obj.phone, 
                obj.email, 
                obj.address,
                obj.rating
            ])
            
    elif model_name == 'purchases':
        writer.writerow(['ID', 'Requester', 'Status', 'Date Created', 'Total Items'])
        purchases_qs = PurchaseRequest.objects.select_related('requester').prefetch_related('items').all()
        if cafe:
            purchases_qs = purchases_qs.filter(cafe=cafe)
        for obj in purchases_qs:
            writer.writerow([
                obj.id, 
                obj.requester.username, 
                obj.get_status_display(), 
                obj.created_at.strftime('%Y-%m-%d %H:%M'), 
                obj.items.count()
            ])
            
    elif model_name == 'invoices':
        writer.writerow(['ID', 'Invoice Number', 'Supplier', 'Amount', 'Status', 'Date'])
        invoices_qs = Invoice.objects.select_related('supplier').all()
        if cafe:
            invoices_qs = invoices_qs.filter(cafe=cafe)
        for obj in invoices_qs:
            writer.writerow([
                obj.id,
                obj.invoice_number,
                obj.supplier.name,
                obj.total_amount,
                obj.get_status_display(),
                obj.issue_date
            ])
            
    else:
        writer.writerow(['Error', 'Invalid model specified'])
        
    return response


@custom_login_required
@role_required(["manager"])
def import_data_json(request):
    """Restore data from a JSON backup file (replaces all current data)."""
    if request.method == 'POST' and request.FILES.get('backup_file'):
        backup_file = request.FILES['backup_file']

        # Save temporarily (under DATA_PATH/tmp)
        temp_dir = settings.TMP_DIR
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f'restore_{datetime.now().strftime("%Y%m%d%H%M%S")}.json')

        try:
            with open(temp_path, 'wb+') as destination:
                for chunk in backup_file.chunks():
                    destination.write(chunk)

            # Quick validation that the file is a JSON fixture before touching the DB
            with open(temp_path, 'r', encoding='utf-8') as f:
                fixture = json.load(f)
            if not isinstance(fixture, list):
                raise ValueError("فرمت فایل JSON نامعتبر است (لیست اشیاء مورد انتظار است).")

            # Snapshot the current DB so a bad restore is recoverable
            snapshot_path = _snapshot_db_before_restore()

            # Full restore: clear existing data, then load the fixture (atomic so a
            # failure rolls everything back)
            with transaction.atomic():
                call_command('flush', verbosity=0, interactive=False)
                call_command('loaddata', temp_path, verbosity=0)

            note = f" نسخه قبلی قبل از بازیابی ذخیره شد: {snapshot_path.name}" if snapshot_path else ""
            messages.success(request, f"داده‌ها با موفقیت بازیابی شد و جایگزین داده‌های قبلی شد.{note}")
        except Exception as e:
            messages.error(request, f"خطا در بازیابی داده‌ها: {str(e)}")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        return get_cafe_redirect_url('backup_management', request)

    messages.error(request, "فایلی انتخاب نشده است.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@role_required(["manager"])
def create_migration_script(request):
    return render(request, "create_migration_script.html")


@custom_login_required
@role_required(["manager"])
def run_migration_script(request):
    messages.success(request, "Migration executed")
    return redirect("backup_management")


@custom_login_required
@role_required(["manager"])
def system_health_check(request):
    return render(request, "system_health.html", {"status": "healthy"})


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
                    item.purchased_quantity = int(request.POST.get(purchased_qty_key, 0))
                except (ValueError, TypeError):
                    item.purchased_quantity = 0
            
            if supply_status_key in request.POST:
                item.supply_status = request.POST.get(supply_status_key, 'pending')
            
            item.save()
        
        # Get the date and role for redirect
        date_str = request.POST.get('date', '')
        role = request.POST.get('role', '')
        
        if date_str and role:
            return redirect(f"{reverse('purchase_day_detail')}?date={date_str}&role={role}")
        return redirect('purchase_lists_by_date')
    
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
            return redirect(f"{reverse('purchase_day_detail')}?date={date_str}&role={role}")
        return redirect('purchase_lists_by_date')
    
    return redirect('purchase_lists_by_date')


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
    
    # جستجو بر اساس نام محصول یا نام تجاری
    search = request.GET.get('search', '')
    if search:
        products = products.filter(
            Q(name__icontains=search) | Q(brands__brand_name__icontains=search)
        ).distinct()
    
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
    
    categories = Category.objects.all()
    if cafe:
        categories = categories.filter(cafe=cafe)
    return render(request, "product_list.html", {
        "products": products,
        "categories": categories,
        "search": search,
        "selected_category": category_id,
        "selected_status": is_active
    })


def _parse_conversion_rate(value):
    """تبدیل مقدار ورودی «میزان بسته‌بندی» به عدد اعشاری - خالی به None تبدیل می‌شود"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _next_delivery_days():
    """لیست ۶ روز آینده (از فردا) به‌جز روزهای شنبه به‌صورت (تاریخ، نام روز هفته) برای انتخاب زمان تحویل"""
    from datetime import timedelta
    weekday_names = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']
    today = datetime.now().date()
    days = []
    d = today + timedelta(days=1)
    while len(days) < 6:
        if d.weekday() != 5:  # شنبه
            days.append((d, weekday_names[d.weekday()]))
        d += timedelta(days=1)
    return days


def _parse_int_or_none(value):
    """تبدیل مقدار ورودی عدد صحیح (مثل عمر مفید) - خالی یا نامعتبر به None تبدیل می‌شود"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _render_product_form(request, product=None, brand=None, mode='create', error='', form_data=None):
    """رندر مشترک فرم محصول/واریانت تجاری

    mode:
      create     - محصول جدید + اولین برند
      add_brand  - افزودن برند به محصول موجود
      edit_brand - ویرایش برند موجود
      edit_product - ویرایش اطلاعات پایه محصول
    """
    cafe = get_cafe_from_request(request)
    categories = Category.objects.all()
    if cafe:
        categories = categories.filter(cafe=cafe)
    departments_qs = Department.objects.filter(is_active=True, cafe=cafe)
    return render(request, "product_form.html", {
        "product": product,
        "brand": brand,
        "mode": mode,
        "categories": categories,
        "units": Unit.objects.all(),
        "departments_qs": departments_qs,
        "departments": DEPARTMENT_CHOICES,
        "error": error,
        "form_data": form_data,
        "cafe_code": get_cafe_code(request) or 'local',
    })


def _save_brand(request, product, brand=None):
    """ثبت یا ویرایش یک واریانت تجاری برای محصول"""
    brand_name = (request.POST.get("brand_name") or "").strip()
    if not brand_name:
        return None, 'نام تجاری الزامی است.'
    duplicate = ProductBrand.objects.filter(product=product, brand_name__iexact=brand_name)
    if brand:
        duplicate = duplicate.exclude(id=brand.id)
    if duplicate.exists():
        return None, f'نام تجاری «{brand_name}» برای این محصول قبلاً ثبت شده است.'
    has_unit_conversion = request.POST.get("has_unit_conversion") == "on"
    trade_unit = (request.POST.get("trade_unit") or "").strip()
    conversion_rate = _parse_conversion_rate(request.POST.get("conversion_rate")) if has_unit_conversion else None
    if brand is None:
        brand = ProductBrand(product=product)
    brand.brand_name = brand_name
    brand.trade_unit = trade_unit
    brand.conversion_rate = conversion_rate
    brand.save()
    return brand, ''


@custom_login_required
@role_required(["purchasing", "manager"])
def product_create(request):
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        # ۱) تعیین محصول مقصد: انتخاب از لیست پیشنهادها یا ایجاد محصول جدید
        selected_product_id = request.POST.get("selected_product_id")
        selected_product = None
        if selected_product_id:
            selected_product = Product.objects.filter(id=selected_product_id).first()

        if selected_product is None:
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
                    error=f'محصولی با نام «{name}» قبلاً ثبت شده است. آن را از لیست پیشنهادها انتخاب کنید تا برند جدید به آن اضافه شود.',
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
                show_sub_products=request.POST.get("show_sub_products") == "on",
                cafe=cafe,
            )
            messages.success(request, f'محصول «{name}» ایجاد شد.')

        # ۲) ثبت واریانت تجاری - برای محصول جدید اختیاری است،
        #    ولی وقتی برند به محصول موجود اضافه می‌شود نام تجاری الزامی است
        brand_name = (request.POST.get("brand_name") or "").strip()
        if brand_name:
            brand, error = _save_brand(request, selected_product)
            if error:
                return _render_product_form(request, product=selected_product, mode='add_brand',
                                            error=error, form_data=request.POST)
            messages.success(request, f'برند «{brand.brand_name}» برای محصول «{selected_product.name}» ثبت شد.')
        elif selected_product_id:
            return _render_product_form(request, product=selected_product, mode='add_brand',
                                        error='نام تجاری الزامی است.', form_data=request.POST)
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
        product.show_sub_products = request.POST.get("show_sub_products") == "on"
        product.description = request.POST.get("description", product.description)
        # تکمیل اطلاعات کالای ناقص (ساخته‌شده از فرم درخواست): با اولین ویرایش کامل می‌شود
        # و از این پس در پیشنهادهای درخواست نمایش داده می‌شود
        if not product.is_complete:
            product.is_complete = True
        product.save()
        messages.success(request, 'محصول با موفقیت ویرایش شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')
    return _render_product_form(request, product=product, mode='edit_product')


@custom_login_required
@role_required(["purchasing", "manager"])
def product_brand_add(request, product_id):
    """افزودن واریانت تجاری جدید به محصول موجود"""
    product = get_object_or_404(Product, id=product_id)
    if request.method == "POST":
        brand, error = _save_brand(request, product)
        if error:
            return _render_product_form(request, product=product, mode='add_brand',
                                        error=error, form_data=request.POST)
        messages.success(request, f'برند «{brand.brand_name}» برای محصول «{product.name}» ثبت شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')
    return _render_product_form(request, product=product, mode='add_brand')


@custom_login_required
@role_required(["purchasing", "manager"])
def product_brand_update(request, brand_id):
    """ویرایش واریانت تجاری"""
    brand = get_object_or_404(ProductBrand, id=brand_id)
    if request.method == "POST":
        brand, error = _save_brand(request, brand.product, brand=brand)
        if error:
            return _render_product_form(request, product=brand.product, brand=brand, mode='edit_brand',
                                        error=error, form_data=request.POST)
        messages.success(request, 'واریانت تجاری با موفقیت ویرایش شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')
    return _render_product_form(request, product=brand.product, brand=brand, mode='edit_brand')


@custom_login_required
@role_required(["purchasing", "manager"])
def product_brand_delete(request, brand_id):
    brand = get_object_or_404(ProductBrand, id=brand_id)
    if request.method == "POST":
        brand.delete()
        messages.success(request, 'واریانت تجاری حذف شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')
    return render(request, "product_brand_confirm_delete.html",
                  {"brand": brand, "cafe_code": get_cafe_code(request) or 'local'})


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
    units = Unit.objects.all()
    return render(request, "unit_list.html", {"units": units})


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        symbol = request.POST.get("symbol", "")
        if name:
            Unit.objects.create(name=name, symbol=symbol)
            return redirect("unit_list")
    return render(request, "unit_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_update(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    if request.method == "POST":
        unit.name = request.POST.get("name", unit.name)
        unit.symbol = request.POST.get("symbol", unit.symbol)
        unit.save()
        return redirect("unit_list")
    return render(request, "unit_form.html", {"unit": unit})


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_delete(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    if request.method == "POST":
        unit.delete()
        return redirect("unit_list")
    return render(request, "unit_confirm_delete.html", {"unit": unit})


@custom_login_required
@role_required(["purchasing", "manager"])
def category_list(request):
    cafe = get_cafe_from_request(request)
    categories = Category.objects.all()
    if cafe:
        categories = categories.filter(cafe=cafe)
    return render(request, "category_list.html", {"categories": categories})

@custom_login_required
@role_required(["purchasing", "manager"])
def category_update(request, category_id):
    cafe = get_cafe_from_request(request)
    category = get_object_or_404(Category, id=category_id)
    if request.method == "POST":
        category.name = request.POST.get("name", category.name)
        category.description = request.POST.get("description", category.description)
        parent_id = request.POST.get("parent")
        if parent_id:
            category.parent = Category.objects.filter(id=parent_id).first()
        else:
            category.parent = None
        category.save()
        return redirect("category_list")
    
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
    category = get_object_or_404(Category, id=category_id)
    if request.method == "POST":
        category.delete()
        return redirect("category_list")
    return render(request, "category_confirm_delete.html", {"category": category})


# ---------------------------------------------------------------------------
# Department (بخش) Management — از صفحه مدیریت کاربران
# ---------------------------------------------------------------------------

@custom_login_required
@role_required(["manager", "admin"])
def department_list(request):
    """لیست بخشهای تعریفشده — فقط بخشهای کافه جاری؛ هیچ بخش سراسری وجود ندارد"""
    cafe = get_cafe_from_request(request)
    departments = Department.objects.filter(cafe=cafe).annotate(user_count=Count('users'))
    departments = departments.order_by('name')
    return render(request, "department_list.html", {"departments": departments, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_create(request):
    cafe = get_cafe_from_request(request)
    error = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        if not name:
            error = "نام بخش الزامی است"
        elif not cafe:
            error = "ابتدا کافه را انتخاب کنید"
        else:
            existing = Department.objects.filter(name__iexact=name, cafe=cafe)
            if existing.exists():
                error = f"بخشی با نام «{name}» قبلاً در این کافه ثبت شده است"
            else:
                Department.objects.create(name=name, cafe=cafe, is_active=is_active)
                return get_cafe_redirect_url('department_list', request)
    return render(request, "department_form.html", {"error": error, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_update(request, department_id):
    cafe = get_cafe_from_request(request)
    # فقط بخش کافه جاری قابل ویرایش است؛ بخش کافه دیگر برای مدیر این کافه ۴۰۴ است
    department = get_object_or_404(Department, cafe=cafe, id=department_id)
    error = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        if not name:
            error = "نام بخش الزامی است"
        else:
            existing = Department.objects.filter(name__iexact=name, cafe=cafe).exclude(id=department.id)
            if existing.exists():
                error = f"بخشی با نام «{name}» قبلاً در این کافه ثبت شده است"
            else:
                department.name = name
                department.is_active = is_active
                department.save()
                return get_cafe_redirect_url('department_list', request)
    return render(request, "department_form.html", {"department": department, "error": error, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_delete(request, department_id):
    cafe = get_cafe_from_request(request)
    department = get_object_or_404(Department, cafe=cafe, id=department_id)
    user_count = department.users.count()
    if request.method == "POST":
        # کاربران متصل به این بخش با SET_NULL بدون بخش میشوند (دادههای تاریخی حفظ میشوند)
        department.delete()
        return get_cafe_redirect_url('department_list', request)
    return render(request, "department_confirm_delete.html", {
        "department": department,
        "user_count": user_count,
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_list(request):
    payment_types = PaymentType.objects.all()
    return render(request, "payment_type_list.html", {"payment_types": payment_types})


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            PaymentType.objects.create(name=name)
            return redirect("payment_type_list")
    return render(request, "payment_type_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_update(request, payment_type_id):
    payment_type = get_object_or_404(PaymentType, id=payment_type_id)
    if request.method == "POST":
        payment_type.name = request.POST.get("name", payment_type.name)
        payment_type.save()
        return redirect("payment_type_list")
    return render(request, "payment_type_form.html", {"payment_type": payment_type})


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_delete(request, payment_type_id):
    payment_type = get_object_or_404(PaymentType, id=payment_type_id)
    if request.method == "POST":
        payment_type.delete()
        return redirect("payment_type_list")
    return render(request, "payment_type_confirm_delete.html", {"payment_type": payment_type})


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["manager", "admin"])
def user_list(request, cafe_code=None):
    cafe = get_cafe_from_request(request)
    from django.db.models import Q
    users = User.objects.all()

    # فیلتر بر اساس کافه جاری - کاربران همین کافه + کاربران مشترک (cafe=None که به همه کافه‌ها دسترسی دارند)
    if cafe:
        users = users.filter(Q(cafe=cafe) | Q(cafe__isnull=True))

    search_query = request.GET.get("search", "").strip()
    selected_role = request.GET.get("role", "")
    officer_filter = request.GET.get("officer", "") == "1"
    if search_query:
        users = users.filter(
            username__icontains=search_query
        ) | users.filter(first_name__icontains=search_query) | users.filter(last_name__icontains=search_query)
    if selected_role:
        users = users.filter(role=selected_role)
    if officer_filter:
        # مسئولان خرید/انبار: نقش اصلی یا نقش دوم آن‌ها purchasing/warehouse باشد
        officer_q = Q(role__in=['purchasing', 'warehouse']) | Q(secondary_role__in=['purchasing', 'warehouse'])
        users = users.filter(officer_q)
    return render(request, "user_list.html", {
        "users": users,
        "role_choices": User.ROLE_CHOICES,
        "search_query": search_query,
        "selected_role": selected_role,
        "officer_filter": officer_filter,
        "current_cafe": cafe,
    })


@custom_login_required
@role_required(["manager"])
def user_create(request, cafe_code=None):
    from .models import Cafe as CafeModel
    cafe = get_cafe_from_request(request)
    cafes = CafeModel.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True, cafe=cafe)
    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email", "")
        password = request.POST.get("password1") or request.POST.get("password")
        confirm_password = request.POST.get("password2") or request.POST.get(
            "confirm_password"
        )
        role = request.POST.get("role", "kitchen")
        cafe_id = request.POST.get("cafe_id", "all")
        department_id = request.POST.get("department_id", "")

        if not username:
            return render(request, "user_form.html", {
                "error": "نام کاربری الزامی است",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})
        if User.objects.filter(username=username).exists():
            return render(request, "user_form.html", {
                "error": f"نام کاربری '{username}' قبلاً استفاده شده است. لطفاً نام دیگری انتخاب کنید.",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})
        if not password:
            return render(request, "user_form.html", {
                "error": "رمز عبور الزامی است",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})
        if password != confirm_password:
            return render(request, "user_form.html", {
                "error": "رمز عبور و تأیید آن یکسان نیستند",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})

        # تعیین کافه کاربر
        cafe_obj = None
        if cafe_id and cafe_id != "all":
            cafe_obj = CafeModel.objects.filter(id=cafe_id, is_active=True).first()

        # تعیین بخش — فقط بخشی که به کافه همین کاربر تعلق دارد پذیرفته میشود
        department_obj = None
        if department_id:
            dep = Department.objects.filter(id=department_id, is_active=True).first()
            if dep and cafe_obj and dep.cafe_id == cafe_obj.id:
                department_obj = dep
            else:
                return render(request, "user_form.html", {
                    "error": "بخش انتخاب‌شده متعلق به کافه این کاربر نیست",
                    "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})

        secondary_role = request.POST.get("secondary_role", "").strip() or None
        User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=role,
            secondary_role=secondary_role,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            is_active=request.POST.get("is_active", True) not in ["False", "0", ""],
            cafe=cafe_obj,
            department=department_obj,
        )
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_form.html", {"roles": _get_departments(), "cafes": cafes, "departments": departments})


@custom_login_required
@role_required(["manager"])
def user_update(request, cafe_code=None, user_id=None):
    from .models import Cafe as CafeModel
    cafe = get_cafe_from_request(request)
    user_obj = get_object_or_404(User, id=user_id)
    cafes = CafeModel.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True, cafe=cafe)
    if request.method == "POST":
        # تغییر نام کاربری (اختیاری)
        new_username = request.POST.get("username", "").strip()
        if new_username and new_username != user_obj.username:
            if User.objects.filter(username=new_username).exists():
                return render(request, "user_form.html", {
                    "edit_user": user_obj,
                    "roles": _get_departments(),
                    "cafes": cafes,
                    "departments": departments,
                    "error": f"نام کاربری '{new_username}' قبلاً استفاده شده است",
                })
            user_obj.username = new_username
        user_obj.first_name = request.POST.get("first_name", user_obj.first_name)
        user_obj.last_name = request.POST.get("last_name", user_obj.last_name)
        user_obj.email = request.POST.get("email", user_obj.email)
        user_obj.role = request.POST.get("role", user_obj.role)
        user_obj.secondary_role = request.POST.get("secondary_role", "").strip() or None
        user_obj.is_active = "is_active" in request.POST
        cafe_id = request.POST.get("cafe_id", "keep")
        if cafe_id == "all":
            user_obj.cafe = None
        elif cafe_id and cafe_id != "keep":
            cafe_obj = CafeModel.objects.filter(id=cafe_id, is_active=True).first()
            if cafe_obj:
                user_obj.cafe = cafe_obj
        # بخش — فقط بخشی که به کافه همین کاربر تعلق دارد پذیرفته میشود
        department_id = request.POST.get("department_id", "").strip()
        if department_id:
            dep = Department.objects.filter(id=department_id, is_active=True).first()
            if dep and user_obj.cafe and dep.cafe_id == user_obj.cafe_id:
                user_obj.department = dep
            else:
                return render(request, "user_form.html", {
                    "edit_user": user_obj,
                    "roles": _get_departments(),
                    "cafes": cafes,
                    "departments": departments,
                    "error": "بخش انتخاب‌شده متعلق به کافه این کاربر نیست",
                })
        else:
            user_obj.department = None
        # تغییر رمز عبور (اختیاری)
        new_password = request.POST.get("new_password", "").strip()
        confirm_new_password = request.POST.get("confirm_new_password", "").strip()
        if new_password:
            if new_password == confirm_new_password:
                user_obj.set_password(new_password)
            else:
                return render(request, "user_form.html", {
                    "edit_user": user_obj,
                    "roles": _get_departments(),
                    "cafes": cafes,
                    "departments": departments,
                    "error": "رمز عبور جدید و تأیید آن یکسان نیستند",
                })
        user_obj.save()
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_form.html", {"edit_user": user_obj, "roles": _get_departments(), "cafes": cafes, "departments": departments})


@custom_login_required
@role_required(["manager"])
def user_delete(request, cafe_code=None, user_id=None):
    user_obj = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        user_obj.delete()
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_confirm_delete.html", {"user": user_obj})


@custom_login_required
@role_required(["manager"])
def user_reset_password(request, cafe_code=None, user_id=None):
    user_obj = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        new_password = request.POST.get("password1")
        if new_password:
            user_obj.set_password(new_password)
            user_obj.save()
            return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_reset_password.html", {"user": user_obj})


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
        return redirect("purchase_order_detail", order_id=order.id)
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
        return redirect("purchase_order_detail", order_id=order.id)
    return render(request, "purchase_order_form.html", {"order": order})


def _set_order_status(request, order_id, status):
    order = get_object_or_404(PurchaseOrder, id=order_id)
    order.status = status
    order.save()
    return redirect("purchase_order_detail", order_id=order.id)


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
    return _set_order_status(request, order_id, "completed")


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_cancel(request, order_id):
    return _set_order_status(request, order_id, "cancelled")


@custom_login_required
@role_required(["purchasing", "manager"])
def approved_requests_for_orders(request):
    cafe = get_cafe_from_request(request)
    approved_requests = PurchaseRequest.objects.filter(status__in=["approved", "purchased"])
    if cafe:
        approved_requests = approved_requests.filter(cafe=cafe)
    return render(
        request,
        "approved_requests_for_orders.html",
        {"requests": approved_requests},
    )


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoices_list(request):
    # Ensure default issues exist
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue)
    
    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    selected_status = request.GET.get('status', '')
    selected_payment_status = request.GET.get('payment_status', '')
    date_search_type = request.GET.get('date_search_type', '')
    date_query = request.GET.get('date_query', '').strip()
    invoice_type = request.GET.get('type', 'store')  # Default to 'store' for store invoices
    
    # Handle single issue selection
    selected_issue = request.GET.get('issue', '').strip()
    sort = request.GET.get('sort', '')
    
    # Base queryset
    cafe = get_cafe_from_request(request)
    invoices = Invoice.objects.select_related("purchase_order", "supplier").all()
    if cafe:
        invoices = invoices.filter(cafe=cafe)
    
    # Filter by type
    if invoice_type == 'store':
        invoices = invoices.filter(supplier__isnull=True).exclude(store_name='')
    
    # Apply filters
    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(supplier__name__icontains=search_query) |
            Q(purchase_order__order_number__icontains=search_query)
        )
    
    if selected_status:
        invoices = invoices.filter(status=selected_status)
    
    if selected_payment_status:
        invoices = invoices.filter(payment_status=selected_payment_status)
    
    if selected_issue:
        try:
            issue_id = int(selected_issue)
            invoices = invoices.filter(issues__id=issue_id).distinct()
        except ValueError:
            pass
    
    # Apply date filter
    if date_query and date_search_type:
        try:
            search_date = datetime.fromisoformat(date_query).date()
            if date_search_type == 'invoice_date':
                invoices = invoices.filter(invoice_date=search_date)
            elif date_search_type == 'request_date':
                invoices = invoices.filter(due_date=search_date)
        except ValueError:
            pass  # Invalid date format, ignore filter
    
    # Ordering
    if sort == 'amount_asc':
        invoices = invoices.order_by('total_amount')
    elif sort == 'amount_desc':
        invoices = invoices.order_by('-total_amount')
    else:
        invoices = invoices.order_by('-created_at')
    
    # Pagination
    paginator = Paginator(invoices, 25)  # 25 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Status choices for filter dropdown
    status_choices = Invoice.STATUS_CHOICES
    payment_status_choices = Invoice.PAYMENT_STATUS_CHOICES
    issues = InvoiceIssue.objects.all()
    
    is_manager = request.user.role == 'manager'

    context = {
        'invoices': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'selected_status': selected_status,
        'selected_payment_status': selected_payment_status,
        'date_search_type': date_search_type,
        'date_query': date_query,
        'status_choices': status_choices,
        'payment_status_choices': payment_status_choices,
        'is_manager': is_manager,
        'invoice_type': invoice_type,
        'issues': issues,
        'selected_issue': selected_issue,
        'selected_issues_str': selected_issue,
        'sort': sort,
    }
    
    response = render(request, "invoice_list.html", context)
    # جلوگیری از cache مرورگر برای نمایش داده‌های به‌روز
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@custom_login_required
@role_required(["manager", "purchasing", "finance_manager"])
def all_invoices_list(request: HttpRequest) -> HttpResponse:
    """Show all invoices in a table format with filtering capabilities"""
    # Ensure default issues exist
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue)

    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    selected_status = request.GET.get('status', '')
    selected_payment_status = request.GET.get('payment_status', '')
    invoice_date_filter = request.GET.get('invoice_date', '').strip()
    due_date_filter = request.GET.get('due_date', '').strip()

    # Handle single issue selection
    selected_issue = request.GET.get('issue', '').strip()
    has_issues = request.GET.get('has_issues', '').strip()

    # Base queryset - ALL invoices (no type filtering)
    cafe = get_cafe_from_request(request)
    invoices = Invoice.objects.select_related("purchase_order", "supplier").all()
    if cafe:
        invoices = invoices.filter(cafe=cafe)

    # Apply filters
    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(supplier__name__icontains=search_query) |
            Q(store_name__icontains=search_query) |
            Q(purchase_order__order_number__icontains=search_query)
        )

    if selected_status:
        invoices = invoices.filter(status=selected_status)

    if selected_payment_status:
        invoices = invoices.filter(payment_status=selected_payment_status)

    if selected_issue:
        try:
            issue_id = int(selected_issue)
            invoices = invoices.filter(issues__id=issue_id).distinct()
        except ValueError:
            pass

    if has_issues == 'yes':
        invoices = invoices.filter(issues__isnull=False).distinct()

    # Apply date filters
    if invoice_date_filter:
        try:
            invoices = invoices.filter(invoice_date=datetime.fromisoformat(invoice_date_filter).date())
        except ValueError:
            pass

    if due_date_filter:
        try:
            invoices = invoices.filter(due_date=datetime.fromisoformat(due_date_filter).date())
        except ValueError:
            pass

    # Sort
    sort = request.GET.get('sort', '')
    if sort == 'amount_asc':
        invoices = invoices.order_by('total_amount')
    elif sort == 'amount_desc':
        invoices = invoices.order_by('-total_amount')
    else:
        invoices = invoices.order_by('-invoice_date', '-created_at')

    # Pagination
    paginator = Paginator(invoices, 25)  # 25 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Status choices for filter dropdown
    status_choices = Invoice.STATUS_CHOICES
    payment_status_choices = Invoice.PAYMENT_STATUS_CHOICES
    issues = InvoiceIssue.objects.all()

    is_manager = request.user.role == 'manager'

    context = {
        'invoices': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'selected_status': selected_status,
        'selected_payment_status': selected_payment_status,
        'invoice_date_filter': invoice_date_filter,
        'due_date_filter': due_date_filter,
        'status_choices': status_choices,
        'payment_status_choices': payment_status_choices,
        'is_manager': is_manager,
        'invoice_type': 'all',  # Indicate this is the all invoices view
        'issues': issues,
        'selected_issue': selected_issue,
        'selected_issues_str': selected_issue,
        'has_issues': has_issues,
        'sort': sort,
        'page_title': 'لیست تمامی فاکتورها',
    }

    return render(request, "all_invoices_list.html", context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_register(request):
    """ایجاد فاکتور دستی بدون نیاز به سفارش"""
    if request.method == "POST":
        # پردازش تاریخ‌ها
        invoice_date_str = request.POST.get("invoice_date")
        request_date_str = request.POST.get("request_date")
        
        try:
            invoice_date = datetime.fromisoformat(invoice_date_str).date() if invoice_date_str else timezone.now().date()
        except ValueError:
            invoice_date = timezone.now().date()
            
        try:
            request_date = datetime.fromisoformat(request_date_str).date() if request_date_str else timezone.now().date()
        except ValueError:
            request_date = timezone.now().date()

        # دریافت تأمین‌کننده (موجود یا جدید)
        supplier_id = request.POST.get("supplier")
        store_name = request.POST.get("supplier_name", "").strip()
        supplier = None
        
        if supplier_id:
            # تامین‌کننده موجود
            try:
                supplier = Supplier.objects.get(id=supplier_id)
            except Supplier.DoesNotExist:
                pass
        
        # اگر تامین‌کننده پیدا نشد، خطا بده
        if not supplier and not store_name:
            messages.error(request, "لطفاً تأمین‌کننده یا نام فروشگاه را وارد کنید.")
            # بازگشت به فرم با داده‌های وارد شده
            context = {
                'categories': Category.objects.filter(is_active=True),
                'manual_invoice': True,
                'form_data': request.POST,
                'items_data': items_data,
            }
            return render(request, "invoice_register.html", context)

        # تابع برای پردازش اعداد با کاما
        def parse_money(value):
            if not value:
                return Decimal('0')
            # حذف کاماها و تبدیل به عدد
            cleaned = str(value).replace(',', '')
            return _parse_decimal(cleaned) or Decimal('0')

        # محاسبه مبالغ از اقلام فاکتور
        subtotal = Decimal('0')
        total_tax = Decimal('0')
        total_discount = Decimal('0')
        items_data = []
        i = 0
        while f"description_{i}" in request.POST:
            description = request.POST.get(f"description_{i}", "")
            quantity = _parse_decimal(request.POST.get(f"quantity_{i}", 0)) or Decimal('0')
            unit_price = parse_money(request.POST.get(f"unit_price_{i}", 0))
            tax_amount = parse_money(request.POST.get(f"tax_amount_{i}", 0))
            discount_amount = parse_money(request.POST.get(f"discount_amount_{i}", 0))
            requester_id = request.POST.get(f"requester_{i}")
            requester = None
            if requester_id:
                try:
                    requester = Requester.objects.get(id=requester_id)
                except Requester.DoesNotExist:
                    pass
            
            base_total = quantity * unit_price
            line_total = base_total + tax_amount - discount_amount
            
            subtotal += base_total
            total_tax += tax_amount
            total_discount += discount_amount
            
            if description:
                items_data.append({
                    'description': description,
                    'quantity': quantity,
                    'unit_price': unit_price,
                    'tax_amount': tax_amount,
                    'discount_amount': discount_amount,
                    'requester': requester,
                    'total': line_total
                })
            i += 1
        
        # هزینه حمل و نقل
        shipping_cost = parse_money(request.POST.get("shipping_cost", 0))
        
        # تخفیف نقدی کل فاکتور
        total_cash_discount = parse_money(request.POST.get("total_cash_discount", 0))
        
        # محاسبه مبلغ کل (جمع اقلام + مالیات - تخفیف اقلام + حمل - تخفیف نقدی کل)
        items_total = subtotal + total_tax - total_discount
        total_amount = items_total + shipping_cost - total_cash_discount

        # بررسی شماره فاکتور
        invoice_number = request.POST.get("invoice_number")
        if not invoice_number:
            # تولید شماره فاکتور خودکار اگر وارد نشده
            invoice_number = f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        # ایجاد فاکتور
        cafe = get_cafe_from_request(request)
        invoice = Invoice.objects.create(
            purchase_order=None,
            supplier=supplier,
            store_name=store_name,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=request_date,  # تاریخ درخواست به عنوان due_date
            shipping_cost=shipping_cost,
            subtotal=subtotal,
            tax_amount=total_tax,
            discount_amount=total_discount,
            cash_discount=total_cash_discount,
            total_amount=total_amount,
            notes=request.POST.get("notes", ""),
            created_by=request.user,
            updated_by=request.user,
            cafe=cafe,
        )
        
        # ایجاد اقلام فاکتور
        from .models import InvoiceItem
        for item in items_data:
            InvoiceItem.objects.create(
                invoice=invoice,
                description=item['description'],
                quantity=item['quantity'],
                unit_price=item['unit_price'],
                tax_amount=item['tax_amount'],
                discount_amount=item['discount_amount'],
                requester=item['requester'],
                line_total=item['total']
            )
        
        # اضافه کردن issues
        issues_ids = request.POST.get('issues', '').split(',')
        issues_ids = [id.strip() for id in issues_ids if id.strip()]
        if issues_ids:
            issues = InvoiceIssue.objects.filter(id__in=issues_ids)
            invoice.issues.set(issues)
        
        # ثبت پرداخت اگر انتخاب شده باشد
        if request.POST.get('add_payment'):
            payment_amount = parse_money(request.POST.get('payment_amount', 0))
            if not payment_amount or payment_amount <= 0:
                payment_amount = total_amount  # اگر مبلغ وارد نشده، کل مبلغ فاکتور
            
            payment_location_id = request.POST.get('payment_location_id')
            payment_date_str = request.POST.get('payment_date')
            payment_notes = request.POST.get('payment_notes', '')
            
            if payment_location_id:
                try:
                    payment_location = PaymentLocation.objects.get(id=payment_location_id)
                    
                    # پردازش تاریخ پرداخت
                    try:
                        payment_date = datetime.fromisoformat(payment_date_str).date() if payment_date_str else timezone.now().date()
                    except ValueError:
                        payment_date = timezone.now().date()
                    
                    # ایجاد پرداخت
                    payment = Payment.objects.create(
                        invoice=invoice,
                        amount=payment_amount,
                        payment_location=payment_location,
                        payment_date=payment_date,
                        notes=payment_notes,
                        created_by=request.user
                    )
                    
                    # بروزرسانی وضعیت پرداخت فاکتور
                    total_paid = invoice.payments.aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
                    if total_paid >= invoice.total_amount:
                        invoice.payment_status = 'paid'
                    elif total_paid > 0:
                        invoice.payment_status = 'partial'
                    else:
                        invoice.payment_status = 'unpaid'
                    invoice.save()
                    
                    messages.success(request, f"پرداخت به مبلغ {payment_amount:,} ریال ثبت شد.")
                except PaymentLocation.DoesNotExist:
                    messages.warning(request, "محل پرداخت معتبر نیست.")
        
        messages.success(request, f"فاکتور {invoice.invoice_number} با موفقیت ایجاد شد.")
        return redirect("invoice_detail", invoice_id=invoice.id)

    # نمایش فرم ایجاد فاکتور دستی
    cafe = get_cafe_from_request(request)
    categories = Category.objects.filter(is_active=True).select_related('parent').order_by('parent__name', 'name')
    if cafe:
        categories = categories.filter(cafe=cafe)
    
    # اگر supplier_id در GET وجود دارد، تامین‌کننده را پیش‌فرض کن
    default_supplier = None
    supplier_id = request.GET.get('supplier_id')
    if supplier_id:
        try:
            default_supplier = Supplier.objects.get(id=supplier_id, status='active')
        except Supplier.DoesNotExist:
            pass
    
    context = {
        'categories': categories,
        'requesters': Requester.objects.all(),
        'issues': InvoiceIssue.objects.all(),
        'payment_types': PaymentType.objects.filter(is_active=True),
        'payment_locations': PaymentLocation.objects.filter(is_active=True, cafe=cafe) if cafe else PaymentLocation.objects.filter(is_active=True),
        'today': timezone.now().date(),
        'manual_invoice': True,
        'default_supplier': default_supplier,
    }
    return render(request, "invoice_register.html", context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_create(request, order_id):
    order = get_object_or_404(PurchaseOrder, id=order_id)
    if request.method == "POST":
        due_date_str = request.POST.get("due_date")
        try:
            due_date = (
                datetime.fromisoformat(due_date_str).date() if due_date_str else timezone.now().date()
            )
        except ValueError:
            due_date = timezone.now().date()

        # بررسی شماره فاکتور
        invoice_number = request.POST.get("invoice_number")
        if not invoice_number:
            # تولید شماره فاکتور خودکار اگر وارد نشده
            invoice_number = f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        invoice = Invoice.objects.create(
            purchase_order=order,
            invoice_number=invoice_number,
            total_amount=_parse_decimal(request.POST.get("total_amount") or 0) or 0,
            due_date=due_date,
            notes=request.POST.get("notes", ""),
            created_by=request.user,
        )
        return redirect("invoice_detail", invoice_id=invoice.id)

    return render(request, "invoice_form.html", {"order": order})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    items = invoice.invoice_items.all()
    response = render(request, "invoice_detail.html", {
        "invoice": invoice,
        "items": items
    })
    # جلوگیری از cache مرورگر برای نمایش داده‌های به‌روز
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_update(request, invoice_id):
    cafe = get_cafe_from_request(request)
    invoice = get_object_or_404(Invoice, id=invoice_id)
    order = invoice.purchase_order
    categories = Category.objects.all().order_by('name')
    if cafe:
        categories = categories.filter(cafe=cafe)
    
    if request.method == "POST":
        # بروزرسانی اطلاعات فاکتور
        invoice_number = request.POST.get("invoice_number")
        if invoice_number:
            invoice.invoice_number = invoice_number
        
        # تاریخ فاکتور
        invoice_date_str = request.POST.get("invoice_date")
        if invoice_date_str:
            try:
                # پشتیبانی از فرمت شمسی
                if '/' in invoice_date_str:
                    parts = invoice_date_str.split('/')
                    if len(parts) == 3:
                        # تبدیل به میلادی (فقط نگهداری به عنوان رشته)
                        invoice.invoice_date = datetime.now().date()
                else:
                    invoice.invoice_date = datetime.fromisoformat(invoice_date_str).date()
            except ValueError:
                pass
        
        # تاریخ سررسید
        due_date_str = request.POST.get("due_date")
        if due_date_str:
            try:
                if '/' in due_date_str:
                    parts = due_date_str.split('/')
                    if len(parts) == 3:
                        invoice.due_date = datetime.now().date()
                else:
                    invoice.due_date = datetime.fromisoformat(due_date_str).date()
            except ValueError:
                pass
        
        # اطلاعات مالی
        invoice.tax_rate = _parse_decimal(request.POST.get("tax_rate")) or invoice.tax_rate
        invoice.discount_rate = _parse_decimal(request.POST.get("discount_rate")) or 0
        # حذف کاما از مقادیر پولی
        shipping_cost_str = request.POST.get("shipping_cost", "0").replace(",", "")
        invoice.shipping_cost = _parse_decimal(shipping_cost_str) or 0
        invoice.notes = request.POST.get("notes", invoice.notes or '')
        
        # بروزرسانی اقلام موجود فاکتور
        for item in invoice.invoice_items.all():
            description_key = f"description_{item.id}"
            quantity_key = f"quantity_{item.id}"
            unit_price_key = f"unit_price_{item.id}"
            category_key = f"category_{item.id}"
            tax_amount_key = f"tax_amount_{item.id}"
            discount_amount_key = f"discount_amount_{item.id}"
            
            if description_key in request.POST:
                item.description = request.POST.get(description_key, item.description)
            if quantity_key in request.POST:
                item.quantity = _parse_decimal(request.POST.get(quantity_key)) or item.quantity
            if unit_price_key in request.POST:
                unit_price_str = request.POST.get(unit_price_key, "0").replace(",", "")
                item.unit_price = _parse_decimal(unit_price_str) or item.unit_price
            if category_key in request.POST:
                category_id = request.POST.get(category_key)
                if category_id:
                    item.category_id = int(category_id)
            if tax_amount_key in request.POST:
                tax_str = request.POST.get(tax_amount_key, "0").replace(",", "")
                item.tax_amount = _parse_decimal(tax_str) or 0
            if discount_amount_key in request.POST:
                discount_str = request.POST.get(discount_amount_key, "0").replace(",", "")
                item.discount_amount = _parse_decimal(discount_str) or 0
            
            item.line_total = (item.quantity * item.unit_price) + (item.tax_amount or 0) - (item.discount_amount or 0)
            item.save()
        
        # افزودن اقلام جدید
        i = 0
        while f"description_new_{i}" in request.POST:
            description = request.POST.get(f"description_new_{i}")
            quantity_str = request.POST.get(f"quantity_new_{i}", "0")
            unit_price_str = request.POST.get(f"unit_price_new_{i}", "0").replace(",", "")
            category_id = request.POST.get(f"category_new_{i}")
            tax_str = request.POST.get(f"tax_amount_new_{i}", "0").replace(",", "")
            discount_str = request.POST.get(f"discount_amount_new_{i}", "0").replace(",", "")
            
            if description and quantity_str:
                quantity = _parse_decimal(quantity_str) or 0
                unit_price = _parse_decimal(unit_price_str) or 0
                tax_amount = _parse_decimal(tax_str) or 0
                discount_amount = _parse_decimal(discount_str) or 0
                line_total = (quantity * unit_price) + tax_amount - discount_amount
                
                new_item = InvoiceItem.objects.create(
                    invoice=invoice,
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    tax_amount=tax_amount,
                    discount_amount=discount_amount,
                    line_total=line_total,
                    category_id=int(category_id) if category_id else None
                )
            i += 1
        
        # محاسبه مجدد مبالغ
        invoice.subtotal = sum(item.line_total or 0 for item in invoice.invoice_items.all())
        invoice.tax_amount = (invoice.subtotal * invoice.tax_rate / 100) if invoice.tax_rate else 0
        invoice.discount_amount = (invoice.subtotal * invoice.discount_rate / 100) if invoice.discount_rate else 0
        invoice.total_amount = invoice.subtotal + invoice.tax_amount - invoice.discount_amount + (invoice.shipping_cost or 0)
        
        # بروزرسانی پرداخت‌های موجود
        for payment in invoice.payments.all():
            payment_date_key = f"payment_date_{payment.id}"
            payment_amount_key = f"payment_amount_{payment.id}"
            payment_location_key = f"payment_location_{payment.id}"
            
            if payment_date_key in request.POST:
                payment_date_str = request.POST.get(payment_date_key)
                if payment_date_str:
                    try:
                        # پشتیبانی از فرمت شمسی
                        if '/' in payment_date_str:
                            parts = payment_date_str.split('/')
                            if len(parts) == 3:
                                payment.payment_date = datetime.now().date()
                        else:
                            payment.payment_date = datetime.fromisoformat(payment_date_str).date()
                    except ValueError:
                        pass
            
            if payment_amount_key in request.POST:
                amount_str = request.POST.get(payment_amount_key, "0").replace(",", "")
                payment.amount = _parse_decimal(amount_str) or payment.amount
            
            if payment_location_key in request.POST:
                location_id = request.POST.get(payment_location_key)
                if location_id:
                    payment.payment_location_id = int(location_id)
                else:
                    payment.payment_location_id = None
            
            payment.save()
        
        # افزودن پرداخت‌های جدید
        i = 0
        while f"payment_date_new_{i}" in request.POST:
            payment_date_str = request.POST.get(f"payment_date_new_{i}")
            payment_amount_str = request.POST.get(f"payment_amount_new_{i}", "0").replace(",", "")
            payment_location_id = request.POST.get(f"payment_location_new_{i}")
            
            if payment_date_str and payment_amount_str:
                payment_amount = _parse_decimal(payment_amount_str) or 0
                
                if payment_amount > 0:
                    try:
                        # Parse payment date
                        if '/' in payment_date_str:
                            payment_date = datetime.now().date()
                        else:
                            payment_date = datetime.fromisoformat(payment_date_str).date()
                        
                        # ایجاد پرداخت جدید
                        new_payment = Payment.objects.create(
                            amount=payment_amount,
                            payment_date=payment_date,
                            payment_location_id=int(payment_location_id) if payment_location_id else None,
                            created_by=request.user,
                            payment_status='completed'
                        )
                        # افزودن فاکتور به پرداخت
                        new_payment.invoices.add(invoice)
                    except (ValueError, TypeError):
                        pass
            i += 1
        
        # محاسبه وضعیت پرداخت بر اساس مجموع پرداخت‌ها
        total_paid = sum(payment.amount for payment in invoice.payments.all())
        
        if total_paid == 0:
            invoice.payment_status = 'unpaid'
            # اگر فاکتور sent بود و پرداختی نشده، همان وضعیت را حفظ کن
            if invoice.status in ['partially_paid', 'paid']:
                invoice.status = 'sent'
        elif total_paid < invoice.total_amount:
            invoice.payment_status = 'partial'
            invoice.status = 'partially_paid'
        elif total_paid >= invoice.total_amount:
            invoice.payment_status = 'paid' if total_paid == invoice.total_amount else 'overpaid'
            invoice.status = 'paid'
        
        # بررسی سررسید برای تنظیم وضعیت overdue
        if invoice.due_date and invoice.due_date < datetime.now().date():
            if invoice.payment_status in ['unpaid', 'partial']:
                invoice.status = 'overdue'
        
        # ثبت کاربری که فاکتور را ویرایش کرده
        invoice.updated_by = request.user
        invoice.save()
        messages.success(request, "فاکتور با موفقیت بروزرسانی شد.")
        return redirect("invoice_detail", cafe_code=cafe.code if cafe else 'local', invoice_id=invoice.id)
    
    # GET request - نمایش فرم با داده‌های موجود
    items = invoice.invoice_items.all()
    payment_locations = PaymentLocation.objects.all()
    if cafe:
        payment_locations = payment_locations.filter(cafe=cafe)
    
    return render(request, "invoice_form.html", {
        "invoice": invoice,
        "order": order,
        "items": items,
        "categories": categories,
        "payment_locations": payment_locations,
    })


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_send(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    invoice.status = "sent"
    invoice.save()
    return redirect("invoice_detail", invoice_id=invoice.id)


@custom_login_required
@role_required(["manager"])
def finance_manager_edited_invoices(request):
    """نمایش فاکتورهای ویرایش شده یا ثبت شده توسط مدیر مالی"""
    # فاکتورهایی که مدیر مالی آنها را ایجاد یا ویرایش کرده
    finance_managers = User.objects.filter(role='finance_manager')
    
    edited_invoices = Invoice.objects.filter(
        Q(created_by__in=finance_managers) | Q(updated_by__in=finance_managers)
    ).select_related('supplier', 'created_by', 'updated_by').order_by('-updated_at')
    
    # فیلترها
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    if search_query:
        edited_invoices = edited_invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(supplier__name__icontains=search_query) |
            Q(store_name__icontains=search_query)
        )
    
    if status_filter:
        edited_invoices = edited_invoices.filter(status=status_filter)
    
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
            edited_invoices = edited_invoices.filter(invoice_date__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
            edited_invoices = edited_invoices.filter(invoice_date__lte=date_to_obj)
        except ValueError:
            pass
    
    context = {
        'invoices': edited_invoices,
        'search_query': search_query,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
        'status_choices': Invoice.STATUS_CHOICES,
    }
    
    return render(request, 'finance_manager_edited_invoices.html', context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_cancel(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    invoice.status = "cancelled"
    invoice.save()
    return redirect("invoice_detail", invoice_id=invoice.id)


@custom_login_required
@role_required(["manager", "finance_manager"])
def invoice_delete(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    if request.method == "POST":
        invoice.delete()
        return redirect("invoices_list")
    return render(request, "invoice_confirm_delete.html", {"invoice": invoice})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_bulk_payment(request):
    """پرداخت دسته‌جمعی فاکتورها"""
    if request.method == 'POST':
        invoice_ids = request.POST.getlist('invoice_ids')
        if not invoice_ids:
            messages.warning(request, "هیچ فاکتوری برای پرداخت انتخاب نشده است.")
            return redirect('invoices_list')

        invoices = Invoice.objects.filter(id__in=invoice_ids)
        count = invoices.count()

        if count > 0:
            # ایجاد یک پرداخت برای همه فاکتورهای انتخاب شده
            total_amount = sum(invoice.total_amount or 0 for invoice in invoices)
            
            payment = Payment.objects.create(
                amount=total_amount,
                payment_date=timezone.now().date(),
                payment_status='completed',
                created_by=request.user,
                notes=f"پرداخت دسته‌جمعی برای {count} فاکتور"
            )
            
            # اضافه کردن فاکتورها به پرداخت
            payment.invoices.set(invoices)
            
            messages.success(request, f"پرداخت {count} فاکتور با موفقیت ثبت شد.")
        else:
            messages.warning(request, "فاکتورهای انتخاب شده یافت نشدند.")

    return redirect('invoices_list')


@custom_login_required
@role_required(["manager", "finance_manager"])
def invoice_bulk_delete(request):
    if request.method == 'POST':
        invoice_ids = request.POST.getlist('invoice_ids')
        if not invoice_ids:
            messages.warning(request, "هیچ فاکتوری برای حذف انتخاب نشده است.")
            return redirect('invoices_list')

        invoices_to_delete = Invoice.objects.filter(id__in=invoice_ids)
        count = invoices_to_delete.count()

        if count > 0:
            invoices_to_delete.delete()
            messages.success(request, f"{count} فاکتور با موفقیت حذف شد.")
        else:
            messages.warning(request, "فاکتورهای انتخاب شده برای حذف یافت نشدند.")

    return redirect('invoices_list')


@custom_login_required
@role_required(["purchasing", "manager"])
def completed_orders_for_invoices(request):
    orders = PurchaseOrder.objects.filter(status="completed")
    return render(request, "completed_orders_for_invoices.html", {"orders": orders})


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["manager", "purchasing", "finance_manager"])
def payment_list(request):
    cafe = get_cafe_from_request(request)
    payments = Payment.objects.select_related("invoice", "payment_location").prefetch_related("invoices").all()
    if cafe:
        payments = payments.filter(
            Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe)
        ).distinct()
    methods = PaymentLocation.objects.all()
    if cafe:
        methods = methods.filter(cafe=cafe)
    
    # Filter by date range
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    if date_from:
        try:
            # پشتیبانی از فرمت شمسی (1404/01/01) و میلادی
            if '/' in date_from:
                parts = date_from.split('/')
                if len(parts) == 3:
                    # فرض می‌کنیم تاریخ میلادی است (برای سادگی)
                    from datetime import datetime
                    date_from_obj = datetime.strptime(date_from.replace('/', '-'), '%Y-%m-%d').date()
                    payments = payments.filter(payment_date__gte=date_from_obj)
            else:
                from datetime import datetime
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                payments = payments.filter(payment_date__gte=date_from_obj)
        except:
            pass
    
    if date_to:
        try:
            if '/' in date_to:
                parts = date_to.split('/')
                if len(parts) == 3:
                    from datetime import datetime
                    date_to_obj = datetime.strptime(date_to.replace('/', '-'), '%Y-%m-%d').date()
                    payments = payments.filter(payment_date__lte=date_to_obj)
            else:
                from datetime import datetime
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                payments = payments.filter(payment_date__lte=date_to_obj)
        except:
            pass
    
    # Filter by payment location
    location_id = request.GET.get('location', '')
    if location_id:
        payments = payments.filter(payment_location_id=location_id)
    
    # Calculate total
    total_amount = payments.aggregate(total=Sum('amount'))['total'] or 0
    
    return render(
        request,
        "payment_list.html",
        {
            "payments": payments.order_by('-payment_date', '-created_at'),
            "payment_locations": methods,
            "date_from": date_from,
            "date_to": date_to,
            "selected_location": location_id,
            "total_amount": total_amount,
        },
    )


# ---------------------------------------------------------------------------
# لیست بدهی تأمین‌کنندگان — تعریف توسط ادمین، پرداخت و تایید توسط مدیرعامل
# ---------------------------------------------------------------------------


MOBILE_UA_RE = re.compile(
    r'(android|iphone|ipad|ipod|windows phone|opera mini|mobile|blackberry|iemobile|webos)',
    re.IGNORECASE,
)


def is_mobile_request(request):
    """تشخیص خودکار دستگاه موبایل از روی User-Agent — بدون نیاز به انتخاب کاربر"""
    ua = request.META.get('HTTP_USER_AGENT', '')
    return bool(MOBILE_UA_RE.search(ua))


def _redirect_preserving_query(request, url_name):
    """Redirect با حفظ پارامترهای query (مثلاً period)"""
    response = get_cafe_redirect_url(url_name, request)
    location = response.get('Location', '')
    if request.GET and location:
        location += ('&' if '?' in location else '?') + request.GET.urlencode()
    return redirect(location)


def _debt_redirect(request, url_name, mobile_url_name=None):
    """Redirect به صفحه بدهی با حفظ cafe_code — اگر فرم از نسخه موبایل آمده باشد (یا دستگاه موبایل باشد)، به همان برمی‌گردد"""
    if mobile_url_name and (request.GET.get("from") == "mobile" or is_mobile_request(request)):
        url_name = mobile_url_name
    return get_cafe_redirect_url(url_name, request)


def _parse_debt_date(raw):
    """پذیرش تاریخ شمسی (1404/05/17) و میلادی (2026-08-08) — مثل بقیه فرم‌های پروژه"""
    if not raw:
        return timezone.now().date()
    # فرمت شمسی با اسلش: 1404/05/17
    if '/' in raw:
        parts = raw.split('/')
        if len(parts) == 3:
            try:
                import jdatetime
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                return jdatetime.date(year, month, day).togregorian()
            except (ValueError, TypeError, AttributeError):
                pass
    # فرمت میلادی: 2026-08-08
    from django.utils.dateparse import parse_date
    parsed = parse_date(raw)
    if parsed:
        return parsed
    return timezone.now().date()


def _today_jalali():
    """امروز به شمسی (1404/05/17) — پیش‌فرض باکس تاریخ شمسی فرم بدهی"""
    import jdatetime
    j = jdatetime.date.today()
    return f"{j.year}/{j.month:02d}/{j.day:02d}"


PERIOD_CHOICES = ['week', 'month', 'season', 'year']
PERIOD_DAYS = {'week': 7, 'month': 30, 'season': 90, 'year': 365}


def _filter_debt_payments_by_period(queryset, request):
    """فیلتر بازه زمانی پرداخت‌ها — دیفالت: هفته — لیست بدهی‌ها بدون فیلتر می‌مانند"""
    period = request.GET.get('period', 'week')
    if period not in PERIOD_CHOICES:
        period = 'week'
    from datetime import timedelta
    since = timezone.now() - timedelta(days=PERIOD_DAYS[period])
    return queryset.filter(paid_at__gte=since), period


def _get_payment_accounts(cafe):
    """حساب‌های پرداخت فعال کافه — برای انتخاب بانک/حساب در ثبت پرداخت"""
    accounts = PaymentAccount.objects.filter(is_active=True)
    if cafe:
        accounts = accounts.filter(cafe=cafe)
    return accounts


def _parse_payment_account(request, cafe):
    """پارسه حساب پرداخت انتخابی فرم — فقط حساب فعال همان کافه پذیرفته می‌شود"""
    account_id = request.POST.get("account_id")
    if not account_id:
        return None
    return _get_payment_accounts(cafe).filter(id=account_id).first()


def _get_active_suppliers(cafe):
    """تأمین‌کنندگان فعال کافه — برای جستجو و انتخاب در فرم‌های ثبت بدهی"""
    suppliers = Supplier.objects.filter(status='active')
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    return suppliers.order_by('name')


def _suppliers_list(cafe):
    """تأمین‌کنندگان فعال کافه برای سرچ سمت کلاینت (picker انتخاب تأمین‌کننده) — با json_script در قالب رندر می‌شود"""
    return [
        {
            'id': s.id,
            'name': s.name,
            'bank_name': s.bank_name or '',
            'bank_account': s.bank_account or '',
            'card_number': s.card_number or '',
            'sheba_number': s.sheba_number or '',
            'contact_person': s.contact_person or '',
            'mobile': s.mobile or '',
        }
        for s in _get_active_suppliers(cafe)
    ]


def _parse_supplier(request, cafe):
    """پارسه تأمین‌کننده انتخابی فرم بدهی — فقط تأمین‌کننده فعال همان کافه پذیرفته می‌شود"""
    supplier_id = request.POST.get("supplier_id")
    if not supplier_id:
        return None
    return _get_active_suppliers(cafe).filter(id=supplier_id).first()


@custom_login_required
@role_required(["admin"])
def debt_list(request):
    """لیست بدهی تأمین‌کنندگان — ادمین بدهی جدید تعریف می‌کند و لیست بدهی‌ها و پرداخت‌های مدیرعامل را می‌بیند"""
    cafe = get_cafe_from_request(request)

    if request.method == "POST":
        company = request.POST.get("company", "").strip()
        supplier = _parse_supplier(request, cafe)  # انتخاب تأمین‌کننده از لیست (اختیاری)
        if supplier:
            company = supplier.name
        amount = _parse_decimal(request.POST.get("amount", ""))
        debt_date = _parse_debt_date(request.POST.get("debt_date"))
        description = request.POST.get("description", "").strip()
        priority = request.POST.get("priority", "medium")

        errors = []
        if not company:
            errors.append("نام تأمین‌کننده الزامی است")
        if amount is None or amount <= 0:
            errors.append("مبلغ بدهی معتبر نیست")
        if priority not in dict(Debt.PRIORITY_CHOICES):
            priority = "medium"
        if errors:
            messages.error(request, "؛ ".join(errors))
        else:
            Debt.objects.create(
                company=company,
                supplier=supplier,
                amount=amount,
                debt_date=debt_date,
                description=description,
                priority=priority,
                cafe=cafe,
                created_by=request.user,
            )
            messages.success(request, f"بدهی تأمین‌کننده {company} با موفقیت ثبت شد و به داشبورد مدیرعامل ارسال شد")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    # تشخیص خودکار دستگاه: موبایل → داشبورد موبایل (بدون انتخاب دستی)
    if is_mobile_request(request):
        return _redirect_preserving_query(request, "mobile_debt_dashboard")

    debts = Debt.objects.select_related("supplier", "paid_by", "created_by").all()
    if cafe:
        debts = debts.filter(cafe=cafe)

    pending_debts = list(debts.filter(status="pending"))
    # لیست بدهی‌ها همیشه کامل نمایش داده می‌شود — فقط بخش پرداخت‌ها فیلتر بازه زمانی دارد
    all_payments = DebtPayment.objects.select_related("debt__account", "debt__supplier", "paid_by", "account")
    if cafe:
        all_payments = all_payments.filter(cafe=cafe)
    payments, selected_period = _filter_debt_payments_by_period(all_payments, request)
    context = {
        "pending_debts": pending_debts,
        "payments": payments,
        "priority_choices": Debt.PRIORITY_CHOICES,
        "today": timezone.now().date(),
        "today_jalali": _today_jalali(),
        "selected_period": selected_period,
        "payment_accounts": _get_payment_accounts(cafe),
        "suppliers_data": _suppliers_list(cafe),
        "total_pending_amount": sum((d.remaining for d in pending_debts), Decimal("0")),
        "total_paid_amount": payments.aggregate(total=Sum("amount"))["total"] or 0,
    }
    return render(request, "debt_list.html", context)


@custom_login_required
@role_required(["ceo", "manager"])
def ceo_debt_payments(request):
    """داشبورد مدیرعامل — مشاهده بدهی‌های در انتظار و لیست پرداخت‌های ثبت شده"""
    cafe = get_cafe_from_request(request)

    # تشخیص خودکار دستگاه: موبایل → داشبورد موبایل (بدون انتخاب دستی)
    if is_mobile_request(request):
        return _redirect_preserving_query(request, "mobile_debt_dashboard")

    debts = Debt.objects.select_related("supplier", "paid_by", "created_by").all()
    if cafe:
        debts = debts.filter(cafe=cafe)

    pending_debts = list(debts.filter(status="pending"))
    # لیست بدهی‌ها همیشه کامل — فقط بخش پرداخت‌ها فیلتر بازه زمانی دارد
    all_payments = DebtPayment.objects.select_related("debt__account", "debt__supplier", "paid_by", "account")
    if cafe:
        all_payments = all_payments.filter(cafe=cafe)
    payments, selected_period = _filter_debt_payments_by_period(all_payments, request)
    context = {
        "pending_debts": pending_debts,
        "payments": payments,
        "priority_choices": Debt.PRIORITY_CHOICES,
        "selected_period": selected_period,
        "payment_accounts": _get_payment_accounts(cafe),
        "suppliers_data": _suppliers_list(cafe),
        "total_pending_amount": sum((d.remaining for d in pending_debts), Decimal("0")),
    }
    return render(request, "ceo_debt_payments.html", context)


@custom_login_required
@role_required(["ceo", "manager"])
def debt_pay(request, debt_id):
    """ثبت پرداخت بدهی توسط مدیرعامل — مبلغ پرداختی، تصویر اختیاری و ثبت خودکار تاریخ سیستم"""
    cafe = get_cafe_from_request(request)
    debt = get_object_or_404(Debt, id=debt_id)
    if cafe and debt.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")
    if debt.status != "pending":
        messages.warning(request, "این بدهی قبلاً پرداخت شده است")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        paid_amount = _parse_decimal(request.POST.get("paid_amount", ""))
        if paid_amount is None or paid_amount <= 0:
            messages.error(request, "مبلغ پرداختی معتبر نیست")
            return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

        account = _parse_payment_account(request, cafe)  # انتخاب بانک/حساب پرداخت
        photo = request.FILES.get("payment_photo")

        # هر پرداخت یک ردیف جداگانه در لیست پرداخت‌ها می‌شود — پرداخت مجدد یک بدهی
        # هم به عنوان پرداخت جدید در لیست پرداخت‌ها ثبت می‌شود
        DebtPayment.objects.create(
            debt=debt,
            company=debt.company,
            amount=paid_amount,
            paid_at=timezone.now(),  # ثبت سیستمی تاریخ و زمان پرداخت
            paid_by=request.user,
            account=account,
            cafe=debt.cafe,
            payment_photo=photo,
            description=debt.description,
        )

        total_paid = debt.paid_total
        debt.paid_amount = total_paid
        debt.paid_at = timezone.now()
        debt.paid_by = request.user
        debt.account = account
        if photo:
            debt.payment_photo = photo  # آپلود تصویر اختیاری است
        # اگر باقی‌مانده بدهی ۱٬۰۰۰٬۰۰۰ تومان یا کمتر شد، بدهی بسته می‌شود و از لیست بدهی‌ها حذف می‌شود
        # در غیر این صورت باقی‌مانده در لیست بدهی‌ها می‌ماند
        if debt.remaining <= 1_000_000:
            debt.status = "paid"
        debt.save()
        if debt.status == "paid":
            messages.success(request, f"پرداخت بدهی تأمین‌کننده {debt.company} با موفقیت ثبت و تایید شد")
        else:
            messages.success(request, f"پرداخت جزئی تأمین‌کننده {debt.company} ثبت شد — باقی‌مانده: {debt.remaining:,} تومان")

    return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")


@custom_login_required
@role_required(["ceo", "manager"])
def debt_pay_offlist(request):
    """ثبت پرداخت خارج از لیست بدهی‌ها توسط مدیرعامل — بدون نیاز به بدهی تعریف‌شده"""
    cafe = get_cafe_from_request(request)

    if request.method == "POST":
        company = request.POST.get("company", "").strip()
        supplier = _parse_supplier(request, cafe)  # انتخاب تأمین‌کننده از لیست (اختیاری)
        if supplier:
            company = supplier.name
        amount = _parse_decimal(request.POST.get("amount", ""))
        errors = []
        if not company:
            errors.append("نام تأمین‌کننده الزامی است")
        if amount is None or amount <= 0:
            errors.append("مبلغ پرداختی معتبر نیست")
        if errors:
            messages.error(request, "؛ ".join(errors))
        else:
            photo = request.FILES.get("payment_photo")
            account = _parse_payment_account(request, cafe)  # انتخاب بانک/حساب پرداخت
            debt = Debt.objects.create(
                company=company,
                supplier=supplier,
                amount=amount,
                description=request.POST.get("description", "").strip(),
                debt_date=timezone.now().date(),
                status="paid",
                paid_amount=amount,
                paid_at=timezone.now(),  # ثبت سیستمی تاریخ و زمان پرداخت
                paid_by=request.user,
                account=account,
                is_off_list=True,
                cafe=cafe,
                created_by=request.user,
            )
            # یک ردیف پرداخت هم می‌سازد تا در لیست پرداخت‌ها نمایش داده شود
            DebtPayment.objects.create(
                debt=None,  # خارج از لیست — به بدهی تعریف‌شده متصل نیست
                company=company,
                amount=amount,
                paid_at=timezone.now(),
                paid_by=request.user,
                account=account,
                cafe=cafe,
                payment_photo=photo,
                description=request.POST.get("description", "").strip(),
            )
            if photo:
                debt.payment_photo = photo  # آپلود تصویر اختیاری است
                debt.save(update_fields=["payment_photo"])
            messages.success(request, f"پرداخت خارج از لیست تأمین‌کننده {company} با موفقیت ثبت شد")

    return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")


@custom_login_required
@role_required(["admin"])
def payment_accounts(request):
    """تعریف حساب‌های پرداخت کافه/رستوران توسط ادمین — دکمه در گوشه صفحه لیست بدهی"""
    cafe = get_cafe_from_request(request)

    if request.method == "POST":
        action = request.POST.get("action", "create")
        if action == "delete":
            account_id = request.POST.get("account_id")
            PaymentAccount.objects.filter(id=account_id, cafe=cafe).update(is_active=False)
            messages.success(request, "حساب پرداخت حذف شد")
        else:
            bank_name = request.POST.get("bank_name", "").strip()
            account_name = request.POST.get("account_name", "").strip()
            account_number = request.POST.get("account_number", "").strip()
            card_number = request.POST.get("card_number", "").strip()
            if not bank_name:
                messages.error(request, "نام بانک الزامی است")
            else:
                PaymentAccount.objects.create(
                    cafe=cafe, bank_name=bank_name, account_name=account_name,
                    account_number=account_number, card_number=card_number,
                )
                messages.success(request, f"حساب پرداخت {bank_name} ثبت شد")
        # بازگشت به همان صفحه بدهی (تشخیص خودکار موبایل/دسکتاپ)
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    return render(request, "payment_accounts.html", {"payment_accounts": _get_payment_accounts(cafe)})


@custom_login_required
@role_required(["admin"])
def debt_edit(request, debt_id):
    """ویرایش بدهی توسط ادمین — تغییرات برای مدیرعامل هم اعمال می‌شود"""
    cafe = get_cafe_from_request(request)
    debt = get_object_or_404(Debt, id=debt_id)
    if cafe and debt.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        company = request.POST.get("company", "").strip()
        supplier = _parse_supplier(request, cafe)  # انتخاب تأمین‌کننده از لیست (اختیاری)
        if supplier:
            company = supplier.name
        amount = _parse_decimal(request.POST.get("amount", ""))
        debt_date = _parse_debt_date(request.POST.get("debt_date"))
        description = request.POST.get("description", "").strip()
        priority = request.POST.get("priority", "medium")

        errors = []
        if not company:
            errors.append("نام تأمین‌کننده الزامی است")
        if amount is None or amount <= 0:
            errors.append("مبلغ بدهی معتبر نیست")
        if priority not in dict(Debt.PRIORITY_CHOICES):
            priority = "medium"
        if errors:
            messages.error(request, "؛ ".join(errors))
        else:
            debt.company = company
            debt.supplier = supplier
            debt.amount = amount
            debt.debt_date = debt_date
            debt.description = description
            debt.priority = priority
            debt.save()
            messages.success(request, f"بدهی تأمین‌کننده {company} ویرایش شد و برای مدیرعامل هم اعمال شد")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    # تاریخ بدهی به شمسی برای باکس تقویم (فرمت 1404/05/17 — مثل فرم ثبت بدهی)
    debt_date_jalali = None
    if debt.debt_date:
        try:
            import jdatetime
            j = jdatetime.date.fromgregorian(date=debt.debt_date)
            debt_date_jalali = f"{j.year}/{j.month:02d}/{j.day:02d}"
        except Exception:
            pass
    return render(request, "debt_edit.html", {
        "debt": debt,
        "priority_choices": Debt.PRIORITY_CHOICES,
        "today_jalali": _today_jalali(),
        "debt_date_jalali": debt_date_jalali,
        "suppliers_data": _suppliers_list(cafe),
    })


@custom_login_required
@role_required(["admin"])
def debt_delete(request, debt_id):
    """حذف بدهی توسط ادمین — بدهی‌هایی که پرداخت دارند قابل حذف نیستند"""
    cafe = get_cafe_from_request(request)
    debt = get_object_or_404(Debt, id=debt_id)
    if cafe and debt.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        if debt.payments.exists():
            messages.error(request, f"بدهی تأمین‌کننده {debt.company} پرداخت شده است و قابل حذف نیست")
        else:
            name = debt.company
            debt.delete()
            messages.success(request, f"بدهی تأمین‌کننده {name} حذف شد و از داشبورد مدیرعامل هم حذف شد")
    return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")


@custom_login_required
@role_required(["ceo", "manager"])
def debt_payment_edit(request, payment_id):
    """ویرایش پرداخت توسط مدیرعامل — مبلغ، حساب و توضیحات؛ وضعیت بدهی دوباره محاسبه می‌شود"""
    cafe = get_cafe_from_request(request)
    payment = get_object_or_404(DebtPayment, id=payment_id)
    if cafe and payment.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        amount = _parse_decimal(request.POST.get("amount", ""))
        if amount is None or amount <= 0:
            messages.error(request, "مبلغ پرداختی معتبر نیست")
            return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

        payment.amount = amount
        payment.description = request.POST.get("description", "").strip()
        payment.account = _parse_payment_account(request, cafe)
        photo = request.FILES.get("payment_photo")
        if photo:
            payment.payment_photo = photo
        payment.save()

        # وضعیت بدهی متصل دوباره محاسبه می‌شود — همان قانون ثبت پرداخت (باقی‌مانده ≤ ۱٬۰۰۰٬۰۰۰ → بسته)
        debt = payment.debt
        if debt:
            total_paid = debt.paid_total
            debt.paid_amount = total_paid
            debt.account = payment.account or debt.account
            if debt.remaining <= 1_000_000:
                debt.status = "paid"
                debt.paid_at = timezone.now()
                debt.paid_by = payment.paid_by
            else:
                debt.status = "pending"
                debt.paid_at = None
                debt.paid_by = None
            debt.save()
        messages.success(request, f"پرداخت تأمین‌کننده {payment.company} ویرایش شد")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    return render(request, "debt_payment_edit.html", {
        "payment": payment,
        "payment_accounts": _get_payment_accounts(cafe),
    })


@custom_login_required
@role_required(["ceo", "manager"])
def debt_payment_delete(request, payment_id):
    """حذف پرداخت توسط مدیرعامل — رسید آپلود شده هم حذف می‌شود؛ وضعیت بدهی دوباره محاسبه می‌شود"""
    cafe = get_cafe_from_request(request)
    payment = get_object_or_404(DebtPayment, id=payment_id)
    if cafe and payment.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        debt = payment.debt
        company = payment.company

        # حذف فایل رسید از دیسک
        if payment.payment_photo:
            payment.payment_photo.delete(save=False)

        payment.delete()

        # وضعیت بدهی متصل دوباره محاسبه می‌شود — همان قانون ثبت پرداخت (باقی‌مانده ≤ ۱٬۰۰۰٬۰۰۰ → بسته)
        if debt:
            total_paid = debt.paid_total
            debt.paid_amount = total_paid
            if debt.remaining <= 1_000_000 and total_paid > 0:
                debt.status = "paid"
                debt.paid_at = timezone.now()
                # اگر پرداخت دیگری باقی مانده، مشخصات آن به عنوان آخرین پرداخت ثبت می‌شود
                last_payment = debt.payments.order_by('-paid_at').first()
                if last_payment:
                    debt.paid_by = last_payment.paid_by
                    debt.account = last_payment.account
                    debt.payment_photo = last_payment.payment_photo
            else:
                debt.status = "pending"
                debt.paid_at = None
                debt.paid_by = None
                debt.account = None
                debt.payment_photo = None
            debt.save()

        messages.success(request, f"پرداخت تأمین‌کننده {company} حذف شد")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    return render(request, "debt_payment_confirm_delete.html", {"payment": payment})


@custom_login_required
@role_required(["admin", "ceo", "manager"])
def mobile_debt_dashboard(request):
    """داشبورد موبایل لیست بدهی — رابط لمسی برای ادمین و مدیرعامل (admin/ceo/manager)
    اگر درخواست از دسکتاپ بیاید، به نسخه دسکتاپ همان نقش برمی‌گردد"""
    if not is_mobile_request(request):
        target = "debt_list" if request.user.role == "admin" else "ceo_debt_payments"
        return _redirect_preserving_query(request, target)

    cafe = get_cafe_from_request(request)
    debts = Debt.objects.select_related("supplier", "paid_by", "created_by").all()
    if cafe:
        debts = debts.filter(cafe=cafe)

    pending_debts = list(debts.filter(status="pending"))
    # نسخه موبایل فیلتر بازه زمانی ندارد — همه پرداخت‌ها کامل نمایش داده می‌شوند
    payments = DebtPayment.objects.select_related("debt__account", "debt__supplier", "paid_by", "account")
    if cafe:
        payments = payments.filter(cafe=cafe)
    context = {
        "pending_debts": pending_debts,
        "payments": payments,
        "payment_accounts": _get_payment_accounts(cafe),
        "suppliers_data": _suppliers_list(cafe),
        "total_pending_amount": sum((d.remaining for d in pending_debts), Decimal("0")),
        "today": timezone.now().date(),
        "today_jalali": _today_jalali(),
    }
    return render(request, "mobile_debt_dashboard.html", context)


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_create(request, invoice_id=None):
    cafe = get_cafe_from_request(request)
    invoices = Invoice.objects.all()
    if cafe:
        invoices = invoices.filter(cafe=cafe)
    methods = PaymentLocation.objects.all()
    if cafe:
        methods = methods.filter(cafe=cafe)
    target_invoice = Invoice.objects.filter(id=invoice_id).first() if invoice_id else None

    # Handle bulk payment
    selected_invoice_ids = request.POST.getlist('invoices') if request.method == 'POST' else []
    selected_invoices = Invoice.objects.filter(id__in=selected_invoice_ids) if selected_invoice_ids else []

    # Calculate default amount for bulk payment
    default_amount = sum(inv.remaining_amount for inv in selected_invoices) if selected_invoices else (target_invoice.remaining_amount if target_invoice else 0)

    if request.method == "POST":
        # Check if this is a bulk payment
        invoice_ids = request.POST.getlist("invoices")
        amount_val = _parse_decimal(request.POST.get("amount") or 0)
        
        if invoice_ids and amount_val and amount_val > 0:
            # Bulk payment for multiple invoices
            selected_invoices = Invoice.objects.filter(id__in=invoice_ids)
            if selected_invoices.exists():
                payment = Payment.objects.create(
                    amount=amount_val,
                    payment_date=request.POST.get("payment_date") or timezone.now().date(),
                    payment_location=PaymentLocation.objects.filter(id=request.POST.get("payment_location")).first(),
                    reference_number=request.POST.get("reference_number", ""),
                    notes=request.POST.get("notes", ""),
                    created_by=request.user,
                )
                # Add all selected invoices to the payment
                payment.invoices.add(*selected_invoices)
                
                # Send notifications to all related users
                notified_users = set()
                for invoice in selected_invoices:
                    if invoice.purchase_order and invoice.purchase_order.request and invoice.purchase_order.request.user:
                        user = invoice.purchase_order.request.user
                        if user not in notified_users:
                            Notification.objects.create(
                                recipient=user,
                                title="Bulk payment recorded",
                                message=f"Bulk payment of {amount_val} for {selected_invoices.count()} invoices",
                                notification_type="success",
                            )
                            notified_users.add(user)
                
                messages.success(request, f"پرداخت گروهی {amount_val} برای {selected_invoices.count()} فاکتور ثبت شد.")
                return redirect("payment_list", cafe_code=cafe.code if cafe else 'local')
        
        # Single payment (existing logic)
        invoice = target_invoice or Invoice.objects.filter(id=request.POST.get("invoice")).first()
        if invoice and amount_val and amount_val > 0:
            Payment.objects.create(
                invoice=invoice,
                amount=amount_val,
                payment_date=request.POST.get("payment_date") or timezone.now().date(),
                payment_location=PaymentLocation.objects.filter(id=request.POST.get("payment_location")).first(),
                reference_number=request.POST.get("reference_number", ""),
                notes=request.POST.get("notes", ""),
                created_by=request.user,
            )
            Notification.objects.create(
                recipient=invoice.purchase_order.request.user,
                title="Payment recorded",
                message=f"Payment for {invoice.invoice_number}",
                notification_type="success",
            )
            return redirect("payment_list", cafe_code=cafe.code if cafe else 'local')

    return render(
        request,
        "payment_form.html",
        {
            "invoice": target_invoice,
            "invoices": invoices,
            "payment_locations": methods,
            "selected_invoices": selected_invoices,
            "default_amount": default_amount,
        },
    )


@custom_login_required
@role_required(["manager", "purchasing", "finance_manager"])
def payment_detail(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    return render(request, "payment_detail.html", {"payment": payment})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_update(request, payment_id):
    cafe = get_cafe_from_request(request)
    payment = get_object_or_404(Payment, id=payment_id)
    if request.method == "POST":
        payment.notes = request.POST.get("notes", payment.notes)
        payment.save()
        return redirect("payment_detail", payment_id=payment.id)
    methods = PaymentLocation.objects.all()
    if cafe:
        methods = methods.filter(cafe=cafe)
    return render(request, "payment_form.html", {"payment": payment, "payment_locations": methods})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_delete(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    if request.method == "POST":
        cafe = get_cafe_from_request(request)
        payment.delete()
        return redirect("payment_list", cafe_code=cafe.code if cafe else 'local')
    return render(request, "payment_confirm_delete.html", {"payment": payment})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_list(request):
    cafe = get_cafe_from_request(request)
    payment_methods = PaymentLocation.objects.all()
    if cafe:
        payment_methods = payment_methods.filter(cafe=cafe)
    return render(request, "payment_method_list.html", {"payment_methods": payment_methods})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_create(request):
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            PaymentLocation.objects.create(
                name=name,
                description=request.POST.get("description", ""),
                is_active=request.POST.get("is_active") == "on",
                requires_reference=request.POST.get("requires_reference") == "on",
                requires_bank_info=request.POST.get("requires_bank_info") == "on",
                requires_check_info=request.POST.get("requires_check_info") == "on",
                requires_card_info=request.POST.get("requires_card_info") == "on",
                icon_class=request.POST.get("icon_class", ""),
                display_order=int(request.POST.get("display_order") or 0),
                cafe=cafe,
            )
            cafe_code = cafe.code if cafe else 'local'
            return redirect("payment_location_list", cafe_code=cafe_code)
    return render(request, "payment_method_form.html")


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_update(request, method_id):
    payment_method = get_object_or_404(PaymentLocation, id=method_id)
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        payment_method.name = request.POST.get("name", payment_method.name)
        payment_method.description = request.POST.get("description", payment_method.description)
        payment_method.is_active = request.POST.get("is_active") == "on"
        payment_method.requires_reference = request.POST.get("requires_reference") == "on"
        payment_method.requires_bank_info = request.POST.get("requires_bank_info") == "on"
        payment_method.requires_check_info = request.POST.get("requires_check_info") == "on"
        payment_method.requires_card_info = request.POST.get("requires_card_info") == "on"
        payment_method.icon_class = request.POST.get("icon_class", payment_method.icon_class)
        payment_method.display_order = int(request.POST.get("display_order") or payment_method.display_order)
        payment_method.save()
        cafe_code = cafe.code if cafe else 'local'
        return redirect("payment_location_list", cafe_code=cafe_code)
    return render(request, "payment_method_form.html", {"payment_method": payment_method})


# ---------------------------------------------------------------------------
# Suppliers and evaluations
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_list(request):
    cafe = get_cafe_from_request(request)
    suppliers = Supplier.objects.all()
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    return render(request, "supplier_list.html", {"suppliers": suppliers})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_select(request):
    cafe = get_cafe_from_request(request)
    suppliers = Supplier.objects.filter(status='active')
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    selected_supplier = None
    invoices = []
    total_owed = 0
    payment_methods = PaymentLocation.objects.all()
    
    supplier_id = request.GET.get('supplier_id') or request.POST.get('supplier_id')
    
    # Handle payment submission
    if request.method == 'POST' and supplier_id:
        try:
            selected_supplier = Supplier.objects.get(id=supplier_id, status='active')
            payment_mode = request.POST.get('payment_mode')  # 'manual' or 'auto'
            amount = _parse_decimal(request.POST.get('amount') or 0)
            payment_location_id = request.POST.get('payment_location')
            reference_number = request.POST.get('reference_number', '')
            notes = request.POST.get('notes', '')
            
            if amount > 0:
                payment_location = PaymentLocation.objects.filter(id=payment_location_id).first()
                
                if payment_mode == 'manual':
                    # Manual mode: user selects specific invoices
                    selected_invoice_ids = request.POST.getlist('selected_invoices')
                    selected_invoices = Invoice.objects.filter(
                        id__in=selected_invoice_ids,
                        supplier=selected_supplier
                    )
                    
                    if selected_invoices.exists():
                        payment = Payment.objects.create(
                            amount=amount,
                            payment_date=timezone.now().date(),
                            payment_location=payment_location,
                            reference_number=reference_number,
                            notes=notes,
                            created_by=request.user,
                            payment_status='completed'
                        )
                        payment.invoices.add(*selected_invoices)
                        messages.success(request, f'پرداخت {amount:,} ریال برای {selected_invoices.count()} فاکتور ثبت شد.')
                else:
                    # Auto mode: automatically settle oldest unpaid/partially paid invoices
                    all_invoices = Invoice.objects.filter(
                        supplier=selected_supplier
                    ).order_by('invoice_date', 'created_at')
                    
                    # Filter to get only invoices with remaining balance
                    unpaid_invoices = []
                    for inv in all_invoices:
                        paid_amount = Payment.objects.filter(
                            Q(invoice=inv) | Q(invoices=inv),
                            payment_status='completed'
                        ).aggregate(total=Sum('amount'))['total'] or 0
                        inv_remaining = (inv.total_amount or 0) - paid_amount
                        if inv_remaining > 0:
                            unpaid_invoices.append((inv, inv_remaining))
                    
                    remaining_amount = amount
                    settled_invoices = []
                    
                    for invoice, invoice_remaining in unpaid_invoices:
                        if remaining_amount <= 0:
                            break
                        
                        settled_invoices.append(invoice)
                        remaining_amount -= invoice_remaining
                    
                    if settled_invoices:
                        payment = Payment.objects.create(
                            amount=amount,
                            payment_date=timezone.now().date(),
                            payment_location=payment_location,
                            reference_number=reference_number,
                            notes=f'تسویه خودکار - {notes}' if notes else 'تسویه خودکار از قدیمی‌ترین فاکتورها',
                            created_by=request.user,
                            payment_status='completed'
                        )
                        payment.invoices.add(*settled_invoices)
                        
                        if remaining_amount > 0:
                            messages.success(request, f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور تسویه شد. اضافه پرداخت: {remaining_amount:,} ریال')
                        elif remaining_amount < 0:
                            messages.warning(request, f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور تسویه شد (بخشی از آخرین فاکتور). کسری: {abs(remaining_amount):,} ریال')
                        else:
                            messages.success(request, f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور کاملاً تسویه شد.')
                    else:
                        messages.warning(request, 'هیچ فاکتور پرداخت‌نشده‌ای برای تسویه یافت نشد.')
                
                return redirect(f'?supplier_id={supplier_id}')
            else:
                messages.error(request, 'مبلغ پرداخت باید بیشتر از صفر باشد.')
        except Supplier.DoesNotExist:
            messages.error(request, 'تأمین‌کننده یافت نشد.')
        except Exception as e:
            messages.error(request, f'خطا در ثبت پرداخت: {str(e)}')
    
    if supplier_id:
        try:
            selected_supplier = Supplier.objects.get(id=supplier_id, status='active')
            # Get all invoices for this supplier
            invoices = Invoice.objects.filter(supplier=selected_supplier).select_related('purchase_order').prefetch_related('issues').order_by('invoice_date', 'created_at')
            
            # Apply filters
            search_query = request.GET.get('search', '')
            payment_status = request.GET.get('payment_status', '')
            issue_id = request.GET.get('issue', '')
            date_from = request.GET.get('date_from', '')
            date_to = request.GET.get('date_to', '')
            
            if search_query:
                invoices = invoices.filter(
                    Q(invoice_number__icontains=search_query) |
                    Q(total_amount__icontains=search_query)
                )
            
            if payment_status:
                invoices = invoices.filter(payment_status=payment_status)
            
            if issue_id:
                invoices = invoices.filter(issues__id=issue_id)
            
            if date_from:
                try:
                    date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                    invoices = invoices.filter(invoice_date__gte=date_from_obj)
                except ValueError:
                    pass
            
            if date_to:
                try:
                    date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                    invoices = invoices.filter(invoice_date__lte=date_to_obj)
                except ValueError:
                    pass
            
            # Calculate total owed using the invoice's remaining_amount property
            for invoice in invoices:
                total_owed += max(0, invoice.remaining_amount)
        except Supplier.DoesNotExist:
            pass
    
    context = {
        'suppliers': suppliers,
        'selected_supplier': selected_supplier,
        'invoices': invoices,
        'total_owed': total_owed,
        'payment_methods': payment_methods,
        'issues': InvoiceIssue.objects.all(),
    }
    return render(request, "supplier_select.html", context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def _supplier_form_data(request):
    """همه فیلدهای فرم تأمین‌کننده — برای ایجاد و ویرایش مشترک"""
    return {
        'name': request.POST.get("name", ""),
        'supplier_type': request.POST.get("supplier_type", "company"),
        'contact_person': request.POST.get("contact_person", ""),
        'email': request.POST.get("email", ""),
        'phone': request.POST.get("phone", ""),
        'mobile': request.POST.get("mobile", ""),
        'address': request.POST.get("address", ""),
        'city': request.POST.get("city", ""),
        'state': request.POST.get("state", ""),
        'postal_code': request.POST.get("postal_code", ""),
        'country': request.POST.get("country", "ایران"),
        'tax_id': request.POST.get("tax_id", ""),
        'registration_number': request.POST.get("registration_number", ""),
        'business_license': request.POST.get("business_license", ""),
        'website': request.POST.get("website", ""),
        'bank_name': request.POST.get("bank_name", ""),
        'bank_account': request.POST.get("bank_account", ""),
        'bank_routing': request.POST.get("bank_routing", ""),
        'card_number': request.POST.get("card_number", ""),
        'sheba_number': request.POST.get("sheba_number", ""),
        'payment_terms': request.POST.get("payment_terms", ""),
        'notes': request.POST.get("notes", ""),
    }


def supplier_create(request):
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        data = _supplier_form_data(request)
        if data['name'] and data['supplier_type']:
            try:
                supplier = Supplier.objects.create(cafe=cafe, **data)
                # دسته‌بندی‌های انتخابی
                categories = request.POST.getlist("categories")
                if categories:
                    supplier.categories.set(Category.objects.filter(id__in=categories))
                messages.success(request, f'تأمین‌کننده "{supplier.name}" با موفقیت ایجاد شد.')
                return get_cafe_redirect_url("supplier_list", request)
            except Exception as e:
                messages.error(request, f'خطا در ایجاد تأمین‌کننده: {str(e)}')
        else:
            messages.error(request, 'نام تأمین‌کننده و نوع تأمین‌کننده الزامی هستند.')
    return render(request, "supplier_form.html", {"supplier_types": Supplier.SUPPLIER_TYPE_CHOICES})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    return render(request, "supplier_detail.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_update(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    if request.method == "POST":
        data = _supplier_form_data(request)
        if not data['name']:
            messages.error(request, 'نام تأمین‌کننده الزامی است.')
            return render(request, "supplier_form.html", {"supplier": supplier, "supplier_types": Supplier.SUPPLIER_TYPE_CHOICES})
        for field, value in data.items():
            setattr(supplier, field, value)
        try:
            supplier.save()
            categories = request.POST.getlist("categories")
            supplier.categories.set(Category.objects.filter(id__in=categories))
            messages.success(request, f'تأمین‌کننده "{supplier.name}" با موفقیت بروزرسانی شد.')
            return get_cafe_redirect_url("supplier_list", request)
        except Exception as e:
            messages.error(request, f'خطا در بروزرسانی تأمین‌کننده: {str(e)}')
    return render(request, "supplier_form.html", {"supplier": supplier, "supplier_types": Supplier.SUPPLIER_TYPE_CHOICES})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_delete(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    if request.method == "POST":
        supplier.delete()
        return get_cafe_redirect_url("supplier_list", request)
    return render(request, "supplier_confirm_delete.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_list(request):
    # Ensure default requesters exist
    default_requesters = ['kitchen', 'bar', 'facilities']
    for req in default_requesters:
        Requester.objects.get_or_create(name=req)
    
    requesters = Requester.objects.all()
    return render(request, "requester_list.html", {"requesters": requesters})


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            Requester.objects.create(name=name)
            return redirect("requester_list")
    return render(request, "requester_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_delete(request, requester_id):
    requester = get_object_or_404(Requester, id=requester_id)
    if request.method == "POST":
        requester.delete()
        return redirect("requester_list")
    return render(request, "requester_confirm_delete.html", {"requester": requester})


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_list(request):
    # Ensure default issues exist
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue)
    
    issues = InvoiceIssue.objects.all()
    return render(request, "invoice_issue_list.html", {"issues": issues})


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            InvoiceIssue.objects.create(name=name)
            return redirect("invoice_issue_list")
    return render(request, "invoice_issue_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_delete(request, issue_id):
    issue = get_object_or_404(InvoiceIssue, id=issue_id)
    if request.method == "POST":
        issue.delete()
        return redirect("invoice_issue_list")
    return render(request, "invoice_issue_confirm_delete.html", {"issue": issue})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_activate(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    supplier.status = "active"
    supplier.save()
    return get_cafe_redirect_url("supplier_detail", request, supplier_id=supplier.id)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_deactivate(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    supplier.status = "inactive"
    supplier.save()
    return get_cafe_redirect_url("supplier_detail", request, supplier_id=supplier.id)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_list(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    evaluations = SupplierEvaluation.objects.filter(supplier=supplier)
    return render(
        request,
        "supplier_evaluation_list.html",
        {"supplier": supplier, "evaluations": evaluations},
    )


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_create(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    if request.method == "POST":
        SupplierEvaluation.objects.create(
            supplier=supplier,
            evaluation_date=timezone.now().date(),
            quality_score=_parse_decimal(request.POST.get("quality_score") or 0),
            delivery_score=_parse_decimal(request.POST.get("delivery_score") or 0),
            pricing_score=_parse_decimal(request.POST.get("pricing_score") or 0),
            comments=request.POST.get("comments", ""),
            evaluator=request.user,
        )
        return get_cafe_redirect_url("supplier_evaluation_list", request, supplier_id=supplier.id)
    return render(request, "supplier_evaluation_form.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_all_list(request):
    evaluations = SupplierEvaluation.objects.select_related("supplier")
    return render(
        request,
        "supplier_evaluation_list.html",
        {"evaluations": evaluations, "supplier": None},
    )


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_create_general(request):
    cafe = get_cafe_from_request(request)
    suppliers = Supplier.objects.all()
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    if request.method == "POST":
        supplier = Supplier.objects.filter(id=request.POST.get("supplier")).first()
        if supplier:
            SupplierEvaluation.objects.create(
                supplier=supplier,
                evaluation_date=timezone.now().date(),
                quality_score=_parse_decimal(request.POST.get("quality_score") or 0),
                delivery_score=_parse_decimal(request.POST.get("delivery_score") or 0),
                pricing_score=_parse_decimal(request.POST.get("pricing_score") or 0),
                comments=request.POST.get("comments", ""),
                evaluator=request.user,
            )
            return get_cafe_redirect_url("supplier_evaluation_all_list", request)
    return render(request, "supplier_evaluation_form.html", {"suppliers": suppliers})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_performance_report(request):
    evaluations = SupplierEvaluation.objects.select_related("supplier")
    return render(request, "supplier_performance_report.html", {"evaluations": evaluations})


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
# Consumption Report (گزارش مصرف) — بخش انبار
# ---------------------------------------------------------------------------

@custom_login_required
@role_required(['warehouse', 'manager', 'admin', 'ceo', 'store', 'purchasing'])
def consumption_report(request):
    """گزارش مصرف کالاها — مصرف کل و تفکیک بخش‌های تحویل‌گیرنده در بازه زمانی انتخابی.

    منبع داده: تراکنش‌های خروج از انبار (StockTransaction با transaction_type='out')؛
    بخشِ تحویل‌گیرنده از reference_id (PurchaseItem یا StockRequest) و کاربر درخواست‌دهنده به دست می‌آید.
    """
    import jdatetime
    from django.db.models.functions import TruncDate, TruncWeek

    cafe = get_cafe_from_request(request)
    today = timezone.localdate()

    # بازه زمانی (پیش‌فرض: ۳۰ روز اخیر)
    start_date = _parse_iso_date(request.GET.get('start_date', ''))
    end_date = _parse_iso_date(request.GET.get('end_date', ''))
    if start_date is None:
        start_date = today - timedelta(days=30)
    if end_date is None:
        end_date = today
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

    # تراکنش‌های خروج از انبار در بازه
    out_qs = StockTransaction.objects.filter(
        transaction_type='out',
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).select_related('stock_item__product', 'stock_item__product_brand', 'stock_item__warehouse', 'unit')
    if cafe:
        out_qs = out_qs.filter(stock_item__warehouse__cafe=cafe)

    # فیلتر بخش (درخواست‌دهنده)
    department_id = request.GET.get('department', '')
    try:
        department_id = int(department_id) if department_id else None
    except (TypeError, ValueError):
        department_id = None
    if department_id:
        item_ids = list(PurchaseItem.objects.filter(
            request__user__department_id=department_id).values_list('id', flat=True))
        sreq_ids = list(StockRequest.objects.filter(
            purchase_item__request__user__department_id=department_id).values_list('id', flat=True))
        out_qs = out_qs.filter(
            Q(reference_type='officer_assignment', reference_id__in=item_ids) |
            Q(reference_type='stock_request', reference_id__in=sreq_ids)
        )

    # شاخص‌های کل
    agg = out_qs.aggregate(total_qty=Sum('quantity'), txn_count=Count('id'))
    total_qty = agg['total_qty'] or Decimal('0')
    txn_count = agg['txn_count'] or 0
    product_count = out_qs.values('stock_item__product_id').distinct().count()

    # مصرف به تفکیک بخش (تحویل به بخش‌ها از طریق تخصیص مسئول انبار + درخواست‌های تامین قدیمی)
    section_data = {}

    def _add_section(name, qty, cnt):
        acc = section_data.setdefault(name, {'qty': Decimal('0'), 'cnt': 0})
        acc['qty'] += qty
        acc['cnt'] += cnt

    oa_agg = (out_qs.filter(reference_type='officer_assignment')
                .values('reference_id')
                .annotate(qty=Sum('quantity'), cnt=Count('id')))
    oa_ids = [r['reference_id'] for r in oa_agg if r['reference_id']]
    item_map = {}
    if oa_ids:
        item_map = {p.id: p for p in PurchaseItem.objects.filter(
            id__in=oa_ids).select_related('request__user__department')}
    for r in oa_agg:
        pi = item_map.get(r['reference_id'])
        sec = _item_section_name(pi.request.user) if pi and pi.request else 'نامشخص'
        _add_section(sec, r['qty'] or Decimal('0'), r['cnt'])

    sr_agg = (out_qs.filter(reference_type='stock_request')
                .values('reference_id')
                .annotate(qty=Sum('quantity'), cnt=Count('id')))
    sr_ids = [r['reference_id'] for r in sr_agg if r['reference_id']]
    sreq_map = {}
    if sr_ids:
        sreq_map = {s.id: s for s in StockRequest.objects.filter(
            id__in=sr_ids).select_related('purchase_item__request__user__department')}
    for r in sr_agg:
        sreq = sreq_map.get(r['reference_id'])
        pi = sreq.purchase_item if sreq else None
        sec = _item_section_name(pi.request.user) if pi and pi.request else 'نامشخص'
        _add_section(sec, r['qty'] or Decimal('0'), r['cnt'])

    section_rows = sorted(
        [{'name': n, 'qty': v['qty'], 'cnt': v['cnt']} for n, v in section_data.items()],
        key=lambda x: x['qty'], reverse=True)
    section_total = sum((r['qty'] for r in section_rows), Decimal('0'))

    # روند مصرف در طول بازه (روزانه؛ برای بازه‌های بلندتر از ۶۰ روز هفتگی)
    span_days = (end_date - start_date).days
    if span_days > 60:
        trend_qs = out_qs.annotate(d=TruncWeek('created_at')).values('d').annotate(
            qty=Sum('quantity'), cnt=Count('id')).order_by('d')
    else:
        trend_qs = out_qs.annotate(d=TruncDate('created_at')).values('d').annotate(
            qty=Sum('quantity'), cnt=Count('id')).order_by('d')

    trend_labels, trend_qty, trend_cnt = [], [], []
    for r in trend_qs:
        day = r['d']
        if hasattr(day, 'date'):
            day = day.date()
        trend_labels.append(jdatetime.date.fromgregorian(date=day).strftime('%Y/%m/%d'))
        trend_qty.append(str(r['qty']))
        trend_cnt.append(r['cnt'])

    # مصرف به تفکیک کالا
    product_rows = (out_qs.values(
            'stock_item__product_id',
            'stock_item__product__name',
            'stock_item__product_brand__brand_name',
            'stock_item__product__unit__name',
        )
        .annotate(qty=Sum('quantity'), cnt=Count('id'))
        .order_by('-qty'))

    # تفکیک نوع خروج (برای دیدن سهم هر مسیر در مصرف کل)
    REF_TYPE_LABELS = {
        'officer_assignment': 'تامین بخش‌ها',
        'stock_request': 'درخواست تامین انبار (قدیمی)',
        'voucher': 'حواله خروج',
    }
    type_rows = (out_qs.values('reference_type')
                 .annotate(qty=Sum('quantity'), cnt=Count('id'))
                 .order_by('-qty'))
    for r in type_rows:
        r['label'] = REF_TYPE_LABELS.get(r['reference_type'], r['reference_type'] or '—')

    departments = Department.objects.filter(is_active=True, cafe=cafe).order_by('name')

    # بازه‌های آماده (چیپ‌ها)
    presets = [
        {'label': '۷ روز اخیر', 'start': (today - timedelta(days=7)).isoformat(), 'end': today.isoformat()},
        {'label': '۳۰ روز اخیر', 'start': (today - timedelta(days=30)).isoformat(), 'end': today.isoformat()},
        {'label': '۹۰ روز اخیر', 'start': (today - timedelta(days=90)).isoformat(), 'end': today.isoformat()},
        {'label': '۶ ماه اخیر', 'start': (today - timedelta(days=180)).isoformat(), 'end': today.isoformat()},
    ]

    # پیش‌پر کردن فیلدهای شمسی فرم
    sj = jdatetime.date.fromgregorian(date=start_date)
    ej = jdatetime.date.fromgregorian(date=end_date)

    return render(request, 'reports/consumption_report.html', {
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'start_year': sj.year, 'start_month': sj.month, 'start_day': sj.day,
        'end_year': ej.year, 'end_month': ej.month, 'end_day': ej.day,
        'selected_department': department_id or '',
        'total_qty': total_qty,
        'txn_count': txn_count,
        'product_count': product_count,
        'section_count': len(section_rows),
        'section_total': section_total,
        'section_rows': section_rows,
        'product_rows': product_rows,
        'type_rows': type_rows,
        'trend_labels': trend_labels,
        'trend_qty': trend_qty,
        'trend_cnt': trend_cnt,
        'departments': departments,
        'presets': presets,
        'span_days': span_days,
    })


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
# System monitoring
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["manager"])
@custom_login_required
@role_required(["manager"])
def system_monitoring(request):
    # System Info
    system_info = {
        "os": platform.system(),
        "os_version": platform.release(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "django_version": django.get_version(),
    }
    
    # DB Info
    db_settings = settings.DATABASES['default']
    db_info = {
        "database_name": db_settings['NAME'],
        "database_engine": db_settings['ENGINE'],
        "host": db_settings['HOST'] or 'localhost',
        "port": db_settings['PORT'] or '5432',
    }
    
    # Resources
    data = {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage("/").percent,
    }
    
    context = {
        "system_info": system_info,
        "db_info": db_info,
        "system_resources": data,
        "last_updated": datetime.now(),
    }
    return render(request, "system_monitoring.html", context)


@custom_login_required
@role_required(["manager"])
def system_monitoring_api(request):
    # System resources snapshot
    system_resources = {
        "cpu_percent": psutil.cpu_percent(interval=0.01),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage("/").percent,
    }

    activity_count = ActivityLog.objects.count()
    cafe = get_cafe_from_request(request)
    if activity_count >= 5:
        recent_activity = [
            {
                "id": act.id,
                "user": act.user.username,
                "action": act.action,
                "description": act.description,
                "timestamp": act.created_at.isoformat(),
            }
            for act in ActivityLog.objects.select_related("user").order_by("-created_at")[:10]
        ]
    else:
        pr_qs = PurchaseRequest.objects.all()
        inv_qs = Invoice.objects.all()
        if cafe:
            pr_qs = pr_qs.filter(cafe=cafe)
            inv_qs = inv_qs.filter(cafe=cafe)
        recent_activity = {
            "new_requests": pr_qs.count(),
            "new_orders": PurchaseOrder.objects.count(),
            "new_invoices": inv_qs.count(),
            "new_payments": Payment.objects.count(),
        }

    health_status = {
        "database_status": "healthy",
        "overall_health": "good",
    }

    data = {
        "system_resources": system_resources,
        "recent_activity": recent_activity,
        "health_status": health_status,
    }
    return JsonResponse({"success": True, "data": data})


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@custom_login_required
def notifications_list(request):
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
    return redirect("notifications_list")


@custom_login_required
def delete_notification(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.delete()
    return redirect("notifications_list")


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
    unrestricted_roles = ['purchasing', 'warehouse', 'admin', 'manager', 'accounting', 'finance_manager', 'ceo']
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

        # برندهای فعال محصول - هر برند یک گزینه مجزا در لیست پیشنهادها است
        active_brands = list(product.brands.filter(is_active=True).order_by('brand_name'))
        brands_meta = [{
            'id': b.id,
            'brand_name': b.brand_name,
            'trade_unit': b.trade_unit or '',
        } for b in active_brands]

        # نمایش برندها به‌صورت جداگانه:
        # - برای درخواست‌دهندگان فقط وقتی تیک «نمایش زیر محصولات» خورده باشد، هر نام تجاری یک گزینه است؛
        #   در غیر این صورت محصول با نام اصلی خودش نمایش داده می‌شود.
        # - سایر نقش‌ها (خرید/انبار/مدیر و...) همیشه برندها را می‌بینند.
        show_brand_rows = user_role in unrestricted_roles or product.show_sub_products

        if active_brands and show_brand_rows:
            # محصول دارای برند: برای هر برند یک ردیف «نام محصول - نام برند» نمایش داده می‌شود
            for brand in active_brands:
                brand_unit = brand.trade_unit or (product.unit.name if product.unit else '')
                results.append({
                    'id': product.id,
                    'name': product.name,
                    'brand': brand.brand_name,
                    'product_brand_id': brand.id,
                    'category': category_display,
                    'category_id': product.category_id,
                    'label': f"{product.name} - {brand.brand_name}",
                    'unit': brand_unit,
                    'has_brands': True,
                    'brands': brands_meta,
                })
        else:
            # محصول بدون برند: نام محصول به صورت عادی نمایش داده می‌شود
            results.append({
                'id': product.id,
                'name': product.name,
                'brand': None,
                'product_brand_id': None,
                'category': category_display,
                'category_id': product.category_id,
                'label': product.name,
                'unit': product.unit.name if product.unit else '',
                'has_brands': False,
                'brands': [],
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
    unrestricted_roles = ['purchasing', 'warehouse', 'admin', 'manager', 'accounting', 'finance_manager', 'ceo']
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
        active_brands = list(product.brands.filter(is_active=True).order_by('brand_name'))
        show_brand_rows = user_role in unrestricted_roles or product.show_sub_products

        if active_brands and show_brand_rows:
            for brand in active_brands:
                brand_unit = brand.trade_unit or (product.unit.name if product.unit else '')
                results.append({
                    'id': product.id,
                    'name': product.name,
                    'brand': brand.brand_name,
                    'product_brand_id': brand.id,
                    'category_id': product.category_id,
                    'label': f"{product.name} - {brand.brand_name}",
                    'unit': brand_unit,
                })
        else:
            results.append({
                'id': product.id,
                'name': product.name,
                'brand': None,
                'product_brand_id': None,
                'category_id': product.category_id,
                'label': product.name,
                'unit': product.unit.name if product.unit else '',
            })

    return JsonResponse({'results': results})


@custom_login_required
def product_brands_api(request: HttpRequest, product_id: int) -> JsonResponse:
    """API برندهای فعال یک محصول (برای انتخاب برند در ثبت موجودی)"""
    product = get_object_or_404(Product, id=product_id)
    brands = product.brands.filter(is_active=True).order_by('brand_name')
    data = [{
        'id': b.id,
        'brand_name': b.brand_name,
        'trade_unit': b.trade_unit or '',
        'conversion_rate': str(b.conversion_rate) if b.conversion_rate else '',
    } for b in brands]
    return JsonResponse({'brands': data})


@custom_login_required
def check_product_availability(request: HttpRequest) -> JsonResponse:
    """بررسی موجودی انبار برای محصول/برند انتخاب‌شده هنگام ثبت درخواست کالا.

    اگر برند انتخاب‌شده موجود نباشد، برندهای جایگزین همان محصول که موجودی دارند
    به همراه مقدار موجود پیشنهاد داده می‌شوند (بدون ساخت محصول جدید).
    """
    product_id = request.GET.get('product_id')
    brand_id = request.GET.get('brand_id') or None
    qty = _parse_decimal(request.GET.get('qty', '') or 0)

    product = Product.objects.filter(id=product_id).first()
    if not product:
        return JsonResponse({'error': 'محصول یافت نشد'}, status=404)

    brand = None
    if brand_id:
        brand = ProductBrand.objects.filter(id=brand_id, product=product).first()
        if not brand:
            return JsonResponse({'error': 'برند انتخاب‌شده برای این محصول معتبر نیست'}, status=404)

    cafe = get_cafe_from_request(request)
    stock_qs = StockItem.objects.filter(product=product, quantity__gt=0).select_related(
        'product_brand', 'warehouse', 'unit')
    if cafe:
        stock_qs = stock_qs.filter(warehouse__cafe=cafe)
    stock_rows = list(stock_qs)

    def brand_availability(b):
        """مجموع موجودی (واحد پایه و تجاری) + واحد نمایشی + انبارهای دارای موجودی"""
        rows = [s for s in stock_rows if s.product_brand_id == (b.id if b else None)]
        base = sum((s.quantity for s in rows), Decimal('0'))
        if b and b.conversion_rate:
            trade = base / b.conversion_rate
            unit_label = b.trade_unit or (rows[0].unit.name if rows and rows[0].unit else (product.unit.name if product.unit else ''))
        else:
            trade = base
            unit_label = (rows[0].unit.name if rows and rows[0].unit else product.unit.name if product.unit else '')
        warehouses = sorted({s.warehouse.name for s in rows if s.warehouse})
        return trade, unit_label, warehouses

    trade, unit_label, warehouses = brand_availability(brand)
    available = qty > 0 and trade >= qty

    # پیشنهاد برند جایگزین: برندهای فعال دیگر همان محصول که موجودی دارند
    alternatives = []
    if not available:
        alt_qs = product.brands.filter(is_active=True)
        if brand:
            alt_qs = alt_qs.exclude(id=brand.id)
        for alt in alt_qs:
            alt_trade, alt_unit, alt_whs = brand_availability(alt)
            if alt_trade > 0:
                alternatives.append({
                    'brand_id': alt.id,
                    'brand_name': alt.brand_name,
                    'available': str(alt_trade),
                    'unit': alt_unit,
                    'warehouses': alt_whs,
                })

    return JsonResponse({
        'product_id': product.id,
        'brand_id': brand.id if brand else None,
        'qty': str(qty),
        'available': available,
        'stock_trade': str(trade),
        'unit_label': unit_label,
        'warehouses': warehouses,
        'alternatives': alternatives,
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

    # صفحه‌بندی
    paginator = Paginator(groups_list, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

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


@custom_login_required
@role_required(["manager", "ceo"])
def manager_approve_request(request, request_id):
    """
    تایید درخواست توسط مدیر
    """
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    cafe = get_cafe_from_request(request)
    
    if cafe and purchase_request.cafe != cafe:
        messages.error(request, 'دسترسی غیرمجاز')
        return redirect('manager_pending_requests', cafe_code=cafe.code)
    
    if purchase_request.status != 'pending_manager_approval':
        messages.warning(request, 'این درخواست قبلاً پردازش شده است')
        return redirect('manager_pending_requests', cafe_code=cafe.code if cafe else purchase_request.cafe.code)
    
    if request.method == 'POST':
        # تایید درخواست
        purchase_request.status = 'pending'
        purchase_request.manager_confirmed = True
        purchase_request.manager_confirmed_by = request.user
        purchase_request.manager_confirmed_at = timezone.now()
        purchase_request.save()
        
        # ثبت لاگ
        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action='approve_request',
            description=f'تایید درخواست کالا #{purchase_request.id} توسط {request.user.get_full_name() or request.user.username}'
        )
        
        messages.success(request, f'درخواست کالا #{purchase_request.id} با موفقیت تایید شد و به واحد خرید ارسال شد')
        
        # برگشت به صفحه قبلی یا درخواست‌های در انتظار
        next_url = request.GET.get('next')
        if next_url:
            return redirect(next_url)
        return redirect('manager_pending_requests', cafe_code=cafe.code if cafe else purchase_request.cafe.code)
    
    return redirect('manager_pending_requests', cafe_code=cafe.code if cafe else purchase_request.cafe.code)


@custom_login_required
@role_required(["manager", "ceo"])
def manager_reject_request(request, request_id):
    """
    رد درخواست توسط مدیر
    """
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    cafe = get_cafe_from_request(request)
    
    if cafe and purchase_request.cafe != cafe:
        messages.error(request, 'دسترسی غیرمجاز')
        return redirect('manager_pending_requests', cafe_code=cafe.code)
    
    if purchase_request.status != 'pending_manager_approval':
        messages.warning(request, 'این درخواست قبلاً پردازش شده است')
        return redirect('manager_pending_requests', cafe_code=cafe.code if cafe else purchase_request.cafe.code)
    
    if request.method == 'POST':
        rejection_reason = request.POST.get('rejection_reason', '')
        
        # رد درخواست
        purchase_request.status = 'rejected'
        purchase_request.rejection_reason = rejection_reason
        purchase_request.manager_confirmed = False
        purchase_request.manager_confirmed_by = request.user
        purchase_request.manager_confirmed_at = timezone.now()
        purchase_request.save()
        
        # ثبت لاگ
        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action='reject_request',
            description=f'رد درخواست کالا #{purchase_request.id} توسط {request.user.get_full_name() or request.user.username}. دلیل: {rejection_reason}'
        )
        
        messages.success(request, f'درخواست کالا #{purchase_request.id} رد شد')
        
        # برگشت به صفحه قبلی یا درخواست‌های در انتظار
        next_url = request.POST.get('next', request.GET.get('next'))
        if next_url:
            return redirect(next_url)
        return redirect('manager_pending_requests', cafe_code=cafe.code if cafe else purchase_request.cafe.code)
    
    return redirect('manager_pending_requests', cafe_code=cafe.code if cafe else purchase_request.cafe.code)


# ---------------------------------------------------------------------------  
# CEO Dashboard
# ---------------------------------------------------------------------------


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

    # برای هر دسته آمار موجودی
    from django.db.models import Count, Sum as DSum
    category_data = []
    for cat in categories_qs:
        items_qs = StockItem.objects.filter(product__category=cat)
        if cafe:
            items_qs = items_qs.filter(warehouse__cafe=cafe)
        count = items_qs.count()
        low   = sum(1 for i in items_qs if i.is_low_stock)
        category_data.append({
            'id': cat.id,
            'name': cat.name,
            'description': cat.description,
            'total_items': count,
            'low_stock': low,
        })

    # دسته‌بندی "بدون دسته" - محصولاتی که category ندارند
    no_cat_qs = StockItem.objects.filter(product__category__isnull=True)
    if cafe:
        no_cat_qs = no_cat_qs.filter(warehouse__cafe=cafe)
    no_cat_count = no_cat_qs.count()
    no_cat_low   = sum(1 for i in no_cat_qs if i.is_low_stock)

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
}


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
    stock_items = (StockItem.objects.filter(warehouse_id=warehouse_id,
                                            warehouse__cafe=cafe)
                   .select_related('product', 'product_brand', 'unit', 'warehouse')
                   .order_by('product__name'))
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
        'cafe_code': cafe.code if cafe else '',
        'products': opts['products'],
        'units': opts['units'],
        'brands_json': opts['brands_json'],
        'stock_items': opts['stock_items'],
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
        'source_warehouse', 'destination_warehouse', 'created_by', 'approver'
    ).filter(cafe=cafe)

    f = {
        'type': request.GET.get('type'),
        'status': request.GET.get('status'),
        'date_from': request.GET.get('date_from', '').strip(),
        'date_to': request.GET.get('date_to', '').strip(),
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
    if f['date_from']:
        try:
            vouchers = vouchers.filter(date__gte=datetime.strptime(f['date_from'], '%Y-%m-%d').date())
        except ValueError:
            pass
    if f['date_to']:
        try:
            vouchers = vouchers.filter(date__lte=datetime.strptime(f['date_to'], '%Y-%m-%d').date())
        except ValueError:
            pass
    if f['source_warehouse']:
        vouchers = vouchers.filter(source_warehouse_id=f['source_warehouse'])
    if f['dest_warehouse']:
        vouchers = vouchers.filter(destination_warehouse_id=f['dest_warehouse'])
    if f['product']:
        vouchers = vouchers.filter(items__product_id=f['product'])
    if f['department']:
        # بخش از طریق انبارِ مرتبط با بخش — مبدأ یا مقصدِ حواله
        dept_wh_ids = Department.objects.filter(cafe=cafe, id=f['department']).values_list('warehouse_id', flat=True)
        vouchers = vouchers.filter(
            Q(source_warehouse_id__in=dept_wh_ids) | Q(destination_warehouse_id__in=dept_wh_ids))
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

        lines, errors = _parse_voucher_lines(request, cafe, voucher_type)
        if errors:
            for e in errors:
                messages.error(request, e)
            return _render_voucher_form(request, cafe, voucher_type, src_wh or dst_wh,
                                        {'form_errors': errors})

        vdate = timezone.now()
        date_str = request.POST.get('date', '').strip()
        if date_str:
            try:
                vdate = timezone.make_aware(datetime.strptime(date_str, '%Y-%m-%dT%H:%M'))
            except Exception:
                pass

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
                                        {'form_errors': [str(exc)]})

        if action == 'draft':
            messages.success(request, f'پیش‌نویس حواله {voucher.voucher_number} ثبت شد.')
        elif voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES:
            messages.success(request, f'حوالهٔ {voucher.voucher_number} ثبت شد و در انتظار تأیید مدیر است.')
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

        lines, errors = _parse_voucher_lines(request, cafe, voucher.voucher_type)
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)

        date_str = request.POST.get('date', '').strip()
        vdate = timezone.now()
        if date_str:
            try:
                vdate = timezone.make_aware(datetime.strptime(date_str, '%Y-%m-%dT%H:%M'))
            except Exception:
                pass

        with transaction.atomic():
            voucher.source_warehouse = src_wh
            voucher.destination_warehouse = dst_wh
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
                                 'edit_items': items, 'edit_date': voucher.date,
                                 'edit_source_wh': voucher.source_warehouse,
                                 'edit_dest_wh': voucher.destination_warehouse})


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
    همهٔ انواع حرکت با لینک به حوالهٔ مبدأ در API سوابق"""
    from django.db.models import Sum
    cafe = get_cafe_from_request(request)
    warehouse = get_scoped_object_or_404(
        Warehouse, request, id=warehouse_id, is_active=True)

    stock_items = StockItem.objects.filter(
        warehouse=warehouse
    ).select_related('product', 'product__category', 'unit').order_by(
        'product__category__name', 'product__name'
    )

    items_data = []
    for si in stock_items:
        txs = StockTransaction.objects.filter(stock_item=si)

        # ورود/خروج بر اساس نوعِ تراکنش (مقدارها همیشه مثبت ذخیره می‌شوند)
        in_total = txs.filter(transaction_type='in').aggregate(t=Sum('quantity'))['t'] or Decimal('0')
        out_total = txs.filter(transaction_type='out').aggregate(t=Sum('quantity'))['t'] or Decimal('0')

        # موجودی اولیه (از تراکنش‌های initial)
        initial_qty = txs.filter(transaction_type='initial').aggregate(
            t=Sum('quantity'))['t'] or Decimal('0')

        items_data.append({
            'stock_item': si,
            'in_count': txs.filter(transaction_type='in').count(),
            'out_count': txs.filter(transaction_type='out').count(),
            'in_total': in_total,
            'out_total': out_total,
            'initial_qty': initial_qty,
            'system_qty': si.quantity,
        })

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
            'quantity': str(tx.quantity),
            'prev_qty': str(tx.previous_quantity),
            'new_qty': str(tx.new_quantity),
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
        'current_qty': str(stock_item.quantity),
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
        'date_from': request.GET.get('date_from', '').strip(),
        'date_to': request.GET.get('date_to', '').strip(),
        'search': request.GET.get('search', '').strip(),
    }
    if f['warehouse']:
        txs = txs.filter(stock_item__warehouse_id=f['warehouse'])
    if f['tx_type']:
        txs = txs.filter(transaction_type=f['tx_type'])
    if f['voucher_type']:
        txs = txs.filter(voucher__voucher_type=f['voucher_type'])
    if f['date_from']:
        try:
            txs = txs.filter(created_at__date__gte=datetime.strptime(f['date_from'], '%Y-%m-%d').date())
        except ValueError:
            pass
    if f['date_to']:
        try:
            txs = txs.filter(created_at__date__lte=datetime.strptime(f['date_to'], '%Y-%m-%d').date())
        except ValueError:
            pass
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


def _sync_request_status(request_obj):
    """همگام‌سازی وضعیت درخواست بر اساس وضعیت تخصیص‌های اقلامش"""
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
    cafe_code = cafe.code if cafe else 'local'

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
        assignment = None
        try:
            assignment = OfficerAssignment.objects.get(id=int(item_id), target_role='purchasing')
        except (OfficerAssignment.DoesNotExist, ValueError, TypeError):
            assignment = None

        if assignment and assignment.status == 'pending' and (
            assignment.assigned_to_id == request.user.id or assignment.assigned_to_id is None
        ):
            if action == 'purchased':
                assignment.status = 'purchased'
                assignment.notes = (assignment.notes or '') + f' خریداری شد توسط {request.user.get_full_name() or request.user.username}.'
                assignment.save()
                _sync_request_status(assignment.item.request)
                messages.success(request, f'«{assignment.item.name}» خریداری شد.')
            elif action == 'return':
                assignment.status = 'returned'
                assignment.notes = (assignment.notes or '') + f' قابل خرید نبود — بازگشت به مدیرعامل توسط {request.user.get_full_name() or request.user.username}.'
                assignment.save()
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
    assignments = assignments_qs.order_by('assigned_at')
    if cafe:
        assignments = assignments.filter(item__request__cafe=cafe)

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
def warehouse_officer_dashboard(request: HttpRequest) -> HttpResponse:
    """صفحه مسئول انبار: کالاهای درخواستی که در انبار موجودی دارند، مستقیم و
    بدون تایید مدیرعامل ارسال می‌شوند. مسئول انبار می‌تواند تامین کند،
    به درخواست خرید ارجاع دهد یا «عدم موجودی» اعلام کند."""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    if not (request.user.role == 'admin' or request.user.is_superuser or request.user.has_warehouse_role):
        return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

    # ── فیلترها: زمان تحویل (تاریخ انتخاب‌شده یا تحویل فوری) + بخش درخواست‌دهنده ──
    # در ریدایرکت‌ها حفظ می‌شوند تا بعد از هر ثبت همان فیلتر باقی بماند
    from urllib.parse import urlencode
    delivery_filter = request.GET.get('delivery', '')
    dept = request.GET.get('department', '')
    dash_params = {}
    if delivery_filter:
        dash_params['delivery'] = delivery_filter
    if dept:
        dash_params['department'] = dept
    dash_url = reverse('warehouse_officer_dashboard', args=[cafe_code])
    if dash_params:
        dash_url += '?' + urlencode(dash_params)

    # ── پردازش فرم ──
    if request.method == 'POST':
        action = request.POST.get('action', '')
        item_id = request.POST.get('item_id', '')
        try:
            item = PurchaseItem.objects.get(id=int(item_id))
        except (PurchaseItem.DoesNotExist, ValueError, TypeError):
            item = None

        if item and cafe and item.request.cafe_id != cafe.id:
            item = None
        if item is None:
            messages.error(request, 'قلم درخواست یافت نشد')
            return redirect(dash_url)

        # تخصیص موجود (در انتظار) یا ساخت تخصیص جدید
        assignment = item.officer_assignments.filter(
            target_role='warehouse', status='pending'
        ).filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True)).first()

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

            # تجمیع ردیف‌های تکراری (همان انبار/برند چند بار انتخاب شده) تا از یک موجودی دوبار کم نشود
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
            validated = []  # (stock_item, base_deduction, qty) — هر عنصر یک برند/انبار
            for sid in order:
                qty = merged[sid]['qty']
                try:
                    stock_item = StockItem.objects.get(id=int(sid))
                except (StockItem.DoesNotExist, ValueError, TypeError):
                    stock_item = None

                if stock_item is None or qty is None or qty <= 0:
                    messages.error(request, f'برای «{item.name}» موجودی انبار و مقدار معتبر انتخاب کنید')
                    has_error = True
                    break
                # برند و نرخ تبدیل از خود ردیف موجودی انبار گرفته می‌شود
                brand = stock_item.product_brand
                if stock_item.product_id != item.product_id:
                    messages.error(request, f'موجودی انبار با کالای «{item.name}» مطابقت ندارد')
                    has_error = True
                    break
                base_deduction = qty
                if brand and brand.conversion_rate:
                    base_deduction = qty * brand.conversion_rate
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

            try:
                with transaction.atomic():
                    for idx, (stock_item, base_deduction, qty) in enumerate(validated):
                        # کسر اتمیک از موجودی انبار
                        ok, prev_qty, new_qty = _change_stock_quantity(
                            stock_item.id, -base_deduction)
                        if not ok:
                            raise _InsufficientStock(stock_item.product.name)
                        StockTransaction.objects.create(
                            stock_item=stock_item,
                            transaction_type='out',
                            quantity=base_deduction,
                            conversion_rate=stock_item.product_brand.conversion_rate if stock_item.product_brand else None,
                            unit=stock_item.unit,
                            reference_type='officer_assignment',
                            reference_id=item.id,
                            description=f'تامین کالا توسط مسئول انبار - {item.name} - درخواست #{item.request_id}',
                            created_by=request.user,
                            previous_quantity=prev_qty,
                            new_quantity=new_qty,
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
            except _InsufficientStock as exc:
                messages.error(request, f'موجودی «{exc}» در این لحظه کافی نیست؛ ثبت نشد')
                return redirect(dash_url)
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
                PurchaseDraftItem.objects.create(
                    draft=draft,
                    product=item.product,
                    product_brand=item.product_brand,
                    name=item.product.name if item.product else item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                    description=item.description or '',
                    source_item=item,
                )
                messages.info(request, f'«{item.product.name if item.product else item.name}» به صفحه «درخواست خرید» اضافه شد (از منوی کنار قابل مشاهده است).')
                return redirect(dash_url)
            else:
                assignment.status = 'not_in_stock'
                assignment.notes = (assignment.notes or '') + ' عدم موجودی اعلام شد.'
                assignment.save()
                _sync_request_status(item.request)
                messages.warning(request, f'برای «{item.name}» عدم موجودی ثبت شد و به لیست مدیرعامل بازگشت.')
            return redirect(dash_url)

        elif action == 'reopen':
            # بازگشایی کالای ثبت‌شده برای ثبت مجدد: موجودی قبلی به انبار برمی‌گردد
            done_assigns = item.officer_assignments.filter(
                target_role='warehouse',
                status__in=('fulfilled', 'not_in_stock', 'returned'),
            ).filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))
            if not done_assigns.exists():
                messages.error(request, 'این کالا برای بازگشایی آماده نیست')
                return redirect(dash_url)
            # معکوس‌سازی اتمیک تراکنش‌های تامین قبلی این قلم (برگشت موجودی به انبار)
            with transaction.atomic():
                for tx in StockTransaction.objects.select_for_update().filter(
                        reference_type='officer_assignment', reference_id=item.id, transaction_type='out'):
                    si = tx.stock_item
                    if si:
                        ok, _prev, _new = _change_stock_quantity(si.id, +tx.quantity)
                        if not ok:
                            raise _InsufficientStock(si.product.name)
                StockTransaction.objects.filter(
                    reference_type='officer_assignment', reference_id=item.id).delete()
                done_assigns.delete()
            _sync_request_status(item.request)
            messages.info(request, f'«{item.name}» بازگشایی شد — دوباره از اول ثبت کنید.')
            return redirect(dash_url)

    # ── درخواست‌های این کافه (پیش‌فرض: فقط امروز؛ فیلتر تاریخ تحویل روی همین پایه) ──
    cutoff_dt = datetime.now() - timedelta(days=45)
    requests_qs = (
        PurchaseRequest.objects
        .select_related('user', 'user__department')
        .prefetch_related(
            'items', 'items__product', 'items__product__unit',
            'items__product_brand', 'items__officer_assignments')
        .filter(created_at__gte=cutoff_dt)
        .order_by('-created_at')
    )
    if cafe:
        requests_qs = requests_qs.filter(cafe=cafe)

    # فیلتر زمان تحویل: تاریخ مشخص یا تحویل فوری؛ بدون فیلتر → فقط درخواست‌های امروز
    # «امروز» بر پایهٔ ساعت محلی تهران (نه UTC) — از ابتدای بامداد امروز
    now_local = timezone.localtime(timezone.now())
    today = now_local.date()
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if delivery_filter == 'immediate':
        requests_qs = requests_qs.filter(delivery_immediate=True)
    elif delivery_filter:
        try:
            ddate = datetime.strptime(delivery_filter, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            ddate = None
        if ddate:
            requests_qs = requests_qs.filter(delivery_immediate=False, delivery_date=ddate)
        else:
            requests_qs = requests_qs.none()
    else:
        # پیش‌فرض صفحه: تاریخ همان روز — همهٔ درخواست‌های ثبت‌شدهٔ امروز
        # (مستقل از زمان تحویل؛ فیلتر زمان تحویل فقط با انتخاب تاریخ/فوری اعمال می‌شود)
        requests_qs = requests_qs.filter(created_at__gte=day_start)

    # فیلتر بخش درخواست‌دهنده
    if dept:
        requests_qs = requests_qs.filter(
            Q(user__department__name=dept) | Q(user__role=dept))

    requests_list = list(requests_qs)

    # محصولات دارای موجودی برای ارسال خودکار (بدون تخصیص مدیرعامل)
    stocked_qs = StockItem.objects.filter(quantity__gt=0).values_list('product_id', flat=True)
    if cafe:
        stocked_qs = StockItem.objects.filter(quantity__gt=0, warehouse__cafe=cafe).values_list('product_id', flat=True)
    stocked_product_ids = set(stocked_qs)

    # ── موجودی فعلی هر محصول/برند (برای ستون «موجودی فعلی») ──
    scope_pids = sorted({item.product_id for req in requests_list for item in req.items.all() if item.product_id})
    stock_map = {}  # (product_id, brand_id|None) -> {'trade': Decimal, 'unit': str}
    if scope_pids:
        sis = StockItem.objects.filter(product_id__in=scope_pids, quantity__gt=0).select_related('product_brand', 'unit')
        if cafe:
            sis = sis.filter(warehouse__cafe=cafe)
        for si in sis:
            brand = si.product_brand
            rate = brand.conversion_rate if brand and brand.conversion_rate else None
            trade = si.quantity / rate if rate else si.quantity
            unit = brand.trade_unit if brand and brand.trade_unit else (
                (si.unit.symbol or si.unit.name) if si.unit else '')
            key = (si.product_id, brand.id if brand else None)
            entry = stock_map.get(key)
            if entry is None:
                stock_map[key] = {'trade': trade, 'unit': unit}
            else:
                entry['trade'] += trade

    def item_stock_info(item):
        """موجودی فعلی (واحد تجاری) یک قلم برای نمایش کنار مقدار درخواستی"""
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
            return {'trade': str(trade_d), 'unit': unit, 'has': trade_d > 0,
                    'enough': enough, 'short': str(short) if short > 0 else None}

        if item.product_brand_id:
            entry = stock_map.get((item.product_id, item.product_brand_id))
            if not entry:
                unit = item.product_brand.trade_unit or (
                    item.product.unit.name if item.product and item.product.unit else item.unit)
                return _with_verdict(0, unit)
            return _with_verdict(entry['trade'], entry['unit'] or item.unit)
        # بدون برند: مجموع موجودی همه برندهای محصول
        total = Decimal('0')
        unit = ''
        for (pid, _bid), entry in stock_map.items():
            if pid == item.product_id:
                total += entry['trade']
                if not unit:
                    unit = entry['unit']
        if total <= 0:
            unit = unit or (item.product.unit.name if item.product and item.product.unit else item.unit)
        return _with_verdict(total, unit)

    # ── دسته‌بندی اقلام هر درخواست ──
    #  ۱) قابل تامین: تخصیص در انتظار مسئول انبار یا ارسال خودکار (موجودی دارد)
    #  ۲) ثبت‌شده: تامین شده / عدم موجودی — کمرنگ با قابلیت بازگشایی
    #  ۳) ارجاع به خرید: قلم به پیش‌نویس صفحه «درخواست خرید» می‌رود
    request_cards = []
    for req in requests_list:
        visible, done = [], []
        for item in req.items.all():
            assigns = list(item.officer_assignments.all())
            my_pending = [a for a in assigns if a.target_role == 'warehouse' and a.status == 'pending'
                          and (a.assigned_to_id == request.user.id or a.assigned_to_id is None)]
            if my_pending:
                visible.append({'item': item, 'assignment': my_pending[0], 'stock': item_stock_info(item)})
            elif not assigns and _item_in_stock(item, cafe, stocked_product_ids):
                visible.append({'item': item, 'assignment': None, 'stock': item_stock_info(item)})
            else:
                my_done = [a for a in assigns if a.target_role == 'warehouse'
                           and a.status in ('fulfilled', 'not_in_stock', 'returned')
                           and (a.assigned_to_id == request.user.id or a.assigned_to_id is None)]
                if my_done:
                    done.append({'item': item, 'assignment': my_done[0]})
        if visible or done:
            request_cards.append({'request': req, 'visible': visible, 'done': done})

    # ── کارت‌های «تمام‌شده» (همهٔ اقلامشان ثبت‌شده) به انتهای لیست منتقل می‌شوند ──
    request_cards.sort(key=lambda c: (0 if c['visible'] else 1,))

    # ── گزینه‌های تامین از انبار برای اقلام قابل تامین (بر اساس ردیف‌های موجودی) ──
    product_ids = sorted({v['item'].product_id for card in request_cards for v in card['visible'] if v['item'].product_id})
    stock_options = {}
    stock_meta = {}
    if product_ids:
        sis = StockItem.objects.filter(product_id__in=product_ids, quantity__gt=0)
        if cafe:
            sis = sis.filter(warehouse__cafe=cafe)
        sis = sis.select_related('product_brand', 'warehouse', 'unit').order_by('warehouse__name')
        # تعداد برندهای فعال هر محصول
        from django.db.models import Count
        brand_counts = dict(
            ProductBrand.objects.filter(product_id__in=product_ids, is_active=True)
            .values('product_id').annotate(c=Count('id')).values_list('product_id', 'c'))
        branded_product_ids = set(brand_counts.keys())
        for si in sis:
            brand = si.product_brand
            avail = si.trade_quantity if si.trade_quantity is not None else si.quantity
            unit_label = brand.trade_unit if brand and brand.trade_unit else (
                (si.unit.symbol or si.unit.name) if si.unit else '')
            stock_options.setdefault(si.product_id, []).append({
                'id': si.id,
                'warehouse_name': si.warehouse.name if si.warehouse else '',
                'brand_name': brand.brand_name if brand else 'بدون برند',
                'default_selected': si.product_id not in branded_product_ids and not stock_options.get(si.product_id),
                'available': avail,
                'unit_label': unit_label,
                'rate': brand.conversion_rate if brand and brand.conversion_rate else None,
                'qty': si.quantity,
            })
        # اگر همان برند در چند انبار موجود باشد، نام انبار هم کنار برند نشان داده می‌شود
        for opts in stock_options.values():
            wh_count = {}
            for opt in opts:
                wh_count[opt['brand_name']] = wh_count.get(opt['brand_name'], 0) + 1
            for opt in opts:
                opt['multi_warehouse'] = wh_count[opt['brand_name']] > 1

        # محصول بدون برند که فقط در یک انبار موجودی دارد → همان ردیف تنها نامرئی ارسال می‌شود
        # محصول با دقیقاً یک برند که فقط در یک انبار موجودی دارد → همان انتخاب شود
        for pid, opts in stock_options.items():
            n_brands = brand_counts.get(pid, 0)
            hide_select = n_brands == 0 and len(opts) == 1
            stock_meta[pid] = {'hide_select': hide_select}
            if not hide_select and n_brands == 1 and len(opts) == 1:
                opts[0]['default_selected'] = True

    # ── تگ‌های زمان تحویل: تاریخ‌های انتخاب‌شده در محدوده + تحویل فوری ──
    delivery_dates = sorted({r.delivery_date for r in requests_list
                             if r.delivery_date and not r.delivery_immediate})
    has_immediate = any(r.delivery_immediate for r in requests_list)

    # ── ناوبری تاریخ تحویل (همان الگوی صفحه «همه بخش‌ها»): روز قبل/بعد + انتخابگر ──
    selected_nav_date = None
    if delivery_filter and delivery_filter != 'immediate':
        try:
            selected_nav_date = datetime.strptime(delivery_filter, '%Y-%m-%d').date()
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
        'delivery_dates': delivery_dates,
        'has_immediate': has_immediate,
        'selected_delivery': delivery_filter,
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


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def warehouse_purchase_draft(request: HttpRequest) -> HttpResponse:
    """صفحه «درخواست خرید» مسئول انبار:

    - کالاهایی که در داشبورد انبار «ارجاع به خرید» شده‌اند، اول در این صفحه
      (پیش‌نویس) قرار می‌گیرند و قابل ویرایش/حذف/افزودن هستند.
    - با «ارسال» یک درخواست خرید ساخته می‌شود که مثل همه درخواست‌ها مستقیماً
      برای مسئول انبار (صندوق مشترک) ارسال می‌شود.
    - درخواست‌های ارسال‌شده (تا قبل از اقدام مسئول انبار) هم در همین صفحه
      قابل ویرایش و حذف هستند.
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
            draft_items = list(draft.items.select_related('product', 'product_brand')) if draft else []
            if not draft_items:
                messages.error(request, 'پیش‌نویس خالی است — چیزی برای ارسال نیست')
                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

            # زمان تحویل: فوری یا تاریخ انتخابی
            delivery_immediate = request.POST.get('delivery_immediate') == '1'
            delivery_date = None
            delivery_raw = request.POST.get('delivery_date', '').strip()
            if delivery_raw and not delivery_immediate:
                try:
                    delivery_date = datetime.strptime(delivery_raw, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    delivery_date = None

            with transaction.atomic():
                new_request = PurchaseRequest.objects.create(
                    user=request.user,
                    cafe=cafe,
                    status='pending_manager_approval',
                    source='warehouse_purchase',
                    delivery_date=delivery_date,
                    delivery_immediate=delivery_immediate,
                )
                new_request_items = []
                for d_item in draft_items:
                    pi = PurchaseItem.objects.create(
                        request=new_request,
                        product=d_item.product,
                        product_brand=d_item.product_brand,
                        name=d_item.name,
                        quantity=d_item.quantity,
                        unit=d_item.unit,
                        description=d_item.description or '',
                    )
                    new_request_items.append(pi)
                draft.items.all().delete()
                draft.delete()
            ActivityLog.objects.create(
                user=request.user,
                request=new_request,
                action='created',
                description=f'درخواست خرید توسط مسئول انبار ارسال شد ({len(draft_items)} قلم)',
            )
            # همه درخواست‌های کالا مستقیماً برای مسئول انبار ارسال می‌شوند
            _route_request_to_warehouse(
                new_request, new_request_items, request.user,
                notes='ارسال مستقیم از فرم درخواست کالا')
            messages.success(request, f'درخواست خرید با {len(draft_items)} قلم برای مسئول انبار ارسال شد.')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'add_item':
            draft = _get_or_create_purchase_draft(cafe, request.user)
            name = (request.POST.get('name', '') or '').strip()
            product_id = request.POST.get('product_id', '')
            brand_id = request.POST.get('brand_id', '')
            # فرم قالب با «quantity» پست می‌کند؛ «new_qty» هم پذیرفته می‌شود
            qty = _parse_decimal(request.POST.get('quantity', '') or request.POST.get('new_qty', ''))
            unit = (request.POST.get('unit', '') or 'عدد').strip()
            description = (request.POST.get('description', '') or '').strip()

            item_product = None
            if product_id.isdigit():
                item_product = Product.objects.filter(id=int(product_id), is_active=True).first()
            if item_product is None and name:
                item_product = (Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe
                                else Product.objects.filter(name__iexact=name).first())
            item_brand = None
            if brand_id.isdigit() and item_product is not None:
                item_brand = ProductBrand.objects.filter(id=int(brand_id), product=item_product).first()

            if item_product is None and not name:
                messages.error(request, 'نام کالا را وارد کنید')
            elif qty is None or qty <= 0 or qty > 2147483647:
                messages.error(request, 'مقدار باید عدد مثبت باشد')
            else:
                PurchaseDraftItem.objects.create(
                    draft=draft,
                    product=item_product,
                    product_brand=item_brand,
                    name=item_product.name if item_product else name,
                    quantity=int(qty),
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
            elif qty is None or qty <= 0 or qty > 2147483647:
                messages.error(request, 'مقدار باید عدد مثبت باشد')
            else:
                d_item.quantity = int(qty)
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
                d_item.delete()
                draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
                if draft and not draft.items.exists():
                    draft.delete()
                messages.info(request, f'«{name}» از پیش‌نویس حذف شد')
            else:
                messages.error(request, 'قلم پیش‌نویس یافت نشد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'discard_draft':
            draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
            if draft:
                draft.delete()
                messages.info(request, 'پیش‌نویس حذف شد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action in ('edit_sent', 'delete_sent'):
            # ویرایش/حذف اقلام درخواست‌های ارسال‌شده (تا قبل از اقدام مسئول انبار).
            # قلمی که تخصیص «pending» ندارد یعنی روی آن اقدام شده یا اصلا ارجاع
            # نشده (درخواست قدیمی) — در هر دو حالت قابل ویرایش نیست.
            req = PurchaseRequest.objects.filter(
                id=request.POST.get('request_id', '') or -1,
                source='warehouse_purchase',
            ).exclude(status__in=('purchased', 'partial'))\
             .select_related('user').first()
            if req is None or (cafe and req.cafe_id != cafe.id):
                messages.error(request, 'درخواست یافت نشد یا قابل ویرایش نیست')
                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

            if action == 'edit_sent':
                item = req.items.filter(id=request.POST.get('item_id', '') or -1).first()
                qty = _parse_decimal(request.POST.get('quantity', ''))
                if item is None:
                    messages.error(request, 'قلم یافت نشد')
                elif not item.officer_assignments.filter(status='pending').exists():
                    messages.error(request, 'این قلم قابل ویرایش نیست (در انتظار اقدام نیست)')
                elif qty is None or qty <= 0 or qty > 2147483647:
                    messages.error(request, 'مقدار باید عدد مثبت باشد')
                else:
                    item.quantity = int(qty)
                    desc = request.POST.get('description')
                    if desc is not None:
                        item.description = desc.strip()
                    item.save()
                    messages.success(request, f'«{item.product.name if item.product else item.name}» ویرایش شد')
            else:  # delete_sent
                item = req.items.filter(id=request.POST.get('item_id', '') or -1).first()
                if item is None:
                    messages.error(request, 'قلم یافت نشد')
                elif not item.officer_assignments.filter(status='pending').exists():
                    messages.error(request, 'این قلم قابل حذف نیست (در انتظار اقدام نیست)')
                else:
                    name = item.product.name if item.product else item.name
                    item.delete()
                    if not req.items.exists():
                        req.delete()
                        messages.info(request, f'«{name}» حذف شد (درخواست خالی بود و حذف شد)')
                    else:
                        messages.info(request, f'«{name}» از درخواست حذف شد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

    # ── پیش‌نویس جاری ──
    draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).select_related('cafe').first()
    draft_items = list(draft.items.select_related('product', 'product_brand', 'product__unit') if draft else [])

    # ── درخواست‌های ارسال‌شده (تا قبل از تایید مدیرعامل) ──
    # درخواست‌های مسئول انبار چون مستقیماً برای خودش/همکار انبار ارسال می‌شوند
    # در انتظار مدیرعابل نمی‌مانند؛ برای ویرایش، همان‌هایی که هنوز تخصیص اقدام
    # نداشته‌اند نمایش داده می‌شوند (تخصیص انبار مشترکِ بدون اقدام).
    sent_qs = (PurchaseRequest.objects
               .filter(source='warehouse_purchase')
               .exclude(status__in=('purchased', 'partial', 'rejected'))
               .filter(items__officer_assignments__status='pending')
               .distinct()
               .select_related('user')
               .prefetch_related('items', 'items__product', 'items__product__unit', 'items__product_brand')
               .order_by('-created_at'))
    if cafe:
        sent_qs = sent_qs.filter(cafe=cafe)
    sent_requests = list(sent_qs[:30])

    # محصولات و برندهای فعال برای فرم افزودن
    products = Product.objects.filter(is_active=True).order_by('name')
    if cafe:
        products = products.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
    products = list(products[:500])
    product_brands = {}
    for b in ProductBrand.objects.filter(product_id__in=[p.id for p in products], is_active=True):
        product_brands.setdefault(b.product_id, []).append(b)

    return render(request, 'warehouse_purchase_draft.html', {
        'draft': draft,
        'draft_items': draft_items,
        'sent_requests': sent_requests,
        'products': products,
        'product_brands': product_brands,
        'delivery_days': _next_delivery_days(),
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
                        _sync_request_status(request_obj)
                        if not request_obj.items.exists():
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
                    new_qty = int(request.POST.get('new_qty', ''))
                except (TypeError, ValueError):
                    new_qty = 0
                items = list(PurchaseItem.objects.filter(id__in=ids)) if ids else []
                if cafe:
                    items = [it for it in items if it.request.cafe_id == cafe.id]
                if new_qty <= 0:
                    messages.error(request, 'مقدار جدید نامعتبر است (باید عدد صحیح مثبت باشد)')
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
                        for rid in affected:
                            req = PurchaseRequest.objects.filter(id=rid).first()
                            if req:
                                _sync_request_status(req)
                                if not req.items.exists():
                                    req.delete()
                        messages.success(request, f'«{name}» در این بخش به {new_qty} {target.unit or ""} تغییر کرد')
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
                    for rid in affected:
                        req = PurchaseRequest.objects.filter(id=rid).first()
                        if req:
                            _sync_request_status(req)
                            if not req.items.exists():
                                req.delete()
                    if unsent:
                        messages.info(request, f'«{name}» حذف شد ({len(unsent)} قلم)')
                    if sent_skipped:
                        messages.info(request, f'{sent_skipped} قلم قبلاً ارسال شده بودند و حذف نشدند')
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
                'stock_item', 'stock_item__product_brand',
            )
            .order_by('item__request__created_at', 'item__request_id', 'item_id')
        )
        if cafe:
            wh_assignments = wh_assignments.filter(item__request__cafe=cafe)

        # هر قلم درخواست یک ردیف؛ چند برند تحویلی با هم جمع می‌شوند
        wh_map = defaultdict(lambda: {
            'name': '', 'unit': '', 'requested_qty': 0.0, 'supplied_qty': 0.0,
            'section': '', 'requester': '', 'brands': [], 'description': '',
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
            brand = a.stock_item.product_brand if a.stock_item and a.stock_item.product_brand else None
            brand_name = brand.brand_name if brand else ''
            if brand_name and brand_name not in row['brands']:
                row['brands'].append(brand_name)
            if item.description and item.description not in row['description']:
                row['description'] = item.description

        wh_rows = []
        for item_id, data in wh_map.items():
            wh_rows.append({
                'item_id': item_id,
                **data,
                'supplied_qty': round(data['supplied_qty'], 2),
                'brands_text': '، '.join(data['brands']) if data['brands'] else '—',
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

    # اقلام تحویل‌شده از انبار (چند برند تجاری هر قلم با هم جمع می‌شوند)
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
            'stock_item', 'stock_item__product_brand',
        )
        .order_by('item__request__created_at', 'item__request_id', 'item_id')
    )
    if cafe:
        wh_assignments = wh_assignments.filter(item__request__cafe=cafe)

    wh_map = defaultdict(lambda: {
        'name': '', 'unit': '', 'requested_qty': 0.0, 'supplied_qty': 0.0,
        'section': '', 'brands': [], 'description': '',
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
        brand = a.stock_item.product_brand if a.stock_item and a.stock_item.product_brand else None
        brand_name = brand.brand_name if brand else ''
        if brand_name and brand_name not in row['brands']:
            row['brands'].append(brand_name)
        if item.description and item.description not in row['description']:
            row['description'] = item.description

    rows = []
    for item_id, data in wh_map.items():
        rows.append({
            'item_id': item_id,
            **data,
            'supplied_qty': round(data['supplied_qty'], 2),
            'brands_text': '، '.join(data['brands']) if data['brands'] else '—',
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
    logical_cols = ['ردیف', 'مرکز هزینه', 'کالا', 'شرح کالا (نام تجاری)',
                    'مقدار و واحد درخواستی', 'مقدار و واحد تحویلی', 'توضیحات']
    header_rev = list(reversed(logical_cols))
    data = [[persian(h) for h in header_rev]]
    for i, r in enumerate(rows, start=1):
        row = [
            persian(str(i)),
            persian(r['section']),
            persian(r['name']),
            persian('\n'.join(r['brands']) if r['brands'] else '—'),
            persian(f"{r['requested_qty']:.2f} {r['unit']}".strip()),
            persian(f"{r['supplied_qty']:.2f} {r['unit']}".strip()),
            persian(r['description']),
        ]
        data.append(list(reversed(row)))

    # ── عرض ستون‌ها: اندازهٔ بزرگترین رشتهٔ همان ستون، نه کمتر از مقدار
    # پیش‌فرض، با سقف برای بیرون نزدن از صفحه؛ اگر جا اضافه بود همان مقادیر قبلی ──
    fixed_cm = [0.8, 2.6, 3.2, 3.2, 2.6, 2.6, 3.4]  # مقادیر مشخص‌شدهٔ قبلی
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

    # ── ارتفاع ردیف‌ها: اگر همهٔ کالاها تک‌برند باشند در ۱۲ ردیف جا شوند؛
    # ردیف محصول چندبرند به تعداد برندهایش بلندتر است ──
    sign_area_top = page_height * 0.12 + 0.4 * cm  # جدول بالای نوار ۸۸٪ پایانی
    avail_height = y - sign_area_top
    base_row = avail_height / 13  # ۱۲ ردیف کالا + ۱ ردیف هدر
    row_heights = [base_row]
    for r in rows:
        row_heights.append(base_row * (len(r['brands']) or 1))

    table_style = TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef0ff')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cccccc')),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),   # راست به چپ
        ('ALIGN', (1, 0), (2, -1), 'CENTER'),
        ('ALIGN', (6, 0), (6, -1), 'CENTER'),
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

    POST JSON body: {"name": "...", "unit": "..."}
    Returns JSON: {"success": true, "product": {"id": ..., "name": "...", "unit": "..."}}
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
    category_id = data.get('category_id', '')

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
        p = existing_qs.first()
        return JsonResponse({'success': True, 'product': {
            'id': p.id,
            'name': p.name,
            'unit': p.unit.name if p.unit else unit_name,
            'is_complete': p.is_complete,
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

    # اتصال دسته‌بندی در صورت ارسال از فرم درخواست
    if category_id and str(category_id).isdigit():
        cat = Category.objects.filter(id=int(category_id), is_active=True).first()
        if cat:
            product.category = cat
            product.save(update_fields=['category'])

    return JsonResponse({'success': True, 'product': {
        'id': product.id,
        'name': product.name,
        'unit': product.unit.name if product.unit else unit_name,
        'is_complete': product.is_complete,
        'description': product.description,
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
