from __future__ import annotations

from ..middleware import get_cafe_code
from ..models import Cafe
from ..models import OfficerAssignment
from ..models import Payment
from ..models import User
from ..models import WarehouseVoucher
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import ROUND_HALF_UP
from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from functools import wraps
import jdatetime


def cafe_view(view_func):
    """
    Decorator که پارامتر cafe_code را از URL می‌گیرد و نادیده می‌گیرد.
    اطلاعات کافه از middleware در request.cafe قرار دارد.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        kwargs.pop('cafe_code', None)
        return view_func(request, *args, **kwargs)
    return wrapper


def get_cafe_from_request(request):
    """گرفتن کافه فعلی از request یا session (فقط کافه‌های فعال و مجاز برای کاربر)"""
    cafe = getattr(request, 'cafe', None)
    if cafe and getattr(cafe, 'is_active', False):
        return cafe

    cafe_id = request.session.get('selected_cafe_id') if hasattr(request, 'session') else None
    if cafe_id:
        try:
            session_cafe = Cafe.objects.get(id=cafe_id, is_active=True)
        except Cafe.DoesNotExist:
            return None
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            from ..middleware import _user_can_access_cafe
            if not _user_can_access_cafe(user, session_cafe):
                return None
        return session_cafe
    return None


CAFE_LOOKUP_PATHS = {
    'WarehouseVoucher':     'cafe',
    'WarehouseVoucherItem': 'voucher__cafe',
    'StockItem':            'warehouse__cafe',
    'StockTransaction':     'stock_item__warehouse__cafe',
    'Warehouse':            'cafe',
    'Product':              'cafe',
    'Unit':                 'cafe',
    'Category':             'cafe',
    'Department':           'cafe',
    'Supplier':             'cafe',
    'Requester':            'cafe',
    'Invoice':              'cafe',
    'InvoiceItem':          'invoice__cafe',
    'PurchaseRequest':      'cafe',
    'PurchaseItem':         'request__cafe',
    'PurchaseOrder':        'request__cafe',
    'WarehouseRequest':     'cafe',
    'WarehouseRequestItem': 'warehouse_request__cafe',
    'OfficerAssignment':    'item__request__cafe',
    'StockRequest':         'purchase_item__request__cafe',
    'StockRequestItem':     'stock_request__purchase_item__request__cafe',
    'PurchaseDraft':        'cafe',
    'PurchaseDraftItem':    'draft__cafe',
    'KukerItem':            'cafe',
    'MenuSection':          'cafe',
    'CogsReport':           'cafe',
    'KukerItemCostHistory': 'item__cafe',
    'Debt':                 'cafe',
    'DebtPayment':          'cafe',
    'PaymentAccount':       'cafe',
    'PaymentLocation':      'cafe',
    'PaymentType':          'cafe',
    'Notification':         'cafe',
    'InvoiceIssue':         'cafe',
    'AssetCategory':        'cafe',
    'AssetCatalog':         'cafe',
    'DepartmentAssetStock': 'cafe',
    'AssetUnit':            'cafe',
    'AssetMovement':        'cafe',
    'AssetAudit':           'cafe',
    'AssetAuditItem':       'audit__cafe',
}


ASSET_MANAGER_ROLES = frozenset({'admin', 'manager', 'ceo', 'warehouse'})


def user_can_manage_assets(user) -> bool:
    """آیا کاربر مجاز به مدیریت کامل اموال است (admin, manager, ceo, warehouse, superuser)."""
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return (
        getattr(user, 'role', None) in ASSET_MANAGER_ROLES
        or getattr(user, 'secondary_role', None) in ASSET_MANAGER_ROLES
    )


def user_can_access_department_assets(user, department) -> bool:
    """آیا کاربر مجاز به دسترسی به اموال بخش مشخص‌شده است (مدیران همان کافه یا requester همان بخش)."""
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or not getattr(user, 'is_active', False)
        or department is None
    ):
        return False

    is_global = getattr(user, 'is_superuser', False) or (
        getattr(user, 'cafe_id', None) is None
        and getattr(user, 'role', None) in User.CAFE_SELECTION_ROLES
    )
    if not is_global and getattr(user, 'cafe_id', None) != department.cafe_id:
        return False

    if user_can_manage_assets(user):
        return True

    if getattr(user, 'role', None) == 'requester' or getattr(user, 'secondary_role', None) == 'requester':
        return getattr(user, 'department_id', None) == department.id

    return False


def resolve_api_cafe(request, cafe_code=None):
    """Resolve and authorize active Cafe for DRF/API views (fail-closed).

    Returns ``(cafe, error_status, error_message)``.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None, 401, 'احراز هویت الزامی است.'
    if not user.is_active:
        return None, 403, 'حساب کاربری غیرفعال است.'

    if cafe_code is None:
        resolver_match = getattr(request, 'resolver_match', None)
        if resolver_match is not None:
            cafe_code = resolver_match.kwargs.get('cafe_code')

    if cafe_code:
        cafe = Cafe.objects.filter(code=cafe_code, is_active=True).first()
        if cafe is None:
            return None, 404, 'کافه یافت نشد.'
    else:
        cafe = get_cafe_from_request(request)
        if cafe is None:
            return None, 403, 'کافهٔ فعال مشخص نشده است.'

    is_global_admin = (
        user.is_superuser
        or (user.cafe_id is None and getattr(user, 'role', None) in User.CAFE_SELECTION_ROLES)
    )
    if not is_global_admin and user.cafe_id != cafe.id:
        return None, 403, 'شما به این کافه دسترسی ندارید.'

    return cafe, None, None


