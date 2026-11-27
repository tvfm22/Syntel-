"""
ویوهای مدیریت اموال و اثاثیه سکشن‌ها:
کاتالوگ با عکس، دسته‌بندی، موجودی سکشن‌ها، اقلام پلاک‌دار، انتقال دوطرفه،
گزارش خرابی/اسقاط، کارتابل تأیید، ورود از فاکتور و ایجاد درخواست خرید جایگزین.
"""
from __future__ import annotations

from decimal import Decimal
import json

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import (
    AssetAudit,
    AssetCatalog,
    AssetCategory,
    AssetMovement,
    AssetUnit,
    Department,
    DepartmentAssetStock,
    Invoice,
    User,
)
from ..services.assets import (
    AssetPermissionError,
    AssetServiceError,
    AssetStateError,
    AssetValidationError,
    InsufficientAssetStockError,
    allocate_initial_or_addition,
    approve_movement,
    compress_and_thumbnail_image,
    compress_evidence_image,
    confirm_transfer_receipt,
    create_replacement_purchase_request,
    create_serialized_unit,
    initiate_transfer,
    reject_movement,
    reject_transfer,
    report_damage_or_disposal,
    update_unit_repair_status,
)
from .helpers import (
    _format_jalali_date,
    _normalize_numeric_str,
    _parse_decimal,
    _parse_jalali_date,
    custom_login_required,
    get_cafe_from_request,
    get_cafe_url,
    get_scoped_object_or_404,
    user_can_access_department_assets,
    user_can_manage_assets,
)


