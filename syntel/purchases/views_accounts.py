"""
Authentication, User Management, Department Management, and Requester Views.
تفکیک‌شده از purchases/views.py برای بهبود ساختار معماری و نگهداری‌پذیری.
"""
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from .middleware import get_cafe_code
from .models import Cafe, Department, Requester, User
from .views_helpers import (
    custom_login_required,
    get_cafe_from_request,
    get_cafe_redirect_url,
    role_required,
)


def _get_departments():
    return User.ROLE_CHOICES


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


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@csrf_exempt
def login_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        cafe_code = request.POST.get("cafe_code", "")

        user = authenticate(request, username=username, password=password)
        if user and user.is_active:
            login(request, user)

            # تنظیم کافه انتخاب شده در session
            if cafe_code:
                try:
                    selected_cafe = Cafe.objects.get(code=cafe_code, is_active=True)
                    request.session['selected_cafe_id'] = selected_cafe.id
                    request.session['selected_cafe_code'] = selected_cafe.code
                    cafe_code = selected_cafe.code
                except Cafe.DoesNotExist:
                    logout(request)
                    return render(
                        request,
                        "login.html",
                        {"error": "کافه انتخاب شده معتبر نیست.", "cafes": Cafe.objects.filter(is_active=True)},
                    )
            else:
                # اگر کافه انتخاب نشده، از کافه کاربر استفاده کن
                if user.cafe:
                    request.session['selected_cafe_id'] = user.cafe_id
                    request.session['selected_cafe_code'] = user.cafe.code
                    cafe_code = user.cafe.code
                else:
                    logout(request)
                    return render(
                        request,
                        "login.html",
                        {"error": "کافه برای کاربر شما تعریف نشده است. با مدیر تماس بگیرید.", "cafes": Cafe.objects.filter(is_active=True)},
                    )

            # Redirect based on role
            return _redirect_user_home(user, cafe_code)
        return render(
            request,
            "login.html",
            {"error": "نام کاربری یا رمز عبور اشتباه است.", "cafes": Cafe.objects.filter(is_active=True)},
        )

    # GET request - نمایش لیست کافه‌ها
    cafes = Cafe.objects.filter(is_active=True)
    return render(request, "login.html", {"cafes": cafes})


@custom_login_required
def select_cafe(request: HttpRequest) -> HttpResponse:
    """صفحه انتخاب کافه برای کاربران ادمین"""
    # بررسی اینکه کاربر نیاز به انتخاب کافه دارد
    if not request.user.needs_cafe_selection:
        # کاربر عادی - redirect به داشبورد کافه خودش
        if request.user.cafe:
            return redirect("dashboard", cafe_code=request.user.cafe.code)
        else:
            logout(request)
            return redirect("login")

    cafes = Cafe.objects.filter(is_active=True)

    if request.method == "POST":
        cafe_code = request.POST.get("cafe_code")
        try:
            cafe = Cafe.objects.get(code=cafe_code, is_active=True)
            request.session['selected_cafe_id'] = cafe.id
            request.session['selected_cafe_code'] = cafe.code
            return _redirect_user_home(request.user, cafe_code)
        except Cafe.DoesNotExist:
            messages.error(request, "کافه انتخاب شده یافت نشد.")

    return render(request, "select_cafe.html", {"cafes": cafes})


def logout_view(request: HttpRequest) -> HttpResponse:
    # پاک کردن اطلاعات کافه از session
    if 'selected_cafe_id' in request.session:
        del request.session['selected_cafe_id']
    if 'selected_cafe_code' in request.session:
        del request.session['selected_cafe_code']
    logout(request)
    return redirect("login")


