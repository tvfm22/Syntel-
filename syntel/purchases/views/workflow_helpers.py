from __future__ import annotations

from ..models import ActivityLog
from ..models import Notification
from ..models import OfficerAssignment
from ..models import Product
from ..models import PurchaseDraft
from ..models import PurchaseDraftItem
from ..models import PurchaseItem
from ..models import PurchaseRequest
from ..models import StockItem
from ..models import User
from .helpers import _item_section_name
from .helpers import _product_key
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_scoped_locked_object_or_404
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone


ASSIGNMENT_ACTIVE_STATUSES = ('pending', 'purchased', 'fulfilled')


ASSIGNMENT_RETURNED_STATUSES = ('returned', 'not_in_stock')


ASSIGNMENT_TERMINAL_STATUSES = ('purchased', 'fulfilled')


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
    assignments = (
        OfficerAssignment.objects
        .filter(item__request=request_obj)
        .exclude(status='reopened')
        .order_by('id')
    )
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
        .exclude(status__in=('pending', 'reopened'))
        .values_list('item__request_id', flat=True)
    )


def _pending_purchasing_assignments_qs(request, user, *, is_self_buy=False):
    """کوئری پایه اقلام در انتظار خرید؛ اقلام در انتظار تایید مدیرعامل/ردشده/پیش‌نویس هرگز نمایش داده نمی‌شوند."""
    qs = (
        OfficerAssignment.objects
        .filter(target_role='purchasing', status='pending')
        .exclude(item__request__status__in=('pending_manager_approval', 'rejected', 'draft'))
        .select_related(
            'item', 'item__request', 'item__request__user', 'item__request__user__department',
            'item__product', 'item__source_item', 'item__source_item__request',
            'item__source_item__request__user', 'item__source_item__request__user__department',
            'assigned_to',
        )
    )
    if is_self_buy:
        qs = qs.filter(assigned_to=user)
    else:
        qs = qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
    return filter_by_cafe(qs.order_by('assigned_at'), request)


def _apply_my_purchase_action(
    request: HttpRequest,
    assignment_pks: list[int],
    action: str,
    *,
    is_self_buy: bool,
    is_warehouse_self_buy: bool,
) -> None:
    """اعمال عملیات خریداری شد / خریداری نشده / بازگشت روی یک یا چند تخصیص در تراکنش اتمیک."""
    actor_name = request.user.get_full_name() or request.user.username
    processed_names = []
    error_msg = None

    with transaction.atomic():
        for pk in assignment_pks:
            assignment = get_scoped_locked_object_or_404(
                OfficerAssignment, request, id=pk, target_role='purchasing')

            if assignment.item.request.status in ('pending_manager_approval', 'rejected', 'draft'):
                error_msg = 'این قلم هنوز توسط مدیرعامل تایید نشده است و قابل پردازش نیست.'
                break
            if assignment.status != 'pending':
                continue
            if is_self_buy:
                if assignment.assigned_to_id != request.user.id:
                    error_msg = 'این قلم به شما تخصیص داده نشده است.'
                    break
            elif not (assignment.assigned_to_id == request.user.id
                      or assignment.assigned_to_id is None):
                error_msg = 'این قلم به شما تخصیص داده نشده است.'
                break

            now_ts = timezone.now()
            if assignment.assigned_to_id is None:
                assignment.assigned_to = request.user

            if action == 'purchased':
                assignment.status = 'purchased'
                assignment.notes = (assignment.notes or '') + f' خریداری شد توسط {actor_name}.'
                assignment.item.supply_status = 'purchased'
                assignment.item.purchased_quantity = assignment.item.quantity
                assignment.item.save(update_fields=['supply_status', 'purchased_quantity'])
            elif action == 'not_purchased':
                assignment.status = 'not_purchased'
                assignment.notes = (assignment.notes or '') + f' خریداری نشد توسط {actor_name}.'
                assignment.item.supply_status = 'not_supplied'
                assignment.item.purchased_quantity = Decimal('0')
                assignment.item.save(update_fields=['supply_status', 'purchased_quantity'])
            elif action == 'return':
                assignment.status = 'returned'
                return_target = 'انبار' if is_warehouse_self_buy else 'درخواست‌ها'
                assignment.notes = (assignment.notes or '') + f' قابل خرید نبود — بازگشت به {return_target} توسط {actor_name}.'
                assignment.item.supply_status = 'not_supplied'
                assignment.item.purchased_quantity = Decimal('0')
                assignment.item.save(update_fields=['supply_status', 'purchased_quantity'])
            else:
                continue

            assignment.completed_at = now_ts
            assignment.save(update_fields=['status', 'completed_at', 'assigned_to', 'notes'])
            copy_req = assignment.item.request
            if (
                action == 'return'
                and copy_req.source == 'warehouse_purchase'
                and not OfficerAssignment.objects.filter(
                    item__request=copy_req,
                    target_role='purchasing',
                    status__in=('pending', 'purchased'),
                ).exists()
            ):
                copy_req.status = 'rejected'
                copy_req.save(update_fields=['status'])
            else:
                _sync_request_status(copy_req)
            processed_names.append(assignment.item.name)

    if error_msg:
        messages.error(request, error_msg)
    elif not processed_names:
        messages.warning(request, 'این قلم قبلاً پردازش شده است.')
    else:
        label = processed_names[0] if len(processed_names) == 1 else f'{len(processed_names)} قلم ({processed_names[0]})'
        if action == 'purchased':
            messages.success(request, f'«{label}» خریداری شد.')
        elif action == 'not_purchased':
            messages.warning(request, f'«{label}» به عنوان خریداری‌نشده ثبت شد.')
        elif action == 'return':
            messages.info(request, f'«{label}» برگشت داده شد.')


