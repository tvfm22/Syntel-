from __future__ import annotations

from ..models import ActivityLog
from ..models import OfficerAssignment
from ..models import PurchaseOrder
from ..models import PurchaseOrderItem
from ..models import PurchaseRequest
from ..models import Supplier
from ..models import User
from ..numbering import request_number as request_number_for
from .helpers import _parse_iso_date
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import get_scoped_locked_object_or_404
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from .workflow_helpers import _officer_can_role
from .workflow_helpers import _officer_users
from .workflow_helpers import _sync_request_status
from datetime import datetime
from datetime import timedelta
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST


_PO_ALLOWED_TRANSITIONS = {
    'draft': {'sent', 'cancelled'},
    'ordered': {'sent', 'confirmed', 'cancelled'},
    'sent': {'confirmed', 'in_transit', 'delivered', 'cancelled'},
    'confirmed': {'in_transit', 'delivered', 'cancelled'},
    'in_transit': {'delivered', 'cancelled'},
    'delivered': {'inspected', 'completed', 'cancelled'},
    'inspected': {'completed', 'cancelled'},
    'completed': set(),
    'cancelled': set(),
}


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_orders_list(request):
    orders = filter_by_cafe(
        PurchaseOrder.objects.select_related("supplier", "request").all(),
        request,
    )
    return render(request, "purchase_orders_list.html", {"orders": orders})


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_create(request, request_id):
    cafe = get_cafe_from_request(request)
    purchase_request = get_scoped_object_or_404(PurchaseRequest, request, id=request_id)
    suppliers = filter_by_cafe(Supplier.objects.all(), request)
    if request.method == "POST":
        supplier_raw = (request.POST.get("supplier") or "").strip()
        if not supplier_raw.isdigit():
            messages.error(request, 'تأمین‌کننده معتبر انتخاب کنید.')
            return render(
                request,
                "purchase_order_form.html",
                {"purchase_request": purchase_request, "suppliers": suppliers},
            )
        supplier = get_scoped_object_or_404(Supplier, request, id=int(supplier_raw))
        delivery_raw = (request.POST.get("expected_delivery_date") or "").strip()
        delivery_date = _parse_iso_date(delivery_raw) if delivery_raw else None
        if delivery_raw and delivery_date is None:
            messages.error(request, 'فرمت تاریخ تحویل نامعتبر است.')
            return render(
                request,
                "purchase_order_form.html",
                {"purchase_request": purchase_request, "suppliers": suppliers},
            )
        with transaction.atomic():
            existing_order = PurchaseOrder.objects.filter(request=purchase_request).first()
            if existing_order is not None:
                return get_cafe_redirect_url("purchase_order_detail", request, order_id=existing_order.id)
            order_number = request.POST.get("order_number") or f"ORD-{timezone.now().strftime('%Y%m%d%H%M%S')}"
            order = PurchaseOrder.objects.create(
                request=purchase_request,
                supplier=supplier,
                order_number=order_number,
                status="draft",
                created_by=request.user,
                delivery_date=delivery_date,
                notes=request.POST.get("notes", ""),
            )
            for req_item in purchase_request.items.all():
                PurchaseOrderItem.objects.create(
                    order=order,
                    request_item=req_item,
                    product=req_item.product,
                    quantity=req_item.quantity,
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


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "accounting", "ceo"])
def purchase_order_detail(request, order_id):
    order = get_scoped_object_or_404(PurchaseOrder, request, id=order_id)
    return render(request, "purchase_order_detail.html", {"order": order})


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_order_update(request, order_id):
    order = get_scoped_object_or_404(PurchaseOrder, request, id=order_id)
    if request.method == "POST":
        new_status = (request.POST.get("status") or order.status).strip()
        valid_statuses = {choice[0] for choice in PurchaseOrder.STATUS_CHOICES}
        if new_status != order.status:
            if new_status not in valid_statuses:
                messages.error(request, 'وضعیت سفارش نامعتبر است.')
                return render(request, "purchase_order_form.html", {"order": order})
            allowed = _PO_ALLOWED_TRANSITIONS.get(order.status, set())
            if new_status not in allowed:
                messages.error(request, 'تغییر وضعیت درخواستی مجاز نیست.')
                return render(request, "purchase_order_form.html", {"order": order})
            order.status = new_status
        order.notes = request.POST.get("notes", order.notes)
        order.save()
        return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)
    return render(request, "purchase_order_form.html", {"order": order})


