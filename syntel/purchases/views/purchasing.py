from __future__ import annotations

from ..models import PurchaseItem
from ..models import PurchaseRequest
from ..models import StockItem
from ..models import WarehouseRequest
from .helpers import _parse_decimal
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import get_cafe_url
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from .workflow_helpers import _handle_my_purchases_history
from .workflow_helpers import _handle_my_purchases_view
from datetime import datetime
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDate
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from urllib.parse import urlencode
import jdatetime
import os
import re


@custom_login_required
@role_required(["purchasing", "warehouse"])
def purchasing_requests_list(request: HttpRequest) -> HttpResponse:
    """List purchase/warehouse requests filtered by department role and status"""
    role = request.GET.get('role', '')
    status = request.GET.get('status', '')
    request_type = request.GET.get('type', 'purchase')  # 'purchase' or 'warehouse'

    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    department_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'store': 'انبار',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    role_q = None
    if role:
        dept_label = department_names.get(role, role)
        role_q = Q(user__role=role) | Q(user__role='requester', user__department__name=dept_label)

    if request_type == 'warehouse':
        requests = WarehouseRequest.objects.all().select_related('user', 'user__department').prefetch_related('items')
        if cafe:
            requests = requests.filter(cafe=cafe)
        if role_q is not None:
            requests = requests.filter(role_q)
        if status == 'pending':
            requests = requests.filter(status='pending')
        requests = requests.order_by('-created_at')
    else:
        requests = PurchaseRequest.objects.all().select_related('user', 'user__department').prefetch_related('items')
        if cafe:
            requests = requests.filter(cafe=cafe)
        if role_q is not None:
            requests = requests.filter(role_q)
        if status == 'pending':
            requests = requests.filter(status='pending')
        requests = requests.order_by('-created_at')

    department_name = department_names.get(role, 'همه بخش‌ها')

    return render(request, "purchasing_requests_list.html", {
        "requests": requests,
        "role": role,
        "status": status,
        "department_name": department_name,
        "cafe_code": cafe_code,
        "request_type": request_type,
    })


@custom_login_required
@role_required(["purchasing", "warehouse"])
def process_purchase_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """Process purchase request items - set quantities and supply status"""
    purchase_request = get_scoped_object_or_404(PurchaseRequest, request, id=request_id)
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'

    valid_supply_statuses = {choice[0] for choice in PurchaseItem.SUPPLY_STATUS_CHOICES}

    if request.method == "POST":
        with transaction.atomic():
            # Process each item
            for item in purchase_request.items.all():
                purchased_qty = request.POST.get(f'purchased_qty_{item.id}', '')
                supply_status = request.POST.get(f'supply_status_{item.id}', 'pending')
                if supply_status not in valid_supply_statuses:
                    supply_status = 'pending'

                if purchased_qty:
                    parsed_qty = _parse_decimal(purchased_qty)
                    if parsed_qty is not None and parsed_qty >= 0:
                        item.purchased_quantity = parsed_qty
                item.supply_status = supply_status
                item.save()

            # Update request status if all items are processed
            all_processed = all(
                item.supply_status != 'pending'
                for item in purchase_request.items.all()
            )
            if all_processed:
                purchase_request.status = 'purchased'
                purchase_request.processed_by = request.user
                purchase_request.processed_at = timezone.now()
                purchase_request.save()
                messages.success(request, 'درخواست با موفقیت پردازش شد')

        return redirect('purchasing_requests_list', cafe_code=cafe_code)

    # Get stock items for search
    stock_items = filter_by_cafe(
        StockItem.objects.select_related('product', 'unit', 'warehouse').filter(quantity__gt=0),
        request,
    )

    return render(request, "process_purchase_request.html", {
        "purchase_request": purchase_request,
        "stock_items": stock_items,
        "cafe_code": cafe_code,
    })


