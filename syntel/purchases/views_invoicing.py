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
    get_cafe_from_request,
    get_cafe_redirect_url,
    get_scoped_object_or_404,
    role_required,
)

logger = logging.getLogger(__name__)

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
    # Ensure default issues exist
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue)
    
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
    
    # Base queryset
    cafe = get_cafe_from_request(request)
    invoices = Invoice.objects.select_related("purchase_order", "supplier").all()
    if cafe:
        invoices = invoices.filter(cafe=cafe)
    
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
    issues = InvoiceIssue.objects.all()
    
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
    # Ensure default issues exist
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue)

    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    selected_status = request.GET.get('status', '')
    selected_payment_status = request.GET.get('payment_status', '')
    invoice_date_filter = request.GET.get('invoice_date', '').strip()
    due_date_filter = request.GET.get('due_date', '').strip()

    # Handle single issue selection
    selected_issue = request.GET.get('issue', '').strip()
    has_issues = request.GET.get('has_issues', '').strip()

    # Base queryset - ALL invoices (no type filtering)
    cafe = get_cafe_from_request(request)
    invoices = Invoice.objects.select_related("purchase_order", "supplier").all()
    if cafe:
        invoices = invoices.filter(cafe=cafe)

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
    issues = InvoiceIssue.objects.all()

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
    if request.method == "POST":
        # پردازش تاریخ‌ها
        invoice_date_str = request.POST.get("invoice_date")
        request_date_str = request.POST.get("request_date")
        
        try:
            invoice_date = datetime.fromisoformat(invoice_date_str).date() if invoice_date_str else timezone.now().date()
        except ValueError:
            invoice_date = timezone.now().date()
            
        try:
            request_date = datetime.fromisoformat(request_date_str).date() if request_date_str else timezone.now().date()
        except ValueError:
            request_date = timezone.now().date()

        # دریافت تأمین‌کننده (موجود یا جدید)
        supplier_id = request.POST.get("supplier")
        store_name = request.POST.get("supplier_name", "").strip()
        supplier = None
        
        if supplier_id:
            # تامین‌کننده موجود
            try:
                supplier = Supplier.objects.get(id=supplier_id)
            except Supplier.DoesNotExist:
                pass
        
        # اگر تامین‌کننده پیدا نشد، خطا بده
        if not supplier and not store_name:
            messages.error(request, "لطفاً تأمین‌کننده یا نام فروشگاه را وارد کنید.")
            # بازگشت به فرم با داده‌های وارد شده
            context = {
                'categories': Category.objects.filter(is_active=True),
                'manual_invoice': True,
                'form_data': request.POST,
                'items_data': items_data,
            }
            return render(request, "invoice_register.html", context)

        # تابع برای پردازش اعداد با کاما
        def parse_money(value):
            if not value:
                return Decimal('0')
            # حذف کاماها و تبدیل به عدد
            cleaned = str(value).replace(',', '')
            return _parse_decimal(cleaned) or Decimal('0')

        # محاسبه مبالغ از اقلام فاکتور
        subtotal = Decimal('0')
        total_tax = Decimal('0')
        total_discount = Decimal('0')
        items_data = []
        i = 0
        while f"description_{i}" in request.POST:
            description = request.POST.get(f"description_{i}", "")
            quantity = _parse_decimal(request.POST.get(f"quantity_{i}", 0)) or Decimal('0')
            unit_price = parse_money(request.POST.get(f"unit_price_{i}", 0))
            tax_amount = parse_money(request.POST.get(f"tax_amount_{i}", 0))
            discount_amount = parse_money(request.POST.get(f"discount_amount_{i}", 0))
            requester_id = request.POST.get(f"requester_{i}")
            requester = None
            if requester_id:
                try:
                    requester = Requester.objects.get(id=requester_id)
                except Requester.DoesNotExist:
                    pass
            
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
            # تولید شماره فاکتور خودکار اگر وارد نشده
            invoice_number = f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        # ایجاد فاکتور
        cafe = get_cafe_from_request(request)
        invoice = Invoice.objects.create(
            purchase_order=None,
            supplier=supplier,
            store_name=store_name,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=request_date,  # تاریخ درخواست به عنوان due_date
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
        
        # ایجاد اقلام فاکتور
        from .models import InvoiceItem
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
        
        # اضافه کردن issues
        issues_ids = request.POST.get('issues', '').split(',')
        issues_ids = [id.strip() for id in issues_ids if id.strip()]
        if issues_ids:
            issues = InvoiceIssue.objects.filter(id__in=issues_ids)
            invoice.issues.set(issues)
        
        # ثبت پرداخت اگر انتخاب شده باشد
        if request.POST.get('add_payment'):
            payment_amount = parse_money(request.POST.get('payment_amount', 0))
            if not payment_amount or payment_amount <= 0:
                payment_amount = total_amount  # اگر مبلغ وارد نشده، کل مبلغ فاکتور
            
            payment_location_id = request.POST.get('payment_location_id')
            payment_date_str = request.POST.get('payment_date')
            payment_notes = request.POST.get('payment_notes', '')
            
            if payment_location_id:
                try:
                    payment_location = PaymentLocation.objects.get(id=payment_location_id)
                    
                    # پردازش تاریخ پرداخت
                    try:
                        payment_date = datetime.fromisoformat(payment_date_str).date() if payment_date_str else timezone.now().date()
                    except ValueError:
                        payment_date = timezone.now().date()
                    
                    # ایجاد پرداخت
                    payment = Payment.objects.create(
                        invoice=invoice,
                        amount=payment_amount,
                        payment_location=payment_location,
                        payment_date=payment_date,
                        notes=payment_notes,
                        created_by=request.user
                    )
                    
                    # بروزرسانی وضعیت پرداخت فاکتور (هر دو نوع اتصال پرداخت)
                    total_paid = invoice.payment_records().aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
                    if total_paid >= invoice.total_amount:
                        invoice.payment_status = 'paid'
                    elif total_paid > 0:
                        invoice.payment_status = 'partial'
                    else:
                        invoice.payment_status = 'unpaid'
                    invoice.save()
                    
                    messages.success(request, f"پرداخت به مبلغ {payment_amount:,} ریال ثبت شد.")
                except PaymentLocation.DoesNotExist:
                    messages.warning(request, "محل پرداخت معتبر نیست.")
        
        messages.success(request, f"فاکتور {invoice.invoice_number} با موفقیت ایجاد شد.")
        return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)

    # نمایش فرم ایجاد فاکتور دستی
    cafe = get_cafe_from_request(request)
    categories = Category.objects.filter(is_active=True).select_related('parent').order_by('parent__name', 'name')
    if cafe:
        categories = categories.filter(cafe=cafe)
    
    # اگر supplier_id در GET وجود دارد، تامین‌کننده را پیش‌فرض کن
    default_supplier = None
    supplier_id = request.GET.get('supplier_id')
    if supplier_id:
        try:
            default_supplier = Supplier.objects.get(id=supplier_id, status='active')
        except Supplier.DoesNotExist:
            pass
    
    context = {
        'categories': categories,
        'requesters': Requester.objects.all(),
        'issues': InvoiceIssue.objects.all(),
        'payment_types': PaymentType.objects.filter(is_active=True),
        'payment_locations': PaymentLocation.objects.filter(is_active=True, cafe=cafe) if cafe else PaymentLocation.objects.filter(is_active=True),
        'today': timezone.now().date(),
        'manual_invoice': True,
        'default_supplier': default_supplier,
    }
    return render(request, "invoice_register.html", context)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_create(request, order_id):
    order = get_object_or_404(PurchaseOrder, id=order_id)
    if request.method == "POST":
        due_date_str = request.POST.get("due_date")
        try:
            due_date = (
                datetime.fromisoformat(due_date_str).date() if due_date_str else timezone.now().date()
            )
        except ValueError:
            due_date = timezone.now().date()

        # بررسی شماره فاکتور
        invoice_number = request.POST.get("invoice_number")
        if not invoice_number:
            # تولید شماره فاکتور خودکار اگر وارد نشده
            invoice_number = f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        invoice = Invoice.objects.create(
            purchase_order=order,
            invoice_number=invoice_number,
            total_amount=_parse_decimal(request.POST.get("total_amount") or 0) or 0,
            due_date=due_date,
            notes=request.POST.get("notes", ""),
            created_by=request.user,
        )
        return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)

    return render(request, "invoice_form.html", {"order": order})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    items = invoice.invoice_items.all()
    response = render(request, "invoice_detail.html", {
        "invoice": invoice,
        "items": items
    })
    # جلوگیری از cache مرورگر برای نمایش داده‌های به‌روز
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_update(request, invoice_id):
    cafe = get_cafe_from_request(request)
    invoice = get_object_or_404(Invoice, id=invoice_id)
    order = invoice.purchase_order
    categories = Category.objects.all().order_by('name')
    if cafe:
        categories = categories.filter(cafe=cafe)
    
    if request.method == "POST":
        # بروزرسانی اطلاعات فاکتور
        invoice_number = request.POST.get("invoice_number")
        if invoice_number:
            invoice.invoice_number = invoice_number
        
        # تاریخ فاکتور
        invoice_date_str = request.POST.get("invoice_date")
        if invoice_date_str:
            try:
                # پشتیبانی از فرمت شمسی
                if '/' in invoice_date_str:
                    parts = invoice_date_str.split('/')
                    if len(parts) == 3:
                        # تبدیل به میلادی (فقط نگهداری به عنوان رشته)
                        invoice.invoice_date = datetime.now().date()
                else:
                    invoice.invoice_date = datetime.fromisoformat(invoice_date_str).date()
            except ValueError:
                pass
        
        # تاریخ سررسید
        due_date_str = request.POST.get("due_date")
        if due_date_str:
            try:
                if '/' in due_date_str:
                    parts = due_date_str.split('/')
                    if len(parts) == 3:
                        invoice.due_date = datetime.now().date()
                else:
                    invoice.due_date = datetime.fromisoformat(due_date_str).date()
            except ValueError:
                pass
        
        # اطلاعات مالی
        invoice.tax_rate = _parse_decimal(request.POST.get("tax_rate")) or invoice.tax_rate
        invoice.discount_rate = _parse_decimal(request.POST.get("discount_rate")) or 0
        # حذف کاما از مقادیر پولی
        shipping_cost_str = request.POST.get("shipping_cost", "0").replace(",", "")
        invoice.shipping_cost = _parse_decimal(shipping_cost_str) or 0
        invoice.notes = request.POST.get("notes", invoice.notes or '')
        
        # بروزرسانی اقلام موجود فاکتور
        for item in invoice.invoice_items.all():
            description_key = f"description_{item.id}"
            quantity_key = f"quantity_{item.id}"
            unit_price_key = f"unit_price_{item.id}"
            category_key = f"category_{item.id}"
            tax_amount_key = f"tax_amount_{item.id}"
            discount_amount_key = f"discount_amount_{item.id}"
            
            if description_key in request.POST:
                item.description = request.POST.get(description_key, item.description)
            if quantity_key in request.POST:
                item.quantity = _parse_decimal(request.POST.get(quantity_key)) or item.quantity
            if unit_price_key in request.POST:
                unit_price_str = request.POST.get(unit_price_key, "0").replace(",", "")
                item.unit_price = _parse_decimal(unit_price_str) or item.unit_price
            if category_key in request.POST:
                category_id = request.POST.get(category_key)
                if category_id:
                    item.category_id = int(category_id)
            if tax_amount_key in request.POST:
                tax_str = request.POST.get(tax_amount_key, "0").replace(",", "")
                item.tax_amount = _parse_decimal(tax_str) or 0
            if discount_amount_key in request.POST:
                discount_str = request.POST.get(discount_amount_key, "0").replace(",", "")
                item.discount_amount = _parse_decimal(discount_str) or 0
            
            item.line_total = (item.quantity * item.unit_price) + (item.tax_amount or 0) - (item.discount_amount or 0)
            item.save()
        
        # افزودن اقلام جدید
        i = 0
        while f"description_new_{i}" in request.POST:
            description = request.POST.get(f"description_new_{i}")
            quantity_str = request.POST.get(f"quantity_new_{i}", "0")
            unit_price_str = request.POST.get(f"unit_price_new_{i}", "0").replace(",", "")
            category_id = request.POST.get(f"category_new_{i}")
            tax_str = request.POST.get(f"tax_amount_new_{i}", "0").replace(",", "")
            discount_str = request.POST.get(f"discount_amount_new_{i}", "0").replace(",", "")
            
            if description and quantity_str:
                quantity = _parse_decimal(quantity_str) or 0
                unit_price = _parse_decimal(unit_price_str) or 0
                tax_amount = _parse_decimal(tax_str) or 0
                discount_amount = _parse_decimal(discount_str) or 0
                line_total = (quantity * unit_price) + tax_amount - discount_amount
                
                new_item = InvoiceItem.objects.create(
                    invoice=invoice,
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    tax_amount=tax_amount,
                    discount_amount=discount_amount,
                    line_total=line_total,
                    category_id=int(category_id) if category_id else None
                )
            i += 1
        
        # محاسبه مجدد مبالغ
        invoice.subtotal = sum(item.line_total or 0 for item in invoice.invoice_items.all())
        invoice.tax_amount = (invoice.subtotal * invoice.tax_rate / 100) if invoice.tax_rate else 0
        invoice.discount_amount = (invoice.subtotal * invoice.discount_rate / 100) if invoice.discount_rate else 0
        invoice.total_amount = invoice.subtotal + invoice.tax_amount - invoice.discount_amount + (invoice.shipping_cost or 0)
        
        # بروزرسانی پرداخت‌های موجود (هر دو نوع اتصال پرداخت)
        for payment in invoice.payment_records():
            payment_date_key = f"payment_date_{payment.id}"
            payment_amount_key = f"payment_amount_{payment.id}"
            payment_location_key = f"payment_location_{payment.id}"
            
            if payment_date_key in request.POST:
                payment_date_str = request.POST.get(payment_date_key)
                if payment_date_str:
                    try:
                        # پشتیبانی از فرمت شمسی
                        if '/' in payment_date_str:
                            parts = payment_date_str.split('/')
                            if len(parts) == 3:
                                payment.payment_date = datetime.now().date()
                        else:
                            payment.payment_date = datetime.fromisoformat(payment_date_str).date()
                    except ValueError:
                        pass
            
            if payment_amount_key in request.POST:
                amount_str = request.POST.get(payment_amount_key, "0").replace(",", "")
                payment.amount = _parse_decimal(amount_str) or payment.amount
            
            if payment_location_key in request.POST:
                location_id = request.POST.get(payment_location_key)
                if location_id:
                    payment.payment_location_id = int(location_id)
                else:
                    payment.payment_location_id = None
            
            payment.save()
        
        # افزودن پرداخت‌های جدید
        i = 0
        while f"payment_date_new_{i}" in request.POST:
            payment_date_str = request.POST.get(f"payment_date_new_{i}")
            payment_amount_str = request.POST.get(f"payment_amount_new_{i}", "0").replace(",", "")
            payment_location_id = request.POST.get(f"payment_location_new_{i}")
            
            if payment_date_str and payment_amount_str:
                payment_amount = _parse_decimal(payment_amount_str) or 0
                
                if payment_amount > 0:
                    try:
                        # Parse payment date
                        if '/' in payment_date_str:
                            payment_date = datetime.now().date()
                        else:
                            payment_date = datetime.fromisoformat(payment_date_str).date()
                        
                        # ایجاد پرداخت جدید
                        new_payment = Payment.objects.create(
                            amount=payment_amount,
                            payment_date=payment_date,
                            payment_location_id=int(payment_location_id) if payment_location_id else None,
                            created_by=request.user,
                            payment_status='completed'
                        )
                        # افزودن فاکتور به پرداخت
                        new_payment.invoices.add(invoice)
                    except (ValueError, TypeError):
                        pass
            i += 1
        
        # محاسبه وضعیت پرداخت بر اساس مجموع پرداخت‌ها (هر دو نوع اتصال)
        total_paid = sum(payment.amount for payment in invoice.payment_records())
        
        if total_paid == 0:
            invoice.payment_status = 'unpaid'
            # اگر فاکتور sent بود و پرداختی نشده، همان وضعیت را حفظ کن
            if invoice.status in ['partially_paid', 'paid']:
                invoice.status = 'sent'
        elif total_paid < invoice.total_amount:
            invoice.payment_status = 'partial'
            invoice.status = 'partially_paid'
        elif total_paid >= invoice.total_amount:
            invoice.payment_status = 'paid' if total_paid == invoice.total_amount else 'overpaid'
            invoice.status = 'paid'
        
        # بررسی سررسید برای تنظیم وضعیت overdue
        if invoice.due_date and invoice.due_date < datetime.now().date():
            if invoice.payment_status in ['unpaid', 'partial']:
                invoice.status = 'overdue'
        
        # ثبت کاربری که فاکتور را ویرایش کرده
        invoice.updated_by = request.user
        invoice.save()
        messages.success(request, "فاکتور با موفقیت بروزرسانی شد.")
        return redirect("invoice_detail", cafe_code=cafe.code if cafe else 'local', invoice_id=invoice.id)
    
    # GET request - نمایش فرم با داده‌های موجود
    items = invoice.invoice_items.all()
    payment_locations = PaymentLocation.objects.all()
    if cafe:
        payment_locations = payment_locations.filter(cafe=cafe)
    
    return render(request, "invoice_form.html", {
        "invoice": invoice,
        "order": order,
        "items": items,
        "categories": categories,
        "payment_locations": payment_locations,
    })


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_send(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    invoice.status = "sent"
    invoice.save()
    return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)