def _handle_my_purchases_view(
    request: HttpRequest,
    *,
    is_self_buy: bool,
    is_warehouse_self_buy: bool = False,
    redirect_url_name: str = 'purchase_officer_dashboard',
    history_url_name: str = 'purchase_officer_history',
) -> HttpResponse:
    """منطق مشترک صفحه «لیست خرید من» مسئول خرید و «خریدهای من» (خودخریدی مدیرعامل/مسئول انبار)."""
    from collections import defaultdict

    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    if request.method == 'POST':
        action = request.POST.get('action', '')
        raw_ids = request.POST.get('item_ids') or request.POST.get('item_id', '')
        assignment_pks = [
            int(x) for x in str(raw_ids).split(',')
            if x.strip().isdigit()
        ]
        if not assignment_pks:
            messages.error(request, 'قلم درخواست یافت نشد')
            return redirect(redirect_url_name, cafe_code=cafe_code)

        _apply_my_purchase_action(
            request,
            assignment_pks,
            action,
            is_self_buy=is_self_buy,
            is_warehouse_self_buy=is_warehouse_self_buy,
        )
        return redirect(redirect_url_name, cafe_code=cafe_code)

    assignments = _pending_purchasing_assignments_qs(
        request, request.user, is_self_buy=is_self_buy)

    rows_map = defaultdict(lambda: {
        'name': '', 'unit': '', 'total_qty': 0,
        'sections': defaultdict(float),
        'assignments': [],
        'descriptions': [],
    })
    for a in assignments:
        item = a.item
        # کالاهای یکسان (حتی از بخش‌های مختلف یا با توضیحات متفاوت) در یک ردیف جمع می‌شوند
        key = (_product_key(item, include_description=False), (item.unit or '').strip())
        row = rows_map[key]
        row['name'] = item.product.name if item.product else item.name
        row['unit'] = item.unit
        row['total_qty'] += float(item.quantity or 0)
        origin_item = item.source_item if item.source_item_id else item
        dept = _item_section_name(origin_item.request.user)
        row['sections'][dept] += float(item.quantity or 0)
        row['assignments'].append(a)
        if item.description:
            row['descriptions'].append({'dept': dept, 'text': item.description})

    rows = []
    for _key, data in rows_map.items():
        rows.append({
            'name': data['name'],
            'unit': data['unit'],
            'total_qty': round(data['total_qty'], 2),
            'sections': [{'name': d, 'qty': round(q, 2)} for d, q in sorted(data['sections'].items())],
            'assignments': data['assignments'],
            'assignment_ids_csv': ','.join(str(a.id) for a in data['assignments']),
            'descriptions': data['descriptions'],
        })
    rows.sort(key=lambda r: (-len(r['sections']), -r['total_qty']))

    awaiting_ceo_count = PurchaseItem.objects.filter(
        request__cafe=cafe,
        request__status='pending_manager_approval',
        request__intended_purchasing_officer=request.user,
    ).count()

    return render(request, 'purchase_officer_dashboard.html', {
        'rows': rows,
        'total_items': sum(len(r['assignments']) for r in rows),
        'cafe_code': cafe_code,
        'user': request.user,
        'is_self_buy': is_self_buy,
        'is_warehouse_self_buy': is_warehouse_self_buy,
        'post_url_name': redirect_url_name,
        'history_url_name': history_url_name,
        'awaiting_ceo_count': awaiting_ceo_count,
    })


