from __future__ import annotations

from ..models import Category
from ..models import Department
from ..models import InvoiceItem
from ..models import KukerItem
from ..models import KukerRecipeLine
from ..models import OfficerAssignment
from ..models import PriceHistory
from ..models import Product
from ..models import ProductPriceEntry
from ..models import PurchaseItem
from ..models import StockRequest
from ..models import StockTransaction
from ..models import WarehouseRequest
from ..numbering import request_number as request_number_for
from ..services.units import smallest_unit_of
from .helpers import _fmt2
from .helpers import _format_jalali_date
from .helpers import _item_section_name
from .helpers import _parse_jalali_date
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import ROUND_HALF_UP
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
import jdatetime


def _parse_consumption_date(request, prefix, fallback):
    """خواندن تاریخ از پارامترهای GET گزارش مصرف:
    ۱) `start_date` / `end_date` (به صورت شمسی `1405/08/10` یا میلادی `2026-11-01`)
    ۲) `start_year`, `start_month`, `start_day` (یا `end_*`) از انتخابگر شمسی
    ۳) مقدار پیش‌فرض `fallback`
    """
    import jdatetime

    raw = (request.GET.get(f'{prefix}_date') or '').strip()
    if raw:
        parsed = _parse_jalali_date(raw)
        if parsed is not None:
            return parsed

    y_raw = (request.GET.get(f'{prefix}_year') or '').strip()
    m_raw = (request.GET.get(f'{prefix}_month') or '').strip()
    d_raw = (request.GET.get(f'{prefix}_day') or '').strip()
    if y_raw.isdigit() and m_raw.isdigit() and d_raw.isdigit():
        try:
            return jdatetime.date(int(y_raw), int(m_raw), int(d_raw)).togregorian()
        except (ValueError, TypeError):
            pass

    return fallback


def _build_product_pricing_map(products, cafe=None):
    """محاسبه آخرین قیمت واردشده برای فهرست محصولات.

    منابع قیمت (به ترتیب اولویت):
    ۱) `ProductPriceEntry`: آخرین قیمت ثبت‌شده توسط کاربر (`price_per_smallest_unit`, `smallest_unit`, `entered_amount`, `entered_unit`)
    ۲) `Product.base_unit_price` و `KukerItem(kind='raw')`: قیمت پایه محصول در بهای تمام شده
       (با تشخیص هوشمند واحد از روی خطوط رسپی وقتی `smallest_unit` خالی است)
    ۳) `PriceHistory`: آخرین رکورد تاریخچه قیمت خرید
    ۴) `InvoiceItem`: آخرین ردیف فاکتور خرید تایید/ثبت شده
    """
    from ..services.units import smallest_unit_of, unit_family

    product_list = [p for p in products if p is not None]
    if not product_list:
        return {}

    pids = [p.id for p in product_list]

    # ۱) آخرین ProductPriceEntry برای هر محصول
    latest_entry = {}
    entries_qs = ProductPriceEntry.objects.filter(product_id__in=pids).order_by('product_id', '-recorded_at', '-id')
    if cafe:
        entries_qs = entries_qs.filter(cafe=cafe)
    for entry in entries_qs:
        if entry.product_id not in latest_entry:
            latest_entry[entry.product_id] = entry

    # ۲) KukerItem و واحد رسپی‌ها برای تشخیص واحد قیمت‌های ایمپورت‌شده از اکسل
    kuker_map = {}
    kuker_qs = KukerItem.objects.filter(product_id__in=pids, kind=KukerItem.KIND_RAW)
    if cafe:
        kuker_qs = kuker_qs.filter(cafe=cafe)
    for ki in kuker_qs:
        kuker_map[ki.product_id] = ki

    recipe_units = {}
    if kuker_map:
        k_ids = [ki.id for ki in kuker_map.values()]
        for mat_id, u in KukerRecipeLine.objects.filter(material_id__in=k_ids).values_list('material_id', 'unit'):
            u_clean = (u or '').strip()
            if u_clean:
                recipe_units.setdefault(mat_id, set()).add(u_clean)

    # ۳) آخرین PriceHistory برای هر محصول
    latest_ph = {}
    for ph in PriceHistory.objects.filter(product_id__in=pids).order_by('product_id', '-date', '-id'):
        if ph.product_id not in latest_ph and ph.price and ph.price > 0:
            latest_ph[ph.product_id] = ph

    # ۴) آخرین InvoiceItem برای هر محصول
    latest_inv = {}
    inv_qs = (InvoiceItem.objects
              .filter(product_id__in=pids, unit_price__gt=0)
              .exclude(invoice__status='cancelled')
              .select_related('invoice')
              .order_by('product_id', '-invoice__invoice_date', '-id'))
    if cafe:
        inv_qs = inv_qs.filter(invoice__cafe=cafe)
    for ii in inv_qs:
        if ii.product_id not in latest_inv:
            latest_inv[ii.product_id] = ii

    pricing_map = {}
    for p in product_list:
        p_unit = (p.unit.name if p.unit_id and p.unit else '').strip()
        p_smallest = (p.smallest_unit or '').strip()
        entry = latest_entry.get(p.id)
        ki = kuker_map.get(p.id)
        ph = latest_ph.get(p.id)
        inv = latest_inv.get(p.id)

        info = {
            'has_price': False,
            'price_Basis_amount': None,   # قیمت به ازای ۱ واحدِ basis_unit
            'basis_unit': '',             # واحدی که price_Basis_amount بر اساس آن است (مثلاً گرم یا کیلوگرم)
            'display_price': None,        # قیمت نمایشی برای کاربر
            'display_unit': '',           # واحد نمایشی قیمت (مثلاً هر کیلوگرم / هر گرم / هر عدد)
            'entered_text': '',           # توضیح تکمیلی آخرین قیمت واردشده
            'source_label': '',
        }

        if entry is not None and entry.price_per_smallest_unit is not None and entry.price_per_smallest_unit > 0:
            b_unit = (entry.smallest_unit or p_smallest or smallest_unit_of(entry.entered_unit) or p_unit or 'واحد').strip()
            info['has_price'] = True
            info['price_Basis_amount'] = Decimal(str(entry.price_per_smallest_unit))
            info['basis_unit'] = b_unit
            info['display_price'] = Decimal(str(entry.entered_amount or entry.price_per_smallest_unit))
            info['display_unit'] = (entry.entered_unit or b_unit).strip()
            info['source_label'] = 'ثبت قیمت کالا'
        elif (p.base_unit_price is not None and p.base_unit_price > 0) or (ki and ki.price is not None and ki.price > 0):
            raw_price = Decimal(str(p.base_unit_price if (p.base_unit_price is not None and p.base_unit_price > 0) else ki.price))
            b_unit = p_smallest or (ki.out_unit if ki else '') or ''
            if not b_unit and ki:
                r_units = recipe_units.get(ki.id, set())
                if 'گرم' in r_units:
                    b_unit = 'گرم'
                elif 'میل' in r_units:
                    b_unit = 'میل'
                elif len(r_units) == 1:
                    b_unit = next(iter(r_units))
            if not b_unit:
                b_unit = smallest_unit_of(p_unit) or p_unit or 'واحد'
            info['has_price'] = True
            info['price_Basis_amount'] = raw_price
            info['basis_unit'] = b_unit
            info['display_price'] = raw_price
            info['display_unit'] = b_unit
            info['source_label'] = 'قیمت پایه کالا'
        elif ph is not None:
            b_unit = p_unit or p_smallest or 'واحد'
            info['has_price'] = True
            info['price_Basis_amount'] = Decimal(str(ph.price))
            info['basis_unit'] = b_unit
            info['display_price'] = Decimal(str(ph.price))
            info['display_unit'] = b_unit
            info['source_label'] = 'تاریخچه خرید'
        elif inv is not None:
            b_unit = p_unit or p_smallest or 'واحد'
            info['has_price'] = True
            info['price_Basis_amount'] = Decimal(str(inv.unit_price))
            info['basis_unit'] = b_unit
            info['display_price'] = Decimal(str(inv.unit_price))
            info['display_unit'] = b_unit
            info['source_label'] = 'آخرین فاکتور خرید'

        pricing_map[p.id] = info

    return pricing_map


