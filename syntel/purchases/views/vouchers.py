from __future__ import annotations

from ..models import Department
from ..models import Product
from ..models import StockItem
from ..models import StockTransaction
from ..models import Unit
from ..models import User
from ..models import Warehouse
from ..models import WarehouseVoucher
from ..models import WarehouseVoucherItem
from ..services.inventory import InventoryServiceError
from ..services.inventory import VoucherValidationError
from ..services.inventory import finalize_voucher
from ..services.inventory import next_voucher_number
from ..services.inventory import reverse_voucher
from ..services.inventory import save_voucher_with_number
from .helpers import VOUCHER_STATUS_LABELS
from .helpers import VOUCHER_TYPE_LABELS
from .helpers import _format_jalali_date
from .helpers import _jalali_input_value
from .helpers import _normalize_numeric_str
from .helpers import _parse_jalali_date
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_scoped_locked_object_or_404
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from datetime import datetime
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone


VOUCHER_WAREHOUSE_FIELDS = {
    'receipt':    ['destination'],
    'issue':      ['source'],
    'return':     ['source'],
    'sale':       ['source'],
    'waste':      ['source'],
    'transfer':   ['source', 'destination'],
    'adjustment': ['source'],
    # «تحویل مستقیم»: انبارِ مقصد = محلِ عبور کالای تازه‌خرید (+ و بلافاصله −)
    'direct_delivery': ['destination'],
}


_VOUCHER_DEPARTMENT_TYPES = {'direct_delivery'}


def _is_manager_user(user) -> bool:
    """بررسی نقش مدیریتی مجاز با لحاظ is_superuser، نقش اصلی و نقش دوم"""
    return bool(
        getattr(user, 'is_superuser', False)
        or getattr(user, 'role', None) in ('manager', 'admin', 'ceo')
        or getattr(user, 'secondary_role', None) in ('manager', 'admin', 'ceo')
    )


def _voucher_datetime_from_input(raw, default_time=None):
    """ورودی تاریخ فرم حواله → (datetime آگاه, معتبر؟)

    ورودی‌های پذیرفته‌شده: تاریخ شمسی ۱۴۰۵/۰۸/۰۵، تاریخ میلادی 2026-10-27 و
    datetime-local قدیمی 2026-10-27T10:30. ساعتِ تاریخ شمسی از default_time
    (ساعتِ حوالهٔ در حال ویرایش) یا ساعتِ اکنون گرفته می‌شود.
    """
    raw = (raw or '').strip()
    base_time = (default_time or timezone.localtime().time()).replace(microsecond=0)

    if not raw:
        return timezone.make_aware(datetime.combine(timezone.localdate(), base_time)), True

    # سازگاری با فرم‌های بازِ قبل از تغییر (datetime-local)
    if 'T' in raw or ' ' in raw:
        try:
            dt = datetime.fromisoformat(raw)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return dt, True
        except ValueError:
            return timezone.make_aware(datetime.combine(timezone.localdate(), base_time)), False

    parsed_date = _parse_jalali_date(raw)
    if parsed_date is None:
        return timezone.make_aware(datetime.combine(timezone.localdate(), base_time)), False
    return timezone.make_aware(datetime.combine(parsed_date, base_time)), True


def _voucher_form_options(cafe, warehouse_id=None):
    """گزینه‌های فرم حواله — فقط داده‌های کافهٔ فعلی"""
    products = (Product.objects.filter(cafe=cafe, is_active=True)
                .select_related('category', 'unit').order_by('category__name', 'name'))
    units = Unit.objects.filter(cafe=cafe)
    # موجودی‌های همهٔ انبارهای کافه بارگذاری می‌شوند تا با انتخاب یا تغییر انبار
    # در سمت کلاینت، موجودی همان انبار بلافاصله نمایش داده شود.
    stock_qs = StockItem.objects.filter(warehouse__cafe=cafe)
    stock_items = (stock_qs.select_related('product', 'unit', 'warehouse')
                   .order_by('warehouse__name', 'product__name'))
    return {
        'products': products,
        'units': units,
        'stock_items': stock_items,
    }


