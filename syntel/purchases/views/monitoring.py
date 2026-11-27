from __future__ import annotations

from ..models import ActivityLog
from ..models import Invoice
from ..models import Payment
from ..models import Product
from ..models import PurchaseOrder
from ..models import PurchaseRequest
from ..models import StockItem
from ..models import Supplier
from ..models import User
from .helpers import _backup_admin_required
from .helpers import _human_size
from .helpers import _is_sqlite_engine
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import role_required
from datetime import timedelta
from django.conf import settings
from django.db import connections
from django.db.models import Count
from django.db.models import F
from django.db.models import Q
from django.db.models import Subquery
from django.db.models import Sum
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
import django
import os
import platform
import psutil


@custom_login_required
@_backup_admin_required
def create_migration_script(request):
    return HttpResponse("این قابلیت غیرفعال شده است.", status=410)


@custom_login_required
@_backup_admin_required
def run_migration_script(request):
    return HttpResponse("این قابلیت غیرفعال شده است.", status=410)


@custom_login_required
@role_required(["manager"])
def system_health_check(request):
    return render(request, "system_health.html", {"status": "healthy"})


def _build_monitoring_metrics(cafe):
    """Collect non-blocking system metrics, scoped business stats, and 7-day trends."""
    cpu_pct = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(settings.DATA_PATH))

    act_qs = ActivityLog.objects.all()
    pr_qs = PurchaseRequest.objects.all()
    inv_qs = Invoice.objects.all()
    po_qs = PurchaseOrder.objects.all()
    pay_qs = Payment.objects.all()
    sup_qs = Supplier.objects.all()
    prod_qs = Product.objects.all()
    stock_qs = StockItem.objects.filter(product__is_active=True)
    user_qs = User.objects.all()

    if cafe:
        act_qs = act_qs.filter(request__cafe=cafe)
        pr_qs = pr_qs.filter(cafe=cafe)
        inv_qs = inv_qs.filter(cafe=cafe)
        po_qs = po_qs.filter(request__cafe=cafe)
        cafe_payment_ids = Payment.objects.filter(
            Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe)
        ).values('id')
        pay_qs = pay_qs.filter(id__in=Subquery(cafe_payment_ids))
        sup_qs = sup_qs.filter(cafe=cafe)
        prod_qs = prod_qs.filter(cafe=cafe)
        stock_qs = stock_qs.filter(warehouse__cafe=cafe)
        user_qs = user_qs.filter(cafe=cafe)

    today = timezone.localdate()
    since_24h = timezone.now() - timedelta(hours=24)

    open_invoices = inv_qs.exclude(status__in=['paid', 'cancelled'])
    pending_invoices = open_invoices.count()
    overdue_invoices = open_invoices.filter(due_date__lt=today).count()
    outstanding_payments = sum(
        (float(inv.remaining_amount) for inv in open_invoices),
        0.0,
    )

    low_stock_products = stock_qs.filter(quantity__lte=F('min_quantity')).count()
    inactive_suppliers = sup_qs.exclude(status='active').count()
    active_connections = len(connections.all())

    recent_activity = {
        "new_requests": pr_qs.filter(created_at__gte=since_24h).count(),
        "new_orders": po_qs.filter(created_at__gte=since_24h).count(),
        "new_invoices": inv_qs.filter(created_at__gte=since_24h).count(),
        "new_payments": pay_qs.filter(created_at__gte=since_24h).count(),
    }

    health_status = {
        "database_status": "healthy",
        "overall_health": "good",
        "pending_approvals": pr_qs.filter(status__in=["pending", "pending_manager_approval"]).count(),
        "pending_invoices": pending_invoices,
        "overdue_invoices": overdue_invoices,
        "outstanding_payments": outstanding_payments,
        "active_connections": active_connections,
        "low_stock_products": low_stock_products,
        "inactive_suppliers": inactive_suppliers,
    }

    system_resources = {
        "cpu_percent": cpu_pct,
        "cpu_usage": cpu_pct,
        "memory_percent": mem.percent,
        "memory_used": round(mem.used / (1024 * 1024), 1),
        "memory_total": round(mem.total / (1024 * 1024), 1),
        "memory_free": _human_size(mem.available),
        "disk_usage": disk.percent,
        "disk_percent": disk.percent,
        "disk_used": round(disk.used / (1024 * 1024 * 1024), 2),
        "disk_total": round(disk.total / (1024 * 1024 * 1024), 2),
        "disk_free": _human_size(disk.free),
    }

    stats = {
        "total_users": user_qs.count(),
        "active_users": user_qs.filter(is_active=True).count(),
        "total_requests": pr_qs.count(),
        "pending_requests": pr_qs.filter(status__in=["pending", "pending_manager_approval"]).count(),
        "total_orders": po_qs.count(),
        "total_invoices": inv_qs.count(),
        "paid_invoices": inv_qs.filter(status='paid').count(),
        "total_payments": pay_qs.count(),
        "total_suppliers": sup_qs.count(),
        "total_products": prod_qs.count(),
    }

    # 7-day performance trends (JSON-serializable lists)
    start_day = today - timedelta(days=6)
    req_by_day = {
        row['created_at__date']: row['c']
        for row in pr_qs.filter(created_at__date__gte=start_day)
        .values('created_at__date')
        .annotate(c=Count('id'))
    }
    pay_by_day = {
        row['payment_date']: (row['c'], float(row['total'] or 0))
        for row in pay_qs.filter(payment_date__gte=start_day)
        .values('payment_date')
        .annotate(c=Count('id'), total=Sum('amount'))
    }

    daily_requests = []
    daily_payments = []
    daily_revenue = []
    for offset in range(7):
        day = start_day + timedelta(days=offset)
        day_label = day.isoformat()
        daily_requests.append({"date": day_label, "count": int(req_by_day.get(day, 0))})
        p_count, p_total = pay_by_day.get(day, (0, 0.0))
        daily_payments.append({"date": day_label, "count": int(p_count)})
        daily_revenue.append({"date": day_label, "amount": p_total})

    performance_trends = {
        "daily_requests": daily_requests,
        "daily_payments": daily_payments,
        "daily_revenue": daily_revenue,
    }

    return {
        "system_resources": system_resources,
        "recent_activity": recent_activity,
        "health_status": health_status,
        "stats": stats,
        "performance_trends": performance_trends,
        "active_connections": active_connections,
        "act_qs": act_qs,
    }