def _handle_my_purchases_history(
    request: HttpRequest,
    *,
    is_self_buy: bool,
    is_warehouse_self_buy: bool = False,
    list_url_name: str = 'purchase_officer_dashboard',
    history_url_name: str = 'purchase_officer_history',
) -> HttpResponse:
    """سوابق لیست خرید: اقلام خریداری‌شده یا خریداری‌نشده تفکیک‌شده بر اساس تاریخ."""
    from collections import OrderedDict

    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    status_filter = request.GET.get('status', 'all')
    if status_filter not in ('all', 'purchased', 'not_purchased', 'returned'):
        status_filter = 'all'
    q_filter = (request.GET.get('q') or '').strip()

    qs = (
        OfficerAssignment.objects
        .filter(target_role='purchasing', status__in=('purchased', 'not_purchased', 'returned'))
        .exclude(item__request__status__in=('pending_manager_approval', 'rejected', 'draft'))
        .select_related(
            'item', 'item__request', 'item__request__user', 'item__request__user__department',
            'item__product', 'item__source_item', 'item__source_item__request',
            'item__source_item__request__user', 'item__source_item__request__user__department',
            'assigned_to',
        )
    )
    if is_self_buy:
        qs = qs.filter(assigned_to=request.user)
    else:
        qs = qs.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))

    qs = filter_by_cafe(qs, request)
    if status_filter == 'not_purchased':
        qs = qs.filter(status__in=('not_purchased', 'returned'))
    elif status_filter != 'all':
        qs = qs.filter(status=status_filter)
    if q_filter:
        qs = qs.filter(
            Q(item__name__icontains=q_filter) | Q(item__product__name__icontains=q_filter)
        )

    assignments = sorted(
        qs,
        key=lambda a: a.completed_at or a.assigned_at,
        reverse=True,
    )[:300]

    grouped = OrderedDict()
    total_purchased = 0
    total_not_purchased = 0
    for a in assignments:
        dt = timezone.localtime(a.completed_at or a.assigned_at)
        d = dt.date()
        if d not in grouped:
            grouped[d] = {
                'date': d,
                'purchased_count': 0,
                'returned_count': 0,
                'items': [],
            }
        item = a.item
        origin_item = item.source_item if item.source_item_id else item
        dept = _item_section_name(origin_item.request.user)
        if a.status == 'purchased':
            grouped[d]['purchased_count'] += 1
            total_purchased += 1
            st_label = 'خریداری شد'
        else:
            grouped[d]['returned_count'] += 1
            total_not_purchased += 1
            st_label = 'خریداری نشده'
        grouped[d]['items'].append({
            'assignment': a,
            'item': item,
            'name': item.product.name if item.product else item.name,
            'unit': item.unit,
            'quantity': item.quantity,
            'description': item.description,
            'dept': dept,
            'request_number': item.request.request_number,
            'status': 'not_purchased' if a.status in ('not_purchased', 'returned') else a.status,
            'status_label': st_label,
            'time_str': dt.strftime('%H:%M'),
            'notes': a.notes,
        })

    return render(request, 'purchase_officer_history.html', {
        'date_groups': list(grouped.values()),
        'total_count': len(assignments),
        'total_purchased': total_purchased,
        'total_returned': total_not_purchased,
        'status_filter': status_filter,
        'q_filter': q_filter,
        'cafe_code': cafe_code,
        'user': request.user,
        'is_self_buy': is_self_buy,
        'is_warehouse_self_buy': is_warehouse_self_buy,
        'list_url_name': list_url_name,
        'history_url_name': history_url_name,
    })


