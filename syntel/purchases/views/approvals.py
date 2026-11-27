from __future__ import annotations

from ..models import ActivityLog
from ..models import PurchaseRequest
from ..models import User
from ..numbering import request_number as request_number_for
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_scoped_locked_object_or_404
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from .workflow_helpers import _assign_item
from .workflow_helpers import _notify_officer
from .workflow_helpers import _officer_users
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme


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
    department_filter = (request.GET.get('department') or '').strip()
    if department_filter:
        dep_q = Q(user__role=department_filter) | Q(user__department__name=department_filter)
        if department_filter.isdigit():
            dep_q = dep_q | Q(user__department_id=int(department_filter))
        requests_qs = requests_qs.filter(dep_q)
    
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

            req_cafe = purchase_request.cafe
            target_officer = purchase_request.intended_purchasing_officer if purchase_request.intended_purchasing_officer_id else None
            for p_item in purchase_request.items.all():
                _assign_item(
                    p_item,
                    'purchasing',
                    target_officer,
                    purchase_request.user,
                    status='pending',
                )
                if target_officer is not None:
                    if target_officer.id != purchase_request.user_id:
                        _notify_officer(target_officer, p_item, 'purchasing', req_cafe)
                else:
                    for officer in _officer_users(req_cafe, 'purchasing'):
                        _notify_officer(officer, p_item, 'purchasing', req_cafe)

            ActivityLog.objects.create(
                user=request.user,
                request=purchase_request,
                action='updated',
                description=f'تایید درخواست {request_number_for(purchase_request.id)} توسط {request.user.get_full_name() or request.user.username}'
            )

            if purchase_request.source == 'warehouse_purchase':
                if purchase_request.intended_purchasing_officer_id == purchase_request.user_id:
                    messages.success(request, f'درخواست خرید {request_number_for(purchase_request.id)} تایید شد و به «خریدهای من» مسئول انبار اضافه شد')
                elif purchase_request.intended_purchasing_officer_id:
                    officer_label = purchase_request.intended_purchasing_officer.get_full_name() or purchase_request.intended_purchasing_officer.username
                    messages.success(request, f'درخواست خرید {request_number_for(purchase_request.id)} تایید شد و در لیست خرید «{officer_label}» قرار گرفت')
                else:
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
                action='updated',
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
