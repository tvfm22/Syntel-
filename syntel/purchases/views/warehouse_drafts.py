from __future__ import annotations

from ..models import Category
from ..models import Department
from ..models import PurchaseDraft
from ..models import PurchaseDraftItem
from ..models import PurchaseItem
from ..models import PurchaseRequest
from ..models import StockItem
from ..models import Warehouse
from ..numbering import request_number as request_number_for
from ..services.inventory import InsufficientStockError
from ..services.inventory import InventoryServiceError
from ..services.inventory import ReopenNotAllowedError
from ..services.inventory import StockScopeError
from ..services.inventory import apply_stock_delta
from ..services.inventory import reopen_officer_assignments
from ..services.recipe_gate import RD_CATEGORY_NAME
from ..services.recipe_gate import inactive_product_error
from .helpers import _fmt2
from .helpers import _item_section_name
from .helpers import _parse_decimal
from .helpers import _product_key
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_scoped_locked_object_or_404
from .helpers import role_required
from .workflow_helpers import _assign_item
from .workflow_helpers import _dispatch_warehouse_purchase_items
from .workflow_helpers import _flash_warehouse_dispatch_result
from .workflow_helpers import _get_or_create_purchase_draft
from .workflow_helpers import _item_in_stock
from .workflow_helpers import _item_sent_to_ceo
from .workflow_helpers import _officer_users
from .workflow_helpers import _pending_purchasing_assignments_qs
from .workflow_helpers import _remove_from_purchase_drafts
from .workflow_helpers import _resolve_draft_product
from .workflow_helpers import _resolve_purchasing_target_officer
from .workflow_helpers import _sync_request_status
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from urllib.parse import urlencode


