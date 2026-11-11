"""
Debt Management Views (مدیریت بدهی‌ها و پرداخت‌های مدیرعامل)
تفکیک‌شده از purchases/views.py برای بهبود ساختار معماری و نگهداری‌پذیری.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import Debt, DebtPayment, PaymentAccount, Supplier
from .views_helpers import (
    _parse_decimal,
    custom_login_required,
    get_cafe_from_request,
    get_cafe_redirect_url,
    role_required,
)

MOBILE_UA_RE = re.compile(
    r'(android|iphone|ipad|ipod|windows phone|opera mini|mobile|blackberry|iemobile|webos)',
    re.IGNORECASE,
)


def is_mobile_request(request):
    """تشخیص خودکار دستگاه موبایل از روی User-Agent — بدون نیاز به انتخاب کاربر"""
    ua = request.META.get('HTTP_USER_AGENT', '')
    return bool(MOBILE_UA_RE.search(ua))


def _redirect_preserving_query(request, url_name):
    """Redirect با حفظ پارامترهای query (مثلاً period)"""
    response = get_cafe_redirect_url(url_name, request)
    location = response.get('Location', '')
    if request.GET and location:
        location += ('&' if '?' in location else '?') + request.GET.urlencode()
    return redirect(location)


def _debt_redirect(request, url_name, mobile_url_name=None):
    """Redirect به صفحه بدهی با حفظ cafe_code — اگر فرم از نسخه موبایل آمده باشد (یا دستگاه موبایل باشد)، به همان برمی‌گردد"""
    if mobile_url_name and (request.GET.get("from") == "mobile" or is_mobile_request(request)):
        url_name = mobile_url_name
    return get_cafe_redirect_url(url_name, request)


def _parse_debt_date(raw):
    """پذیرش تاریخ شمسی (1404/05/17) و میلادی (2026-08-08) — مثل بقیه فرم‌های پروژه"""
    if not raw:
        return timezone.now().date()
    if '/' in raw:
        parts = raw.split('/')
        if len(parts) == 3:
            try:
                import jdatetime
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                return jdatetime.date(year, month, day).togregorian()
            except (ValueError, TypeError, AttributeError):
                pass
    parsed = parse_date(raw)
    if parsed:
        return parsed
    return timezone.now().date()


def _today_jalali():
    """امروز به شمسی (1404/05/17) — پیش‌فرض باکس تاریخ شمسی فرم بدهی"""
    try:
        import jdatetime
        j = jdatetime.date.today()
        return f"{j.year}/{j.month:02d}/{j.day:02d}"
    except ImportError:
        d = timezone.now().date()
        return f"{d.year}/{d.month:02d}/{d.day:02d}"


PERIOD_CHOICES = ['week', 'month', 'season', 'year']
PERIOD_DAYS = {'week': 7, 'month': 30, 'season': 90, 'year': 365}


def _filter_debt_payments_by_period(queryset, request):
    """فیلتر بازه زمانی پرداخت‌ها — دیفالت: هفته — لیست بدهی‌ها بدون فیلتر می‌مانند"""
    period = request.GET.get('period', 'week')
    if period not in PERIOD_CHOICES:
        period = 'week'
    since = timezone.now() - timedelta(days=PERIOD_DAYS[period])
    return queryset.filter(paid_at__gte=since), period


def _get_payment_accounts(cafe):
    """حساب‌های پرداخت فعال کافه — برای انتخاب بانک/حساب در ثبت پرداخت"""
    accounts = PaymentAccount.objects.filter(is_active=True)
    if cafe:
        accounts = accounts.filter(cafe=cafe)
    return accounts


def _parse_payment_account(request, cafe):
    """پارسه حساب پرداخت انتخابی فرم — فقط حساب فعال همان کافه پذیرفته می‌شود"""
    account_id = request.POST.get("account_id")
    if not account_id:
        return None
    return _get_payment_accounts(cafe).filter(id=account_id).first()


def _get_active_suppliers(cafe):
    """تأمین‌کنندگان فعال کافه — برای جستجو و انتخاب در فرم‌های ثبت بدهی"""
    suppliers = Supplier.objects.filter(status='active')
    if cafe:
        suppliers = suppliers.filter(cafe=cafe)
    return suppliers.order_by('name')


def _suppliers_list(cafe):
    """تأمین‌کنندگان فعال کافه برای سرچ سمت کلاینت (picker انتخاب تأمین‌کننده) — با json_script در قالب رندر می‌شود"""
    return [
        {
            'id': s.id,
            'name': s.name,
            'bank_name': s.bank_name or '',
            'bank_account': s.bank_account or '',
            'card_number': s.card_number or '',
            'sheba_number': s.sheba_number or '',
            'contact_person': s.contact_person or '',
            'mobile': s.mobile or '',
        }
        for s in _get_active_suppliers(cafe)
    ]


def _parse_supplier(request, cafe):
    """پارسه تأمین‌کننده انتخابی فرم بدهی — فقط تأمین‌کننده فعال همان کافه پذیرفته می‌شود"""
    supplier_id = request.POST.get("supplier_id")
    if not supplier_id:
        return None
    return _get_active_suppliers(cafe).filter(id=supplier_id).first()


@custom_login_required
@role_required(["admin"])
def debt_list(request):
    """لیست بدهی تأمین‌کنندگان — ادمین بدهی جدید تعریف می‌کند و لیست بدهی‌ها و پرداخت‌های مدیرعامل را می‌بیند"""
    cafe = get_cafe_from_request(request)

    if request.method == "POST":
        company = request.POST.get("company", "").strip()
        supplier = _parse_supplier(request, cafe)
        if supplier:
            company = supplier.name
        amount = _parse_decimal(request.POST.get("amount", ""))
        debt_date = _parse_debt_date(request.POST.get("debt_date"))
        description = request.POST.get("description", "").strip()
        priority = request.POST.get("priority", "medium")

        errors = []
        if not company:
            errors.append("نام تأمین‌کننده الزامی است")
        if amount is None or amount <= 0:
            errors.append("مبلغ بدهی معتبر نیست")
        if priority not in dict(Debt.PRIORITY_CHOICES):
            priority = "medium"
        if errors:
            messages.error(request, "؛ ".join(errors))
        else:
            Debt.objects.create(
                company=company,
                supplier=supplier,
                amount=amount,
                debt_date=debt_date,
                description=description,
                priority=priority,
                cafe=cafe,
                created_by=request.user,
            )
            messages.success(request, f"بدهی تأمین‌کننده {company} با موفقیت ثبت شد و به داشبورد مدیرعامل ارسال شد")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    if is_mobile_request(request):
        return _redirect_preserving_query(request, "mobile_debt_dashboard")

    debts = Debt.objects.select_related("supplier", "paid_by", "created_by").all()
    if cafe:
        debts = debts.filter(cafe=cafe)

    pending_debts = list(debts.filter(status="pending"))
    all_payments = DebtPayment.objects.select_related("debt__account", "debt__supplier", "paid_by", "account")
    if cafe:
        all_payments = all_payments.filter(cafe=cafe)
    payments, selected_period = _filter_debt_payments_by_period(all_payments, request)
    context = {
        "pending_debts": pending_debts,
        "payments": payments,
        "priority_choices": Debt.PRIORITY_CHOICES,
        "today": timezone.now().date(),
        "today_jalali": _today_jalali(),
        "selected_period": selected_period,
        "payment_accounts": _get_payment_accounts(cafe),
        "suppliers_data": _suppliers_list(cafe),
        "total_pending_amount": sum((d.remaining for d in pending_debts), Decimal("0")),
        "total_paid_amount": payments.aggregate(total=Sum("amount"))["total"] or 0,
    }
    return render(request, "debt_list.html", context)


@custom_login_required
@role_required(["ceo", "manager"])
def ceo_debt_payments(request):
    """داشبورد مدیرعامل — مشاهده بدهی‌های در انتظار و لیست پرداخت‌های ثبت شده"""
    cafe = get_cafe_from_request(request)

    if is_mobile_request(request):
        return _redirect_preserving_query(request, "mobile_debt_dashboard")

    debts = Debt.objects.select_related("supplier", "paid_by", "created_by").all()
    if cafe:
        debts = debts.filter(cafe=cafe)

    pending_debts = list(debts.filter(status="pending"))
    all_payments = DebtPayment.objects.select_related("debt__account", "debt__supplier", "paid_by", "account")
    if cafe:
        all_payments = all_payments.filter(cafe=cafe)
    payments, selected_period = _filter_debt_payments_by_period(all_payments, request)
    context = {
        "pending_debts": pending_debts,
        "payments": payments,
        "priority_choices": Debt.PRIORITY_CHOICES,
        "selected_period": selected_period,
        "payment_accounts": _get_payment_accounts(cafe),
        "suppliers_data": _suppliers_list(cafe),
        "total_pending_amount": sum((d.remaining for d in pending_debts), Decimal("0")),
    }
    return render(request, "ceo_debt_payments.html", context)


@custom_login_required
@role_required(["ceo", "manager"])
def debt_pay(request, debt_id):
    """ثبت پرداخت بدهی توسط مدیرعامل — مبلغ پرداختی، تصویر اختیاری و ثبت خودکار تاریخ سیستم"""
    cafe = get_cafe_from_request(request)
    debt = get_object_or_404(Debt, id=debt_id)
    if cafe and debt.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")
    if debt.status != "pending":
        messages.warning(request, "این بدهی قبلاً پرداخت شده است")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        paid_amount = _parse_decimal(request.POST.get("paid_amount", ""))
        if paid_amount is None or paid_amount <= 0:
            messages.error(request, "مبلغ پرداختی معتبر نیست")
            return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

        account = _parse_payment_account(request, cafe)
        photo = request.FILES.get("payment_photo")

        DebtPayment.objects.create(
            debt=debt,
            company=debt.company,
            amount=paid_amount,
            paid_at=timezone.now(),
            paid_by=request.user,
            account=account,
            cafe=debt.cafe,
            payment_photo=photo,
            description=debt.description,
        )

        total_paid = debt.paid_total
        debt.paid_amount = total_paid
        debt.paid_at = timezone.now()
        debt.paid_by = request.user
        debt.account = account
        if photo:
            debt.payment_photo = photo
        if debt.remaining <= 1_000_000:
            debt.status = "paid"
        debt.save()
        if debt.status == "paid":
            messages.success(request, f"پرداخت بدهی تأمین‌کننده {debt.company} با موفقیت ثبت و تایید شد")
        else:
            messages.success(request, f"پرداخت جزئی تأمین‌کننده {debt.company} ثبت شد — باقی‌مانده: {debt.remaining:,} تومان")

    return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")


@custom_login_required
@role_required(["ceo", "manager"])
def debt_pay_offlist(request):
    """ثبت پرداخت خارج از لیست بدهی‌ها توسط مدیرعامل — بدون نیاز به بدهی تعریف‌شده"""
    cafe = get_cafe_from_request(request)

    if request.method == "POST":
        company = request.POST.get("company", "").strip()
        supplier = _parse_supplier(request, cafe)
        if supplier:
            company = supplier.name
        amount = _parse_decimal(request.POST.get("amount", ""))
        errors = []
        if not company:
            errors.append("نام تأمین‌کننده الزامی است")
        if amount is None or amount <= 0:
            errors.append("مبلغ پرداختی معتبر نیست")
        if errors:
            messages.error(request, "؛ ".join(errors))
        else:
            photo = request.FILES.get("payment_photo")
            account = _parse_payment_account(request, cafe)
            debt = Debt.objects.create(
                company=company,
                supplier=supplier,
                amount=amount,
                description=request.POST.get("description", "").strip(),
                debt_date=timezone.now().date(),
                status="paid",
                paid_amount=amount,
                paid_at=timezone.now(),
                paid_by=request.user,
                account=account,
                is_off_list=True,
                cafe=cafe,
                created_by=request.user,
            )
            DebtPayment.objects.create(
                debt=None,
                company=company,
                amount=amount,
                paid_at=timezone.now(),
                paid_by=request.user,
                account=account,
                cafe=cafe,
                payment_photo=photo,
                description=request.POST.get("description", "").strip(),
            )
            if photo:
                debt.payment_photo = photo
                debt.save(update_fields=["payment_photo"])
            messages.success(request, f"پرداخت خارج از لیست تأمین‌کننده {company} با موفقیت ثبت شد")

    return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")


@custom_login_required
@role_required(["admin"])
def payment_accounts(request):
    """تعریف حساب‌های پرداخت کافه/رستوران توسط ادمین — دکمه در گوشه صفحه لیست بدهی"""
    cafe = get_cafe_from_request(request)

    if request.method == "POST":
        action = request.POST.get("action", "create")
        if action == "delete":
            account_id = request.POST.get("account_id")
            PaymentAccount.objects.filter(id=account_id, cafe=cafe).update(is_active=False)
            messages.success(request, "حساب پرداخت حذف شد")
        else:
            bank_name = request.POST.get("bank_name", "").strip()
            account_name = request.POST.get("account_name", "").strip()
            account_number = request.POST.get("account_number", "").strip()
            card_number = request.POST.get("card_number", "").strip()
            if not bank_name:
                messages.error(request, "نام بانک الزامی است")
            else:
                PaymentAccount.objects.create(
                    cafe=cafe, bank_name=bank_name, account_name=account_name,
                    account_number=account_number, card_number=card_number,
                )
                messages.success(request, f"حساب پرداخت {bank_name} ثبت شد")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    return render(request, "payment_accounts.html", {"payment_accounts": _get_payment_accounts(cafe)})


@custom_login_required
@role_required(["admin"])
def debt_edit(request, debt_id):
    """ویرایش بدهی توسط ادمین — تغییرات برای مدیرعامل هم اعمال می‌شود"""
    cafe = get_cafe_from_request(request)
    debt = get_object_or_404(Debt, id=debt_id)
    if cafe and debt.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        company = request.POST.get("company", "").strip()
        supplier = _parse_supplier(request, cafe)
        if supplier:
            company = supplier.name
        amount = _parse_decimal(request.POST.get("amount", ""))
        debt_date = _parse_debt_date(request.POST.get("debt_date"))
        description = request.POST.get("description", "").strip()
        priority = request.POST.get("priority", "medium")

        errors = []
        if not company:
            errors.append("نام تأمین‌کننده الزامی است")
        if amount is None or amount <= 0:
            errors.append("مبلغ بدهی معتبر نیست")
        if priority not in dict(Debt.PRIORITY_CHOICES):
            priority = "medium"
        if errors:
            messages.error(request, "؛ ".join(errors))
        else:
            debt.company = company
            debt.supplier = supplier
            debt.amount = amount
            debt.debt_date = debt_date
            debt.description = description
            debt.priority = priority
            debt.save()
            messages.success(request, f"بدهی تأمین‌کننده {company} ویرایش شد و برای مدیرعامل هم اعمال شد")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    debt_date_jalali = None
    if debt.debt_date:
        try:
            import jdatetime
            j = jdatetime.date.fromgregorian(date=debt.debt_date)
            debt_date_jalali = f"{j.year}/{j.month:02d}/{j.day:02d}"
        except Exception:
            pass
    return render(request, "debt_edit.html", {
        "debt": debt,
        "priority_choices": Debt.PRIORITY_CHOICES,
        "today_jalali": _today_jalali(),
        "debt_date_jalali": debt_date_jalali,
        "suppliers_data": _suppliers_list(cafe),
    })


@custom_login_required
@role_required(["admin"])
def debt_delete(request, debt_id):
    """حذف بدهی توسط ادمین — بدهی‌هایی که پرداخت دارند قابل حذف نیستند"""
    cafe = get_cafe_from_request(request)
    debt = get_object_or_404(Debt, id=debt_id)
    if cafe and debt.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        if debt.payments.exists():
            messages.error(request, f"بدهی تأمین‌کننده {debt.company} پرداخت شده است و قابل حذف نیست")
        else:
            name = debt.company
            debt.delete()
            messages.success(request, f"بدهی تأمین‌کننده {name} حذف شد و از داشبورد مدیرعامل هم حذف شد")
    return _debt_redirect(request, "debt_list", mobile_url_name="mobile_debt_dashboard")


@custom_login_required
@role_required(["ceo", "manager"])
def debt_payment_edit(request, payment_id):
    """ویرایش پرداخت توسط مدیرعامل — مبلغ، حساب و توضیحات؛ وضعیت بدهی دوباره محاسبه می‌شود"""
    cafe = get_cafe_from_request(request)
    payment = get_object_or_404(DebtPayment, id=payment_id)
    if cafe and payment.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        amount = _parse_decimal(request.POST.get("amount", ""))
        if amount is None or amount <= 0:
            messages.error(request, "مبلغ پرداختی معتبر نیست")
            return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

        payment.amount = amount
        payment.description = request.POST.get("description", "").strip()
        payment.account = _parse_payment_account(request, cafe)
        photo = request.FILES.get("payment_photo")
        if photo:
            payment.payment_photo = photo
        payment.save()

        debt = payment.debt
        if debt:
            total_paid = debt.paid_total
            debt.paid_amount = total_paid
            debt.account = payment.account or debt.account
            if debt.remaining <= 1_000_000:
                debt.status = "paid"
                debt.paid_at = timezone.now()
                debt.paid_by = payment.paid_by
            else:
                debt.status = "pending"
                debt.paid_at = None
                debt.paid_by = None
            debt.save()
        messages.success(request, f"پرداخت تأمین‌کننده {payment.company} ویرایش شد")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    return render(request, "debt_payment_edit.html", {
        "payment": payment,
        "payment_accounts": _get_payment_accounts(cafe),
    })


@custom_login_required
@role_required(["ceo", "manager"])
def debt_payment_delete(request, payment_id):
    """حذف پرداخت توسط مدیرعامل — رسید آپلود شده هم حذف می‌شود؛ وضعیت بدهی دوباره محاسبه می‌شود"""
    cafe = get_cafe_from_request(request)
    payment = get_object_or_404(DebtPayment, id=payment_id)
    if cafe and payment.cafe != cafe:
        messages.error(request, "دسترسی غیرمجاز")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    if request.method == "POST":
        debt = payment.debt
        company = payment.company

        if payment.payment_photo:
            payment.payment_photo.delete(save=False)

        payment.delete()

        if debt:
            total_paid = debt.paid_total
            debt.paid_amount = total_paid
            if debt.remaining <= 1_000_000 and total_paid > 0:
                debt.status = "paid"
                debt.paid_at = timezone.now()
                last_payment = debt.payments.order_by('-paid_at').first()
                if last_payment:
                    debt.paid_by = last_payment.paid_by
                    debt.account = last_payment.account
                    debt.payment_photo = last_payment.payment_photo
            else:
                debt.status = "pending"
                debt.paid_at = None
                debt.paid_by = None
                debt.account = None
                debt.payment_photo = None
            debt.save()

        messages.success(request, f"پرداخت تأمین‌کننده {company} حذف شد")
        return _debt_redirect(request, "ceo_debt_payments", mobile_url_name="mobile_debt_dashboard")

    return render(request, "debt_payment_confirm_delete.html", {"payment": payment})


@custom_login_required
@role_required(["admin", "ceo", "manager"])
def mobile_debt_dashboard(request):
    """داشبورد موبایل لیست بدهی — رابط لمسی برای ادمین و مدیرعامل (admin/ceo/manager)
    اگر درخواست از دسکتاپ بیاید، به نسخه دسکتاپ همان نقش برمی‌گردد"""
    if not is_mobile_request(request):
        target = "debt_list" if request.user.role == "admin" else "ceo_debt_payments"
        return _redirect_preserving_query(request, target)

    cafe = get_cafe_from_request(request)
    debts = Debt.objects.select_related("supplier", "paid_by", "created_by").all()
    if cafe:
        debts = debts.filter(cafe=cafe)

    pending_debts = list(debts.filter(status="pending"))
    payments = DebtPayment.objects.select_related("debt__account", "debt__supplier", "paid_by", "account")
    if cafe:
        payments = payments.filter(cafe=cafe)
    context = {
        "pending_debts": pending_debts,
        "payments": payments,
        "payment_accounts": _get_payment_accounts(cafe),
        "suppliers_data": _suppliers_list(cafe),
        "total_pending_amount": sum((d.remaining for d in pending_debts), Decimal("0")),
        "today": timezone.now().date(),
        "today_jalali": _today_jalali(),
    }
    return render(request, "mobile_debt_dashboard.html", context)
