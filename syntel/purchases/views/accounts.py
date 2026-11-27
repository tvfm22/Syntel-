from __future__ import annotations

from ..middleware import get_cafe_code
from ..models import ActivityLog
from ..models import Cafe
from ..models import Department
from ..models import Requester
from ..models import User
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import role_required
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login
from django.contrib.auth import logout
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Count
from django.db.models import Q
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from rest_framework.authtoken.models import Token
import logging

logger = logging.getLogger(__name__)


MIN_PASSWORD_LENGTH = 8

LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 300

PRIVILEGED_IMPERSONATION_ROLES = {"admin", "manager", "ceo"}
PRIVILEGED_MANAGEMENT_ROLES = {"admin", "ceo"}


def _get_departments():
    return User.ROLE_CHOICES


def _valid_roles():
    return {choice[0] for choice in User.ROLE_CHOICES}


def _is_global_admin(user):
    """Superuser or global admin (cafe=None, role='admin')."""
    return bool(user.is_superuser or (user.cafe is None and user.role == "admin"))


def _is_user_authorized_for_cafe(user, cafe):
    """Return True if `user` is allowed to enter `cafe`."""
    if not cafe or not cafe.is_active:
        return False
    if user.is_superuser:
        return True
    if user.cafe is None and user.role in User.CAFE_SELECTION_ROLES:
        return True
    return user.cafe_id == cafe.id


def _authorize_target_user_management(actor, target, current_cafe):
    """Return None if `actor` may manage `target`, or a 403/404 HttpResponse."""
    if actor.is_superuser:
        return None

    if current_cafe is None:
        return HttpResponseForbidden("ابتدا کافه را انتخاب کنید.")

    if (
        target.is_superuser
        or target.cafe is None
        or target.role in PRIVILEGED_MANAGEMENT_ROLES
        or target.secondary_role in PRIVILEGED_MANAGEMENT_ROLES
    ):
        return HttpResponseForbidden("شما مجاز به مدیریت این حساب کاربری نیستید.")

    if actor.cafe is not None:
        if target.cafe_id != actor.cafe_id or current_cafe.id != actor.cafe_id:
            raise Http404("کاربر یافت نشد.")
    elif target.cafe_id != current_cafe.id:
        raise Http404("کاربر یافت نشد.")

    return None


def _log_impersonation_event(actor, action, description):
    """Record impersonation start/stop via security logger and ActivityLog when nullable."""
    logger.info(
        "Impersonation audit [%s] actor_id=%s (%s): %s",
        action,
        getattr(actor, "id", None),
        getattr(actor, "username", ""),
        description,
    )
    try:
        request_field = ActivityLog._meta.get_field("request")
        if request_field.null:
            ActivityLog.objects.create(
                user=actor,
                action=action[:20],
                description=description,
            )
    except Exception:
        logger.exception("Failed to persist ActivityLog for %s", action)


def _redirect_user_home(user, cafe_code):
    """هدایت کاربر به صفحه پیش‌فرض خودش بر اساس نقش (همان منطق login_view)."""
    if user.is_superuser or user.role == "admin":
        return redirect("finance_dashboard", cafe_code=cafe_code)
    if user.role == "ceo":
        return redirect("all_departments_requests", cafe_code=cafe_code)
    if user.role == "manager":
        return redirect("management_dashboard", cafe_code=cafe_code)
    if user.role in ["kitchen", "bar", "counter", "cashier", "facilities", "requester"]:
        return redirect("mobile_dashboard", cafe_code=cafe_code)
    if user.role == "purchasing":
        return redirect("purchase_officer_dashboard", cafe_code=cafe_code)
    if user.role == "warehouse":
        return redirect("warehouse_officer_dashboard", cafe_code=cafe_code)
    if user.role == "finance_manager":
        return redirect("finance_manager_dashboard", cafe_code=cafe_code)
    if user.role == "accounting":
        return redirect("finance_dashboard", cafe_code=cafe_code)
    return redirect("management_dashboard", cafe_code=cafe_code)


def _get_client_ip(request: HttpRequest) -> str:
    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return xff or (request.META.get("REMOTE_ADDR") or "unknown")


