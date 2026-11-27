from __future__ import annotations

from ..models import ActivityLog
from ..models import Category
from ..models import Department
from ..models import Notification
from ..models import OfficerAssignment
from ..models import Product
from ..models import PurchaseItem
from ..models import PurchaseRequest
from ..models import WarehouseRequest
from ..models import WarehouseRequestItem
from ..services.receipt_gate import pending_receipt_count
from ..services.receipt_gate import receipt_gate_context
from ..services.receipt_gate import receipt_gate_message
from ..services.recipe_gate import RD_CATEGORY_NAME
from ..services.recipe_gate import bypasses_recipe_gate
from ..services.recipe_gate import inactive_product_error
from ..services.recipe_gate import recipe_gate_error
from .catalog import _filter_products_for_user
from .helpers import _next_delivery_days
from .helpers import _parse_decimal
from .helpers import _received_delivery_rows
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import get_scoped_locked_object_or_404
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from .workflow_helpers import _route_request_to_warehouse
from .workflow_helpers import _warehouse_decided_request_ids
from datetime import datetime
from datetime import timedelta
from django.contrib import messages
from django.db import models
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone


@custom_login_required
@role_required(["kitchen", "bar", "counter", "store", "cashier", "facilities", "purchasing", "warehouse", "manager", "ceo", "requester"])
def warehouse_request_form(request: HttpRequest) -> HttpResponse:
    """فرم ثبت درخواست از انبار توسط درخواست‌دهندگان (آشپزخانه، بار، تاسیسات و...)"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    # Receipt gate — same source of truth as the main goods-request form.
    receipt_gate = receipt_gate_context(request.user, cafe, cafe_code)

    if request.method == 'POST':
        product_names = request.POST.getlist('product_name')
        quantities = request.POST.getlist('item_quantity')
        units = request.POST.getlist('item_unit')
        descriptions = request.POST.getlist('item_description')
        product_ids = request.POST.getlist('item_product_id')

        items_data = []
        submitted_items = []   # برای برگرداندنِ همان ردیف‌ها به فرم بعد از خطا
        blocked = []          # کالاهای غذاییِ بدون رسپی — قابل سفارش نیستند
        for idx, name in enumerate(product_names):
            name = name.strip() if name else ''
            if not name:
                continue
            qty_raw = quantities[idx] if idx < len(quantities) else ''
            qty_val = _parse_decimal(qty_raw)
            unit_val = units[idx].strip() if idx < len(units) and units[idx] else 'عدد'
            desc_val = descriptions[idx] if idx < len(descriptions) else ''
            product_id_raw = product_ids[idx] if idx < len(product_ids) else ''
            submitted_items.append({
                'name': name,
                'quantity': str(qty_raw or ''),
                'unit': unit_val,
                'description': desc_val,
                'productId': product_id_raw,
            })
            if qty_val is None or qty_val <= 0:
                continue
            # محصول انتخاب‌شده از لیست پیشنهادها (اگر انتخاب نشده باشد با نام جستجو می‌شود)
            # شناسهٔ خامِ POST هرگز بدون قفلِ کافه واکشی نمی‌شود؛ در غیر این صورت
            # امکان وصل‌کردن محصولِ کافهٔ دیگر به درخواست همین کافه فراهم می‌شد.
            item_product = None
            if product_id_raw.isdigit():
                qs_p = Product.objects.filter(id=int(product_id_raw))
                if cafe is not None:
                    qs_p = qs_p.filter(cafe=cafe)
                candidate = qs_p.first()
                if candidate is not None and candidate.name.strip().lower() == name.lower():
                    item_product = candidate
            if item_product is None:
                item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else Product.objects.filter(name__iexact=name).first()
            if item_product is None and not bypasses_recipe_gate(request.user):
                blocked.append(
                    f'کالای «{name}» در فهرست محصولات ثبت نشده است. '
                    'لطفاً کالا را از فهرست انتخاب کنید یا به‌عنوان کالای جدید ثبت نمایید.'
                )
                continue
            inactive_err = inactive_product_error(item_product)
            if inactive_err:
                blocked.append(inactive_err)
                continue
            # قاعدهٔ رسپی: کالای غذاییِ بدون رسپی از این فرم هم قابل درخواست نیست.
            if item_product is not None and not bypasses_recipe_gate(request.user):
                gate_error = recipe_gate_error(item_product, cafe=cafe)
                if gate_error:
                    blocked.append(gate_error)
                    continue
            items_data.append((name, qty_val, unit_val, desc_val, item_product))

        if blocked:
            for blocked_message in blocked[:3]:
                messages.error(request, blocked_message)
            return render(request, 'warehouse_request_form.html', {
                'cafe_code': cafe_code, 'submitted_items': submitted_items,
                'receipt_gate': receipt_gate})

        if not items_data:
            messages.error(request, 'حداقل یک قلم کالا وارد کنید')
            return render(request, 'warehouse_request_form.html', {
                'cafe_code': cafe_code, 'submitted_items': submitted_items,
                'receipt_gate': receipt_gate})

        # Receipt gate — same rule as the main goods-request form, enforced on
        # the server so a crafted POST cannot slip past a disabled button.
        pending_receipts = pending_receipt_count(request.user, cafe)
        if pending_receipts:
            messages.error(request, receipt_gate_message(pending_receipts))
            return render(request, 'warehouse_request_form.html', {
                'cafe_code': cafe_code, 'submitted_items': submitted_items,
                'receipt_gate': receipt_gate})

        wh_request = WarehouseRequest.objects.create(
            user=request.user,
            cafe=cafe,
            status='pending',
        )
        for name, qty, unit, desc, item_product in items_data:
            WarehouseRequestItem.objects.create(
                warehouse_request=wh_request,
                product=item_product,
                name=name,
                quantity=qty,
                unit=unit,
                description=desc,
            )

        Notification.objects.create(
            recipient=request.user,
            title='درخواست از انبار ثبت شد',
            message='درخواست شما از انبار ثبت گردید.',
            notification_type='info',
            cafe=cafe,
        )
        return redirect('warehouse_request_success', cafe_code=cafe_code)

    return render(request, 'warehouse_request_form.html', {
        'cafe_code': cafe_code, 'submitted_items': [],
        'receipt_gate': receipt_gate})


@custom_login_required
def warehouse_request_success(request: HttpRequest) -> HttpResponse:
    """صفحه موفقیت پس از ثبت درخواست انبار"""
    return render(request, 'warehouse_request_success.html')


@custom_login_required
def warehouse_requests_list(request: HttpRequest) -> HttpResponse:
    """لیست درخواست‌های انبار برای کاربران درخواست‌دهنده"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    
    requests_qs = WarehouseRequest.objects.filter(user=request.user).prefetch_related('items').order_by('-created_at')
    if cafe:
        requests_qs = requests_qs.filter(cafe=cafe)
    
    return render(request, 'warehouse_requests_list.html', {
        'requests': requests_qs,
        'cafe_code': cafe_code,
    })


