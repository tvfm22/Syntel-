from __future__ import annotations

from ..models import Category
from ..models import Invoice
from ..models import InvoiceIssue
from ..models import InvoiceItem
from ..models import Payment
from ..models import PaymentLocation
from ..models import PaymentType
from ..models import Product
from ..models import PurchaseOrder
from ..models import Requester
from ..models import Supplier
from ..models import User
from ..services.inventory import InventoryServiceError
from .helpers import _parse_decimal
from .helpers import _parse_invoice_date
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import get_scoped_locked_object_or_404
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from datetime import datetime
from decimal import Decimal
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST


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
            parsed = _parse_decimal(value)
            return parsed if parsed is not None else Decimal('0')

        # محاسبه مبالغ از اقلام فاکتور (پیمایش همه کلیدهای description_<idx> حتی با حذف سطر میانی)
        desc_indices = sorted({
            int(k[len("description_"):])
            for k in request.POST.keys()
            if k.startswith("description_") and k[len("description_"):].isdigit()
        })

        subtotal = Decimal('0')
        total_tax = Decimal('0')
        total_discount = Decimal('0')
        for i in desc_indices:
            description = (request.POST.get(f"description_{i}") or "").strip()
            if not description:
                continue

            quantity_parsed = _parse_decimal(request.POST.get(f"quantity_{i}", 0))
            quantity = quantity_parsed if quantity_parsed is not None else Decimal('0')
            unit_price = parse_money(request.POST.get(f"unit_price_{i}", 0))
            tax_amount = parse_money(request.POST.get(f"tax_amount_{i}", 0))
            discount_amount = parse_money(request.POST.get(f"discount_amount_{i}", 0))
            requester_id = (request.POST.get(f"requester_{i}") or "").strip()
            requester = None
            if requester_id.isdigit():
                requester = Requester.objects.filter(id=int(requester_id), cafe=cafe).first()

            product = None
            product_id_raw = (request.POST.get(f"product_id_{i}") or "").strip()
            if product_id_raw.isdigit():
                product = Product.objects.filter(id=int(product_id_raw), cafe=cafe).first()
            if product is None:
                product = Product.objects.filter(name__iexact=description, cafe=cafe).first()

            base_total = quantity * unit_price
            line_total = base_total + tax_amount - discount_amount

            subtotal += base_total
            total_tax += tax_amount
            total_discount += discount_amount

            items_data.append({
                'description': description,
                'product': product,
                'quantity': quantity,
                'unit_price': unit_price,
                'tax_amount': tax_amount,
                'discount_amount': discount_amount,
                'requester': requester,
                'total': line_total
            })

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
                    product=item['product'],
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
                        invoice.update_payment_status()
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
            new_indices = sorted({
                int(k[len("description_new_"):])
                for k in request.POST.keys()
                if k.startswith("description_new_") and k[len("description_new_"):].isdigit()
            })
            for i in new_indices:
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

            # بروزرسانی پرداخت‌های موجود (فقط پرداخت‌های تک‌فاکتور همین فاکتور؛ پرداخت گروهی مشترک دست‌نخورده می‌ماند)
            for payment in invoice.payment_records():
                is_shared = (
                    payment.allocations.count() > 1
                    or payment.invoices.count() > 1
                    or (payment.invoice_id is not None and payment.invoice_id != invoice.id)
                )
                if is_shared:
                    continue

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

            # افزودن پرداخت‌های جدید با کلید خارجی مستقیم (برای جلوگیری از حذف فاکتور پرداخت‌شده)
            new_pay_indices = sorted({
                int(k[len("payment_date_new_"):])
                for k in request.POST.keys()
                if k.startswith("payment_date_new_") and k[len("payment_date_new_"):].isdigit()
            })
            for i in new_pay_indices:
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
                            Payment.objects.create(
                                invoice=invoice,
                                amount=payment_amount,
                                payment_date=payment_date,
                                payment_location=loc,
                                created_by=request.user,
                                payment_status='completed'
                            )

            if invoice.subtotal is None:
                invoice.subtotal = Decimal('0')
            invoice.updated_by = request.user
            invoice.save()
            invoice.update_payment_status()

            today_local = timezone.localdate()
            if (
                invoice.status not in ('draft', 'cancelled')
                and invoice.due_date
                and invoice.due_date < today_local
                and invoice.payment_status in ('unpaid', 'partial')
            ):
                invoice.status = 'overdue'
                invoice.save(update_fields=['status'])

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
    with transaction.atomic():
        invoice = get_scoped_locked_object_or_404(Invoice, request, id=invoice_id)
        if invoice.status != "draft":
            messages.warning(request, "فقط فاکتورهای پیش‌نویس قابل ارسال هستند.")
            return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)
        invoice.status = "sent"
        invoice.updated_by = request.user
        invoice.save(update_fields=["status", "updated_by", "updated_at"])
    messages.success(request, f"فاکتور {invoice.invoice_number} ارسال شد.")
    return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)


@custom_login_required
@role_required(["manager", "finance_manager", "ceo"])
@require_POST
def invoice_approve(request, invoice_id):
    with transaction.atomic():
        invoice = get_scoped_locked_object_or_404(Invoice, request, id=invoice_id)
        if not invoice.can_be_approved():
            messages.warning(request, "این فاکتور در وضعیت قابل تأیید نیست.")
            return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)
        invoice.status = "approved"
        invoice.approved_by = request.user
        invoice.approved_at = timezone.now()
        invoice.updated_by = request.user
        invoice.save(update_fields=["status", "approved_by", "approved_at", "updated_by", "updated_at"])
    messages.success(request, f"فاکتور {invoice.invoice_number} با موفقیت تأیید شد.")
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
    try:
        with transaction.atomic():
            invoice = get_scoped_locked_object_or_404(Invoice, request, id=invoice_id)
            if invoice.status in ("paid", "partially_paid") or invoice.get_total_paid() > 0:
                messages.error(request, "فاکتور دارای پرداخت قابل لغو نیست.")
                return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)
            if invoice.status == "cancelled":
                messages.info(request, "این فاکتور قبلاً لغو شده است.")
                return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice.id)
            invoice.status = "cancelled"
            invoice.updated_by = request.user
            invoice.save(update_fields=["status", "updated_by", "updated_at"])
    except InventoryServiceError as exc:
        messages.error(request, str(exc))
        return get_cafe_redirect_url("invoice_detail", request, invoice_id=invoice_id)
    messages.success(request, f"فاکتور {invoice.invoice_number} لغو شد.")
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