def _login_rate_limit_key(request: HttpRequest, username: str) -> str:
    return f"login_fail:{_get_client_ip(request)}:{username.lower()}"


def login_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password", "")
        cafe_code = (request.POST.get("cafe_code") or "").strip()

        rate_key = _login_rate_limit_key(request, username)
        attempts = cache.get(rate_key, 0)
        if attempts >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
            return render(
                request,
                "login.html",
                {
                    "error": "تعداد تلاش‌های ناموفق بیش از حد مجاز است. لطفاً چند دقیقه دیگر دوباره تلاش کنید.",
                    "cafes": Cafe.objects.filter(is_active=True),
                },
                status=429,
            )

        user = authenticate(request, username=username, password=password)
        if user and user.is_active:
            if cafe_code:
                selected_cafe = Cafe.objects.filter(code=cafe_code, is_active=True).first()
                if not selected_cafe or not _is_user_authorized_for_cafe(user, selected_cafe):
                    return render(
                        request,
                        "login.html",
                        {
                            "error": "کافه انتخاب شده معتبر نیست یا دسترسی ندارید.",
                            "cafes": Cafe.objects.filter(is_active=True),
                        },
                    )
                cache.delete(rate_key)
                login(request, user)
                request.session["selected_cafe_id"] = selected_cafe.id
                request.session["selected_cafe_code"] = selected_cafe.code
                return _redirect_user_home(user, selected_cafe.code)

            # No cafe_code supplied
            if user.cafe:
                if not user.cafe.is_active:
                    return render(
                        request,
                        "login.html",
                        {
                            "error": "کافه حساب کاربری شما غیرفعال است.",
                            "cafes": Cafe.objects.filter(is_active=True),
                        },
                    )
                cache.delete(rate_key)
                login(request, user)
                request.session["selected_cafe_id"] = user.cafe_id
                request.session["selected_cafe_code"] = user.cafe.code
                return _redirect_user_home(user, user.cafe.code)

            if user.is_superuser or user.role in User.CAFE_SELECTION_ROLES:
                cache.delete(rate_key)
                login(request, user)
                return redirect("select_cafe")

            return render(
                request,
                "login.html",
                {
                    "error": "کافه برای کاربر شما تعریف نشده است. با مدیر تماس بگیرید.",
                    "cafes": Cafe.objects.filter(is_active=True),
                },
            )

        if not cache.add(rate_key, 1, timeout=LOGIN_RATE_LIMIT_WINDOW_SECONDS):
            try:
                cache.incr(rate_key)
            except ValueError:
                cache.set(rate_key, attempts + 1, timeout=LOGIN_RATE_LIMIT_WINDOW_SECONDS)

        return render(
            request,
            "login.html",
            {
                "error": "نام کاربری یا رمز عبور اشتباه است.",
                "cafes": Cafe.objects.filter(is_active=True),
            },
        )

    cafes = Cafe.objects.filter(is_active=True)
    return render(request, "login.html", {"cafes": cafes})


@custom_login_required
def select_cafe(request: HttpRequest) -> HttpResponse:
    """صفحه انتخاب کافه برای کاربران ادمین"""
    if not (request.user.is_superuser or request.user.needs_cafe_selection):
        if request.user.cafe and request.user.cafe.is_active:
            return redirect("dashboard", cafe_code=request.user.cafe.code)
        logout(request)
        return redirect("login")

    cafes = Cafe.objects.filter(is_active=True)

    if request.method == "POST":
        cafe_code = (request.POST.get("cafe_code") or "").strip()
        cafe = Cafe.objects.filter(code=cafe_code, is_active=True).first()
        if cafe and _is_user_authorized_for_cafe(request.user, cafe):
            request.session["selected_cafe_id"] = cafe.id
            request.session["selected_cafe_code"] = cafe.code
            return _redirect_user_home(request.user, cafe.code)
        messages.error(request, "کافه انتخاب شده یافت نشد.")

    return render(request, "select_cafe.html", {"cafes": cafes})


def logout_view(request: HttpRequest) -> HttpResponse:
    request.session.pop("selected_cafe_id", None)
    request.session.pop("selected_cafe_code", None)
    logout(request)
    return redirect("login")