def get_scoped_object_or_404(model, request, **lookup):
    """دریافت یک رکورد با قفلِ کافه‌محور — بدون کافهٔ فعال همیشه 404 (fail-closed)."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    path = CAFE_LOOKUP_PATHS.get(model.__name__)
    if path is None:
        raise Http404(f'مدل {model.__name__} در CAFE_LOOKUP_PATHS نیست')
    lookup[path] = cafe
    return get_object_or_404(model, **lookup)


def filter_by_cafe(queryset, request):
    """فیلتر کردن queryset بر اساس کافهٔ فعلی — شکستِ بسته"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return queryset.none()
    path = CAFE_LOOKUP_PATHS.get(queryset.model.__name__)
    if path is None:
        return queryset.none()
    return queryset.filter(**{path: cafe})


def get_scoped_locked_object_or_404(model, request, **lookup):
    """مثل get_scoped_object_or_404، ولی ردیف را با select_for_update قفل می‌کند."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    path = CAFE_LOOKUP_PATHS.get(model.__name__)
    if path is None:
        raise Http404(f'مدل {model.__name__} در CAFE_LOOKUP_PATHS نیست')
    lookup[path] = cafe
    return get_object_or_404(model.objects.select_for_update(), **lookup)


def get_cafe_redirect_url(url_name, request, **kwargs):
    """ساخت URL با cafe_code"""
    cafe_code = get_cafe_code(request)
    if cafe_code:
        kwargs['cafe_code'] = cafe_code
    return redirect(url_name, **kwargs)


def get_cafe_url(url_name, request, **kwargs):
    """مثل get_cafe_redirect_url ولی رشتهٔ URL برمیگرداند"""
    cafe_code = get_cafe_code(request)
    if cafe_code:
        kwargs['cafe_code'] = cafe_code
    return reverse(url_name, kwargs=kwargs)


def cafe_context(request):
    """اضافه کردن اطلاعات کافه به context"""
    cafe = get_cafe_from_request(request)
    return {
        'current_cafe': cafe,
        'cafe_code': cafe.code if cafe else None,
        'can_switch_cafe': request.user.is_authenticated and request.user.can_switch_cafe,
        'all_cafes': Cafe.objects.filter(is_active=True) if request.user.is_authenticated and request.user.can_switch_cafe else [],
    }


def custom_login_required(view_func):
    """Wrapper around login_required to keep a single login URL."""
    return login_required(view_func, login_url="login")


def _is_ajax_or_api_request(request):
    """تشخیص درخواست‌های AJAX / JSON / API برای برگرداندن پاسخ JSON به جای HTML."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.headers.get('Accept') or ''
    if 'application/json' in accept and 'text/html' not in accept:
        return True
    path = getattr(request, 'path', '') or ''
    return '/api/' in path