def _require_active_cafe(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافه فعال مشخص نیست.')
    return cafe


def _is_requester_role(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (getattr(user, 'role', None) == 'requester' or getattr(user, 'secondary_role', None) == 'requester')
    )


def _require_manager(request, cafe):
    if not user_can_manage_assets(request.user):
        return HttpResponseForbidden('دسترسی فقط برای مدیران یا مسئول انبار مجاز است.')
    is_global = getattr(request.user, 'is_superuser', False) or (
        getattr(request.user, 'cafe_id', None) is None
        and getattr(request.user, 'role', None) in User.CAFE_SELECTION_ROLES
    )
    if not is_global and getattr(request.user, 'cafe_id', None) != cafe.id:
        return HttpResponseForbidden('شما به این کافه دسترسی ندارید.')
    return None


def _parse_int(value, default=0) -> int:
    text = _normalize_numeric_str(value) if value is not None else ''
    if not text:
        return default
    try:
        return int(float(text))
    except (ValueError, TypeError):
        return default


def _save_catalog_images(asset: AssetCatalog, uploaded_file) -> None:
    main_file, thumb_file = compress_and_thumbnail_image(uploaded_file)
    asset.image.save(main_file.name, main_file, save=False)
    asset.thumbnail.save(thumb_file.name, thumb_file, save=False)


# ---------------------------------------------------------------------------
# داشبورد اموال
# ---------------------------------------------------------------------------


@custom_login_required
def assets_dashboard(request):
    cafe = _require_active_cafe(request)
    is_manager = user_can_manage_assets(request.user)

    if not is_manager and not _is_requester_role(request.user):
        return HttpResponseForbidden('شما به ماژول اموال و اثاثیه دسترسی ندارید.')

    if is_manager:
        departments = list(
            Department.objects.filter(cafe=cafe, is_active=True).order_by('name')
        )
        stocks = list(
            DepartmentAssetStock.objects.filter(cafe=cafe)
            .select_related('department', 'asset')
        )
        units = list(
            AssetUnit.objects.filter(cafe=cafe).exclude(status__in=('lost', 'disposed'))
            .select_related('department', 'asset')
        )

        dept_stats = {
            d.id: {
                'department': d,
                'healthy': 0,
                'damaged': 0,
                'value': Decimal('0'),
                'shortages': 0,
                'unit_count': 0,
            }
            for d in departments
        }
        total_healthy = 0
        total_damaged = 0
        total_value = Decimal('0')
        total_shortages = 0

        for st in stocks:
            total_healthy += st.quantity_healthy
            total_damaged += st.quantity_damaged
            if st.shortage_quantity > 0:
                total_shortages += 1
            if st.asset.tracking_mode == 'quantity':
                val = Decimal(st.total_quantity) * Decimal(st.asset.estimated_unit_value or 0)
                total_value += val
            else:
                val = Decimal('0')
            if st.department_id in dept_stats:
                dept_stats[st.department_id]['healthy'] += st.quantity_healthy
                dept_stats[st.department_id]['damaged'] += st.quantity_damaged
                dept_stats[st.department_id]['value'] += val
                if st.shortage_quantity > 0:
                    dept_stats[st.department_id]['shortages'] += 1

        for u in units:
            u_val = Decimal(u.purchase_price or u.asset.estimated_unit_value or 0)
            total_value += u_val
            if u.department_id and u.department_id in dept_stats:
                dept_stats[u.department_id]['value'] += u_val
                dept_stats[u.department_id]['unit_count'] += 1

        pending_approvals_count = AssetMovement.objects.filter(cafe=cafe, status='pending').count()
        pending_handshakes_count = AssetMovement.objects.filter(
            cafe=cafe, movement_type='transfer', status='pending_receipt'
        ).count()
        repairs_count = AssetUnit.objects.filter(
            cafe=cafe, status__in=('needs_repair', 'in_repair')
        ).count()
        active_audits_count = AssetAudit.objects.filter(
            cafe=cafe, status__in=('in_progress', 'submitted', 'recount_requested')
        ).count()
        catalog_count = AssetCatalog.objects.filter(cafe=cafe, is_active=True).count()
        recent_movements = (
            AssetMovement.objects.filter(cafe=cafe)
            .select_related('asset', 'asset_unit', 'from_department', 'to_department', 'created_by')
            .order_by('-created_at')[:8]
        )

        return render(
            request,
            'assets/manage_dashboard.html',
            {
                'cafe': cafe,
                'is_manager': True,
                'dept_rows': list(dept_stats.values()),
                'catalog_count': catalog_count,
                'total_healthy': total_healthy,
                'total_damaged': total_damaged,
                'total_value': total_value,
                'total_shortages': total_shortages,
                'pending_approvals_count': pending_approvals_count,
                'pending_handshakes_count': pending_handshakes_count,
                'repairs_count': repairs_count,
                'active_audits_count': active_audits_count,
                'recent_movements': recent_movements,
            },
        )

    # نمای اختصاصی کاربر سکشن (requester)
    dept = getattr(request.user, 'department', None)
    if dept is None or dept.cafe_id != cafe.id:
        return render(
            request,
            'assets/manage_dashboard.html',
            {
                'cafe': cafe,
                'is_manager': False,
                'department': None,
            },
        )

    stocks = list(
        DepartmentAssetStock.objects.filter(cafe=cafe, department=dept)
        .select_related('asset', 'asset__category')
        .order_by('asset__name')
    )
    incoming_transfers = list(
        AssetMovement.objects.filter(
            cafe=cafe, movement_type='transfer', status='pending_receipt', to_department=dept
        )
        .select_related('asset', 'asset_unit', 'from_department', 'created_by')
        .order_by('-created_at')
    )
    pending_reports = list(
        AssetMovement.objects.filter(
            cafe=cafe, from_department=dept, status='pending'
        )
        .select_related('asset', 'asset_unit')
        .order_by('-created_at')[:10]
    )
    active_audits = list(
        AssetAudit.objects.filter(
            cafe=cafe, department=dept, status__in=('in_progress', 'recount_requested', 'submitted')
        ).order_by('-created_at')[:5]
    )

    return render(
        request,
        'assets/manage_dashboard.html',
        {
            'cafe': cafe,
            'is_manager': False,
            'department': dept,
            'stocks': stocks,
            'total_healthy': sum(s.quantity_healthy for s in stocks),
            'total_damaged': sum(s.quantity_damaged for s in stocks),
            'incoming_transfers': incoming_transfers,
            'pending_reports': pending_reports,
            'active_audits': active_audits,
        },
    )


# ---------------------------------------------------------------------------
# دسته‌بندی‌های اموال
# ---------------------------------------------------------------------------


@custom_login_required
def asset_category_list(request):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    if request.method == 'POST':
        action = (request.POST.get('action') or 'create').strip()
        if action == 'toggle':
            cat_id = _parse_int(request.POST.get('category_id'))
            cat = get_scoped_object_or_404(AssetCategory, request, pk=cat_id)
            cat.is_active = not cat.is_active
            cat.save(update_fields=['is_active'])
            messages.success(request, f'وضعیت دسته‌بندی «{cat.name}» تغییر یافت.')
            return redirect(get_cafe_url('asset_category_list', request))

        name = (request.POST.get('name') or '').strip()
        parent_id = _parse_int(request.POST.get('parent_id'))
        description = (request.POST.get('description') or '').strip()
        edit_id = _parse_int(request.POST.get('edit_id'))

        if not name:
            messages.error(request, 'نام دسته‌بندی الزامی است.')
        else:
            parent = None
            if parent_id:
                parent = get_scoped_object_or_404(AssetCategory, request, pk=parent_id)
            try:
                if edit_id:
                    cat = get_scoped_object_or_404(AssetCategory, request, pk=edit_id)
                    cat.name = name
                    cat.parent = parent
                    cat.description = description
                    cat.full_clean()
                    cat.save()
                    messages.success(request, f'دسته‌بندی «{cat.name}» بروزرسانی شد.')
                else:
                    cat = AssetCategory(cafe=cafe, name=name, parent=parent, description=description)
                    cat.full_clean()
                    cat.save()
                    messages.success(request, f'دسته‌بندی «{cat.name}» ایجاد شد.')
                return redirect(get_cafe_url('asset_category_list', request))
            except Exception as exc:
                messages.error(request, f'خطا در ذخیره دسته‌بندی: {exc}')

    categories = list(
        AssetCategory.objects.filter(cafe=cafe)
        .select_related('parent')
        .order_by('parent__name', 'name')
    )
    return render(
        request,
        'assets/manage_categories.html',
        {
            'cafe': cafe,
            'categories': categories,
            'parent_options': [c for c in categories if c.is_active],
        },
    )


# ---------------------------------------------------------------------------
# کاتالوگ اموال (با فشرده‌سازی عکس و thumbnail)
# ---------------------------------------------------------------------------


@custom_login_required
def asset_catalog_list(request):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    qs = AssetCatalog.objects.filter(cafe=cafe).select_related('category').order_by('name')

    q = (request.GET.get('q') or '').strip()
    selected_category = (request.GET.get('category') or '').strip()
    selected_mode = (request.GET.get('tracking_mode') or '').strip()
    selected_status = (request.GET.get('status') or '').strip()

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(brand_model__icontains=q))
    if selected_category.isdigit():
        cat_id = int(selected_category)
        qs = qs.filter(Q(category_id=cat_id) | Q(category__parent_id=cat_id))
    if selected_mode in ('quantity', 'serialized'):
        qs = qs.filter(tracking_mode=selected_mode)
    if selected_status == 'active':
        qs = qs.filter(is_active=True)
    elif selected_status == 'inactive':
        qs = qs.filter(is_active=False)

    assets = list(qs)
    stock_totals = {
        row['asset_id']: row
        for row in DepartmentAssetStock.objects.filter(cafe=cafe)
        .values('asset_id')
        .annotate(healthy=Sum('quantity_healthy'), damaged=Sum('quantity_damaged'))
    }
    for a in assets:
        st = stock_totals.get(a.id) or {}
        a.total_healthy = st.get('healthy') or 0
        a.total_damaged = st.get('damaged') or 0

    categories = AssetCategory.objects.filter(cafe=cafe, is_active=True).order_by('name')
    return render(
        request,
        'assets/manage_catalog_list.html',
        {
            'cafe': cafe,
            'assets': assets,
            'categories': categories,
            'q': q,
            'selected_category': selected_category,
            'selected_mode': selected_mode,
            'selected_status': selected_status,
        },
    )


