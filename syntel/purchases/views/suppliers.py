from __future__ import annotations

from ..models import Category
from ..models import Invoice
from ..models import InvoiceIssue
from ..models import PaymentLocation
from ..models import Supplier
from ..models import SupplierEvaluation
from .helpers import _allocate_payment_across_invoices
from .helpers import _parse_decimal
from .helpers import _parse_invoice_date
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from decimal import Decimal
from django.contrib import messages
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST


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
                        .exclude(status__in=['cancelled', 'draft'])
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
                        if allocated_invoices:
                            messages.success(
                                request,
                                f'پرداخت {amount:,} ریال برای {len(allocated_invoices)} فاکتور ثبت شد.',
                            )
                        else:
                            messages.warning(request, 'هیچ فاکتور پرداخت‌نشده‌ای برای تسویه یافت نشد.')
                else:
                    all_invoices = list(
                        Invoice.objects.select_for_update()
                        .filter(cafe=cafe, supplier=selected_supplier)
                        .exclude(status__in=['cancelled', 'draft'])
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
            ).exclude(status__in=['cancelled', 'draft']).select_related('purchase_order').prefetch_related('issues').order_by('invoice_date', 'created_at')

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
                supplier = Supplier.objects.create(cafe=cafe, created_by=request.user, **data)
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
        try:
            supplier.delete()
            messages.success(request, f'تأمین‌کننده "{supplier.name}" با موفقیت حذف شد.')
        except models.ProtectedError:
            messages.error(request, 'این تأمین‌کننده دارای سوابق مرتبط است و امکان حذف آن وجود ندارد.')
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


def _parse_rating(value):
    raw = (value or "").strip()
    if raw.isdigit():
        num = int(raw)
        if 1 <= num <= 5:
            return num
    return None


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def supplier_evaluation_create(request, supplier_id):
    supplier = get_scoped_object_or_404(Supplier, request, id=supplier_id)
    suppliers = filter_by_cafe(Supplier.objects.all(), request)
    if request.method == "POST":
        eval_date = _parse_invoice_date(request.POST.get("evaluation_date")) or timezone.localdate()
        eval_type = (request.POST.get("evaluation_type") or "manual").strip()
        if eval_type not in dict(SupplierEvaluation.EVALUATION_TYPE_CHOICES):
            eval_type = "manual"
        try:
            with transaction.atomic():
                SupplierEvaluation.objects.create(
                    supplier=supplier,
                    evaluation_type=eval_type,
                    evaluation_date=eval_date,
                    quality_rating=_parse_rating(request.POST.get("quality_rating")),
                    delivery_rating=_parse_rating(request.POST.get("delivery_rating")),
                    price_rating=_parse_rating(request.POST.get("price_rating")),
                    communication_rating=_parse_rating(request.POST.get("communication_rating")),
                    strengths=request.POST.get("strengths", ""),
                    weaknesses=request.POST.get("weaknesses", ""),
                    recommendations=request.POST.get("recommendations", ""),
                    comments=request.POST.get("comments", ""),
                    evaluator=request.user,
                )
            messages.success(request, "ارزیابی تأمین‌کننده با موفقیت ثبت شد.")
            return get_cafe_redirect_url("supplier_evaluation_list", request, supplier_id=supplier.id)
        except IntegrityError:
            messages.error(request, "ارزیابی تکراری برای این تأمین‌کننده در این تاریخ و نوع قبلاً ثبت شده است.")
    return render(request, "supplier_evaluation_form.html", {"supplier": supplier, "suppliers": suppliers})


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
    suppliers = filter_by_cafe(Supplier.objects.all(), request)
    if request.method == "POST":
        sup_raw = (request.POST.get("supplier") or "").strip()
        if sup_raw.isdigit():
            supplier = get_scoped_object_or_404(Supplier, request, id=int(sup_raw))
            eval_date = _parse_invoice_date(request.POST.get("evaluation_date")) or timezone.localdate()
            eval_type = (request.POST.get("evaluation_type") or "manual").strip()
            if eval_type not in dict(SupplierEvaluation.EVALUATION_TYPE_CHOICES):
                eval_type = "manual"
            try:
                with transaction.atomic():
                    SupplierEvaluation.objects.create(
                        supplier=supplier,
                        evaluation_type=eval_type,
                        evaluation_date=eval_date,
                        quality_rating=_parse_rating(request.POST.get("quality_rating")),
                        delivery_rating=_parse_rating(request.POST.get("delivery_rating")),
                        price_rating=_parse_rating(request.POST.get("price_rating")),
                        communication_rating=_parse_rating(request.POST.get("communication_rating")),
                        strengths=request.POST.get("strengths", ""),
                        weaknesses=request.POST.get("weaknesses", ""),
                        recommendations=request.POST.get("recommendations", ""),
                        comments=request.POST.get("comments", ""),
                        evaluator=request.user,
                    )
                messages.success(request, "ارزیابی تأمین‌کننده با موفقیت ثبت شد.")
                return get_cafe_redirect_url("supplier_evaluation_all_list", request)
            except IntegrityError:
                messages.error(request, "ارزیابی تکراری برای این تأمین‌کننده در این تاریخ و نوع قبلاً ثبت شده است.")
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