def _resolve_purchasing_target_officer(cafe, request_user, officer_user_id_raw):
    """تشخیص مسئول خرید مقصد یا گزینه «خرید توسط خودم» برای ارسال از انبار."""
    raw = str(officer_user_id_raw or '').strip()
    if not raw:
        return None, False, None
    if raw == 'self':
        return request_user, True, None
    if not raw.isdigit():
        return None, False, 'مسئول خرید انتخاب‌شده معتبر نیست.'
    officer = _officer_users(cafe, 'purchasing').filter(id=int(raw)).first()
    if officer is None:
        return None, False, 'مسئول خرید انتخاب‌شده در این مجموعه یافت نشد.'
    return officer, False, None


def _dispatch_warehouse_purchase_items(
    *,
    cafe,
    request_user,
    entries,
    target_officer=None,
    is_self_buy=False,
):
    """تفکیک اقلام انبار به صف تایید مدیرعامل و ارسال مستقیم به مسئول خرید / خودخریدی.

    اقلام نیازمند تایید مدیرعامل با وضعیت pending_manager_approval ثبت می‌شوند و
    تا قبل از تایید مدیرعامل هیچ تخصیص OfficerAssignment(target_role='purchasing')
    برایشان ساخته نمی‌شود تا در لیست خرید مسئول خرید یا «خریدهای من» دیده نشوند.
    """
    from ..services.purchase_approval import evaluate_draft_items_ceo_status

    eval_map = evaluate_draft_items_ceo_status(entries, cafe=cafe)
    ceo_entries = [e for e in entries if eval_map[e.id]['needs_ceo']]
    direct_entries = [e for e in entries if not eval_map[e.id]['needs_ceo']]

    ceo_req = None
    direct_req = None

    with transaction.atomic():
        if ceo_entries:
            ceo_req = PurchaseRequest.objects.create(
                user=request_user,
                cafe=cafe,
                status='pending_manager_approval',
                source='warehouse_purchase',
                intended_purchasing_officer=target_officer,
            )
            for entry in ceo_entries:
                src_item = entry if isinstance(entry, PurchaseItem) else entry.source_item
                PurchaseItem.objects.create(
                    request=ceo_req,
                    product=entry.product,
                    name=entry.name,
                    quantity=entry.quantity,
                    unit=entry.unit,
                    description=(entry.description or ''),
                    source_item=src_item,
                )
            reasons_summary = '; '.join(
                f"{e.name}: {'، '.join(eval_map[e.id]['reasons'])}"
                for e in ceo_entries
            )[:350]
            target_desc = ''
            if is_self_buy:
                target_desc = ' (خرید توسط خودم پس از تایید مدیرعامل)'
            elif target_officer:
                target_desc = f' (مسئول خرید: {target_officer.get_full_name() or target_officer.username})'
            ActivityLog.objects.create(
                user=request_user,
                request=ceo_req,
                action='created',
                description=(
                    f'درخواست خرید توسط مسئول انبار ارسال شد و در انتظار تایید مدیرعامل است{target_desc} '
                    f'({len(ceo_entries)} قلم — {reasons_summary})'
                ),
            )

        if direct_entries:
            direct_req = PurchaseRequest.objects.create(
                user=request_user,
                cafe=cafe,
                status='pending',
                source='warehouse_purchase',
                intended_purchasing_officer=target_officer,
            )
            for entry in direct_entries:
                src_item = entry if isinstance(entry, PurchaseItem) else entry.source_item
                created_item = PurchaseItem.objects.create(
                    request=direct_req,
                    product=entry.product,
                    name=entry.name,
                    quantity=entry.quantity,
                    unit=entry.unit,
                    description=(entry.description or ''),
                    source_item=src_item,
                )
                _assign_item(
                    created_item,
                    'purchasing',
                    target_officer,
                    request_user,
                    status='pending',
                )
                if target_officer is not None:
                    if not is_self_buy:
                        _notify_officer(target_officer, created_item, 'purchasing', cafe)
                else:
                    for officer in _officer_users(cafe, 'purchasing'):
                        _notify_officer(officer, created_item, 'purchasing', cafe)
            if is_self_buy:
                target_label = 'به «خریدهای من» اضافه شد'
            elif target_officer is not None:
                officer_name = target_officer.get_full_name() or target_officer.username
                target_label = f'برای «{officer_name}» ارسال شد'
            else:
                target_label = 'مستقیم به واحد خرید ارسال شد'
            ActivityLog.objects.create(
                user=request_user,
                request=direct_req,
                action='created',
                description=(
                    f'درخواست خرید توسط مسئول انبار بدون نیاز به تایید مدیرعامل '
                    f'{target_label} ({len(direct_entries)} قلم)'
                ),
            )

    return ceo_req, direct_req, ceo_entries, direct_entries


