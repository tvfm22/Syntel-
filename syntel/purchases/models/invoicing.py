from decimal import Decimal
import uuid
from django.db import models
from django.utils import timezone


# Invoice Management Models
def _alias_invoice_kwargs(kwargs):
    if 'order' in kwargs:
        kwargs['purchase_order'] = kwargs.pop('order')
    for key in list(kwargs.keys()):
        if key.startswith('order__'):
            kwargs['purchase_' + key] = kwargs.pop(key)
    return kwargs


class InvoiceQuerySet(models.QuerySet):
    def filter(self, *args, **kwargs):
        return super().filter(*args, **_alias_invoice_kwargs(dict(kwargs)))

    def exclude(self, *args, **kwargs):
        return super().exclude(*args, **_alias_invoice_kwargs(dict(kwargs)))

    def get(self, *args, **kwargs):
        return super().get(*args, **_alias_invoice_kwargs(dict(kwargs)))

    def create(self, **kwargs):
        return super().create(**_alias_invoice_kwargs(dict(kwargs)))

    def get_or_create(self, defaults=None, **kwargs):
        return super().get_or_create(defaults=defaults, **_alias_invoice_kwargs(dict(kwargs)))

    def update_or_create(self, defaults=None, **kwargs):
        return super().update_or_create(defaults=defaults, **_alias_invoice_kwargs(dict(kwargs)))


