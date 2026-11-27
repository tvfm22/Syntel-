"""
Django REST Framework Mobile API for Syntel Android App.

Covers the three mobile field roles:
  1. Requester (درخواست‌دهنده)
  2. Warehouse Officer / Manager (مسئول / مدیر انبار)
  3. Purchasing Officer (مسئول خرید)
  + Dual-role users (has_warehouse_role & has_purchasing_role)

Reuses Syntel's core business services (receipt gate, recipe gate, atomic stock
deduction, CEO approval split, multi-cafe scoping) without duplicating rules.
"""
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

try:
    import jdatetime
except ImportError:
    jdatetime = None

from .models import (
    ActivityLog,
    Cafe,
    Department,
    Notification,
    OfficerAssignment,
    Product,
    PurchaseDraft,
    PurchaseDraftItem,
    PurchaseItem,
    PurchaseRequest,
    StockItem,
    StockTransaction,
    Unit,
    Warehouse,
    WarehouseVoucher,
    WarehouseVoucherItem,
)
from .numbering import request_number as request_number_for
from .services.inventory import (
    InsufficientStockError,
    InventoryServiceError,
    ReopenNotAllowedError,
    StockScopeError,
    VoucherValidationError,
    apply_stock_delta,
    finalize_voucher,
    next_voucher_number,
    reopen_officer_assignments,
    save_voucher_with_number,
)
from .services.purchase_approval import evaluate_draft_items_ceo_status
from .services.receipt_gate import pending_receipt_count, receipt_gate_message
from .services.recipe_gate import (
    RD_CATEGORY_NAME,
    annotate_recipe_usage,
    bypasses_recipe_gate,
    inactive_product_error,
    recipe_gate_error,
    recipe_required_category_ids,
)
from .views.catalog import _filter_products_for_user
from .views.helpers import _fmt2, _parse_decimal, resolve_api_cafe

REQUESTER_ROLES = {"kitchen", "bar", "counter", "cashier", "facilities", "requester"}
MOBILE_REQUESTER_ROLES = REQUESTER_ROLES | {"store", "purchasing", "manager", "ceo", "admin"}
MOBILE_AUTH = [TokenAuthentication, SessionAuthentication]


def _jalali_date(dt) -> str:
    if dt is None:
        return ""
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        dt = timezone.localtime(dt)
    d = dt.date() if hasattr(dt, "date") else dt
    if jdatetime is not None:
        try:
            jd = jdatetime.date.fromgregorian(date=d)
            return f"{jd.year:04d}/{jd.month:02d}/{jd.day:02d}"
        except Exception:
            pass
    return d.isoformat()


def _jalali_datetime(dt) -> str:
    if dt is None:
        return ""
    local_dt = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    return f"{_jalali_date(local_dt)} - {local_dt.strftime('%H:%M')}"


def _serialize_user(user, active_cafe=None):
    is_requester = user.role in REQUESTER_ROLES
    cafe = active_cafe or user.cafe
    cafes = (
        [
            {"id": c.id, "code": c.code, "name": c.name}
            for c in Cafe.objects.filter(is_active=True).order_by("name")
        ]
        if (user.can_switch_cafe or user.is_superuser)
        else ([{"id": cafe.id, "code": cafe.code, "name": cafe.name}] if cafe else [])
    )
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.get_full_name() or user.username,
        "role": user.role,
        "role_display": user.get_role_display(),
        "secondary_role": user.secondary_role or "",
        "is_requester": is_requester,
        "has_warehouse_role": bool(user.has_warehouse_role or user.is_superuser or user.role == "admin"),
        "has_purchasing_role": bool(user.has_purchasing_role or user.is_superuser or user.role == "admin"),
        "has_dual_role": bool(user.has_dual_role),
        "department_name": user.department_display,
        "cafe_code": cafe.code if cafe else "",
        "cafe_name": cafe.name if cafe else "",
        "needs_cafe_selection": bool(user.needs_cafe_selection and cafe is None),
        "available_cafes": cafes,
    }


def _resolve_mobile_cafe(request, cafe_code: str):
    """Validate active cafe and ensure the authenticated user may access it."""
    cafe, err_status, err_msg = resolve_api_cafe(request, cafe_code=cafe_code)
    if err_status:
        return None, Response({"detail": err_msg}, status=err_status)
    return cafe, None


def _ensure_requester_access(user):
    return bool(
        user.is_superuser
        or user.role in MOBILE_REQUESTER_ROLES
        or (user.secondary_role or "") in MOBILE_REQUESTER_ROLES
    )


def _ensure_warehouse_access(user):
    return bool(user.is_superuser or user.role == "admin" or user.has_warehouse_role)


def _has_pure_purchasing_access(user):
    return bool(user.is_superuser or user.role == "admin" or user.has_purchasing_role)


def _ensure_purchasing_access(user):
    return bool(_has_pure_purchasing_access(user) or user.has_warehouse_role)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Authentication (DRF TokenAuth)
