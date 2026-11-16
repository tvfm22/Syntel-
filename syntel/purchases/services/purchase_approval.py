"""قواعد تشخیص نیاز به تایید مدیرعامل برای اقلام «درخواست خرید» مسئول انبار.

قانون: همهٔ کالاها نیازی به تایید مدیرعامل ندارند. یک قلم فقط در صورت
برقراری دست‌کم یکی از چهار شرط زیر به تایید مدیرعامل نیاز دارد:
1. کالا در لیست کالاها تعریف نشده باشد (`product is None`).
2. کالای جدید توسط کاربر تعریف شده باشد (`is_complete=False` یا دستهٔ «تحقیق و توسعه»).
3. ارزش خرید تخمینی آن بیش از ۴۰۰٬۰۰۰٬۰۰۰ ریال (۴۰۰ میلیون ریال) شود.
4. مقدار خرید آن بیش از ۳ برابر میانگین خریدهای قبلی همان کالا باشد.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from purchases.models import InvoiceItem, PurchaseItem, PurchaseOrderItem
from purchases.services.recipe_gate import RD_CATEGORY_NAME
from purchases.services.units import (
    _squash,
    convert_quantity_to_smallest_unit,
    unit_family,
)

# آستانهٔ ارزش خرید نیازمند تایید مدیرعامل: ۴۰۰ میلیون ریال
HIGH_VALUE_THRESHOLD_RIAL = Decimal('400000000')
# ضریب آستانهٔ مقدار خرید نسبت به میانگین خرید قبلی
HIGH_QTY_AVG_MULTIPLIER = Decimal('3')


def _fmt_qty(value: Decimal) -> str:
    """قالب‌بندی عدد اعشاری با حداکثر ۲ رقم اعشار و بدون صفر اضافه."""
    q = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    s = format(q, 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


def _norm_group_and_qty(qty, unit_name: str) -> tuple[object, Decimal]:
    """نرمال‌سازی مقدار به گروه واحد سازگار (گرم/میل/واحد شمارشی)."""
    qty_dec = Decimal(str(qty or 0))
    fam = unit_family(unit_name)
    if fam in ('kg', 'gram'):
        norm_qty, _ = convert_quantity_to_smallest_unit(qty_dec, unit_name)
        return 'mass', norm_qty if norm_qty is not None else qty_dec
    if fam in ('liter', 'mil'):
        norm_qty, _ = convert_quantity_to_smallest_unit(qty_dec, unit_name)
        return 'volume', norm_qty if norm_qty is not None else qty_dec
    return ('unit', _squash(unit_name)), qty_dec


def _item_unit_factor(unit_name: str) -> tuple[object, Decimal]:
    """کلید گروه و ضریب تبدیل از واحد کوچک‌ترین (گرم/میل) به واحد قلم."""
    fam = unit_family(unit_name)
    if fam == 'kg':
        return 'mass', Decimal('1000')
    if fam == 'gram':
        return 'mass', Decimal('1')
    if fam == 'liter':
        return 'volume', Decimal('1000')
    if fam == 'mil':
        return 'volume', Decimal('1')
    return ('unit', _squash(unit_name)), Decimal('1')


def compute_product_avg_purchase_quantities(product_ids, cafe=None) -> dict[int, dict[object, Decimal]]:
    """محاسبهٔ میانگین مقدار خریدهای قبلی هر محصول به تفکیک گروه واحد.

    منابع سابقهٔ خرید (بدون شمارش تکراری):
    - اقلام درخواست خرید تاییدشده/ارسال‌شده به خرید یا خریداری‌شده (`PurchaseItem`)
    - اقلام سفارش خرید (`PurchaseOrderItem`) غیرلغو و متصل‌نشده به قلم قبلی
    - اقلام فاکتور خرید (`InvoiceItem`) غیرلغو و متصل‌نشده به سفارش قبلی
    """
    pids = [pid for pid in set(product_ids or []) if pid]
    if not pids:
        return {}

    buckets: dict[int, dict[object, list[Decimal]]] = defaultdict(lambda: defaultdict(list))

    pi_qs = (
        PurchaseItem.objects
        .filter(product_id__in=pids)
        .exclude(request__status__in=('draft', 'rejected'))
        .filter(
            Q(request__source='warehouse_purchase', request__status__in=('pending', 'purchased', 'partial'))
            | Q(supply_status='purchased')
            | Q(purchased_quantity__gt=0)
            | Q(officer_assignments__target_role='purchasing', officer_assignments__status='purchased')
        )
        .select_related('product__unit')
        .distinct()
    )
    if cafe is not None:
        pi_qs = pi_qs.filter(request__cafe=cafe)

    used_pi_ids = set()
    for pi in pi_qs:
        used_pi_ids.add(pi.id)
        qty = pi.purchased_quantity if (pi.purchased_quantity and pi.purchased_quantity > 0) else pi.quantity
        if not qty or qty <= 0:
            continue
        unit_name = (pi.unit or (pi.product.unit.name if pi.product and pi.product.unit else '')).strip()
        group, norm_qty = _norm_group_and_qty(qty, unit_name)
        buckets[pi.product_id][group].append(norm_qty)

    poi_qs = (
        PurchaseOrderItem.objects
        .filter(product_id__in=pids, quantity__gt=0)
        .exclude(order__status='cancelled')
        .select_related('product__unit', 'request_item')
    )
    if cafe is not None:
        poi_qs = poi_qs.filter(Q(order__request__cafe=cafe) | Q(order__request__isnull=True, product__cafe=cafe))

    used_poi_ids = set()
    for poi in poi_qs:
        used_poi_ids.add(poi.id)
        if poi.request_item_id in used_pi_ids:
            continue
        unit_name = (
            (poi.request_item.unit if poi.request_item else '')
            or (poi.product.unit.name if poi.product and poi.product.unit else '')
        ).strip()
        group, norm_qty = _norm_group_and_qty(poi.quantity, unit_name)
        buckets[poi.product_id][group].append(norm_qty)

    inv_qs = (
        InvoiceItem.objects
        .filter(product_id__in=pids, quantity__gt=0)
        .exclude(invoice__status='cancelled')
        .select_related('product__unit', 'order_item__request_item')
    )
    if cafe is not None:
        inv_qs = inv_qs.filter(invoice__cafe=cafe)

    for ii in inv_qs:
        if ii.order_item_id in used_poi_ids:
            continue
        if ii.order_item_id and ii.order_item.request_item_id in used_pi_ids:
            continue
        unit_name = (ii.product.unit.name if ii.product and ii.product.unit else '').strip()
        group, norm_qty = _norm_group_and_qty(ii.quantity, unit_name)
        buckets[ii.product_id][group].append(norm_qty)

    avg_map: dict[int, dict[object, Decimal]] = {}
    for pid, groups in buckets.items():
        avg_map[pid] = {
            grp: sum(vals, Decimal('0')) / Decimal(len(vals))
            for grp, vals in groups.items()
            if vals
        }
    return avg_map


def evaluate_draft_items_ceo_status(draft_items, cafe=None) -> dict[int, dict]:
    """ارزیابی همهٔ اقلام پیش‌نویس برای تشخیص نیاز به تایید مدیرعامل.

    خروجی نگاشت ``item.id -> dict`` شامل:
    - ``needs_ceo``: bool
    - ``reasons``: فهرست دلایل فارسی برای نمایش به کاربر
    - ``reason_codes``: کدهای ماشینی (``unlisted``, ``new_product``, ``high_value``, ``high_qty_avg``)
    - ``estimated_value``: ارزش خرید تخمینی (ریال) یا None
    - ``unit_price``: قیمت تخمینی واحد (ریال) یا None
    - ``avg_qty``: میانگین مقدار خرید قبلی در واحد قلم یا None
    """
    from purchases.views import _build_product_pricing_map, _estimate_consumption_cost

    items = list(draft_items or [])
    if not items:
        return {}

    products = [d.product for d in items if getattr(d, 'product_id', None) and getattr(d, 'product', None)]
    pricing_map = _build_product_pricing_map(products, cafe=cafe)
    avg_map = compute_product_avg_purchase_quantities([p.id for p in products], cafe=cafe)

    results: dict[int, dict] = {}
    for d in items:
        reasons: list[tuple[str, str]] = []
        est_cost = None
        unit_price = None
        avg_qty_in_unit = None

        prod = getattr(d, 'product', None) if getattr(d, 'product_id', None) else None
        qty_dec = Decimal(str(d.quantity or 0))
        item_unit = (d.unit or (prod.unit.name if prod and prod.unit else '') or '').strip()

        if prod is None:
            reasons.append(('unlisted', 'تعریف‌نشده در لیست کالاها'))
        else:
            cat_name = getattr(getattr(prod, 'category', None), 'name', '')
            if not prod.is_complete or cat_name == RD_CATEGORY_NAME:
                reasons.append(('new_product', 'کالای جدید تعریف‌شده توسط کاربر'))

            p_info = pricing_map.get(prod.id)
            est_cost, unit_price, _ = _estimate_consumption_cost(qty_dec, item_unit, p_info)
            if est_cost is not None and est_cost > HIGH_VALUE_THRESHOLD_RIAL:
                reasons.append(('high_value', 'ارزش خرید بیش از ۴۰۰ میلیون ریال'))

            group, factor = _item_unit_factor(item_unit)
            avg_norm = avg_map.get(prod.id, {}).get(group)
            if avg_norm is not None and avg_norm > 0:
                avg_qty_in_unit = (avg_norm / factor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if qty_dec > HIGH_QTY_AVG_MULTIPLIER * (avg_norm / factor):
                    unit_suffix = f' {item_unit}' if item_unit else ''
                    reasons.append((
                        'high_qty_avg',
                        f'مقدار بیش از ۳ برابر میانگین خرید (میانگین: {_fmt_qty(avg_qty_in_unit)}{unit_suffix})',
                    ))

        results[d.id] = {
            'needs_ceo': bool(reasons),
            'reasons': [label for _, label in reasons],
            'reason_codes': [code for code, _ in reasons],
            'estimated_value': est_cost,
            'unit_price': unit_price,
            'avg_qty': avg_qty_in_unit,
        }

    return results