def _estimate_consumption_cost(qty, tx_unit_name, price_info):
    """محاسبه تخمین قیمت مصرف برای مقدار `qty` با واحد `tx_unit_name` بر اساس `price_info`.

    همچنین قیمتِ معادلِ هر ۱ واحدِ مصرفی (`unit_price_for_tx_unit`) را برمی‌گرداند تا
    در جدول و مودال به زبانِ همان واحدِ تحویلی (مثلاً کیلوگرم یا گرم) قابل فهم باشد.
    """
    from ..services.units import unit_family, units_are_compatible

    if not price_info or not price_info.get('has_price') or price_info.get('price_Basis_amount') is None:
        return None, None, ''

    qty_dec = Decimal(str(qty or 0))
    basis_price = Decimal(str(price_info['price_Basis_amount']))
    basis_unit = (price_info.get('basis_unit') or '').strip()
    tx_unit = (tx_unit_name or basis_unit or 'واحد').strip()

    if basis_unit and tx_unit and not units_are_compatible(tx_unit, basis_unit):
        return None, None, ''

    fam_tx = unit_family(tx_unit)
    fam_basis = unit_family(basis_unit)

    factor = Decimal('1')
    if fam_tx in ('kg', 'liter') and fam_basis in ('gram', 'mil'):
        factor = Decimal('1000')
    elif fam_tx in ('gram', 'mil') and fam_basis in ('kg', 'liter'):
        factor = Decimal('0.001')

    unit_price_in_tx_unit = (basis_price * factor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    estimated_cost = (qty_dec * basis_price * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return estimated_cost, unit_price_in_tx_unit, tx_unit


def _collect_consumption_deliveries(cafe, start_dt=None, end_dt=None, product_id=None):
    """استخراج و نرمال‌سازی تمامی تحویل‌های خروجی انبار (`StockTransaction` با `transaction_type='out'`).

    - تخصیص‌های بازگشایی‌شده (`officer_assignment_reopen`) و حواله‌های برگشت‌خورده (`reversed`)
      به صورت خودکار فیلتر می‌شوند تا فقط تحویل‌های واقعی محاسبه شوند.
    - بخش تحویل‌گیرنده (`section_name`, `department_id`) برای همه مسیرها (`officer_assignment`,
      `purchase_item`, `warehouse_request`, `stock_request`, `warehouse_voucher`) استخراج می‌شود.
    """
    qs = StockTransaction.objects.filter(transaction_type='out').select_related(
        'stock_item__product__category__parent',
        'stock_item__product__unit',
        'stock_item__warehouse',
        'unit',
        'voucher__department',
        'voucher__destination_warehouse',
        'voucher__created_by__department',
        'created_by__department',
    ).prefetch_related('stock_item__product__departments')
    if cafe is not None:
        qs = qs.filter(stock_item__warehouse__cafe=cafe)
    if start_dt is not None:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt is not None:
        qs = qs.filter(created_at__lte=end_dt)
    if product_id is not None:
        qs = qs.filter(stock_item__product_id=product_id)

    txs = list(qs.order_by('-created_at', '-id'))
    if not txs:
        return [], {}

    # شناسایی تخصیص‌های بازگشایی‌شده تا از گزارش مصرف حذف شوند:
    # به ازای هر قلم فقط به تعداد تراکنش‌های بازگشایی‌شده، قدیمی‌ترین تراکنش‌های خروج متناظر خنثی می‌شوند
    # تا تحویل‌های سالمِ بعدیِ همان PurchaseItem حذف نشوند.
    oa_ref_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type == 'officer_assignment' and tx.reference_id
    }
    neutralized_tx_ids = set()
    fully_reopened_item_ids = set()
    if oa_ref_ids:
        from collections import Counter
        reopen_qs = StockTransaction.objects.filter(
            reference_type='officer_assignment_reopen',
            reference_id__in=oa_ref_ids,
        )
        if cafe is not None:
            reopen_qs = reopen_qs.filter(stock_item__warehouse__cafe=cafe)
        reopen_counts = Counter(reopen_qs.values_list('reference_id', flat=True))

        if reopen_counts:
            # txs به صورت نزولی (-created_at, -id) مرتب است، پس پیمایش معکوس از قدیمی‌ترین شروع می‌کند
            remaining = dict(reopen_counts)
            for tx in reversed(txs):
                if tx.reference_type == 'officer_assignment' and tx.reference_id in remaining:
                    if remaining[tx.reference_id] > 0:
                        neutralized_tx_ids.add(tx.id)
                        remaining[tx.reference_id] -= 1

        # اگر تمام تخصیص‌های انبار یک قلم در وضعیت reopened باشند
        active_oa_item_ids = set(
            OfficerAssignment.objects.filter(
                item_id__in=oa_ref_ids,
                target_role='warehouse',
            ).exclude(status='reopened').values_list('item_id', flat=True)
        )
        all_oa_item_ids = set(
            OfficerAssignment.objects.filter(
                item_id__in=oa_ref_ids,
                target_role='warehouse',
            ).values_list('item_id', flat=True)
        )
        fully_reopened_item_ids.update(all_oa_item_ids - active_oa_item_ids)

    # واکشی یکجای رکوردهای مرجع (PurchaseItem, WarehouseRequest, StockRequest)
    pi_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type in ('officer_assignment', 'purchase_item') and tx.reference_id
    }
    wh_req_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type == 'warehouse_request' and tx.reference_id
    }
    sreq_ids = {
        tx.reference_id for tx in txs
        if tx.reference_type == 'stock_request' and tx.reference_id
    }

    pi_map = {}
    if pi_ids:
        pi_map = {
            pi.id: pi for pi in PurchaseItem.objects.filter(id__in=pi_ids)
            .select_related('request__user__department')
        }

    wh_req_map = {}
    if wh_req_ids:
        wh_req_map = {
            wr.id: wr for wr in WarehouseRequest.objects.filter(id__in=wh_req_ids)
            .select_related('user__department')
        }

    sreq_map = {}
    if sreq_ids:
        sreq_map = {
            sr.id: sr for sr in StockRequest.objects.filter(id__in=sreq_ids)
            .select_related('purchase_item__request__user__department', 'requested_by__department')
        }

    # نگاشت نام بخش به Department.id در کافه فعلی
    dept_by_name = {}
    if cafe is not None:
        for d in Department.objects.filter(cafe=cafe):
            dept_by_name[d.name.strip()] = d.id

    products_seen = {}
    for tx in txs:
        if tx.stock_item_id and tx.stock_item and tx.stock_item.product_id:
            products_seen[tx.stock_item.product_id] = tx.stock_item.product

    pricing_map = _build_product_pricing_map(products_seen.values(), cafe=cafe)

    deliveries = []
    for tx in txs:
        # ۱) حذف تخصیص‌های بازگشایی‌شده
        if tx.id in neutralized_tx_ids or (
            tx.reference_type == 'officer_assignment' and tx.reference_id in fully_reopened_item_ids
        ):
            continue

        # ۲) حذف حواله‌های برگشت‌خورده یا معکوس‌کننده
        voucher = tx.voucher
        if voucher is not None:
            if voucher.status == 'reversed' or voucher.reverses_id is not None:
                continue
            # حواله انتقال بین انبارها مصرف نهایی سکشن نیست مگر مقصد بخش باشد
            if voucher.voucher_type == 'transfer' and not voucher.department_id:
                continue

        si = tx.stock_item
        product = si.product if si else None
        if product is None:
            continue

        # تعیین واحد تراکنش
        unit_name = ''
        if tx.unit_id and tx.unit:
            unit_name = tx.unit.name
        elif si and si.unit_id and si.unit:
            unit_name = si.unit.name
        elif product.unit_id and product.unit:
            unit_name = product.unit.name
        elif product.smallest_unit:
            unit_name = product.smallest_unit
        unit_name = (unit_name or 'عدد').strip()

        # تعیین بخش تحویل‌گیرنده و درخواست‌دهنده
        section_name = 'نامشخص'
        dept_id = None
        requester_name = '—'
        ref_label = 'تحویل انبار'

        if tx.reference_type in ('officer_assignment', 'purchase_item') and tx.reference_id:
            pi = pi_map.get(tx.reference_id)
            if pi and pi.request:
                req_user = pi.request.user
                section_name = _item_section_name(req_user)
                dept_id = getattr(req_user, 'department_id', None)
                requester_name = (req_user.get_full_name() or req_user.username) if req_user else '—'
                ref_label = f'درخواست {request_number_for(pi.request_id)}'
        elif tx.reference_type == 'warehouse_request' and tx.reference_id:
            wr = wh_req_map.get(tx.reference_id)
            if wr:
                req_user = wr.user
                section_name = _item_section_name(req_user)
                dept_id = getattr(req_user, 'department_id', None)
                requester_name = (req_user.get_full_name() or req_user.username) if req_user else '—'
                ref_label = f'درخواست انبار {request_number_for(wr.id)}'
        elif tx.reference_type == 'stock_request' and tx.reference_id:
            sr = sreq_map.get(tx.reference_id)
            if sr:
                req_user = (sr.purchase_item.request.user
                            if (sr.purchase_item and sr.purchase_item.request)
                            else sr.requested_by)
                section_name = _item_section_name(req_user)
                dept_id = getattr(req_user, 'department_id', None)
                requester_name = (req_user.get_full_name() or req_user.username) if req_user else '—'
                ref_label = f'تامین انبار {sr.id}'
        elif voucher is not None:
            if voucher.department_id and voucher.department:
                section_name = voucher.department.name
                dept_id = voucher.department_id
            elif voucher.voucher_type == 'waste':
                section_name = 'ضایعات'
            elif voucher.voucher_type == 'sale':
                section_name = 'فروش'
            elif voucher.voucher_type == 'return':
                section_name = 'مرجوعی به تامین‌کننده'
            elif voucher.created_by:
                section_name = _item_section_name(voucher.created_by)
                dept_id = getattr(voucher.created_by, 'department_id', None)
            else:
                section_name = 'حواله خروج'
            if voucher.created_by:
                requester_name = voucher.created_by.get_full_name() or voucher.created_by.username
            ref_label = f'حواله {voucher.voucher_number}'
        else:
            product_depts = list(product.departments.all())
            if len(product_depts) == 1:
                section_name = product_depts[0].name
                dept_id = product_depts[0].id
            elif len(product_depts) > 1:
                section_name = '، '.join(d.name for d in product_depts)
            elif product.usage_location and product.usage_location not in ('general', 'multi'):
                section_name = _item_section_name(type('U', (), {'role': product.usage_location, 'department': None})())
            elif tx.created_by:
                section_name = _item_section_name(tx.created_by)
                dept_id = getattr(tx.created_by, 'department_id', None)

        if not dept_id and section_name in dept_by_name:
            dept_id = dept_by_name[section_name]

        officer_name = (
            (tx.created_by.get_full_name() or tx.created_by.username)
            if tx.created_by else '—'
        )
        warehouse_name = (
            si.warehouse.name
            if (si and si.warehouse_id and si.warehouse) else '—'
        )

        cat = product.category
        cat_id = cat.id if cat else None
        parent_cat_id = cat.parent_id if (cat and cat.parent_id) else None
        if cat:
            cat_name = f'{cat.parent.name} / {cat.name}' if cat.parent else cat.name
        else:
            cat_name = 'بدون دسته‌بندی'

        p_price_info = pricing_map.get(product.id) or {}
        est_cost, unit_price_tx, price_unit_tx = _estimate_consumption_cost(
            tx.quantity, unit_name, p_price_info
        )

        local_dt = timezone.localtime(tx.created_at) if timezone.is_aware(tx.created_at) else tx.created_at
        jalali_date = _format_jalali_date(local_dt)
        time_str = local_dt.strftime('%H:%M')

        deliveries.append({
            'id': tx.id,
            'product_id': product.id,
            'product_name': product.name,
            'category_id': cat_id,
            'parent_category_id': parent_cat_id,
            'category_name': cat_name,
            'warehouse_name': warehouse_name,
            'section_name': section_name,
            'department_id': dept_id,
            'requester_name': requester_name,
            'officer_name': officer_name,
            'ref_label': ref_label,
            'description': tx.description or '',
            'quantity': tx.quantity,
            'unit_name': unit_name,
            'estimated_cost': est_cost,
            'unit_price': unit_price_tx,
            'price_unit': price_unit_tx,
            'created_at': local_dt,
            'date': local_dt.date(),
            'jalali_date': jalali_date,
            'time_str': time_str,
            'jalali_datetime': f'{jalali_date} - {time_str}',
        })

    return deliveries, pricing_map