def _handle_catalog_form(request, cafe, instance: AssetCatalog | None = None):
    categories = AssetCategory.objects.filter(cafe=cafe, is_active=True).order_by('name')
    is_edit = instance is not None

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        code = (request.POST.get('code') or '').strip()
        brand_model = (request.POST.get('brand_model') or '').strip()
        category_id = _parse_int(request.POST.get('category_id'))
        tracking_mode = (request.POST.get('tracking_mode') or 'quantity').strip()
        est_value = _parse_decimal(request.POST.get('estimated_unit_value'))
        if est_value is None:
            est_value = Decimal('0')
        life_months_raw = (request.POST.get('expected_life_months') or '').strip()
        expected_life_months = _parse_int(life_months_raw, default=0) if life_months_raw else None
        description = (request.POST.get('description') or '').strip()
        is_active = request.POST.get('is_active') in ('on', '1', 'true', 'True') or not is_edit
        if is_edit:
            is_active = request.POST.get('is_active') in ('on', '1', 'true', 'True')

        errors = []
        if not name:
            errors.append('نام کالای اموالی الزامی است.')
        if not code:
            errors.append('کد کالا الزامی است.')
        if tracking_mode not in ('quantity', 'serialized'):
            errors.append('روش ردیابی نامعتبر است.')
        if est_value < 0:
            errors.append('ارزش تخمینی واحد نمی‌تواند منفی باشد.')

        category = None
        if category_id:
            category = AssetCategory.objects.filter(pk=category_id, cafe=cafe).first()
            if category is None:
                errors.append('دسته‌بندی انتخاب‌شده معتبر نیست.')

        if not errors:
            dup_code = AssetCatalog.objects.filter(cafe=cafe, code=code)
            dup_name = AssetCatalog.objects.filter(cafe=cafe, name=name)
            if is_edit:
                dup_code = dup_code.exclude(pk=instance.pk)
                dup_name = dup_name.exclude(pk=instance.pk)
            if dup_code.exists():
                errors.append(f'کد «{code}» قبلاً در کاتالوگ این کافه ثبت شده است.')
            if dup_name.exists():
                errors.append(f'نام «{name}» قبلاً در کاتالوگ این کافه ثبت شده است.')

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            try:
                asset = instance or AssetCatalog(cafe=cafe)
                asset.name = name
                asset.code = code
                asset.brand_model = brand_model
                asset.category = category
                asset.tracking_mode = tracking_mode
                asset.estimated_unit_value = est_value
                asset.expected_life_months = expected_life_months
                asset.description = description
                asset.is_active = is_active

                uploaded_img = request.FILES.get('image')
                if uploaded_img:
                    _save_catalog_images(asset, uploaded_img)

                asset.full_clean()
                asset.save()
                messages.success(
                    request,
                    f'کالای اموالی «{asset.name}» با موفقیت {"بروزرسانی" if is_edit else "ثبت"} شد.',
                )
                return redirect(get_cafe_url('asset_catalog_list', request))
            except (AssetServiceError, IntegrityError) as exc:
                messages.error(request, f'خطا در ذخیره کالا: {exc}')

    return render(
        request,
        'assets/manage_catalog_form.html',
        {
            'cafe': cafe,
            'asset': instance,
            'is_edit': is_edit,
            'categories': categories,
            'tracking_choices': AssetCatalog.TRACKING_MODE_CHOICES,
        },
    )


@custom_login_required
def asset_catalog_create(request):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied
    return _handle_catalog_form(request, cafe, instance=None)


@custom_login_required
def asset_catalog_edit(request, pk: int):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied
    asset = get_scoped_object_or_404(AssetCatalog, request, pk=pk)
    return _handle_catalog_form(request, cafe, instance=asset)


# ---------------------------------------------------------------------------
# موجودی سکشن‌ها و تخصیص اولیه
# ---------------------------------------------------------------------------


