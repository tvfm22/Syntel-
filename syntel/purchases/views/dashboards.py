from __future__ import annotations

from ..models import Invoice
from ..models import Payment
from ..models import PurchaseRequest
from ..models import StockItem
from ..models import StockRequest
from ..models import StockTransaction
from ..models import Supplier
from .helpers import _received_delivery_rows
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import role_required
from django.db.models import F
from django.db.models import Q
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone


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
    if request.user.role in ("requester", "kitchen", "bar", "counter", "cashier", "facilities"):
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
        qs = PurchaseRequest.objects.filter(
            Q(user__role=dept['role'])
            | Q(user__role='requester', user__department__name=dept['name'])
        )
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
@role_required(["admin", "accounting", "ceo", "manager"])
def finance_dashboard(request: HttpRequest) -> HttpResponse:
    today_local = timezone.localdate()

    # آمار اصلی (شکست بسته بدون کافه)
    cafe = get_cafe_from_request(request)
    if cafe is None:
        invoice_qs = Invoice.objects.none()
        payment_qs = Payment.objects.none()
        stock_qs = StockItem.objects.none()
        stock_req_qs = StockRequest.objects.none()
        tx_qs = StockTransaction.objects.none()
    else:
        invoice_qs = Invoice.objects.filter(cafe=cafe)
        payment_qs = Payment.objects.filter(
            Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe)
        ).distinct()
        stock_qs = StockItem.objects.filter(warehouse__cafe=cafe)
        stock_req_qs = StockRequest.objects.filter(purchase_item__request__cafe=cafe)
        tx_qs = StockTransaction.objects.filter(stock_item__warehouse__cafe=cafe)

    stats = {
        "invoices": invoice_qs.count(),
        "payments": payment_qs.count(),
    }

    # آمار انبار
    stock_stats = {
        "total_items": stock_qs.count(),
        "low_stock_items": stock_qs.filter(quantity__lte=F('min_quantity')).count(),
        "total_stock_value": 0,
        "pending_stock_requests": stock_req_qs.filter(status='pending').count(),
        "fulfilled_today": stock_req_qs.filter(
            status='fulfilled',
            fulfilled_at__date=today_local
        ).count(),
    }

    # موجودی‌های کم
    low_stock_items = stock_qs.select_related('product', 'unit').filter(
        quantity__lte=F('min_quantity')
    )[:10]

    # آخرین تراکنش‌های انبار
    recent_transactions = tx_qs.select_related(
        'stock_item__product',
        'created_by'
    ).order_by('-created_at')[:10]

    return render(request, "finance_dashboard.html", {
        "stats": stats,
        "stock_stats": stock_stats,
        "low_stock_items": low_stock_items,
        "recent_transactions": recent_transactions,
        "today": today_local,
    })


@custom_login_required
@role_required(["finance_manager"])
def finance_manager_dashboard(request: HttpRequest) -> HttpResponse:
    """داشبورد اختصاصی مدیر مالی"""
    today_local = timezone.localdate()

    cafe = get_cafe_from_request(request)
    if cafe is None:
        invoice_qs = Invoice.objects.none()
        supplier_qs = Supplier.objects.none()
        payment_qs = Payment.objects.none()
    else:
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

    return render(request, "finance_manager_dashboard.html", {
        "stats": stats,
        "today": today_local,
    })


@custom_login_required
@role_required(["manager", "ceo", "admin"])
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