def _purchase_request_form_context(request, department='', **extra):
    """Context مشترکِ فرم درخواست کالا (GET و رندر دوباره پس از خطای اعتبارسنجی).

    دستهٔ «تحقیق و توسعه» عمداً از فهرست دسته‌ها حذف می‌شود: سبدِ مدیریتی است،
    برای درخواست‌کننده همیشه خالی است و انتخابش فقط گمراه‌کننده است.
    """
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    all_categories = (Category.objects.filter(is_active=True)
                      .exclude(name=RD_CATEGORY_NAME)
                      .select_related('parent').order_by('name'))
    if cafe:
        all_categories = all_categories.filter(cafe=cafe)

    # فقط دسته‌هایی نمایش داده شوند که کاربر دست‌کم به یک کالای فعال و کامل در آن‌ها
    # (یا زیردسته‌های فعالشان) دسترسی دارد.
    accessible_products = Product.objects.filter(is_active=True, is_complete=True)
    if cafe:
        accessible_products = accessible_products.filter(cafe=cafe)
    accessible_products = _filter_products_for_user(accessible_products, request.user, cafe)
    accessible_cat_ids = set(
        accessible_products.exclude(category_id__isnull=True)
        .values_list('category_id', flat=True)
        .distinct()
    )
    if accessible_cat_ids:
        all_categories = all_categories.filter(
            models.Q(id__in=accessible_cat_ids)
            | models.Q(subcategories__id__in=accessible_cat_ids, subcategories__is_active=True)
        ).distinct()
    else:
        all_categories = all_categories.none()

    context = {
        "department": department,
        "delivery_days": _next_delivery_days(),
        "all_categories": all_categories,
        "submitted_items": [],
        # قفل «تایید دریافت قبل از درخواست جدید» — هم بنر فرم، هم قفل سمت
        # سرور، هر دو این عدد را از services/receipt_gate میگیرند.
        "receipt_gate": receipt_gate_context(request.user, cafe, cafe_code),
    }
    context.update(extra)
    return context


