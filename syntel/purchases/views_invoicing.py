"""
Invoicing, Payments, Payment Methods/Locations/Types, and Supplier Views.
تفکیک‌شده از purchases/views.py برای بهبود ساختار معماری و نگهداری‌پذیری.
"""
import os
import json
import logging
from decimal import Decimal
from datetime import datetime, date

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
import jdatetime

from .middleware import get_cafe_code
from .models import (
    ActivityLog,
    Cafe,
    Category,
    Debt,
    Department,
    Invoice,
    InvoiceItem,
    InvoiceIssue,
    Notification,
    Payment,
    PaymentAccount,
    PaymentLocation,
    PaymentType,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequest,
    Requester,
    Supplier,
    SupplierEvaluation,
    User,
)
from .views_helpers import (
    _fmt2,
    _parse_decimal,
    cafe_view,
    custom_login_required,
    filter_by_cafe,
    get_cafe_from_request,
    get_cafe_redirect_url,
    get_scoped_locked_object_or_404,
    get_scoped_object_or_404,
    role_required,
)

logger = logging.getLogger(__name__)

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


def _scoped_payment_or_404(request, payment_id):
    """بازیابی پرداخت در محدودهٔ کافهٔ فعال (شکست بسته بدون کافه)."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    qs = Payment.objects.filter(
        Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe)
    ).distinct()
    return get_object_or_404(qs, id=payment_id)


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
    """ثبت پرداخت تفکیک‌شده به‌ازای هر فاکتور با مبلغ تخصیص‌یافتهٔ واقعی (بدون دوباره‌حساب‌کردن مبلغ کل)."""
    remaining_pool = total_amount
    created_payments = []
    allocated_invoices = []

    for idx, inv in enumerate(invoices_list):
        if remaining_pool <= 0:
            break
        inv_remaining = Decimal(str(inv.remaining_amount or 0))
        if idx == len(invoices_list) - 1:
            alloc = remaining_pool
        else:
            if inv_remaining <= 0:
                continue
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

    # اگر همه فاکتورها قبلاً تسویه بودند ولی پرداختی ثبت شد، کل مبلغ روی اولین فاکتور ثبت می‌شود
    if not created_payments and invoices_list and total_amount > 0:
        inv = invoices_list[0]
        payment = Payment.objects.create(
            invoice=inv,
            amount=total_amount,
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

    return created_payments, allocated_invoices, remaining_pool

@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_list(request):
    cafe = get_cafe_from_request(request)
    # انواع پرداختِ سراسری (cafe=NULL) + انواع همین کافه
    payment_types = PaymentType.objects.none() if cafe is None else \
        PaymentType.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
    return render(request, "payment_type_list.html", {"payment_types": payment_types})


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            PaymentType.objects.create(name=name, cafe=get_cafe_from_request(request))
            return get_cafe_redirect_url("payment_type_list", request)
    return render(request, "payment_type_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_update(request, payment_type_id):
    cafe = get_cafe_from_request(request)
    # انواع پرداختِ سراسری مشترکاند و از این صفحه ویرایش نمیشوند؛ فقط انواع کافه جاری
    payment_type = get_object_or_404(PaymentType, cafe=cafe, id=payment_type_id)
    if request.method == "POST":
        payment_type.name = request.POST.get("name", payment_type.name)
        payment_type.save()
        return get_cafe_redirect_url("payment_type_list", request)
    return render(request, "payment_type_form.html", {"payment_type": payment_type})


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_type_delete(request, payment_type_id):
    cafe = get_cafe_from_request(request)
    payment_type = get_object_or_404(PaymentType, cafe=cafe, id=payment_type_id)
    if request.method == "POST":
        payment_type.delete()
        return get_cafe_redirect_url("payment_type_list", request)
    return render(request, "payment_type_confirm_delete.html", {"payment_type": payment_type})




# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoices_list(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return render(request, "invoice_list.html", {
            'invoices': [],
            'page_obj': None,
            'issues': InvoiceIssue.objects.none(),
            'status_choices': Invoice.STATUS_CHOICES,
            'payment_status_choices': Invoice.PAYMENT_STATUS_CHOICES,
        })

    # Ensure default issues exist for this cafe
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue, cafe=cafe)

    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    selected_status = request.GET.get('status', '')
    selected_payment_status = request.GET.get('payment_status', '')
    date_search_type = request.GET.get('date_search_type', '')
    date_query = request.GET.get('date_query', '').strip()
    invoice_type = request.GET.get('type', 'store')  # Default to 'store' for store invoices

    # Handle single issue selection
    selected_issue = request.GET.get('issue', '').strip()
    sort = request.GET.get('sort', '')

    # Base queryset (fail-closed)
    invoices = Invoice.objects.select_related("purchase_order", "supplier").filter(cafe=cafe)
    
    # Filter by type
    if invoice_type == 'store':
        invoices = invoices.filter(supplier__isnull=True).exclude(store_name='')
    
    # Apply filters
    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(supplier__name__icontains=search_query) |
            Q(purchase_order__order_number__icontains=search_query)
        )
    
    if selected_status:
        invoices = invoices.filter(status=selected_status)
    
    if selected_payment_status:
        invoices = invoices.filter(payment_status=selected_payment_status)
    
    if selected_issue:
        try:
            issue_id = int(selected_issue)
            invoices = invoices.filter(issues__id=issue_id).distinct()
        except ValueError:
            pass
    
    # Apply date filter
    if date_query and date_search_type:
        try:
            search_date = datetime.fromisoformat(date_query).date()
            if date_search_type == 'invoice_date':
                invoices = invoices.filter(invoice_date=search_date)
            elif date_search_type == 'request_date':
                invoices = invoices.filter(due_date=search_date)
        except ValueError:
            pass  # Invalid date format, ignore filter
    
    # Ordering
    if sort == 'amount_asc':
        invoices = invoices.order_by('total_amount')
    elif sort == 'amount_desc':
        invoices = invoices.order_by('-total_amount')
    else:
        invoices = invoices.order_by('-created_at')
    
    # Pagination
    paginator = Paginator(invoices, 25)  # 25 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Status choices for filter dropdown
    status_choices = Invoice.STATUS_CHOICES
    payment_status_choices = Invoice.PAYMENT_STATUS_CHOICES
    issues = InvoiceIssue.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
    
    is_manager = request.user.role == 'manager'

    context = {
        'invoices': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'selected_status': selected_status,
        'selected_payment_status': selected_payment_status,
        'date_search_type': date_search_type,
        'date_query': date_query,
        'status_choices': status_choices,
        'payment_status_choices': payment_status_choices,
        'is_manager': is_manager,
        'invoice_type': invoice_type,
        'issues': issues,
        'selected_issue': selected_issue,
        'selected_issues_str': selected_issue,
        'sort': sort,
    }
    
    response = render(request, "invoice_list.html", context)
    # جلوگیری از cache مرورگر برای نمایش داده‌های به‌روز
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@custom_login_required
@role_required(["manager", "purchasing", "finance_manager"])
def all_invoices_list(request: HttpRequest) -> HttpResponse:
    """Show all invoices in a table format with filtering capabilities"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return render(request, "all_invoices_list.html", {
            'invoices': [],
            'page_obj': None,
            'issues': InvoiceIssue.objects.none(),
            'status_choices': Invoice.STATUS_CHOICES,
            'payment_status_choices': Invoice.PAYMENT_STATUS_CHOICES,
        })

    # Ensure default issues exist for this cafe
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue, cafe=cafe)

    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    selected_status = request.GET.get('status', '')
    selected_payment_status = request.GET.get('payment_status', '')
    invoice_date_filter = request.GET.get('invoice_date', '').strip()
    due_date_filter = request.GET.get('due_date', '').strip()

    # Handle single issue selection
    selected_issue = request.GET.get('issue', '').strip()
    has_issues = request.GET.get('has_issues', '').strip()

    # Base queryset - ALL invoices (fail-closed)
    invoices = Invoice.objects.select_related("purchase_order", "supplier").filter(cafe=cafe)

    # Apply filters
    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(supplier__name__icontains=search_query) |
            Q(store_name__icontains=search_query) |
            Q(purchase_order__order_number__icontains=search_query)
        )

    if selected_status:
        invoices = invoices.filter(status=selected_status)

    if selected_payment_status:
        invoices = invoices.filter(payment_status=selected_payment_status)

    if selected_issue:
        try:
            issue_id = int(selected_issue)
            invoices = invoices.filter(issues__id=issue_id).distinct()
        except ValueError:
            pass

    if has_issues == 'yes':
        invoices = invoices.filter(issues__isnull=False).distinct()

    # Apply date filters
    if invoice_date_filter:
        try:
            invoices = invoices.filter(invoice_date=datetime.fromisoformat(invoice_date_filter).date())
        except ValueError:
            pass

    if due_date_filter:
        try:
            invoices = invoices.filter(due_date=datetime.fromisoformat(due_date_filter).date())
        except ValueError:
            pass

    # Sort
    sort = request.GET.get('sort', '')
    if sort == 'amount_asc':
        invoices = invoices.order_by('total_amount')
    elif sort == 'amount_desc':
        invoices = invoices.order_by('-total_amount')
    else:
        invoices = invoices.order_by('-invoice_date', '-created_at')

    # Pagination
    paginator = Paginator(invoices, 25)  # 25 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Status choices for filter dropdown
    status_choices = Invoice.STATUS_CHOICES
    payment_status_choices = Invoice.PAYMENT_STATUS_CHOICES
    issues = InvoiceIssue.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True))

    is_manager = request.user.role == 'manager'

    context = {
        'invoices': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'selected_status': selected_status,
        'selected_payment_status': selected_payment_status,
        'invoice_date_filter': invoice_date_filter,
        'due_date_filter': due_date_filter,
        'status_choices': status_choices,
        'payment_status_choices': payment_status_choices,
        'is_manager': is_manager,
        'invoice_type': 'all',  # Indicate this is the all invoices view
        'issues': issues,
        'selected_issue': selected_issue,
        'selected_issues_str': selected_issue,
        'has_issues': has_issues,
        'sort': sort,
        'page_title': 'لیست تمامی فاکتورها',
    }

    return render(request, "all_invoices_list.html", context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_register(request):
    """ایجاد فاکتور دستی بدون نیاز به سفارش"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')

    if request.method == "POST":
        # پردازش تاریخ‌ها (پشتیبانی از شمسی و میلادی)
        invoice_date = _parse_invoice_date(request.POST.get("invoice_date")) or timezone.localdate()
        request_date = _parse_invoice_date(request.POST.get("request_date")) or timezone.localdate()

        # دریافت تأمین‌کننده (موجود در کافه یا نام فروشگاه جدید)
        supplier_id = (request.POST.get("supplier") or "").strip()
        store_name = request.POST.get("supplier_name", "").strip()
        supplier = None
        items_data = []

        if supplier_id and supplier_id.isdigit():
            supplier = Supplier.objects.filter(id=int(supplier_id), cafe=cafe).first()

        # اگر تامین‌کننده پیدا نشد، خطا بده
        if not supplier and not store_name:
            messages.error(request, "لطفاً تأمین‌کننده یا نام فروشگاه را وارد کنید.")
            context = {
                'categories': Category.objects.filter(is_active=True, cafe=cafe),
                'manual_invoice': True,
                'form_data': request.POST,
                'items_data': items_data,
            }
            return render(request, "invoice_register.html", context)

        # تابع برای پردازش اعداد با کاما
        def parse_money(value):
            if value is None or str(value).strip() == '':
                return Decimal('0')
            cleaned = str(value).replace(',', '')
            parsed = _parse_decimal(cleaned)
            return parsed if parsed is not None else Decimal('0')

        # محاسبه مبالغ از اقلام فاکتور
        subtotal = Decimal('0')
        total_tax = Decimal('0')
        total_discount = Decimal('0')
        i = 0
        while f"description_{i}" in request.POST:
            description = request.POST.get(f"description_{i}", "")
            quantity_parsed = _parse_decimal(request.POST.get(f"quantity_{i}", 0))
            quantity = quantity_parsed if quantity_parsed is not None else Decimal('0')
            unit_price = parse_money(request.POST.get(f"unit_price_{i}", 0))
            tax_amount = parse_money(request.POST.get(f"tax_amount_{i}", 0))
            discount_amount = parse_money(request.POST.get(f"discount_amount_{i}", 0))
            requester_id = (request.POST.get(f"requester_{i}") or "").strip()
            requester = None
            if requester_id.isdigit():
                requester = Requester.objects.filter(id=int(requester_id), cafe=cafe).first()

            base_total = quantity * unit_price
            line_total = base_total + tax_amount - discount_amount

            subtotal += base_total
            total_tax += tax_amount
            total_discount += discount_amount

            if description:
                items_data.append({
                    'description': description,
                    'quantity': quantity,
                    'unit_price': unit_price,
                    'tax_amount': tax_amount,
                    'discount_amount': discount_amount,
                    'requester': requester,
                    'total': line_total
                })
            i += 1

        # هزینه حمل و نقل
        shipping_cost = parse_money(request.POST.get("shipping_cost", 0))

        # تخفیف نقدی کل فاکتور
        total_cash_discount = parse_money(request.POST.get("total_cash_discount", 0))

        # محاسبه مبلغ کل (جمع اقلام + مالیات - تخفیف اقلام + حمل - تخفیف نقدی کل)
        items_total = subtotal + total_tax - total_discount
        total_amount = items_total + shipping_cost - total_cash_discount

        # بررسی شماره فاکتور
        invoice_number = request.POST.get("invoice_number")
        if not invoice_number:
            invoice_number = f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        with transaction.atomic():
            invoice = Invoice.objects.create(
                purchase_order=None,
                supplier=supplier,
                store_name=store_name,
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                due_date=request_date,
                shipping_cost=shipping_cost,
                subtotal=subtotal,
                tax_amount=total_tax,
                discount_amount=total_discount,
                cash_discount=total_cash_discount,
                total_amount=total_amount,
                notes=request.POST.get("notes", ""),
                created_by=request.user,
                updated_by=request.user,
                cafe=cafe,
            )

            for item in items_data:
                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=item['description'],
                    quantity=item['quantity'],
                    unit_price=item['unit_price'],
                    tax_amount=item['tax_amount'],
                    discount_amount=item['discount_amount'],
                    requester=item['requester'],
                    line_total=item['total']
                )

            issues_ids = [
                int(iid.strip())
                for iid in request.POST.get('issues', '').split(',')
                if iid.strip().isdigit()
            ]
            if issues_ids:
                issues = InvoiceIssue.objects.filter(
                    id__in=issues_ids
                ).filter(Q(cafe=cafe) | Q(cafe__isnull=True))
                invoice.issues.set(issues)

            if request.POST.get('add_payment'):
                payment_amount = parse_money(request.POST.get('payment_amount', 0))
                if not payment_amount or payment_amount <= 0:
                    payment_amount = total_amount

                payment_location_id = (request.POST.get('payment_location_id') or '').strip()
                payment_date_str = request.POST.get('payment_date')
                payment_notes = request.POST.get('payment_notes', '')

                if payment_location_id.isdigit():
                    payment_location = PaymentLocation.objects.filter(
                        id=int(payment_location_id), cafe=cafe
                    ).first()
                    if payment_location is not None:
                        payment_date = _parse_invoice_date(payment_date_str) or timezone.localdate()
                        Payment.objects.create(
                            invoice=invoice,
                            amount=payment_amount,
                            payment_location=payment_location,
                            payment_date=payment_date,
                            notes=payment_notes,
                            created_by=request.user,
                            payment_status='completed',
                        )
                        total_paid = invoice.payment_records().filter(
                            payment_status='completed'
                        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
                        if total_paid >= invoice.total_amount:
                            invoice.payment_status = 'paid'
                        elif total_paid > 0:
                            invoice.payment_status = 'partial'
                        else:
                            invoice.payment_status = 'unpaid'
                        Invoice.objects.filter(pk=invoice.pk).update(
                            payment_status=invoice.payment_status
                        )
                        messages.success(request, f"پرداخت به مبلغ {payment_amount:,} ریال ثبت شد.")
                    else:
                        messages.warning(request, "محل پرداخت معتبر نیست.")

        messages.success(request, f"فاکتور {invoice.invoice_number} با موفقیت ایجاد شد.")
        return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)

    # نمایش فرم ایجاد فاکتور دستی
    categories = Category.objects.filter(is_active=True, cafe=cafe).select_related('parent').order_by('parent__name', 'name')

    default_supplier = None
    supplier_id = (request.GET.get('supplier_id') or '').strip()
    if supplier_id.isdigit():
        default_supplier = Supplier.objects.filter(id=int(supplier_id), cafe=cafe, status='active').first()

    context = {
        'categories': categories,
        'requesters': Requester.objects.filter(cafe=cafe),
        'issues': InvoiceIssue.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True)),
        'payment_types': PaymentType.objects.filter(is_active=True).filter(Q(cafe=cafe) | Q(cafe__isnull=True)),
        'payment_locations': PaymentLocation.objects.filter(is_active=True, cafe=cafe),
        'today': timezone.localdate(),
        'manual_invoice': True,
        'default_supplier': default_supplier,
    }
    return render(request, "invoice_register.html", context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_create(request, order_id):
    cafe = get_cafe_from_request(request)
    order = get_scoped_object_or_404(PurchaseOrder, request, id=order_id)
    if request.method == "POST":
        due_date = _parse_invoice_date(request.POST.get("due_date")) or timezone.localdate()

        invoice_number = request.POST.get("invoice_number")
        if not invoice_number:
            invoice_number = f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        total_parsed = _parse_decimal(request.POST.get("total_amount") or 0)
        total_amount = total_parsed if (total_parsed is not None and total_parsed >= 0) else Decimal('0')

        invoice = Invoice.objects.create(
            cafe=cafe,
            purchase_order=order,
            supplier=order.supplier,
            invoice_number=invoice_number,
            total_amount=total_amount,
            due_date=due_date,
            notes=request.POST.get("notes", ""),
            created_by=request.user,
        )
        return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)

    return render(request, "invoice_form.html", {"order": order})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_detail(request, invoice_id):
    invoice = get_scoped_object_or_404(Invoice, request, id=invoice_id)
    items = invoice.invoice_items.all()
    response = render(request, "invoice_detail.html", {
        "invoice": invoice,
        "items": items
    })
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_update(request, invoice_id):
    cafe = get_cafe_from_request(request)
    invoice = get_scoped_object_or_404(Invoice, request, id=invoice_id)
    order = invoice.purchase_order
    categories = Category.objects.filter(cafe=cafe).order_by('name')

    if request.method == "POST":
        with transaction.atomic():
            invoice = get_scoped_locked_object_or_404(Invoice, request, id=invoice_id)
            invoice_number = request.POST.get("invoice_number")
            if invoice_number:
                invoice.invoice_number = invoice_number

            parsed_inv_date = _parse_invoice_date(request.POST.get("invoice_date"))
            if parsed_inv_date is not None:
                invoice.invoice_date = parsed_inv_date

            parsed_due_date = _parse_invoice_date(request.POST.get("due_date"))
            if parsed_due_date is not None:
                invoice.due_date = parsed_due_date

            tax_rate_parsed = _parse_decimal(request.POST.get("tax_rate"))
            if tax_rate_parsed is not None and tax_rate_parsed >= 0:
                invoice.tax_rate = tax_rate_parsed
            discount_rate_parsed = _parse_decimal(request.POST.get("discount_rate"))
            if discount_rate_parsed is not None and discount_rate_parsed >= 0:
                invoice.discount_rate = discount_rate_parsed

            shipping_cost_str = request.POST.get("shipping_cost", "0").replace(",", "")
            shipping_parsed = _parse_decimal(shipping_cost_str)
            if shipping_parsed is not None and shipping_parsed >= 0:
                invoice.shipping_cost = shipping_parsed

            if "cash_discount" in request.POST:
                cash_disc_str = request.POST.get("cash_discount", "0").replace(",", "")
                cash_disc_parsed = _parse_decimal(cash_disc_str)
                if cash_disc_parsed is not None and cash_disc_parsed >= 0:
                    invoice.cash_discount = cash_disc_parsed

            invoice.notes = request.POST.get("notes", invoice.notes or '')

            # بروزرسانی اقلام موجود فاکتور
            for item in invoice.invoice_items.all():
                description_key = f"description_{item.id}"
                quantity_key = f"quantity_{item.id}"
                unit_price_key = f"unit_price_{item.id}"
                tax_amount_key = f"tax_amount_{item.id}"
                discount_amount_key = f"discount_amount_{item.id}"

                if description_key in request.POST:
                    item.description = request.POST.get(description_key, item.description)
                if quantity_key in request.POST:
                    qty_parsed = _parse_decimal(request.POST.get(quantity_key))
                    if qty_parsed is not None and qty_parsed >= 0:
                        item.quantity = qty_parsed
                if unit_price_key in request.POST:
                    unit_price_str = request.POST.get(unit_price_key, "0").replace(",", "")
                    up_parsed = _parse_decimal(unit_price_str)
                    if up_parsed is not None and up_parsed >= 0:
                        item.unit_price = up_parsed
                if tax_amount_key in request.POST:
                    tax_str = request.POST.get(tax_amount_key, "0").replace(",", "")
                    tax_parsed = _parse_decimal(tax_str)
                    if tax_parsed is not None and tax_parsed >= 0:
                        item.tax_amount = tax_parsed
                if discount_amount_key in request.POST:
                    discount_str = request.POST.get(discount_amount_key, "0").replace(",", "")
                    disc_parsed = _parse_decimal(discount_str)
                    if disc_parsed is not None and disc_parsed >= 0:
                        item.discount_amount = disc_parsed

                item.line_total = (
                    (item.quantity or Decimal('0')) * (item.unit_price or Decimal('0'))
                    + (item.tax_amount or Decimal('0'))
                    - (item.discount_amount or Decimal('0'))
                )
                item.save()

            # افزودن اقلام جدید
            i = 0
            while f"description_new_{i}" in request.POST:
                description = (request.POST.get(f"description_new_{i}") or "").strip()
                quantity_str = request.POST.get(f"quantity_new_{i}", "0")
                unit_price_str = request.POST.get(f"unit_price_new_{i}", "0").replace(",", "")
                tax_str = request.POST.get(f"tax_amount_new_{i}", "0").replace(",", "")
                discount_str = request.POST.get(f"discount_amount_new_{i}", "0").replace(",", "")

                if description and quantity_str:
                    qty_parsed = _parse_decimal(quantity_str)
                    up_parsed = _parse_decimal(unit_price_str)
                    tax_parsed = _parse_decimal(tax_str)
                    disc_parsed = _parse_decimal(discount_str)
                    quantity = qty_parsed if (qty_parsed is not None and qty_parsed >= 0) else Decimal('0')
                    unit_price = up_parsed if (up_parsed is not None and up_parsed >= 0) else Decimal('0')
                    tax_amount = tax_parsed if (tax_parsed is not None and tax_parsed >= 0) else Decimal('0')
                    discount_amount = disc_parsed if (disc_parsed is not None and disc_parsed >= 0) else Decimal('0')
                    line_total = (quantity * unit_price) + tax_amount - discount_amount

                    InvoiceItem.objects.create(
                        invoice=invoice,
                        description=description,
                        quantity=quantity,
                        unit_price=unit_price,
                        tax_amount=tax_amount,
                        discount_amount=discount_amount,
                        line_total=line_total,
                    )
                i += 1

            # محاسبه مجدد مبالغ بدون مالیات مضاعف (tax-on-tax) و با لحاظ تخفیف نقدی
            items = list(invoice.invoice_items.all())
            base_subtotal = sum(
                ((it.quantity or Decimal('0')) * (it.unit_price or Decimal('0')))
                for it in items
            )
            items_tax = sum((it.tax_amount or Decimal('0')) for it in items)
            items_discount = sum((it.discount_amount or Decimal('0')) for it in items)
            rate_tax = (base_subtotal * (invoice.tax_rate or Decimal('0')) / Decimal('100')) if invoice.tax_rate else Decimal('0')
            rate_discount = (base_subtotal * (invoice.discount_rate or Decimal('0')) / Decimal('100')) if invoice.discount_rate else Decimal('0')
            cash_discount = invoice.cash_discount or Decimal('0')

            computed_subtotal = base_subtotal
            computed_tax = items_tax + rate_tax
            computed_discount = items_discount + rate_discount
            computed_total = (
                computed_subtotal
                + computed_tax
                - computed_discount
                + (invoice.shipping_cost or Decimal('0'))
                - cash_discount
            )

            # بروزرسانی پرداخت‌های موجود (با اسکوپ کافه برای PaymentLocation)
            for payment in invoice.payment_records():
                payment_date_key = f"payment_date_{payment.id}"
                payment_amount_key = f"payment_amount_{payment.id}"
                payment_location_key = f"payment_location_{payment.id}"

                if payment_date_key in request.POST:
                    parsed_pdate = _parse_invoice_date(request.POST.get(payment_date_key))
                    if parsed_pdate is not None:
                        payment.payment_date = parsed_pdate

                if payment_amount_key in request.POST:
                    amount_str = request.POST.get(payment_amount_key, "0").replace(",", "")
                    amt_parsed = _parse_decimal(amount_str)
                    if amt_parsed is not None and amt_parsed >= 0:
                        payment.amount = amt_parsed

                if payment_location_key in request.POST:
                    location_id = (request.POST.get(payment_location_key) or "").strip()
                    if location_id.isdigit():
                        loc = PaymentLocation.objects.filter(id=int(location_id), cafe=cafe).first()
                        payment.payment_location = loc
                    else:
                        payment.payment_location = None

                payment.save()

            # افزودن پرداخت‌های جدید
            i = 0
            while f"payment_date_new_{i}" in request.POST:
                payment_date_str = request.POST.get(f"payment_date_new_{i}")
                payment_amount_str = request.POST.get(f"payment_amount_new_{i}", "0").replace(",", "")
                payment_location_id = (request.POST.get(f"payment_location_new_{i}") or "").strip()

                if payment_date_str and payment_amount_str:
                    amt_parsed = _parse_decimal(payment_amount_str)
                    payment_amount = amt_parsed if amt_parsed is not None else Decimal('0')

                    if payment_amount > 0:
                        payment_date = _parse_invoice_date(payment_date_str)
                        if payment_date is not None:
                            loc = (
                                PaymentLocation.objects.filter(id=int(payment_location_id), cafe=cafe).first()
                                if payment_location_id.isdigit() else None
                            )
                            new_payment = Payment.objects.create(
                                amount=payment_amount,
                                payment_date=payment_date,
                                payment_location=loc,
                                created_by=request.user,
                                payment_status='completed'
                            )
                            new_payment.invoices.add(invoice)
                i += 1

            # محاسبه وضعیت پرداخت بر اساس پرداخت‌های تکمیل‌شده
            total_paid = sum(
                (p.amount or Decimal('0'))
                for p in invoice.payment_records().filter(payment_status='completed')
            )

            if total_paid == 0:
                invoice.payment_status = 'unpaid'
                if invoice.status in ['partially_paid', 'paid']:
                    invoice.status = 'sent'
            elif total_paid < computed_total:
                invoice.payment_status = 'partial'
                invoice.status = 'partially_paid'
            else:
                invoice.payment_status = 'paid' if total_paid == computed_total else 'overpaid'
                invoice.status = 'paid'

            today_local = timezone.localdate()
            if invoice.due_date and invoice.due_date < today_local:
                if invoice.payment_status in ['unpaid', 'partial']:
                    invoice.status = 'overdue'

            invoice.updated_by = request.user
            invoice.save()
            # اعمال دقیق مبالغ محاسبه‌شده (چون Invoice.save() در مدل مبالغ اقلام را بدون cash_discount بازنویسی می‌کند)
            if items:
                Invoice.objects.filter(pk=invoice.pk).update(
                    subtotal=computed_subtotal,
                    tax_amount=computed_tax,
                    discount_amount=computed_discount,
                    cash_discount=cash_discount,
                    total_amount=computed_total,
                    payment_status=invoice.payment_status,
                    status=invoice.status,
                )

        messages.success(request, "فاکتور با موفقیت بروزرسانی شد.")
        return redirect("invoice_detail", cafe_code=cafe.code if cafe else 'local', invoice_id=invoice.id)

    # GET request - نمایش فرم با داده‌های موجود
    items = invoice.invoice_items.all()
    payment_locations = PaymentLocation.objects.filter(cafe=cafe)

    return render(request, "invoice_form.html", {
        "invoice": invoice,
        "order": order,
        "items": items,
        "categories": categories,
        "payment_locations": payment_locations,
    })


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
@require_POST
def invoice_send(request, invoice_id):
    invoice = get_scoped_object_or_404(Invoice, request, id=invoice_id)
    invoice.status = "sent"
    invoice.save()
    return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)


@custom_login_required
@role_required(["manager"])
def finance_manager_edited_invoices(request):
    """نمایش فاکتورهای ویرایش شده یا ثبت شده توسط مدیر مالی"""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        edited_invoices = Invoice.objects.none()
    else:
        finance_managers = User.objects.filter(role='finance_manager').filter(
            Q(cafe=cafe) | Q(cafe__isnull=True)
        )
        edited_invoices = Invoice.objects.filter(cafe=cafe).filter(
            Q(created_by__in=finance_managers) | Q(updated_by__in=finance_managers)
        ).select_related('supplier', 'created_by', 'updated_by').order_by('-updated_at')

    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if search_query:
        edited_invoices = edited_invoices.filter(
            Q(invoice_number__icontains=search_query) |
            Q(supplier__name__icontains=search_query) |
            Q(store_name__icontains=search_query)
        )

    if status_filter:
        edited_invoices = edited_invoices.filter(status=status_filter)

    if date_from:
        date_from_obj = _parse_invoice_date(date_from)
        if date_from_obj:
            edited_invoices = edited_invoices.filter(invoice_date__gte=date_from_obj)

    if date_to:
        date_to_obj = _parse_invoice_date(date_to)
        if date_to_obj:
            edited_invoices = edited_invoices.filter(invoice_date__lte=date_to_obj)

    context = {
        'invoices': edited_invoices,
        'search_query': search_query,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
        'status_choices': Invoice.STATUS_CHOICES,
    }

    return render(request, 'finance_manager_edited_invoices.html', context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
@require_POST
def invoice_cancel(request, invoice_id):
    invoice = get_scoped_object_or_404(Invoice, request, id=invoice_id)
    invoice.status = "cancelled"
    invoice.save()
    return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)


@custom_login_required
@role_required(["manager", "finance_manager"])
def invoice_delete(request, invoice_id):
    cafe = get_cafe_from_request(request)
    # فاکتور فقط با کافهٔ صریح قابل حذف است؛ فاکتور بدون کافه در scope هیچ کافهای نیست
    invoice = get_scoped_object_or_404(Invoice, request, id=invoice_id, cafe=cafe)
    if request.method == "POST":
        try:
            invoice.delete()
            messages.success(request, "فاکتور با موفقیت حذف شد.")
        except models.ProtectedError:
            messages.error(request, "این فاکتور دارای سابقه پرداخت ثبت‌شده است و امکان حذف آن وجود ندارد.")
        return get_cafe_redirect_url("invoices_list", request)
    return render(request, "invoice_confirm_delete.html", {"invoice": invoice})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_bulk_payment(request):
    """پرداخت دسته‌جمعی فاکتورها (با تخصیص مجزا به‌ازای هر فاکتور در کافهٔ فعلی)"""
    if request.method == 'POST':
        cafe = get_cafe_from_request(request)
        if cafe is None:
            messages.error(request, "کافهٔ فعال یافت نشد.")
            return get_cafe_redirect_url('invoices_list', request)

        raw_ids = request.POST.getlist('invoice_ids')
        invoice_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
        if not invoice_ids:
            messages.warning(request, "هیچ فاکتوری برای پرداخت انتخاب نشده است.")
            return get_cafe_redirect_url('invoices_list', request)

        with transaction.atomic():
            invoices = list(
                Invoice.objects.select_for_update()
                .filter(id__in=invoice_ids, cafe=cafe)
                .order_by('invoice_date', 'id')
            )
            if not invoices:
                messages.warning(request, "فاکتورهای انتخاب شده یافت نشدند.")
                return get_cafe_redirect_url('invoices_list', request)

            paid_count = 0
            today_local = timezone.localdate()
            for inv in invoices:
                inv_remaining = Decimal(str(inv.remaining_amount or 0))
                if inv_remaining <= 0:
                    continue
                Payment.objects.create(
                    invoice=inv,
                    amount=inv_remaining,
                    payment_date=today_local,
                    payment_status='completed',
                    created_by=request.user,
                    notes=f"پرداخت دسته‌جمعی برای فاکتور {inv.invoice_number}",
                )
                inv.update_payment_status()
                paid_count += 1

        if paid_count:
            messages.success(request, f"پرداخت {paid_count} فاکتور با موفقیت ثبت شد.")
        else:
            messages.info(request, "فاکتورهای انتخاب‌شده ماندهٔ پرداخت‌نشده نداشتند.")

    return get_cafe_redirect_url('invoices_list', request)


@custom_login_required
@role_required(["manager", "finance_manager"])
def invoice_bulk_delete(request):
    if request.method == 'POST':
        cafe = get_cafe_from_request(request)
        if cafe is None:
            messages.error(request, "کافهٔ فعال یافت نشد.")
            return get_cafe_redirect_url('invoices_list', request)

        raw_ids = request.POST.getlist('invoice_ids')
        invoice_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
        if not invoice_ids:
            messages.warning(request, "هیچ فاکتوری برای حذف انتخاب نشده است.")
            return get_cafe_redirect_url('invoices_list', request)

        invoices_to_delete = list(Invoice.objects.filter(id__in=invoice_ids, cafe=cafe))
        if invoices_to_delete:
            deleted_count = 0
            protected_count = 0
            for inv in invoices_to_delete:
                try:
                    inv.delete()
                    deleted_count += 1
                except models.ProtectedError:
                    protected_count += 1
            if deleted_count:
                messages.success(request, f"{deleted_count} فاکتور با موفقیت حذف شد.")
            if protected_count:
                messages.error(
                    request,
                    f"{protected_count} فاکتور به دلیل داشتن سابقه پرداخت قابل حذف نبود.",
                )
        else:
            messages.warning(request, "فاکتورهای انتخاب شده برای حذف یافت نشدند.")

    return get_cafe_redirect_url('invoices_list', request)


@custom_login_required
@role_required(["purchasing", "manager"])
def completed_orders_for_invoices(request):
    orders = filter_by_cafe(PurchaseOrder.objects.filter(status="completed"), request)
    return render(request, "completed_orders_for_invoices.html", {"orders": orders})


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["manager", "purchasing", "finance_manager"])
def payment_list(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return render(request, "payment_list.html", {
            "payments": Payment.objects.none(),
            "payment_locations": PaymentLocation.objects.none(),
            "date_from": "",
            "date_to": "",
            "selected_location": "",
            "total_amount": 0,
        })

    payments = (
        Payment.objects.select_related("invoice", "payment_location")
        .prefetch_related("invoices")
        .filter(Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe))
        .distinct()
    )
    methods = PaymentLocation.objects.filter(cafe=cafe)

    # Filter by date range (پشتیبانی کامل از تاریخ شمسی و میلادی)
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if date_from:
        date_from_obj = _parse_invoice_date(date_from)
        if date_from_obj:
            payments = payments.filter(payment_date__gte=date_from_obj)

    if date_to:
        date_to_obj = _parse_invoice_date(date_to)
        if date_to_obj:
            payments = payments.filter(payment_date__lte=date_to_obj)

    # Filter by payment location
    location_id = (request.GET.get('location') or '').strip()
    if location_id and location_id.isdigit():
        payments = payments.filter(payment_location_id=int(location_id))

    # Calculate total
    total_amount = payments.aggregate(total=Sum('amount'))['total'] or 0

    return render(
        request,
        "payment_list.html",
        {
            "payments": payments.order_by('-payment_date', '-created_at'),
            "payment_locations": methods,
            "date_from": date_from,
            "date_to": date_to,
            "selected_location": location_id,
            "total_amount": total_amount,
        },
    )


# ---------------------------------------------------------------------------
# لیست بدهی تأمین‌کنندگان — تعریف توسط ادمین، پرداخت و تایید توسط مدیرعامل
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Debts & Debt Payments (مدیریت بدهی‌ها — تفکیک‌شده در views_debts.py)
# ---------------------------------------------------------------------------
from .views_debts import (
    MOBILE_UA_RE,
    is_mobile_request,
    _redirect_preserving_query,
    _debt_redirect,
    _parse_debt_date,
    _today_jalali,
    PERIOD_CHOICES,
    PERIOD_DAYS,
    _filter_debt_payments_by_period,
    _get_payment_accounts,
    _parse_payment_account,
    _get_active_suppliers,
    _suppliers_list,
    _parse_supplier,
    debt_list,
    ceo_debt_payments,
    debt_pay,
    debt_pay_offlist,
    debt_edit,
    debt_delete,
    debt_payment_edit,
    debt_payment_delete,
    payment_accounts,
    mobile_debt_dashboard,
)





@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_create(request, invoice_id=None):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')

    invoices = Invoice.objects.filter(cafe=cafe)
    methods = PaymentLocation.objects.filter(cafe=cafe)
    target_invoice = get_scoped_object_or_404(Invoice, request, id=invoice_id) if invoice_id else None

    # Handle bulk payment pre-selection
    raw_selected_ids = request.POST.getlist('invoices') if request.method == 'POST' else []
    selected_ids_int = [int(x) for x in raw_selected_ids if str(x).strip().isdigit()]
    selected_invoices = Invoice.objects.filter(id__in=selected_ids_int, cafe=cafe) if selected_ids_int else Invoice.objects.none()

    # Calculate default amount for bulk payment
    default_amount = (
        sum((inv.remaining_amount for inv in selected_invoices), Decimal('0'))
        if selected_ids_int
        else (target_invoice.remaining_amount if target_invoice else 0)
    )

    if request.method == "POST":
        amount_val = _parse_decimal(request.POST.get("amount") or 0)
        payment_date = _parse_invoice_date(request.POST.get("payment_date")) or timezone.localdate()
        loc_raw = (request.POST.get("payment_location") or "").strip()
        payment_location = PaymentLocation.objects.filter(id=int(loc_raw), cafe=cafe).first() if loc_raw.isdigit() else None
        reference_number = request.POST.get("reference_number", "")
        notes = request.POST.get("notes", "")

        if selected_ids_int and amount_val is not None and amount_val > 0:
            with transaction.atomic():
                locked_invoices = list(
                    Invoice.objects.select_for_update()
                    .filter(id__in=selected_ids_int, cafe=cafe)
                    .order_by('invoice_date', 'id')
                )
                if locked_invoices:
                    _payments, allocated_invoices, _rem = _allocate_payment_across_invoices(
                        invoices_list=locked_invoices,
                        total_amount=amount_val,
                        payment_date=payment_date,
                        payment_location=payment_location,
                        reference_number=reference_number,
                        notes=notes,
                        created_by=request.user,
                    )

                    notified_users = set()
                    for inv in allocated_invoices:
                        if inv.purchase_order and inv.purchase_order.request and inv.purchase_order.request.user:
                            u = inv.purchase_order.request.user
                            if u not in notified_users:
                                Notification.objects.create(
                                    cafe=cafe,
                                    recipient=u,
                                    title="Bulk payment recorded",
                                    message=f"Bulk payment of {amount_val} for {len(allocated_invoices)} invoices",
                                    notification_type="success",
                                )
                                notified_users.add(u)

                    messages.success(request, f"پرداخت گروهی {amount_val} برای {len(allocated_invoices)} فاکتور ثبت شد.")
                    return redirect("payment_list", cafe_code=cafe.code)

        # Single payment
        single_inv_id_raw = (request.POST.get("invoice") or "").strip()
        with transaction.atomic():
            if target_invoice is not None:
                invoice = get_scoped_locked_object_or_404(Invoice, request, id=target_invoice.id)
            elif single_inv_id_raw.isdigit():
                invoice = Invoice.objects.select_for_update().filter(id=int(single_inv_id_raw), cafe=cafe).first()
            else:
                invoice = None

            if invoice and amount_val is not None and amount_val > 0:
                Payment.objects.create(
                    invoice=invoice,
                    amount=amount_val,
                    payment_date=payment_date,
                    payment_location=payment_location,
                    reference_number=reference_number,
                    notes=notes,
                    created_by=request.user,
                    payment_status='completed',
                )
                invoice.update_payment_status()
                if invoice.purchase_order and invoice.purchase_order.request and invoice.purchase_order.request.user:
                    Notification.objects.create(
                        cafe=cafe,
                        recipient=invoice.purchase_order.request.user,
                        title="Payment recorded",
                        message=f"Payment for {invoice.invoice_number}",
                        notification_type="success",
                    )
                return redirect("payment_list", cafe_code=cafe.code)

    return render(
        request,
        "payment_form.html",
        {
            "invoice": target_invoice,
            "invoices": invoices,
            "payment_locations": methods,
            "selected_invoices": selected_invoices,
            "default_amount": default_amount,
        },
    )


@custom_login_required
@role_required(["manager", "purchasing", "finance_manager"])
def payment_detail(request, payment_id):
    payment = _scoped_payment_or_404(request, payment_id)
    return render(request, "payment_detail.html", {"payment": payment})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_update(request, payment_id):
    cafe = get_cafe_from_request(request)
    payment = _scoped_payment_or_404(request, payment_id)
    if request.method == "POST":
        payment.notes = request.POST.get("notes", payment.notes)
        payment.save()
        return get_cafe_redirect_url("payment_detail", request, payment_id=payment.id)
    methods = PaymentLocation.objects.filter(cafe=cafe) if cafe else PaymentLocation.objects.none()
    return render(request, "payment_form.html", {"payment": payment, "payment_locations": methods})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_delete(request, payment_id):
    payment = _scoped_payment_or_404(request, payment_id)
    if request.method == "POST":
        cafe = get_cafe_from_request(request)
        with transaction.atomic():
            locked_payment = _scoped_payment_or_404(request, payment_id)
            linked_invoices = list(locked_payment.get_related_invoices())
            locked_payment.delete()
            for inv in linked_invoices:
                inv.update_payment_status()
        return redirect("payment_list", cafe_code=cafe.code if cafe else 'local')
    return render(request, "payment_confirm_delete.html", {"payment": payment})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_list(request):
    payment_methods = filter_by_cafe(PaymentLocation.objects.all(), request)
    return render(request, "payment_method_list.html", {"payment_methods": payment_methods})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_create(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if name:
            try:
                display_order = int(request.POST.get("display_order") or 0)
            except (ValueError, TypeError):
                display_order = 0
            PaymentLocation.objects.create(
                name=name,
                description=request.POST.get("description", ""),
                is_active=request.POST.get("is_active") == "on",
                requires_reference=request.POST.get("requires_reference") == "on",
                requires_bank_info=request.POST.get("requires_bank_info") == "on",
                requires_check_info=request.POST.get("requires_check_info") == "on",
                requires_card_info=request.POST.get("requires_card_info") == "on",
                icon_class=request.POST.get("icon_class", ""),
                display_order=display_order,
                cafe=cafe,
            )
            return redirect("payment_location_list", cafe_code=cafe.code)
    return render(request, "payment_method_form.html")


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_update(request, method_id):
    payment_method = get_scoped_object_or_404(PaymentLocation, request, id=method_id)
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        payment_method.name = request.POST.get("name", payment_method.name)
        payment_method.description = request.POST.get("description", payment_method.description)
        payment_method.is_active = request.POST.get("is_active") == "on"
        payment_method.requires_reference = request.POST.get("requires_reference") == "on"
        payment_method.requires_bank_info = request.POST.get("requires_bank_info") == "on"
        payment_method.requires_check_info = request.POST.get("requires_check_info") == "on"
        payment_method.requires_card_info = request.POST.get("requires_card_info") == "on"
        payment_method.icon_class = request.POST.get("icon_class", payment_method.icon_class)
        try:
            payment_method.display_order = int(request.POST.get("display_order") or payment_method.display_order)
        except (ValueError, TypeError):
            pass
        payment_method.save()
        return redirect("payment_location_list", cafe_code=cafe.code if cafe else 'local')
    return render(request, "payment_method_form.html", {"payment_method": payment_method})


# ---------------------------------------------------------------------------
# Suppliers and evaluations
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_list(request):
    suppliers = filter_by_cafe(Supplier.objects.all(), request)

    search = (request.GET.get('search') or '').strip()
    status = (request.GET.get('status') or '').strip()
    supplier_type = (request.GET.get('type') or '').strip()
    if search:
        suppliers = suppliers.filter(
            Q(name__icontains=search) |
            Q(supplier_code__icontains=search) |
            Q(contact_person__icontains=search) |
            Q(phone__icontains=search) |
            Q(city__icontains=search)
        )
    if status:
        suppliers = suppliers.filter(status=status)
    if supplier_type:
        suppliers = suppliers.filter(supplier_type=supplier_type)

    return render(request, "supplier_list.html", {
        "suppliers": suppliers,
        "current_filters": {'search': search, 'status': status, 'type': supplier_type},
        "status_choices": Supplier.STATUS_CHOICES,
        "type_choices": Supplier.SUPPLIER_TYPE_CHOICES,
    })


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_select(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')

    suppliers = Supplier.objects.filter(status='active', cafe=cafe)
    selected_supplier = None
    invoices = []
    total_owed = 0
    payment_methods = PaymentLocation.objects.filter(cafe=cafe)

    supplier_id_raw = (request.GET.get('supplier_id') or request.POST.get('supplier_id') or '').strip()
    supplier_id = int(supplier_id_raw) if supplier_id_raw.isdigit() else None

    if request.method == 'POST' and supplier_id:
        selected_supplier = Supplier.objects.filter(id=supplier_id, cafe=cafe, status='active').first()
        if selected_supplier is None:
            messages.error(request, 'تأمین‌کننده یافت نشد.')
            return redirect('supplier_select', cafe_code=cafe.code)

        payment_mode = request.POST.get('payment_mode')  # 'manual' or 'auto'
        amount = _parse_decimal(request.POST.get('amount') or 0)
        payment_location_id = (request.POST.get('payment_location') or '').strip()
        reference_number = request.POST.get('reference_number', '')
        notes = request.POST.get('notes', '')

        if amount is not None and amount > 0:
            payment_location = (
                PaymentLocation.objects.filter(id=int(payment_location_id), cafe=cafe).first()
                if payment_location_id.isdigit() else None
            )
            today_local = timezone.localdate()

            with transaction.atomic():
                if payment_mode == 'manual':
                    raw_ids = request.POST.getlist('selected_invoices')
                    sel_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
                    selected_invoices = list(
                        Invoice.objects.select_for_update()
                        .filter(id__in=sel_ids, cafe=cafe, supplier=selected_supplier)
                        .order_by('invoice_date', 'created_at', 'id')
                    )
                    if selected_invoices:
                        _payments, allocated_invoices, _rem = _allocate_payment_across_invoices(
                            invoices_list=selected_invoices,
                            total_amount=amount,
                            payment_date=today_local,
                            payment_location=payment_location,
                            reference_number=reference_number,
                            notes=notes,
                            created_by=request.user,
                        )
                        messages.success(
                            request,
                            f'پرداخت {amount:,} ریال برای {len(allocated_invoices)} فاکتور ثبت شد.',
                        )
                else:
                    all_invoices = list(
                        Invoice.objects.select_for_update()
                        .filter(cafe=cafe, supplier=selected_supplier)
                        .order_by('invoice_date', 'created_at', 'id')
                    )
                    unpaid_invoices = [
                        inv for inv in all_invoices
                        if Decimal(str(inv.remaining_amount or 0)) > 0
                    ]
                    if unpaid_invoices:
                        total_owed_before = sum(
                            (Decimal(str(inv.remaining_amount or 0)) for inv in unpaid_invoices),
                            Decimal('0'),
                        )
                        _payments, settled_invoices, leftover = _allocate_payment_across_invoices(
                            invoices_list=unpaid_invoices,
                            total_amount=amount,
                            payment_date=today_local,
                            payment_location=payment_location,
                            reference_number=reference_number,
                            notes=f'تسویه خودکار - {notes}' if notes else 'تسویه خودکار از قدیمی‌ترین فاکتورها',
                            created_by=request.user,
                        )
                        if amount > total_owed_before:
                            overpaid = amount - total_owed_before
                            messages.success(
                                request,
                                f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور تسویه شد. اضافه پرداخت: {overpaid:,} ریال',
                            )
                        elif amount < total_owed_before:
                            shortfall = total_owed_before - amount
                            messages.warning(
                                request,
                                f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور تسویه شد. مانده: {shortfall:,} ریال',
                            )
                        else:
                            messages.success(
                                request,
                                f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور کاملاً تسویه شد.',
                            )
                    else:
                        messages.warning(request, 'هیچ فاکتور پرداخت‌نشده‌ای برای تسویه یافت نشد.')

            return redirect(f'?supplier_id={supplier_id}')
        else:
            messages.error(request, 'مبلغ پرداخت باید بیشتر از صفر باشد.')

    if supplier_id:
        selected_supplier = Supplier.objects.filter(id=supplier_id, cafe=cafe, status='active').first()
        if selected_supplier is not None:
            invoices = Invoice.objects.filter(
                cafe=cafe, supplier=selected_supplier
            ).select_related('purchase_order').prefetch_related('issues').order_by('invoice_date', 'created_at')

            search_query = request.GET.get('search', '')
            payment_status = request.GET.get('payment_status', '')
            issue_id = (request.GET.get('issue') or '').strip()
            date_from = request.GET.get('date_from', '')
            date_to = request.GET.get('date_to', '')

            if search_query:
                invoices = invoices.filter(
                    Q(invoice_number__icontains=search_query) |
                    Q(total_amount__icontains=search_query)
                )

            if payment_status:
                invoices = invoices.filter(payment_status=payment_status)

            if issue_id.isdigit():
                invoices = invoices.filter(issues__id=int(issue_id))

            if date_from:
                date_from_obj = _parse_invoice_date(date_from)
                if date_from_obj:
                    invoices = invoices.filter(invoice_date__gte=date_from_obj)

            if date_to:
                date_to_obj = _parse_invoice_date(date_to)
                if date_to_obj:
                    invoices = invoices.filter(invoice_date__lte=date_to_obj)

            for invoice in invoices:
                total_owed += max(0, invoice.remaining_amount)

    context = {
        'suppliers': suppliers,
        'selected_supplier': selected_supplier,
        'invoices': invoices,
        'total_owed': total_owed,
        'payment_methods': payment_methods,
        'issues': InvoiceIssue.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True)),
    }
    return render(request, "supplier_select.html", context)


def _supplier_form_data(request):
    """همه فیلدهای فرم تأمین‌کننده — برای ایجاد و ویرایش مشترک"""
    return {
        'name': request.POST.get("name", ""),
        'supplier_type': request.POST.get("supplier_type", "company"),
        'contact_person': request.POST.get("contact_person", ""),
        'email': request.POST.get("email", ""),
        'phone': request.POST.get("phone", ""),
        'mobile': request.POST.get("mobile", ""),
        'address': request.POST.get("address", ""),
        'city': request.POST.get("city", ""),
        'state': request.POST.get("state", ""),
        'postal_code': request.POST.get("postal_code", ""),
        'country': request.POST.get("country", "ایران"),
        'tax_id': request.POST.get("tax_id", ""),
        'registration_number': request.POST.get("registration_number", ""),
        'business_license': request.POST.get("business_license", ""),
        'website': request.POST.get("website", ""),
        'bank_name': request.POST.get("bank_name", ""),
        'bank_account': request.POST.get("bank_account", ""),
        'bank_routing': request.POST.get("bank_routing", ""),
        'card_number': request.POST.get("card_number", ""),
        'sheba_number': request.POST.get("sheba_number", ""),
        'payment_terms': request.POST.get("payment_terms", ""),
        'notes': request.POST.get("notes", ""),
    }


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_create(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    if request.method == "POST":
        data = _supplier_form_data(request)
        if data['name'] and data['supplier_type']:
            try:
                supplier = Supplier.objects.create(cafe=cafe, **data)
                cat_ids = [int(x) for x in request.POST.getlist("categories") if str(x).strip().isdigit()]
                if cat_ids:
                    supplier.categories.set(Category.objects.filter(id__in=cat_ids, cafe=cafe))
                messages.success(request, f'تأمین‌کننده "{supplier.name}" با موفقیت ایجاد شد.')
                return get_cafe_redirect_url("supplier_list", request)
            except Exception as e:
                messages.error(request, f'خطا در ایجاد تأمین‌کننده: {str(e)}')
        else:
            messages.error(request, 'نام تأمین‌کننده و نوع تأمین‌کننده الزامی هستند.')
    return render(request, "supplier_form.html", {
        "supplier_types": Supplier.SUPPLIER_TYPE_CHOICES,
        "categories": Category.objects.filter(cafe=cafe, is_active=True),
    })


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_detail(request, supplier_id):
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    return render(request, "supplier_detail.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_update(request, supplier_id):
    cafe = get_cafe_from_request(request)
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    if request.method == "POST":
        data = _supplier_form_data(request)
        if not data['name']:
            messages.error(request, 'نام تأمین‌کننده الزامی است.')
            return render(request, "supplier_form.html", {
                "supplier": supplier,
                "supplier_types": Supplier.SUPPLIER_TYPE_CHOICES,
                "categories": Category.objects.filter(cafe=cafe, is_active=True),
            })
        for field, value in data.items():
            setattr(supplier, field, value)
        try:
            supplier.save()
            cat_ids = [int(x) for x in request.POST.getlist("categories") if str(x).strip().isdigit()]
            supplier.categories.set(Category.objects.filter(id__in=cat_ids, cafe=cafe))
            messages.success(request, f'تأمین‌کننده "{supplier.name}" با موفقیت بروزرسانی شد.')
            return get_cafe_redirect_url("supplier_list", request)
        except Exception as e:
            messages.error(request, f'خطا در بروزرسانی تأمین‌کننده: {str(e)}')
    return render(request, "supplier_form.html", {
        "supplier": supplier,
        "supplier_types": Supplier.SUPPLIER_TYPE_CHOICES,
        "categories": Category.objects.filter(cafe=cafe, is_active=True),
    })


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_delete(request, supplier_id):
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    if request.method == "POST":
        supplier.delete()
        return get_cafe_redirect_url("supplier_list", request)
    return render(request, "supplier_confirm_delete.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_list(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        issues = InvoiceIssue.objects.none()
    else:
        default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
        for issue in default_issues:
            InvoiceIssue.objects.get_or_create(name=issue, cafe=cafe)
        issues = InvoiceIssue.objects.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
    return render(request, "invoice_issue_list.html", {"issues": issues})


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_create(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if name:
            InvoiceIssue.objects.get_or_create(name=name, cafe=cafe)
            return get_cafe_redirect_url("invoice_issue_list", request)
    return render(request, "invoice_issue_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_delete(request, issue_id):
    issue = get_scoped_object_or_404(InvoiceIssue, request, id=issue_id)
    if request.method == "POST":
        issue.delete()
        return get_cafe_redirect_url("invoice_issue_list", request)
    return render(request, "invoice_issue_confirm_delete.html", {"issue": issue})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
@require_POST
def supplier_activate(request, supplier_id):
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    supplier.status = "active"
    supplier.save()
    return get_cafe_redirect_url("supplier_detail", request, supplier_id=supplier.id)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
@require_POST
def supplier_deactivate(request, supplier_id):
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    supplier.status = "inactive"
    supplier.save()
    return get_cafe_redirect_url("supplier_detail", request, supplier_id=supplier.id)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_list(request, supplier_id):
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    evaluations = SupplierEvaluation.objects.filter(supplier=supplier)
    return render(
        request,
        "supplier_evaluation_list.html",
        {"supplier": supplier, "evaluations": evaluations},
    )


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_create(request, supplier_id):
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    if request.method == "POST":
        SupplierEvaluation.objects.create(
            supplier=supplier,
            evaluation_date=timezone.localdate(),
            quality_score=_parse_decimal(request.POST.get("quality_score") or 0) or Decimal('0'),
            delivery_score=_parse_decimal(request.POST.get("delivery_score") or 0) or Decimal('0'),
            pricing_score=_parse_decimal(request.POST.get("pricing_score") or 0) or Decimal('0'),
            comments=request.POST.get("comments", ""),
            evaluator=request.user,
        )
        return get_cafe_redirect_url("supplier_evaluation_list", request, supplier_id=supplier.id)
    return render(request, "supplier_evaluation_form.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_all_list(request):
    cafe = get_cafe_from_request(request)
    evaluations = (
        SupplierEvaluation.objects.filter(supplier__cafe=cafe).select_related("supplier")
        if cafe is not None else SupplierEvaluation.objects.none()
    )
    return render(
        request,
        "supplier_evaluation_list.html",
        {"evaluations": evaluations, "supplier": None},
    )


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_create_general(request):
    cafe = get_cafe_from_request(request)
    suppliers = filter_by_cafe(Supplier.objects.all(), request)
    if request.method == "POST":
        sup_raw = (request.POST.get("supplier") or "").strip()
        if sup_raw.isdigit():
            supplier = get_scoped_object_or_404(Supplier, request, id=int(sup_raw))
            SupplierEvaluation.objects.create(
                supplier=supplier,
                evaluation_date=timezone.localdate(),
                quality_score=_parse_decimal(request.POST.get("quality_score") or 0) or Decimal('0'),
                delivery_score=_parse_decimal(request.POST.get("delivery_score") or 0) or Decimal('0'),
                pricing_score=_parse_decimal(request.POST.get("pricing_score") or 0) or Decimal('0'),
                comments=request.POST.get("comments", ""),
                evaluator=request.user,
            )
            return get_cafe_redirect_url("supplier_evaluation_all_list", request)
    return render(request, "supplier_evaluation_form.html", {"suppliers": suppliers})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_performance_report(request):
    cafe = get_cafe_from_request(request)
    evaluations = (
        SupplierEvaluation.objects.filter(supplier__cafe=cafe).select_related("supplier")
        if cafe is not None else SupplierEvaluation.objects.none()
    )
    return render(request, "supplier_performance_report.html", {"evaluations": evaluations})


