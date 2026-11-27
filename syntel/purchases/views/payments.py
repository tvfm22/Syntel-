from __future__ import annotations

from ..models import Invoice
from ..models import Notification
from ..models import Payment
from ..models import PaymentLocation
from .helpers import _allocate_payment_across_invoices
from .helpers import _parse_decimal
from .helpers import _parse_invoice_date
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import get_scoped_locked_object_or_404
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone


def _scoped_payment_or_404(request, payment_id):
    """بازیابی پرداخت در محدودهٔ کافهٔ فعال (شکست بسته بدون کافه)."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    qs = Payment.objects.filter(
        Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe) | Q(allocations__invoice__cafe=cafe)
    ).distinct()
    return get_object_or_404(qs, id=payment_id)


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
        .prefetch_related("invoices", "allocations__invoice")
        .filter(
            Q(invoice__cafe=cafe) | Q(invoices__cafe=cafe) | Q(allocations__invoice__cafe=cafe)
        )
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

    # Calculate total without M2M row multiplication
    total_amount = (
        Payment.objects.filter(id__in=payments.values('id')).aggregate(total=Sum('amount'))['total']
        or 0
    )

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
        with transaction.atomic():
            scoped_payment = _scoped_payment_or_404(request, payment_id)
            locked_payment = Payment.objects.select_for_update().get(pk=scoped_payment.pk)
            if "amount" in request.POST:
                amt = _parse_decimal(request.POST.get("amount"))
                if amt is not None and amt >= 0:
                    locked_payment.amount = amt
            if "payment_date" in request.POST:
                pdate = _parse_invoice_date(request.POST.get("payment_date"))
                if pdate is not None:
                    locked_payment.payment_date = pdate
            if "payment_location" in request.POST:
                loc_raw = (request.POST.get("payment_location") or "").strip()
                if loc_raw.isdigit():
                    locked_payment.payment_location = PaymentLocation.objects.filter(
                        id=int(loc_raw), cafe=cafe
                    ).first()
                else:
                    locked_payment.payment_location = None
            if "reference_number" in request.POST:
                locked_payment.reference_number = request.POST.get("reference_number", "")
            if "notes" in request.POST:
                locked_payment.notes = request.POST.get("notes", "")
            locked_payment.save()
            related_ids = [inv.id for inv in locked_payment.get_related_invoices()]
            if related_ids:
                for inv in Invoice.objects.select_for_update().filter(id__in=related_ids).order_by('id'):
                    inv.update_payment_status()
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