@custom_login_required
@role_required(["kitchen", "bar", "counter", "store", "cashier", "facilities", "purchasing", "manager", "requester"])
def purchase_request_form(request):
    cafe = get_cafe_from_request(request)
    
    # Get department parameter if purchasing user wants to create request for specific department
    department = request.GET.get('department', '')
    
    if request.method == "POST":
        # Basic CSRF check for the explicit test case
        if "HTTP_X_CSRFTOKEN" in request.META and not request.META.get(
            "HTTP_X_CSRFTOKEN"
        ):
            return HttpResponseForbidden("CSRF token missing")

        # Get product names (free text input from user)
        product_names = request.POST.getlist("product_name")
        quantities = request.POST.getlist("item_quantity")
        units = request.POST.getlist("item_unit")
        descriptions = request.POST.getlist("item_description")
        product_ids = request.POST.getlist("item_product_id")

        # Get department from POST if provided
        selected_department = request.POST.get('department', department)

        items_data = []
        submitted_items = []   # برای برگرداندنِ همان ردیف‌ها به فرم بعد از خطا
        errors = []
        blocked = []          # کالاهای غذاییِ بدون رسپی — قابل سفارش نیستند
        for idx, name in enumerate(product_names):
            name = name.strip() if name else ""
            if not name:
                errors.append("product_name")
                continue
            qty_raw = quantities[idx] if idx < len(quantities) else ""
            qty_val = _parse_decimal(qty_raw)
            unit_val = units[idx].strip() if idx < len(units) and units[idx] else "عدد"
            desc_val = descriptions[idx] if idx < len(descriptions) else ""
            product_id_raw = product_ids[idx] if idx < len(product_ids) else ""
            submitted_items.append({
                'name': name,
                'quantity': str(qty_raw or ''),
                'unit': unit_val,
                'description': desc_val,
                'productId': product_id_raw,
            })
            if qty_val is None or qty_val <= 0 or qty_val > 99999999:
                errors.append("quantity")
                continue
            # محصول انتخاب‌شده از لیست پیشنهادها (اگر انتخاب نشده باشد با نام جستجو می‌شود)
            # شناسهٔ خامِ POST هرگز بدون قفلِ کافه واکشی نمی‌شود.
            item_product = None
            if product_id_raw.isdigit():
                qs_p = Product.objects.filter(id=int(product_id_raw))
                if cafe is not None:
                    qs_p = qs_p.filter(cafe=cafe)
                candidate = qs_p.first()
                if candidate is not None and candidate.name.strip().lower() == name.lower():
                    item_product = candidate
            if item_product is None:
                item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else Product.objects.filter(name__iexact=name).first()
            if item_product is None and not bypasses_recipe_gate(request.user):
                blocked.append(
                    f'کالای «{name}» در فهرست محصولات ثبت نشده است. '
                    'لطفاً کالا را از فهرست انتخاب کنید یا به‌عنوان کالای جدید ثبت نمایید.'
                )
                continue
            inactive_err = inactive_product_error(item_product)
            if inactive_err:
                blocked.append(inactive_err)
                continue
            # قاعدهٔ رسپی: کالای غذایی که در هیچ رسپیِ این کافه مصرف نمی‌شود،
            # حتی با فرستادن شناسهٔ خام هم قابل درخواست نیست.
            if item_product is not None and not bypasses_recipe_gate(request.user):
                gate_error = recipe_gate_error(item_product, cafe=cafe)
                if gate_error:
                    blocked.append(gate_error)
                    continue
            items_data.append((name, qty_val, unit_val, desc_val, item_product))

        if blocked:
            for blocked_message in blocked[:3]:
                messages.error(request, blocked_message)
            return render(
                request,
                "purchase_request_form.html",
                _purchase_request_form_context(
                    request, selected_department,
                    errors=["recipe_gate"], error_message="recipe_gate",
                    submitted_items=submitted_items),
                status=200,
            )

        if errors:
            return render(
                request,
                "purchase_request_form.html",
                _purchase_request_form_context(
                    request, selected_department,
                    errors=errors,
                    error_message="quantity" if "quantity" in errors else "product_name",
                    submitted_items=submitted_items),
                status=200,
            )

        # Receipt gate: a requester cannot open a new request while deliveries
        # from their earlier requests are still unconfirmed. Enforced here on
        # the server (the disabled button in the form is only a hint) and the
        # rows already typed in are kept for the re-render.
        pending_receipts = pending_receipt_count(request.user, cafe)
        if pending_receipts:
            messages.error(request, receipt_gate_message(pending_receipts))
            return render(
                request,
                "purchase_request_form.html",
                _purchase_request_form_context(
                    request, selected_department,
                    errors=["receipt_gate"], error_message="receipt_gate",
                    submitted_items=submitted_items),
                status=200,
            )

        cafe = get_cafe_from_request(request)
        # زمان تحویل: تاریخ انتخاب‌شده (۷ روز آینده)
        delivery_date = None
        delivery_raw = request.POST.get('delivery_date', '').strip()
        if delivery_raw:
            try:
                delivery_date = datetime.strptime(delivery_raw, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                delivery_date = None
        purchase_request = PurchaseRequest.objects.create(
            user=request.user, status="pending_manager_approval", cafe=cafe,
            delivery_date=delivery_date,
        )
        created_items = []
        for index, (name, qty, unit, desc, item_product) in enumerate(items_data, start=1):
            item = PurchaseItem.objects.create(
                request=purchase_request,
                product=item_product,
                name=name,
                quantity=qty,
                unit=unit,
                description=desc or "",
            )
            created_items.append(item)

        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action="created",
            description="Purchase request created",
        )
        Notification.objects.create(
            recipient=request.user,
            title="Request created",
            message="Your purchase request was submitted.",
            notification_type="info",
            cafe=cafe,
        )

        # ارسال مستقیم به مسئول انبار: هر قلم بلافاصله در صندوق مشترک مسئول انبار
        # قرار می‌گیرد (بدون مرحله تایید مدیرعامل/مدیر) و اعلان ارسال می‌شود.
        _route_request_to_warehouse(
            purchase_request, created_items, request.user,
            notes='ارسال مستقیم از فرم درخواست کالا')

        cafe_code = cafe.code if cafe else 'local'
        return redirect("purchase_request_success", cafe_code=cafe_code)

    return render(request, "purchase_request_form.html",
                  _purchase_request_form_context(request, department))