def _parse_voucher_lines(request, cafe, voucher_type):
    """پارس قلم‌های فرم — هر شناسهٔ ارسالی (محصول/موجودی/واحد) فقط در
    کافهٔ فعلی معتبر است؛ خروجی (خطوط معتبر، خطاها)"""
    product_ids = request.POST.getlist('product_id[]')
    stock_ids   = request.POST.getlist('stock_item_id[]')
    unit_ids    = request.POST.getlist('unit_id[]')
    quantities  = request.POST.getlist('base_quantity[]')
    prices      = request.POST.getlist('unit_price[]')
    item_descs  = request.POST.getlist('item_description[]')

    lines = []
    errors = []
    for idx, pid in enumerate(product_ids):
        if not pid:
            continue
        try:
            product = Product.objects.get(id=int(pid), cafe=cafe)
        except (Product.DoesNotExist, ValueError, TypeError):
            errors.append(f'قلم {idx + 1}: محصول نامعتبر یا متعلق به کافهٔ دیگر است')
            continue

        stock_item = None
        if idx < len(stock_ids) and stock_ids[idx]:
            try:
                stock_item = StockItem.objects.select_related('warehouse').get(
                    id=int(stock_ids[idx]), warehouse__cafe=cafe)
            except (StockItem.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: موجودی انبار نامعتبر یا متعلق به کافهٔ دیگر است')
                continue

        unit_obj = None
        if idx < len(unit_ids) and unit_ids[idx]:
            try:
                unit_obj = Unit.objects.get(id=int(unit_ids[idx]), cafe=cafe)
            except (Unit.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: واحد نامعتبر یا متعلق به کافهٔ دیگر است')
                continue
        if unit_obj is None:
            unit_obj = stock_item.unit if stock_item else product.unit

        def _dec(vals, default=None):
            s = _normalize_numeric_str(vals[idx] if idx < len(vals) else '')
            if not s:
                return default
            try:
                return Decimal(s)
            except Exception:
                return 'INVALID'

        base_qty = _dec(quantities)
        if base_qty == 'INVALID':
            errors.append(f'قلم {idx + 1}: مقدار نامعتبر است')
            continue
        if base_qty is None:
            errors.append(f'قلم {idx + 1}: مقدار الزامی است')
            continue
        if base_qty == 0:
            errors.append(f'قلم {idx + 1}: مقدار نباید صفر باشد')
            continue
        if base_qty < 0 and voucher_type != 'adjustment':
            errors.append(f'قلم {idx + 1}: مقدار باید مثبت باشد')
            continue

        price = _dec(prices, default=Decimal('0'))
        if price == 'INVALID':
            price = Decimal('0')

        lines.append({
            'product': product,
            'stock_item': stock_item,
            'unit': unit_obj,
            'base_quantity': base_qty,
            'unit_price': price,
            'description': (item_descs[idx] if idx < len(item_descs) else '').strip(),
        })
    return lines, errors


def _voucher_prefill_items(request, cafe):
    """اقلامِ ارسال‌شده در POST را برای نمایش دوبارهٔ فرم پس از خطا برمی‌گرداند؛
    بدون این کار با هر خطای اعتبارسنجی، همهٔ ردیف‌های واردشده پاک می‌شدند."""
    product_ids = request.POST.getlist('product_id[]')
    stock_ids = request.POST.getlist('stock_item_id[]')
    unit_ids = request.POST.getlist('unit_id[]')
    quantities = request.POST.getlist('base_quantity[]')
    prices = request.POST.getlist('unit_price[]')
    descs = request.POST.getlist('item_description[]')

    def at(vals, idx):
        return vals[idx] if idx < len(vals) else ''

    def as_int(value):
        value = (value or '').strip()
        return int(value) if value.isdigit() else None

    pids = [int(p) for p in product_ids if (p or '').strip().isdigit()]
    names = {str(p.id): p.name for p in Product.objects.filter(cafe=cafe, id__in=pids)}

    items = []
    for idx, pid in enumerate(product_ids):
        pid = (pid or '').strip()
        qty = at(quantities, idx)
        desc = at(descs, idx)
        if not pid and not qty and not desc:
            continue  # ردیف کاملاً خالی
        items.append({
            'product_id': as_int(pid),
            'product_name': names.get(pid, ''),
            'stock_id': as_int(at(stock_ids, idx)),
            'unit_id': as_int(at(unit_ids, idx)),
            'base': qty,
            'price': at(prices, idx),
            'desc': desc,
        })
    return items


def _render_voucher_form(request, cafe, voucher_type, warehouse, extra=None):
    """رندر فرم ایجاد/ویرایش حواله با گزینه‌های کافهٔ فعلی"""
    opts = _voucher_form_options(cafe, warehouse.id if warehouse else None)
    ctx = {
        'voucher_type': voucher_type,
        'type_label': VOUCHER_TYPE_LABELS.get(voucher_type, ''),
        'voucher_types_all': WarehouseVoucher.VOUCHER_TYPE_CHOICES,
        'warehouse': warehouse,
        'warehouses': Warehouse.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'warehouse_fields': VOUCHER_WAREHOUSE_FIELDS.get(voucher_type, []),
        'departments': Department.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'cafe_code': cafe.code if cafe else '',
        'products': opts['products'],
        'units': opts['units'],
        'stock_items': opts['stock_items'],
        'prefill_items': [],
        'form_description': '',
        # تاریخ شمسیِ پیش‌فرضِ باکس تاریخ (همان ظاهر فاکتور نقدی)
        'form_date': _format_jalali_date(timezone.localdate()),
    }
    if extra:
        ctx.update(extra)
    return render(request, 'stock/voucher_create.html', ctx)


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def voucher_list(request: HttpRequest) -> HttpResponse:
    """لیست حواله‌های انبار با فیلترهای کامل (نوع، وضعیت، بازهٔ تاریخ، انبار
    مبدأ/مقصد، محصول، بخش، ایجادکننده، شماره)"""
    cafe = get_cafe_from_request(request)
    vouchers = WarehouseVoucher.objects.select_related(
        'source_warehouse', 'destination_warehouse', 'department',
        'created_by', 'approver',
    ).filter(cafe=cafe)

    f = {
        'type': request.GET.get('type'),
        'status': request.GET.get('status'),
        # مقدار خامِ شمسی برای نمایش در باکس تاریخ؛ میلادی هم سازگار است
        'date_from': _jalali_input_value(request.GET.get('date_from', '')),
        'date_to': _jalali_input_value(request.GET.get('date_to', '')),
        'source_warehouse': request.GET.get('source_warehouse'),
        'dest_warehouse': request.GET.get('dest_warehouse'),
        'product': request.GET.get('product'),
        'department': request.GET.get('department'),
        'creator': request.GET.get('creator'),
        'search': request.GET.get('search', '').strip(),
    }

    if f['type'] in VOUCHER_TYPE_LABELS:
        vouchers = vouchers.filter(voucher_type=f['type'])
    if f['status'] in VOUCHER_STATUS_LABELS:
        vouchers = vouchers.filter(status=f['status'])
    date_from = _parse_jalali_date(f['date_from'])
    if date_from:
        vouchers = vouchers.filter(date__date__gte=date_from)
    date_to = _parse_jalali_date(f['date_to'])
    if date_to:
        vouchers = vouchers.filter(date__date__lte=date_to)
    if f['source_warehouse'] and str(f['source_warehouse']).strip().isdigit():
        vouchers = vouchers.filter(source_warehouse_id=int(f['source_warehouse']))
    if f['dest_warehouse'] and str(f['dest_warehouse']).strip().isdigit():
        vouchers = vouchers.filter(destination_warehouse_id=int(f['dest_warehouse']))
    if f['product'] and str(f['product']).strip().isdigit():
        vouchers = vouchers.filter(items__product_id=int(f['product']))
    if f['department'] and str(f['department']).strip().isdigit():
        dept_id_int = int(f['department'])
        # بخش گیرندهٔ مستقیمِ «تحویل مستقیم» یا بخشِ مرتبط با انبارِ مبدأ/مقصد
        dept_wh_ids = Department.objects.filter(cafe=cafe, id=dept_id_int).values_list('warehouse_id', flat=True)
        vouchers = vouchers.filter(
            Q(department_id=dept_id_int)
            | Q(source_warehouse_id__in=dept_wh_ids)
            | Q(destination_warehouse_id__in=dept_wh_ids))
    if f['creator'] and str(f['creator']).strip().isdigit():
        vouchers = vouchers.filter(created_by_id=int(f['creator']))
    if f['search']:
        vouchers = vouchers.filter(voucher_number__icontains=f['search'])

    vouchers = vouchers.distinct().order_by('-created_at')
    paginator = Paginator(vouchers, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'stock/voucher_list.html', {
        'page_obj': page_obj,
        'warehouses': Warehouse.objects.filter(cafe=cafe),
        'departments': Department.objects.filter(cafe=cafe).order_by('name'),
        'creators': (User.objects.filter(created_warehouse_vouchers__cafe=cafe)
                     .distinct().order_by('username')),
        'products': Product.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'voucher_types': VOUCHER_TYPE_LABELS,
        'statuses': VOUCHER_STATUS_LABELS,
        'f': f,
        'advanced_active': any([
            f['date_from'], f['date_to'], f['source_warehouse'],
            f['dest_warehouse'], f['product'], f['department'], f['creator'],
        ]),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_create(request: HttpRequest, voucher_type: str) -> HttpResponse:
    """فرم یکپارچهٔ ایجاد حواله — همهٔ ۷ نوع در یک فرم؛ انتخاب نوع فیلدهای
    انبارِ مرتبط را نشان می‌دهد و اعتبارسنجی حساس به نوع در سرویس مرکزی است.

    - «ثبت پیش‌نویس» → وضعیت draft (قابل ویرایش/لغو)
    - «ثبت و نهایی‌سازی» → نوع‌های روتین همان لحظه نهایی می‌شوند (اثر فوری مثل
      قبل)؛ تعدیل «ثبت‌شده» می‌ماند تا مدیر تأییدش کند.
    """
    cafe = get_cafe_from_request(request)
    if voucher_type not in VOUCHER_TYPE_LABELS:
        raise Http404('نوع حواله نامعتبر است')

    if request.method == 'POST':
        action = request.POST.get('action', 'submit')
        try:
            src_id = int(request.POST.get('source_warehouse')) if request.POST.get('source_warehouse') else None
            dst_id = int(request.POST.get('dest_warehouse')) if request.POST.get('dest_warehouse') else None
        except (ValueError, TypeError):
            src_id = dst_id = None

        src_wh = get_scoped_object_or_404(Warehouse, request, id=src_id) if src_id else None
        dst_wh = get_scoped_object_or_404(Warehouse, request, id=dst_id) if dst_id else None

        # بخش گیرندهٔ «تحویل مستقیم» — فقط بخش‌های کافهٔ فعلی معتبرند
        dept = None
        if voucher_type in _VOUCHER_DEPARTMENT_TYPES:
            dept_id_str = request.POST.get('department', '').strip()
            if not dept_id_str or not dept_id_str.isdigit():
                messages.error(request, 'بخش گیرنده را انتخاب کنید')
                return _render_voucher_form(
                    request, cafe, voucher_type, src_wh or dst_wh,
                    {'form_errors': ['بخش گیرنده الزامی است'],
                     'prefill_items': _voucher_prefill_items(request, cafe),
                     'form_description': request.POST.get('description', ''),
                     'form_date': _jalali_input_value(request.POST.get('date', '')),
                     'form_source_wh': src_wh, 'form_dest_wh': dst_wh})
            dept = get_scoped_object_or_404(Department, request, id=int(dept_id_str))

        lines, errors = _parse_voucher_lines(request, cafe, voucher_type)
        if not lines and not errors:
            errors.append('حداقل یک قلم کالا وارد کنید')

        # تاریخ فرم شمسی است؛ ساعتِ سند از لحظهٔ ثبت گرفته می‌شود
        date_str = request.POST.get('date', '').strip()
        vdate, date_ok = _voucher_datetime_from_input(date_str)
        if not date_ok:
            errors.append('تاریخ واردشده معتبر نیست — نمونهٔ درست: ۱۴۰۵/۰۸/۰۵')

        if errors:
            for e in errors:
                messages.error(request, e)
            return _render_voucher_form(request, cafe, voucher_type, src_wh or dst_wh,
                                        {'form_errors': errors,
                                         'selected_department': dept,
                                         'prefill_items': _voucher_prefill_items(request, cafe),
                                         'form_description': request.POST.get('description', ''),
                                         'form_date': _jalali_input_value(date_str),
                                         'form_source_wh': src_wh, 'form_dest_wh': dst_wh})

        description = request.POST.get('description', '').strip()

        try:
            with transaction.atomic():
                voucher = WarehouseVoucher(
                    cafe=cafe,
                    voucher_type=voucher_type,
                    status='draft' if action == 'draft' else 'submitted',
                    voucher_number=next_voucher_number(cafe.id, voucher_type),
                    source_warehouse=src_wh,
                    destination_warehouse=dst_wh,
                    department=dept,
                    date=vdate,
                    description=description,
                    created_by=request.user,
                )
                try:
                    voucher.full_clean(exclude=['voucher_number', 'status', 'created_by', 'approver', 'approved_at'])
                except ValidationError as exc:
                    raise VoucherValidationError(f'سند ناسازگار است: {exc}')
                save_voucher_with_number(voucher, cafe.id, voucher_type)

                for line in lines:
                    WarehouseVoucherItem.objects.create(voucher=voucher, **line)

                if action != 'draft':
                    if voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES:
                        # ثبت شد؛ نهایی‌سازی بعد از تأیید مدیر
                        pass
                    else:
                        finalize_voucher(voucher, finalized_by=request.user)
        except InventoryServiceError as exc:
            messages.error(request, f'حواله ثبت نشد: {exc}')
            return _render_voucher_form(request, cafe, voucher_type, src_wh or dst_wh,
                                        {'form_errors': [str(exc)],
                                         'prefill_items': _voucher_prefill_items(request, cafe),
                                         'form_description': request.POST.get('description', ''),
                                         'form_date': _jalali_input_value(request.POST.get('date', '')),
                                         'form_source_wh': src_wh, 'form_dest_wh': dst_wh,
                                         'selected_department': dept})

        if action == 'draft':
            messages.success(request, f'پیش‌نویس حواله {voucher.voucher_number} ثبت شد.')
        elif voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES:
            messages.success(request, f'حوالهٔ {voucher.voucher_number} ثبت شد و در انتظار تأیید مدیر است.')
        elif voucher_type == 'direct_delivery':
            _dept_name = dept.name if dept else '—'
            messages.success(
                request,
                f'حوالهٔ {voucher.voucher_number} نهایی شد؛ کالا به «{_dept_name}» تحویل داده شد '
                f'(در سابقهٔ موجودیِ انبار، ورود و خروجِ آن ثبت شده).')
        else:
            messages.success(request, f'حوالهٔ {voucher.voucher_number} نهایی‌سازی شد و اثر آن روی موجودی اعمال شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return _render_voucher_form(request, cafe, voucher_type, None)


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting', 'warehouse'])
def voucher_detail(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """جزئیات حواله: سند، اقلام، اثر روی موجودی، خط زمانی، سندِ منبع و اقدامات"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    items = voucher.items.select_related(
        'product', 'unit', 'stock_item__warehouse').all()
    movements = StockTransaction.objects.filter(voucher=voucher).select_related(
        'stock_item__product', 'stock_item__warehouse', 'created_by').order_by('created_at')

    is_manager = _is_manager_user(request.user)
    return render(request, 'stock/voucher_detail.html', {
        'voucher': voucher,
        'items': items,
        'movements': movements,
        'can_edit': (voucher.status == 'draft'
                     and (voucher.created_by_id == request.user.id or is_manager)),
        'can_cancel': voucher.status in ('draft', 'submitted'),
        'can_approve': (is_manager and voucher.status == 'submitted'
                        and (voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                             or voucher.requires_approval)),
        'can_reverse': (voucher.status == 'finalized' and not voucher.reversed_at
                        and not voucher.reverses_id),
        'tx_type_labels': dict(StockTransaction.TRANSACTION_TYPE_CHOICES),
        'approval_pending': (voucher.status == 'submitted'
                             and (voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                                  or voucher.requires_approval)),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_edit(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """ویرایش پیش‌نویس حواله — فقط draft و فقط توسط ایجادکننده (یا مدیر)"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    is_manager = _is_manager_user(request.user)
    if voucher.status != 'draft':
        messages.error(request, 'فقط پیش‌نویس قابل ویرایش است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    if voucher.created_by_id != request.user.id and not is_manager:
        raise Http404('فقط ایجادکنندهٔ پیش‌نویس می‌تواند آن را ویرایش کند')

    if request.method == 'POST':
        action = request.POST.get('action', 'draft')
        quick_submit = request.POST.get('quick_submit') == '1'

        if quick_submit and action == 'submit':
            try:
                with transaction.atomic():
                    locked_voucher = get_scoped_locked_object_or_404(WarehouseVoucher, request, id=voucher_id)
                    if locked_voucher.status != 'draft':
                        messages.error(request, 'فقط پیش‌نویس قابل ویرایش است.')
                        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=locked_voucher.id)
                    if not locked_voucher.items.exists():
                        messages.error(request, 'حداقل یک قلم کالا وارد کنید')
                        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=locked_voucher.id)
                    locked_voucher.status = 'submitted'
                    locked_voucher.full_clean(
                        exclude=['voucher_number', 'status', 'created_by', 'approver', 'approved_at']
                    )
                    locked_voucher.save()
                    if not (locked_voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                            or locked_voucher.requires_approval):
                        finalize_voucher(locked_voucher, finalized_by=request.user)
            except (ValidationError, InventoryServiceError) as exc:
                messages.error(request, f'حواله ثبت نشد: {exc}')
                return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

            if (locked_voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                    or locked_voucher.requires_approval):
                messages.success(request, f'حوالهٔ {locked_voucher.voucher_number} ثبت شد و در انتظار تأیید مدیر است.')
            else:
                messages.success(request, f'حوالهٔ {locked_voucher.voucher_number} نهایی‌سازی شد و اثر آن روی موجودی اعمال شد.')
            return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

        try:
            src_id = int(request.POST.get('source_warehouse')) if request.POST.get('source_warehouse') else None
            dst_id = int(request.POST.get('dest_warehouse')) if request.POST.get('dest_warehouse') else None
        except (ValueError, TypeError):
            src_id = dst_id = None
        src_wh = get_scoped_object_or_404(Warehouse, request, id=src_id) if src_id else None
        dst_wh = get_scoped_object_or_404(Warehouse, request, id=dst_id) if dst_id else None

        dept = voucher.department  # پیش‌فرض: نگه‌داشتن بخشِ قبلی پیش‌نویس
        if voucher.voucher_type in _VOUCHER_DEPARTMENT_TYPES:
            dept_id_str = request.POST.get('department', '').strip()
            if not dept_id_str or not dept_id_str.isdigit():
                messages.error(request, 'بخش گیرنده را انتخاب کنید')
                return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)
            dept = get_scoped_object_or_404(Department, request, id=int(dept_id_str))

        lines, errors = _parse_voucher_lines(request, cafe, voucher.voucher_type)
        if not lines and not errors:
            errors.append('حداقل یک قلم کالا وارد کنید')
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)

        # تاریخ شمسی است؛ ساعتِ قبلیِ پیش‌نویس حفظ می‌شود
        date_str = request.POST.get('date', '').strip()
        previous_time = (timezone.localtime(voucher.date).time()
                         if timezone.is_aware(voucher.date) else voucher.date.time())
        vdate, date_ok = _voucher_datetime_from_input(date_str, default_time=previous_time)
        if not date_ok:
            messages.error(request, 'تاریخ واردشده معتبر نیست — نمونهٔ درست: ۱۴۰۵/۰۸/۰۵')
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)

        try:
            with transaction.atomic():
                locked_voucher = get_scoped_locked_object_or_404(WarehouseVoucher, request, id=voucher_id)
                if locked_voucher.status != 'draft':
                    messages.error(request, 'فقط پیش‌نویس قابل ویرایش است.')
                    return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=locked_voucher.id)
                locked_voucher.source_warehouse = src_wh
                locked_voucher.destination_warehouse = dst_wh
                locked_voucher.department = dept
                locked_voucher.date = vdate
                locked_voucher.description = request.POST.get('description', '').strip()
                if action == 'submit':
                    locked_voucher.status = 'submitted'
                locked_voucher.full_clean(
                    exclude=['voucher_number', 'status', 'created_by', 'approver', 'approved_at']
                )
                locked_voucher.save()
                locked_voucher.items.all().delete()
                for line in lines:
                    WarehouseVoucherItem.objects.create(voucher=locked_voucher, **line)
                if action == 'submit':
                    if not (locked_voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                            or locked_voucher.requires_approval):
                        finalize_voucher(locked_voucher, finalized_by=request.user)
        except ValidationError as exc:
            messages.error(request, f'سند ناسازگار است: {exc}')
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)
        except InventoryServiceError as exc:
            messages.error(request, f'حواله ثبت نشد: {exc}')
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)

        if action == 'submit':
            if (locked_voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                    or locked_voucher.requires_approval):
                messages.success(request, f'حوالهٔ {voucher.voucher_number} ثبت شد و در انتظار تأیید مدیر است.')
            elif locked_voucher.voucher_type == 'direct_delivery':
                _dept_name = dept.name if dept else '—'
                messages.success(
                    request,
                    f'حوالهٔ {voucher.voucher_number} نهایی شد؛ کالا به «{_dept_name}» تحویل داده شد.')
            else:
                messages.success(request, f'حوالهٔ {voucher.voucher_number} نهایی‌سازی شد و اثر آن روی موجودی اعمال شد.')
        else:
            messages.success(request, f'پیش‌نویس {voucher.voucher_number} به‌روزرسانی شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    items = voucher.items.select_related('product', 'unit').all()
    return _render_voucher_form(request, cafe, voucher.voucher_type,
                                voucher.source_warehouse or voucher.destination_warehouse,
                                {'editing': True, 'edit_voucher': voucher,
                                 'edit_items': items,
                                 'form_date': _format_jalali_date(voucher.date),
                                 'edit_source_wh': voucher.source_warehouse,
                                 'edit_dest_wh': voucher.destination_warehouse,
                                 'selected_department': voucher.department})


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_cancel(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """لغو پیش‌نویس یا حوالهٔ ثبت‌شده (پیش از نهایی‌سازی)"""
    cafe = get_cafe_from_request(request)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'متد نامعتبر'}, status=405)
    with transaction.atomic():
        voucher = get_scoped_locked_object_or_404(WarehouseVoucher, request, id=voucher_id)
        if voucher.status not in ('draft', 'submitted'):
            messages.error(request, 'فقط پیش‌نویس یا حوالهٔ ثبت‌شدهٔ نهایی‌نشده قابل لغو است.')
            return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
        voucher.status = 'cancelled'
        voucher.cancelled_at = timezone.now()
        voucher.save()
    messages.success(request, f'حوالهٔ {voucher.voucher_number} لغو شد.')
    return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)


