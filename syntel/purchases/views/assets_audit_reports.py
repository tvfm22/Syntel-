"""
ویوهای انبارگردانی اموال و ۶ گزارش تحلیلی اموال به همراه خروجی Excel و PDF.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import json
import os

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
import jdatetime
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from ..models import (
    AssetAudit,
    AssetAuditItem,
    AssetCatalog,
    AssetCategory,
    AssetMovement,
    AssetUnit,
    Cafe,
    Department,
    DepartmentAssetStock,
    User,
)
from ..services.assets import (
    AssetPermissionError,
    AssetServiceError,
    AssetStateError,
    AssetValidationError,
    add_unexpected_audit_item,
    approve_audit_and_apply_adjustments,
    compress_evidence_image,
    create_audit_session,
    request_audit_recount,
    save_audit_item_count,
    submit_audit,
)
from .helpers import (
    _format_jalali_date,
    _normalize_numeric_str,
    _parse_jalali_date,
    custom_login_required,
    get_cafe_from_request,
    get_cafe_url,
    get_scoped_object_or_404,
    role_required,
    user_can_access_department_assets,
    user_can_manage_assets,
)


ASSET_REPORT_ROLES = [
    'admin',
    'manager',
    'ceo',
    'warehouse',
    'finance_manager',
    'accounting',
    'purchasing',
]

JALALI_MONTH_NAMES = {
    1: 'فروردین',
    2: 'اردیبهشت',
    3: 'خرداد',
    4: 'تیر',
    5: 'مرداد',
    6: 'شهریور',
    7: 'مهر',
    8: 'آبان',
    9: 'آذر',
    10: 'دی',
    11: 'بهمن',
    12: 'اسفند',
}

AUDIT_CONDITION_LABELS = {
    **dict(AssetAuditItem.CONDITION_CHOICES),
    **dict(AssetUnit.STATUS_CHOICES),
}

MOVEMENT_TYPE_LABELS = dict(AssetMovement.MOVEMENT_TYPE_CHOICES)
MOVEMENT_STATUS_LABELS = dict(AssetMovement.STATUS_CHOICES)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_active_cafe(request):
    """دریافت کافه فعال از request با سیاست شکستِ بسته."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافه فعال مشخص نیست.')
    return cafe


def _user_is_global_admin(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (
            getattr(user, 'is_superuser', False)
            or (
                getattr(user, 'cafe_id', None) is None
                and getattr(user, 'role', None) in User.CAFE_SELECTION_ROLES
            )
        )
    )


def _user_can_view_audit(user, audit: AssetAudit) -> bool:
    return user_can_access_department_assets(user, audit.department)


def _parse_int_input(raw_value, default: int = 0) -> int:
    if raw_value is None or raw_value == '':
        return default
    cleaned = _normalize_numeric_str(raw_value)
    if not cleaned:
        return default
    try:
        return int(Decimal(cleaned))
    except Exception:
        return default


def _audit_progress_stats(audit: AssetAudit) -> dict:
    agg = audit.items.aggregate(
        total=Count('id'),
        counted=Count('id', filter=Q(is_counted=True)),
        recount=Count('id', filter=Q(needs_recount=True)),
    )
    total = agg['total'] or 0
    counted = agg['counted'] or 0
    recount = agg['recount'] or 0
    uncounted = max(0, total - counted)
    pct = int(round((counted / total) * 100)) if total > 0 else 0
    return {
        'total_items': total,
        'counted_items': counted,
        'uncounted_items': uncounted,
        'recount_items': recount,
        'progress_pct': pct,
    }


def _enrich_audit_item_display(item: AssetAuditItem, *, include_expected: bool) -> dict:
    """ساخت داده نمایشی کارت انبارگردانی با حفظ کامل امنیت شمارش کور."""
    is_serialized = bool(item.asset_unit_id or item.asset.tracking_mode == 'serialized')
    thumb_url = ''
    full_image_url = ''
    if item.asset.thumbnail:
        thumb_url = item.asset.thumbnail.url
    if item.asset.image:
        full_image_url = item.asset.image.url
    if not thumb_url:
        thumb_url = full_image_url
    if not full_image_url:
        full_image_url = thumb_url

    if not thumb_url and item.asset_unit_id and item.asset_unit and item.asset_unit.photo:
        thumb_url = item.asset_unit.photo.url
        full_image_url = thumb_url

    row = {
        'obj': item,
        'id': item.id,
        'is_serialized': is_serialized,
        'thumb_url': thumb_url,
        'full_image_url': full_image_url,
        'counted_healthy_val': item.counted_healthy if item.counted_healthy is not None else 0,
        'counted_damaged_val': item.counted_damaged if item.counted_damaged is not None else 0,
        'counted_status_val': item.counted_status or '',
        'counted_status_label': AUDIT_CONDITION_LABELS.get(item.counted_status, item.counted_status or '—'),
    }
    if include_expected:
        row['expected_healthy_val'] = item.expected_healthy
        row['expected_damaged_val'] = item.expected_damaged
        row['expected_status_label'] = AUDIT_CONDITION_LABELS.get(
            item.expected_status, item.expected_status or '—'
        )
    return row


def _safe_reverse(url_name: str, cafe_code: str | None) -> str:
    if not cafe_code:
        return ''
    try:
        return reverse(url_name, kwargs={'cafe_code': cafe_code})
    except NoReverseMatch:
        return ''


def _shape_pdf_text(text) -> str:
    """بازآرایی متن فارسی برای ReportLab مطابق استاندارد reports.py."""
    if text is None:
        return ''
    s = str(text)
    if not s:
        return ''
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(s))
    except ImportError:
        return s