@custom_login_required
@role_required(["manager", "admin"])
def department_list(request):
    """لیست بخشهای تعریفشده — فقط بخشهای کافه جاری؛ هیچ بخش سراسری وجود ندارد"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return HttpResponseForbidden("ابتدا کافه را انتخاب کنید.")
    departments = (
        Department.objects.filter(cafe=cafe)
        .annotate(user_count=Count("users"))
        .order_by("name")
    )
    return render(request, "department_list.html", {"departments": departments, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_create(request):
    cafe = get_cafe_from_request(request)
    error = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        is_recipe_department = request.POST.get("is_recipe_department") == "on"
        if not name:
            error = "نام بخش الزامی است"
        elif not cafe:
            error = "ابتدا کافه را انتخاب کنید"
        else:
            existing = Department.objects.filter(name__iexact=name, cafe=cafe)
            if existing.exists():
                error = f"بخشی با نام «{name}» قبلاً در این کافه ثبت شده است"
            else:
                Department.objects.create(
                    name=name,
                    cafe=cafe,
                    is_active=is_active,
                    is_recipe_department=is_recipe_department,
                )
                return get_cafe_redirect_url("department_list", request)
    return render(request, "department_form.html", {"error": error, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_update(request, department_id):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404("کافه فعال یافت نشد")
    department = get_object_or_404(Department, cafe=cafe, id=department_id)
    error = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        is_recipe_department = request.POST.get("is_recipe_department") == "on"
        if not name:
            error = "نام بخش الزامی است"
        else:
            existing = Department.objects.filter(name__iexact=name, cafe=cafe).exclude(id=department.id)
            if existing.exists():
                error = f"بخشی با نام «{name}» قبلاً در این کافه ثبت شده است"
            else:
                department.name = name
                department.is_active = is_active
                department.is_recipe_department = is_recipe_department
                department.save()
                return get_cafe_redirect_url("department_list", request)
    return render(request, "department_form.html", {"department": department, "error": error, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_delete(request, department_id):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404("کافه فعال یافت نشد")
    department = get_object_or_404(Department, cafe=cafe, id=department_id)
    user_count = department.users.count()
    if request.method == "POST":
        department.delete()
        return get_cafe_redirect_url("department_list", request)
    return render(
        request,
        "department_confirm_delete.html",
        {
            "department": department,
            "user_count": user_count,
        },
    )


@custom_login_required
@role_required(["manager", "admin"])
def user_list(request, cafe_code=None):
    cafe = get_cafe_from_request(request)

    if request.user.is_superuser:
        users = User.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True)) if cafe else User.objects.all()
    elif cafe is None:
        users = User.objects.none()
    elif request.user.cafe is not None:
        # Single-cafe managers/admins see only users of their own cafe, never global/superuser accounts
        users = User.objects.filter(cafe=cafe, is_superuser=False)
    elif request.user.role == "admin":
        users = User.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True), is_superuser=False)
    else:
        users = User.objects.filter(cafe=cafe, is_superuser=False)

    search_query = request.GET.get("search", "").strip()
    selected_role = request.GET.get("role", "")
    officer_filter = request.GET.get("officer", "") == "1"
    if search_query:
        users = users.filter(
            Q(username__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
        )
    if selected_role:
        users = users.filter(role=selected_role)
    if officer_filter:
        officer_q = Q(role__in=["purchasing", "warehouse"]) | Q(secondary_role__in=["purchasing", "warehouse"])
        users = users.filter(officer_q)

    return render(
        request,
        "user_list.html",
        {
            "users": users,
            "role_choices": User.ROLE_CHOICES,
            "search_query": search_query,
            "selected_role": selected_role,
            "officer_filter": officer_filter,
            "current_cafe": cafe,
        },
    )


@custom_login_required
@role_required(["manager"])
def user_create(request, cafe_code=None):
    cafe = get_cafe_from_request(request)
    if not request.user.is_superuser and cafe is None:
        return HttpResponseForbidden("ابتدا کافه را انتخاب کنید.")

    if request.user.cafe is not None and not request.user.is_superuser:
        cafes = Cafe.objects.filter(id=request.user.cafe_id, is_active=True)
    else:
        cafes = Cafe.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True, cafe=cafe) if cafe else Department.objects.none()

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password1") or request.POST.get("password") or ""
        confirm_password = request.POST.get("password2") or request.POST.get("confirm_password") or ""
        role = (request.POST.get("role") or "kitchen").strip()
        secondary_role = (request.POST.get("secondary_role") or "").strip() or None
        raw_cafe_id = request.POST.get("cafe_id")
        cafe_id = raw_cafe_id.strip() if raw_cafe_id is not None else None
        department_id = (request.POST.get("department_id") or "").strip()

        def _render_err(msg):
            return render(
                request,
                "user_form.html",
                {
                    "error": msg,
                    "roles": _get_departments(),
                    "cafes": cafes,
                    "departments": departments,
                    "form_data": request.POST,
                },
            )

        if not request.user.is_superuser:
            if role in PRIVILEGED_MANAGEMENT_ROLES or secondary_role in PRIVILEGED_MANAGEMENT_ROLES:
                return HttpResponseForbidden("فقط سوپرادمین مجاز به تخصیص نقش مدیرعامل یا ادمین است.")
            if request.user.cafe is not None:
                if cafe_id == "all" or (cafe_id and cafe_id != str(request.user.cafe_id)):
                    return HttpResponseForbidden("شما مجاز به ایجاد کاربر برای کافه دیگر یا به صورت سراسری نیستید.")

        if not username:
            return _render_err("نام کاربری الزامی است (username)")
        if User.objects.filter(username__iexact=username).exists():
            return _render_err(f"نام کاربری '{username}' قبلاً استفاده شده است. لطفاً نام دیگری انتخاب کنید.")
        if email:
            try:
                validate_email(email)
            except ValidationError:
                return _render_err("فرمت ایمیل معتبر نیست (email)")
        if role not in _valid_roles():
            return _render_err("نقش انتخاب‌شده معتبر نیست.")
        if secondary_role and secondary_role not in _valid_roles():
            return _render_err("نقش ثانویه انتخاب‌شده معتبر نیست.")
        if role not in ("purchasing", "warehouse"):
            secondary_role = None
        if not password:
            return _render_err("رمز عبور الزامی است")
        if len(password) < MIN_PASSWORD_LENGTH:
            return _render_err(f"رمز عبور باید حداقل {MIN_PASSWORD_LENGTH} کاراکتر باشد.")
        if password != confirm_password:
            return _render_err("رمز عبور و تأیید آن یکسان نیستند")
        candidate_user = User(
            username=username,
            email=email,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
        )
        try:
            validate_password(password, user=candidate_user)
        except ValidationError as exc:
            return _render_err(" ".join(exc.messages))

        # تعیین کافه کاربر
        if request.user.cafe is not None and not request.user.is_superuser:
            cafe_obj = request.user.cafe
        elif cafe_id and cafe_id != "all":
            cafe_obj = Cafe.objects.filter(id=cafe_id, is_active=True).first()
            if cafe_obj is None:
                return _render_err("کافه انتخاب‌شده معتبر نیست.")
        elif cafe_id == "all":
            if not _is_global_admin(request.user):
                return HttpResponseForbidden("شما مجاز به ایجاد کاربر سراسری نیستید.")
            cafe_obj = None
        else:
            cafe_obj = cafe

        department_obj = None
        if department_id:
            dep = Department.objects.filter(id=department_id, is_active=True).first()
            if dep and cafe_obj and dep.cafe_id == cafe_obj.id:
                department_obj = dep
            else:
                return _render_err("بخش انتخاب‌شده متعلق به کافه این کاربر نیست")

        if "is_active_present" in request.POST:
            is_active = str(request.POST.get("is_active", "")).strip().lower() not in ("", "false", "0")
        elif "is_active" in request.POST:
            is_active = str(request.POST.get("is_active", "")).strip().lower() not in ("", "false", "0")
        else:
            is_active = True

        User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=role,
            secondary_role=secondary_role,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            is_active=is_active,
            cafe=cafe_obj,
            department=department_obj,
        )
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")

    return render(
        request,
        "user_form.html",
        {"roles": _get_departments(), "cafes": cafes, "departments": departments},
    )


@custom_login_required
@role_required(["manager"])
def user_update(request, cafe_code=None, user_id=None):
    cafe = get_cafe_from_request(request)
    user_obj = get_object_or_404(User, id=user_id)
    denied = _authorize_target_user_management(request.user, user_obj, cafe)
    if denied is not None:
        return denied

    if request.user.cafe is not None and not request.user.is_superuser:
        cafes = Cafe.objects.filter(id=request.user.cafe_id, is_active=True)
    else:
        cafes = Cafe.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True, cafe=cafe) if cafe else Department.objects.none()

    def _render_err(msg):
        return render(
            request,
            "user_form.html",
            {
                "edit_user": user_obj,
                "roles": _get_departments(),
                "cafes": cafes,
                "departments": departments,
                "error": msg,
            },
        )

    if request.method == "POST":
        new_role = (request.POST.get("role") or user_obj.role).strip()
        new_secondary_role = (request.POST.get("secondary_role") or "").strip() or None
        cafe_id = (request.POST.get("cafe_id") or "keep").strip()

        if not request.user.is_superuser:
            if new_role in PRIVILEGED_MANAGEMENT_ROLES or new_secondary_role in PRIVILEGED_MANAGEMENT_ROLES:
                return HttpResponseForbidden("فقط سوپرادمین مجاز به تخصیص نقش مدیرعامل یا ادمین است.")
            if request.user.cafe is not None:
                if cafe_id == "all" or (cafe_id not in ("", "keep") and cafe_id != str(request.user.cafe_id)):
                    return HttpResponseForbidden("شما مجاز به تغییر کافه کاربر نیستید.")

        if new_role not in _valid_roles():
            return _render_err("نقش انتخاب‌شده معتبر نیست.")
        if new_secondary_role and new_secondary_role not in _valid_roles():
            return _render_err("نقش ثانویه انتخاب‌شده معتبر نیست.")
        if new_role not in ("purchasing", "warehouse"):
            new_secondary_role = None

        old_role = user_obj.role
        old_secondary_role = user_obj.secondary_role
        old_cafe_id = user_obj.cafe_id

        new_username = (request.POST.get("username") or "").strip()
        if new_username and new_username != user_obj.username:
            if User.objects.filter(username__iexact=new_username).exclude(pk=user_obj.pk).exists():
                return _render_err(f"نام کاربری '{new_username}' قبلاً استفاده شده است")
            user_obj.username = new_username

        new_email = (request.POST.get("email") if "email" in request.POST else user_obj.email) or ""
        new_email = new_email.strip()
        if new_email:
            try:
                validate_email(new_email)
            except ValidationError:
                return _render_err("فرمت ایمیل معتبر نیست (email)")

        user_obj.first_name = request.POST.get("first_name", user_obj.first_name)
        user_obj.last_name = request.POST.get("last_name", user_obj.last_name)
        user_obj.email = new_email
        user_obj.role = new_role
        user_obj.secondary_role = new_secondary_role
        user_obj.is_active = "is_active" in request.POST

        if cafe_id == "all":
            if not _is_global_admin(request.user):
                return HttpResponseForbidden("شما مجاز به تغییر کاربر به حالت سراسری نیستید.")
            user_obj.cafe = None
        elif cafe_id and cafe_id != "keep":
            cafe_obj = Cafe.objects.filter(id=cafe_id, is_active=True).first()
            if cafe_obj:
                user_obj.cafe = cafe_obj
            else:
                return _render_err("کافه انتخاب‌شده معتبر نیست.")

        department_id = (request.POST.get("department_id") or "").strip()
        if department_id:
            dep = Department.objects.filter(id=department_id, is_active=True).first()
            if dep and user_obj.cafe and dep.cafe_id == user_obj.cafe_id:
                user_obj.department = dep
            else:
                return _render_err("بخش انتخاب‌شده متعلق به کافه این کاربر نیست")
        else:
            user_obj.department = None

        new_password = request.POST.get("new_password") or ""
        confirm_new_password = request.POST.get("confirm_new_password") or ""
        password_changed = False
        if new_password:
            if len(new_password) < MIN_PASSWORD_LENGTH:
                return _render_err(f"رمز عبور جدید باید حداقل {MIN_PASSWORD_LENGTH} کاراکتر باشد.")
            if new_password != confirm_new_password:
                return _render_err("رمز عبور جدید و تأیید آن یکسان نیستند")
            try:
                validate_password(new_password, user=user_obj)
            except ValidationError as exc:
                return _render_err(" ".join(exc.messages))
            user_obj.set_password(new_password)
            password_changed = True

        user_obj.save()
        if (
            password_changed
            or not user_obj.is_active
            or user_obj.role != old_role
            or user_obj.secondary_role != old_secondary_role
            or user_obj.cafe_id != old_cafe_id
        ):
            Token.objects.filter(user=user_obj).delete()
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")

    return render(
        request,
        "user_form.html",
        {"edit_user": user_obj, "roles": _get_departments(), "cafes": cafes, "departments": departments},
    )


@custom_login_required
@role_required(["manager"])
def user_delete(request, cafe_code=None, user_id=None):
    cafe = get_cafe_from_request(request)
    user_obj = get_object_or_404(User, id=user_id)
    denied = _authorize_target_user_management(request.user, user_obj, cafe)
    if denied is not None:
        return denied
    if user_obj.pk == request.user.pk:
        return HttpResponseForbidden("امکان حذف حساب کاربری خودتان وجود ندارد.")

    if request.method == "POST":
        Token.objects.filter(user=user_obj).delete()
        user_obj.delete()
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_confirm_delete.html", {"user": user_obj})


@custom_login_required
@role_required(["manager"])
def user_reset_password(request, cafe_code=None, user_id=None):
    cafe = get_cafe_from_request(request)
    user_obj = get_object_or_404(User, id=user_id)
    denied = _authorize_target_user_management(request.user, user_obj, cafe)
    if denied is not None:
        return denied

    error = None
    if request.method == "POST":
        new_password = request.POST.get("new_password") or request.POST.get("password1") or ""
        confirm_password = request.POST.get("confirm_password") or request.POST.get("password2") or ""
        if len(new_password) < MIN_PASSWORD_LENGTH:
            error = f"رمز عبور باید حداقل {MIN_PASSWORD_LENGTH} کاراکتر باشد."
        elif new_password != confirm_password:
            error = "رمز عبور و تأیید آن یکسان نیستند."
        else:
            try:
                validate_password(new_password, user=user_obj)
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                user_obj.set_password(new_password)
                user_obj.save()
                Token.objects.filter(user=user_obj).delete()
                messages.success(request, "رمز عبور با موفقیت بازنشانی شد.")
                return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_reset_password.html", {"user": user_obj, "error": error})


@custom_login_required
@role_required(["manager", "admin"])
def user_impersonate(request, cafe_code=None, user_id=None):
    """ورود با حساب کاربر دیگر (جانشینی) — با کنترل دقیق سلسله‌مراتب و کافه."""
    if request.method != "POST":
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")

    target = get_object_or_404(User, id=user_id)
    if not target.is_active:
        messages.error(request, "امکان ورود با حساب غیرفعال وجود ندارد.")
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    if target == request.user:
        messages.info(request, "شما هم‌اکنون با همین حساب وارد هستید.")
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")

    current_cafe = get_cafe_from_request(request)
    actor = request.user
    actor_is_global_admin = _is_global_admin(actor)

    # Strict privilege hierarchy and tenant isolation
    if not actor.is_superuser:
        if target.is_superuser or target.cafe is None:
            return HttpResponseForbidden("امکان ورود با حساب سوپرادمین یا کاربر سراسری وجود ندارد.")
        if not actor_is_global_admin:
            if (
                target.role in PRIVILEGED_IMPERSONATION_ROLES
                or target.secondary_role in PRIVILEGED_IMPERSONATION_ROLES
            ):
                return HttpResponseForbidden("شما مجاز به ورود با حساب مدیر یا ادمین نیستید.")
            if actor.cafe is not None:
                if target.cafe_id != actor.cafe_id:
                    return HttpResponseForbidden("شما مجاز به ورود با حساب کاربر کافه دیگر نیستید.")
            elif not current_cafe or target.cafe_id != current_cafe.id:
                return HttpResponseForbidden("شما مجاز به ورود با حساب کاربر کافه دیگر نیستید.")

    # کافه فعال بعد از ورود — طبق قواعد CafeMiddleware
    if target.cafe:
        if not target.cafe.is_active:
            return HttpResponseForbidden("کافه کاربر هدف غیرفعال است.")
        active_cafe = target.cafe
    elif current_cafe and current_cafe.is_active and (target.is_superuser or target.role in User.CAFE_SELECTION_ROLES):
        active_cafe = current_cafe
    else:
        active_cafe = None

    if active_cafe is None:
        messages.error(request, "این کاربر کافه مشخصی ندارد و امکان ورود با آن وجود ندارد.")
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")

    impersonator_id = actor.id
    impersonator_username = actor.username
    impersonator_cafe_code = get_cafe_code(request)

    login(request, target)
    request.session["impersonator_id"] = impersonator_id
    request.session["impersonator_cafe_code"] = impersonator_cafe_code
    request.session["selected_cafe_id"] = active_cafe.id
    request.session["selected_cafe_code"] = active_cafe.code

    _log_impersonation_event(
        actor,
        "impersonate_start",
        f"User '{impersonator_username}' started impersonating '{target.username}' (cafe={active_cafe.code}).",
    )

    messages.success(request, f"شما با حساب «{target.get_full_name() or target.username}» وارد شدید.")
    return _redirect_user_home(target, active_cafe.code)


@custom_login_required
def user_impersonate_stop(request):
    """بازگشت به حساب اصلی پس از ورود با حساب کاربر دیگر."""
    original_id = request.session.get("impersonator_id")
    original_cafe_code = request.session.get("impersonator_cafe_code")
    impersonated_user = request.user
    original = User.objects.filter(id=original_id, is_active=True).first() if original_id else None
    if original is None or not (
        original.is_superuser
        or original.role in ("manager", "admin")
        or original.secondary_role in ("manager", "admin")
    ):
        request.session.pop("impersonator_id", None)
        request.session.pop("impersonator_cafe_code", None)
        messages.error(request, "امکان بازگشت به حساب اصلی وجود ندارد؛ لطفاً دوباره وارد شوید.")
        return redirect("login")

    login(request, original)
    request.session.pop("impersonator_id", None)
    request.session.pop("impersonator_cafe_code", None)

    _log_impersonation_event(
        original,
        "impersonate_stop",
        f"User '{original.username}' stopped impersonating '{impersonated_user.username}'.",
    )

    active_cafe = None
    if original.cafe and original.cafe.is_active:
        active_cafe = original.cafe
    elif original_cafe_code:
        active_cafe = Cafe.objects.filter(code=original_cafe_code, is_active=True).first()
    if active_cafe is None and (original.is_superuser or _is_global_admin(original) or original.needs_cafe_selection):
        active_cafe = get_cafe_from_request(request)
    if active_cafe is None:
        if original.is_superuser or original.needs_cafe_selection:
            return redirect("select_cafe")
        messages.error(request, "کافه‌ای برای حساب شما یافت نشد.")
        return redirect("login")

    request.session["selected_cafe_id"] = active_cafe.id
    request.session["selected_cafe_code"] = active_cafe.code
    messages.success(request, f"به حساب «{original.get_full_name() or original.username}» بازگشتید.")
    return redirect("user_list", cafe_code=active_cafe.code)


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_list(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404("کافه فعال یافت نشد")
    if not Requester.objects.filter(cafe=cafe).exists():
        for req in ["kitchen", "bar", "facilities", "store"]:
            Requester.objects.get_or_create(name=req, cafe=cafe)

    requesters = Requester.objects.filter(cafe=cafe)
    return render(request, "requester_list.html", {"requesters": requesters})


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_create(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404("کافه فعال یافت نشد")
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if name:
            Requester.objects.get_or_create(name=name, cafe=cafe)
            return get_cafe_redirect_url("requester_list", request)
    return render(request, "requester_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_delete(request, requester_id):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404("کافه فعال یافت نشد")
    requester = get_object_or_404(Requester, id=requester_id, cafe=cafe)
    if request.method == "POST":
        requester.delete()
        return get_cafe_redirect_url("requester_list", request)
    return render(request, "requester_confirm_delete.html", {"requester": requester})