@custom_login_required
@role_required(['manager', 'admin', 'ceo'])
def voucher_approve(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """تأیید و نهایی‌سازی حوالهٔ نیازمندِ تأیید (تعدیل) — اقدامِ مدیر = تأیید + اثر فوری"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)

    if request.method == 'POST':
        try:
            with transaction.atomic():
                v = get_scoped_locked_object_or_404(WarehouseVoucher, request, id=voucher_id)
                if v.status != 'submitted' or not (
                        v.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                        or v.requires_approval):
                    messages.error(request, 'این حواله در وضعیت نیازمند تأیید نیست.')
                    return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=v.id)
                v.status = 'approved'
                v.approver = request.user
                v.approved_at = timezone.now()
                v.save()
                finalize_voucher(v, finalized_by=request.user)
        except InventoryServiceError as exc:
            messages.error(request, f'تأیید انجام نشد: {exc}')
            return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
        messages.success(request, f'حوالهٔ {voucher.voucher_number} تأیید و نهایی‌سازی شد — اثر روی موجودی اعمال شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    if voucher.status != 'submitted' or not (
            voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
            or voucher.requires_approval):
        messages.error(request, 'این حواله در وضعیت نیازمند تأیید نیست.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return render(request, 'stock/voucher_approve.html', {
        'voucher': voucher,
        'items': voucher.items.select_related('product', 'unit').all(),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'warehouse'])
def voucher_reverse(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """برگشت حوالهٔ نهایی‌شده — دلیلِ اجباری؛ حوالهٔ آینه‌ای ساخته و نهایی می‌شود"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    if (voucher.voucher_type == 'adjustment' or voucher.requires_approval) and not _is_manager_user(request.user):
        return HttpResponseForbidden('برگشت حواله تعدیل فقط توسط مدیر مجاز است.')
    if voucher.reverses_id:
        messages.error(request, 'حوالهٔ برگشتی قابل برگشت نیست — حوالهٔ اصلی را برگشت بزنید.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    if voucher.status != 'finalized':
        messages.error(request, 'فقط حوالهٔ نهایی‌شده قابل برگشت است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'دلیل برگشت الزامی است — بدون دلیل برگشت ثبت نمی‌شود.')
            return render(request, 'stock/voucher_reverse.html', {
                'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})
        try:
            reversal = reverse_voucher(voucher, created_by=request.user, description=reason)
        except InventoryServiceError as exc:
            messages.error(request, f'برگشت انجام نشد: {exc}')
            return render(request, 'stock/voucher_reverse.html', {
                'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})
        messages.success(request, f'حوالهٔ {voucher.voucher_number} برگشت خورد — حوالهٔ برگشتی {reversal.voucher.voucher_number} نهایی شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return render(request, 'stock/voucher_reverse.html', {
        'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})