@custom_login_required
@role_required(["manager"])
def system_monitoring(request):
    cafe = get_cafe_from_request(request)
    if not request.user.is_superuser and cafe is None:
        return HttpResponseForbidden('ابتدا کافه را انتخاب کنید.')

    db_cfg = settings.DATABASES['default']
    is_sqlite = _is_sqlite_engine()
    db_name = str(db_cfg.get('NAME', ''))
    db_size_bytes = os.path.getsize(db_name) if is_sqlite and os.path.exists(db_name) else 0

    system_info = {
        "os": platform.system(),
        "os_version": platform.release(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "django_version": django.get_version(),
        "cpu_count": psutil.cpu_count(),
        "total_memory": _human_size(psutil.virtual_memory().total),
        "total_disk": _human_size(psutil.disk_usage(str(settings.DATA_PATH)).total),
    }

    db_info = {
        "database_name": os.path.basename(db_name) if is_sqlite else db_name,
        "database_engine": db_cfg.get('ENGINE', ''),
        "host": db_cfg.get('HOST') or 'localhost',
        "port": db_cfg.get('PORT') or '-',
    }

    metrics = _build_monitoring_metrics(cafe)
    context = {
        "last_updated": timezone.now(),
        "system_info": system_info,
        "db_info": db_info,
        "db_size": _human_size(db_size_bytes) if db_size_bytes else '-',
        "system_resources": metrics["system_resources"],
        "recent_activity": metrics["recent_activity"],
        "health_status": metrics["health_status"],
        "stats": metrics["stats"],
        "performance_trends": metrics["performance_trends"],
        "active_connections": metrics["active_connections"],
        "alerts": [],
    }
    return render(request, "system_monitoring.html", context)


@custom_login_required
@role_required(["manager"])
def system_monitoring_api(request):
    cafe = get_cafe_from_request(request)
    if not request.user.is_superuser and cafe is None:
        return JsonResponse({"success": False, "error": "کافه فعال یافت نشد."}, status=403)

    metrics = _build_monitoring_metrics(cafe)
    recent_activities = [
        {
            "id": act.id,
            "user": act.user.username if act.user else "-",
            "action": act.action,
            "description": act.description,
            "timestamp": act.created_at.isoformat(),
        }
        for act in metrics["act_qs"].select_related("user").order_by("-created_at")[:10]
    ]

    resources_payload = {
        "cpu_percent": metrics["system_resources"]["cpu_percent"],
        "cpu_usage": metrics["system_resources"]["cpu_usage"],
        "memory_percent": metrics["system_resources"]["memory_percent"],
        "disk_usage": metrics["system_resources"]["disk_usage"],
        "disk_percent": metrics["system_resources"]["disk_percent"],
    }
    health_payload = metrics["health_status"]

    data = {
        "system_resources": resources_payload,
        "resources": resources_payload,
        "recent_activity": metrics["recent_activity"],
        "recent_activities": recent_activities,
        "health_status": health_payload,
        "health": health_payload,
        "active_connections": metrics["active_connections"],
        "timestamp": timezone.now().isoformat(),
    }
    return JsonResponse({
        "success": True,
        "data": data,
        "resources": resources_payload,
        "health": health_payload,
    })


def handler404(request, exception=None):
    return HttpResponse("صفحه مورد نظر یافت نشد.", status=404)
