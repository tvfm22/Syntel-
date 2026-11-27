from django.db import models
from django.utils import timezone


# PurchaseOrder model for formal purchase orders
class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('draft', 'پیش‌نویس'),
        ('sent', 'ارسال شده'),
        ('confirmed', 'تأیید شده'),
        ('in_transit', 'در حال حمل'),
        ('delivered', 'تحویل داده شده'),
        ('inspected', 'بازرسی شده'),
        ('completed', 'تکمیل شده'),
        ('cancelled', 'لغو شده'),
    ]

    DELIVERY_STATUS_CHOICES = [
        ('pending', 'در انتظار'),
        ('partial', 'جزئی'),
        ('full', 'کامل'),
        ('over_delivered', 'بیش از سفارش'),
    ]

    QUALITY_STATUS_CHOICES = [
        ('pending', 'در انتظار بازرسی'),
        ('passed', 'قابل قبول'),
        ('failed', 'رد کیفیت'),
        ('conditional', 'قابل قبول با شرایط'),
    ]

    order_number = models.CharField(max_length=50, unique=True, verbose_name='شماره سفارش')
    request = models.OneToOneField('purchases.PurchaseRequest', on_delete=models.CASCADE, related_name='purchase_order', verbose_name='درخواست خرید')
    supplier = models.ForeignKey('Supplier', on_delete=models.PROTECT, related_name='purchase_orders', verbose_name='تأمین‌کننده')
    delivery_date = models.DateField(null=True, blank=True, verbose_name='تاریخ تحویل برنامه‌ریزی شده')
    payment_terms = models.TextField(blank=True, verbose_name='شرایط پرداخت')
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='وضعیت')
    
    # Delivery tracking fields
    actual_delivery_date = models.DateField(null=True, blank=True, verbose_name='تاریخ تحویل واقعی')
    delivery_status = models.CharField(max_length=20, choices=DELIVERY_STATUS_CHOICES, default='pending', verbose_name='وضعیت تحویل')
    delivery_notes = models.TextField(blank=True, verbose_name='یادداشت‌های تحویل')
    received_by = models.ForeignKey('purchases.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='received_orders', verbose_name='دریافت شده توسط')
    
    # Quality control fields
    quality_status = models.CharField(max_length=20, choices=QUALITY_STATUS_CHOICES, default='pending', verbose_name='وضعیت کیفیت')
    quality_inspector = models.ForeignKey('purchases.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='inspected_orders', verbose_name='بازرس کیفیت')
    inspection_date = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ بازرسی')
    quality_notes = models.TextField(blank=True, verbose_name='یادداشت‌های کیفیت')
    
    # Tracking fields
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ ارسال')
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تأیید')
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تحویل')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تکمیل')
    
    created_by = models.ForeignKey('purchases.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='created_orders', verbose_name='ایجاد شده توسط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return self.order_number

    def get_total_items(self):
        """Return total number of items in this order"""
        return self.request.items.count()

    def get_total_quantity(self):
        """Return total quantity of all items"""
        return sum(item.quantity for item in self.request.items.all())

    @property
    def total_amount(self):
        """Calculate total amount of the order"""
        return sum(item.total_price for item in self.order_items.all() if item.total_price)

    def is_overdue(self):
        """Check if the order is overdue"""
        if self.delivery_date and not self.actual_delivery_date:
            return timezone.localdate() > self.delivery_date
        return False

    def get_delivery_delay_days(self):
        """Return number of days delayed in delivery"""
        if self.actual_delivery_date and self.delivery_date:
            delay = (self.actual_delivery_date - self.delivery_date).days
            return max(0, delay)
        return 0

    def can_be_sent(self):
        """Check if order can be sent"""
        return self.status == 'draft'

    def can_be_confirmed(self):
        """Check if order can be confirmed"""
        return self.status == 'sent'

    def can_be_delivered(self):
        """Check if order can be marked as delivered"""
        return self.status in ['confirmed', 'in_transit']

    def can_be_inspected(self):
        """Check if order can be inspected"""
        return self.status == 'delivered'

    def can_be_completed(self):
        """Check if order can be completed"""
        return self.status == 'inspected' and self.quality_status in ['passed', 'conditional']

    class Meta:
        verbose_name = 'سفارش خرید'
        verbose_name_plural = 'سفارشات خرید'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['supplier']),
            models.Index(fields=['delivery_date']),
            models.Index(fields=['actual_delivery_date']),
            models.Index(fields=['created_at', 'status']),
            models.Index(fields=['supplier', 'status']),
        ]


# PurchaseOrderItem model for order items (linking to request items)
class PurchaseOrderItem(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='order_items', verbose_name='سفارش')
    request_item = models.OneToOneField('purchases.PurchaseItem', on_delete=models.CASCADE, null=True, blank=True, related_name='order_item', verbose_name='آیتم درخواست')
    product = models.ForeignKey('purchases.Product', null=True, blank=True, on_delete=models.SET_NULL, related_name='purchase_order_items', verbose_name='محصول')
    quantity = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True, verbose_name='مقدار')
    unit_price = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, verbose_name='قیمت واحد')
    total_price = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, verbose_name='قیمت کل')
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')

    def __str__(self):
        name = None
        if self.product:
            name = self.product.name
        elif self.request_item:
            name = self.request_item.name
        qty = self.quantity or (self.request_item.quantity if self.request_item else '')
        unit = self.request_item.unit if self.request_item else ''
        return f"{name or 'آیتم سفارش'} - {qty} {unit}".strip()

    def save(self, *args, **kwargs):
        qty = self.quantity
        if qty is None and self.request_item:
            qty = self.request_item.quantity

        if self.unit_price is not None and qty is not None:
            self.total_price = self.unit_price * qty
        else:
            self.total_price = None
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'آیتم سفارش خرید'
        verbose_name_plural = 'آیتم‌های سفارش خرید'