def role_required(roles):
    """
    Simple role-based access control.
    کاربران با نقش 'admin' و superuser ها به همه صفحات دسترسی دارند.
    کاربر احراز هویت‌شده‌ای که نقش مجاز ندارد، ۴۰۳ (یا JSON 403 برای AJAX/API) می‌گیرد.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if _is_ajax_or_api_request(request):
                    return JsonResponse({'error': 'احراز هویت الزامی است.'}, status=401)
                return redirect("login")

            if not request.user.is_active:
                logout(request)
                if _is_ajax_or_api_request(request):
                    return JsonResponse({'error': 'حساب کاربری غیرفعال است.'}, status=403)
                return redirect("login")

            user_role = getattr(request.user, "role", None)
            user_secondary_role = getattr(request.user, "secondary_role", None)
            if not (user_role == 'admin' or request.user.is_superuser):
                if roles and user_role not in roles:
                    if not (user_secondary_role and user_secondary_role in roles):
                        if _is_ajax_or_api_request(request):
                            return JsonResponse(
                                {'error': 'شما مجاز به انجام این عملیات نیستید.'},
                                status=403,
                            )
                        return HttpResponseForbidden('شما مجاز به دسترسی به این صفحه نیستید.')

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


_NUM_TRANS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫', '01234567890123456789.')


def _normalize_numeric_str(value):
    return str(value).translate(_NUM_TRANS).replace(",", "").replace("،", "").replace("٬", "").strip()


def _parse_decimal(value):
    if value is None:
        return None
    text = _normalize_numeric_str(value)
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _fmt2(value):
    """نمایش عددی موجودی: حداکثر ۲ رقم اعشار؛ رقم‌های صفرِ آخر حذف می‌شوند."""
    try:
        d = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return format(d.normalize(), 'f')
    except (ValueError, TypeError, ArithmeticError):
        return str(value)


def _warehouse_request_status(statuses):
    """وضعیت نهایی درخواست انبار از وضعیت اقلام آن."""
    statuses = set(statuses)
    if not statuses:
        return 'pending'
    if statuses == {'from_stock'}:
        return 'approved_from_stock'
    if statuses == {'to_purchase'}:
        return 'approved_purchase'
    if statuses == {'declined'}:
        return 'declined'
    return 'partial'


def _parse_iso_date(value):
    """پارس تاریخ ISO (YYYY-MM-DD یا YYYY/MM/DD)؛ در صورت نامعتبر None"""
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


_PERSIAN_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def _parse_invoice_date(raw_value):
    """پارس تاریخ شمسی (1404/05/15) یا میلادی (2026-08-06) به date میلادی."""
    if not raw_value:
        return None
    s = str(raw_value).strip().translate(_PERSIAN_DIGITS)
    if not s:
        return None
    sep = '/' if '/' in s else ('-' if '-' in s else None)
    if sep:
        parts = s.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            try:
                if y < 1700:
                    return jdatetime.date(y, m, d).togregorian()
                return date(y, m, d)
            except (ValueError, OverflowError):
                return None
    try:
        return datetime.fromisoformat(s).date()
    except (ValueError, TypeError):
        return None


def _allocate_payment_across_invoices(
    *,
    invoices_list,
    total_amount: Decimal,
    payment_date,
    payment_location,
    reference_number: str,
    notes: str,
    created_by,
):
    """ثبت پرداخت تفکیک‌شده به‌ازای هر فاکتور با مبلغ تخصیص‌یافتهٔ واقعی (بدون دوباره‌حساب‌کردن مبلغ کل و بدون پرداخت روی فاکتور تسویه‌شده)."""
    remaining_pool = total_amount
    created_payments = []
    allocated_invoices = []

    unsettled_invoices = [
        inv for inv in invoices_list
        if Decimal(str(inv.remaining_amount or 0)) > 0
    ]

    for idx, inv in enumerate(unsettled_invoices):
        if remaining_pool <= 0:
            break
        inv_remaining = Decimal(str(inv.remaining_amount or 0))
        if idx == len(unsettled_invoices) - 1:
            alloc = remaining_pool
        else:
            alloc = min(remaining_pool, inv_remaining)
        if alloc <= 0:
            continue

        payment = Payment.objects.create(
            invoice=inv,
            amount=alloc,
            payment_date=payment_date,
            payment_location=payment_location,
            reference_number=reference_number,
            notes=notes,
            created_by=created_by,
            payment_status='completed',
        )
        inv.update_payment_status()
        created_payments.append(payment)
        allocated_invoices.append(inv)
        remaining_pool -= alloc

    return created_payments, allocated_invoices, remaining_pool


def _parse_jalali_date(raw):
    """تبدیل تاریخ شمسی (۱۴۰۵/۰۸/۰۵) یا میلادی (2026-10-27) به date میلادی.

    مقدار خالی یا نامعتبر → None. تاریخ‌های اسلش‌دار همیشه شمسی در نظر
    گرفته می‌شوند (مثل سایر فرم‌های پروژه).
    """
    raw = (raw or '').strip()
    if not raw:
        return None
    if '/' in raw:
        parts = raw.split('/')
        if len(parts) != 3:
            return None
        try:
            import jdatetime
            year, month, day = (int(p) for p in parts)
            return jdatetime.date(year, month, day).togregorian()
        except (ValueError, TypeError):
            return None
    from django.utils.dateparse import parse_date
    return parse_date(raw)


def _format_jalali_date(value):
    """date/datetime میلادی → رشتهٔ شمسی YYYY/MM/DD برای مقدار باکس تاریخ."""
    import jdatetime
    if isinstance(value, datetime):
        value = (timezone.localtime(value).date()
                 if timezone.is_aware(value) else value.date())
    if not value:
        return ''
    j = jdatetime.date.fromgregorian(date=value)
    return f'{j.year}/{j.month:02d}/{j.day:02d}'


def _jalali_input_value(raw):
    """مقدار نمایشی باکس تاریخ: شمسی می‌ماند، میلادی به شمسی تبدیل می‌شود."""
    raw = (raw or '').strip()
    if not raw or '/' in raw:
        return raw
    parsed = _parse_jalali_date(raw)
    if parsed is None and ('T' in raw or ' ' in raw):
        # سازگاری با مقدار datetime-local فرم‌های بازِ قبل از تغییر
        try:
            parsed = datetime.fromisoformat(raw).date()
        except ValueError:
            parsed = None
    return _format_jalali_date(parsed) if parsed else raw


VOUCHER_TYPE_LABELS = dict(WarehouseVoucher.VOUCHER_TYPE_CHOICES)


VOUCHER_STATUS_LABELS = dict(WarehouseVoucher.STATUS_CHOICES)


def _is_backup_authorized(user) -> bool:
    """Only superusers or global admins (cafe=None, role='admin') can access backups/Drive."""
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and (user.is_superuser or (user.cafe is None and getattr(user, 'role', None) == 'admin'))
    )


def _backup_admin_required(view_func):
    """Enforce superuser/global-admin authorization, returning HTTP 403 for single-cafe managers."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_active:
            logout(request)
            return redirect('login')
        if not _is_backup_authorized(request.user):
            return HttpResponseForbidden('دسترسی فقط برای سوپرادمین یا ادمین کل مجاز است.')
        return view_func(request, *args, **kwargs)

    return _wrapped