@custom_login_required
def asset_department_stock(request, department_id: int):
    cafe = _require_active_cafe(request)
    department = get_scoped_object_or_404(Department, request, pk=department_id)
    if not user_can_access_department_assets(request.user, department):
        return HttpResponseForbidden('شما به اموال این بخش دسترسی ندارید.')

    is_manager = user_can_manage_assets(request.user)

    stocks_qs = (
        DepartmentAssetStock.objects.filter(cafe=cafe, department=department)
        .select_related('asset', 'asset__category')
        .order_by('asset__name')
    )
    q = (request.GET.get('q') or '').strip()
    selected_category = (request.GET.get('category') or '').strip()
    selected_mode = (request.GET.get('tracking_mode') or '').strip()
    shortage_only = request.GET.get('shortage_only') == '1'

    if q:
        stocks_qs = stocks_qs.filter(Q(asset__name__icontains=q) | Q(asset__code__icontains=q))
    if selected_category.isdigit():
        cat_id = int(selected_category)
        stocks_qs = stocks_qs.filter(Q(asset__category_id=cat_id) | Q(asset__category__parent_id=cat_id))
    if selected_mode in ('quantity', 'serialized'):
        stocks_qs = stocks_qs.filter(asset__tracking_mode=selected_mode)
    if shortage_only:
        stocks_qs = stocks_qs.filter(quantity_healthy__lt=F('min_expected_quantity'))

    stocks = list(stocks_qs)
    units = list(
        AssetUnit.objects.filter(cafe=cafe, department=department)
        .select_related('asset', 'custodian')
        .order_by('asset__name', 'asset_tag')
    )
    categories = AssetCategory.objects.filter(cafe=cafe, is_active=True).order_by('name')
    quantity_assets = (
        AssetCatalog.objects.filter(cafe=cafe, is_active=True, tracking_mode='quantity').order_by('name')
        if is_manager
        else []
    )
    all_departments = Department.objects.filter(cafe=cafe, is_active=True).order_by('name')

    total_healthy = sum(s.quantity_healthy for s in stocks)
    total_damaged = sum(s.quantity_damaged for s in stocks)
    shortage_rows = [s for s in stocks if s.shortage_quantity > 0]

    return render(
        request,
        'assets/manage_department_stock.html',
        {
            'cafe': cafe,
            'department': department,
            'is_manager': is_manager,
            'stocks': stocks,
            'units': units,
            'categories': categories,
            'quantity_assets': quantity_assets,
            'all_departments': all_departments,
            'total_healthy': total_healthy,
            'total_damaged': total_damaged,
            'shortage_count': len(shortage_rows),
            'q': q,
            'selected_category': selected_category,
            'selected_mode': selected_mode,
            'shortage_only': shortage_only,
        },
    )


@custom_login_required
@require_POST
def asset_stock_allocate(request, department_id: int):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    department = get_scoped_object_or_404(Department, request, pk=department_id)
    asset_id = _parse_int(request.POST.get('asset_id'))
    asset = get_scoped_object_or_404(AssetCatalog, request, pk=asset_id)
    qty = _parse_int(request.POST.get('quantity'))
    movement_type = (request.POST.get('movement_type') or 'initial').strip()
    if movement_type not in ('initial', 'addition'):
        movement_type = 'initial'
    min_raw = (request.POST.get('min_expected_quantity') or '').strip()
    min_expected = _parse_int(min_raw) if min_raw != '' else None
    val_raw = (request.POST.get('unit_value') or '').strip()
    unit_value = _parse_decimal(val_raw) if val_raw else None
    location_note = (request.POST.get('location_note') or '').strip()
    reason = (request.POST.get('reason') or '').strip()

    try:
        allocate_initial_or_addition(
            cafe=cafe,
            department=department,
            asset=asset,
            quantity=qty,
            user=request.user,
            movement_type=movement_type,
            unit_value=unit_value,
            min_expected_quantity=min_expected,
            location_note=location_note,
            reason=reason,
        )
        messages.success(request, f'{qty} عدد «{asset.name}» به بخش «{department.name}» تخصیص داده شد.')
    except AssetServiceError as exc:
        messages.error(request, str(exc))

    return redirect(get_cafe_url('asset_department_stock', request, department_id=department.id))


# ---------------------------------------------------------------------------
# اموال پلاک‌دار (Serialized Units)
# ---------------------------------------------------------------------------


