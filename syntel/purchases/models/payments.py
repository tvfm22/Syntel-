from decimal import Decimal
import uuid
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify


# Payment Tracking System
class Payment(models.Model):
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'در انتظار'),
        ('completed', 'تکمیل شده'),
        ('failed', 'ناموفق'),
        ('cancelled', 'لغو شده'),
        ('refunded', 'بازپرداخت شده'),
    ]

    # Support for both single and multiple invoices
    invoice = models.ForeignKey('purchases.Invoice', on_delete=models.PROTECT, related_name='single_payments', null=True, blank=True, verbose_name='فاکتور')
    invoices = models.ManyToManyField('purchases.Invoice', related_name='payments', blank=True, verbose_name='فاکتورها')
    
    amount = models.DecimalField(max_digits=16, decimal_places=2, verbose_name='مبلغ پرداخت', validators=[MinValueValidator(0)])
    
    # Payment details
    payment_location = models.ForeignKey('PaymentLocation', null=True, blank=True, on_delete=models.SET_NULL, related_name='payments', verbose_name='محل پرداخت')
    payment_date = models.DateField(default=timezone.localdate, verbose_name='تاریخ پرداخت')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='completed', verbose_name='وضعیت پرداخت')
    
    # Reference information
    reference_number = models.CharField(max_length=100, blank=True, verbose_name='شماره مرجع')
    bank_name = models.CharField(max_length=100, blank=True, verbose_name='نام بانک')
    check_number = models.CharField(max_length=50, blank=True, verbose_name='شماره چک')
    card_last_four = models.CharField(max_length=4, blank=True, verbose_name='چهار رقم آخر کارت')
    
    # Notes and tracking
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    transaction_id = models.CharField(max_length=100, blank=True, unique=True, verbose_name='شناسه تراکنش')
    
    # Audit fields
    created_by = models.ForeignKey('purchases.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='created_payments', verbose_name='ایجاد شده توسط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __init__(self, *args, **kwargs):
        status_alias = kwargs.pop('status', None)
        super().__init__(*args, **kwargs)
        if status_alias is not None:
            self.payment_status = status_alias

    @property
    def status(self):
        return self.payment_status

    @status.setter
    def status(self, value):
        self.payment_status = value

    def __str__(self):
        if not self.pk:
            if self.invoice_id:
                return f"پرداخت {self.amount} برای فاکتور {self.invoice.invoice_number}"
            return f"پرداخت {self.amount}"
        alloc_qs = self.allocations.select_related('invoice')
        alloc_count = alloc_qs.count()
        if alloc_count == 1:
            return f"پرداخت {self.amount} برای فاکتور {alloc_qs.first().invoice.invoice_number}"
        if alloc_count > 1:
            return f"پرداخت گروهی {self.amount} برای {alloc_count} فاکتور"
        m2m_count = self.invoices.count()
        if m2m_count == 1 and not self.invoice_id:
            inv = self.invoices.first()
            return f"پرداخت {self.amount} برای فاکتور {inv.invoice_number}"
        if m2m_count > 0:
            total = len(self.get_related_invoices())
            return f"پرداخت گروهی {self.amount} برای {total} فاکتور"
        if self.invoice_id:
            return f"پرداخت {self.amount} برای فاکتور {self.invoice.invoice_number}"
        return f"پرداخت {self.amount}"

    def get_related_invoices(self):
        """Get all related invoices (single, M2M, and allocations)"""
        invoices = {}
        if self.invoice_id:
            invoices[self.invoice_id] = self.invoice
        if self.pk:
            for inv in self.invoices.all():
                invoices[inv.pk] = inv
            for alloc in self.allocations.select_related('invoice').all():
                invoices[alloc.invoice_id] = alloc.invoice
        return list(invoices.values())
    
    def distribute_amount_evenly(self):
        """Distribute payment amount evenly among all invoices"""
        invoices = self.get_related_invoices()
        if not invoices:
            return {}
        
        amount_per_invoice = Decimal(str(self.amount or 0)) / Decimal(len(invoices))
        return {invoice.id: amount_per_invoice for invoice in invoices}
    
    def distribute_amount_by_remaining(self):
        """Distribute payment amount based on remaining amounts of invoices"""
        invoices = self.get_related_invoices()
        if not invoices:
            return {}
        
        remaining_amounts = {}
        total_remaining = Decimal('0')
        for invoice in invoices:
            remaining = Decimal(str(invoice.get_remaining_amount() or 0))
            remaining_amounts[invoice.id] = remaining
            total_remaining += remaining
        
        if total_remaining == 0:
            return self.distribute_amount_evenly()
        
        amount_dec = Decimal(str(self.amount or 0))
        distribution = {}
        for invoice_id, remaining in remaining_amounts.items():
            proportion = remaining / total_remaining
            distribution[invoice_id] = amount_dec * proportion
        
        return distribution

    def save(self, *args, **kwargs):
        # Auto-generate transaction ID if not provided
        if not self.transaction_id:
            self.transaction_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"
        
        super().save(*args, **kwargs)
        
        related_ids = set()
        if self.invoice_id:
            related_ids.add(self.invoice_id)
        related_ids.update(self.invoices.values_list('id', flat=True))
        related_ids.update(self.allocations.values_list('invoice_id', flat=True))
        if related_ids:
            from .invoicing import Invoice
            with transaction.atomic():
                for inv in Invoice.objects.select_for_update().filter(id__in=related_ids).order_by('id'):
                    inv.update_payment_status()

    class Meta:
        verbose_name = 'پرداخت'
        verbose_name_plural = 'پرداخت‌ها'
        ordering = ['-payment_date', '-created_at']
        indexes = [
            models.Index(fields=['payment_date']),
            models.Index(fields=['payment_location']),
            models.Index(fields=['payment_status']),
            models.Index(fields=['amount']),
            models.Index(fields=['payment_date', 'payment_status']),
        ]


class PaymentAllocation(models.Model):
    """تخصیص صریح مبلغ یک پرداخت گروهی به هر فاکتور (جلوگیری از شمارش مضاعف)."""
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='allocations', verbose_name='پرداخت')
    invoice = models.ForeignKey('purchases.Invoice', on_delete=models.PROTECT, related_name='payment_allocations', verbose_name='فاکتور')
    amount = models.DecimalField(max_digits=16, decimal_places=2, verbose_name='مبلغ تخصیص‌یافته')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'تخصیص پرداخت'
        verbose_name_plural = 'تخصیص‌های پرداخت'
        constraints = [
            models.UniqueConstraint(fields=['payment', 'invoice'], name='uniq_payment_invoice_allocation'),
            models.CheckConstraint(check=models.Q(amount__gt=0), name='chk_payment_allocation_amount_gt_0'),
        ]

    def __str__(self):
        return f"تخصیص {self.amount} از پرداخت #{self.payment_id} به فاکتور {self.invoice.invoice_number}"