# ---------------------------------------------------------------------------
# Department Management
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["manager", "admin"])
def department_list(request):
    """لیست بخشهای تعریفشده — فقط بخشهای کافه جاری؛ هیچ بخش سراسری وجود ندارد"""
    cafe = get_cafe_from_request(request)
    departments = Department.objects.filter(cafe=cafe).annotate(user_count=Count('users'))
    departments = departments.order_by('name')
    return render(request, "department_list.html", {"departments": departments, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_create(request):
    cafe = get_cafe_from_request(request)
    error = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        if not name:
            error = "نام بخش الزامی است"
        elif not cafe:
            error = "ابتدا کافه را انتخاب کنید"
        else:
            existing = Department.objects.filter(name__iexact=name, cafe=cafe)
            if existing.exists():
                error = f"بخشی با نام «{name}» قبلاً در این کافه ثبت شده است"
            else:
                Department.objects.create(name=name, cafe=cafe, is_active=is_active)
                return get_cafe_redirect_url('department_list', request)
    return render(request, "department_form.html", {"error": error, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_update(request, department_id):
    cafe = get_cafe_from_request(request)
    # فقط بخش کافه جاری قابل ویرایش است؛ بخش کافه دیگر برای مدیر این کافه ۴۰۴ است
    department = get_object_or_404(Department, cafe=cafe, id=department_id)
    error = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        if not name:
            error = "نام بخش الزامی است"
        else:
            existing = Department.objects.filter(name__iexact=name, cafe=cafe).exclude(id=department.id)
            if existing.exists():
                error = f"بخشی با نام «{name}» قبلاً در این کافه ثبت شده است"
            else:
                department.name = name
                department.is_active = is_active
                department.save()
                return get_cafe_redirect_url('department_list', request)
    return render(request, "department_form.html", {"department": department, "error": error, "cafe": cafe})


@custom_login_required
@role_required(["manager", "admin"])
def department_delete(request, department_id):
    cafe = get_cafe_from_request(request)
    department = get_object_or_404(Department, cafe=cafe, id=department_id)
    user_count = department.users.count()
    if request.method == "POST":
        # کاربران متصل به این بخش با SET_NULL بدون بخش میشوند (دادههای تاریخی حفظ میشوند)
        department.delete()
        return get_cafe_redirect_url('department_list', request)
    return render(request, "department_confirm_delete.html", {
        "department": department,
        "user_count": user_count,
    })


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["manager", "admin"])
def user_list(request, cafe_code=None):
    cafe = get_cafe_from_request(request)
    users = User.objects.all()

    # فیلتر بر اساس کافه جاری - کاربران همین کافه + کاربران مشترک (cafe=None که به همه کافه‌ها دسترسی دارند)
    if cafe:
        users = users.filter(Q(cafe=cafe) | Q(cafe__isnull=True))

    search_query = request.GET.get("search", "").strip()
    selected_role = request.GET.get("role", "")
    officer_filter = request.GET.get("officer", "") == "1"
    if search_query:
        users = users.filter(
            username__icontains=search_query
        ) | users.filter(first_name__icontains=search_query) | users.filter(last_name__icontains=search_query)
    if selected_role:
        users = users.filter(role=selected_role)
    if officer_filter:
        # مسئولان خرید/انبار: نقش اصلی یا نقش دوم آن‌ها purchasing/warehouse باشد
        officer_q = Q(role__in=['purchasing', 'warehouse']) | Q(secondary_role__in=['purchasing', 'warehouse'])
        users = users.filter(officer_q)
    return render(request, "user_list.html", {
        "users": users,
        "role_choices": User.ROLE_CHOICES,
        "search_query": search_query,
        "selected_role": selected_role,
        "officer_filter": officer_filter,
        "current_cafe": cafe,
    })


@custom_login_required
@role_required(["manager"])
def user_create(request, cafe_code=None):
    cafe = get_cafe_from_request(request)
    cafes = Cafe.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True, cafe=cafe)
    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email", "")
        password = request.POST.get("password1") or request.POST.get("password")
        confirm_password = request.POST.get("password2") or request.POST.get("confirm_password")
        role = request.POST.get("role", "kitchen")
        cafe_id = request.POST.get("cafe_id", "all")
        department_id = request.POST.get("department_id", "")

        if not username:
            return render(request, "user_form.html", {
                "error": "نام کاربری الزامی است",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})
        if User.objects.filter(username=username).exists():
            return render(request, "user_form.html", {
                "error": f"نام کاربری '{username}' قبلاً استفاده شده است. لطفاً نام دیگری انتخاب کنید.",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})
        if not password:
            return render(request, "user_form.html", {
                "error": "رمز عبور الزامی است",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})
        if password != confirm_password:
            return render(request, "user_form.html", {
                "error": "رمز عبور و تأیید آن یکسان نیستند",
                "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})

        # تعیین کافه کاربر
        cafe_obj = None
        if cafe_id and cafe_id != "all":
            cafe_obj = Cafe.objects.filter(id=cafe_id, is_active=True).first()

        # تعیین بخش — فقط بخشی که به کافه همین کاربر تعلق دارد پذیرفته میشود
        department_obj = None
        if department_id:
            dep = Department.objects.filter(id=department_id, is_active=True).first()
            if dep and cafe_obj and dep.cafe_id == cafe_obj.id:
                department_obj = dep
            else:
                return render(request, "user_form.html", {
                    "error": "بخش انتخاب‌شده متعلق به کافه این کاربر نیست",
                    "roles": _get_departments(), "cafes": cafes, "departments": departments, "form_data": request.POST})

        secondary_role = request.POST.get("secondary_role", "").strip() or None
        User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=role,
            secondary_role=secondary_role,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            is_active=request.POST.get("is_active", True) not in ["False", "0", ""],
            cafe=cafe_obj,
            department=department_obj,
        )
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_form.html", {"roles": _get_departments(), "cafes": cafes, "departments": departments})


@custom_login_required
@role_required(["manager"])
def user_update(request, cafe_code=None, user_id=None):
    cafe = get_cafe_from_request(request)
    user_obj = get_object_or_404(User, id=user_id)
    cafes = Cafe.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True, cafe=cafe)
    if request.method == "POST":
        # تغییر نام کاربری (اختیاری)
        new_username = request.POST.get("username", "").strip()
        if new_username and new_username != user_obj.username:
            if User.objects.filter(username=new_username).exists():
                return render(request, "user_form.html", {
                    "edit_user": user_obj,
                    "roles": _get_departments(),
                    "cafes": cafes,
                    "departments": departments,
                    "error": f"نام کاربری '{new_username}' قبلاً استفاده شده است",
                })
            user_obj.username = new_username
        user_obj.first_name = request.POST.get("first_name", user_obj.first_name)
        user_obj.last_name = request.POST.get("last_name", user_obj.last_name)
        user_obj.email = request.POST.get("email", user_obj.email)
        user_obj.role = request.POST.get("role", user_obj.role)
        user_obj.secondary_role = request.POST.get("secondary_role", "").strip() or None
        user_obj.is_active = "is_active" in request.POST
        cafe_id = request.POST.get("cafe_id", "keep")
        if cafe_id == "all":
            user_obj.cafe = None
        elif cafe_id and cafe_id != "keep":
            cafe_obj = Cafe.objects.filter(id=cafe_id, is_active=True).first()
            if cafe_obj:
                user_obj.cafe = cafe_obj
        # بخش — فقط بخشی که به کافه همین کاربر تعلق دارد پذیرفته میشود
        department_id = request.POST.get("department_id", "").strip()
        if department_id:
            dep = Department.objects.filter(id=department_id, is_active=True).first()
            if dep and user_obj.cafe and dep.cafe_id == user_obj.cafe_id:
                user_obj.department = dep
            else:
                return render(request, "user_form.html", {
                    "edit_user": user_obj,
                    "roles": _get_departments(),
                    "cafes": cafes,
                    "departments": departments,
                    "error": "بخش انتخاب‌شده متعلق به کافه این کاربر نیست",
                })
        else:
            user_obj.department = None
        # تغییر رمز عبور (اختیاری)
        new_password = request.POST.get("new_password", "").strip()
        confirm_new_password = request.POST.get("confirm_new_password", "").strip()
        if new_password:
            if new_password == confirm_new_password:
                user_obj.set_password(new_password)
            else:
                return render(request, "user_form.html", {
                    "edit_user": user_obj,
                    "roles": _get_departments(),
                    "cafes": cafes,
                    "departments": departments,
                    "error": "رمز عبور جدید و تأیید آن یکسان نیستند",
                })
        user_obj.save()
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_form.html", {"edit_user": user_obj, "roles": _get_departments(), "cafes": cafes, "departments": departments})


@custom_login_required
@role_required(["manager"])
def user_delete(request, cafe_code=None, user_id=None):
    user_obj = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        user_obj.delete()
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_confirm_delete.html", {"user": user_obj})


@custom_login_required
@role_required(["manager"])
def user_reset_password(request, cafe_code=None, user_id=None):
    user_obj = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        new_password = request.POST.get("password1")
        if new_password:
            user_obj.set_password(new_password)
            user_obj.save()
            return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    return render(request, "user_reset_password.html", {"user": user_obj})


@custom_login_required
@role_required(["manager", "admin"])
def user_impersonate(request, cafe_code=None, user_id=None):
    """ورود با حساب کاربر دیگر (جانشینی) — فقط برای مدیر و ادمین."""
    if request.method != "POST":
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    target = get_object_or_404(User, id=user_id)
    if not target.is_active:
        messages.error(request, "امکان ورود با حساب غیرفعال وجود ندارد.")
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")
    if target == request.user:
        messages.info(request, "شما هم‌اکنون با همین حساب وارد هستید.")
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")

    # کافه فعال بعد از ورود — طبق قواعد CafeMiddleware
    if target.cafe:
        active_cafe = target.cafe
    else:
        current_cafe = get_cafe_from_request(request)
        if current_cafe and (target.is_superuser or target.role in ["admin", "manager", "finance_manager", "accounting", "ceo"]):
            active_cafe = current_cafe
        else:
            active_cafe = None
    if active_cafe is None:
        messages.error(request, "این کاربر کافه مشخصی ندارد و امکان ورود با آن وجود ندارد.")
        return redirect("user_list", cafe_code=get_cafe_code(request) or "local")

    # ذخیره حساب اصلی برای بازگشت — بعد از login() چون login در صورت
    # تغییر کاربر، session را flush() می‌کند و کلیدهای قبلی پاک می‌شوند
    impersonator_id = request.user.id
    impersonator_cafe_code = get_cafe_code(request)

    login(request, target)
    request.session["impersonator_id"] = impersonator_id
    request.session["impersonator_cafe_code"] = impersonator_cafe_code
    request.session["selected_cafe_id"] = active_cafe.id
    request.session["selected_cafe_code"] = active_cafe.code

    messages.success(request, f"شما با حساب «{target.get_full_name() or target.username}» وارد شدید.")
    return _redirect_user_home(target, active_cafe.code)


@custom_login_required
def user_impersonate_stop(request):
    """بازگشت به حساب اصلی پس از ورود با حساب کاربر دیگر."""
    original_id = request.session.get("impersonator_id")
    original_cafe_code = request.session.get("impersonator_cafe_code")
    original = User.objects.filter(id=original_id, is_active=True).first() if original_id else None
    if original is None:
        request.session.pop("impersonator_id", None)
        request.session.pop("impersonator_cafe_code", None)
        messages.error(request, "امکان بازگشت به حساب اصلی وجود ندارد؛ لطفاً دوباره وارد شوید.")
        return redirect("login")

    login(request, original)
    request.session.pop("impersonator_id", None)
    request.session.pop("impersonator_cafe_code", None)

    # بازگردانی کافه اصلی (برای سوپر ادمین‌ها از کافه فعلی URL یا کافه ذخیره‌شده)
    active_cafe = original.cafe or get_cafe_from_request(request)
    if active_cafe is None and original_cafe_code:
        active_cafe = Cafe.objects.filter(code=original_cafe_code, is_active=True).first()
    if active_cafe is None:
        if original.needs_cafe_selection:
            return redirect("select_cafe")
        messages.error(request, "کافه‌ای برای حساب شما یافت نشد.")
        return redirect("login")

    request.session["selected_cafe_id"] = active_cafe.id
    request.session["selected_cafe_code"] = active_cafe.code
    messages.success(request, f"به حساب «{original.get_full_name() or original.username}» بازگشتید.")
    return redirect("user_list", cafe_code=active_cafe.code)


# ---------------------------------------------------------------------------
# Requesters
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_list(request):
    default_requesters = ['kitchen', 'bar', 'facilities']
    for req in default_requesters:
        Requester.objects.get_or_create(name=req)

    requesters = Requester.objects.all()
    return render(request, "requester_list.html", {"requesters": requesters})


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            Requester.objects.create(name=name, cafe=get_cafe_from_request(request))
            return get_cafe_redirect_url("requester_list", request)
    return render(request, "requester_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def requester_delete(request, requester_id):
    requester = get_object_or_404(Requester, id=requester_id)
    if request.method == "POST":
        requester.delete()
        return get_cafe_redirect_url("requester_list", request)
    return render(request, "requester_confirm_delete.html", {"requester": requester})