@custom_login_required
def asset_unit_list(request):
    cafe = _require_active_cafe(request)
    is_manager = user_can_manage_assets(request.user)

    if not is_manager and not _is_requester_role(request.user):
        return HttpResponseForbidden('شما به فهرست اموال پلاک‌دار دسترسی ندارید.')

    qs = AssetUnit.objects.filter(cafe=cafe).select_related('asset', 'department', 'custodian')
    if not is_manager:
        dept = getattr(request.user, 'department', None)
        if dept is None or dept.cafe_id != cafe.id:
            qs = qs.none()
        else:
            qs = qs.filter(department=dept)

    q = (request.GET.get('q') or '').strip()
    selected_dept = (request.GET.get('department') or '').strip()
    selected_status = (request.GET.get('status') or '').strip()

    if q:
        qs = qs.filter(
            Q(asset_tag__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(asset__name__icontains=q)
            | Q(asset__code__icontains=q)
        )
    if is_manager and selected_dept.isdigit():
        qs = qs.filter(department_id=int(selected_dept))
    if selected_status:
        qs = qs.filter(status=selected_status)

    units = list(qs.order_by('asset__name', 'asset_tag'))
    departments = Department.objects.filter(cafe=cafe, is_active=True).order_by('name')

    return render(
        request,
        'assets/manage_unit_list.html',
        {
            'cafe': cafe,
            'is_manager': is_manager,
            'units': units,
            'departments': departments,
            'status_choices': AssetUnit.STATUS_CHOICES,
            'q': q,
            'selected_dept': selected_dept,
            'selected_status': selected_status,
            'today': timezone.localdate(),
        },
    )


@custom_login_required
def asset_unit_create(request):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    serialized_assets = AssetCatalog.objects.filter(
        cafe=cafe, is_active=True, tracking_mode='serialized'
    ).order_by('name')
    departments = Department.objects.filter(cafe=cafe, is_active=True).order_by('name')
    invoices = Invoice.objects.filter(cafe=cafe).order_by('-invoice_date', '-id')[:100]
    custodians = User.objects.filter(is_active=True).filter(
        Q(cafe=cafe) | Q(cafe__isnull=True)
    ).order_by('username')

    if request.method == 'POST':
        asset_id = _parse_int(request.POST.get('asset_id'))
        dept_id = _parse_int(request.POST.get('department_id'))
        invoice_id = _parse_int(request.POST.get('invoice_id'))
        custodian_id = _parse_int(request.POST.get('custodian_id'))
        asset_tag = (request.POST.get('asset_tag') or '').strip()
        serial_number = (request.POST.get('serial_number') or '').strip()
        purchase_date = _parse_jalali_date(request.POST.get('purchase_date'))
        warranty_until = _parse_jalali_date(request.POST.get('warranty_until'))
        purchase_price = _parse_decimal(request.POST.get('purchase_price')) or Decimal('0')
        notes = (request.POST.get('notes') or '').strip()

        asset = get_scoped_object_or_404(AssetCatalog, request, pk=asset_id)
        department = get_scoped_object_or_404(Department, request, pk=dept_id) if dept_id else None
        invoice = get_scoped_object_or_404(Invoice, request, pk=invoice_id) if invoice_id else None
        custodian = custodians.filter(pk=custodian_id).first() if custodian_id else None

        photo_file = None
        if request.FILES.get('photo'):
            try:
                photo_file = compress_evidence_image(request.FILES['photo'])
            except AssetServiceError as exc:
                messages.error(request, str(exc))
                photo_file = False

        if photo_file is not False:
            try:
                unit = create_serialized_unit(
                    cafe=cafe,
                    department=department,
                    asset=asset,
                    asset_tag=asset_tag,
                    user=request.user,
                    serial_number=serial_number,
                    purchase_date=purchase_date,
                    warranty_until=warranty_until,
                    purchase_price=purchase_price,
                    invoice=invoice,
                    custodian=custodian,
                    photo=photo_file,
                    notes=notes,
                )
                messages.success(request, f'پلاک «{unit.asset_tag}» با موفقیت ثبت شد.')
                return redirect(get_cafe_url('asset_unit_detail', request, pk=unit.id))
            except AssetServiceError as exc:
                messages.error(request, str(exc))

    return render(
        request,
        'assets/manage_unit_form.html',
        {
            'cafe': cafe,
            'serialized_assets': serialized_assets,
            'departments': departments,
            'invoices': invoices,
            'custodians': custodians,
        },
    )


@custom_login_required
def asset_unit_detail(request, pk: int):
    cafe = _require_active_cafe(request)
    unit = get_scoped_object_or_404(AssetUnit, request, pk=pk)
    is_manager = user_can_manage_assets(request.user)

    if not is_manager:
        if not unit.department or not user_can_access_department_assets(request.user, unit.department):
            return HttpResponseForbidden('شما به اطلاعات این پلاک دسترسی ندارید.')

    movements = list(
        AssetMovement.objects.filter(cafe=cafe, asset_unit=unit)
        .select_related('from_department', 'to_department', 'created_by', 'approved_by')
        .order_by('-created_at')
    )
    return render(
        request,
        'assets/manage_unit_detail.html',
        {
            'cafe': cafe,
            'unit': unit,
            'is_manager': is_manager,
            'movements': movements,
            'status_choices': AssetUnit.STATUS_CHOICES,
            'purchase_date_jalali': _format_jalali_date(unit.purchase_date) if unit.purchase_date else '—',
            'warranty_until_jalali': _format_jalali_date(unit.warranty_until) if unit.warranty_until else '—',
        },
    )


@custom_login_required
@require_POST
def asset_unit_repair_action(request, pk: int):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    unit = get_scoped_object_or_404(AssetUnit, request, pk=pk)
    new_status = (request.POST.get('new_status') or '').strip()
    reason = (request.POST.get('reason') or '').strip()

    evidence_photo = None
    if request.FILES.get('evidence_photo'):
        try:
            evidence_photo = compress_evidence_image(request.FILES['evidence_photo'])
        except AssetServiceError as exc:
            messages.error(request, str(exc))
            return redirect(get_cafe_url('asset_unit_detail', request, pk=unit.id))

    try:
        update_unit_repair_status(
            unit=unit,
            new_status=new_status,
            user=request.user,
            reason=reason,
            evidence_photo=evidence_photo,
        )
        messages.success(request, f'وضعیت پلاک «{unit.asset_tag}» به «{unit.get_status_display()}» تغییر یافت.')
    except AssetServiceError as exc:
        messages.error(request, str(exc))

    return redirect(get_cafe_url('asset_unit_detail', request, pk=unit.id))


# ---------------------------------------------------------------------------
# انتقال بین سکشن‌ها (با تأیید دوطرفه Handshake)
# ---------------------------------------------------------------------------


@custom_login_required
def asset_transfer_list(request):
    cafe = _require_active_cafe(request)
    is_manager = user_can_manage_assets(request.user)

    if not is_manager and not _is_requester_role(request.user):
        return HttpResponseForbidden('شما به انتقال اموال دسترسی ندارید.')

    qs = AssetMovement.objects.filter(cafe=cafe, movement_type='transfer').select_related(
        'asset', 'asset_unit', 'from_department', 'to_department', 'created_by', 'received_by'
    )
    user_dept = getattr(request.user, 'department', None)

    if not is_manager:
        if user_dept is None or user_dept.cafe_id != cafe.id:
            qs = qs.none()
        else:
            qs = qs.filter(Q(from_department=user_dept) | Q(to_department=user_dept))

    selected_status = (request.GET.get('status') or '').strip()
    if selected_status:
        qs = qs.filter(status=selected_status)

    transfers = list(qs.order_by('-created_at'))
    for tr in transfers:
        tr.can_confirm = (
            tr.status == 'pending_receipt'
            and tr.to_department is not None
            and user_can_access_department_assets(request.user, tr.to_department)
        )
        tr.can_reject = (
            tr.status == 'pending_receipt'
            and (
                is_manager
                or (tr.to_department and user_can_access_department_assets(request.user, tr.to_department))
                or (tr.from_department and user_can_access_department_assets(request.user, tr.from_department))
            )
        )

    return render(
        request,
        'assets/manage_transfers_list.html',
        {
            'cafe': cafe,
            'is_manager': is_manager,
            'user_dept': user_dept,
            'transfers': transfers,
            'selected_status': selected_status,
            'status_choices': AssetMovement.STATUS_CHOICES,
        },
    )


@custom_login_required
def asset_transfer_create(request):
    cafe = _require_active_cafe(request)
    is_manager = user_can_manage_assets(request.user)
    user_dept = getattr(request.user, 'department', None)

    if not is_manager:
        if not _is_requester_role(request.user) or user_dept is None or user_dept.cafe_id != cafe.id:
            return HttpResponseForbidden('شما مجاز به ثبت انتقال اموال نیستید.')

    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))
    assets = list(AssetCatalog.objects.filter(cafe=cafe, is_active=True).order_by('name'))
    units = list(
        AssetUnit.objects.filter(cafe=cafe, department__isnull=False)
        .exclude(status__in=('lost', 'disposed'))
        .select_related('asset', 'department')
        .order_by('asset__name', 'asset_tag')
    )
    if not is_manager:
        units = [u for u in units if u.department_id == user_dept.id]

    if request.method == 'POST':
        from_dept_id = _parse_int(request.POST.get('from_department_id')) if is_manager else user_dept.id
        to_dept_id = _parse_int(request.POST.get('to_department_id'))
        asset_id = _parse_int(request.POST.get('asset_id'))
        unit_id = _parse_int(request.POST.get('asset_unit_id'))
        qty = _parse_int(request.POST.get('quantity'), default=1)
        reason = (request.POST.get('reason') or '').strip()
        require_handshake = True if not is_manager else (request.POST.get('require_handshake') != '0')

        from_dept = get_scoped_object_or_404(Department, request, pk=from_dept_id)
        to_dept = get_scoped_object_or_404(Department, request, pk=to_dept_id)
        asset = get_scoped_object_or_404(AssetCatalog, request, pk=asset_id)
        unit = get_scoped_object_or_404(AssetUnit, request, pk=unit_id) if unit_id else None

        try:
            mv = initiate_transfer(
                cafe=cafe,
                asset=asset,
                from_department=from_dept,
                to_department=to_dept,
                quantity=qty,
                user=request.user,
                asset_unit=unit,
                reason=reason,
                require_handshake=require_handshake,
            )
            if mv.status == 'pending_receipt':
                messages.success(
                    request,
                    f'درخواست انتقال «{asset.name}» به بخش «{to_dept.name}» ثبت شد و منتظر تأیید تحویل مقصد است.',
                )
            else:
                messages.success(request, f'انتقال «{asset.name}» به بخش «{to_dept.name}» انجام شد.')
            return redirect(get_cafe_url('asset_transfer_list', request))
        except AssetServiceError as exc:
            messages.error(request, str(exc))

    pre_from = request.GET.get('from_department') or (str(user_dept.id) if user_dept else '')
    pre_asset = request.GET.get('asset') or ''
    return render(
        request,
        'assets/manage_transfer_form.html',
        {
            'cafe': cafe,
            'is_manager': is_manager,
            'user_dept': user_dept,
            'departments': departments,
            'assets': assets,
            'units': units,
            'pre_from': pre_from,
            'pre_asset': pre_asset,
        },
    )