class InvoiceManager(models.Manager):
    def get_queryset(self):
        return InvoiceQuerySet(self.model, using=self._db)

    def create(self, **kwargs):
        return self.get_queryset().create(**kwargs)

    def filter(self, *args, **kwargs):
        return self.get_queryset().filter(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.get_queryset().get(*args, **kwargs)


class Invoice(models.Model):
    STATUS_CHOICES = [
        ('draft', 'پیش‌نویس'),
        ('sent', 'ارسال شده'),
        ('approved', 'تأیید شده'),
        ('partially_paid', 'partial payment'),
        ('paid', 'پرداخت شده'),
        ('overdue', 'سررسید گذشته'),
        ('cancelled', 'لغو شده'),
    ]

    PAYMENT_STATUS_CHOICES = [
        ('unpaid', 'پرداخت نشده'),
        ('partial', 'پرداخت جزئی'),
        ('paid', 'پرداخت کامل'),
        ('overpaid', 'پرداخت اضافی'),
    ]

    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='invoices', verbose_name='کافه', null=True, blank=True)
    invoice_number = models.CharField(max_length=50, verbose_name='شماره فاکتور')
    purchase_order = models.OneToOneField('purchases.PurchaseOrder', on_delete=models.PROTECT, related_name='invoice', verbose_name='سفارش خرید', null=True, blank=True)
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices', verbose_name='تأمین‌کننده')
    store_name = models.CharField(max_length=200, blank=True, verbose_name='نام فروشگاه')
    
    # Invoice details
    invoice_date = models.DateField(default=timezone.localdate, verbose_name='تاریخ فاکتور')
    due_date = models.DateField(verbose_name='تاریخ سررسید')
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='نرخ مالیات (%)')
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='نرخ تخفیف (%)')
    shipping_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0, verbose_name='هزینه حمل و نقل')
    
    # Status and tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='وضعیت')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='unpaid', verbose_name='وضعیت پرداخت')
    
    # Financial tracking
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, verbose_name='جمع جزء')
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, verbose_name='مبلغ مالیات')
    discount_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, verbose_name='مبلغ تخفیف اقلام')
    cash_discount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, default=0, verbose_name='تخفیف پرداخت نقدی')
    total_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, verbose_name='مجموع کل')
    
    # Notes and tracking
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    issues = models.ManyToManyField('InvoiceIssue', blank=True, related_name='invoices', verbose_name='ایرادات')
    
    # Audit fields
    created_by = models.ForeignKey('purchases.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='created_invoices', verbose_name='ایجاد شده توسط')
    updated_by = models.ForeignKey('purchases.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='updated_invoices', verbose_name='ویرایش شده توسط')
    approved_by = models.ForeignKey('purchases.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_invoices', verbose_name='تأیید شده توسط')
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تأیید')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    objects = InvoiceManager()

    def __init__(self, *args, **kwargs):
        order = kwargs.pop('order', None)
        super().__init__(*args, **kwargs)
        if order is None:
            order = kwargs.pop('purchase_order', None)
        if order is not None:
            self.purchase_order = order

    @property
    def order(self):
        return self.purchase_order

    @order.setter
    def order(self, value):
        self.purchase_order = value

    @property
    def supplier_name(self):
        if self.supplier:
            return self.supplier.name
        return self.store_name or "نامشخص"

    def __str__(self):
        return self.invoice_number

    _FINANCIAL_FIELDS = frozenset({
        'subtotal', 'tax_amount', 'discount_amount', 'cash_discount',
        'total_amount', 'tax_rate', 'discount_rate', 'shipping_cost',
    })

    def save(self, *args, **kwargs):
        # Auto-generate invoice number if not provided
        if not self.invoice_number:
            self.invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
        if not self.due_date:
            self.due_date = self.invoice_date or timezone.localdate()

        update_fields = kwargs.get('update_fields')
        was_existing = self.pk is not None
        check_financial = was_existing and (
            update_fields is None or bool(set(update_fields) & self._FINANCIAL_FIELDS)
        )
        old_total = None
        if check_financial:
            old_total = (
                Invoice.objects.filter(pk=self.pk)
                .values_list('total_amount', flat=True)
                .first()
            )

        should_recalc = check_financial and (
            self.invoice_items.exists() or self.subtotal is not None
        )
        if should_recalc:
            items = list(self.invoice_items.all())
            subtotal = Decimal('0')
            item_tax_sum = Decimal('0')
            item_disc_sum = Decimal('0')
            for item in items:
                if item.quantity is not None and item.unit_price is not None:
                    subtotal += Decimal(str(item.quantity)) * Decimal(str(item.unit_price))
                else:
                    subtotal += Decimal(str(getattr(item, 'line_total', None) or 0))
                item_tax_sum += Decimal(str(item.tax_amount or 0))
                item_disc_sum += Decimal(str(item.discount_amount or 0))

            self.subtotal = subtotal
            tax_rate = Decimal(str(self.tax_rate or 0))
            discount_rate = Decimal(str(self.discount_rate or 0))
            rate_tax = (subtotal * tax_rate / Decimal('100')) if tax_rate else Decimal('0')
            rate_discount = (subtotal * discount_rate / Decimal('100')) if discount_rate else Decimal('0')

            self.tax_amount = item_tax_sum + rate_tax
            self.discount_amount = item_disc_sum + rate_discount

            shipping = Decimal(str(self.shipping_cost or 0))
            cash_disc = Decimal(str(self.cash_discount or 0))
            self.total_amount = (
                self.subtotal
                + self.tax_amount
                - self.discount_amount
                + shipping
                - cash_disc
            )
            if update_fields is not None:
                kwargs['update_fields'] = list(
                    set(update_fields) | {'subtotal', 'tax_amount', 'discount_amount', 'total_amount'}
                )

        super().save(*args, **kwargs)

        if check_financial and self.total_amount != old_total:
            self.update_payment_status()

    def is_overdue(self):
        """Check if invoice is overdue"""
        if self.due_date and self.payment_status != 'paid':
            return timezone.localdate() > self.due_date
        return False

    def get_days_overdue(self):
        """Return number of days overdue"""
        if self.due_date and self.payment_status != 'paid':
            days = (timezone.localdate() - self.due_date).days
            return max(0, days)
        return 0

    def can_be_approved(self):
        """Check if invoice can be approved"""
        return self.status in ('draft', 'sent')

    def can_be_paid(self):
        """Check if invoice can be marked as paid"""
        return (
            self.status in ('sent', 'approved', 'partially_paid', 'overdue')
            and self.get_remaining_amount() > 0
        )

    def payment_records(self):
        """همهٔ پرداخت‌های این فاکتور — از تخصیص صریح، کلید خارجی یا M2M."""
        from .payments import Payment
        return Payment.objects.filter(
            models.Q(allocations__invoice=self)
            | models.Q(invoices=self)
            | models.Q(invoice=self)
        ).distinct()

    def _legacy_m2m_allocated_amount(self, payment):
        """مبلغ سهم این فاکتور از یک پرداخت گروهیِ قدیمی (بدون PaymentAllocation)."""
        linked = list(payment.invoices.order_by('invoice_date', 'created_at', 'id'))
        if len(linked) <= 1:
            return Decimal(str(payment.amount or 0))
        remaining_to_allocate = Decimal(str(payment.amount or 0))
        total_caps = sum((Decimal(str(inv.total_amount or 0)) for inv in linked), Decimal('0'))
        if total_caps <= 0:
            share = remaining_to_allocate / Decimal(len(linked))
            return share
        for idx, inv in enumerate(linked):
            cap = max(Decimal('0'), Decimal(str(inv.total_amount or 0)))
            if idx == len(linked) - 1:
                alloc = remaining_to_allocate
            else:
                alloc = min(cap, remaining_to_allocate)
            if inv.pk == self.pk:
                return alloc
            remaining_to_allocate -= alloc
        return Decimal('0')

    def update_payment_status(self):
        """Update payment status based on payments"""
        total_paid = self.get_total_paid()
        total_amount = Decimal(str(self.total_amount)) if self.total_amount is not None else Decimal('0')

        if total_paid <= 0:
            self.payment_status = 'unpaid'
            if self.status in ('partially_paid', 'paid'):
                self.status = 'approved' if self.approved_at else 'sent'
        elif total_paid < total_amount:
            self.payment_status = 'partial'
            if self.status != 'cancelled':
                self.status = 'partially_paid'
        elif total_paid == total_amount:
            self.payment_status = 'paid'
            if self.status != 'cancelled':
                self.status = 'paid'
        else:
            self.payment_status = 'overpaid'
            if self.status != 'cancelled':
                self.status = 'paid'

        self.save(update_fields=['payment_status', 'status'])

    @property
    def paid_amount(self):
        return self.get_total_paid()

    @property
    def remaining_amount(self):
        return self.get_remaining_amount()

    def get_total_paid(self):
        """Get total amount paid without double-counting bulk/M2M payments."""
        from .payments import Payment, PaymentAllocation
        alloc_total = (
            PaymentAllocation.objects.filter(
                invoice=self,
                payment__payment_status='completed',
            ).aggregate(total=models.Sum('amount'))['total']
            or Decimal('0')
        )
        single_total = (
            Payment.objects.filter(
                invoice=self,
                payment_status='completed',
                allocations__isnull=True,
            ).aggregate(total=models.Sum('amount'))['total']
            or Decimal('0')
        )
        m2m_total = Decimal('0')
        legacy_m2m = Payment.objects.filter(
            invoices=self,
            invoice__isnull=True,
            allocations__isnull=True,
            payment_status='completed',
        ).distinct()
        for p in legacy_m2m:
            m2m_total += self._legacy_m2m_allocated_amount(p)
        return alloc_total + single_total + m2m_total

    def get_remaining_amount(self):
        """Get remaining amount to be paid"""
        total = Decimal(str(self.total_amount)) if self.total_amount is not None else Decimal('0')
        return max(Decimal('0'), total - self.get_total_paid())

    def can_add_payment(self):
        """Check if payment can be added to this invoice"""
        return self.payment_status != 'paid'

    class Meta:
        verbose_name = 'فاکتور'
        verbose_name_plural = 'فاکتورها'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['payment_status']),
            models.Index(fields=['due_date']),
            models.Index(fields=['total_amount']),
            models.Index(fields=['created_at', 'status']),
            models.Index(fields=['due_date', 'payment_status']),
            models.Index(fields=['cafe', 'status']),
            models.Index(fields=['cafe', '-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['cafe', 'invoice_number'],
                condition=models.Q(cafe__isnull=False),
                name='uniq_invoice_number_per_cafe',
            ),
        ]


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='invoice_items', verbose_name='فاکتور')
    order_item = models.OneToOneField('purchases.PurchaseOrderItem', on_delete=models.CASCADE, null=True, blank=True, related_name='invoice_item', verbose_name='آیتم سفارش')
    product = models.ForeignKey('purchases.Product', null=True, blank=True, on_delete=models.SET_NULL, related_name='invoice_items', verbose_name='محصول')
    
    # Item details (may differ from order)
    description = models.CharField(max_length=200, verbose_name='توضیحات')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='مقدار')
    unit_price = models.DecimalField(max_digits=16, decimal_places=2, verbose_name='قیمت واحد')
    
    # Tax and discount per item
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0, verbose_name='مبلغ مالیات')
    discount_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0, verbose_name='تخفیف پرداخت نقدی')
    
    line_total = models.DecimalField(max_digits=16, decimal_places=2, verbose_name='مجموع خط')
    
    # Additional fields
    requester = models.ForeignKey('purchases.Requester', null=True, blank=True, on_delete=models.SET_NULL, verbose_name='درخواست‌کننده')
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')

    def __init__(self, *args, **kwargs):
        total_price = kwargs.pop('total_price', None)
        super().__init__(*args, **kwargs)
        if total_price is not None:
            self.line_total = total_price

    @property
    def total_price(self):
        return self.line_total

    @total_price.setter
    def total_price(self, value):
        self.line_total = value

    def __str__(self):
        return f"{self.description} - {self.quantity} × {self.unit_price}"

    def save(self, *args, **kwargs):
        if self.quantity is not None and self.unit_price is not None:
            base_total = self.quantity * self.unit_price
            tax = self.tax_amount or 0
            discount = self.discount_amount or 0
            self.line_total = base_total + tax - discount
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'آیتم فاکتور'
        verbose_name_plural = 'آیتم‌های فاکتور'


# Invoice Issue model
class InvoiceIssue(models.Model):
    name = models.CharField(max_length=100, verbose_name='نام ایراد')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='invoice_issues', verbose_name='کافه', null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'ایراد فاکتور'
        verbose_name_plural = 'ایرادات فاکتور'
        unique_together = [['name', 'cafe']]


# Purchase Invoices model (optional)
class PurchaseInvoice(models.Model):
    request = models.OneToOneField('purchases.PurchaseRequest', on_delete=models.CASCADE)
    invoice_file = models.FileField(upload_to='invoices/', blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Invoice for {self.request}"