# Payment Location Configuration
class PaymentLocation(models.Model):
    name = models.CharField(max_length=100, verbose_name='نام محل پرداخت')
    code = models.CharField(max_length=20, blank=True, null=True, verbose_name='کد محل پرداخت')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='payment_locations', verbose_name='کافه', null=True, blank=True)
    
    # Configuration
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    requires_reference = models.BooleanField(default=False, verbose_name='نیاز به شماره مرجع')
    requires_bank_info = models.BooleanField(default=False, verbose_name='نیاز به اطلاعات بانکی')
    requires_check_info = models.BooleanField(default=False, verbose_name='نیاز به اطلاعات چک')
    requires_card_info = models.BooleanField(default=False, verbose_name='نیاز به اطلاعات کارت')
    
    # Display settings
    icon_class = models.CharField(max_length=50, blank=True, verbose_name='کلاس آیکون')
    display_order = models.PositiveIntegerField(default=0, verbose_name='ترتیب نمایش')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            base_code = (slugify(self.name, allow_unicode=True) or 'location')[:20]
            candidate = base_code
            suffix = 2
            existing = PaymentLocation.objects.filter(cafe=self.cafe)
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            while existing.filter(code=candidate).exists():
                suffix_str = f'-{suffix}'
                candidate = f'{base_code[:20 - len(suffix_str)]}{suffix_str}'
                suffix += 1
            self.code = candidate
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'محل پرداخت'
        verbose_name_plural = 'محل‌های پرداخت'
        ordering = ['display_order', 'name']
        unique_together = [['name', 'cafe'], ['code', 'cafe']]


# PaymentType model for payment methods
class PaymentType(models.Model):
    name = models.CharField(max_length=100, verbose_name='نام نوع پرداخت')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='payment_types', verbose_name='کافه', null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'نوع پرداخت'
        verbose_name_plural = 'انواع پرداخت'
        unique_together = [['name', 'cafe']]