def _human_size(num_bytes):
    """Format a byte count into a human-readable string."""
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{int(size)} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def _is_sqlite_engine():
    return settings.DATABASES['default']['ENGINE'].endswith('sqlite3')


def _received_delivery_rows(user, cafe):
    """اقلامِ تحویل‌گرفتهٔ یک کاربر از انبار — با مقدار و واحدِ هر تحویل.

    منبعِ «تحویل»: تخصیص‌های تامین‌شدهٔ مسئول انبار (OfficerAssignment.status='fulfilled')
    روی اقلامِ درخواستِ خودِ کاربر. مقدارِ تامین‌شده در واحدِ پایه ذخیره می‌شود تا
    مقدارِ نمایش‌داده‌شده همان چیزی باشد که تحویل داده شده. بازگشاییِ قلم
    تخصیص‌ها را پاک می‌کند، پس این فهرست همیشه وضعیتِ جاری است.
    """
    assigns = (OfficerAssignment.objects
               .filter(target_role='warehouse', status='fulfilled',
                       item__request__user=user)
               .select_related('item', 'item__product', 'item__request',
                               'stock_item', 'stock_item__unit',
                               'stock_item__warehouse')
               .order_by('item__request__created_at', 'item_id', 'id'))
    if cafe:
        assigns = assigns.filter(item__request__cafe=cafe)

    rows = {}
    for a in assigns:
        item = a.item
        row = rows.get(item.id)
        if row is None:
            row = rows[item.id] = {
                'item': item,
                'name': item.product.name if item.product else item.name,
                'requested_qty': _fmt2(item.quantity),
                'unit': item.unit or '',
                'request': item.request,
                'deliveries': [],
                'confirmed_at': item.received_confirmed_at,
                'confirmed_by': item.received_confirmed_by,
            }
        si = a.stock_item
        qty = a.supplied_quantity if a.supplied_quantity is not None else Decimal('0')
        unit_label = (((si.unit.name or si.unit.symbol) if si and si.unit else '')
                      or row['unit'])
        row['deliveries'].append({
            'qty': _fmt2(qty),
            'unit': unit_label,
            'warehouse': si.warehouse.name if si and si.warehouse else '',
        })
    return list(rows.values())