# ─────────────────────────────────────────────────────────────────────────────


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def mobile_login(request):
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    requested_cafe_code = (request.data.get("cafe_code") or "").strip()

    if not username or not password:
        return Response(
            {"detail": "نام کاربری و رمز عبور الزامی است."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        return Response(
            {"detail": "نام کاربری یا رمز عبور اشتباه است."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    active_cafe = None
    can_pick_any_cafe = bool(user.is_superuser or (user.cafe_id is None and user.role in user.CAFE_SELECTION_ROLES))
    if requested_cafe_code:
        if user.cafe_id is not None and not user.is_superuser:
            if not user.cafe or not user.cafe.is_active or user.cafe.code != requested_cafe_code:
                return Response({"detail": "دسترسی به این کافه مجاز نیست."}, status=status.HTTP_403_FORBIDDEN)
            active_cafe = user.cafe
        else:
            if not can_pick_any_cafe:
                return Response({"detail": "دسترسی به این کافه مجاز نیست."}, status=status.HTTP_403_FORBIDDEN)
            active_cafe = Cafe.objects.filter(code=requested_cafe_code, is_active=True).first()
            if active_cafe is None:
                return Response({"detail": "کافه انتخاب‌شده معتبر نیست."}, status=status.HTTP_400_BAD_REQUEST)
    elif user.cafe is not None:
        if not user.cafe.is_active:
            return Response({"detail": "کافه کاربر غیرفعال است."}, status=status.HTTP_403_FORBIDDEN)
        active_cafe = user.cafe
    elif can_pick_any_cafe:
        active_cafe = Cafe.objects.filter(is_active=True).order_by("name").first()

    if active_cafe is None:
        return Response(
            {"detail": "کافه برای کاربر شما تعریف نشده است. با مدیر تماس بگیرید."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token, _ = Token.objects.get_or_create(user=user)
    return Response({
        "token": token.key,
        "user": _serialize_user(user, active_cafe=active_cafe),
    })


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_me(request):
    cafe_code = (request.query_params.get("cafe_code") or "").strip()
    cafe = None
    if cafe_code:
        cafe, err = _resolve_mobile_cafe(request, cafe_code)
        if err:
            return err
    elif request.user.cafe and request.user.cafe.is_active:
        cafe = request.user.cafe
    elif request.user.can_switch_cafe or request.user.is_superuser:
        cafe = Cafe.objects.filter(is_active=True).order_by("name").first()
    return Response({"user": _serialize_user(request.user, active_cafe=cafe)})


@api_view(["POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_logout(request):
    Token.objects.filter(user=request.user).delete()
    return Response({"detail": "خروج با موفقیت انجام شد."})


# ─────────────────────────────────────────────────────────────────────────────
# 2. Requester Endpoints (درخواست‌دهنده)
# ─────────────────────────────────────────────────────────────────────────────


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_requester_summary(request, cafe_code: str):
    from .views import _received_delivery_rows

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_requester_access(request.user):
        return Response({"detail": "دسترسی به بخش درخواست ندارید."}, status=status.HTTP_403_FORBIDDEN)

    reqs = PurchaseRequest.objects.filter(user=request.user, cafe=cafe)
    received_rows = _received_delivery_rows(request.user, cafe)
    received_pending = sum(1 for r in received_rows if r["confirmed_at"] is None)
    gate_count = pending_receipt_count(request.user, cafe)

    return Response({
        "total_requests": reqs.count(),
        "pending_requests": reqs.filter(status__in=("pending_manager_approval", "pending", "partial")).count(),
        "completed_requests": reqs.filter(status="purchased").count(),
        "received_pending_count": received_pending,
        "receipt_gate_blocked": gate_count > 0,
        "receipt_gate_message": receipt_gate_message(gate_count) if gate_count > 0 else "",
    })


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_requester_products(request, cafe_code: str):
    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_requester_access(request.user):
        return Response({"detail": "دسترسی به بخش درخواست ندارید."}, status=status.HTTP_403_FORBIDDEN)

    qs = (
        Product.objects.filter(cafe=cafe, is_active=True, is_complete=True)
        .exclude(category__name=RD_CATEGORY_NAME)
        .select_related("unit", "category")
        .prefetch_related("departments")
        .order_by("name")
    )
    qs = _filter_products_for_user(qs, request.user, cafe)
    q = (request.query_params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(category__name__icontains=q))

    bypass = bypasses_recipe_gate(request.user)
    if not bypass:
        qs = annotate_recipe_usage(qs)
        food_cat_ids = recipe_required_category_ids(cafe)
    else:
        food_cat_ids = set()

    product_list = list(qs[:500])
    product_ids = [p.id for p in product_list]
    stock_totals = {}
    if product_ids:
        stock_totals = dict(
            StockItem.objects.filter(warehouse__cafe=cafe, product_id__in=product_ids)
            .values("product_id")
            .annotate(total=Sum("quantity"))
            .values_list("product_id", "total")
        )

    products = []
    for p in product_list:
        blocked_reason = ""
        if not bypass and p.category_id and p.category_id in food_cat_ids and not p.used_in_cogs_recipe:
            blocked_reason = (
                f'کالای «{p.name}» در هیچ رسپی منو مصرف نمی‌شود. '
                'کالاهای غذایی باید اول در رسپی یک غذا یا نیمه‌آماده استفاده شوند، '
                'وگرنه قابل درخواست نیستند.'
            )
        products.append({
            "id": p.id,
            "name": p.name,
            "unit_name": p.unit.name if p.unit else "عدد",
            "category_name": p.category.name if p.category else "عمومی",
            "current_stock": stock_totals.get(p.id) or Decimal("0"),
            "is_blocked": bool(blocked_reason),
            "blocked_reason": blocked_reason,
        })

    units = list(
        Unit.objects.filter(cafe=cafe, is_active=True)
        .order_by("name")
        .values_list("name", flat=True)
    )
    if not units:
        units = ["عدد", "کیلوگرم", "گرم", "لیتر", "بسته", "کارتن"]

    return Response({"products": products, "units": units})


def _validate_requester_items(raw_items, cafe, user):
    if not isinstance(raw_items, list) or not raw_items:
        return None, Response(
            {"detail": "لطفاً حداقل یک قلم کالا به درخواست اضافه کنید."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    bypass = bypasses_recipe_gate(user)
    items_data = []
    for row in raw_items:
        if not isinstance(row, dict):
            return None, Response(
                {"detail": "ساختار اقلام درخواست نامعتبر است."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        name = (row.get("name") or "").strip()
        qty_val = _parse_decimal(str(row.get("quantity", "")))
        unit_val = (row.get("unit") or "عدد").strip() or "عدد"
        desc_val = (row.get("description") or "").strip()
        product_id = row.get("product_id")

        if not name:
            return None, Response({"detail": "نام کالا در تمام ردیف‌ها الزامی است."}, status=status.HTTP_400_BAD_REQUEST)
        if len(name) > 100:
            return None, Response({"detail": "نام کالا نباید بیش از ۱۰۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
        if len(unit_val) > 20:
            return None, Response({"detail": "نام واحد نباید بیش از ۲۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
        if len(desc_val) > 1000:
            return None, Response({"detail": "توضیحات قلم نباید بیش از ۱۰۰۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
        if qty_val is None or not qty_val.is_finite() or qty_val <= 0 or qty_val > Decimal("99999999"):
            return None, Response({"detail": f"مقدار معتبر برای «{name}» وارد کنید."}, status=status.HTTP_400_BAD_REQUEST)

        accessible_qs = _filter_products_for_user(
            Product.objects.filter(cafe=cafe, is_active=True, is_complete=True)
            .exclude(category__name=RD_CATEGORY_NAME),
            user,
            cafe,
        )
        item_product = None
        if product_id and str(product_id).isdigit():
            candidate = accessible_qs.filter(id=int(product_id)).first()
            if candidate is not None and candidate.name.strip().lower() == name.lower():
                item_product = candidate
        if item_product is None:
            item_product = accessible_qs.filter(name__iexact=name).first()

        if item_product is None and not bypass:
            return None, Response(
                {"detail": f"کالای «{name}» در فهرست محصولات ثبت نشده است. لطفاً از فهرست انتخاب کنید."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        inactive_err = inactive_product_error(item_product)
        if inactive_err:
            return None, Response({"detail": inactive_err}, status=status.HTTP_400_BAD_REQUEST)
        if item_product is not None and not bypass:
            gate_err = recipe_gate_error(item_product, cafe=cafe)
            if gate_err:
                return None, Response({"detail": gate_err}, status=status.HTTP_400_BAD_REQUEST)

        resolved_name = item_product.name if item_product else name
        items_data.append((resolved_name, qty_val, unit_val, desc_val, item_product))

    return items_data, None


@api_view(["GET", "POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_requester_requests(request, cafe_code: str):
    from .views import _route_request_to_warehouse, _warehouse_decided_request_ids

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_requester_access(request.user):
        return Response({"detail": "دسترسی به بخش درخواست ندارید."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        qs = (
            PurchaseRequest.objects.filter(user=request.user, cafe=cafe)
            .prefetch_related("items")
            .order_by("-created_at")[:150]
        )
        reqs = list(qs)
        decided_ids = _warehouse_decided_request_ids([r.id for r in reqs])
        data = []
        for req in reqs:
            can_edit = req.status != "rejected" and req.id not in decided_ids
            items = [
                {
                    "id": it.id,
                    "product_id": it.product_id,
                    "name": it.name,
                    "quantity": _fmt2(it.quantity),
                    "unit": it.unit or "عدد",
                    "description": it.description or "",
                    "supply_status": it.supply_status,
                    "supply_status_label": it.get_supply_status_display(),
                    "purchased_quantity": _fmt2(it.purchased_quantity) if it.purchased_quantity is not None else "",
                }
                for it in req.items.all()
            ]
            data.append({
                "id": req.id,
                "request_number": str(req.request_number),
                "status": req.status,
                "status_label": req.get_status_display(),
                "created_at": req.created_at.isoformat(),
                "created_at_jalali": _jalali_datetime(req.created_at),
                "created_date_iso": timezone.localtime(req.created_at).date().isoformat(),
                "delivery_date": req.delivery_date.isoformat() if req.delivery_date else "",
                "delivery_date_jalali": _jalali_date(req.delivery_date) if req.delivery_date else "",
                "delivery_immediate": bool(req.delivery_immediate),
                "rejection_reason": req.rejection_reason or "",
                "can_edit": can_edit,
                "items": items,
            })
        return Response({"requests": data})

    # POST — Create PurchaseRequest
    pending_receipts = pending_receipt_count(request.user, cafe)
    if pending_receipts:
        return Response(
            {"detail": receipt_gate_message(pending_receipts), "code": "receipt_gate"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    items_data, items_err = _validate_requester_items(request.data.get("items"), cafe, request.user)
    if items_err:
        return items_err

    delivery_date = None
    delivery_raw = (request.data.get("delivery_date") or "").strip()
    if delivery_raw:
        try:
            delivery_date = datetime.strptime(delivery_raw, "%Y-%m-%d").date()
        except ValueError:
            return Response({"detail": "فرمت تاریخ تحویل نامعتبر است."}, status=status.HTTP_400_BAD_REQUEST)
    delivery_immediate = bool(request.data.get("delivery_immediate", False))

    with transaction.atomic():
        purchase_request = PurchaseRequest.objects.create(
            user=request.user,
            status="pending_manager_approval",
            cafe=cafe,
            delivery_date=delivery_date,
            delivery_immediate=delivery_immediate,
        )
        created_items = [
            PurchaseItem.objects.create(
                request=purchase_request,
                product=item_product,
                name=name,
                quantity=qty,
                unit=unit,
                description=desc,
            )
            for name, qty, unit, desc, item_product in items_data
        ]
        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action="created",
            description="Purchase request created via Android app",
        )
        Notification.objects.create(
            recipient=request.user,
            cafe=cafe,
            title="ثبت درخواست جدید",
            message=f"درخواست شماره {purchase_request.request_number} با موفقیت ثبت شد.",
            notification_type="info",
        )
        _route_request_to_warehouse(
            purchase_request,
            created_items,
            request.user,
            notes="ارسال مستقیم از اپلیکیشن اندروید",
        )

    return Response(
        {
            "id": purchase_request.id,
            "request_number": str(purchase_request.request_number),
            "detail": f"درخواست شماره {purchase_request.request_number} با موفقیت ثبت و به انبار ارسال شد.",
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["PATCH"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_requester_edit_request(request, cafe_code: str, request_id: int):
    from .views import _route_request_to_warehouse, _warehouse_decided_request_ids

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_requester_access(request.user):
        return Response({"detail": "دسترسی به بخش درخواست ندارید."}, status=status.HTTP_403_FORBIDDEN)

    pending_receipts = pending_receipt_count(request.user, cafe)
    if pending_receipts:
        return Response(
            {"detail": receipt_gate_message(pending_receipts), "code": "receipt_gate"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    items_data, items_err = _validate_requester_items(request.data.get("items"), cafe, request.user)
    if items_err:
        return items_err

    delivery_date = None
    has_delivery_update = False
    if "delivery_date" in request.data:
        delivery_raw = (request.data.get("delivery_date") or "").strip()
        if delivery_raw:
            try:
                delivery_date = datetime.strptime(delivery_raw, "%Y-%m-%d").date()
                has_delivery_update = True
            except ValueError:
                return Response({"detail": "فرمت تاریخ تحویل نامعتبر است."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        pr = get_object_or_404(
            PurchaseRequest.objects.select_for_update(),
            id=request_id,
            user=request.user,
            cafe=cafe,
        )
        if pr.status == "rejected" or pr.id in _warehouse_decided_request_ids([pr.id]):
            return Response(
                {"detail": "این درخواست توسط انبار بررسی شده و دیگر قابل ویرایش نیست."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pr.items.all().delete()
        new_items = [
            PurchaseItem.objects.create(
                request=pr,
                product=item_product,
                name=name,
                quantity=qty_val,
                unit=unit_val,
                description=desc_val,
            )
            for name, qty_val, unit_val, desc_val, item_product in items_data
        ]
        if has_delivery_update:
            pr.delivery_date = delivery_date
            pr.save(update_fields=["delivery_date"])
        _route_request_to_warehouse(pr, new_items, request.user, notes="ویرایش درخواست از اپلیکیشن اندروید")

    return Response({"detail": f"درخواست {pr.request_number} به‌روزرسانی شد."})


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_received_items(request, cafe_code: str):
    from .views import _received_delivery_rows

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_requester_access(request.user):
        return Response({"detail": "دسترسی به بخش درخواست ندارید."}, status=status.HTTP_403_FORBIDDEN)

    rows = _received_delivery_rows(request.user, cafe)
    pending = [r for r in rows if r["confirmed_at"] is None]
    confirmed = [r for r in rows if r["confirmed_at"] is not None]
    pending.sort(key=lambda r: r["item"].id, reverse=True)
    confirmed.sort(key=lambda r: (r["confirmed_at"], r["item"].id), reverse=True)

    def _ser(r):
        return {
            "item_id": r["item"].id,
            "request_number": str(r["request"].request_number),
            "name": r["name"],
            "requested_qty": str(r["requested_qty"]),
            "unit": r["unit"],
            "deliveries": r["deliveries"],
            "is_confirmed": r["confirmed_at"] is not None,
            "confirmed_at_jalali": _jalali_datetime(r["confirmed_at"]) if r["confirmed_at"] else "",
        }

    return Response({
        "pending_count": len(pending),
        "pending": [_ser(r) for r in pending],
        "confirmed": [_ser(r) for r in confirmed[:80]],
    })


@api_view(["POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_confirm_received_items(request, cafe_code: str):
    from .views import _received_delivery_rows

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_requester_access(request.user):
        return Response({"detail": "دسترسی به بخش درخواست ندارید."}, status=status.HTTP_403_FORBIDDEN)

    rows = _received_delivery_rows(request.user, cafe)
    pending_ids = {r["item"].id for r in rows if r["confirmed_at"] is None}

    action = (request.data.get("action") or "confirm_one").strip()
    if action == "confirm_all":
        targets = sorted(pending_ids)
    else:
        raw_id = request.data.get("item_id")
        targets = [int(raw_id)] if str(raw_id or "").isdigit() and int(raw_id) in pending_ids else []

    if not targets:
        return Response(
            {"detail": "قلمی برای تایید دریافت پیدا نشد یا قبلاً تایید شده است."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    updated = (
        PurchaseItem.objects.filter(id__in=targets, received_confirmed_at__isnull=True)
        .update(received_confirmed_at=timezone.now(), received_confirmed_by=request.user)
    )
    msg = "تایید دریافت ثبت شد." if updated == 1 else f"تایید دریافت {updated} قلم ثبت شد."
    return Response({"detail": msg, "confirmed_count": updated})


# ─────────────────────────────────────────────────────────────────────────────
# 3. Warehouse Manager / Officer Endpoints (مسئول انبار)
# ─────────────────────────────────────────────────────────────────────────────


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_dashboard(request, cafe_code: str):
    from .views import _item_sent_to_ceo

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به بخش انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    now_local = timezone.localtime(timezone.now())
    today = now_local.date()
    day_filter = (request.query_params.get("day") or today.isoformat()).strip()
    dept = (request.query_params.get("department") or "").strip()

    try:
        selected_date = datetime.strptime(day_filter, "%Y-%m-%d").date()
    except ValueError:
        selected_date = today

    day_start = timezone.make_aware(datetime.combine(selected_date, datetime.min.time()))
    requests_qs = (
        PurchaseRequest.objects.select_related("user", "user__department")
        .prefetch_related("items", "items__product", "items__product__unit", "items__officer_assignments")
        .filter(cafe=cafe, created_at__gte=day_start, created_at__lt=day_start + timedelta(days=1))
        .exclude(source="warehouse_purchase")
        .order_by("-created_at")
    )
    if dept:
        requests_qs = requests_qs.filter(Q(user__department__name=dept) | Q(user__role=dept))

    requests_list = list(requests_qs)
    scope_pids = sorted({it.product_id for r in requests_list for it in r.items.all() if it.product_id})

    stock_by_product = defaultdict(list)
    total_stock_map = defaultdict(Decimal)
    if scope_pids:
        sis = (
            StockItem.objects.filter(
                Q(quantity__gt=0) | Q(warehouse__allow_negative_stock=True, warehouse__is_active=True),
                product_id__in=scope_pids,
                warehouse__cafe=cafe,
            )
            .select_related("warehouse", "unit")
            .order_by("warehouse__name")
        )
        for si in sis:
            u_label = (si.unit.name or si.unit.symbol) if si.unit else ""
            total_stock_map[si.product_id] += si.quantity
            stock_by_product[si.product_id].append({
                "stock_item_id": si.id,
                "warehouse_name": si.warehouse.name if si.warehouse else "انبار",
                "available_qty": _fmt2(si.quantity),
                "unit_label": u_label,
            })

    cards = []
    stats = {"pending": 0, "done": 0, "enough": 0, "short": 0, "no_stock": 0}
    for req in requests_list:
        visible, done = [], []
        for item in req.items.all():
            assigns = [a for a in item.officer_assignments.all() if a.status != "reopened"]
            reopened = [a for a in item.officer_assignments.all() if a.target_role == "warehouse" and a.status == "reopened"]
            my_pending = [
                a for a in assigns
                if a.target_role == "warehouse" and a.status == "pending"
                and (a.assigned_to_id == request.user.id or a.assigned_to_id is None)
            ]
            available = total_stock_map.get(item.product_id, Decimal("0")) if item.product_id else Decimal("0")
            enough = available >= (item.quantity or Decimal("0"))
            has_stock = available > 0

            if my_pending or (not assigns and (reopened or has_stock)):
                stats["pending"] += 1
                if enough:
                    stats["enough"] += 1
                elif has_stock:
                    stats["short"] += 1
                else:
                    stats["no_stock"] += 1
                visible.append({
                    "item_id": item.id,
                    "product_id": item.product_id,
                    "name": item.product.name if item.product else item.name,
                    "quantity": _fmt2(item.quantity),
                    "unit": item.unit or "عدد",
                    "description": item.description or "",
                    "available_qty": _fmt2(available),
                    "has_stock": has_stock,
                    "is_enough": enough,
                    "stock_options": stock_by_product.get(item.product_id, []),
                })
            else:
                my_done = [
                    a for a in assigns
                    if a.target_role == "warehouse"
                    and a.status in ("fulfilled", "not_in_stock", "returned")
                    and (a.assigned_to_id == request.user.id or a.assigned_to_id is None)
                ]
                if my_done:
                    a0 = my_done[0]
                    sent_copy = _item_sent_to_ceo(item)
                    stats["done"] += 1
                    done.append({
                        "item_id": item.id,
                        "name": item.product.name if item.product else item.name,
                        "quantity": _fmt2(item.quantity),
                        "unit": item.unit or "عدد",
                        "status": a0.status,
                        "status_label": a0.get_status_display(),
                        "supplied_qty": _fmt2(a0.supplied_quantity) if a0.supplied_quantity is not None else "",
                        "notes": a0.notes or "",
                        "is_locked": sent_copy is not None,
                    })

        if visible or done:
            cards.append({
                "request_id": req.id,
                "request_number": str(req.request_number),
                "requester_name": req.user.get_full_name() or req.user.username,
                "department_name": req.user.department_display,
                "created_at_jalali": _jalali_datetime(req.created_at),
                "delivery_date_jalali": _jalali_date(req.delivery_date) if req.delivery_date else "",
                "delivery_immediate": bool(req.delivery_immediate),
                "visible_items": visible,
                "done_items": done,
            })

    cards.sort(key=lambda c: 0 if c["visible_items"] else 1)

    departments = list(
        Department.objects.filter(is_active=True, cafe=cafe).order_by("name").values_list("name", flat=True)
    )
    active_draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
    draft_count = active_draft.items.count() if active_draft else 0
    my_purchases_count = (
        OfficerAssignment.objects.filter(
            target_role="purchasing",
            status="pending",
            assigned_to=request.user,
            item__request__cafe=cafe,
        )
        .exclude(item__request__status__in=("pending_manager_approval", "rejected", "draft"))
        .count()
    )

    return Response({
        "selected_date": selected_date.isoformat(),
        "selected_date_jalali": _jalali_date(selected_date),
        "today_iso": today.isoformat(),
        "stats": stats,
        "departments": departments,
        "draft_count": draft_count,
        "my_purchases_count": my_purchases_count,
        "cards": cards,
    })


@api_view(["POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_item_action(request, cafe_code: str, item_id: int):
    from .views import (
        _assign_item,
        _get_or_create_purchase_draft,
        _item_sent_to_ceo,
        _remove_from_purchase_drafts,
        _sync_request_status,
    )

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به بخش انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    action = (request.data.get("action") or "").strip()
    if action not in ("fulfill", "to_purchase", "not_in_stock", "reopen"):
        return Response({"detail": "عملیات نامعتبر است."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        item = get_object_or_404(
            PurchaseItem.objects.select_for_update(),
            id=item_id,
            request__cafe=cafe,
        )

        if _item_sent_to_ceo(item) is not None:
            return Response(
                {"detail": f"«{item.name}» در لیست خرید ثبت شده و برای تایید مدیرعامل ارسال شده است."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment = (
            item.officer_assignments.select_for_update()
            .filter(target_role="warehouse", status="pending")
            .filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))
            .first()
        )
        done_assigns = (
            item.officer_assignments.select_for_update()
            .filter(target_role="warehouse", status__in=("fulfilled", "returned", "not_in_stock"))
            .filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))
        )

        if action == "fulfill":
            if done_assigns.exists():
                return Response({"detail": "این قلم قبلاً پردازش شده است."}, status=status.HTTP_400_BAD_REQUEST)
            allocations = request.data.get("allocations")
            if not isinstance(allocations, list) or not allocations:
                return Response({"detail": "موجودی انبار و مقدار را انتخاب کنید."}, status=status.HTTP_400_BAD_REQUEST)

            merged = defaultdict(Decimal)
            for alloc in allocations:
                if not isinstance(alloc, dict):
                    return Response({"detail": "ساختار تخصیص انبار نامعتبر است."}, status=status.HTTP_400_BAD_REQUEST)
                sid = alloc.get("stock_item_id")
                qty = _parse_decimal(str(alloc.get("quantity", "")))
                if not str(sid or "").isdigit() or qty is None or not qty.is_finite() or qty <= 0 or qty > Decimal("99999999"):
                    return Response({"detail": "موجودی انبار و مقدار معتبر انتخاب کنید."}, status=status.HTTP_400_BAD_REQUEST)
                merged[int(sid)] += qty

            locked_sis = {
                si.id: si
                for si in StockItem.objects.select_for_update()
                .select_related("product", "warehouse")
                .filter(id__in=sorted(merged.keys()), warehouse__cafe=cafe)
                .order_by("id")
            }

            validated = []
            for sid in sorted(merged.keys()):
                qty = merged[sid]
                si = locked_sis.get(sid)
                if si is None:
                    return Response({"detail": "موجودی انبار و مقدار معتبر انتخاب کنید."}, status=status.HTTP_400_BAD_REQUEST)
                if si.product_id != item.product_id:
                    return Response({"detail": "موجودی انتخاب‌شده با کالا مطابقت ندارد."}, status=status.HTTP_400_BAD_REQUEST)
                if not (si.warehouse and si.warehouse.allow_negative_stock) and qty > si.quantity:
                    return Response(
                        {"detail": f"موجودی «{si.product.name}» ({_fmt2(si.quantity)}) کمتر از مقدار انتخابی است."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                validated.append((si, qty))

            total_qty = sum((q for _, q in validated), Decimal("0"))
            if total_qty > item.quantity:
                return Response({"detail": "مقدار تامین‌شده از مقدار درخواستی بیشتر است."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                for idx, (si, qty) in enumerate(validated):
                    apply_stock_delta(
                        si,
                        -qty,
                        cafe=cafe,
                        created_by=request.user,
                        reference_type="officer_assignment",
                        reference_id=item.id,
                        description=f"تامین کالا (اپ موبایل) - {item.name} - درخواست {request_number_for(item.request_id)}",
                    )
                    if idx == 0:
                        if assignment is None:
                            assignment = _assign_item(item, "warehouse", request.user, request.user)
                        elif assignment.assigned_to_id is None:
                            assignment.assigned_to = request.user
                        assignment.status = "fulfilled"
                        assignment.stock_item = si
                        assignment.supplied_quantity = qty
                        assignment.completed_at = timezone.now()
                        assignment.notes = (assignment.notes or "") + f" تامین شد: {_fmt2(qty)} از انبار {si.warehouse.name if si.warehouse else ''}."
                        assignment.save()
                    else:
                        extra = _assign_item(item, "warehouse", request.user, request.user, status="fulfilled")
                        extra.stock_item = si
                        extra.supplied_quantity = qty
                        extra.completed_at = timezone.now()
                        extra.notes = f"تامین شد: {_fmt2(qty)} از انبار {si.warehouse.name if si.warehouse else ''}."
                        extra.save()
            except (InsufficientStockError, StockScopeError) as exc:
                transaction.set_rollback(True)
                return Response({"detail": f"موجودی کافی نیست: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

            item.supply_status = "from_stock"
            item.purchased_quantity = total_qty
            item.save(update_fields=["supply_status", "purchased_quantity"])
            _remove_from_purchase_drafts(item)
            _sync_request_status(item.request)
            return Response({"detail": f"«{item.name}» با موفقیت از انبار تامین شد."})

        if action == "to_purchase":
            if done_assigns.exists():
                return Response({"detail": "این قلم قبلاً پردازش شده است."}, status=status.HTTP_400_BAD_REQUEST)
            if assignment is None:
                assignment = _assign_item(item, "warehouse", request.user, request.user)
            elif assignment.assigned_to_id is None:
                assignment.assigned_to = request.user
            assignment.status = "returned"
            assignment.completed_at = timezone.now()
            assignment.notes = (assignment.notes or "") + " ارجاع به درخواست خرید."
            assignment.save()
            _sync_request_status(item.request)
            draft = _get_or_create_purchase_draft(cafe, request.user)
            PurchaseDraftItem.objects.get_or_create(
                draft=draft,
                source_item=item,
                defaults={
                    "product": item.product,
                    "name": item.product.name if item.product else item.name,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "description": item.description or "",
                },
            )
            return Response({"detail": f"«{item.name}» به پیش‌نویس درخواست خرید اضافه شد."})

        if action == "not_in_stock":
            if done_assigns.filter(status__in=("fulfilled", "not_in_stock")).exists():
                return Response({"detail": "این قلم قبلاً پردازش شده است."}, status=status.HTTP_400_BAD_REQUEST)
            if assignment is None:
                assignment = done_assigns.order_by("-assigned_at").first() or _assign_item(item, "warehouse", request.user, request.user)
            if assignment.assigned_to_id is None:
                assignment.assigned_to = request.user
            assignment.status = "not_in_stock"
            assignment.completed_at = timezone.now()
            assignment.notes = (assignment.notes or "") + " عدم امکان تأمین ثبت شد."
            assignment.save()
            item.supply_status = "not_supplied"
            item.purchased_quantity = Decimal("0")
            item.save(update_fields=["supply_status", "purchased_quantity"])
            _remove_from_purchase_drafts(item)
            _sync_request_status(item.request)
            return Response({"detail": f"برای «{item.name}» عدم موجودی ثبت شد."})

        # reopen
        reason = (request.data.get("reason") or "").strip()
        if len(reason) > 500:
            return Response({"detail": "دلیل بازگشایی نباید بیش از ۵۰۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            reopen_officer_assignments(item, cafe=cafe, reopened_by=request.user, reason=reason)
        except (ReopenNotAllowedError, StockScopeError, InsufficientStockError) as exc:
            return Response({"detail": f"بازگشایی ناموفق بود: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
        item.supply_status = "pending"
        item.purchased_quantity = None
        item.save(update_fields=["supply_status", "purchased_quantity"])
        _remove_from_purchase_drafts(item)
        _sync_request_status(item.request)
        return Response({"detail": f"«{item.name}» بازگشایی شد و به صف انبار برگشت."})


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_draft(request, cafe_code: str):
    from .views import _officer_users

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به بخش انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    draft = PurchaseDraft.objects.filter(cafe=cafe, created_by=request.user).first()
    draft_items = list(
        draft.items.select_related("product", "product__unit", "product__category", "source_item")
    ) if draft else []
    eval_map = evaluate_draft_items_ceo_status(draft_items, cafe=cafe) if draft_items else {}

    items_payload = []
    for di in draft_items:
        ev = eval_map.get(di.id, {"needs_ceo": False, "reasons": []})
        items_payload.append({
            "id": di.id,
            "product_id": di.product_id,
            "name": di.name,
            "quantity": _fmt2(di.quantity),
            "unit": di.unit or "عدد",
            "description": di.description or "",
            "needs_ceo": bool(ev.get("needs_ceo")),
            "ceo_reasons": list(ev.get("reasons") or []),
            "from_request": di.source_item_id is not None,
        })

    officers = [
        {"id": str(u.id), "name": u.get_full_name() or u.username}
        for u in _officer_users(cafe, "purchasing")
    ]
    officers.insert(0, {"id": "self", "name": "خرید توسط خودم (خریدهای من)"})

    return Response({
        "items": items_payload,
        "officers": officers,
    })


@api_view(["POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_draft_add(request, cafe_code: str):
    from .views import _get_or_create_purchase_draft, _resolve_draft_product

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به بخش انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    name = (request.data.get("name") or "").strip()
    product_id = request.data.get("product_id")
    qty = _parse_decimal(str(request.data.get("quantity", "")))
    unit = (request.data.get("unit") or "").strip()
    description = (request.data.get("description") or "").strip()

    item_product = _resolve_draft_product(cafe, product_id, name)
    if item_product is None and not name:
        return Response({"detail": "نام کالا را وارد کنید."}, status=status.HTTP_400_BAD_REQUEST)
    if len(name) > 100:
        return Response({"detail": "نام کالا نباید بیش از ۱۰۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
    if len(unit) > 20:
        return Response({"detail": "نام واحد نباید بیش از ۲۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
    if len(description) > 1000:
        return Response({"detail": "توضیحات نباید بیش از ۱۰۰۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
    inactive_err = inactive_product_error(item_product)
    if inactive_err:
        return Response({"detail": inactive_err}, status=status.HTTP_400_BAD_REQUEST)
    if qty is None or not qty.is_finite() or qty <= 0 or qty > Decimal("99999999"):
        return Response({"detail": "مقدار باید عدد مثبت معتبر باشد."}, status=status.HTTP_400_BAD_REQUEST)

    draft = _get_or_create_purchase_draft(cafe, request.user)
    resolved_unit = unit or (item_product.unit.name if item_product and item_product.unit else "عدد")
    item_label = item_product.name if item_product else name

    di = PurchaseDraftItem.objects.create(
        draft=draft,
        product=item_product,
        name=item_label,
        quantity=qty,
        unit=resolved_unit,
        description=description,
    )
    return Response({"id": di.id, "detail": f"«{item_label}» به پیش‌نویس اضافه شد."}, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_draft_item_detail(request, cafe_code: str, item_id: int):
    from .views import _item_sent_to_ceo, _sync_request_status

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به بخش انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "DELETE":
        with transaction.atomic():
            d_item = get_object_or_404(
                PurchaseDraftItem.objects.select_for_update(),
                id=item_id,
                draft__cafe=cafe,
                draft__created_by=request.user,
            )
            draft = d_item.draft
            name = d_item.name
            if d_item.source_item_id:
                src_item = d_item.source_item
                if _item_sent_to_ceo(src_item) is not None:
                    return Response(
                        {"detail": f"«{name}» در لیست خرید ثبت شده و برای تایید مدیرعامل ارسال شده است."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    reopen_officer_assignments(
                        src_item,
                        cafe=cafe,
                        reopened_by=request.user,
                        reason="حذف از لیست خرید و بازگشت به لیست انبار",
                    )
                    src_item.supply_status = "pending"
                    src_item.purchased_quantity = None
                    src_item.save(update_fields=["supply_status", "purchased_quantity"])
                except ReopenNotAllowedError:
                    pass
                except (StockScopeError, InsufficientStockError) as exc:
                    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                _sync_request_status(src_item.request)
            d_item.delete()
            if not draft.items.exists():
                draft.delete()
        return Response({"detail": f"«{name}» از پیش‌نویس حذف شد."})

    d_item = get_object_or_404(
        PurchaseDraftItem, id=item_id, draft__cafe=cafe, draft__created_by=request.user
    )
    qty = _parse_decimal(str(request.data.get("quantity", "")))
    if qty is None or not qty.is_finite() or qty <= 0 or qty > Decimal("99999999"):
        return Response({"detail": "مقدار باید عدد مثبت معتبر باشد."}, status=status.HTTP_400_BAD_REQUEST)
    d_item.quantity = qty
    if "description" in request.data:
        desc_val = (request.data.get("description") or "").strip()
        if len(desc_val) > 1000:
            return Response({"detail": "توضیحات نباید بیش از ۱۰۰۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
        d_item.description = desc_val
    d_item.save(update_fields=["quantity", "description"])
    return Response({"detail": f"«{d_item.name}» به‌روزرسانی شد."})


@api_view(["POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_draft_send(request, cafe_code: str):
    from .views import _dispatch_warehouse_purchase_items, _resolve_purchasing_target_officer

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به بخش انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    officer_raw = str(request.data.get("officer_user_id") or "").strip()
    if not officer_raw:
        return Response({"detail": "لطفاً مسئول خرید یا «خرید توسط خودم» را انتخاب کنید."}, status=status.HTTP_400_BAD_REQUEST)

    target_officer, is_self_buy, err_msg = _resolve_purchasing_target_officer(cafe, request.user, officer_raw)
    if err_msg:
        return Response({"detail": err_msg}, status=status.HTTP_400_BAD_REQUEST)

    selected_ids = request.data.get("selected_item_ids")
    if selected_ids is not None:
        if not isinstance(selected_ids, list) or not selected_ids:
            return Response({"detail": "حداقل یک قلم را انتخاب کنید."}, status=status.HTTP_400_BAD_REQUEST)
        id_set = {int(x) for x in selected_ids if str(x).isdigit()}
        if not id_set:
            return Response({"detail": "حداقل یک قلم را انتخاب کنید."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        id_set = None

    with transaction.atomic():
        draft = PurchaseDraft.objects.select_for_update().filter(cafe=cafe, created_by=request.user).first()
        draft_items = list(
            draft.items.select_related("product", "product__unit", "product__category", "source_item")
        ) if draft else []
        if not draft_items:
            return Response({"detail": "پیش‌نویس خرید خالی است."}, status=status.HTTP_400_BAD_REQUEST)

        if id_set is not None:
            draft_items = [d for d in draft_items if d.id in id_set]
            if not draft_items:
                return Response({"detail": "حداقل یک قلم را انتخاب کنید."}, status=status.HTTP_400_BAD_REQUEST)

        ceo_req, direct_req, ceo_items, direct_items = _dispatch_warehouse_purchase_items(
            cafe=cafe,
            request_user=request.user,
            entries=draft_items,
            target_officer=target_officer,
            is_self_buy=is_self_buy,
        )
        draft.items.filter(id__in=[d.id for d in draft_items]).delete()
        if not draft.items.exists():
            draft.delete()

    parts = []
    if direct_req:
        parts.append(f"{len(direct_items)} قلم مستقیم ارسال شد (شماره {direct_req.request_number})")
    if ceo_req:
        parts.append(f"{len(ceo_items)} قلم برای تایید مدیرعامل ارسال شد (شماره {ceo_req.request_number})")
    return Response({
        "detail": "؛ ".join(parts) or "درخواست خرید با موفقیت ارسال شد.",
        "is_self_buy": is_self_buy,
        "direct_count": len(direct_items),
        "ceo_count": len(ceo_items),
    })


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_stock(request, cafe_code: str):
    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به موجودی انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    warehouses = list(
        Warehouse.objects.filter(cafe=cafe, is_active=True)
        .order_by("name")
        .values("id", "name", "code", "location")
    )
    warehouse_id = request.query_params.get("warehouse_id")
    low_stock_only = request.query_params.get("low_stock") in ("1", "true", "yes")
    search_q = (request.query_params.get("q") or "").strip()

    qs = (
        StockItem.objects.filter(warehouse__cafe=cafe)
        .select_related("product", "product__category", "product__unit", "warehouse", "unit")
        .order_by("warehouse__name", "product__name")
    )
    if warehouse_id and str(warehouse_id).isdigit():
        qs = qs.filter(warehouse_id=int(warehouse_id))
    if low_stock_only:
        qs = qs.filter(quantity__lte=models.F("min_quantity"))
    if search_q:
        qs = qs.filter(Q(product__name__icontains=search_q) | Q(product__category__name__icontains=search_q))

    items = []
    for si in qs[:600]:
        u_name = (si.unit.name if si.unit else (si.product.unit.name if si.product and si.product.unit else "عدد"))
        items.append({
            "id": si.id,
            "product_id": si.product_id,
            "product_name": si.product.name if si.product else "نامشخص",
            "category_name": si.product.category.name if si.product and si.product.category else "عمومی",
            "warehouse_id": si.warehouse_id or 0,
            "warehouse_name": si.warehouse.name if si.warehouse else "انبار",
            "quantity": _fmt2(si.quantity),
            "min_quantity": _fmt2(si.min_quantity),
            "unit_name": u_name,
            "is_low_stock": bool(si.quantity <= si.min_quantity),
            "last_updated_jalali": _jalali_datetime(si.last_updated),
        })

    return Response({"warehouses": warehouses, "items": items})


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_stock_transactions(request, cafe_code: str, stock_id: int):
    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به گردش کالا ندارید."}, status=status.HTTP_403_FORBIDDEN)

    si = get_object_or_404(StockItem, id=stock_id, warehouse__cafe=cafe)
    txs = StockTransaction.objects.filter(stock_item=si).select_related("created_by", "unit").order_by("-created_at")[:60]
    return Response({
        "product_name": si.product.name if si.product else "",
        "warehouse_name": si.warehouse.name if si.warehouse else "",
        "current_quantity": _fmt2(si.quantity),
        "transactions": [
            {
                "id": t.id,
                "type": t.transaction_type,
                "type_label": t.get_transaction_type_display(),
                "quantity": _fmt2(t.quantity),
                "previous_quantity": _fmt2(t.previous_quantity),
                "new_quantity": _fmt2(t.new_quantity),
                "description": t.description or "",
                "created_by": (t.created_by.get_full_name() or t.created_by.username) if t.created_by else "سیستم",
                "created_at_jalali": _jalali_datetime(t.created_at),
            }
            for t in txs
        ],
    })


@api_view(["GET", "POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_warehouse_vouchers(request, cafe_code: str):
    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_warehouse_access(request.user):
        return Response({"detail": "دسترسی به حواله‌های انبار ندارید."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        qs = (
            WarehouseVoucher.objects.filter(cafe=cafe)
            .select_related("source_warehouse", "destination_warehouse", "created_by")
            .prefetch_related("items", "items__product", "items__unit")
            .order_by("-created_at")[:80]
        )
        vouchers = []
        for v in qs:
            prefetched_items = list(v.items.all())
            vouchers.append({
                "id": v.id,
                "voucher_number": v.voucher_number,
                "voucher_type": v.voucher_type,
                "voucher_type_label": v.get_voucher_type_display(),
                "status": v.status,
                "status_label": v.get_status_display(),
                "source_warehouse": v.source_warehouse.name if v.source_warehouse else "",
                "destination_warehouse": v.destination_warehouse.name if v.destination_warehouse else "",
                "description": v.description or "",
                "date_jalali": _jalali_datetime(v.date),
                "item_count": len(prefetched_items),
                "items_summary": "، ".join(
                    f"{(it.product.name if it.product else 'کالا')} ({_fmt2(it.base_quantity)})"
                    for it in prefetched_items[:3]
                ),
            })
        return Response({"vouchers": vouchers})

    # POST — Create & finalize simple receipt / issue / waste voucher
    v_type = (request.data.get("voucher_type") or "receipt").strip()
    if v_type not in ("receipt", "issue", "waste", "transfer"):
        return Response({"detail": "نوع حواله پشتیبانی نمی‌شود."}, status=status.HTTP_400_BAD_REQUEST)

    src_id = request.data.get("source_warehouse_id")
    dst_id = request.data.get("destination_warehouse_id")
    src_wh = Warehouse.objects.filter(id=int(src_id), cafe=cafe).first() if str(src_id or "").isdigit() else None
    dst_wh = Warehouse.objects.filter(id=int(dst_id), cafe=cafe).first() if str(dst_id or "").isdigit() else None
    description = (request.data.get("description") or "").strip()
    if len(description) > 1000:
        return Response({"detail": "توضیحات نباید بیش از ۱۰۰۰ کاراکتر باشد."}, status=status.HTTP_400_BAD_REQUEST)
    raw_items = request.data.get("items") or []

    if not isinstance(raw_items, list) or not raw_items:
        return Response({"detail": "حداقل یک قلم کالا برای حواله وارد کنید."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            voucher = WarehouseVoucher(
                cafe=cafe,
                voucher_type=v_type,
                status="submitted",
                source_warehouse=src_wh,
                destination_warehouse=dst_wh,
                voucher_number=next_voucher_number(cafe.id, v_type),
                description=description,
                created_by=request.user,
            )
            voucher.full_clean(exclude=["voucher_number"])
            save_voucher_with_number(voucher, cafe.id, v_type)
            for row in raw_items:
                if not isinstance(row, dict):
                    raise VoucherValidationError("ساختار ردیف حواله نامعتبر است.")
                pid = row.get("product_id")
                qty = _parse_decimal(str(row.get("quantity", "")))
                row_desc = (row.get("description") or "").strip()
                if len(row_desc) > 500:
                    raise VoucherValidationError("توضیح ردیف حواله نباید بیش از ۵۰۰ کاراکتر باشد.")
                product = Product.objects.select_related("unit").filter(id=int(pid), cafe=cafe).first() if str(pid or "").isdigit() else None
                if product is None or qty is None or not qty.is_finite() or qty <= 0 or qty > Decimal("99999999"):
                    raise VoucherValidationError("محصول و مقدار معتبر برای ردیف حواله الزامی است.")
                if product.unit is None:
                    raise VoucherValidationError(f"کالای «{product.name}» فاقد واحد شمارش است.")
                WarehouseVoucherItem.objects.create(
                    voucher=voucher,
                    product=product,
                    unit=product.unit,
                    base_quantity=qty,
                    description=row_desc,
                )
            finalize_voucher(voucher, finalized_by=request.user)
    except (InventoryServiceError, VoucherValidationError, ValidationError) as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {"id": voucher.id, "voucher_number": voucher.voucher_number, "detail": f"حواله {voucher.voucher_number} ثبت و نهایی شد."},
        status=status.HTTP_201_CREATED,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Purchasing Officer & Warehouse Self-Buy Endpoints (مسئول خرید)
# ─────────────────────────────────────────────────────────────────────────────


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_purchasing_dashboard(request, cafe_code: str):
    from .views import _item_section_name, _product_key

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_purchasing_access(request.user):
        return Response({"detail": "دسترسی به بخش خرید ندارید."}, status=status.HTTP_403_FORBIDDEN)

    scope = (request.query_params.get("scope") or "purchasing").strip()
    is_self_buy = scope == "warehouse_self"
    if not is_self_buy and not _has_pure_purchasing_access(request.user):
        return Response({"detail": "فقط به خریدهای خودتان (خریدهای انبار) دسترسی دارید."}, status=status.HTTP_403_FORBIDDEN)

    qs = (
        OfficerAssignment.objects.filter(target_role="purchasing", status="pending", item__request__cafe=cafe)
        .exclude(item__request__status__in=("pending_manager_approval", "rejected", "draft"))
        .select_related(
            "item", "item__request", "item__request__user", "item__request__user__department",
            "item__product", "item__source_item", "item__source_item__request",
            "item__source_item__request__user", "item__source_item__request__user__department",
        )
    )
    if is_self_buy:
        qs = qs.filter(assigned_to=request.user)
    else:
        qs = qs.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))

    rows_map = defaultdict(lambda: {
        "name": "",
        "unit": "",
        "total_qty": Decimal("0"),
        "sections": defaultdict(Decimal),
        "assignment_ids": [],
        "descriptions": [],
    })
    for a in qs:
        item = a.item
        key = (_product_key(item, include_description=False), (item.unit or "").strip())
        row = rows_map[key]
        row["name"] = item.product.name if item.product else item.name
        row["unit"] = item.unit or "عدد"
        row["total_qty"] += item.quantity or Decimal("0")
        origin_item = item.source_item if item.source_item_id else item
        dept = _item_section_name(origin_item.request.user)
        row["sections"][dept] += item.quantity or Decimal("0")
        row["assignment_ids"].append(a.id)
        if item.description:
            row["descriptions"].append(f"{dept}: {item.description}")

    rows = []
    for _key, data in rows_map.items():
        rows.append({
            "row_key": ",".join(str(x) for x in data["assignment_ids"]),
            "name": data["name"],
            "unit": data["unit"],
            "total_qty": _fmt2(data["total_qty"]),
            "sections": [
                {"name": d, "qty": _fmt2(q)} for d, q in sorted(data["sections"].items())
            ],
            "assignment_ids": data["assignment_ids"],
            "descriptions": data["descriptions"],
        })
    rows.sort(key=lambda r: (-len(r["sections"]), r["name"]))

    awaiting_ceo_count = PurchaseItem.objects.filter(
        request__cafe=cafe,
        request__status="pending_manager_approval",
        request__intended_purchasing_officer=request.user,
    ).count()

    return Response({
        "scope": scope,
        "total_items": sum(len(r["assignment_ids"]) for r in rows),
        "awaiting_ceo_count": awaiting_ceo_count,
        "rows": rows,
    })


@api_view(["POST"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_purchasing_action(request, cafe_code: str):
    from .views import _sync_request_status

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_purchasing_access(request.user):
        return Response({"detail": "دسترسی به بخش خرید ندارید."}, status=status.HTTP_403_FORBIDDEN)

    action = (request.data.get("action") or "").strip()
    if action not in ("purchased", "not_purchased", "return"):
        return Response({"detail": "عملیات نامعتبر است."}, status=status.HTTP_400_BAD_REQUEST)

    raw_ids = request.data.get("assignment_ids")
    if not isinstance(raw_ids, list):
        return Response({"detail": "فهرست اقلام نامعتبر است."}, status=status.HTTP_400_BAD_REQUEST)
    pks = sorted({int(x) for x in raw_ids if str(x).isdigit()})
    if not pks:
        return Response({"detail": "هیچ قلمی انتخاب نشده است."}, status=status.HTTP_400_BAD_REQUEST)

    scope = (request.data.get("scope") or "purchasing").strip()
    is_self_buy = scope == "warehouse_self"
    if not is_self_buy and not _has_pure_purchasing_access(request.user):
        return Response({"detail": "فقط به خریدهای خودتان (خریدهای انبار) دسترسی دارید."}, status=status.HTTP_403_FORBIDDEN)

    actor_name = request.user.get_full_name() or request.user.username
    extra_note = (request.data.get("notes") or "").strip()

    processed = []
    with transaction.atomic():
        assigns = list(
            OfficerAssignment.objects.select_for_update()
            .select_related("item", "item__request")
            .filter(id__in=pks, target_role="purchasing", item__request__cafe=cafe)
            .order_by("id")
        )
        # Pre-validate ALL assignments before any mutation
        pending_targets = []
        for a in assigns:
            if a.item.request.status in ("pending_manager_approval", "rejected", "draft"):
                return Response(
                    {"detail": "این قلم هنوز توسط مدیرعامل تایید نشده است."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if a.status != "pending":
                continue
            if is_self_buy and a.assigned_to_id != request.user.id:
                return Response({"detail": "این قلم به شما تخصیص داده نشده است."}, status=status.HTTP_403_FORBIDDEN)
            if not is_self_buy and a.assigned_to_id not in (None, request.user.id):
                return Response({"detail": "این قلم به شما تخصیص داده نشده است."}, status=status.HTTP_403_FORBIDDEN)
            pending_targets.append(a)

        if not pending_targets:
            return Response({"detail": "این اقلام قبلاً پردازش شده‌اند."}, status=status.HTTP_400_BAD_REQUEST)

        for a in pending_targets:
            if a.assigned_to_id is None:
                a.assigned_to = request.user
            suffix = f" ({extra_note})" if extra_note else ""
            if action == "purchased":
                a.status = "purchased"
                a.notes = (a.notes or "") + f" خریداری شد توسط {actor_name}{suffix}."
                a.item.supply_status = "purchased"
                a.item.purchased_quantity = a.item.quantity
                a.item.save(update_fields=["supply_status", "purchased_quantity"])
            elif action == "not_purchased":
                a.status = "not_purchased"
                a.notes = (a.notes or "") + f" خریداری نشد توسط {actor_name}{suffix}."
                a.item.supply_status = "not_supplied"
                a.item.purchased_quantity = Decimal("0")
                a.item.save(update_fields=["supply_status", "purchased_quantity"])
            else:
                a.status = "returned"
                target_lbl = "انبار" if is_self_buy else "درخواست‌ها"
                a.notes = (a.notes or "") + f" بازگشت به {target_lbl} توسط {actor_name}{suffix}."
                a.item.supply_status = "not_supplied"
                a.item.purchased_quantity = Decimal("0")
                a.item.save(update_fields=["supply_status", "purchased_quantity"])
            a.completed_at = timezone.now()
            a.save(update_fields=["status", "completed_at", "assigned_to", "notes"])
            copy_req = a.item.request
            if (
                action == "return"
                and copy_req.source == "warehouse_purchase"
                and not OfficerAssignment.objects.filter(
                    item__request=copy_req,
                    target_role="purchasing",
                    status__in=("pending", "purchased"),
                ).exists()
            ):
                copy_req.status = "rejected"
                copy_req.save(update_fields=["status"])
            else:
                _sync_request_status(copy_req)
            processed.append(a.item.name)

    verb = {"purchased": "خریداری شد", "not_purchased": "خریداری‌نشده ثبت شد", "return": "برگشت داده شد"}[action]
    return Response({"detail": f"{len(processed)} قلم ({processed[0]}) {verb}."})


@api_view(["GET"])
@authentication_classes(MOBILE_AUTH)
@permission_classes([IsAuthenticated])
def mobile_purchasing_history(request, cafe_code: str):
    from .views import _item_section_name

    cafe, err = _resolve_mobile_cafe(request, cafe_code)
    if err:
        return err
    if not _ensure_purchasing_access(request.user):
        return Response({"detail": "دسترسی به سوابق خرید ندارید."}, status=status.HTTP_403_FORBIDDEN)

    scope = (request.query_params.get("scope") or "purchasing").strip()
    is_self_buy = scope == "warehouse_self"
    if not is_self_buy and not _has_pure_purchasing_access(request.user):
        return Response({"detail": "فقط به خریدهای خودتان (خریدهای انبار) دسترسی دارید."}, status=status.HTTP_403_FORBIDDEN)

    status_filter = (request.query_params.get("status") or "all").strip()
    q_filter = (request.query_params.get("q") or "").strip()

    qs = (
        OfficerAssignment.objects.filter(
            target_role="purchasing",
            status__in=("purchased", "not_purchased", "returned"),
            item__request__cafe=cafe,
        )
        .exclude(item__request__status__in=("pending_manager_approval", "rejected", "draft"))
        .select_related(
            "item", "item__request", "item__request__user", "item__request__user__department",
            "item__product", "item__source_item", "item__source_item__request",
            "item__source_item__request__user", "item__source_item__request__user__department",
        )
    )
    if is_self_buy:
        qs = qs.filter(assigned_to=request.user)
    else:
        qs = qs.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))

    if status_filter == "not_purchased":
        qs = qs.filter(status__in=("not_purchased", "returned"))
    elif status_filter == "purchased":
        qs = qs.filter(status="purchased")
    if q_filter:
        qs = qs.filter(Q(item__name__icontains=q_filter) | Q(item__product__name__icontains=q_filter))

    assignments = qs.annotate(
        sort_dt=Coalesce("completed_at", "assigned_at")
    ).order_by("-sort_dt", "-id")[:200]
    items = []
    for a in assignments:
        dt = timezone.localtime(a.completed_at or a.assigned_at)
        item = a.item
        origin_item = item.source_item if item.source_item_id else item
        dept = _item_section_name(origin_item.request.user)
        norm_status = "purchased" if a.status == "purchased" else "not_purchased"
        items.append({
            "assignment_id": a.id,
            "name": item.product.name if item.product else item.name,
            "quantity": _fmt2(item.quantity),
            "unit": item.unit or "عدد",
            "department_name": dept,
            "request_number": str(item.request.request_number),
            "status": norm_status,
            "status_label": "خریداری شد" if norm_status == "purchased" else "خریداری نشده",
            "date_jalali": _jalali_date(dt),
            "time_str": dt.strftime("%H:%M"),
            "notes": a.notes or "",
        })

    return Response({"scope": scope, "items": items})