@custom_login_required
@role_required(['purchasing', 'warehouse'])
def purchasing_warehouse_requests_list(request: HttpRequest) -> HttpResponse:
    """لیست درخواست‌های انبار برای مسئول خرید/انبار"""
    cafe = get_cafe_from_request(request)
    cafe_code = cafe.code if cafe else 'local'
    role_filter = request.GET.get('role', '')
    status_filter = request.GET.get('status', '')

    department_names = {
        'kitchen': 'آشپزخانه', 'bar': 'بار', 'counter': 'کانتر',
        'store': 'انبار', 'cashier': 'صندوق', 'facilities': 'تاسیسات',
    }

    qs = WarehouseRequest.objects.select_related('user', 'user__department').prefetch_related('items').order_by('-created_at')
    if cafe:
        qs = qs.filter(cafe=cafe)
    if role_filter:
        dept_label = department_names.get(role_filter, role_filter)
        qs = qs.filter(
            Q(user__role=role_filter) | Q(user__role='requester', user__department__name=dept_label)
        )
    if status_filter == 'pending':
        qs = qs.filter(status='pending')

    department_name = department_names.get(role_filter, 'همه بخش‌ها')

    return render(request, 'purchasing_warehouse_requests_list.html', {
        'requests': qs,
        'role': role_filter,
        'status': status_filter,
        'department_name': department_name,
        'cafe_code': cafe_code,
    })


@custom_login_required
@role_required(["manager", "purchasing"])
def purchase_lists_by_date(request: HttpRequest) -> HttpResponse:
    """Show purchase lists grouped by date and department"""
    from django.db.models import Count, Q
    from django.db.models.functions import TruncDate, Coalesce
    
    cafe = get_cafe_from_request(request)
    # Get all purchased requests with items
    # Use processed_at if available, otherwise fallback to created_at
    purchased_requests = PurchaseRequest.objects.filter(
        status='purchased'
    )
    if cafe:
        purchased_requests = purchased_requests.filter(cafe=cafe)
    purchased_requests = purchased_requests.annotate(
        date=TruncDate(Coalesce('processed_at', 'created_at'))
    ).values('date', 'user__role', 'user__department__name').annotate(
        count=Count('id')
    ).order_by('-date', 'user__role', 'user__department__name')
    
    # Group by date and role
    purchase_days = {}
    role_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    dept_to_legacy_role = {v: k for k, v in role_names.items()}
    for req in purchased_requests:
        date_obj = req['date']
        if not date_obj:
            continue
        date_str = date_obj.strftime('%Y-%m-%d')  # Convert to string for URL
        role = req['user__role']
        dept_name = req.get('user__department__name')
        if role == 'requester' and dept_name:
            dept_key = dept_to_legacy_role.get(dept_name, dept_name)
            display_name = dept_name
        else:
            dept_key = role
            display_name = role_names.get(role, role)
        
        if date_str not in purchase_days:
            purchase_days[date_str] = {
                'date_obj': date_obj,  # Keep object for display
                'departments': {}
            }
        
        existing = purchase_days[date_str]['departments'].get(dept_key)
        if existing:
            existing['count'] += req['count']
        else:
            purchase_days[date_str]['departments'][dept_key] = {
                'name': display_name,
                'count': req['count']
            }
    
    return render(request, "purchase_lists_by_date.html", {
        "purchase_days": purchase_days,
    })


@custom_login_required
@role_required(["manager", "purchasing"])
def purchase_day_detail(request: HttpRequest) -> HttpResponse:
    """Show detailed purchase list for a specific date and department"""
    from datetime import datetime

    date_str = request.GET.get('date')
    role = request.GET.get('role')

    if not date_str or not role:
        return get_cafe_redirect_url('purchase_lists_by_date', request)
    
    # Parse date safely
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        messages.error(request, 'فرمت تاریخ نامعتبر است.')
        return get_cafe_redirect_url('purchase_lists_by_date', request)

    role_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    dept_label = role_names.get(role, role)

    cafe = get_cafe_from_request(request)
    # Get all purchased requests for this date and role (fail closed if no cafe)
    if cafe is None:
        requests = PurchaseRequest.objects.none()
    else:
        requests = PurchaseRequest.objects.filter(
            cafe=cafe,
            status='purchased',
            processed_at__date=date_obj,
        ).filter(
            Q(user__role=role) | Q(user__role='requester', user__department__name=dept_label)
        ).prefetch_related('items', 'user', 'processed_by')

    # Collect all items
    all_items = []
    for req in requests:
        for item in req.items.all():
            # Calculate supplied quantity based on status
            if item.supply_status == 'purchased':
                supplied_quantity = item.purchased_quantity or 0
            elif item.supply_status == 'from_stock':
                supplied_quantity = item.purchased_quantity if item.purchased_quantity is not None else item.quantity
            elif item.supply_status == 'not_supplied':
                supplied_quantity = 0
            else:  # pending
                supplied_quantity = 0
            
            new_difference = item.quantity - supplied_quantity
            
            all_items.append({
                'request': req,
                'item': item,
                'supplied_quantity': supplied_quantity,
                'new_difference': new_difference,
                'difference': (item.quantity - (item.purchased_quantity or 0)),  # Keep old for compatibility
                'display_difference': abs(new_difference)  # For display purposes
            })
    
    role_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    
    return render(request, "purchase_day_detail.html", {
        "date": date_obj,
        "date_str": date_str,
        "role": role,
        "role_name": role_names.get(role, role),
        "requests": requests,
        "all_items": all_items,
    })