def _register_persian_pdf_font() -> str:
    """ثبت فونت فارسی Vazir از staticfiles یا frontend/static."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_name = 'Vazir'
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name

    base_dir = str(getattr(settings, 'BASE_DIR', ''))
    candidates = [
        os.path.join(base_dir, 'staticfiles', 'fonts', 'Vazir.ttf'),
        os.path.join(base_dir, 'frontend', 'static', 'fonts', 'Vazir.ttf'),
        os.path.join(base_dir, 'staticfiles', 'fonts', 'Vazirmatn-Regular.ttf'),
        os.path.join(base_dir, 'frontend', 'static', 'fonts', 'Vazirmatn-Regular.ttf'),
        r'C:\Windows\Fonts\tahoma.ttf',
        r'C:\Windows\Fonts\arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, path))
                return font_name
            except Exception:
                continue
    return 'Helvetica'


def _build_excel_response(
    *,
    filename: str,
    sheet_title: str,
    headers: list[str],
    rows: list[list],
    summary_row: list | None = None,
    numeric_cols: set[int] | None = None,
) -> HttpResponse:
    """ساخت فایل Excel راست‌به‌چپ با openpyxl و استایل یکسان."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    ws.sheet_view.rightToLeft = True

    header_fill = PatternFill(start_color='EEF2FF', end_color='EEF2FF', fill_type='solid')
    summary_fill = PatternFill(start_color='ECFDF5', end_color='ECFDF5', fill_type='solid')
    header_font = Font(bold=True, size=11, color='1E1B4B')
    body_font = Font(size=10, color='0F172A')
    summary_font = Font(bold=True, size=10, color='065F46')
    thin_side = Side(style='thin', color='CBD5E1')
    border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 28

    num_cols = numeric_cols or set()
    for row_idx, row_vals in enumerate(rows, start=2):
        for col_idx, val in enumerate(row_vals, start=1):
            if isinstance(val, Decimal):
                val = float(val)
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = body_font
            cell.border = border
            if col_idx in num_cols and isinstance(val, (int, float)):
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal='center', vertical='center')
            else:
                cell.alignment = Alignment(horizontal='right', vertical='center', wrap_text=True)

    if summary_row:
        s_row_idx = len(rows) + 2
        for col_idx, val in enumerate(summary_row, start=1):
            if isinstance(val, Decimal):
                val = float(val)
            cell = ws.cell(row=s_row_idx, column=col_idx, value=val)
            cell.font = summary_font
            cell.fill = summary_fill
            cell.border = border
            if col_idx in num_cols and isinstance(val, (int, float)):
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal='center', vertical='center')
            else:
                cell.alignment = Alignment(horizontal='right', vertical='center')

    for col_idx in range(1, len(headers) + 1):
        col_letter = openpyxl.utils.get_column_letter(col_idx)
        max_len = len(str(headers[col_idx - 1]))
        for row_vals in rows[:200]:
            if col_idx - 1 < len(row_vals):
                max_len = max(max_len, len(str(row_vals[col_idx - 1] or '')))
        ws.column_dimensions[col_letter].width = min(max(max_len + 5, 12), 42)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _build_pdf_table_response(
    *,
    filename: str,
    title: str,
    subtitle: str,
    headers: list[str],
    rows: list[list[str]],
    col_weights: list[float] | None = None,
    summary_row: list[str] | None = None,
) -> HttpResponse:
    """تولید PDF فارسی RTL چندصفحه‌ای با ReportLab و فونت Vazir."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle

    buffer = io.BytesIO()
    page_width, page_height = A4[1], A4[0]  # A4 Landscape
    c = canvas.Canvas(buffer, pagesize=(page_width, page_height))
    font_name = _register_persian_pdf_font()

    margin = 1.3 * cm
    content_width = page_width - 2 * margin
    print_date_jalali = _format_jalali_date(timezone.localdate())

    def _draw_header(page_num: int):
        c.setFont(font_name, 14)
        title_shaped = _shape_pdf_text(title)
        c.drawRightString(page_width - margin, page_height - margin, title_shaped)

        c.setFont(font_name, 9)
        c.setFillColor(colors.HexColor('#475569'))
        c.drawString(margin, page_height - margin, _shape_pdf_text(f'تاریخ چاپ: {print_date_jalali}'))
        if subtitle:
            c.drawRightString(
                page_width - margin,
                page_height - margin - 0.55 * cm,
                _shape_pdf_text(subtitle),
            )
        c.setFillColor(colors.black)
        c.setFont(font_name, 8)
        c.drawCentredString(page_width / 2, 0.7 * cm, _shape_pdf_text(f'صفحه {page_num}'))

    rev_headers = list(reversed(headers))
    data = [[_shape_pdf_text(h) for h in rev_headers]]
    for row in rows:
        data.append([_shape_pdf_text(cell) for cell in reversed(row)])
    if summary_row:
        data.append([_shape_pdf_text(cell) for cell in reversed(summary_row)])

    if col_weights and len(col_weights) == len(headers):
        rev_weights = list(reversed(col_weights))
        total_w = sum(rev_weights) or len(headers)
        col_widths = [(w / total_w) * content_width for w in rev_weights]
    else:
        col_widths = [content_width / max(1, len(headers))] * len(headers)

    style_cmds = [
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, 0), 8.5),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EEF2FF')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1E1B4B')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]
    if summary_row:
        style_cmds.extend([
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#ECFDF5')),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor('#065F46')),
        ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))

    table_top = page_height - margin - (1.25 * cm if subtitle else 0.75 * cm)
    avail_height = table_top - 1.3 * cm
    pieces = table.split(content_width, avail_height) or [table]

    for idx, piece in enumerate(pieces, start=1):
        if idx > 1:
            c.showPage()
        _draw_header(idx)
        _, ph = piece.wrap(content_width, avail_height)
        piece.drawOn(c, margin, max(1.3 * cm, table_top - ph))

    c.save()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _fmt_money(value) -> str:
    try:
        return f'{int(round(Decimal(str(value or 0)))):,}'
    except Exception:
        return '0'


def _parse_report_date_range(request, default_days: int = 30) -> tuple[date, date, str, str]:
    today = timezone.localdate()
    default_start = today - timedelta(days=default_days)
    raw_start = (request.GET.get('start_date') or '').strip()
    raw_end = (request.GET.get('end_date') or '').strip()

    start_dt = _parse_jalali_date(raw_start) if raw_start else default_start
    if start_dt is None:
        start_dt = default_start
    end_dt = _parse_jalali_date(raw_end) if raw_end else today
    if end_dt is None:
        end_dt = today
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    return start_dt, end_dt, _format_jalali_date(start_dt), _format_jalali_date(end_dt)


def _subtract_jalali_months(j_year: int, j_month: int, count: int) -> list[tuple[int, int]]:
    """تولید لیست (سال، ماه) شمسی به ترتیب زمانی از قدیم به جدید."""
    months = []
    y, m = j_year, j_month
    for _ in range(count):
        months.append((y, m))
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    months.reverse()
    return months


# ---------------------------------------------------------------------------
# Audit workflow views (Section user mobile-first + Manager review)
# ---------------------------------------------------------------------------


@custom_login_required
def asset_audit_list(request):
    """لیست جلسات انبارگردانی اموال با فیلتر سکشن، نوع و وضعیت."""
    cafe = _require_active_cafe(request)
    is_manager = user_can_manage_assets(request.user)
    is_requester = getattr(request.user, 'role', None) == 'requester' or getattr(
        request.user, 'secondary_role', None
    ) == 'requester'

    if not is_manager and not is_requester:
        return HttpResponseForbidden('شما مجاز به مشاهده جلسات انبارگردانی نیستید.')

    audits_qs = AssetAudit.objects.filter(cafe=cafe).select_related(
        'department', 'created_by', 'counted_by', 'reviewed_by'
    )

    if not is_manager:
        if not getattr(request.user, 'department_id', None):
            audits_qs = audits_qs.none()
        else:
            audits_qs = audits_qs.filter(department_id=request.user.department_id)

    departments = (
        Department.objects.filter(cafe=cafe, is_active=True).order_by('name')
        if is_manager
        else Department.objects.filter(cafe=cafe, id=request.user.department_id)
        if getattr(request.user, 'department_id', None)
        else Department.objects.none()
    )

    selected_department = (request.GET.get('department') or '').strip()
    selected_type = (request.GET.get('audit_type') or '').strip()
    selected_status = (request.GET.get('status') or '').strip()

    if selected_department.isdigit() and is_manager:
        audits_qs = audits_qs.filter(department_id=int(selected_department))
    if selected_type in dict(AssetAudit.AUDIT_TYPE_CHOICES):
        audits_qs = audits_qs.filter(audit_type=selected_type)
    if selected_status in dict(AssetAudit.STATUS_CHOICES):
        audits_qs = audits_qs.filter(status=selected_status)

    audits_qs = audits_qs.annotate(
        total_items=Count('items', distinct=True),
        counted_items=Count('items', filter=Q(items__is_counted=True), distinct=True),
        recount_items=Count('items', filter=Q(items__needs_recount=True), distinct=True),
    ).order_by('-created_at')

    audits = list(audits_qs[:100])
    for a in audits:
        a.progress_pct = int(round((a.counted_items / a.total_items) * 100)) if a.total_items else 0

    return render(
        request,
        'assets/audit_list.html',
        {
            'audits': audits,
            'departments': departments,
            'selected_department': selected_department,
            'selected_type': selected_type,
            'selected_status': selected_status,
            'audit_type_choices': AssetAudit.AUDIT_TYPE_CHOICES,
            'status_choices': AssetAudit.STATUS_CHOICES,
            'is_manager': is_manager,
        },
    )


@custom_login_required
def asset_audit_create(request):
    """ایجاد جلسه جدید انبارگردانی (شمارش کامل / موردی / تحویل و تحول + شمارش کور)."""
    cafe = _require_active_cafe(request)
    if not user_can_manage_assets(request.user):
        return HttpResponseForbidden('فقط مدیران یا مسئول انبار مجاز به ایجاد انبارگردانی هستند.')

    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))
    catalog_assets = list(
        AssetCatalog.objects.filter(cafe=cafe, is_active=True)
        .select_related('category')
        .order_by('name')
    )

    if request.method == 'POST':
        dept_raw = (request.POST.get('department_id') or '').strip()
        title = (request.POST.get('title') or '').strip()
        audit_type = (request.POST.get('audit_type') or 'full').strip()
        blind_count = (request.POST.get('blind_count') or '').lower() in ('on', '1', 'true', 'yes')
        notes = (request.POST.get('notes') or '').strip()
        raw_asset_ids = [int(x) for x in request.POST.getlist('asset_ids') if str(x).isdigit()]

        department = departments and next((d for d in departments if str(d.id) == dept_raw), None)
        if department is None and dept_raw.isdigit():
            department = Department.objects.filter(cafe=cafe, id=int(dept_raw)).first()

        selected_asset_ids = raw_asset_ids if (audit_type == 'spot' and raw_asset_ids) else None

        try:
            audit = create_audit_session(
                cafe=cafe,
                department=department,
                title=title,
                user=request.user,
                audit_type=audit_type,
                blind_count=blind_count,
                asset_ids=selected_asset_ids,
                notes=notes,
            )
            messages.success(request, f'جلسه انبارگردانی «{audit.title}» با موفقیت ایجاد شد.')
            return redirect(get_cafe_url('asset_audit_count', request, audit_id=audit.id))
        except AssetServiceError as exc:
            messages.error(request, str(exc))

    return render(
        request,
        'assets/audit_create.html',
        {
            'departments': departments,
            'catalog_assets': catalog_assets,
            'audit_type_choices': AssetAudit.AUDIT_TYPE_CHOICES,
        },
    )


@custom_login_required
def asset_audit_count(request, audit_id: int):
    """صفحه موبایل-محور شمارش انبارگردانی توسط کاربر سکشن."""
    audit = get_scoped_object_or_404(AssetAudit, request, pk=audit_id)
    if not _user_can_view_audit(request.user, audit):
        return HttpResponseForbidden('شما مجاز به دسترسی به انبارگردانی این سکشن نیستید.')

    is_manager = user_can_manage_assets(request.user)
    include_expected = not audit.blind_count

    items_qs = (
        audit.items.select_related('asset', 'asset__category', 'asset_unit')
        .order_by('-needs_recount', 'is_counted', 'id')
    )
    cards = [_enrich_audit_item_display(it, include_expected=include_expected) for it in items_qs]
    progress = _audit_progress_stats(audit)

    catalog_assets = list(
        AssetCatalog.objects.filter(cafe=audit.cafe, is_active=True, tracking_mode='quantity')
        .select_related('category')
        .order_by('name')
    )
    can_edit = audit.status in ('in_progress', 'recount_requested')

    return render(
        request,
        'assets/audit_count_mobile.html',
        {
            'audit': audit,
            'cards': cards,
            'progress': progress,
            'can_edit': can_edit,
            'is_manager': is_manager,
            'include_expected': include_expected,
            'condition_choices': AssetAuditItem.CONDITION_CHOICES,
            'catalog_assets': catalog_assets,
        },
    )


@custom_login_required
@require_POST
def asset_audit_save_item(request, audit_id: int, item_id: int):
    """ذخیره خودکار HTMX یک ردیف شمارش در انبارگردانی."""
    audit = get_scoped_object_or_404(AssetAudit, request, pk=audit_id)
    if not _user_can_view_audit(request.user, audit):
        return HttpResponseForbidden('شما مجاز به ثبت شمارش برای این سکشن نیستید.')

    item = get_object_or_404(
        AssetAuditItem.objects.select_related('audit', 'asset', 'asset__category', 'asset_unit'),
        pk=item_id,
        audit=audit,
    )
    include_expected = not audit.blind_count
    can_edit = audit.status in ('in_progress', 'recount_requested')

    note = (request.POST.get('note') or '').strip()
    counted_status = (request.POST.get('counted_status') or '').strip()
    counted_healthy = _parse_int_input(request.POST.get('counted_healthy'), default=0)
    counted_damaged = _parse_int_input(request.POST.get('counted_damaged'), default=0)

    evidence_photo = None
    uploaded = request.FILES.get('evidence_photo')
    error_message = ''

    if uploaded is not None:
        try:
            evidence_photo = compress_evidence_image(uploaded)
        except AssetServiceError as exc:
            error_message = str(exc)

    saved_just_now = False
    if not error_message:
        try:
            item = save_audit_item_count(
                item,
                request.user,
                counted_healthy=counted_healthy,
                counted_damaged=counted_damaged,
                counted_status=counted_status,
                note=note,
                evidence_photo=evidence_photo,
            )
            saved_just_now = True
        except AssetPermissionError as exc:
            return HttpResponseForbidden(str(exc))
        except AssetServiceError as exc:
            error_message = str(exc)

    card = _enrich_audit_item_display(item, include_expected=include_expected)
    progress = _audit_progress_stats(audit)
    status_code = 200 if not error_message else 400

    return render(
        request,
        'assets/partials/audit_item_card.html',
        {
            'audit': audit,
            'card': card,
            'can_edit': can_edit,
            'include_expected': include_expected,
            'condition_choices': AssetAuditItem.CONDITION_CHOICES,
            'saved_just_now': saved_just_now,
            'error_message': error_message,
            'progress': progress,
            'oob_progress': saved_just_now,
        },
        status=status_code,
    )


@custom_login_required
@require_POST
def asset_audit_add_unexpected(request, audit_id: int):
    """ثبت کالای یافت‌شده خارج از لیست اولیه انبارگردانی."""
    audit = get_scoped_object_or_404(AssetAudit, request, pk=audit_id)
    if not _user_can_view_audit(request.user, audit):
        return HttpResponseForbidden('شما مجاز به افزودن قلم به این انبارگردانی نیستید.')

    asset_id = _parse_int_input(request.POST.get('asset_id'), default=0)
    asset = get_object_or_404(AssetCatalog, pk=asset_id, cafe=audit.cafe)
    counted_healthy = _parse_int_input(request.POST.get('counted_healthy'), default=0)
    counted_damaged = _parse_int_input(request.POST.get('counted_damaged'), default=0)
    note = (request.POST.get('note') or '').strip()

    evidence_photo = None
    uploaded = request.FILES.get('evidence_photo')
    try:
        if uploaded is not None:
            evidence_photo = compress_evidence_image(uploaded)
        new_item = add_unexpected_audit_item(
            audit=audit,
            asset=asset,
            counted_healthy=counted_healthy,
            counted_damaged=counted_damaged,
            user=request.user,
            note=note,
            evidence_photo=evidence_photo,
        )
    except AssetPermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except AssetServiceError as exc:
        if request.headers.get('HX-Request') == 'true':
            return HttpResponse(str(exc), status=400)
        messages.error(request, str(exc))
        return redirect(get_cafe_url('asset_audit_count', request, audit_id=audit.id))

    if request.headers.get('HX-Request') == 'true':
        card = _enrich_audit_item_display(new_item, include_expected=not audit.blind_count)
        progress = _audit_progress_stats(audit)
        return render(
            request,
            'assets/partials/audit_item_card.html',
            {
                'audit': audit,
                'card': card,
                'can_edit': True,
                'include_expected': not audit.blind_count,
                'condition_choices': AssetAuditItem.CONDITION_CHOICES,
                'saved_just_now': True,
                'progress': progress,
                'oob_progress': True,
            },
        )

    messages.success(request, f'کالای «{asset.name}» به لیست انبارگردانی اضافه شد.')
    return redirect(get_cafe_url('asset_audit_count', request, audit_id=audit.id))


@custom_login_required
@require_POST
def asset_audit_submit(request, audit_id: int):
    """ارسال انبارگردانی برای بررسی و تأیید مدیر."""
    audit = get_scoped_object_or_404(AssetAudit, request, pk=audit_id)
    if not _user_can_view_audit(request.user, audit):
        return HttpResponseForbidden('شما مجاز به ارسال این انبارگردانی نیستید.')

    try:
        submit_audit(audit, request.user)
        messages.success(request, 'انبارگردانی با موفقیت برای بررسی و تأیید مدیر ارسال شد.')
    except AssetPermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except AssetServiceError as exc:
        messages.error(request, str(exc))

    if user_can_manage_assets(request.user):
        return redirect(get_cafe_url('asset_audit_review', request, audit_id=audit.id))
    return redirect(get_cafe_url('asset_audit_count', request, audit_id=audit.id))


@custom_login_required
def asset_audit_review(request, audit_id: int):
    """صفحه بررسی مغایرت‌ها، مشاهده عکس مدرک، بازشماری یا تأیید نهایی مدیر."""
    if not user_can_manage_assets(request.user):
        return HttpResponseForbidden('فقط مدیران یا مسئول انبار مجاز به بررسی انبارگردانی هستند.')

    audit = get_scoped_object_or_404(AssetAudit, request, pk=audit_id)
    items = list(
        audit.items.select_related('asset', 'asset__category', 'asset_unit').order_by('id')
    )

    rows = []
    discrepancy_count = 0
    total_shortage_value = Decimal('0')
    total_surplus_value = Decimal('0')
    net_variance_value = Decimal('0')

    for item in items:
        is_serialized = bool(item.asset_unit_id or item.asset.tracking_mode == 'serialized')
        if is_serialized:
            has_discrepancy = bool(
                item.is_counted
                and item.counted_status
                and item.counted_status != item.expected_status
            )
            if item.is_counted and item.counted_status == 'lost':
                unit_val = (
                    (item.asset_unit.purchase_price if item.asset_unit else None)
                    or item.asset.estimated_unit_value
                    or Decimal('0')
                )
                row_val = -Decimal(unit_val)
            else:
                row_val = Decimal('0')
        else:
            has_discrepancy = bool(
                item.is_counted
                and (item.variance_healthy != 0 or item.variance_damaged != 0 or item.is_unexpected_item)
            )
            row_val = item.variance_value if item.is_counted else Decimal('0')

        if has_discrepancy:
            discrepancy_count += 1
        if row_val < 0:
            total_shortage_value += abs(row_val)
        elif row_val > 0:
            total_surplus_value += row_val
        net_variance_value += row_val

        thumb_url = ''
        if item.asset.thumbnail:
            thumb_url = item.asset.thumbnail.url
        elif item.asset.image:
            thumb_url = item.asset.image.url

        rows.append(
            {
                'item': item,
                'is_serialized': is_serialized,
                'has_discrepancy': has_discrepancy,
                'variance_value': row_val,
                'expected_status_label': AUDIT_CONDITION_LABELS.get(
                    item.expected_status, item.expected_status or '—'
                ),
                'counted_status_label': AUDIT_CONDITION_LABELS.get(
                    item.counted_status, item.counted_status or '—'
                ),
                'thumb_url': thumb_url,
                'evidence_url': item.evidence_photo.url if item.evidence_photo else '',
            }
        )

    progress = _audit_progress_stats(audit)
    movements = list(
        audit.movements.select_related('asset', 'asset_unit', 'from_department', 'to_department').order_by('id')
    )

    return render(
        request,
        'assets/audit_review.html',
        {
            'audit': audit,
            'rows': rows,
            'progress': progress,
            'discrepancy_count': discrepancy_count,
            'total_shortage_value': total_shortage_value,
            'total_surplus_value': total_surplus_value,
            'net_variance_value': net_variance_value,
            'movements': movements,
        },
    )


@custom_login_required
@require_POST
def asset_audit_recount(request, audit_id: int):
    """ارجاع ردیف‌های انتخابی انبارگردانی برای بازشماری."""
    if not user_can_manage_assets(request.user):
        return HttpResponseForbidden('فقط مدیران مجاز به درخواست بازشماری هستند.')

    audit = get_scoped_object_or_404(AssetAudit, request, pk=audit_id)
    raw_ids = [int(x) for x in request.POST.getlist('item_ids') if str(x).isdigit()]
    review_notes = (request.POST.get('review_notes') or '').strip()

    try:
        request_audit_recount(
            audit,
            request.user,
            item_ids=raw_ids if raw_ids else None,
            review_notes=review_notes,
        )
        messages.warning(request, 'انبارگردانی برای بازشماری به کاربر سکشن ارجاع داده شد.')
    except AssetPermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except AssetServiceError as exc:
        messages.error(request, str(exc))

    return redirect(get_cafe_url('asset_audit_review', request, audit_id=audit.id))


@custom_login_required
@require_POST
def asset_audit_approve(request, audit_id: int):
    """تأیید نهایی انبارگردانی و اعمال تعدیل‌ها روی موجودی."""
    if not user_can_manage_assets(request.user):
        return HttpResponseForbidden('فقط مدیران مجاز به تأیید نهایی انبارگردانی هستند.')

    audit = get_scoped_object_or_404(AssetAudit, request, pk=audit_id)
    review_notes = (request.POST.get('review_notes') or '').strip()

    try:
        movements = approve_audit_and_apply_adjustments(
            audit,
            request.user,
            review_notes=review_notes,
        )
        messages.success(
            request,
            f'انبارگردانی تأیید نهایی شد و {len(movements)} سند تعدیل موجودی ثبت گردید.',
        )
    except AssetPermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except AssetServiceError as exc:
        messages.error(request, str(exc))

    return redirect(get_cafe_url('asset_audit_review', request, audit_id=audit.id))


# ---------------------------------------------------------------------------
# Report query builders (shared between HTML views & Excel/PDF exporters)
# ---------------------------------------------------------------------------


def _build_valuation_data(request, current_cafe: Cafe) -> dict:
    is_global_admin = _user_is_global_admin(request.user)
    available_cafes = (
        list(Cafe.objects.filter(is_active=True).order_by('name'))
        if is_global_admin
        else [current_cafe]
    )

    selected_cafe_param = (request.GET.get('cafe') or '').strip()
    if is_global_admin and selected_cafe_param == 'all':
        target_cafes = available_cafes
        selected_cafe_val = 'all'
    elif is_global_admin and selected_cafe_param.isdigit():
        chosen = next((c for c in available_cafes if c.id == int(selected_cafe_param)), current_cafe)
        target_cafes = [chosen]
        selected_cafe_val = str(chosen.id)
    else:
        target_cafes = [current_cafe]
        selected_cafe_val = str(current_cafe.id)

    cafe_ids = [c.id for c in target_cafes]
    departments = list(
        Department.objects.filter(cafe_id__in=cafe_ids, is_active=True)
        .select_related('cafe')
        .order_by('cafe__name', 'name')
    )
    categories = list(
        AssetCategory.objects.filter(cafe_id__in=cafe_ids, is_active=True)
        .select_related('parent')
        .order_by('name')
    )

    selected_department = (request.GET.get('department') or '').strip()
    selected_category = (request.GET.get('category') or '').strip()
    selected_mode = (request.GET.get('tracking_mode') or '').strip()
    search_q = (request.GET.get('q') or '').strip()

    stocks_qs = (
        DepartmentAssetStock.objects.filter(cafe_id__in=cafe_ids)
        .select_related('cafe', 'department', 'asset', 'asset__category')
        .order_by('department__name', 'asset__name')
    )
    if selected_department.isdigit():
        stocks_qs = stocks_qs.filter(department_id=int(selected_department))
    if selected_category.isdigit():
        cat_id = int(selected_category)
        stocks_qs = stocks_qs.filter(
            Q(asset__category_id=cat_id) | Q(asset__category__parent_id=cat_id)
        )
    if selected_mode in ('quantity', 'serialized'):
        stocks_qs = stocks_qs.filter(asset__tracking_mode=selected_mode)
    if search_q:
        stocks_qs = stocks_qs.filter(
            Q(asset__name__icontains=search_q)
            | Q(asset__code__icontains=search_q)
            | Q(asset__brand_model__icontains=search_q)
        )

    stocks = list(stocks_qs)

    # ارزش واقعی اقلام پلاکدار بر اساس قیمت خرید پلاک‌ها در هر بخش
    unit_values_map: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal('0'))
    unit_counts_map: dict[tuple[int, int], int] = defaultdict(int)
    serialized_units = (
        AssetUnit.objects.filter(
            cafe_id__in=cafe_ids,
            department__isnull=False,
            status__in=('active', 'needs_repair', 'in_repair'),
        )
        .select_related('asset')
        .only('department_id', 'asset_id', 'purchase_price', 'asset__estimated_unit_value')
    )
    for u in serialized_units:
        key = (u.department_id, u.asset_id)
        val = u.purchase_price if u.purchase_price and u.purchase_price > 0 else (u.asset.estimated_unit_value or Decimal('0'))
        unit_values_map[key] += Decimal(val)
        unit_counts_map[key] += 1

    rows = []
    section_totals: dict[str, dict] = {}
    grand_healthy = 0
    grand_damaged = 0
    grand_total_value = Decimal('0')

    for st in stocks:
        total_qty = st.total_quantity
        if st.asset.tracking_mode == 'serialized' and unit_counts_map.get((st.department_id, st.asset_id)):
            row_value = unit_values_map[(st.department_id, st.asset_id)]
            unit_val = (row_value / Decimal(unit_counts_map[(st.department_id, st.asset_id)])).quantize(Decimal('1'))
        else:
            unit_val = Decimal(st.asset.estimated_unit_value or 0)
            row_value = Decimal(total_qty) * unit_val

        grand_healthy += st.quantity_healthy
        grand_damaged += st.quantity_damaged
        grand_total_value += row_value

        sec_label = (
            f'{st.department.name} ({st.cafe.name})'
            if len(target_cafes) > 1
            else st.department.name
        )
        sec_entry = section_totals.setdefault(
            sec_label,
            {'label': sec_label, 'value': Decimal('0'), 'healthy': 0, 'damaged': 0},
        )
        sec_entry['value'] += row_value
        sec_entry['healthy'] += st.quantity_healthy
        sec_entry['damaged'] += st.quantity_damaged

        thumb_url = ''
        full_url = ''
        if st.asset.thumbnail:
            thumb_url = st.asset.thumbnail.url
        if st.asset.image:
            full_url = st.asset.image.url
        if not thumb_url:
            thumb_url = full_url
        if not full_url:
            full_url = thumb_url

        rows.append(
            {
                'stock': st,
                'cafe_name': st.cafe.name,
                'department_name': st.department.name,
                'asset_name': st.asset.name,
                'asset_code': st.asset.code,
                'category_name': str(st.asset.category) if st.asset.category else 'بدون دسته',
                'tracking_mode_display': st.asset.get_tracking_mode_display(),
                'quantity_healthy': st.quantity_healthy,
                'quantity_damaged': st.quantity_damaged,
                'total_quantity': total_qty,
                'unit_value': unit_val,
                'total_value': row_value,
                'thumb_url': thumb_url,
                'full_url': full_url,
            }
        )

    section_list = sorted(section_totals.values(), key=lambda x: x['value'], reverse=True)
    chart_data = {
        'labels': [s['label'] for s in section_list],
        'values': [float(s['value']) for s in section_list],
        'healthy': [s['healthy'] for s in section_list],
        'damaged': [s['damaged'] for s in section_list],
    }

    return {
        'is_global_admin': is_global_admin,
        'available_cafes': available_cafes,
        'selected_cafe_val': selected_cafe_val,
        'show_cafe_column': len(target_cafes) > 1,
        'departments': departments,
        'categories': categories,
        'selected_department': selected_department,
        'selected_category': selected_category,
        'selected_mode': selected_mode,
        'search_q': search_q,
        'rows': rows,
        'section_list': section_list,
        'grand_healthy': grand_healthy,
        'grand_damaged': grand_damaged,
        'grand_total_qty': grand_healthy + grand_damaged,
        'grand_total_value': grand_total_value,
        'chart_data_json': json.dumps(chart_data, ensure_ascii=False),
    }


def _build_variance_data(request, cafe: Cafe) -> dict:
    start_dt, end_dt, start_jalali, end_jalali = _parse_report_date_range(request, default_days=60)
    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))
    audits = list(
        AssetAudit.objects.filter(
            cafe=cafe,
            status__in=('submitted', 'recount_requested', 'approved'),
        )
        .select_related('department')
        .order_by('-created_at')[:50]
    )

    selected_department = (request.GET.get('department') or '').strip()
    selected_audit = (request.GET.get('audit') or '').strip()
    only_discrepancies = (request.GET.get('only_discrepancies') or '') in ('1', 'on', 'true')

    items_qs = (
        AssetAuditItem.objects.filter(
            audit__cafe=cafe,
            audit__status__in=('submitted', 'recount_requested', 'approved'),
            audit__created_at__date__gte=start_dt,
            audit__created_at__date__lte=end_dt,
            is_counted=True,
        )
        .select_related('audit', 'audit__department', 'asset', 'asset_unit')
        .order_by('-audit__created_at', 'id')
    )
    if selected_department.isdigit():
        items_qs = items_qs.filter(audit__department_id=int(selected_department))
    if selected_audit.isdigit():
        items_qs = items_qs.filter(audit_id=int(selected_audit))

    rows = []
    dept_loss: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    total_shortage_val = Decimal('0')
    total_surplus_val = Decimal('0')
    discrepancy_rows_count = 0

    for item in items_qs:
        is_serialized = bool(item.asset_unit_id or item.asset.tracking_mode == 'serialized')
        if is_serialized:
            exp_txt = AUDIT_CONDITION_LABELS.get(item.expected_status, item.expected_status or '—')
            cnt_txt = AUDIT_CONDITION_LABELS.get(item.counted_status, item.counted_status or '—')
            diff_qty = 0
            has_diff = bool(item.counted_status and item.counted_status != item.expected_status)
            if item.counted_status == 'lost':
                unit_val = (
                    (item.asset_unit.purchase_price if item.asset_unit else None)
                    or item.asset.estimated_unit_value
                    or Decimal('0')
                )
                val_diff = -Decimal(unit_val)
                diff_qty = -1
            else:
                val_diff = Decimal('0')
            diff_label = f'{exp_txt} ← {cnt_txt}' if has_diff else 'مطابق'
        else:
            exp_txt = f'س:{item.expected_healthy} / م:{item.expected_damaged}'
            cnt_txt = f'س:{item.counted_healthy or 0} / م:{item.counted_damaged or 0}'
            diff_qty = item.variance_total
            val_diff = item.variance_value
            has_diff = bool(item.variance_healthy != 0 or item.variance_damaged != 0 or item.is_unexpected_item)
            if diff_qty > 0:
                diff_label = f'+{diff_qty} اضافی'
            elif diff_qty < 0:
                diff_label = f'{diff_qty} کسری'
            elif item.variance_healthy != 0 or item.variance_damaged != 0:
                diff_label = f'س:{item.variance_healthy:+d} / م:{item.variance_damaged:+d}'
            else:
                diff_label = 'مطابق'

        if only_discrepancies and not has_diff:
            continue

        if has_diff:
            discrepancy_rows_count += 1
        if val_diff < 0:
            total_shortage_val += abs(val_diff)
            dept_loss[item.audit.department.name] += abs(val_diff)
        elif val_diff > 0:
            total_surplus_val += val_diff

        rows.append(
            {
                'item': item,
                'audit': item.audit,
                'department_name': item.audit.department.name,
                'asset_name': item.asset.name,
                'asset_tag': item.asset_unit.asset_tag if item.asset_unit_id else '',
                'audit_date_jalali': _format_jalali_date(item.audit.created_at),
                'expected_text': exp_txt,
                'counted_text': cnt_txt,
                'diff_qty': diff_qty,
                'diff_label': diff_label,
                'has_diff': has_diff,
                'variance_value': val_diff,
                'note': item.note,
                'evidence_url': item.evidence_photo.url if item.evidence_photo else '',
            }
        )

    chart_labels = list(dept_loss.keys())
    chart_values = [float(dept_loss[k]) for k in chart_labels]
    return {
        'departments': departments,
        'audits': audits,
        'selected_department': selected_department,
        'selected_audit': selected_audit,
        'only_discrepancies': only_discrepancies,
        'start_jalali': start_jalali,
        'end_jalali': end_jalali,
        'rows': rows,
        'discrepancy_rows_count': discrepancy_rows_count,
        'total_shortage_val': total_shortage_val,
        'total_surplus_val': total_surplus_val,
        'net_variance_val': total_surplus_val - total_shortage_val,
        'chart_data_json': json.dumps(
            {'labels': chart_labels, 'values': chart_values},
            ensure_ascii=False,
        ),
    }


def _build_kardex_data(request, cafe: Cafe) -> dict:
    start_dt, end_dt, start_jalali, end_jalali = _parse_report_date_range(request, default_days=90)
    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))
    assets = list(AssetCatalog.objects.filter(cafe=cafe).order_by('name'))

    selected_department = (request.GET.get('department') or '').strip()
    selected_asset = (request.GET.get('asset') or '').strip()
    selected_type = (request.GET.get('movement_type') or '').strip()
    selected_status = (request.GET.get('status') or '').strip()

    qs = (
        AssetMovement.objects.filter(
            cafe=cafe,
            created_at__date__gte=start_dt,
            created_at__date__lte=end_dt,
        )
        .select_related(
            'asset',
            'asset_unit',
            'from_department',
            'to_department',
            'audit',
            'invoice',
            'created_by',
            'approved_by',
        )
        .order_by('-created_at', '-id')
    )
    if selected_department.isdigit():
        dept_id = int(selected_department)
        qs = qs.filter(Q(from_department_id=dept_id) | Q(to_department_id=dept_id))
    if selected_asset.isdigit():
        qs = qs.filter(asset_id=int(selected_asset))
    if selected_type in MOVEMENT_TYPE_LABELS:
        qs = qs.filter(movement_type=selected_type)
    if selected_status in MOVEMENT_STATUS_LABELS:
        qs = qs.filter(status=selected_status)

    return {
        'queryset': qs,
        'departments': departments,
        'assets': assets,
        'selected_department': selected_department,
        'selected_asset': selected_asset,
        'selected_type': selected_type,
        'selected_status': selected_status,
        'movement_types': AssetMovement.MOVEMENT_TYPE_CHOICES,
        'movement_statuses': AssetMovement.STATUS_CHOICES,
        'start_jalali': start_jalali,
        'end_jalali': end_jalali,
    }


# ---------------------------------------------------------------------------
# 6 Asset Reports + Report Dashboard + Excel/PDF Exports
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_reports_dashboard(request):
    """مرکز گزارش‌های اموال با خلاصه KPI و نمودارهای Chart.js."""
    cafe = _require_active_cafe(request)
    today = timezone.localdate()

    valuation = _build_valuation_data(request, cafe)
    open_audits_count = AssetAudit.objects.filter(
        cafe=cafe, status__in=('in_progress', 'submitted', 'recount_requested')
    ).count()
    shortage_count = DepartmentAssetStock.objects.filter(
        cafe=cafe,
        min_expected_quantity__gt=0,
        quantity_healthy__lt=F('min_expected_quantity'),
    ).count()
    repairs_count = AssetUnit.objects.filter(
        cafe=cafe, status__in=('needs_repair', 'in_repair')
    ).count()
    expiring_warranty_count = AssetUnit.objects.filter(
        cafe=cafe,
        warranty_until__gte=today,
        warranty_until__lte=today + timedelta(days=30),
    ).exclude(status__in=('lost', 'disposed')).count()

    # روند ۶ ماهه شکستگی برای نمودار داشبورد
    j_today = jdatetime.date.fromgregorian(date=today)
    months_seq = _subtract_jalali_months(j_today.year, j_today.month, 6)
    first_y, first_m = months_seq[0]
    since_dt = jdatetime.date(first_y, first_m, 1).togregorian()
    loss_movements = (
        AssetMovement.objects.filter(
            cafe=cafe,
            status='approved',
            created_at__date__gte=since_dt,
        )
        .filter(Q(movement_type='disposal') | Q(movement_type='audit_adjustment', quantity__lt=0))
        .only('created_at', 'quantity', 'unit_value_snapshot')
    )
    monthly_loss_qty = defaultdict(int)
    for mv in loss_movements:
        dt = timezone.localtime(mv.created_at).date() if timezone.is_aware(mv.created_at) else mv.created_at.date()
        jd = jdatetime.date.fromgregorian(date=dt)
        monthly_loss_qty[(jd.year, jd.month)] += abs(int(mv.quantity or 0))

    trend_labels = [f'{JALALI_MONTH_NAMES[m]} {y}' for y, m in months_seq]
    trend_values = [monthly_loss_qty[(y, m)] for y, m in months_seq]

    return render(
        request,
        'assets/report_dashboard.html',
        {
            'total_value': valuation['grand_total_value'],
            'total_healthy': valuation['grand_healthy'],
            'total_damaged': valuation['grand_damaged'],
            'open_audits_count': open_audits_count,
            'shortage_count': shortage_count,
            'repairs_count': repairs_count,
            'expiring_warranty_count': expiring_warranty_count,
            'valuation_chart_json': valuation['chart_data_json'],
            'trend_chart_json': json.dumps(
                {'labels': trend_labels, 'values': trend_values},
                ensure_ascii=False,
            ),
        },
    )


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_valuation(request):
    """گزارش ۱: صورتبرداری و ارزش ریالی اموال به تفکیک سکشن."""
    cafe = _require_active_cafe(request)
    ctx = _build_valuation_data(request, cafe)
    return render(request, 'assets/report_section_valuation.html', ctx)


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_valuation_export(request, fmt: str):
    """خروجی Excel یا PDF گزارش صورتبرداری و ارزش اموال."""
    cafe = _require_active_cafe(request)
    fmt_clean = (fmt or '').strip().lower()
    if fmt_clean not in ('excel', 'pdf'):
        return HttpResponseBadRequest('فرمت خروجی نامعتبر است.')

    data = _build_valuation_data(request, cafe)
    show_cafe = data['show_cafe_column']

    if fmt_clean == 'excel':
        headers = ['ردیف']
        if show_cafe:
            headers.append('کافه')
        headers.extend([
            'سکشن / بخش',
            'نام مال',
            'کد کالا',
            'دسته‌بندی',
            'نوع ردیابی',
            'تعداد سالم',
            'تعداد معیوب',
            'مجموع تعداد',
            'ارزش واحد (ریال)',
            'ارزش کل (ریال)',
        ])
        excel_rows = []
        for idx, r in enumerate(data['rows'], start=1):
            row = [idx]
            if show_cafe:
                row.append(r['cafe_name'])
            row.extend([
                r['department_name'],
                r['asset_name'],
                r['asset_code'],
                r['category_name'],
                r['tracking_mode_display'],
                r['quantity_healthy'],
                r['quantity_damaged'],
                r['total_quantity'],
                float(r['unit_value']),
                float(r['total_value']),
            ])
            excel_rows.append(row)

        offset = 1 if show_cafe else 0
        summary = ['جمع کل'] + [''] * (5 + offset) + [
            data['grand_healthy'],
            data['grand_damaged'],
            data['grand_total_qty'],
            '',
            float(data['grand_total_value']),
        ]
        num_cols = {7 + offset, 8 + offset, 9 + offset, 10 + offset, 11 + offset}
        return _build_excel_response(
            filename='asset_valuation_report.xlsx',
            sheet_title='ارزش اموال',
            headers=headers,
            rows=excel_rows,
            summary_row=summary,
            numeric_cols=num_cols,
        )

    pdf_headers = ['ردیف', 'سکشن', 'نام کالا', 'دسته', 'سالم', 'معیوب', 'ارزش واحد', 'ارزش کل (ریال)']
    pdf_rows = [
        [
            str(idx),
            r['department_name'],
            r['asset_name'],
            r['category_name'],
            str(r['quantity_healthy']),
            str(r['quantity_damaged']),
            _fmt_money(r['unit_value']),
            _fmt_money(r['total_value']),
        ]
        for idx, r in enumerate(data['rows'], start=1)
    ]
    summary = [
        '',
        'جمع کل',
        '',
        '',
        str(data['grand_healthy']),
        str(data['grand_damaged']),
        '',
        _fmt_money(data['grand_total_value']),
    ]
    return _build_pdf_table_response(
        filename='asset_valuation_report.pdf',
        title=f'گزارش صورتبرداری و ارزش اموال ({cafe.name})',
        subtitle=f'تعداد کل اقلام: {data["grand_total_qty"]} | جمع ارزش: {_fmt_money(data["grand_total_value"])} ریال',
        headers=pdf_headers,
        rows=pdf_rows,
        col_weights=[0.6, 1.8, 2.5, 1.5, 0.8, 0.8, 1.4, 1.6],
        summary_row=summary,
    )


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_variance(request):
    """گزارش ۲: مغایرت انبارگردانی‌ها."""
    cafe = _require_active_cafe(request)
    ctx = _build_variance_data(request, cafe)
    return render(request, 'assets/report_audit_variance.html', ctx)


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_variance_export(request, fmt: str):
    """خروجی Excel یا PDF گزارش مغایرت انبارگردانی."""
    cafe = _require_active_cafe(request)
    fmt_clean = (fmt or '').strip().lower()
    if fmt_clean not in ('excel', 'pdf'):
        return HttpResponseBadRequest('فرمت خروجی نامعتبر است.')

    data = _build_variance_data(request, cafe)

    if fmt_clean == 'excel':
        headers = [
            'ردیف',
            'تاریخ',
            'جلسه انبارگردانی',
            'سکشن',
            'کالا',
            'پلاک',
            'موجودی سیستم',
            'شمارش‌شده',
            'مغایرت',
            'ارزش اختلاف (ریال)',
            'توضیحات',
        ]
        excel_rows = [
            [
                idx,
                r['audit_date_jalali'],
                r['audit'].title,
                r['department_name'],
                r['asset_name'],
                r['asset_tag'] or '-',
                r['expected_text'],
                r['counted_text'],
                r['diff_label'],
                float(r['variance_value']),
                r['note'] or '-',
            ]
            for idx, r in enumerate(data['rows'], start=1)
        ]
        summary = [
            'جمع کل',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            f'{data["discrepancy_rows_count"]} مغایرت',
            float(data['net_variance_val']),
            f'خسارت: {_fmt_money(data["total_shortage_val"])} ریال',
        ]
        return _build_excel_response(
            filename='asset_audit_variance.xlsx',
            sheet_title='مغایرت انبارگردانی',
            headers=headers,
            rows=excel_rows,
            summary_row=summary,
            numeric_cols={10},
        )

    pdf_headers = ['ردیف', 'تاریخ', 'سکشن', 'کالا', 'سیستم', 'شمارش', 'مغایرت', 'ارزش اختلاف', 'توضیحات']
    pdf_rows = [
        [
            str(idx),
            r['audit_date_jalali'],
            r['department_name'],
            f"{r['asset_name']} ({r['asset_tag']})" if r['asset_tag'] else r['asset_name'],
            r['expected_text'],
            r['counted_text'],
            r['diff_label'],
            _fmt_money(r['variance_value']),
            (r['note'] or '-')[:40],
        ]
        for idx, r in enumerate(data['rows'], start=1)
    ]
    summary = [
        '',
        '',
        'جمع کل',
        '',
        '',
        '',
        f'{data["discrepancy_rows_count"]} مورد',
        _fmt_money(data['net_variance_val']),
        '',
    ]
    return _build_pdf_table_response(
        filename='asset_audit_variance.pdf',
        title=f'گزارش مغایرت انبارگردانی اموال ({cafe.name})',
        subtitle=f'بازه: {data["start_jalali"]} تا {data["end_jalali"]} | جمع خسارت: {_fmt_money(data["total_shortage_val"])} ریال',
        headers=pdf_headers,
        rows=pdf_rows,
        col_weights=[0.5, 1.1, 1.4, 2.2, 1.2, 1.2, 1.3, 1.3, 1.8],
        summary_row=summary,
    )


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_breakage(request):
    """گزارش ۳: روند شکستگی و مفقودی ماهانه (Chart.js + Top 10)."""
    cafe = _require_active_cafe(request)
    months_raw = (request.GET.get('months') or '6').strip()
    months_count = int(months_raw) if months_raw in ('3', '6', '12') else 6
    selected_department = (request.GET.get('department') or '').strip()

    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))

    today = timezone.localdate()
    j_today = jdatetime.date.fromgregorian(date=today)
    months_seq = _subtract_jalali_months(j_today.year, j_today.month, months_count)
    first_y, first_m = months_seq[0]
    since_dt = jdatetime.date(first_y, first_m, 1).togregorian()

    mv_qs = (
        AssetMovement.objects.filter(
            cafe=cafe,
            status='approved',
            created_at__date__gte=since_dt,
        )
        .filter(Q(movement_type='disposal') | Q(movement_type='audit_adjustment', quantity__lt=0))
        .select_related('asset', 'asset__category', 'from_department', 'to_department')
    )
    if selected_department.isdigit():
        dept_id = int(selected_department)
        mv_qs = mv_qs.filter(Q(from_department_id=dept_id) | Q(to_department_id=dept_id))

    month_dept_qty: dict[tuple[str, int, int], int] = defaultdict(int)
    month_total_qty: dict[tuple[int, int], int] = defaultdict(int)
    month_total_val: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal('0'))
    dept_names_seen: set[str] = set()
    asset_stats: dict[int, dict] = {}
    total_qty = 0
    total_val = Decimal('0')

    for mv in mv_qs:
        dt = timezone.localtime(mv.created_at).date() if timezone.is_aware(mv.created_at) else mv.created_at.date()
        jd = jdatetime.date.fromgregorian(date=dt)
        qty = abs(int(mv.quantity or 0))
        if qty == 0:
            continue
        unit_val = Decimal(mv.unit_value_snapshot or mv.asset.estimated_unit_value or 0)
        loss_val = Decimal(qty) * unit_val
        dept_name = (
            mv.from_department.name
            if mv.from_department_id
            else (mv.to_department.name if mv.to_department_id else 'نامشخص')
        )

        dept_names_seen.add(dept_name)
        month_dept_qty[(dept_name, jd.year, jd.month)] += qty
        month_total_qty[(jd.year, jd.month)] += qty
        month_total_val[(jd.year, jd.month)] += loss_val
        total_qty += qty
        total_val += loss_val

        a_entry = asset_stats.get(mv.asset_id)
        if a_entry is None:
            thumb_url = mv.asset.thumbnail.url if mv.asset.thumbnail else (mv.asset.image.url if mv.asset.image else '')
            a_entry = asset_stats[mv.asset_id] = {
                'asset': mv.asset,
                'thumb_url': thumb_url,
                'category_name': str(mv.asset.category) if mv.asset.category else 'بدون دسته',
                'total_qty': 0,
                'total_val': Decimal('0'),
                'disposal_count': 0,
                'audit_loss_count': 0,
            }
        a_entry['total_qty'] += qty
        a_entry['total_val'] += loss_val
        if mv.movement_type == 'disposal':
            a_entry['disposal_count'] += qty
        else:
            a_entry['audit_loss_count'] += qty

    top_assets = sorted(
        asset_stats.values(),
        key=lambda x: (x['total_qty'], x['total_val']),
        reverse=True,
    )[:10]

    labels = [f'{JALALI_MONTH_NAMES[m]} {y}' for y, m in months_seq]
    palette = ['#8B5CF6', '#EC4899', '#F97316', '#10B981', '#3B82F6', '#EF4444', '#14B8A6']
    datasets = []
    for idx, dept_name in enumerate(sorted(dept_names_seen)):
        datasets.append({
            'label': dept_name,
            'data': [month_dept_qty[(dept_name, y, m)] for y, m in months_seq],
            'backgroundColor': palette[idx % len(palette)],
            'borderColor': palette[idx % len(palette)],
        })

    chart_data = {
        'labels': labels,
        'datasets': datasets,
        'total_qty_series': [month_total_qty[(y, m)] for y, m in months_seq],
        'total_val_series': [float(month_total_val[(y, m)]) for y, m in months_seq],
    }

    return render(
        request,
        'assets/report_breakage_trend.html',
        {
            'departments': departments,
            'selected_department': selected_department,
            'months_count': months_count,
            'total_qty': total_qty,
            'total_val': total_val,
            'top_assets': top_assets,
            'chart_data_json': json.dumps(chart_data, ensure_ascii=False),
        },
    )


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_shortage(request):
    """گزارش ۴: اقلام دارای کسری نسبت به استاندارد سکشن (min_expected_quantity)."""
    cafe = _require_active_cafe(request)
    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))
    categories = list(AssetCategory.objects.filter(cafe=cafe, is_active=True).order_by('name'))

    selected_department = (request.GET.get('department') or '').strip()
    selected_category = (request.GET.get('category') or '').strip()

    stocks_qs = (
        DepartmentAssetStock.objects.filter(
            cafe=cafe,
            min_expected_quantity__gt=0,
            quantity_healthy__lt=F('min_expected_quantity'),
        )
        .select_related('department', 'asset', 'asset__category')
        .order_by('department__name', 'asset__name')
    )
    if selected_department.isdigit():
        stocks_qs = stocks_qs.filter(department_id=int(selected_department))
    if selected_category.isdigit():
        cat_id = int(selected_category)
        stocks_qs = stocks_qs.filter(
            Q(asset__category_id=cat_id) | Q(asset__category__parent_id=cat_id)
        )

    rows = []
    total_shortage_qty = 0
    total_estimated_cost = Decimal('0')
    dept_grouped: dict[int, dict] = {}

    for st in stocks_qs:
        shortage = st.shortage_quantity
        est_cost = Decimal(shortage) * Decimal(st.asset.estimated_unit_value or 0)
        total_shortage_qty += shortage
        total_estimated_cost += est_cost
        thumb_url = st.asset.thumbnail.url if st.asset.thumbnail else (st.asset.image.url if st.asset.image else '')

        row = {
            'stock': st,
            'department': st.department,
            'asset': st.asset,
            'thumb_url': thumb_url,
            'shortage_qty': shortage,
            'estimated_cost': est_cost,
        }
        rows.append(row)

        grp = dept_grouped.setdefault(
            st.department_id,
            {'department': st.department, 'shortage_qty': 0, 'items': []},
        )
        grp['shortage_qty'] += shortage
        grp['items'].append(row)

    replacement_request_url = _safe_reverse('asset_create_replacement_request', cafe.code)

    chart_data = {
        'labels': [r['asset'].name for r in rows[:12]],
        'healthy': [r['stock'].quantity_healthy for r in rows[:12]],
        'expected': [r['stock'].min_expected_quantity for r in rows[:12]],
    }

    return render(
        request,
        'assets/report_shortage.html',
        {
            'departments': departments,
            'categories': categories,
            'selected_department': selected_department,
            'selected_category': selected_category,
            'rows': rows,
            'dept_grouped': list(dept_grouped.values()),
            'total_shortage_qty': total_shortage_qty,
            'total_estimated_cost': total_estimated_cost,
            'replacement_request_url': replacement_request_url,
            'chart_data_json': json.dumps(chart_data, ensure_ascii=False),
        },
    )


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_repairs(request):
    """گزارش ۵: وضعیت تعمیرات و گارانتی تجهیزات پلاک‌دار."""
    cafe = _require_active_cafe(request)
    today = timezone.localdate()
    warranty_days = _parse_int_input(request.GET.get('days'), default=30)
    if warranty_days <= 0 or warranty_days > 365:
        warranty_days = 30
    horizon = today + timedelta(days=warranty_days)

    selected_department = (request.GET.get('department') or '').strip()
    active_tab = (request.GET.get('tab') or 'needs_repair').strip()
    if active_tab not in ('needs_repair', 'in_repair', 'expiring_warranty', 'expired_warranty'):
        active_tab = 'needs_repair'

    departments = list(Department.objects.filter(cafe=cafe, is_active=True).order_by('name'))

    base_qs = AssetUnit.objects.filter(cafe=cafe).select_related('asset', 'department', 'custodian')
    if selected_department.isdigit():
        base_qs = base_qs.filter(department_id=int(selected_department))

    needs_repair_qs = base_qs.filter(status='needs_repair')
    in_repair_qs = base_qs.filter(status='in_repair')
    expiring_qs = base_qs.filter(
        warranty_until__isnull=False,
        warranty_until__gte=today,
        warranty_until__lte=horizon,
    ).exclude(status__in=('lost', 'disposed'))
    expired_qs = base_qs.filter(
        warranty_until__isnull=False,
        warranty_until__lt=today,
    ).exclude(status__in=('lost', 'disposed'))

    counts = {
        'needs_repair': needs_repair_qs.count(),
        'in_repair': in_repair_qs.count(),
        'expiring_warranty': expiring_qs.count(),
        'expired_warranty': expired_qs.count(),
    }

    tab_map = {
        'needs_repair': needs_repair_qs.order_by('asset__name', 'asset_tag'),
        'in_repair': in_repair_qs.order_by('asset__name', 'asset_tag'),
        'expiring_warranty': expiring_qs.order_by('warranty_until', 'asset__name'),
        'expired_warranty': expired_qs.order_by('-warranty_until', 'asset__name'),
    }
    units = list(tab_map[active_tab])
    for u in units:
        if u.warranty_until:
            u.warranty_days_left = (u.warranty_until - today).days
        else:
            u.warranty_days_left = None

    # توزیع وضعیت کل پلاک‌ها برای نمودار Chart.js
    status_counts_raw = dict(
        base_qs.values_list('status').annotate(c=Count('id'))
    )
    status_labels = [label for _, label in AssetUnit.STATUS_CHOICES]
    status_values = [status_counts_raw.get(code, 0) for code, _ in AssetUnit.STATUS_CHOICES]

    return render(
        request,
        'assets/report_repairs_warranty.html',
        {
            'departments': departments,
            'selected_department': selected_department,
            'active_tab': active_tab,
            'warranty_days': warranty_days,
            'counts': counts,
            'units': units,
            'chart_data_json': json.dumps(
                {'labels': status_labels, 'values': status_values},
                ensure_ascii=False,
            ),
        },
    )


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_kardex(request):
    """گزارش ۶: کاردکس و تاریخچه کامل حرکات اموال."""
    cafe = _require_active_cafe(request)
    data = _build_kardex_data(request, cafe)

    paginator = Paginator(data['queryset'], 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(
        request,
        'assets/report_kardex.html',
        {
            **data,
            'page_obj': page_obj,
            'total_movements': paginator.count,
        },
    )


@custom_login_required
@role_required(ASSET_REPORT_ROLES)
def asset_report_kardex_export(request):
    """خروجی Excel کاردکس و تاریخچه حرکات اموال."""
    cafe = _require_active_cafe(request)
    data = _build_kardex_data(request, cafe)

    headers = [
        'ردیف',
        'تاریخ ثبت',
        'نوع گردش',
        'وضعیت',
        'نام کالا',
        'کد کالا',
        'پلاک',
        'از سکشن',
        'به سکشن',
        'تعداد',
        'وضعیت سلامت',
        'ارزش واحد (ریال)',
        'مرجع / انبارگردانی / فاکتور',
        'توضیحات',
        'ثبت‌کننده',
    ]
    rows = []
    for idx, mv in enumerate(data['queryset'][:5000], start=1):
        ref_parts = []
        if mv.audit_id:
            ref_parts.append(f'انبارگردانی: {mv.audit.title}')
        if mv.invoice_id:
            ref_parts.append(f'فاکتور #{mv.invoice_id}')
        rows.append([
            idx,
            _format_jalali_date(mv.created_at),
            mv.get_movement_type_display(),
            mv.get_status_display(),
            mv.asset.name,
            mv.asset.code,
            mv.asset_unit.asset_tag if mv.asset_unit_id else '-',
            mv.from_department.name if mv.from_department_id else '-',
            mv.to_department.name if mv.to_department_id else '-',
            mv.quantity,
            mv.get_condition_bucket_display(),
            float(mv.unit_value_snapshot or 0),
            ' | '.join(ref_parts) or '-',
            mv.reason or '-',
            mv.created_by.get_full_name() or mv.created_by.username if mv.created_by_id else '-',
        ])

    return _build_excel_response(
        filename='asset_kardex_report.xlsx',
        sheet_title='کاردکس اموال',
        headers=headers,
        rows=rows,
        numeric_cols={10, 12},
    )