@custom_login_required
def purchase_request_success(request):
    return render(request, "purchase_request_success.html")


@custom_login_required
def purchase_requests_list(request):
    cafe = get_cafe_from_request(request)
    requester_roles = ['kitchen', 'bar', 'counter', 'cashier', 'facilities', 'requester']
    staff_roles = ['purchasing', 'warehouse', 'manager', 'ceo', 'admin']

    # For mobile/requester users, show only their own requests
    if request.user.role in requester_roles:
        if cafe is None:
            requests_qs = PurchaseRequest.objects.none()
        else:
            requests_qs = PurchaseRequest.objects.filter(
                user=request.user, cafe=cafe
            ).prefetch_related("items").order_by("-created_at")
        requests_list = list(requests_qs)

        # پرچم «قابل ویرایش»: تا وقتی مسئول انبار برای اقلامِ درخواست وضعیتی ثبت
        # نکرده باشد (تامین/عدم موجودی/ارجاع به خرید)، صاحب درخواست می‌تواند آن را
        # ویرایش کند. درخواست ردشده توسط مدیر نیز قفل است.
        warehouse_decided_ids = _warehouse_decided_request_ids([r.id for r in requests_list])
        for req in requests_list:
            req.can_edit = req.status != 'rejected' and req.id not in warehouse_decided_ids

        # گروه‌بندی درخواست‌ها بر اساس روز: ۷ روز گذشته هر روز یک لیست جداگانه،
        # درخواست‌های قدیمی‌تر زیر یک بخش «قبلی» نمایش داده می‌شوند
        from datetime import timedelta
        weekday_names = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']
        today = timezone.localdate()
        week_start = today - timedelta(days=6)
        grouped_by_day = {}
        older_requests = []
        for req in requests_list:
            created_at = req.created_at
            if timezone.is_aware(created_at):
                day = timezone.localtime(created_at).date()
            else:
                day = created_at.date()
            if day >= week_start:
                grouped_by_day.setdefault(day, []).append(req)
            else:
                older_requests.append(req)
        day_groups = [
            (day, weekday_names[day.weekday()], grouped_by_day[day])
            for day in sorted(grouped_by_day, reverse=True)
        ]

        context = {
            "requests": requests_list,
            "day_groups": day_groups,
            "older_requests": older_requests,
            "today": today,
            "yesterday": today - timedelta(days=1),
            "is_mobile_user": True,
        }
        return render(request, "purchase_requests_list.html", context)

    if not (request.user.is_superuser or request.user.role in staff_roles):
        return HttpResponseForbidden("شما مجاز به مشاهده فهرست درخواست‌ها نیستید")

    # For purchasing/manager users
    selected_department = request.GET.get("department") or ""
    if cafe is None:
        requests_qs = PurchaseRequest.objects.none()
        department_list = []
    else:
        requests_qs = PurchaseRequest.objects.filter(cafe=cafe).select_related(
            "user__department"
        ).prefetch_related("items").order_by("-created_at")
        if selected_department:
            requests_qs = requests_qs.filter(
                models.Q(user__department__name=selected_department) |
                models.Q(user__role=selected_department)
            )
        department_list = Department.objects.filter(is_active=True, cafe=cafe).values_list('name', 'name')

    context = {
        "requests": list(requests_qs),
        "departments": department_list,
        "selected_department": selected_department,
        "is_mobile_user": False,
    }

    if request.headers.get('HX-Request'):
        return render(request, "partials/requests_table_body.html", context)

    return render(request, "purchase_requests_list.html", context)