def _set_order_status(request, order_id, status):
    with transaction.atomic():
        order = get_scoped_locked_object_or_404(PurchaseOrder, request, id=order_id)
        allowed = _PO_ALLOWED_TRANSITIONS.get(order.status, set())
        if status not in allowed:
            messages.error(request, f'تغییر وضعیت سفارش از «{order.status}» به «{status}» مجاز نیست.')
            return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)
        order.status = status
        now = timezone.now()
        if status == "sent" and not order.sent_at:
            order.sent_at = now
        elif status == "confirmed" and not order.confirmed_at:
            order.confirmed_at = now
        elif status == "delivered" and not order.delivered_at:
            order.delivered_at = now
        order.save()
    return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)


@custom_login_required
@role_required(["purchasing", "manager"])
@require_POST
def purchase_order_send(request, order_id):
    return _set_order_status(request, order_id, "sent")


@custom_login_required
@role_required(["purchasing", "manager"])
@require_POST
def purchase_order_confirm(request, order_id):
    return _set_order_status(request, order_id, "confirmed")


@custom_login_required
@role_required(["purchasing", "manager"])
@require_POST
def purchase_order_mark_in_transit(request, order_id):
    return _set_order_status(request, order_id, "in_transit")


@custom_login_required
@role_required(["purchasing", "manager"])
@require_POST
def purchase_order_deliver(request, order_id):
    return _set_order_status(request, order_id, "delivered")


@custom_login_required
@role_required(["purchasing", "manager"])
@require_POST
def purchase_order_inspect(request, order_id):
    return _set_order_status(request, order_id, "inspected")


@custom_login_required
@role_required(["purchasing", "manager"])
@require_POST
def purchase_order_complete(request, order_id):
    with transaction.atomic():
        order = get_scoped_locked_object_or_404(PurchaseOrder, request, id=order_id)
        allowed = _PO_ALLOWED_TRANSITIONS.get(order.status, set())
        if "completed" not in allowed:
            messages.error(request, f'تغییر وضعیت سفارش از «{order.status}» به «completed» مجاز نیست.')
            return get_cafe_redirect_url("purchase_order_detail", request, order_id=order.id)
        order.status = "completed"
        order.completed_at = timezone.now()
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
@require_POST
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


