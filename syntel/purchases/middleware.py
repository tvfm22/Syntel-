"""
Middleware برای مدیریت انتخاب و دسترسی به کافه (Multi-Tenant)
"""
from django.http import Http404
from django.shortcuts import redirect
from django.urls import Resolver404, resolve


def _clear_cafe_session(request):
    request.session.pop("selected_cafe_id", None)
    request.session.pop("selected_cafe_code", None)


def _user_can_access_cafe(user, cafe):
    """Return True if authenticated `user` is authorized to access active `cafe`."""
    if not user or not user.is_authenticated or not cafe or not cafe.is_active:
        return False
    if user.is_superuser:
        return True
    if user.cafe is None and user.role in CafeMiddleware.CAFE_SELECTION_ROLES:
        return True
    return user.cafe_id == cafe.id


class CafeMiddleware:
    """
    Middleware برای مدیریت انتخاب و دسترسی به کافه
    - کاربران عادی فقط به کافه خودشان دسترسی دارند
    - کاربران ادمین (admin, manager, finance_manager, accounting, ceo) با cafe=None و superuser ها باید ابتدا کافه را انتخاب کنند
    - فقط superuser و کاربران با cafe=None و نقش مدیریتی می‌توانند بین کافه‌ها جابجا شوند
    """

    # مسیرهایی که نیاز به کافه ندارند
    EXEMPT_PATHS = [
        "/login/",
        "/logout/",
        "/select-cafe/",
        "/admin/",
        "/static/",
        "/media/",
        "/health/",
        "/accounts/login/",
        "/api/mobile/",
        "/backup/drive/callback/",
    ]

    # Role هایی که باید کافه انتخاب کنند
    CAFE_SELECTION_ROLES = ["admin", "manager", "finance_manager", "accounting", "ceo"]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .models import Cafe

        path = request.path
        if any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS) or "/api/mobile/" in path:
            return self.get_response(request)

        # اگر کاربر لاگین نیست، اجازه بده ادامه بده (login_required خودش handle می‌کنه)
        if not request.user.is_authenticated:
            return self.get_response(request)

        cafe_code = self._extract_cafe_code(request)

        if cafe_code:
            cafe = self._validate_cafe_access(request, cafe_code, Cafe)
            if cafe is None:
                if request.user.is_superuser or request.user.needs_cafe_selection:
                    _clear_cafe_session(request)
                    return redirect("select_cafe")
                _clear_cafe_session(request)
                raise Http404("کافه یافت نشد یا دسترسی ندارید")

            request.session["selected_cafe_id"] = cafe.id
            request.session["selected_cafe_code"] = cafe.code
            request.cafe = cafe
        else:
            if request.user.is_superuser or request.user.needs_cafe_selection:
                selected_cafe_id = request.session.get("selected_cafe_id")
                if not selected_cafe_id:
                    return redirect("select_cafe")
                cafe = Cafe.objects.filter(id=selected_cafe_id, is_active=True).first()
                if cafe is None or not _user_can_access_cafe(request.user, cafe):
                    _clear_cafe_session(request)
                    return redirect("select_cafe")
                request.cafe = cafe
            else:
                if not request.user.cafe or not request.user.cafe.is_active:
                    _clear_cafe_session(request)
                    raise Http404("کافه برای کاربر تعریف نشده یا غیرفعال است. با مدیر تماس بگیرید.")

                request.session["selected_cafe_id"] = request.user.cafe_id
                request.session["selected_cafe_code"] = request.user.cafe.code
                request.cafe = request.user.cafe

        return self.get_response(request)

    def _extract_cafe_code(self, request):
        """استخراج cafe_code از URL"""
        try:
            resolved = resolve(request.path)
            return resolved.kwargs.get("cafe_code")
        except Resolver404:
            return None

    def _validate_cafe_access(self, request, cafe_code, Cafe):
        """
        اعتبارسنجی دسترسی کاربر به کافه
        Returns: Cafe object if valid and active, None otherwise.
        """
        try:
            cafe = Cafe.objects.get(code=cafe_code, is_active=True)
        except Cafe.DoesNotExist:
            return None

        if _user_can_access_cafe(request.user, cafe):
            return cafe
        return None


class CafeContextMiddleware:
    """
    Middleware برای اضافه کردن اطلاعات کافه به context
    این باید بعد از CafeMiddleware باشه
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .models import Cafe

        if not hasattr(request, "cafe"):
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated:
                request.cafe = None
            else:
                cafe_id = request.session.get("selected_cafe_id")
                cafe = Cafe.objects.filter(id=cafe_id, is_active=True).first() if cafe_id else None
                if cafe and _user_can_access_cafe(user, cafe):
                    request.cafe = cafe
                else:
                    if cafe_id:
                        _clear_cafe_session(request)
                    request.cafe = None

        return self.get_response(request)


def get_current_cafe(request):
    """Helper function برای گرفتن کافه فعلی از request"""
    from .models import Cafe

    if hasattr(request, "cafe"):
        return request.cafe

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None

    cafe_id = request.session.get("selected_cafe_id")
    if cafe_id:
        cafe = Cafe.objects.filter(id=cafe_id, is_active=True).first()
        if cafe and _user_can_access_cafe(user, cafe):
            return cafe
    return None


def get_cafe_code(request):
    """Helper function برای گرفتن کد کافه از request"""
    if hasattr(request, "cafe") and request.cafe:
        return request.cafe.code
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    return request.session.get("selected_cafe_code")