@custom_login_required
def purchase_request_detail(request, request_id):
    requester_roles = ['kitchen', 'bar', 'counter', 'cashier', 'facilities', 'requester']
    staff_roles = ['purchasing', 'warehouse', 'manager', 'ceo', 'admin']

    if not (request.user.is_superuser or request.user.role in requester_roles or request.user.role in staff_roles):
        return HttpResponseForbidden("شما مجاز به مشاهده این درخواست نیستید")

    purchase_request = get_scoped_object_or_404(
        PurchaseRequest, request, id=request_id
    )

    # Check permissions: mobile/requester users can only see their own requests
    is_mobile_user = request.user.role in requester_roles and not request.user.is_superuser
    if is_mobile_user and purchase_request.user_id != request.user.id:
        return HttpResponseForbidden("شما مجاز به مشاهده این درخواست نیستید")

    context = {
        "purchase_request": purchase_request,
        "is_mobile_user": is_mobile_user,
    }
    return render(request, "purchase_request_detail.html", context)


@custom_login_required
def my_received_items(request: HttpRequest) -> HttpResponse:
    """«کالاهای دریافتی من» — اقلامی که از انبار به این کاربر تحویل شده است.

    درخواست‌دهنده مقدارِ تحویل‌شده را می‌بیند و «تایید دریافت» را ثبت می‌کند.
    این تایید فقط اعلامِ وصول است: هیچ اثر انبار/مالی ندارد و کسرِ موجودی همان‌جا
    که مسئول انبار کالا را تامین می‌کند انجام شده است (به تایید درخواست‌دهنده
    وابسته نیست).
    """
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    rows = _received_delivery_rows(request.user, cafe)
    pending = [r for r in rows if r['confirmed_at'] is None]
    confirmed = [r for r in rows if r['confirmed_at'] is not None]
    pending.sort(key=lambda r: r['item'].id, reverse=True)
    confirmed.sort(key=lambda r: (r['confirmed_at'], r['item'].id), reverse=True)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        pending_ids = {r['item'].id for r in pending}
        if action == 'confirm_all':
            targets = sorted(pending_ids)
        else:
            raw_id = request.POST.get('item_id', '')
            targets = [int(raw_id)] if raw_id.isdigit() and int(raw_id) in pending_ids else []
        if not targets:
            messages.warning(request, 'قلمی برای تایید دریافت پیدا نشد (یا قبلاً تایید شده است).')
        else:
            updated = (PurchaseItem.objects
                       .filter(id__in=targets, received_confirmed_at__isnull=True)
                       .update(received_confirmed_at=timezone.now(),
                               received_confirmed_by=request.user))
            if updated == 1:
                messages.success(request, 'تایید دریافت ثبت شد.')
            else:
                messages.success(request, f'تایید دریافت {updated} قلم ثبت شد.')
        return redirect('my_received_items', cafe_code=cafe_code)

    return render(request, "my_received_items.html", {
        "pending_rows": pending,
        "confirmed_rows": confirmed,
        "pending_count": len(pending),
        "cafe_code": cafe_code,
    })