@custom_login_required
@role_required(["manager", "purchasing"])
def export_purchase_day_pdf(request: HttpRequest) -> HttpResponse:
    """Export purchase day detail as PDF - Persian RTL minimal design"""
    from datetime import datetime
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import jdatetime
    import os
    import arabic_reshaper
    from bidi.algorithm import get_display

    date_str = request.GET.get('date')
    role = request.GET.get('role')

    if not date_str or not role:
        return get_cafe_redirect_url('purchase_lists_by_date', request)
    
    # Parse date safely
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        messages.error(request, 'فرمت تاریخ نامعتبر است.')
        return get_cafe_redirect_url('purchase_lists_by_date', request)
    jalali_date = jdatetime.date.fromgregorian(date=date_obj)
    jalali_months = {
        1: 'فروردین', 2: 'اردیبهشت', 3: 'خرداد',
        4: 'تیر', 5: 'مرداد', 6: 'شهریور',
        7: 'مهر', 8: 'آبان', 9: 'آذر',
        10: 'دی', 11: 'بهمن', 12: 'اسفند'
    }
    jalali_str = f"{jalali_date.day} {jalali_months[jalali_date.month]} {jalali_date.year}"
    
    role_names = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }
    role_name = role_names.get(role, role)

    cafe = get_cafe_from_request(request)
    # Get all purchased requests for this date and role (fail closed if no cafe)
    if cafe is None:
        requests_qs = PurchaseRequest.objects.none()
    else:
        requests_qs = PurchaseRequest.objects.filter(
            cafe=cafe,
            status='purchased',
            processed_at__date=date_obj,
        ).filter(
            Q(user__role=role) | Q(user__role='requester', user__department__name=role_name)
        ).prefetch_related('items', 'user', 'processed_by')
    
    # Collect all items
    all_items = []
    for req in requests_qs:
        for item in req.items.all():
            # Calculate supplied quantity based on status
            if item.supply_status == 'purchased':
                supplied_quantity = item.purchased_quantity or 0
            elif item.supply_status == 'from_stock':
                supplied_quantity = item.purchased_quantity if item.purchased_quantity is not None else item.quantity
            elif item.supply_status == 'not_supplied':
                supplied_quantity = 0
            else:  # pending
                supplied_quantity = 0
            
            all_items.append(item)
    
    # Create PDF
    buffer = BytesIO()
    page_width, page_height = A4
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # Register Persian font (Vazir)
    font_name = 'Helvetica'
    try:
        from django.conf import settings
        font_path = os.path.join(settings.BASE_DIR, 'frontend', 'static', 'fonts', 'Vazir.ttf')
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('Vazir', font_path))
            font_name = 'Vazir'
    except:
        pass
    
    # Helper function to reshape Persian text for proper display
    def persian(text):
        """Reshape Persian text for correct display in PDF"""
        if not text:
            return ''
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)
    
    # Page margins
    margin = 2 * cm
    content_width = page_width - 2 * margin
    
    # Header
    y = page_height - margin
    
    # Title - centered
    c.setFont(font_name, 18)
    title = persian(f"لیست خرید {role_name}")
    title_width = c.stringWidth(title, font_name, 18)
    c.drawString((page_width - title_width) / 2, y, title)
    
    # Date - centered
    y -= 30
    c.setFont(font_name, 12)
    date_text = persian(jalali_str)
    date_width = c.stringWidth(date_text, font_name, 12)
    c.drawString((page_width - date_width) / 2, y, date_text)
    
    # Separator line
    y -= 20
    c.setStrokeColor(colors.Color(0.85, 0.85, 0.85))
    c.setLineWidth(0.5)
    c.line(margin, y, page_width - margin, y)
    
    # Table setup - columns from LEFT to RIGHT (RTL: rightmost is name, leftmost is diff)
    y -= 35
    col_widths = [2.5*cm, 3*cm, 3.5*cm, 3.5*cm, 4.5*cm]
    headers = ['تفاوت', 'وضعیت', 'تامین شده', 'درخواستی', 'نام کالا']
    
    # Header background
    c.setFillColor(colors.Color(0.93, 0.93, 0.93))
    c.rect(margin, y - 8, content_width, 28, fill=1, stroke=0)
    
    # Draw headers from LEFT to RIGHT
    c.setFillColor(colors.Color(0.2, 0.2, 0.2))
    c.setFont(font_name, 11)
    x = margin
    for i, header in enumerate(headers):
        header_text = persian(header)
        text_width = c.stringWidth(header_text, font_name, 11)
        col_center = x + col_widths[i] / 2
        c.drawString(col_center - text_width / 2, y + 5, header_text)
        x += col_widths[i]
    
    # Draw rows
    y -= 28
    status_map = {
        'purchased': 'خرید شده',
        'from_stock': 'از انبار',
        'not_supplied': 'تامین نشده',
        'pending': 'در انتظار'
    }
    
    row_height = 24
    
    for idx, item in enumerate(all_items):
        # Alternate row background
        if idx % 2 == 1:
            c.setFillColor(colors.Color(0.97, 0.97, 0.97))
            c.rect(margin, y - 6, content_width, row_height, fill=1, stroke=0)
        
        c.setFillColor(colors.Color(0.15, 0.15, 0.15))
        c.setFont(font_name, 10)
        
        # Calculate supplied quantity
        if item.supply_status == 'purchased':
            supplied_quantity = item.purchased_quantity or 0
        elif item.supply_status == 'from_stock':
            supplied_quantity = item.purchased_quantity if item.purchased_quantity is not None else item.quantity
        elif item.supply_status == 'not_supplied':
            supplied_quantity = 0
        else:  # pending
            supplied_quantity = 0
        
        difference = item.quantity - supplied_quantity
        if item.supply_status == 'not_supplied':
            diff_text = '-'
        elif difference == 0:
            diff_text = '0'
        elif difference > 0:
            diff_text = f'{difference}-'
        else:
            diff_text = f'{abs(difference)}+'
        
        # Row data from LEFT to RIGHT (RTL: reversed so name appears on right)
        row_data = [
            diff_text,
            status_map.get(item.supply_status, item.supply_status),
            f"{supplied_quantity} {item.unit}",
            f"{item.quantity} {item.unit}",
            item.name
        ]
        
        x = margin
        for i, cell in enumerate(row_data):
            cell_text = persian(str(cell))
            text_width = c.stringWidth(cell_text, font_name, 10)
            col_center = x + col_widths[i] / 2
            c.drawString(col_center - text_width / 2, y + 4, cell_text)
            x += col_widths[i]
        
        y -= row_height
        
        # Check if we need a new page
        if y < margin + 5*cm:
            c.showPage()
            c.setFont(font_name, 10)
            y = page_height - margin - 30
    
    # Separator before signatures
    y -= 30
    c.setStrokeColor(colors.Color(0.85, 0.85, 0.85))
    c.line(margin, y, page_width - margin, y)
    
    # Signature section - Two signatures side by side
    y -= 50
    c.setFont(font_name, 11)
    c.setFillColor(colors.Color(0.3, 0.3, 0.3))
    c.setStrokeColor(colors.Color(0.7, 0.7, 0.7))
    
    # Right side: امضا مسئول خرید
    sig1_text = persian('امضا مسئول خرید:')
    sig1_width = c.stringWidth(sig1_text, font_name, 11)
    right_edge = page_width - margin
    c.drawString(right_edge - sig1_width, y, sig1_text)
    c.line(right_edge - sig1_width - 4.5*cm, y - 3, right_edge - sig1_width - 0.3*cm, y - 3)
    
    # Left side: امضا مسئول بخش
    sig2_text = persian('امضا مسئول بخش:')
    sig2_width = c.stringWidth(sig2_text, font_name, 11)
    c.drawString(margin + 4.5*cm + 0.3*cm, y, sig2_text)
    c.line(margin, y - 3, margin + 4.5*cm, y - 3)
    
    c.save()
    
    # Get PDF content
    pdf = buffer.getvalue()
    buffer.close()
    
    safe_date = re.sub(r'[^A-Za-z0-9_-]', '_', str(date_str or ''))[:20]
    safe_role = re.sub(r'[^A-Za-z0-9_-]', '_', str(role or ''))[:30]
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="purchase_list_{safe_date}_{safe_role}.pdf"'
    return response


