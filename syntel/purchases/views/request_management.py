from __future__ import annotations

from ..models import Department
from ..models import OfficerAssignment
from ..models import PurchaseDraftItem
from ..models import PurchaseItem
from ..models import PurchaseRequest
from ..models import User
from ..models import WarehouseRequest
from .helpers import _item_section_name
from .helpers import _parse_decimal
from .helpers import _product_key
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import role_required
from .workflow_helpers import ASSIGNMENT_ACTIVE_STATUSES
from .workflow_helpers import ASSIGNMENT_RETURNED_STATUSES
from .workflow_helpers import _assign_item
from .workflow_helpers import _item_sent_to_ceo
from .workflow_helpers import _notify_officer
from .workflow_helpers import _officer_role_label
from .workflow_helpers import _officer_users
from .workflow_helpers import _remove_from_purchase_drafts
from .workflow_helpers import _sync_request_status
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from urllib.parse import urlencode
import csv
import jdatetime
import os


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
        can_mutate = (
            request.user.is_superuser
            or request.user.role in ('manager', 'ceo', 'admin')
        )
        if not can_mutate:
            return HttpResponseForbidden('فقط مدیر یا مدیرعامل مجاز به انجام این عملیات است.')
        action = request.POST.get('action', '')
        if request_type == 'purchase':
            with transaction.atomic():
                if action == 'send':
                    item_ids = [i for i in request.POST.get('item_ids', '').split(',') if i.strip().isdigit()]
                    officer_id = request.POST.get('officer_user_id', '').strip()
                    officer = None
                    if officer_id.isdigit():
                        officer_qs = User.objects.filter(id=int(officer_id), is_active=True)
                        if cafe:
                            officer_qs = officer_qs.filter(
                                Q(cafe=cafe) | Q(cafe__isnull=True, role__in=User.CAFE_SELECTION_ROLES)
                            )
                        officer = officer_qs.first()
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
                        items = PurchaseItem.objects.select_for_update().filter(id__in=item_ids).order_by('id')
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
                        item = PurchaseItem.objects.select_for_update().filter(id=int(item_id)).first()
                        if item and (not cafe or item.request.cafe_id == cafe.id):
                            request_obj = item.request
                            item_name = item.name or item.product.name if item.product else (item.name or '')
                            _remove_from_purchase_drafts(item)
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
                    items = list(PurchaseItem.objects.select_for_update().filter(id__in=ids).order_by('id')) if ids else []
                    if cafe:
                        items = [it for it in items if it.request.cafe_id == cafe.id]
                    if new_qty is None or not new_qty.is_finite() or new_qty <= 0 or new_qty > 99999999:
                        messages.error(request, 'مقدار جدید نامعتبر است (باید عدد مثبت باشد)')
                    elif not items:
                        messages.error(request, 'قلمی برای ویرایش پیدا نشد')
                    else:
                        unsent = [
                            it for it in items
                            if not it.officer_assignments.filter(status__in=ASSIGNMENT_ACTIVE_STATUSES).exists()
                            and _item_sent_to_ceo(it) is None
                        ]
                        if not unsent:
                            messages.error(request, 'همه قلم‌های این بخش قبلاً ارسال شده‌اند و قابل ویرایش نیستند')
                        else:
                            name = unsent[0].name or (unsent[0].product.name if unsent[0].product else '')
                            target = unsent[0]
                            target.quantity = new_qty
                            target.save()
                            PurchaseDraftItem.objects.filter(source_item=target).update(quantity=new_qty)
                            affected = {target.request_id}
                            for it in unsent[1:]:
                                affected.add(it.request_id)
                                _remove_from_purchase_drafts(it)
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
                    items = list(PurchaseItem.objects.select_for_update().filter(id__in=ids).order_by('id')) if ids else []
                    if cafe:
                        items = [it for it in items if it.request.cafe_id == cafe.id]
                    if not items:
                        messages.error(request, 'قلمی برای حذف پیدا نشد')
                    else:
                        name = items[0].name or (items[0].product.name if items[0].product else '')
                        unsent = [
                            it for it in items
                            if not it.officer_assignments.filter(status__in=ASSIGNMENT_ACTIVE_STATUSES).exists()
                            and _item_sent_to_ceo(it) is None
                        ]
                        sent_skipped = len(items) - len(unsent)
                        affected = {it.request_id for it in unsent}
                        for it in unsent:
                            _remove_from_purchase_drafts(it)
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
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()
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
        'total_qty': Decimal('0'),
        'unit': '',
        'departments': defaultdict(lambda: Decimal('0')),
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

            qty = Decimal(str(item.quantity)) if item.quantity else Decimal('0')

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
            'name': '', 'unit': '', 'total_qty': Decimal('0'),
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
                qty = Decimal(str(item.quantity or 0))
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
                    'qty': round(sum((e['qty'] for e in entries), Decimal('0')), 2),
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
                qty = sum((s['qty'] for s in secs), Decimal('0'))
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
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()
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
            'name': '', 'unit': '', 'requested_qty': Decimal('0'), 'supplied_qty': Decimal('0'),
            'section': '', 'requester': '', 'description': '',
        })
        for a in wh_assignments:
            item = a.item
            req = item.request
            requester = req.user if req.user else None
            row = wh_map[item.id]
            row['name'] = item.product.name if item.product else item.name
            row['unit'] = item.unit or ''
            row['requested_qty'] = Decimal(str(item.quantity or 0))
            row['supplied_qty'] += Decimal(str(a.supplied_quantity or 0))
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
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

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
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

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
        'name': '', 'unit': '', 'requested_qty': Decimal('0'), 'supplied_qty': Decimal('0'),
        'section': '', 'description': '',
    })
    for a in wh_assignments:
        item = a.item
        req = item.request
        requester = req.user if req.user else None
        row = wh_map[item.id]
        row['name'] = item.product.name if item.product else item.name
        row['unit'] = item.unit or ''
        row['requested_qty'] = Decimal(str(item.quantity or 0))
        row['supplied_qty'] += Decimal(str(a.supplied_quantity or 0))
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