@custom_login_required
@require_POST
def asset_transfer_confirm(request, pk: int):
    cafe = _require_active_cafe(request)
    mv = get_scoped_object_or_404(AssetMovement, request, pk=pk, movement_type='transfer')
    if mv.to_department is None or not user_can_access_department_assets(request.user, mv.to_department):
        return HttpResponseForbidden('فقط بخش مقصد یا مدیران مجاز به تأیید تحویل هستند.')

    try:
        confirm_transfer_receipt(mv, request.user)
        messages.success(request, f'تحویل «{mv.asset.name}» در بخش «{mv.to_department.name}» تأیید و موجودی اعمال شد.')
    except AssetServiceError as exc:
        messages.error(request, str(exc))
    return redirect(get_cafe_url('asset_transfer_list', request))


@custom_login_required
@require_POST
def asset_transfer_reject(request, pk: int):
    cafe = _require_active_cafe(request)
    mv = get_scoped_object_or_404(AssetMovement, request, pk=pk, movement_type='transfer')
    is_allowed = (
        user_can_manage_assets(request.user)
        or (mv.to_department and user_can_access_department_assets(request.user, mv.to_department))
        or (mv.from_department and user_can_access_department_assets(request.user, mv.from_department))
    )
    if not is_allowed:
        return HttpResponseForbidden('شما مجاز به رد این انتقال نیستید.')

    reason = (request.POST.get('reason') or '').strip()
    try:
        reject_transfer(mv, request.user, reason=reason)
        messages.info(request, f'انتقال «{mv.asset.name}» رد شد.')
    except AssetServiceError as exc:
        messages.error(request, str(exc))
    return redirect(get_cafe_url('asset_transfer_list', request))


# ---------------------------------------------------------------------------
# گزارش شکستگی / اسقاط و کارتابل تأیید مدیر
# ---------------------------------------------------------------------------