def _next_delivery_days():
    """روزهای قابل انتخاب زمان تحویل: ۷ روز آینده (از فردا) فقط یکشنبه تا پنجشنبه
    — جمعه و شنبه نمایش داده نمی‌شوند — به‌صورت (تاریخ، نام روز هفته)"""
    from datetime import timedelta
    from django.utils import timezone as _tz
    weekday_names = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']
    today = _tz.localtime(_tz.now()).date()  # ساعت تهران
    days = []
    for offset in range(1, 8):  # ۷ روز آینده از فردا
        d = today + timedelta(days=offset)
        if d.weekday() in (4, 5):  # جمعه و شنبه قابل سفارش نیستند
            continue
        days.append((d, weekday_names[d.weekday()]))
    return days


def _item_section_name(user):
    """نام بخشِ قلم درخواست بر اساس کاربر درخواست‌دهنده"""
    if user and user.role == 'requester' and user.department:
        return user.department.name
    dept_names = {
        'kitchen': 'آشپزخانه', 'bar': 'بار', 'counter': 'کانتر',
        'cashier': 'صندوق', 'facilities': 'تاسیسات', 'store': 'انبار',
        'purchasing': 'خرید', 'warehouse': 'انبارداری', 'manager': 'مدیر',
        'admin': 'ادمین', 'ceo': 'مدیرعامل', 'accounting': 'حسابداری',
        'finance_manager': 'مدیر مالی',
    }
    return dept_names.get(getattr(user, 'role', ''), getattr(user, 'role', '—'))


def _product_key(item, include_description=False):
    """کلید ادغام کالا: شناسه محصول اگر موجود باشد وگرنه نام کوچک‌شده.

    اگر include_description=True باشد، توضیحات قلم هم جزئی از کلید می‌شود تا
    اقلام همنام با توضیحات متفاوت به‌عنوان گزینه‌های جدا نمایش داده شوند.
    """
    if item.product_id:
        key = f"product_{item.product_id}"
    else:
        key = (item.name or '').strip().lower()
    if include_description:
        desc = (getattr(item, 'description', '') or '').strip()
        if desc:
            key += f"|desc:{desc}"
    return key