@custom_login_required
@role_required(['manager', 'ceo', 'warehouse'])
def purchase_orders_status(request: HttpRequest) -> HttpResponse:
    """صفحه وضعیت خرید: گزارش خریدهای انجام‌شده، خریداری‌نشده و در انتظار خرید به تفکیک هر شخص."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    can_edit_status = (
        request.user.role in ('manager', 'ceo', 'admin') or request.user.is_superuser
    )

    if request.method == 'POST':
        if not can_edit_status:
            messages.error(request, 'فقط مدیرعامل یا مدیر امکان ویرایش مستقیم وضعیت را دارد.')
            return redirect('purchase_orders_status', cafe_code=cafe_code)

        assignment_id = request.POST.get('assignment_id', '')
        action = request.POST.get('action', '')
        try:
            assignment = OfficerAssignment.objects.select_related('item__request').get(id=int(assignment_id))
        except (OfficerAssignment.DoesNotExist, ValueError, TypeError):
            assignment = None

        if assignment and assignment.item.request.cafe_id != cafe.id:
            assignment = None

        if assignment:
            if action == 'status':
                new_status = request.POST.get('status', '')
                if assignment.status == 'fulfilled' and new_status != 'fulfilled':
                    messages.error(request, 'تغییر مستقیم وضعیت تخصیص تأمین‌شده از انبار مجاز نیست — از بازگشایی انبار استفاده کنید.')
                    return redirect('purchase_orders_status', cafe_code=cafe_code)
                if new_status in dict(OfficerAssignment.STATUS_CHOICES):
                    assignment.status = new_status
                    assignment.completed_at = timezone.now() if new_status in ('purchased', 'not_purchased', 'returned', 'fulfilled', 'not_in_stock') else None
                    assignment.save()
                    _sync_request_status(assignment.item.request)
                    messages.success(request, f'وضعیت «{assignment.item.name}» به‌روز شد.')
            elif action == 'reassign':
                officer_id = (request.POST.get('officer_id') or '').strip()
                officer = None
                if officer_id.isdigit():
                    officer = _officer_users(cafe, assignment.target_role).filter(id=int(officer_id)).first()
                if officer and _officer_can_role(officer, assignment.target_role):
                    assignment.assigned_to = officer
                    assignment.save()
                    messages.success(request, f'«{assignment.item.name}» به {officer.get_full_name() or officer.username} واگذار شد.')
                else:
                    messages.error(request, 'مسئول انتخاب‌شده این نقش را ندارد.')
            elif action == 'delete':
                if assignment.status == 'fulfilled':
                    messages.error(request, 'حذف تخصیص تأمین‌شده از انبار مجاز نیست — از بازگشایی انبار استفاده کنید.')
                    return redirect('purchase_orders_status', cafe_code=cafe_code)
                item_name = assignment.item.name
                assignment.delete()
                _sync_request_status(assignment.item.request)
                messages.info(request, f'تخصیص «{item_name}» حذف شد.')
        return redirect('purchase_orders_status', cafe_code=cafe_code)

    # ── فیلترها + ناوبری عقب/جلوی تاریخ ──
    now_local = timezone.localtime(timezone.now())
    today = now_local.date()
    day_raw = (request.GET.get('day') or '').strip()
    is_all_days = (day_raw == 'all')
    selected_date = None
    if day_raw and not is_all_days:
        try:
            selected_date = datetime.strptime(day_raw, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            selected_date = None
    if selected_date is None:
        selected_date = today

    day_param = 'all' if is_all_days else selected_date.isoformat()
    date_str = selected_date.isoformat()
    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    officer_filter = request.GET.get('officer', '')
    status_filter = request.GET.get('status', '')
    if 'role' in request.GET:
        role_filter = request.GET.get('role', '')
    elif request.user.role == 'warehouse':
        role_filter = 'purchasing'
    else:
        role_filter = ''
    q_filter = (request.GET.get('q') or '').strip()

    base_qs = (
        OfficerAssignment.objects
        .exclude(item__request__status__in=('pending_manager_approval', 'rejected', 'draft'))
        .select_related(
            'item', 'item__request', 'item__request__user', 'item__request__user__department',
            'item__product', 'item__source_item', 'item__source_item__request',
            'item__source_item__request__user', 'item__source_item__request__user__department',
            'assigned_to', 'assigned_by',
        )
        .order_by('-completed_at', '-assigned_at')
    )
    if cafe:
        base_qs = base_qs.filter(item__request__cafe=cafe)
    if not is_all_days:
        tz = timezone.get_current_timezone()
        day_start = timezone.make_aware(datetime.combine(selected_date, datetime.min.time()), tz)
        day_end = day_start + timedelta(days=1)
        base_qs = base_qs.filter(
            Q(completed_at__gte=day_start, completed_at__lt=day_end)
            | Q(completed_at__isnull=True, assigned_at__gte=day_start, assigned_at__lt=day_end)
        )
    if officer_filter and officer_filter.isdigit():
        base_qs = base_qs.filter(assigned_to_id=int(officer_filter))
    elif officer_filter:
        base_qs = base_qs.none()
    if role_filter in ('purchasing', 'warehouse'):
        base_qs = base_qs.filter(target_role=role_filter)
    if q_filter:
        base_qs = base_qs.filter(
            Q(item__name__icontains=q_filter) | Q(item__product__name__icontains=q_filter)
        )

    # آمار خلاصه روی پایه فیلترشده (قبل از فیلتر وضعیت)
    purchasing_stats_qs = base_qs.filter(target_role='purchasing') if role_filter != 'warehouse' else base_qs
    stats = {
        'total': purchasing_stats_qs.count(),
        'purchased': purchasing_stats_qs.filter(status='purchased').count(),
        'not_purchased': purchasing_stats_qs.filter(status='not_purchased').count(),
        'pending': purchasing_stats_qs.filter(status='pending').count(),
    }

    assignments = base_qs
    if status_filter:
        assignments = assignments.filter(status=status_filter)
    assignments = list(assignments[:400])

    # فهرست کامل افراد: مسئولان خرید + انبار + هر فردی (مثل خودخریدی انبار/مدیرعامل) که خرید یا عدم خرید ثبت کرده
    seen_ids = set()
    all_officers = []
    for role in ('purchasing', 'warehouse'):
        for u in _officer_users(cafe, role):
            if u.id not in seen_ids:
                seen_ids.add(u.id)
                all_officers.append(u)
    assigned_user_ids = (
        OfficerAssignment.objects
        .filter(assigned_to__isnull=False, **({'item__request__cafe': cafe} if cafe else {}))
        .values_list('assigned_to_id', flat=True)
        .distinct()
    )
    for uid in assigned_user_ids:
        if uid not in seen_ids:
            u = User.objects.filter(id=uid).first()
            if u:
                seen_ids.add(u.id)
                all_officers.append(u)

    return render(request, 'purchase_orders_status.html', {
        'assignments': assignments,
        'all_officers': all_officers,
        'officer_filter': officer_filter,
        'status_filter': status_filter,
        'role_filter': role_filter,
        'q_filter': q_filter,
        'stats': stats,
        'selected_date': selected_date,
        'prev_date': prev_date,
        'next_date': next_date,
        'today_str': today.isoformat(),
        'date_str': date_str,
        'is_all_days': is_all_days,
        'day_param': day_param,
        'status_choices': OfficerAssignment.STATUS_CHOICES,
        'can_edit_status': can_edit_status,
        'cafe_code': cafe_code,
        'user': request.user,
    })