@custom_login_required
@role_required(["manager"])
def finance_manager_edited_invoices(request):
    """نمایش فاکتورهای ویرایش شده یا ثبت شده توسط مدیر مالی"""
    # فاکتورهایی که مدیر مالی آنها را ایجاد یا ویرایش کرده
    finance_managers = User.objects.filter(role='finance_manager')
    
    edited_invoices = Invoice.objects.filter(
        Q(created_by__in=finance_managers) | Q(updated_by__in=finance_managers)
    ).select_related('supplier', 'created_by', 'updated_by').order_by('-updated_at')
    
    # فیلترها
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
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
            edited_invoices = edited_invoices.filter(invoice_date__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
            edited_invoices = edited_invoices.filter(invoice_date__lte=date_to_obj)
        except ValueError:
            pass
    
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
def invoice_cancel(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
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
        invoice.delete()
        return get_cafe_redirect_url("invoices_list", request)
    return render(request, "invoice_confirm_delete.html", {"invoice": invoice})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def invoice_bulk_payment(request):
    """پرداخت دسته‌جمعی فاکتورها"""
    if request.method == 'POST':
        invoice_ids = request.POST.getlist('invoice_ids')
        if not invoice_ids:
            messages.warning(request, "هیچ فاکتوری برای پرداخت انتخاب نشده است.")
            return get_cafe_redirect_url('invoices_list', request)

        invoices = Invoice.objects.filter(id__in=invoice_ids)
        count = invoices.count()

        if count > 0:
            # ایجاد یک پرداخت برای همه فاکتورهای انتخاب شده
            total_amount = sum(invoice.total_amount or 0 for invoice in invoices)
            
            payment = Payment.objects.create(
                amount=total_amount,
                payment_date=timezone.now().date(),
                payment_status='completed',
                created_by=request.user,
                notes=f"پرداخت دسته‌جمعی برای {count} فاکتور"
            )
            
            # اضافه کردن فاکتورها به پرداخت
            payment.invoices.set(invoices)
            
            messages.success(request, f"پرداخت {count} فاکتور با موفقیت ثبت شد.")
        else:
            messages.warning(request, "فاکتورهای انتخاب شده یافت نشدند.")

    return get_cafe_redirect_url('invoices_list', request)


@custom_login_required
@role_required(["manager", "finance_manager"])
def invoice_bulk_delete(request):
    if request.method == 'POST':
        invoice_ids = request.POST.getlist('invoice_ids')
        if not invoice_ids:
            messages.warning(request, "هیچ فاکتوری برای حذف انتخاب نشده است.")
            return get_cafe_redirect_url('invoices_list', request)

        invoices_to_delete = Invoice.objects.filter(id__in=invoice_ids)
        count = invoices_to_delete.count()

        if count > 0:
            invoices_to_delete.delete()
            messages.success(request, f"{count} فاکتور با موفقیت حذف شد.")
        else:
            messages.warning(request, "فاکتورهای انتخاب شده برای حذف یافت نشدند.")

    return get_cafe_redirect_url('invoices_list', request)


@custom_login_required
@role_required(["purchasing", "manager"])
def completed_orders_for_invoices(request):
    orders = PurchaseOrder.objects.filter(status="completed")
    return render(request, "completed_orders_for_invoices.html", {"orders": orders})


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["manager", "purchasing", "finance_manager"])
def payment_list(request):
    cafe = get_cafe_from_request(request)
    payments = Payment.objects.select_related("invoice", "payment_location").prefetch_related("invoices").all()
    if cafe:
        payments = payments.filter(
            Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe)
        ).distinct()
    methods = PaymentLocation.objects.all()
    if cafe:
        methods = methods.filter(cafe=cafe)
    
    # Filter by date range
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    if date_from:
        try:
            # پشتیبانی از فرمت شمسی (1404/01/01) و میلادی
            if '/' in date_from:
                parts = date_from.split('/')
                if len(parts) == 3:
                    # فرض می‌کنیم تاریخ میلادی است (برای سادگی)
                    from datetime import datetime
                    date_from_obj = datetime.strptime(date_from.replace('/', '-'), '%Y-%m-%d').date()
                    payments = payments.filter(payment_date__gte=date_from_obj)
            else:
                from datetime import datetime
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                payments = payments.filter(payment_date__gte=date_from_obj)
        except:
            pass
    
    if date_to:
        try:
            if '/' in date_to:
                parts = date_to.split('/')
                if len(parts) == 3:
                    from datetime import datetime
                    date_to_obj = datetime.strptime(date_to.replace('/', '-'), '%Y-%m-%d').date()
                    payments = payments.filter(payment_date__lte=date_to_obj)
            else:
                from datetime import datetime
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                payments = payments.filter(payment_date__lte=date_to_obj)
        except:
            pass
    
    # Filter by payment location
    location_id = request.GET.get('location', '')
    if location_id:
        payments = payments.filter(payment_location_id=location_id)
    
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
    invoices = Invoice.objects.all()
    if cafe:
        invoices = invoices.filter(cafe=cafe)
    methods = PaymentLocation.objects.all()
    if cafe:
        methods = methods.filter(cafe=cafe)
    target_invoice = Invoice.objects.filter(id=invoice_id).first() if invoice_id else None

    # Handle bulk payment
    selected_invoice_ids = request.POST.getlist('invoices') if request.method == 'POST' else []
    selected_invoices = Invoice.objects.filter(id__in=selected_invoice_ids) if selected_invoice_ids else []

    # Calculate default amount for bulk payment
    default_amount = sum(inv.remaining_amount for inv in selected_invoices) if selected_invoices else (target_invoice.remaining_amount if target_invoice else 0)

    if request.method == "POST":
        # Check if this is a bulk payment
        invoice_ids = request.POST.getlist("invoices")
        amount_val = _parse_decimal(request.POST.get("amount") or 0)
        
        if invoice_ids and amount_val and amount_val > 0:
            # Bulk payment for multiple invoices
            selected_invoices = Invoice.objects.filter(id__in=invoice_ids)
            if selected_invoices.exists():
                payment = Payment.objects.create(
                    amount=amount_val,
                    payment_date=request.POST.get("payment_date") or timezone.now().date(),
                    payment_location=PaymentLocation.objects.filter(id=request.POST.get("payment_location")).first(),
                    reference_number=request.POST.get("reference_number", ""),
                    notes=request.POST.get("notes", ""),
                    created_by=request.user,
                )
                # Add all selected invoices to the payment
                payment.invoices.add(*selected_invoices)
                # اتصال چندبه‌چند بعد از ذخیره اضافه می‌شود، پس وضعیت پرداخت
                # هر فاکتور باید اینجا دوباره محاسبه شود
                for invoice in selected_invoices:
                    invoice.update_payment_status()

                # Send notifications to all related users
                notified_users = set()
                for invoice in selected_invoices:
                    if invoice.purchase_order and invoice.purchase_order.request and invoice.purchase_order.request.user:
                        user = invoice.purchase_order.request.user
                        if user not in notified_users:
                            Notification.objects.create(
                                recipient=user,
                                title="Bulk payment recorded",
                                message=f"Bulk payment of {amount_val} for {selected_invoices.count()} invoices",
                                notification_type="success",
                            )
                            notified_users.add(user)
                
                messages.success(request, f"پرداخت گروهی {amount_val} برای {selected_invoices.count()} فاکتور ثبت شد.")
                return redirect("payment_list", cafe_code=cafe.code if cafe else 'local')
        
        # Single payment (existing logic)
        invoice = target_invoice or Invoice.objects.filter(id=request.POST.get("invoice")).first()
        if invoice and amount_val and amount_val > 0:
            Payment.objects.create(
                invoice=invoice,
                amount=amount_val,
                payment_date=request.POST.get("payment_date") or timezone.now().date(),
                payment_location=PaymentLocation.objects.filter(id=request.POST.get("payment_location")).first(),
                reference_number=request.POST.get("reference_number", ""),
                notes=request.POST.get("notes", ""),
                created_by=request.user,
            )
            Notification.objects.create(
                recipient=invoice.purchase_order.request.user,
                title="Payment recorded",
                message=f"Payment for {invoice.invoice_number}",
                notification_type="success",
            )
            return redirect("payment_list", cafe_code=cafe.code if cafe else 'local')

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
    payment = get_object_or_404(Payment, id=payment_id)
    return render(request, "payment_detail.html", {"payment": payment})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_update(request, payment_id):
    cafe = get_cafe_from_request(request)
    payment = get_object_or_404(Payment, id=payment_id)
    if request.method == "POST":
        payment.notes = request.POST.get("notes", payment.notes)
        payment.save()
        return redirect("payment_detail", payment_id=payment.id)
    methods = PaymentLocation.objects.all()
    if cafe:
        methods = methods.filter(cafe=cafe)
    return render(request, "payment_form.html", {"payment": payment, "payment_locations": methods})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_delete(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    if request.method == "POST":
        cafe = get_cafe_from_request(request)
        payment.delete()
        return redirect("payment_list", cafe_code=cafe.code if cafe else 'local')
    return render(request, "payment_confirm_delete.html", {"payment": payment})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_list(request):
    cafe = get_cafe_from_request(request)
    payment_methods = PaymentLocation.objects.all()
    if cafe:
        payment_methods = payment_methods.filter(cafe=cafe)
    return render(request, "payment_method_list.html", {"payment_methods": payment_methods})


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_create(request):
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            PaymentLocation.objects.create(
                name=name,
                description=request.POST.get("description", ""),
                is_active=request.POST.get("is_active") == "on",
                requires_reference=request.POST.get("requires_reference") == "on",
                requires_bank_info=request.POST.get("requires_bank_info") == "on",
                requires_check_info=request.POST.get("requires_check_info") == "on",
                requires_card_info=request.POST.get("requires_card_info") == "on",
                icon_class=request.POST.get("icon_class", ""),
                display_order=int(request.POST.get("display_order") or 0),
                cafe=cafe,
            )
            cafe_code = cafe.code if cafe else 'local'
            return redirect("payment_location_list", cafe_code=cafe_code)
    return render(request, "payment_method_form.html")


@custom_login_required
@role_required(["manager", "finance_manager"])
def payment_location_update(request, method_id):
    payment_method = get_object_or_404(PaymentLocation, id=method_id)
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
        payment_method.display_order = int(request.POST.get("display_order") or payment_method.display_order)
        payment_method.save()
        cafe_code = cafe.code if cafe else 'local'
        return redirect("payment_location_list", cafe_code=cafe_code)
    return render(request, "payment_method_form.html", {"payment_method": payment_method})


# ---------------------------------------------------------------------------
# Suppliers and evaluations
# ---------------------------------------------------------------------------


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_list(request):
    cafe = get_cafe_from_request(request)
    suppliers = Supplier.objects.all()
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)

    # فیلترهای فرمِ صفحه (جستجو / وضعیت / نوع) — تا انتخاب‌ها بعد از جستجو هم در فرم بمانند
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
    suppliers = Supplier.objects.filter(status='active')
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    selected_supplier = None
    invoices = []
    total_owed = 0
    payment_methods = PaymentLocation.objects.all()
    
    supplier_id = request.GET.get('supplier_id') or request.POST.get('supplier_id')
    
    # Handle payment submission
    if request.method == 'POST' and supplier_id:
        try:
            selected_supplier = Supplier.objects.get(id=supplier_id, status='active')
            payment_mode = request.POST.get('payment_mode')  # 'manual' or 'auto'
            amount = _parse_decimal(request.POST.get('amount') or 0)
            payment_location_id = request.POST.get('payment_location')
            reference_number = request.POST.get('reference_number', '')
            notes = request.POST.get('notes', '')
            
            if amount > 0:
                payment_location = PaymentLocation.objects.filter(id=payment_location_id).first()
                
                if payment_mode == 'manual':
                    # Manual mode: user selects specific invoices
                    selected_invoice_ids = request.POST.getlist('selected_invoices')
                    selected_invoices = Invoice.objects.filter(
                        id__in=selected_invoice_ids,
                        supplier=selected_supplier
                    )
                    
                    if selected_invoices.exists():
                        payment = Payment.objects.create(
                            amount=amount,
                            payment_date=timezone.now().date(),
                            payment_location=payment_location,
                            reference_number=reference_number,
                            notes=notes,
                            created_by=request.user,
                            payment_status='completed'
                        )
                        payment.invoices.add(*selected_invoices)
                        messages.success(request, f'پرداخت {amount:,} ریال برای {selected_invoices.count()} فاکتور ثبت شد.')
                else:
                    # Auto mode: automatically settle oldest unpaid/partially paid invoices
                    all_invoices = Invoice.objects.filter(
                        supplier=selected_supplier
                    ).order_by('invoice_date', 'created_at')
                    
                    # Filter to get only invoices with remaining balance
                    unpaid_invoices = []
                    for inv in all_invoices:
                        paid_amount = Payment.objects.filter(
                            Q(invoice=inv) | Q(invoices=inv),
                            payment_status='completed'
                        ).aggregate(total=Sum('amount'))['total'] or 0
                        inv_remaining = (inv.total_amount or 0) - paid_amount
                        if inv_remaining > 0:
                            unpaid_invoices.append((inv, inv_remaining))
                    
                    remaining_amount = amount
                    settled_invoices = []
                    
                    for invoice, invoice_remaining in unpaid_invoices:
                        if remaining_amount <= 0:
                            break
                        
                        settled_invoices.append(invoice)
                        remaining_amount -= invoice_remaining
                    
                    if settled_invoices:
                        payment = Payment.objects.create(
                            amount=amount,
                            payment_date=timezone.now().date(),
                            payment_location=payment_location,
                            reference_number=reference_number,
                            notes=f'تسویه خودکار - {notes}' if notes else 'تسویه خودکار از قدیمی‌ترین فاکتورها',
                            created_by=request.user,
                            payment_status='completed'
                        )
                        payment.invoices.add(*settled_invoices)
                        
                        if remaining_amount > 0:
                            messages.success(request, f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور تسویه شد. اضافه پرداخت: {remaining_amount:,} ریال')
                        elif remaining_amount < 0:
                            messages.warning(request, f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور تسویه شد (بخشی از آخرین فاکتور). کسری: {abs(remaining_amount):,} ریال')
                        else:
                            messages.success(request, f'پرداخت {amount:,} ریال ثبت شد. {len(settled_invoices)} فاکتور کاملاً تسویه شد.')
                    else:
                        messages.warning(request, 'هیچ فاکتور پرداخت‌نشده‌ای برای تسویه یافت نشد.')
                
                return redirect(f'?supplier_id={supplier_id}')
            else:
                messages.error(request, 'مبلغ پرداخت باید بیشتر از صفر باشد.')
        except Supplier.DoesNotExist:
            messages.error(request, 'تأمین‌کننده یافت نشد.')
        except Exception as e:
            messages.error(request, f'خطا در ثبت پرداخت: {str(e)}')
    
    if supplier_id:
        try:
            selected_supplier = Supplier.objects.get(id=supplier_id, status='active')
            # Get all invoices for this supplier
            invoices = Invoice.objects.filter(supplier=selected_supplier).select_related('purchase_order').prefetch_related('issues').order_by('invoice_date', 'created_at')
            
            # Apply filters
            search_query = request.GET.get('search', '')
            payment_status = request.GET.get('payment_status', '')
            issue_id = request.GET.get('issue', '')
            date_from = request.GET.get('date_from', '')
            date_to = request.GET.get('date_to', '')
            
            if search_query:
                invoices = invoices.filter(
                    Q(invoice_number__icontains=search_query) |
                    Q(total_amount__icontains=search_query)
                )
            
            if payment_status:
                invoices = invoices.filter(payment_status=payment_status)
            
            if issue_id:
                invoices = invoices.filter(issues__id=issue_id)
            
            if date_from:
                try:
                    date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                    invoices = invoices.filter(invoice_date__gte=date_from_obj)
                except ValueError:
                    pass
            
            if date_to:
                try:
                    date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                    invoices = invoices.filter(invoice_date__lte=date_to_obj)
                except ValueError:
                    pass
            
            # Calculate total owed using the invoice's remaining_amount property
            for invoice in invoices:
                total_owed += max(0, invoice.remaining_amount)
        except Supplier.DoesNotExist:
            pass
    
    context = {
        'suppliers': suppliers,
        'selected_supplier': selected_supplier,
        'invoices': invoices,
        'total_owed': total_owed,
        'payment_methods': payment_methods,
        'issues': InvoiceIssue.objects.all(),
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


def supplier_create(request):
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        data = _supplier_form_data(request)
        if data['name'] and data['supplier_type']:
            try:
                supplier = Supplier.objects.create(cafe=cafe, **data)
                # دسته‌بندی‌های انتخابی
                categories = request.POST.getlist("categories")
                if categories:
                    supplier.categories.set(Category.objects.filter(id__in=categories))
                messages.success(request, f'تأمین‌کننده "{supplier.name}" با موفقیت ایجاد شد.')
                return get_cafe_redirect_url("supplier_list", request)
            except Exception as e:
                messages.error(request, f'خطا در ایجاد تأمین‌کننده: {str(e)}')
        else:
            messages.error(request, 'نام تأمین‌کننده و نوع تأمین‌کننده الزامی هستند.')
    return render(request, "supplier_form.html", {"supplier_types": Supplier.SUPPLIER_TYPE_CHOICES})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    return render(request, "supplier_detail.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_update(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    if request.method == "POST":
        data = _supplier_form_data(request)
        if not data['name']:
            messages.error(request, 'نام تأمین‌کننده الزامی است.')
            return render(request, "supplier_form.html", {"supplier": supplier, "supplier_types": Supplier.SUPPLIER_TYPE_CHOICES})
        for field, value in data.items():
            setattr(supplier, field, value)
        try:
            supplier.save()
            categories = request.POST.getlist("categories")
            supplier.categories.set(Category.objects.filter(id__in=categories))
            messages.success(request, f'تأمین‌کننده "{supplier.name}" با موفقیت بروزرسانی شد.')
            return get_cafe_redirect_url("supplier_list", request)
        except Exception as e:
            messages.error(request, f'خطا در بروزرسانی تأمین‌کننده: {str(e)}')
    return render(request, "supplier_form.html", {"supplier": supplier, "supplier_types": Supplier.SUPPLIER_TYPE_CHOICES})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_delete(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    if request.method == "POST":
        supplier.delete()
        return get_cafe_redirect_url("supplier_list", request)
    return render(request, "supplier_confirm_delete.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_list(request):
    # Ensure default issues exist
    default_issues = ['فاکتور رسمی نیست', 'فاکتور مهر ندارد', 'فاکتور ناقص است']
    for issue in default_issues:
        InvoiceIssue.objects.get_or_create(name=issue)
    
    issues = InvoiceIssue.objects.all()
    return render(request, "invoice_issue_list.html", {"issues": issues})


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            InvoiceIssue.objects.create(name=name)
            return get_cafe_redirect_url("invoice_issue_list", request)
    return render(request, "invoice_issue_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def invoice_issue_delete(request, issue_id):
    issue = get_object_or_404(InvoiceIssue, id=issue_id)
    if request.method == "POST":
        issue.delete()
        return get_cafe_redirect_url("invoice_issue_list", request)
    return render(request, "invoice_issue_confirm_delete.html", {"issue": issue})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_activate(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    supplier.status = "active"
    supplier.save()
    return get_cafe_redirect_url("supplier_detail", request, supplier_id=supplier.id)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_deactivate(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    supplier.status = "inactive"
    supplier.save()
    return get_cafe_redirect_url("supplier_detail", request, supplier_id=supplier.id)


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_list(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    evaluations = SupplierEvaluation.objects.filter(supplier=supplier)
    return render(
        request,
        "supplier_evaluation_list.html",
        {"supplier": supplier, "evaluations": evaluations},
    )


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_create(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    if request.method == "POST":
        SupplierEvaluation.objects.create(
            supplier=supplier,
            evaluation_date=timezone.now().date(),
            quality_score=_parse_decimal(request.POST.get("quality_score") or 0),
            delivery_score=_parse_decimal(request.POST.get("delivery_score") or 0),
            pricing_score=_parse_decimal(request.POST.get("pricing_score") or 0),
            comments=request.POST.get("comments", ""),
            evaluator=request.user,
        )
        return get_cafe_redirect_url("supplier_evaluation_list", request, supplier_id=supplier.id)
    return render(request, "supplier_evaluation_form.html", {"supplier": supplier})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_all_list(request):
    evaluations = SupplierEvaluation.objects.select_related("supplier")
    return render(
        request,
        "supplier_evaluation_list.html",
        {"evaluations": evaluations, "supplier": None},
    )


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_create_general(request):
    cafe = get_cafe_from_request(request)
    suppliers = Supplier.objects.all()
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    if request.method == "POST":
        supplier = Supplier.objects.filter(id=request.POST.get("supplier")).first()
        if supplier:
            SupplierEvaluation.objects.create(
                supplier=supplier,
                evaluation_date=timezone.now().date(),
                quality_score=_parse_decimal(request.POST.get("quality_score") or 0),
                delivery_score=_parse_decimal(request.POST.get("delivery_score") or 0),
                pricing_score=_parse_decimal(request.POST.get("pricing_score") or 0),
                comments=request.POST.get("comments", ""),
                evaluator=request.user,
            )
            return get_cafe_redirect_url("supplier_evaluation_all_list", request)
    return render(request, "supplier_evaluation_form.html", {"suppliers": suppliers})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_performance_report(request):
    evaluations = SupplierEvaluation.objects.select_related("supplier")
    return render(request, "supplier_performance_report.html", {"evaluations": evaluations})