@custom_login_required
def asset_damage_report(request, department_id: int):
    cafe = _require_active_cafe(request)
    department = get_scoped_object_or_404(Department, request, pk=department_id)
    if not user_can_access_department_assets(request.user, department):
        return HttpResponseForbidden('شما به اموال این بخش دسترسی ندارید.')

    is_manager = user_can_manage_assets(request.user)
    stocks = list(
        DepartmentAssetStock.objects.filter(cafe=cafe, department=department)
        .filter(Q(quantity_healthy__gt=0) | Q(quantity_damaged__gt=0))
        .select_related('asset')
        .order_by('asset__name')
    )
    units = list(
        AssetUnit.objects.filter(cafe=cafe, department=department)
        .exclude(status__in=('lost', 'disposed'))
        .select_related('asset')
        .order_by('asset__name', 'asset_tag')
    )

    if request.method == 'POST':
        asset_id = _parse_int(request.POST.get('asset_id'))
        unit_id = _parse_int(request.POST.get('asset_unit_id'))
        qty = _parse_int(request.POST.get('quantity'), default=1)
        reason = (request.POST.get('reason') or '').strip()
        auto_approve = is_manager and (request.POST.get('auto_approve') == '1')

        asset = get_scoped_object_or_404(AssetCatalog, request, pk=asset_id)
        unit = get_scoped_object_or_404(AssetUnit, request, pk=unit_id) if unit_id else None

        evidence_photo = None
        if request.FILES.get('evidence_photo'):
            try:
                evidence_photo = compress_evidence_image(request.FILES['evidence_photo'])
            except AssetServiceError as exc:
                messages.error(request, str(exc))
                evidence_photo = False

        if evidence_photo is not False:
            try:
                mv = report_damage_or_disposal(
                    cafe=cafe,
                    department=department,
                    asset=asset,
                    quantity=qty,
                    user=request.user,
                    reason=reason,
                    evidence_photo=evidence_photo,
                    asset_unit=unit,
                    auto_approve=auto_approve,
                )
                if mv.status == 'approved':
                    messages.success(request, f'اسقاط/خرابی «{asset.name}» ثبت و بر موجودی اعمال شد.')
                else:
                    messages.success(
                        request,
                        f'گزارش خرابی/اسقاط «{asset.name}» ثبت شد و پس از تأیید مدیر اعمال خواهد شد.',
                    )
                return redirect(get_cafe_url('asset_department_stock', request, department_id=department.id))
            except AssetServiceError as exc:
                messages.error(request, str(exc))

    pre_asset = request.GET.get('asset') or ''
    return render(
        request,
        'assets/manage_disposal_form.html',
        {
            'cafe': cafe,
            'department': department,
            'is_manager': is_manager,
            'stocks': stocks,
            'units': units,
            'pre_asset': pre_asset,
        },
    )


@custom_login_required
def asset_pending_movements(request):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    pending_list = list(
        AssetMovement.objects.filter(cafe=cafe, status='pending')
        .select_related('asset', 'asset_unit', 'from_department', 'to_department', 'created_by')
        .order_by('-created_at')
    )
    handshake_list = list(
        AssetMovement.objects.filter(cafe=cafe, status='pending_receipt')
        .select_related('asset', 'asset_unit', 'from_department', 'to_department', 'created_by')
        .order_by('-created_at')
    )
    return render(
        request,
        'assets/manage_pending_movements.html',
        {
            'cafe': cafe,
            'pending_list': pending_list,
            'handshake_list': handshake_list,
        },
    )


@custom_login_required
@require_POST
def asset_movement_approve(request, pk: int):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    mv = get_scoped_object_or_404(AssetMovement, request, pk=pk)
    try:
        approve_movement(mv, request.user)
        messages.success(request, f'سند گردش «{mv.asset.name}» تأیید و اعمال شد.')
    except AssetServiceError as exc:
        messages.error(request, str(exc))
    return redirect(get_cafe_url('asset_pending_movements', request))


@custom_login_required
@require_POST
def asset_movement_reject(request, pk: int):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    mv = get_scoped_object_or_404(AssetMovement, request, pk=pk)
    reason = (request.POST.get('reason') or '').strip()
    try:
        reject_movement(mv, request.user, reason=reason)
        messages.info(request, f'سند گردش «{mv.asset.name}» رد شد.')
    except AssetServiceError as exc:
        messages.error(request, str(exc))
    return redirect(get_cafe_url('asset_pending_movements', request))


# ---------------------------------------------------------------------------
# ورود اموال از فاکتور خرید
# ---------------------------------------------------------------------------