def _flash_warehouse_dispatch_result(
    request, *, ceo_req, direct_req, ceo_entries, direct_entries, target_officer, is_self_buy
):
    """پیام بازخورد متناسب با نتیجه تفکیک و ارسال اقلام خرید انبار."""
    if is_self_buy:
        target_name = '«خریدهای من»'
    elif target_officer is not None:
        officer_name = target_officer.get_full_name() or target_officer.username
        target_name = f'لیست خرید «{officer_name}»'
    else:
        target_name = 'واحد خرید'

    if ceo_req and direct_req:
        messages.success(
            request,
            f'تفکیک خودکار انجام شد: {len(direct_entries)} قلم مستقیم به {target_name} '
            f'(درخواست {direct_req.request_number}) و {len(ceo_entries)} قلم برای تایید مدیرعامل '
            f'(درخواست {ceo_req.request_number} — تا قبل از تایید در لیست خرید نمایش داده نمی‌شود) ارسال شد.',
        )
    elif direct_req:
        messages.success(
            request,
            f'درخواست خرید {direct_req.request_number} با {len(direct_entries)} قلم '
            f'مستقیم به {target_name} ارسال شد (بدون نیاز به تایید مدیرعامل).',
        )
    elif ceo_req:
        messages.success(
            request,
            f'درخواست خرید {ceo_req.request_number} با {len(ceo_entries)} قلم '
            f'برای تایید مدیرعامل ارسال شد؛ تا قبل از تایید در لیست خرید قرار نمی‌گیرد.',
        )


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
    می‌روند. تا وقتی آن درخواست رد نشده باشد و قلم کپی توسط خرید برگشت داده نشده باشد،
    قلمِ مبدأ در داشبورد انبار قابل تغییر وضعیت نیست.
    """
    copies = (
        item.purchase_copies
        .filter(request__source='warehouse_purchase')
        .exclude(request__status='rejected')
        .select_related('request')
        .prefetch_related('officer_assignments')
        .order_by('-id')
    )
    for copy_item in copies:
        p_assigns = [a for a in copy_item.officer_assignments.all() if a.target_role == 'purchasing']
        if p_assigns and all(a.status in ('returned', 'not_purchased') for a in p_assigns):
            continue
        return copy_item
    return None


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


def _resolve_draft_product(cafe, product_id, name):
    """بازیابی محصول از شناسه یا نام در محدودهٔ کافهٔ فعلی (هرگز محصول کافهٔ دیگر برنمی‌گرداند)."""
    item_product = None
    pid_str = str(product_id or '').strip()
    if pid_str.isdigit():
        qs = Product.objects.filter(id=int(pid_str), is_active=True)
        if cafe is not None:
            qs = qs.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
        item_product = qs.first()
    if item_product is None and name:
        qs_name = Product.objects.filter(name__iexact=name)
        if cafe is not None:
            qs_name = qs_name.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
        item_product = qs_name.first()
    return item_product