@custom_login_required
@role_required(["manager"])
def purchase_list_edit_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """Edit a purchased request - allows manager to modify items"""
    from urllib.parse import urlencode

    purchase_request = get_scoped_object_or_404(PurchaseRequest, request, id=request_id)
    valid_supply_statuses = {choice[0] for choice in PurchaseItem.SUPPLY_STATUS_CHOICES}

    if request.method == 'POST':
        with transaction.atomic():
            for item in purchase_request.items.all():
                purchased_qty_key = f'purchased_quantity_{item.id}'
                supply_status_key = f'supply_status_{item.id}'

                if purchased_qty_key in request.POST:
                    parsed_qty = _parse_decimal(request.POST.get(purchased_qty_key, ''))
                    item.purchased_quantity = parsed_qty if (parsed_qty is not None and parsed_qty >= 0) else 0

                if supply_status_key in request.POST:
                    status_val = request.POST.get(supply_status_key, 'pending')
                    if status_val in valid_supply_statuses:
                        item.supply_status = status_val

                item.save()

        date_str = request.POST.get('date', '')
        role = request.POST.get('role', '')

        if date_str and role:
            return redirect(
                f"{get_cafe_url('purchase_day_detail', request)}?{urlencode({'date': date_str, 'role': role})}"
            )
        return get_cafe_redirect_url('purchase_lists_by_date', request)

    date_str = request.GET.get('date', '')
    role = request.GET.get('role', '')

    return render(request, "purchase_list_edit_request.html", {
        "purchase_request": purchase_request,
        "date_str": date_str,
        "role": role,
    })