@custom_login_required
@role_required(['warehouse', 'manager', 'admin', 'ceo', 'store', 'purchasing'])
def consumption_report(request):
    """گزارش جامع مصرف کالاها — تفکیک تحویلی هر سکشن، فیلتر بازه زمانی و دسته‌بندی،
    تخمین قیمت بر اساس آخرین قیمت واردشده و مشاهده سوابق تحویلی هر کالا در تاریخ‌های مختلف.
    """
    import jdatetime

    cafe = get_cafe_from_request(request)
    today = timezone.localdate()

    # ۱) بازه زمانی (پشتیبانی از تاریخ شمسی، میلادی و انتخابگر روز/ماه/سال؛ پیش‌فرض: ۳۰ روز اخیر)
    start_date = _parse_consumption_date(request, 'start', today - timedelta(days=30))
    end_date = _parse_consumption_date(request, 'end', today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))
    span_days = (end_date - start_date).days + 1

    # ۲) پارامترهای فیلتر: سکشن (بخش)، دسته‌بندی کالا، جستجو
    selected_section = (request.GET.get('section') or '').strip()
    department_param = (request.GET.get('department') or '').strip()
    selected_department_id = int(department_param) if department_param.isdigit() else None
    if not selected_section and department_param and not department_param.isdigit():
        selected_section = department_param

    selected_category = (request.GET.get('category') or '').strip()
    selected_category_id = int(selected_category) if selected_category.isdigit() else None
    search_query = (request.GET.get('q') or request.GET.get('search') or '').strip()

    # اگر department با شناسه عددی آمده، نام بخش متناظر را هم پیدا کن
    departments = list(Department.objects.filter(is_active=True, cafe=cafe).order_by('name')) if cafe else []
    dept_id_to_name = {d.id: d.name for d in departments}
    if selected_department_id and selected_department_id in dept_id_to_name and not selected_section:
        selected_section = dept_id_to_name[selected_department_id]

    # دسته‌بندی‌های کافه (شامل زیردسته‌ها هنگام فیلتر کردن دسته والد)
    categories_qs = Category.objects.filter(is_active=True).select_related('parent').order_by('parent__name', 'name')
    if cafe:
        categories_qs = categories_qs.filter(cafe=cafe)
    categories = list(categories_qs)

    allowed_category_ids = None
    if selected_category == 'none':
        allowed_category_ids = {None}
    elif selected_category_id is not None:
        child_ids = {c.id for c in categories if c.parent_id == selected_category_id}
        allowed_category_ids = {selected_category_id} | child_ids

    # ۳) استخراج تمام تحویل‌های بازه زمانی
    all_deliveries, pricing_map = _collect_consumption_deliveries(cafe, start_dt=start_dt, end_dt=end_dt)

    # گردآوری فهرست تمام سکشن‌های موجود (بخش‌های فعال کافه + سکشن‌های دارای تحویل در بازه)
    section_options = []
    seen_sections = set()
    for d in departments:
        if d.name not in seen_sections:
            seen_sections.add(d.name)
            section_options.append({'name': d.name, 'id': d.id})
    for row in all_deliveries:
        sname = row['section_name']
        if sname and sname not in seen_sections:
            seen_sections.add(sname)
            section_options.append({'name': sname, 'id': row['department_id']})

    # ۴) اعمال فیلتر دسته‌بندی و جستجو ابتدا برای محاسبه خلاصه سکشن‌ها، سپس فیلتر سکشن
    deliveries_after_cat_search = []
    for row in all_deliveries:
        if allowed_category_ids is not None and row['category_id'] not in allowed_category_ids:
            continue
        if search_query:
            q_low = search_query.lower()
            if (q_low not in row['product_name'].lower()
                    and q_low not in (row['category_name'] or '').lower()
                    and q_low not in (row['section_name'] or '').lower()):
                continue
        deliveries_after_cat_search.append(row)

    # خلاصه تحویلی هر سکشن (مقدار تحویلی هر سکشن، تعداد تحویل، تنوع کالا و تخمین هزینه هر سکشن)
    section_buckets = {}
    for row in deliveries_after_cat_search:
        sname = row['section_name']
        b = section_buckets.setdefault(sname, {
            'name': sname,
            'department_id': row['department_id'],
            'delivery_count': 0,
            'product_ids': set(),
            'estimated_cost': Decimal('0'),
            'priced_count': 0,
            'by_unit': {},
            'top_products': {},
        })
        b['delivery_count'] += 1
        b['product_ids'].add(row['product_id'])
        u = row['unit_name']
        b['by_unit'][u] = b['by_unit'].get(u, Decimal('0')) + row['quantity']
        if row['estimated_cost'] is not None:
            b['estimated_cost'] += row['estimated_cost']
            b['priced_count'] += 1
        tp = b['top_products'].setdefault(row['product_id'], {
            'name': row['product_name'],
            'qty': Decimal('0'),
            'unit': u,
        })
        tp['qty'] += row['quantity']

    total_deliveries_all_sections = sum((b['delivery_count'] for b in section_buckets.values()), 0)
    total_cost_all_sections = sum((b['estimated_cost'] for b in section_buckets.values()), Decimal('0'))

    section_rows = []
    for sname, b in section_buckets.items():
        unit_parts = [f"{_fmt2(qty)} {u}" for u, qty in b['by_unit'].items()]
        top_items = sorted(b['top_products'].values(), key=lambda x: x['qty'], reverse=True)[:4]
        if total_cost_all_sections > 0 and b['estimated_cost'] > 0:
            share_pct = int(round((b['estimated_cost'] / total_cost_all_sections) * 100))
        elif total_deliveries_all_sections > 0:
            share_pct = int(round((b['delivery_count'] / total_deliveries_all_sections) * 100))
        else:
            share_pct = 0
        section_rows.append({
            'name': sname,
            'department_id': b['department_id'],
            'cnt': b['delivery_count'],
            'product_count': len(b['product_ids']),
            'qty': sum(b['by_unit'].values(), Decimal('0')),
            'unit_summary': ' ، '.join(unit_parts),
            'unit_breakdown': [{'unit': u, 'qty': _fmt2(q)} for u, q in b['by_unit'].items()],
            'estimated_cost': b['estimated_cost'],
            'has_cost': b['priced_count'] > 0,
            'share_pct': max(0, min(100, share_pct)),
            'top_items': [{'name': ti['name'], 'qty': _fmt2(ti['qty']), 'unit': ti['unit']} for ti in top_items],
            'is_selected': (selected_section == sname) or (
                selected_department_id is not None and b['department_id'] == selected_department_id
            ),
        })
    section_rows.sort(key=lambda r: (r['estimated_cost'], r['cnt'], r['qty']), reverse=True)

    # ۵) اعمال فیلتر سکشن روی تحویل‌ها برای جدول کالاها و شاخص‌های کل
    filtered_deliveries = []
    for row in deliveries_after_cat_search:
        if selected_section and row['section_name'] != selected_section:
            if not (selected_department_id and row['department_id'] == selected_department_id):
                continue
        elif selected_department_id and not selected_section:
            if row['department_id'] != selected_department_id:
                continue
        filtered_deliveries.append(row)

    # ۶) تجمیع مصرف به تفکیک هر کالا (به همراه تفکیک تحویل به هر سکشن، تخمین قیمت و سوابق تحویل)
    product_groups = {}
    for row in filtered_deliveries:
        pid = row['product_id']
        p_info = pricing_map.get(pid) or {}
        g = product_groups.setdefault(pid, {
            'product_id': pid,
            'product_name': row['product_name'],
            'category_id': row['category_id'],
            'category_name': row['category_name'],
            'unit_name': row['unit_name'],
            'qty': Decimal('0'),
            'cnt': 0,
            'estimated_cost': Decimal('0'),
            'has_price': bool(p_info.get('has_price')),
            'unit_price': row['unit_price'],
            'price_unit': row['price_unit'] or p_info.get('display_unit') or row['unit_name'],
            'display_price': p_info.get('display_price'),
            'display_unit': p_info.get('display_unit') or row['unit_name'],
            'price_source': p_info.get('source_label') or '',
            'sections_map': {},
            'history': [],
        })
        g['qty'] += row['quantity']
        g['cnt'] += 1
        if row['estimated_cost'] is not None:
            g['estimated_cost'] += row['estimated_cost']
        if g['unit_price'] is None and row['unit_price'] is not None:
            g['unit_price'] = row['unit_price']
            g['price_unit'] = row['price_unit']

        sec_entry = g['sections_map'].setdefault(row['section_name'], {
            'section': row['section_name'],
            'qty': Decimal('0'),
            'cnt': 0,
            'cost': Decimal('0'),
        })
        sec_entry['qty'] += row['quantity']
        sec_entry['cnt'] += 1
        if row['estimated_cost'] is not None:
            sec_entry['cost'] += row['estimated_cost']

        g['history'].append({
            'id': row['id'],
            'jalali_date': row['jalali_date'],
            'time_str': row['time_str'],
            'jalali_datetime': row['jalali_datetime'],
            'section_name': row['section_name'],
            'requester_name': row['requester_name'],
            'officer_name': row['officer_name'],
            'warehouse_name': row['warehouse_name'],
            'ref_label': row['ref_label'],
            'description': row['description'],
            'quantity': _fmt2(row['quantity']),
            'quantity_raw': str(row['quantity']),
            'unit_name': row['unit_name'],
            'unit_price': _fmt2(row['unit_price']) if row['unit_price'] is not None else None,
            'estimated_cost': int(row['estimated_cost']) if row['estimated_cost'] is not None else None,
        })

    product_rows = []
    product_histories_payload = {}
    total_estimated_cost = Decimal('0')
    priced_products_count = 0

    for pid, g in product_groups.items():
        if g['has_price']:
            total_estimated_cost += g['estimated_cost']
            priced_products_count += 1

        sec_list = sorted(
            [
                {
                    'section': s['section'],
                    'qty': s['qty'],
                    'qty_fmt': _fmt2(s['qty']),
                    'cnt': s['cnt'],
                    'cost': int(s['cost']) if g['has_price'] else None,
                    'share_pct': int(round((s['qty'] / g['qty']) * 100)) if g['qty'] > 0 else 0,
                }
                for s in g['sections_map'].values()
            ],
            key=lambda x: x['qty'],
            reverse=True,
        )
        effective_unit_price = g['unit_price'] if g['unit_price'] is not None else g['display_price']
        effective_price_unit = g['price_unit'] if g['unit_price'] is not None else g['display_unit']

        row_dict = {
            'product_id': pid,
            'product_name': g['product_name'],
            'category_id': g['category_id'],
            'category_name': g['category_name'],
            'unit_name': g['unit_name'],
            'qty': g['qty'],
            'qty_fmt': _fmt2(g['qty']),
            'cnt': g['cnt'],
            'has_price': g['has_price'],
            'unit_price': effective_unit_price,
            'unit_price_fmt': _fmt2(effective_unit_price) if effective_unit_price is not None else None,
            'price_unit': effective_price_unit,
            'price_source': g['price_source'],
            'estimated_cost': g['estimated_cost'] if g['has_price'] else None,
            'section_breakdown': sec_list,
            'last_delivery_jalali': g['history'][0]['jalali_datetime'] if g['history'] else '—',
            # کلیدهای سازگاری با قالب‌های قدیمی
            'stock_item__product_id': pid,
            'stock_item__product__name': g['product_name'],
            'stock_item__product__unit__name': g['unit_name'],
        }
        product_rows.append(row_dict)

        product_histories_payload[str(pid)] = {
            'product_id': pid,
            'product_name': g['product_name'],
            'category_name': g['category_name'],
            'unit_name': g['unit_name'],
            'total_qty': _fmt2(g['qty']),
            'delivery_count': g['cnt'],
            'has_price': g['has_price'],
            'unit_price': _fmt2(effective_unit_price) if effective_unit_price is not None else None,
            'price_unit': effective_price_unit,
            'price_source': g['price_source'],
            'estimated_cost': int(g['estimated_cost']) if g['has_price'] else None,
            'sections': sec_list,
            'records': g['history'],
        }

    # مرتب‌سازی کالاها: بیشترین هزینه تخمینی و سپس بیشترین مقدار مصرف
    product_rows.sort(
        key=lambda r: (
            r['estimated_cost'] if r['estimated_cost'] is not None else Decimal('-1'),
            r['qty'],
        ),
        reverse=True,
    )

    total_qty = sum((r['qty'] for r in product_rows), Decimal('0'))
    txn_count = len(filtered_deliveries)
    product_count = len(product_rows)
    active_sections_count = len({r['section_name'] for r in filtered_deliveries})

    # بازه‌های زمانی آماده (چیپ‌های انتخاب سریع)
    sj = jdatetime.date.fromgregorian(date=start_date)
    ej = jdatetime.date.fromgregorian(date=end_date)
    today_j = jdatetime.date.fromgregorian(date=today)
    first_of_jmonth = jdatetime.date(today_j.year, today_j.month, 1).togregorian()

    presets = [
        {
            'label': 'امروز',
            'start': today.isoformat(),
            'end': today.isoformat(),
            'active': start_date == today and end_date == today,
        },
        {
            'label': '۷ روز اخیر',
            'start': (today - timedelta(days=7)).isoformat(),
            'end': today.isoformat(),
            'active': start_date == (today - timedelta(days=7)) and end_date == today,
        },
        {
            'label': 'ماه جاری',
            'start': first_of_jmonth.isoformat(),
            'end': today.isoformat(),
            'active': start_date == first_of_jmonth and end_date == today,
        },
        {
            'label': '۳۰ روز اخیر',
            'start': (today - timedelta(days=30)).isoformat(),
            'end': today.isoformat(),
            'active': start_date == (today - timedelta(days=30)) and end_date == today,
        },
        {
            'label': '۹۰ روز اخیر',
            'start': (today - timedelta(days=90)).isoformat(),
            'end': today.isoformat(),
            'active': start_date == (today - timedelta(days=90)) and end_date == today,
        },
    ]

    start_jalali = f'{sj.year}/{sj.month:02d}/{sj.day:02d}'
    end_jalali = f'{ej.year}/{ej.month:02d}/{ej.day:02d}'

    return render(request, 'reports/consumption_report.html', {
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'start_jalali': start_jalali,
        'end_jalali': end_jalali,
        'start_year': sj.year, 'start_month': sj.month, 'start_day': sj.day,
        'end_year': ej.year, 'end_month': ej.month, 'end_day': ej.day,
        'selected_department': selected_department_id or '',
        'selected_section': selected_section,
        'selected_category': selected_category,
        'search_query': search_query,
        'has_active_filters': bool(selected_section or selected_department_id or selected_category or search_query),
        'total_qty': total_qty,
        'txn_count': txn_count,
        'product_count': product_count,
        'priced_products_count': priced_products_count,
        'unpriced_products_count': product_count - priced_products_count,
        'total_estimated_cost': total_estimated_cost,
        'section_count': active_sections_count,
        'section_total': sum((r['qty'] for r in section_rows), Decimal('0')),
        'section_rows': section_rows,
        'section_options': section_options,
        'product_rows': product_rows,
        'product_histories': product_histories_payload,
        'departments': departments,
        'categories': categories,
        'presets': presets,
        'span_days': span_days,
    })