@custom_login_required
@role_required(['purchasing', 'warehouse'])
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

        with transaction.atomic():
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
                has_error = False
                for sid, qty_str in all_rows:
                    if not sid:
                        continue
                    qty = _parse_decimal(qty_str)
                    if qty is None or not qty.is_finite() or qty <= 0:
                        messages.error(request, f'برای «{item.name}» مقدار معتبر مثبت وارد کنید')
                        has_error = True
                        break
                    if sid not in merged:
                        merged[sid] = {'qty': qty, 'order': len(order)}
                        order.append(sid)
                    else:
                        merged[sid]['qty'] = merged[sid]['qty'] + qty

                if has_error:
                    return redirect(dash_url)

                validated = []  # (stock_item, qty)
                for sid in order:
                    qty = merged[sid]['qty']
                    # موجودی فقط در محدودهٔ کافهٔ فعلی — نه با id خام از POST
                    sid_str = str(sid)
                    if sid_str.startswith('wh:') and sid_str[3:].isdigit():
                        wh = Warehouse.objects.filter(
                            id=int(sid_str[3:]),
                            cafe=cafe,
                            is_active=True,
                            allow_negative_stock=True,
                        ).first()
                        if wh is not None and item.product_id:
                            stock_item, _ = StockItem.objects.get_or_create(
                                product=item.product,
                                warehouse=wh,
                                defaults={
                                    'quantity': Decimal('0'),
                                    'unit': item.product.unit,
                                },
                            )
                        else:
                            stock_item = None
                    elif not sid_str.isdigit():
                        stock_item = None
                    else:
                        stock_item = StockItem.objects.select_related('warehouse').filter(
                            id=int(sid_str), warehouse__cafe=cafe).first()

                    if stock_item is None or qty is None or qty <= 0:
                        messages.error(request, f'برای «{item.name}» موجودی انبار و مقدار معتبر انتخاب کنید')
                        has_error = True
                        break
                    if stock_item.product_id != item.product_id:
                        messages.error(request, f'موجودی انبار با کالای «{item.name}» مطابقت ندارد')
                        has_error = True
                        break
                    base_deduction = qty
                    if not (stock_item.warehouse and stock_item.warehouse.allow_negative_stock) and base_deduction > stock_item.quantity:
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
                            elif assignment.assigned_to_id is None:
                                assignment.assigned_to = request.user
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
                    transaction.set_rollback(True)
                    messages.error(request, f'موجودی «{exc}» در این لحظه کافی نیست؛ ثبت نشد')
                    return redirect(dash_url)
                item.supply_status = 'from_stock'
                item.purchased_quantity = total_trade
                item.save(update_fields=['supply_status', 'purchased_quantity'])
                _remove_from_purchase_drafts(item)
                _sync_request_status(item.request)
                messages.success(request, f'«{item.name}» از انبار تامین شد.')
                return redirect(dash_url)

            elif action in ('to_purchase', 'not_in_stock'):
                if assignment is None:
                    assignment = _assign_item(item, 'warehouse', request.user, request.user)
                elif assignment.assigned_to_id is None:
                    assignment.assigned_to = request.user
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
                    item.supply_status = 'not_supplied'
                    item.purchased_quantity = Decimal('0')
                    item.save(update_fields=['supply_status', 'purchased_quantity'])
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
                    transaction.set_rollback(True)
                    messages.error(request, f'بازگشایی ناموفق بود: {exc}')
                    return redirect(dash_url)
                item.supply_status = 'pending'
                item.purchased_quantity = None
                item.save(update_fields=['supply_status', 'purchased_quantity'])
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
            Q(quantity__gt=0) | Q(warehouse__allow_negative_stock=True),
            product_id__in=scope_pids,
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
                                 'locked_status': sent_copy.request.status if sent_copy else None,
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
            Q(quantity__gt=0) | Q(warehouse__allow_negative_stock=True, warehouse__is_active=True),
            product_id__in=product_ids,
            warehouse__cafe=cafe,
        ).select_related('warehouse', 'unit').order_by('warehouse__name')
        seen_wh_by_product = {}
        for si in sis:
            unit_label = (si.unit.name or si.unit.symbol) if si.unit else ''
            seen_wh_by_product.setdefault(si.product_id, set()).add(si.warehouse_id)
            stock_options.setdefault(si.product_id, []).append({
                'id': si.id,
                'warehouse_name': si.warehouse.name if si.warehouse else '',
                'default_selected': False,
                'available': _fmt2(si.quantity),
                'unit_label': unit_label,
                'qty': _fmt2(si.quantity),
            })

        # اگر انباری موجودی منفی مجاز دارد و هنوز ردیف StockItem برای این محصول ندارد،
        # گزینهٔ مجازی wh:<id> اضافه می‌شود تا بدون ورود اولیه هم قابل تامین باشد.
        neg_warehouses = list(
            Warehouse.objects.filter(
                cafe=cafe, is_active=True, allow_negative_stock=True
            ).order_by('name')
        )
        if neg_warehouses:
            product_unit_map = {}
            for card in request_cards:
                for v in card['visible']:
                    it = v['item']
                    if it.product_id and it.product_id not in product_unit_map:
                        u = it.product.unit if it.product else None
                        product_unit_map[it.product_id] = (
                            (u.name or u.symbol) if u else (it.unit or '')
                        )
            for pid in product_ids:
                seen_whs = seen_wh_by_product.get(pid, set())
                u_label = product_unit_map.get(pid, '')
                for wh in neg_warehouses:
                    if wh.id in seen_whs:
                        continue
                    stock_options.setdefault(pid, []).append({
                        'id': f'wh:{wh.id}',
                        'warehouse_name': wh.name,
                        'default_selected': False,
                        'available': '0',
                        'unit_label': u_label,
                        'qty': '0',
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

    active_draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
    draft_count = active_draft.items.count() if active_draft else 0
    my_purchases_count = _pending_purchasing_assignments_qs(
        request, request.user, is_self_buy=True
    ).count()

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
        'draft_count': draft_count,
        'my_purchases_count': my_purchases_count,
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


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def warehouse_purchase_draft(request: HttpRequest) -> HttpResponse:
    """صفحه «درخواست خرید» مسئول انبار:

    - کالاهایی که در داشبورد انبار «ارجاع به خرید» شده‌اند، اول در این صفحه
      (پیش‌نویس) قرار می‌گیرند و قابل ویرایش/حذف/افزودن هستند.
    - هنگام «ارسال»، فقط اقلام نیازمند تایید (ارزش بالای ۴۰۰ میلیون ریال،
      مقدار بیش از ۳ برابر میانگین، تعریف‌نشده در لیست، یا کالای جدید کاربر)
      برای تایید مدیرعامل می‌روند و بقیهٔ اقلام مستقیم برای واحد خرید ثبت می‌شوند.
    - درخواست‌های در انتظار تایید مدیرعامل تا قبل از تایید در همین صفحه قابل
      ویرایش و حذف هستند.
    """
    from ..services.purchase_approval import evaluate_draft_items_ceo_status

    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    if not (request.user.role == 'admin' or request.user.is_superuser or request.user.has_warehouse_role):
        return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

    # ── پردازش فرم‌ها ──
    if request.method == 'POST':
        action = request.POST.get('action', '')

        def _post_int(key):
            raw = (request.POST.get(key) or '').strip()
            return int(raw) if raw.isdigit() else None

        if action == 'send':
            with transaction.atomic():
                draft = PurchaseDraft.objects.select_for_update().filter(
                    cafe=cafe, created_by=request.user
                ).first()
                draft_items = list(
                    draft.items.select_related('product', 'product__unit', 'product__category', 'source_item')
                ) if draft else []
                if not draft_items:
                    messages.error(request, 'پیش‌نویس خالی است — چیزی برای ارسال نیست')
                    return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

                has_selection_ui = request.POST.get('has_selection_ui') == '1'
                if has_selection_ui or 'selected_item_ids' in request.POST:
                    selected_ids = {
                        int(x) for x in request.POST.getlist('selected_item_ids')
                        if str(x).strip().isdigit()
                    }
                    draft_items = [d for d in draft_items if d.id in selected_ids]
                    if not draft_items:
                        messages.error(request, 'لطفاً حداقل یک قلم را برای ارسال انتخاب کنید')
                        return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

                officer_raw = (request.POST.get('officer_user_id') or '').strip()
                if has_selection_ui and not officer_raw:
                    messages.error(request, 'لطفاً مسئول خرید یا «خرید توسط خودم» را انتخاب کنید')
                    return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

                target_officer, is_self_buy, err_msg = _resolve_purchasing_target_officer(
                    cafe, request.user, officer_raw)
                if err_msg:
                    messages.error(request, err_msg)
                    return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

                ceo_req, direct_req, ceo_items, direct_items = _dispatch_warehouse_purchase_items(
                    cafe=cafe,
                    request_user=request.user,
                    entries=draft_items,
                    target_officer=target_officer,
                    is_self_buy=is_self_buy,
                )
                sent_ids = [d.id for d in draft_items]
                draft.items.filter(id__in=sent_ids).delete()
                if not draft.items.exists():
                    draft.delete()

            _flash_warehouse_dispatch_result(
                request,
                ceo_req=ceo_req,
                direct_req=direct_req,
                ceo_entries=ceo_items,
                direct_entries=direct_items,
                target_officer=target_officer,
                is_self_buy=is_self_buy,
            )
            if is_self_buy:
                return redirect('warehouse_my_purchases', cafe_code=cafe_code)
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'add_item':
            draft = _get_or_create_purchase_draft(cafe, request.user)
            name = (request.POST.get('name', '') or request.POST.get('new_name', '') or '').strip()
            product_id = request.POST.get('product_id', '')
            qty = _parse_decimal(request.POST.get('quantity', '') or request.POST.get('new_qty', ''))
            unit = (request.POST.get('unit', '') or '').strip()
            description = (request.POST.get('description', '') or '').strip()

            item_product = _resolve_draft_product(cafe, product_id, name)

            if item_product is None and not name:
                messages.error(request, 'نام کالا را وارد کنید')
            elif inactive_product_error(item_product):
                messages.error(request, inactive_product_error(item_product))
            elif qty is None or qty <= 0 or qty > 99999999:
                messages.error(request, 'مقدار باید عدد مثبت باشد')
            else:
                resolved_unit = unit or (item_product.unit.name if item_product and item_product.unit else 'عدد')
                item_label = item_product.name if item_product else name
                existing_manual = None
                if item_product is not None:
                    existing_manual = PurchaseDraftItem.objects.filter(
                        draft=draft,
                        source_item__isnull=True,
                        product=item_product,
                        unit=resolved_unit,
                    ).first()
                else:
                    existing_manual = PurchaseDraftItem.objects.filter(
                        draft=draft,
                        source_item__isnull=True,
                        product__isnull=True,
                        name__iexact=item_label,
                        unit=resolved_unit,
                    ).first()

                if existing_manual is not None:
                    existing_manual.quantity = (existing_manual.quantity or Decimal('0')) + qty
                    if description and description not in (existing_manual.description or ''):
                        existing_manual.description = (
                            f"{existing_manual.description} | {description}".strip(' |')
                            if existing_manual.description else description
                        )
                    existing_manual.save(update_fields=['quantity', 'description'])
                    messages.success(
                        request,
                        f'«{existing_manual.name}» با مقدار قبلی جمع شد (جمع کل: {existing_manual.quantity.normalize()} {resolved_unit})',
                    )
                else:
                    PurchaseDraftItem.objects.create(
                        draft=draft,
                        product=item_product,
                        name=item_label,
                        quantity=qty,
                        unit=resolved_unit,
                        description=description,
                    )
                    messages.success(request, f'«{item_label}» به پیش‌نویس اضافه شد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'edit_item':
            item_pk = _post_int('item_id')
            d_item = PurchaseDraftItem.objects.filter(
                id=item_pk, draft__cafe=cafe,
                draft__created_by=request.user).select_related('draft').first() if item_pk else None
            qty = _parse_decimal(request.POST.get('quantity', ''))
            if d_item is None:
                messages.error(request, 'قلم پیش‌نویس یافت نشد')
            elif qty is None or qty <= 0 or qty > 99999999:
                messages.error(request, 'مقدار باید عدد مثبت باشد')
            else:
                has_name_field = any(k in request.POST for k in ('product_id', 'name', 'new_name'))
                if has_name_field:
                    raw_name = (request.POST.get('name', '') or request.POST.get('new_name', '') or '').strip()
                    raw_pid = request.POST.get('product_id', '')
                    new_product = _resolve_draft_product(cafe, raw_pid, raw_name)
                    if new_product is None and not raw_name:
                        messages.error(request, 'نام کالا را وارد کنید')
                        return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
                    if inactive_product_error(new_product):
                        messages.error(request, inactive_product_error(new_product))
                        return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
                    d_item.product = new_product
                    d_item.name = new_product.name if new_product else raw_name

                new_unit = (request.POST.get('unit', '') or '').strip()
                if new_unit:
                    d_item.unit = new_unit
                elif has_name_field and d_item.product and d_item.product.unit:
                    d_item.unit = d_item.product.unit.name

                d_item.quantity = qty
                new_desc = request.POST.get('description')
                if new_desc is not None:
                    d_item.description = new_desc.strip()
                d_item.save()
                messages.success(request, f'«{d_item.name}» ویرایش شد')
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'delete_item':
            item_pk = _post_int('item_id')
            if not item_pk:
                messages.error(request, 'قلم پیش‌نویس یافت نشد')
                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
            with transaction.atomic():
                d_item = PurchaseDraftItem.objects.select_for_update().filter(
                    id=item_pk, draft__cafe=cafe,
                    draft__created_by=request.user).first()
                if not d_item:
                    messages.error(request, 'قلم پیش‌نویس یافت نشد')
                    return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
                name = d_item.name
                source_item = d_item.source_item
                if source_item:
                    try:
                        reopen_officer_assignments(
                            source_item, cafe=cafe, reopened_by=request.user,
                            reason='حذف از لیست خرید و بازگشت به لیست انبار')
                        _sync_request_status(source_item.request)
                    except (ReopenNotAllowedError, StockScopeError,
                            InsufficientStockError, InventoryServiceError) as exc:
                        transaction.set_rollback(True)
                        messages.error(request, f'بازگشت به لیست انبار ناموفق بود: {exc}')
                        return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
                d_item.delete()
                draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
                if draft and not draft.items.exists():
                    draft.delete()
            msg = f'«{name}» از پیش‌نویس حذف شد'
            if source_item:
                msg += ' و به لیست انبار بازگشت'
            messages.info(request, msg)
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action == 'discard_draft':
            with transaction.atomic():
                draft = PurchaseDraft.objects.select_for_update().filter(
                    cafe=cafe, created_by=request.user
                ).first()
                if draft:
                    has_source = False
                    for di in list(draft.items.filter(source_item__isnull=False)):
                        has_source = True
                        try:
                            reopen_officer_assignments(
                                di.source_item, cafe=cafe, reopened_by=request.user,
                                reason='حذف از لیست خرید و بازگشت به لیست انبار')
                            _sync_request_status(di.source_item.request)
                        except (ReopenNotAllowedError, StockScopeError,
                                InsufficientStockError, InventoryServiceError) as exc:
                            transaction.set_rollback(True)
                            messages.error(request, f'بازگشت اقلام به لیست انبار ناموفق بود: {exc}')
                            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
                    draft.delete()
                    msg = 'پیش‌نویس حذف شد'
                    if has_source:
                        msg += ' و اقلام ارجاعی به لیست انبار بازگشتند'
                    messages.info(request, msg)
            return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

        elif action in ('edit_sent', 'delete_sent'):
            # ویرایش/حذف اقلام درخواست‌های ارسال‌شده فقط تا قبل از تایید مدیرعامل.
            req_pk = _post_int('request_id')
            item_pk = _post_int('item_id')
            if not req_pk or not item_pk:
                messages.error(request, 'درخواست یا قلم نامعتبر است')
                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
            with transaction.atomic():
                req = PurchaseRequest.objects.select_for_update().filter(
                    id=req_pk,
                    source='warehouse_purchase',
                    status='pending_manager_approval',
                ).select_related('user').first()
                if req is None or (cafe and req.cafe_id != cafe.id):
                    messages.error(request, 'درخواست یافت نشد یا دیگر قابل ویرایش نیست (پس از تایید مدیرعامل)')
                    return redirect('warehouse_purchase_draft', cafe_code=cafe_code)

                if action == 'edit_sent':
                    item = req.items.select_for_update().filter(id=item_pk).first()
                    qty = _parse_decimal(request.POST.get('quantity', ''))
                    if item is None:
                        messages.error(request, 'قلم یافت نشد')
                    elif qty is None or qty <= 0 or qty > 99999999:
                        messages.error(request, 'مقدار باید عدد مثبت باشد')
                    else:
                        has_name_field = any(k in request.POST for k in ('product_id', 'name', 'new_name'))
                        if has_name_field:
                            raw_name = (request.POST.get('name', '') or request.POST.get('new_name', '') or '').strip()
                            raw_pid = request.POST.get('product_id', '')
                            new_product = _resolve_draft_product(cafe, raw_pid, raw_name)
                            if new_product is None and not raw_name:
                                messages.error(request, 'نام کالا را وارد کنید')
                                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
                            if inactive_product_error(new_product):
                                messages.error(request, inactive_product_error(new_product))
                                return redirect('warehouse_purchase_draft', cafe_code=cafe_code)
                            item.product = new_product
                            item.name = new_product.name if new_product else raw_name

                        new_unit = (request.POST.get('unit', '') or '').strip()
                        if new_unit:
                            item.unit = new_unit
                        elif has_name_field and item.product and item.product.unit:
                            item.unit = item.product.unit.name

                        item.quantity = qty
                        desc = request.POST.get('description')
                        if desc is not None:
                            item.description = desc.strip()
                        item.save()
                        messages.success(request, f'«{item.product.name if item.product else item.name}» ویرایش شد')
                else:  # delete_sent
                    item = req.items.select_for_update().filter(id=item_pk).first()
                    if item is None:
                        messages.error(request, 'قلم یافت نشد')
                    else:
                        name = item.product.name if item.product else item.name
                        item.delete()
                        if not req.items.exists():
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
        'product', 'product__unit', 'product__category', 'source_item__request') if draft else [])
    draft_eval_map = evaluate_draft_items_ceo_status(draft_items, cafe=cafe)
    ceo_draft_count = sum(1 for d in draft_items if draft_eval_map.get(d.id, {}).get('needs_ceo'))
    direct_draft_count = len(draft_items) - ceo_draft_count

    # ── درخواست‌های ارسال‌شده ──
    # درخواست‌های نیازمند تایید به مدیرعامل می‌روند (pending_manager_approval) و
    # درخواست‌های عادی مستقیم به واحد خرید (pending) ارسال می‌شوند.
    sent_cutoff = timezone.now() - timedelta(days=7)
    sent_base = (PurchaseRequest.objects
                 .filter(source='warehouse_purchase', created_at__gte=sent_cutoff)
                 .select_related('user', 'manager_confirmed_by')
                 .prefetch_related('items', 'items__product', 'items__product__unit'))
    if cafe:
        sent_base = sent_base.filter(cafe=cafe)
    sent_requests = list(sent_base.filter(status='pending_manager_approval').order_by('-created_at')[:30])
    # تاییدشده توسط مدیرعامل یا ارسال مستقیم — در انتظار ثبت سفارش خرید در واحد خرید (فقط نمایش)
    approved_sent_requests = list(sent_base.filter(status='pending').order_by('-created_at')[:15])
    # ردشده توسط مدیرعامل (فقط نمایش)
    rejected_sent_requests = list(sent_base.filter(status='rejected').order_by('-created_at')[:10])

    # دسته‌بندی‌های فعال برای مرور کالاها در باکس افزودن کالا (هماهنگ با فرم درخواست کالا)
    all_categories = (
        Category.objects.filter(is_active=True)
        .exclude(name=RD_CATEGORY_NAME)
        .select_related('parent')
        .order_by('name')
    )
    if cafe:
        all_categories = all_categories.filter(cafe=cafe)

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

    draft_item_sections = {}
    from collections import defaultdict
    merged_draft_map = defaultdict(lambda: {
        'name': '', 'unit': '', 'total_qty': Decimal('0'),
        'sections': defaultdict(lambda: Decimal('0')),
        'item_count': 0,
    })
    for d in draft_items:
        dept = _item_section_name(d.source_item.request.user) if d.source_item_id else 'انبار'
        draft_item_sections[d.id] = dept
        m_key = (_product_key(d, include_description=False), (d.unit or '').strip())
        m = merged_draft_map[m_key]
        m['name'] = d.product.name if d.product else d.name
        m['unit'] = d.unit or 'عدد'
        q_dec = Decimal(str(d.quantity or 0))
        m['total_qty'] += q_dec
        m['sections'][dept] += q_dec
        m['item_count'] += 1

    draft_grouped_summary = []
    for _k, data in merged_draft_map.items():
        draft_grouped_summary.append({
            'name': data['name'],
            'unit': data['unit'],
            'total_qty': data['total_qty'],
            'item_count': data['item_count'],
            'sections': [
                {'name': sec_name, 'qty': sec_qty}
                for sec_name, sec_qty in sorted(data['sections'].items())
            ],
        })
    draft_grouped_summary.sort(key=lambda g: (-g['item_count'], -float(g['total_qty'])))
    draft_has_merged = any(g['item_count'] > 1 for g in draft_grouped_summary)

    purchasing_officers = list(_officer_users(cafe, 'purchasing'))
    my_purchases_count = _pending_purchasing_assignments_qs(
        request, request.user, is_self_buy=True
    ).count()

    return render(request, 'warehouse_purchase_draft.html', {
        'draft': draft,
        'draft_items': draft_items,
        'draft_item_infos': draft_item_infos,
        'draft_item_sections': draft_item_sections,
        'draft_grouped_summary': draft_grouped_summary,
        'draft_has_merged': draft_has_merged,
        'draft_eval_map': draft_eval_map,
        'ceo_draft_count': ceo_draft_count,
        'direct_draft_count': direct_draft_count,
        'draft_requested_delivery': draft_requested_delivery,
        'purchasing_officers': purchasing_officers,
        'my_purchases_count': my_purchases_count,
        'sent_requests': sent_requests,
        'approved_sent_requests': approved_sent_requests,
        'rejected_sent_requests': rejected_sent_requests,
        'all_categories': all_categories,
        'cafe_code': cafe_code,
        'user': request.user,
    })