@custom_login_required
@role_required(["manager"])
def purchase_list_confirm_request(request: HttpRequest, request_id: int) -> HttpResponse:
    """Confirm a purchased request by manager"""
    from urllib.parse import urlencode

    purchase_request = get_scoped_object_or_404(PurchaseRequest, request, id=request_id)

    if request.method == 'POST':
        if purchase_request.manager_confirmed:
            purchase_request.manager_confirmed = False
            purchase_request.manager_confirmed_at = None
            purchase_request.manager_confirmed_by = None
        else:
            purchase_request.manager_confirmed = True
            purchase_request.manager_confirmed_at = timezone.now()
            purchase_request.manager_confirmed_by = request.user

        purchase_request.save()

        date_str = request.POST.get('date', '')
        role = request.POST.get('role', '')

        if date_str and role:
            return redirect(
                f"{get_cafe_url('purchase_day_detail', request)}?{urlencode({'date': date_str, 'role': role})}"
            )
        return get_cafe_redirect_url('purchase_lists_by_date', request)

    return get_cafe_redirect_url('purchase_lists_by_date', request)


@custom_login_required
@role_required(['purchasing', 'warehouse', 'manager', 'ceo'])
def purchase_officer_dashboard(request: HttpRequest) -> HttpResponse:
    """لیست خرید مسئول خرید: اقلام آماده خرید برای مسئول خرید یا مدیرعامل (خرید توسط خودم)."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    # نقش «خرید توسط خودم»: مدیرعامل/مدیرِ بدون نقش خرید، خریدِ خودش را انجام می‌دهد
    is_self_buy = (
        request.user.role in ('manager', 'ceo')
        and not request.user.has_purchasing_role
        and not request.user.has_warehouse_role
    )

    if not (request.user.role == 'admin' or request.user.is_superuser
            or request.user.has_purchasing_role or is_self_buy):
        return redirect('warehouse_officer_dashboard', cafe_code=cafe_code)

    return _handle_my_purchases_view(
        request,
        is_self_buy=is_self_buy,
        is_warehouse_self_buy=False,
        redirect_url_name='purchase_officer_dashboard',
        history_url_name='purchase_officer_history',
    )


@custom_login_required
@role_required(['purchasing', 'warehouse', 'manager', 'ceo'])
def purchase_officer_history(request: HttpRequest) -> HttpResponse:
    """سوابق لیست خرید مسئول خرید (یا خودخریدی مدیرعامل)."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    cafe_code = cafe.code

    is_self_buy = (
        request.user.role in ('manager', 'ceo')
        and not request.user.has_purchasing_role
        and not request.user.has_warehouse_role
    )

    if not (request.user.role == 'admin' or request.user.is_superuser
            or request.user.has_purchasing_role or is_self_buy):
        return redirect('warehouse_officer_dashboard', cafe_code=cafe_code)

    return _handle_my_purchases_history(
        request,
        is_self_buy=is_self_buy,
        is_warehouse_self_buy=False,
        list_url_name='purchase_officer_dashboard',
        history_url_name='purchase_officer_history',
    )