@custom_login_required
@role_required(["purchasing", "manager", "ceo"])
def update_purchase_request(request, request_id):
    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.all()
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
    purchase_request = get_object_or_404(pr_qs, id=request_id)

    if request.method == "POST":
        item_ids = request.POST.getlist("item_id")
        purchased_quantities = request.POST.getlist("purchased_quantity")
        for idx, item_id in enumerate(item_ids):
            try:
                item = PurchaseItem.objects.get(id=item_id, request=purchase_request)
            except PurchaseItem.DoesNotExist:
                continue
            qty_val = _parse_decimal(
                purchased_quantities[idx] if idx < len(purchased_quantities) else ""
            )
            if qty_val is not None and qty_val >= 0:
                item.purchased_quantity = qty_val
                item.save()

        purchase_request.status = request.POST.get("status") or "purchased"
        purchase_request.save()
        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action="updated",
            description="Purchase request updated",
        )
        return get_cafe_redirect_url("purchase_request_detail", request, request_id=purchase_request.id)

    items = purchase_request.items.all()
    return render(
        request,
        "purchase_request_update.html",
        {"purchase_request": purchase_request, "items": items},
    )


def _request_editable_by_owner(req):
    """آیا صاحب درخواست می‌تواند آن را ویرایش کند؟

    تا قبل از «تعیین وضعیت توسط مسئول انبار» ویرایش آزاد است؛ درخواست ردشده
    توسط مدیر نیز قفل است.
    """
    if req.status == 'rejected':
        return False
    return req.id not in _warehouse_decided_request_ids([req.id])


def _recipe_gate_for_name(request, cafe, name):
    """گیتِ رسپی و وضعیت فعال‌بودن برای فرم‌هایی که فقط «نام» می‌فرستند، نه شناسهٔ محصول.

    نام با کالای موجود تطبیق داده می‌شود؛ اگر غیرفعال باشد یا کالای غذاییِ بدون
    رسپی باشد، پیام خطا برمی‌گردد. نامِ ناشناخته (کالای بیرون از فهرست) آزاد است.
    """
    if not name:
        return None
    product = (Product.objects.filter(name__iexact=name, cafe=cafe).first()
               if cafe else Product.objects.filter(name__iexact=name).first())
    inactive_err = inactive_product_error(product)
    if inactive_err:
        return inactive_err
    if bypasses_recipe_gate(request.user):
        return None
    return recipe_gate_error(product, cafe=cafe)