@custom_login_required
@role_required(['warehouse', 'manager', 'admin', 'ceo', 'store', 'purchasing'])
def consumption_product_history_api(request, product_id):
    """API سوابق تحویلی یک کالا در تاریخ‌های مختلف (برای مودال جزئیات تحویل کالا).

    امکان دریافت سوابق در بازه انتخابی یا کل تاریخچه تحویل آن کالا در کافه فعلی (`scope=all`).
    """
    cafe = get_cafe_from_request(request)
    product = get_scoped_object_or_404(Product, request, id=product_id)

    scope = (request.GET.get('scope') or 'range').strip()
    today = timezone.localdate()
    start_dt = None
    end_dt = None
    start_jalali = ''
    end_jalali = ''

    if scope != 'all':
        start_date = _parse_consumption_date(request, 'start', today - timedelta(days=30))
        end_date = _parse_consumption_date(request, 'end', today)
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))
        start_jalali = _format_jalali_date(start_date)
        end_jalali = _format_jalali_date(end_date)

    section_filter = (request.GET.get('section') or '').strip()
    deliveries, pricing_map = _collect_consumption_deliveries(
        cafe, start_dt=start_dt, end_dt=end_dt, product_id=product.id
    )
    if section_filter:
        deliveries = [d for d in deliveries if d['section_name'] == section_filter]

    if not pricing_map:
        pricing_map = _build_product_pricing_map([product], cafe=cafe)
    p_info = pricing_map.get(product.id) or {}

    total_qty = Decimal('0')
    total_cost = Decimal('0')
    sections_map = {}
    records = []

    p_unit = (product.unit.name if product.unit_id and product.unit else product.smallest_unit or 'عدد').strip()

    for row in deliveries:
        total_qty += row['quantity']
        if row['estimated_cost'] is not None:
            total_cost += row['estimated_cost']
        p_unit = row['unit_name'] or p_unit

        sec_entry = sections_map.setdefault(row['section_name'], {
            'section': row['section_name'],
            'qty': Decimal('0'),
            'cnt': 0,
            'cost': Decimal('0'),
        })
        sec_entry['qty'] += row['quantity']
        sec_entry['cnt'] += 1
        if row['estimated_cost'] is not None:
            sec_entry['cost'] += row['estimated_cost']

        records.append({
            'id': row['id'],
            'jalali_date': row['jalali_date'],
            'time_str': row['time_str'],
            'jalali_datetime': row['jalali_datetime'],
            'section_name': row['section_name'],
            'requester_name': row['requester_name'],
            'officer_name': row['officer_name'],
            'warehouse_name': row['warehouse_name'],
            'ref_label': row['ref_label'],
            'description': row['description'],
            'quantity': _fmt2(row['quantity']),
            'unit_name': row['unit_name'],
            'unit_price': _fmt2(row['unit_price']) if row['unit_price'] is not None else None,
            'estimated_cost': int(row['estimated_cost']) if row['estimated_cost'] is not None else None,
        })

    sections_list = sorted(
        [
            {
                'section': s['section'],
                'qty': _fmt2(s['qty']),
                'cnt': s['cnt'],
                'cost': int(s['cost']) if p_info.get('has_price') else None,
                'share_pct': int(round((s['qty'] / total_qty) * 100)) if total_qty > 0 else 0,
            }
            for s in sections_map.values()
        ],
        key=lambda x: x['share_pct'],
        reverse=True,
    )

    _, unit_price_in_p_unit, price_unit_label = _estimate_consumption_cost(
        Decimal('1'), p_unit, p_info
    )
    effective_unit_price = unit_price_in_p_unit if unit_price_in_p_unit is not None else p_info.get('display_price')
    effective_price_unit = price_unit_label or p_info.get('display_unit') or p_unit

    return JsonResponse({
        'success': True,
        'product_id': product.id,
        'product_name': product.name,
        'category_name': product.category.name if product.category else 'بدون دسته‌بندی',
        'unit_name': p_unit,
        'scope': scope,
        'start_jalali': start_jalali,
        'end_jalali': end_jalali,
        'total_qty': _fmt2(total_qty),
        'delivery_count': len(records),
        'has_price': bool(p_info.get('has_price')),
        'unit_price': _fmt2(effective_unit_price) if effective_unit_price is not None else None,
        'price_unit': effective_price_unit,
        'price_source': p_info.get('source_label') or '',
        'estimated_cost': int(total_cost) if p_info.get('has_price') else None,
        'sections': sections_list,
        'records': records,
    }, json_dumps_params={'ensure_ascii': False})
