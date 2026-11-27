from __future__ import annotations

from ..models import Product
from ..models import PurchaseItem
from ..models import PurchaseRequest
from ..models import StockItem
from ..models import Warehouse
from ..models import WarehouseRequest
from ..models import WarehouseRequestItem
from ..numbering import request_number as request_number_for
from ..services.inventory import InsufficientStockError
from ..services.inventory import StockScopeError
from ..services.inventory import apply_stock_delta
from .helpers import _fmt2
from .helpers import _parse_decimal
from .helpers import _warehouse_request_status
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_scoped_locked_object_or_404
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from .workflow_helpers import _handle_my_purchases_history
from .workflow_helpers import _handle_my_purchases_view
from .workflow_helpers import _route_request_to_warehouse
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
import json


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def process_warehouse_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """پردازش درخواست انبار توسط مسئول خرید یا مسئول انبار"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    # موجودی‌های انبار برای جستجو — فقط همین کافه (در انبار با موجودی منفی مجاز، صفر/منفی هم نمایش داده می‌شود)
    stock_items = (
        StockItem.objects.select_related('product', 'unit', 'warehouse')
        .filter(
            Q(quantity__gt=0) | Q(warehouse__allow_negative_stock=True, warehouse__is_active=True),
            warehouse__cafe=cafe,
        )
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
                        if not (s_item.warehouse and s_item.warehouse.allow_negative_stock) and supplied_qty > s_item.quantity:
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

    qs = StockItem.objects.select_related('product', 'unit', 'warehouse').filter(
        Q(quantity__gt=0) | Q(warehouse__allow_negative_stock=True, warehouse__is_active=True)
    )
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

                if not (s_item.warehouse and s_item.warehouse.allow_negative_stock) and base_deduction > s_item.quantity:
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
            Q(quantity__gt=0) | Q(warehouse__allow_negative_stock=True, warehouse__is_active=True),
            product=item.product,
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
@role_required(['warehouse', 'purchasing'])
def warehouse_my_purchases(request: HttpRequest) -> HttpResponse:
    """صفحه «خریدهای من» مسئول انبار وقتی خرید توسط خودم را انتخاب کرده است."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    if not (request.user.role == 'admin' or request.user.is_superuser or request.user.has_warehouse_role):
        return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

    return _handle_my_purchases_view(
        request,
        is_self_buy=True,
        is_warehouse_self_buy=True,
        redirect_url_name='warehouse_my_purchases',
        history_url_name='warehouse_my_purchases_history',
    )


@custom_login_required
@role_required(['warehouse', 'purchasing'])
def warehouse_my_purchases_history(request: HttpRequest) -> HttpResponse:
    """سوابق «خریدهای من» مسئول انبار."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    if not (request.user.role == 'admin' or request.user.is_superuser or request.user.has_warehouse_role):
        return redirect('purchase_officer_dashboard', cafe_code=cafe_code)

    return _handle_my_purchases_history(
        request,
        is_self_buy=True,
        is_warehouse_self_buy=True,
        list_url_name='warehouse_my_purchases',
        history_url_name='warehouse_my_purchases_history',
    )