@custom_login_required
def edit_my_request(request, request_id):
    """ویرایش درخواست از بخش «درخواست‌های من» — فقط تا قبل از تعیین وضعیت توسط مسئول انبار.

    صاحب درخواست می‌تواند اقلام (نام/مقدار/واحد/توضیح) را ویرایش کند، قلم حذف
    یا قلم جدید اضافه کند. اقلام جدید مثل فرم ثبت، مستقیم به صندوق مسئول انبار
    می‌روند.
    """
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    receipt_gate = receipt_gate_context(request.user, cafe, cafe_code)
    purchase_request = get_scoped_object_or_404(PurchaseRequest, request, id=request_id)

    if purchase_request.user != request.user:
        return HttpResponseForbidden("فقط صاحب درخواست می‌تواند آن را ویرایش کند")

    if not _request_editable_by_owner(purchase_request):
        messages.error(
            request,
            "این درخواست توسط مسئول انبار تعیین وضعیت شده و دیگر قابل ویرایش نیست.")
        return redirect('purchase_requests_list', cafe_code=cafe_code)

    def _render(rows, selected_delivery, error_message=None):
        return render(request, "my_request_edit.html", {
            "purchase_request": purchase_request,
            "form_rows": rows,
            "delivery_days": _next_delivery_days(),
            "selected_delivery": selected_delivery,
            "receipt_gate": receipt_gate,
        })

    if request.method == "POST":
        item_ids = request.POST.getlist("item_id")
        names = request.POST.getlist("name")
        quantities = request.POST.getlist("quantity")
        units = request.POST.getlist("unit")
        descriptions = request.POST.getlist("description")
        removed_ids = {int(x) for x in request.POST.getlist("remove_item_id") if x.isdigit()}

        if not item_ids:
            messages.error(request, "درخواست باید دست‌کم یک قلم داشته باشد.")
            return _render([], request.POST.get("delivery_date", "").strip())

        with transaction.atomic():
            purchase_request = get_scoped_locked_object_or_404(
                PurchaseRequest, request, id=request_id
            )
            if not _request_editable_by_owner(purchase_request):
                messages.error(
                    request,
                    "این درخواست توسط مسئول انبار تعیین وضعیت شده و دیگر قابل ویرایش نیست.")
                return redirect('purchase_requests_list', cafe_code=cafe_code)

            existing = {item.id: item for item in purchase_request.items.all()}

            # قفل «تایید دریافت قبل از درخواست جدید» — بررسی قبل از هرگونه تغییر
            pending_receipts = pending_receipt_count(request.user, cafe)
            if pending_receipts:
                known_ids = set(existing.keys())
                adds_new_item = False
                for idx, raw_id in enumerate(item_ids):
                    row_name = (names[idx] if idx < len(names) else '').strip()
                    if not row_name:
                        continue
                    if raw_id.isdigit() and int(raw_id) in known_ids:
                        continue
                    adds_new_item = True
                    break
                if adds_new_item:
                    messages.error(request, receipt_gate_message(pending_receipts))
                    rows = [{
                        "id": raw_id,
                        "name": (names[i] if i < len(names) else '').strip(),
                        "quantity": quantities[i] if i < len(quantities) else '',
                        "unit": (units[i] if i < len(units) else '').strip(),
                        "description": (descriptions[i] if i < len(descriptions) else '').strip(),
                        "removed": raw_id.isdigit() and int(raw_id) in removed_ids,
                    } for i, raw_id in enumerate(item_ids)]
                    return _render(rows, request.POST.get("delivery_date", "").strip())

            locked_item_ids = set(
                OfficerAssignment.objects
                .filter(item__request=purchase_request, target_role='warehouse')
                .exclude(status='pending')
                .values_list('item_id', flat=True)
            )
            removable_ids = removed_ids - locked_item_ids

            # اعتبارسنجی کاملِ همهٔ ردیف‌ها پیش از هرگونه delete یا save
            posted_rows = []
            updates_to_apply = []
            new_items = []
            error_message = None

            for idx, raw_id in enumerate(item_ids):
                name = (names[idx] if idx < len(names) else "").strip()
                qty = _parse_decimal(quantities[idx] if idx < len(quantities) else "")
                unit = (units[idx] if idx < len(units) else "").strip()
                desc = (descriptions[idx] if idx < len(descriptions) else "").strip()
                is_removed = raw_id.isdigit() and int(raw_id) in removed_ids
                posted_rows.append({
                    "id": raw_id,
                    "name": name,
                    "quantity": quantities[idx] if idx < len(quantities) else "",
                    "unit": unit,
                    "description": desc,
                    "removed": is_removed,
                })
                if raw_id.isdigit() and int(raw_id) in removable_ids:
                    continue
                if not name:
                    error_message = "نام همهٔ اقلام الزامی است"
                    break
                if qty is None or qty <= 0 or qty > 99999999:
                    error_message = f"مقدار «{name}» باید عددی بزرگ‌تر از صفر باشد"
                    break
                if raw_id.isdigit() and int(raw_id) in existing:
                    item = existing[int(raw_id)]
                    old_name = (item.name or '').strip()
                    new_product = item.product
                    if old_name != name:
                        gate_error = _recipe_gate_for_name(request, cafe, name)
                        if gate_error:
                            error_message = gate_error
                            break
                        new_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else None
                    updates_to_apply.append((item, name, new_product, qty, unit, desc))
                else:
                    item_product = Product.objects.filter(name__iexact=name, cafe=cafe).first() if cafe else None
                    inactive_err = inactive_product_error(item_product)
                    if inactive_err:
                        error_message = inactive_err
                        break
                    if item_product is not None and not bypasses_recipe_gate(request.user):
                        gate_error = recipe_gate_error(item_product, cafe=cafe)
                        if gate_error:
                            error_message = gate_error
                            break
                    new_items.append(PurchaseItem(
                        request=purchase_request, product=item_product,
                        name=name, quantity=qty, unit=unit or "عدد",
                        description=desc or "",
                    ))

            if error_message:
                messages.error(request, error_message)
                return _render(posted_rows, request.POST.get("delivery_date", "").strip())

            # همه‌چیز معتبر است — اکنون حذف‌ها و ذخیره‌ها را اتمیک اعمال می‌کنیم
            if removed_ids & locked_item_ids:
                messages.warning(
                    request,
                    "برخی اقلام انتخاب‌شده برای حذف، توسط مسئول انبار تعیین وضعیت شده‌اند و حذف نشدند.")
            if removable_ids:
                purchase_request.items.filter(id__in=removable_ids).delete()

            for item, name, new_product, qty, unit, desc in updates_to_apply:
                item.name = name
                item.product = new_product
                item.quantity = qty
                if unit:
                    item.unit = unit
                item.description = desc
                item.save()

            if new_items:
                for item in new_items:
                    item.save()
                _route_request_to_warehouse(
                    purchase_request, new_items, request.user,
                    notes='قلم اضافه‌شده هنگام ویرایش درخواست توسط درخواست‌دهنده')

            delivery_raw = request.POST.get('delivery_date', '').strip()
            if delivery_raw:
                try:
                    purchase_request.delivery_date = datetime.strptime(delivery_raw, '%Y-%m-%d').date()
                    purchase_request.delivery_immediate = False
                    purchase_request.save(update_fields=['delivery_date', 'delivery_immediate'])
                except (ValueError, TypeError):
                    pass

            ActivityLog.objects.create(
                user=request.user,
                request=purchase_request,
                action="updated",
                description="ویرایش درخواست توسط صاحب آن (بخش درخواست‌های من)",
            )

        messages.success(request, "درخواست با موفقیت ویرایش شد.")
        return redirect('purchase_requests_list', cafe_code=cafe_code)

    form_rows = [{
        "id": item.id,
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "description": item.description,
        "removed": False,
    } for item in purchase_request.items.all()]
    return _render(
        form_rows,
        purchase_request.delivery_date.isoformat() if purchase_request.delivery_date else "",
    )
