from decimal import Decimal
import uuid
from django.db import models
from django.utils import timezone


class Supplier(models.Model):
    STATUS_CHOICES = [
        ('active', 'فعال'),
        ('inactive', 'غیرفعال'),
        ('blacklisted', 'لیست سیاه'),
        ('pending', 'در انتظار تأیید'),
    ]

    SUPPLIER_TYPE_CHOICES = [
        ('individual', 'شخص حقیقی'),
        ('company', 'شرکت'),
        ('cooperative', 'تعاونی'),
        ('government', 'دولتی'),
        ('other', 'سایر'),
    ]

    # Basic Information
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='suppliers', verbose_name='کافه', null=True, blank=True)
    name = models.CharField(max_length=200, verbose_name='نام تأمین‌کننده')
    supplier_code = models.CharField(max_length=20, blank=True, verbose_name='کد تأمین‌کننده')
    supplier_type = models.CharField(max_length=20, choices=SUPPLIER_TYPE_CHOICES, default='company', verbose_name='نوع تأمین‌کننده')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', verbose_name='وضعیت')

    # Contact Information
    contact_person = models.CharField(max_length=100, blank=True, verbose_name='شخص تماس')
    phone = models.CharField(max_length=20, blank=True, verbose_name='تلفن')
    mobile = models.CharField(max_length=20, blank=True, verbose_name='موبایل')
    email = models.EmailField(blank=True, verbose_name='ایمیل')
    website = models.URLField(blank=True, verbose_name='وب‌سایت')

    # Address Information
    address = models.TextField(blank=True, verbose_name='آدرس')
    city = models.CharField(max_length=100, blank=True, verbose_name='شهر')
    state = models.CharField(max_length=100, blank=True, verbose_name='استان')
    postal_code = models.CharField(max_length=20, blank=True, verbose_name='کد پستی')
    country = models.CharField(max_length=100, default='ایران', verbose_name='کشور')

    # Business Information
    tax_id = models.CharField(max_length=50, blank=True, verbose_name='شناسه مالیاتی')
    registration_number = models.CharField(max_length=50, blank=True, verbose_name='شماره ثبت')
    business_license = models.CharField(max_length=100, blank=True, verbose_name='مجوز کسب')

    # Banking Information
    bank_name = models.CharField(max_length=100, blank=True, verbose_name='نام بانک')
    bank_account = models.CharField(max_length=50, blank=True, verbose_name='شماره حساب')
    bank_routing = models.CharField(max_length=50, blank=True, verbose_name='کد شعبه/شبا')
    card_number = models.CharField(max_length=20, blank=True, verbose_name='شماره کارت')
    sheba_number = models.CharField(max_length=30, blank=True, verbose_name='شماره شبا')

    # Performance Tracking
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0, verbose_name='امتیاز عملکرد کلی')
    total_orders = models.PositiveIntegerField(default=0, verbose_name='تعداد کل سفارشات')
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='مجموع مبلغ سفارشات')
    on_time_delivery_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='نرخ تحویل به موقع (%)')
    quality_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0, verbose_name='امتیاز کیفیت')
    avg_delivery_delay_days = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='میانگین تأخیر تحویل (روز)')
    payment_reliability_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='امتیاز قابلیت اطمینان پرداخت')
    last_performance_update = models.DateTimeField(null=True, blank=True, verbose_name='آخرین بروزرسانی عملکرد')

    # Payment Terms
    payment_terms = models.TextField(blank=True, verbose_name='شرایط پرداخت پیش‌فرض')
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='حد اعتباری')
    current_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='مانده فعلی')

    # Additional Information
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    categories = models.ManyToManyField('purchases.Category', blank=True, verbose_name='دسته‌بندی‌های تأمین')

    # Audit Fields
    created_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, related_name='created_suppliers', verbose_name='ایجاد شده توسط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Auto-generate supplier code if not provided
        if not self.supplier_code:
            import uuid
            self.supplier_code = f"SUP-{uuid.uuid4().hex[:6].upper()}"

        super().save(*args, **kwargs)

    def get_full_address(self):
        """Return formatted full address"""
        address_parts = [self.address, self.city, self.state, self.postal_code, self.country]
        return ', '.join(filter(None, address_parts))

    def get_contact_info(self):
        """Return formatted contact information"""
        contacts = []
        if self.contact_person:
            contacts.append(f"تماس: {self.contact_person}")
        if self.phone:
            contacts.append(f"تلفن: {self.phone}")
        if self.mobile:
            contacts.append(f"موبایل: {self.mobile}")
        if self.email:
            contacts.append(f"ایمیل: {self.email}")
        return ' | '.join(contacts)

    def update_performance_metrics(self):
        """Update comprehensive performance metrics based on completed orders and evaluations"""
        from django.db.models import Avg

        # Get completed orders
        orders = self.purchase_orders.filter(status='completed')

        if orders.exists():
            self.total_orders = orders.count()
            self.total_amount = sum(
                (Decimal(str(order.total_amount)) for order in orders if order.total_amount),
                Decimal('0'),
            )

            # Calculate on-time delivery rate and average delay
            on_time_deliveries = 0
            total_delays = []

            for order in orders:
                if order.actual_delivery_date and order.delivery_date:
                    if order.actual_delivery_date <= order.delivery_date:
                        on_time_deliveries += 1

                    delay = (order.actual_delivery_date - order.delivery_date).days
                    total_delays.append(max(0, delay))

            if self.total_orders > 0:
                self.on_time_delivery_rate = (
                    Decimal(on_time_deliveries) / Decimal(self.total_orders)
                ) * Decimal('100')

            if total_delays:
                self.avg_delivery_delay_days = Decimal(sum(total_delays)) / Decimal(len(total_delays))

            quality_ratings = []
            quality_map = {
                'passed': Decimal('5'),
                'conditional': Decimal('3'),
                'failed': Decimal('1'),
            }
            for order in orders:
                if getattr(order, 'quality_status', None) in quality_map:
                    quality_ratings.append(quality_map[order.quality_status])

            if quality_ratings:
                self.quality_rating = sum(quality_ratings, Decimal('0')) / Decimal(len(quality_ratings))

        # Update evaluation-based rating
        evaluations = self.evaluations.all()
        if evaluations.exists():
            avg_eval_raw = evaluations.aggregate(Avg('overall_rating'))['overall_rating__avg']
            avg_evaluation_rating = Decimal(str(avg_eval_raw)) if avg_eval_raw is not None else Decimal('0')
            if Decimal(str(self.quality_rating or 0)) > 0:
                on_time = Decimal(str(self.on_time_delivery_rate or 0))
                qual = Decimal(str(self.quality_rating or 0))
                self.rating = (
                    (on_time / Decimal('20')) * Decimal('0.4')
                    + qual * Decimal('0.3')
                    + avg_evaluation_rating * Decimal('0.3')
                )
            else:
                self.rating = avg_evaluation_rating

        # Calculate payment reliability score
        self.update_payment_reliability_score()

        self.last_performance_update = timezone.now()
        self.save()

    def update_payment_reliability_score(self):
        """Calculate payment reliability based on invoice payment history"""
        from .invoicing import Invoice
        invoices = Invoice.objects.filter(
            models.Q(purchase_order__supplier=self) | models.Q(supplier=self)
        ).distinct()

        if not invoices.exists():
            self.payment_reliability_score = Decimal('0')
            return

        on_time_payments = 0
        total_payment_delay = 0
        paid_invoices_count = 0

        for invoice in invoices:
            if invoice.payment_status == 'paid':
                paid_invoices_count += 1

                payments = invoice.payment_records().filter(payment_status='completed')
                if payments.exists():
                    first_payment_date = payments.order_by('payment_date').first().payment_date
                    if first_payment_date <= invoice.due_date:
                        on_time_payments += 1

                    delay = (first_payment_date - invoice.due_date).days
                    total_payment_delay += max(0, delay)

        if paid_invoices_count > 0:
            on_time_rate = (Decimal(on_time_payments) / Decimal(paid_invoices_count)) * Decimal('100')
            avg_delay = Decimal(total_payment_delay) / Decimal(paid_invoices_count)
            delay_score = max(Decimal('0'), Decimal('100') - (avg_delay * Decimal('2')))
            self.payment_reliability_score = (on_time_rate * Decimal('0.7')) + (delay_score * Decimal('0.3'))
        else:
            self.payment_reliability_score = Decimal('0')

    def get_performance_trend(self, months=6):
        """Get performance trend over the last N months"""
        from dateutil.relativedelta import relativedelta

        end_date = timezone.localdate()
        start_date = end_date - relativedelta(months=months)
        
        # Get evaluations in date range
        evaluations = self.evaluations.filter(
            evaluation_date__gte=start_date,
            evaluation_date__lte=end_date
        ).order_by('evaluation_date')
        
        trend_data = []
        for evaluation in evaluations:
            trend_data.append({
                'date': evaluation.evaluation_date,
                'rating': evaluation.overall_rating,
                'type': evaluation.get_evaluation_type_display()
            })
        
        return trend_data

    def get_performance_summary(self):
        """Get a comprehensive performance summary"""
        return {
            'overall_rating': self.rating,
            'total_orders': self.total_orders,
            'total_amount': self.total_amount,
            'on_time_delivery_rate': self.on_time_delivery_rate,
            'avg_delivery_delay': self.avg_delivery_delay_days,
            'quality_rating': self.quality_rating,
            'payment_reliability': self.payment_reliability_score,
            'evaluation_count': self.evaluations.count(),
            'last_update': self.last_performance_update,
            'status': self.get_status_display(),
            'performance_level': self.get_performance_level()
        }

    def get_performance_level(self):
        """Get performance level based on overall rating"""
        if self.rating >= 4.5:
            return 'excellent'
        elif self.rating >= 4.0:
            return 'very_good'
        elif self.rating >= 3.5:
            return 'good'
        elif self.rating >= 3.0:
            return 'satisfactory'
        elif self.rating >= 2.0:
            return 'needs_improvement'
        else:
            return 'poor'

    def get_recent_evaluations(self, limit=5):
        """Get recent evaluations"""
        return self.evaluations.order_by('-evaluation_date')[:limit]

    def get_performance_alerts(self):
        """Get performance alerts and recommendations"""
        alerts = []
        
        if self.on_time_delivery_rate < 80:
            alerts.append({
                'type': 'warning',
                'message': f'نرخ تحویل به موقع ({self.on_time_delivery_rate:.1f}%) کمتر از استاندارد است'
            })
        
        if self.avg_delivery_delay_days > 7:
            alerts.append({
                'type': 'warning',
                'message': f'میانگین تأخیر تحویل ({self.avg_delivery_delay_days:.1f} روز) بالا است'
            })
        
        if self.payment_reliability_score < 70:
            alerts.append({
                'type': 'danger',
                'message': f'قابلیت اطمینان پرداخت ({self.payment_reliability_score:.1f}) نیاز به بهبود دارد'
            })
        
        if self.rating < 3.0:
            alerts.append({
                'type': 'danger',
                'message': f'امتیاز کلی عملکرد ({self.rating:.1f}) پایین است'
            })
        
        if not self.evaluations.exists():
            alerts.append({
                'type': 'info',
                'message': 'هیچ ارزیابی‌ای برای این تأمین‌کننده ثبت نشده است'
            })
        
        return alerts

    def get_recent_orders(self, limit=5):
        """Get recent orders from this supplier"""
        return self.purchase_orders.order_by('-created_at')[:limit]

    def get_pending_invoices(self):
        """Get pending invoices for this supplier"""
        from .invoicing import Invoice
        return Invoice.objects.filter(
            models.Q(purchase_order__supplier=self) | models.Q(supplier=self),
            status__in=['sent', 'approved'],
        ).distinct().order_by('due_date')

    def get_overdue_invoices(self):
        """Get overdue invoices for this supplier"""
        from .invoicing import Invoice
        return Invoice.objects.filter(
            models.Q(purchase_order__supplier=self) | models.Q(supplier=self),
            status__in=['sent', 'approved', 'partially_paid', 'overdue'],
            due_date__lt=timezone.localdate(),
        ).exclude(payment_status='paid').distinct()

    class Meta:
        verbose_name = 'تأمین‌کننده'
        verbose_name_plural = 'تأمین‌کنندگان'
        ordering = ['name']
        unique_together = [['name', 'cafe'], ['supplier_code', 'cafe']]
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['rating']),
            models.Index(fields=['total_orders']),
            models.Index(fields=['total_amount']),
            models.Index(fields=['status', 'rating']),
            models.Index(fields=['cafe', 'status']),
            models.Index(fields=['cafe', '-created_at']),
        ]