@custom_login_required
def asset_import_from_invoice(request):
    cafe = _require_active_cafe(request)
    denied = _require_manager(request, cafe)
    if denied:
        return denied

    invoices = list(
        Invoice.objects.filter(cafe=cafe)
        .select_related('supplier')
        .order_by('-invoice_date', '-id')[:100]
    )
    selected_invoice = None
    invoice_id_raw = request.POST.get('invoice_id') or request.GET.get('invoice_id')
    if invoice_id_raw and str(invoice_id_raw).isdigit():
        selected_invoice = get_scoped_object_or_404(Invoice, request, pk=int(invoice_id_raw))

    assets = list(AssetCatalog.objects.filter(cafe=cafe, is_active=True).order_by('name'))
    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))

    if request.method == 'POST' and selected_invoice is not None:
        asset_id = _parse_int(request.POST.get('asset_id'))
        dept_id = _parse_int(request.POST.get('department_id'))
        asset = get_scoped_object_or_404(AssetCatalog, request, pk=asset_id)
        department = get_scoped_object_or_404(Department, request, pk=dept_id)
        unit_val_raw = (request.POST.get('unit_value') or '').strip()
        unit_val = _parse_decimal(unit_val_raw) if unit_val_raw else None
        reason = (request.POST.get('reason') or '').strip() or f'ورود از فاکتور {selected_invoice.invoice_number}'

        try:
            if asset.tracking_mode == 'serialized':
                asset_tag = (request.POST.get('asset_tag') or '').strip()
                serial_number = (request.POST.get('serial_number') or '').strip()
                warranty_until = _parse_jalali_date(request.POST.get('warranty_until'))
                unit = create_serialized_unit(
                    cafe=cafe,
                    department=department,
                    asset=asset,
                    asset_tag=asset_tag,
                    user=request.user,
                    serial_number=serial_number,
                    purchase_date=selected_invoice.invoice_date,
                    warranty_until=warranty_until,
                    purchase_price=unit_val if unit_val is not None else (asset.estimated_unit_value or Decimal('0')),
                    invoice=selected_invoice,
                    notes=reason,
                )
                messages.success(
                    request,
                    f'پلاک «{unit.asset_tag}» از فاکتور {selected_invoice.invoice_number} به بخش «{department.name}» اضافه شد.',
                )
            else:
                qty = _parse_int(request.POST.get('quantity'), default=0)
                allocate_initial_or_addition(
                    cafe=cafe,
                    department=department,
                    asset=asset,
                    quantity=qty,
                    user=request.user,
                    movement_type='addition',
                    unit_value=unit_val,
                    invoice=selected_invoice,
                    reason=reason,
                )
                messages.success(
                    request,
                    f'{qty} عدد «{asset.name}» از فاکتور {selected_invoice.invoice_number} به بخش «{department.name}» اضافه شد.',
                )
            return redirect(f"{get_cafe_url('asset_import_from_invoice', request)}?invoice_id={selected_invoice.id}")
        except AssetServiceError as exc:
            messages.error(request, str(exc))

    invoice_items = (
        list(selected_invoice.invoice_items.select_related('product').all())
        if selected_invoice is not None
        else []
    )
    imported_movements = (
        list(
            AssetMovement.objects.filter(cafe=cafe, invoice=selected_invoice)
            .select_related('asset', 'asset_unit', 'to_department')
            .order_by('-created_at')
        )
        if selected_invoice is not None
        else []
    )
    return render(
        request,
        'assets/manage_invoice_import.html',
        {
            'cafe': cafe,
            'invoices': invoices,
            'selected_invoice': selected_invoice,
            'invoice_items': invoice_items,
            'imported_movements': imported_movements,
            'assets': assets,
            'departments': departments,
        },
    )


# ---------------------------------------------------------------------------
# ایجاد درخواست خرید جایگزین از روی کسری سکشن
# ---------------------------------------------------------------------------


@custom_login_required
@require_POST
def asset_create_replacement_request(request, department_id: int | None = None):
    cafe = _require_active_cafe(request)
    dept_id = department_id or _parse_int(request.POST.get('department_id'))
    if not dept_id:
        messages.error(request, 'بخش برای ایجاد درخواست خرید جایگزین مشخص نشده است.')
        return redirect(get_cafe_url('asset_report_shortage', request))

    department = get_scoped_object_or_404(Department, request, pk=dept_id)
    if not user_can_access_department_assets(request.user, department):
        return HttpResponseForbidden('شما به اموال این بخش دسترسی ندارید.')

    shortage_items: list[dict] = []
    json_payload = (request.POST.get('shortage_items') or '').strip()
    if json_payload:
        try:
            parsed = json.loads(json_payload)
            if isinstance(parsed, list):
                shortage_items = parsed
        except ValueError:
            shortage_items = []
    elif request.POST.get('mode') == 'all_shortages':
        stocks = DepartmentAssetStock.objects.filter(
            cafe=cafe, department=department, quantity_healthy__lt=F('min_expected_quantity')
        ).select_related('asset')
        for st in stocks:
            if st.shortage_quantity > 0:
                shortage_items.append({'stock': st, 'quantity': st.shortage_quantity})
    else:
        stock_id = _parse_int(request.POST.get('stock_id'))
        asset_id = _parse_int(request.POST.get('asset_id'))
        qty = _parse_int(request.POST.get('quantity'))
        name = (request.POST.get('name') or '').strip()
        stock_obj = (
            DepartmentAssetStock.objects.filter(pk=stock_id, cafe=cafe, department=department)
            .select_related('asset')
            .first()
            if stock_id
            else None
        )
        asset_obj = (
            AssetCatalog.objects.filter(pk=asset_id, cafe=cafe).first()
            if asset_id
            else (stock_obj.asset if stock_obj else None)
        )
        if qty <= 0 and stock_obj is not None:
            qty = stock_obj.shortage_quantity
        if asset_obj is not None or name:
            shortage_items.append(
                {
                    'stock': stock_obj,
                    'asset': asset_obj,
                    'quantity': qty,
                    'name': name or (asset_obj.name if asset_obj else ''),
                    'note': (request.POST.get('note') or '').strip(),
                }
            )

    try:
        pr = create_replacement_purchase_request(
            cafe=cafe,
            department=department,
            user=request.user,
            shortage_items=shortage_items,
        )
        messages.success(
            request,
            f'درخواست خرید جایگزین به شماره {pr.request_number} برای بخش «{department.name}» ایجاد شد.',
        )
        return redirect(get_cafe_url('purchase_request_detail', request, request_id=pr.id))
    except AssetServiceError as exc:
        messages.error(request, str(exc))
        next_url = (request.POST.get('next') or '').strip()
        if next_url.startswith('/'):
            return redirect(next_url)
        return redirect(get_cafe_url('asset_department_stock', request, department_id=department.id))
