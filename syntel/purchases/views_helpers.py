"""
Common view helpers, decorators, and multi-tenant scoping utilities.
"""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from .middleware import get_cafe_code
from .models import Cafe, User


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
    """گرفتن کافه فعلی از request یا session (فقط کافه‌های فعال)"""
    cafe = getattr(request, 'cafe', None)
    if cafe and getattr(cafe, 'is_active', False):
        return cafe

    cafe_id = request.session.get('selected_cafe_id') if hasattr(request, 'session') else None
    if cafe_id:
        try:
            return Cafe.objects.get(id=cafe_id, is_active=True)
        except Cafe.DoesNotExist:
            return None
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
}


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


def _get_departments():
    return User.ROLE_CHOICES


def _parse_decimal(value):
    try:
        return Decimal(str(value).replace(",", ""))
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


class _InsufficientStock(Exception):
    """موجودی انبار برای کسر درخواستی کافی نیست"""