# Supplier Evaluation System
class SupplierEvaluation(models.Model):
    EVALUATION_TYPE_CHOICES = [
        ('automatic', 'ارزیابی خودکار'),
        ('manual', 'ارزیابی دستی'),
        ('periodic', 'ارزیابی دوره‌ای'),
    ]

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='evaluations', verbose_name='تأمین‌کننده')
    evaluator = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='supplier_evaluations', verbose_name='ارزیاب')

    # Evaluation Details
    evaluation_type = models.CharField(max_length=20, choices=EVALUATION_TYPE_CHOICES, default='manual', verbose_name='نوع ارزیابی')
    evaluation_date = models.DateField(default=timezone.localdate, verbose_name='تاریخ ارزیابی')

    # Rating Criteria (1-5 scale)
    quality_rating = models.PositiveIntegerField(choices=[(i, i) for i in range(1, 6)], null=True, blank=True, verbose_name='امتیاز کیفیت')
    delivery_rating = models.PositiveIntegerField(choices=[(i, i) for i in range(1, 6)], null=True, blank=True, verbose_name='امتیاز تحویل')
    price_rating = models.PositiveIntegerField(choices=[(i, i) for i in range(1, 6)], null=True, blank=True, verbose_name='امتیاز قیمت')
    communication_rating = models.PositiveIntegerField(choices=[(i, i) for i in range(1, 6)], null=True, blank=True, verbose_name='امتیاز ارتباط')
    overall_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True, verbose_name='امتیاز کلی')

    # Comments and Notes
    strengths = models.TextField(blank=True, verbose_name='نقاط قوت')
    weaknesses = models.TextField(blank=True, verbose_name='نقاط ضعف')
    recommendations = models.TextField(blank=True, verbose_name='پیشنهادات')
    comments = models.TextField(blank=True, verbose_name='نظرات کلی')

    # Related Order (optional)
    related_order = models.ForeignKey('purchases.PurchaseOrder', null=True, blank=True, on_delete=models.SET_NULL, verbose_name='سفارش مرتبط')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __init__(self, *args, **kwargs):
        rating = kwargs.pop('rating', None)
        super().__init__(*args, **kwargs)
        if rating is not None:
            self.overall_rating = rating

    @property
    def rating(self):
        return self.overall_rating

    @rating.setter
    def rating(self, value):
        self.overall_rating = value

    def __str__(self):
        ev_name = self.evaluator.get_full_name() if self.evaluator else '—'
        return f"ارزیابی {self.supplier.name} توسط {ev_name}"

    def save(self, *args, **kwargs):
        ratings = [r for r in [self.quality_rating, self.delivery_rating, self.price_rating, self.communication_rating] if r is not None]
        if self.overall_rating is None and ratings:
            self.overall_rating = Decimal(sum(ratings)) / Decimal(len(ratings))

        super().save(*args, **kwargs)

        if self.supplier_id and self.supplier.quality_rating == 0:
            evaluations = SupplierEvaluation.objects.filter(supplier=self.supplier, overall_rating__isnull=False)
            if evaluations.exists():
                avg_rating = evaluations.aggregate(models.Avg('overall_rating'))['overall_rating__avg']
                if avg_rating is not None:
                    self.supplier.rating = Decimal(str(avg_rating))
                    self.supplier.save(update_fields=['rating'])

    class Meta:
        verbose_name = 'ارزیابی تأمین‌کننده'
        verbose_name_plural = 'ارزیابی‌های تأمین‌کننده'
        ordering = ['-evaluation_date']
        unique_together = ['supplier', 'evaluator', 'evaluation_date', 'evaluation_type']
        indexes = [
            models.Index(fields=['evaluation_date']),
            models.Index(fields=['supplier']),
            models.Index(fields=['overall_rating']),
            models.Index(fields=['evaluation_date', 'supplier']),
        ]
