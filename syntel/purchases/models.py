from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator


# Cafe model for multi-tenant support
class Cafe(models.Model):
    """مدل کافه برای پشتیبانی از چند شعبه"""
    name = models.CharField(max_length=200, verbose_name='نام کافه')
    code = models.CharField(max_length=50, unique=True, verbose_name='کد کافه')  # e.g., 'local', 'baba_adam'
    address = models.TextField(blank=True, verbose_name='آدرس')
    phone = models.CharField(max_length=20, blank=True, verbose_name='تلفن')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'کافه'
        verbose_name_plural = 'کافه‌ها'
        ordering = ['name']


# Custom User model with roles
class User(AbstractUser):
    ROLE_CHOICES = [
        ('kitchen', 'آشپزخانه'),
        ('bar', 'بار'),
        ('counter', 'کانتر'),
        ('cashier', 'صندوق'),
        ('facilities', 'تاسیسات'),
        ('purchasing', 'خرید'),
        ('manager', 'مدیر'),
        ('accounting', 'دفتر حسابداری'),
        ('finance_manager', 'مدیر مالی'),
        ('ceo', 'مدیرعامل'),
    ]
    role: models.CharField = models.CharField(max_length=20, choices=ROLE_CHOICES, default='kitchen')
    cafe = models.ForeignKey('Cafe', on_delete=models.SET_NULL, null=True, blank=True, related_name='users', verbose_name='کافه')
    
    # Roles that can switch between cafes
    ADMIN_ROLES = ['manager', 'ceo', 'finance_manager']
    # Roles that see cafe selection on login
    CAFE_SELECTION_ROLES = ['manager', 'finance_manager', 'accounting', 'ceo']

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"
    
    @property
    def can_switch_cafe(self):
        """
        فقط سوپر ادمین‌ها می‌توانند بین کافه‌ها جابجا شوند
        
        سوپر ادمین = کاربر با نقش manager یا ceo که cafe=None دارد
        """
        return self.role in self.ADMIN_ROLES and self.cafe is None
    
    @property
    def needs_cafe_selection(self):
        """
        آیا کاربر نیاز به انتخاب کافه در لاگین دارد
        
        فقط کاربرانی که:
        1. نقش ادمین دارند (manager, finance_manager, accounting, ceo)
        2. و کافه مشخصی ندارند (cafe=None)
        
        یا نقش finance_manager دارند (همیشه انتخاب می‌کنند)
        
        صفحه انتخاب کافه می‌بینند. این کاربران "سوپر ادمین" هستند.
        کاربران عادی و کاربران ادمین با کافه مشخص، مستقیم به کافه خودشان می‌روند.
        """
        return (self.role in self.CAFE_SELECTION_ROLES and self.cafe is None) or self.role == 'finance_manager'

# Requester model
class Requester(models.Model):
    REQUESTER_CHOICES = [
        ('kitchen', 'آشپزخانه'),
        ('bar', 'بار'),
        ('facilities', 'تاسیسات'),
        ('store', 'انبار'),
    ]
    name = models.CharField(max_length=50, choices=REQUESTER_CHOICES, verbose_name='نام درخواست‌کننده')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='requesters', verbose_name='کافه', null=True, blank=True)

    def __str__(self):
        return self.get_name_display()

    class Meta:
        verbose_name = 'درخواست‌کننده'
        verbose_name_plural = 'درخواست‌کنندگان'
        unique_together = [['name', 'cafe']]

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

# Purchase Request model
class PurchaseRequest(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending_manager_approval', 'Pending Manager Approval'),
        ('pending', 'Pending Purchase'),
        ('purchased', 'Purchased'),
        ('partial', 'Partial'),
        ('rejected', 'Rejected'),
    ]
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='purchase_requests', verbose_name='کافه', null=True, blank=True)
    user: models.ForeignKey = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requests')
    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)
    status: models.CharField = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending_manager_approval')
    processed_by: models.ForeignKey = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_requests')
    processed_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)
    manager_confirmed: models.BooleanField = models.BooleanField(default=False, verbose_name='تایید نهایی مدیر')
    manager_confirmed_at: models.DateTimeField = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تایید مدیر')
    manager_confirmed_by: models.ForeignKey = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='confirmed_requests', verbose_name='تایید کننده')
    rejection_reason: models.TextField = models.TextField(blank=True, null=True, verbose_name='دلیل رد')

    def __str__(self) -> str:
        return f"Request by {self.user.username} - {self.status}"

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['processed_at']),
            models.Index(fields=['created_at', 'status']),
        ]

# Purchase Items model
class PurchaseItem(models.Model):
    SUPPLY_STATUS_CHOICES = [
        ('pending', 'در انتظار'),
        ('purchased', 'خرید'),
        ('from_stock', 'تامین از انبار'),
        ('not_supplied', 'تامین نشده'),
    ]
    
    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='محصول')
    name = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    purchased_quantity = models.PositiveIntegerField(null=True, blank=True)  # Actual purchased
    supply_status = models.CharField(max_length=20, choices=SUPPLY_STATUS_CHOICES, default='pending', verbose_name='وضعیت تامین')

    def __str__(self):
        return f"{self.name} - {self.quantity} {self.unit}"

    class Meta:
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['product']),
        ]

# Purchase Invoices model (optional)
class PurchaseInvoice(models.Model):
    request = models.OneToOneField(PurchaseRequest, on_delete=models.CASCADE)
    invoice_file = models.FileField(upload_to='invoices/', blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Invoice for {self.request}"

# Activity Log model
class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('created', 'Created Request'),
        ('purchased', 'Marked as Purchased'),
        ('updated', 'Updated Request'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='activities')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.created_at}"

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['user']),
            models.Index(fields=['action']),
            models.Index(fields=['created_at', 'user']),
            models.Index(fields=['request', 'created_at']),
        ]

# Unit model for measurement units
class Unit(models.Model):
    name = models.CharField(max_length=50, verbose_name='نام واحد')
    symbol = models.CharField(max_length=10, blank=True, verbose_name='نماد')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='units', verbose_name='کافه', null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'واحد اندازه‌گیری'
        verbose_name_plural = 'واحدهای اندازه‌گیری'
        unique_together = [['name', 'cafe']]

# Category model for product categories
class Category(models.Model):
    name = models.CharField(max_length=100, verbose_name='نام دسته‌بندی')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subcategories', verbose_name='دسته‌بندی والد')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='categories', verbose_name='کافه', null=True, blank=True)

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name

    @property
    def product_count(self):
        """Return the count of active products in this category"""
        return self.product_set.filter(is_active=True).count()
    
    @property
    def full_path(self):
        """Return the full hierarchical path of the category"""
        if self.parent:
            return f"{self.parent.full_path} > {self.name}"
        return self.name

    class Meta:
        verbose_name = 'دسته‌بندی'
        verbose_name_plural = 'دسته‌بندی‌ها'
        unique_together = [['name', 'parent', 'cafe']]

# Product model for base products
class Product(models.Model):
    name = models.CharField(max_length=200, verbose_name='نام محصول')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='دسته‌بندی')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد اصلی')
    min_stock = models.PositiveIntegerField(default=0, verbose_name='حداقل موجودی')
    current_stock = models.PositiveIntegerField(default=0, verbose_name="موجودی فعلی")
    description = models.TextField(blank=True, verbose_name='توضیحات')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='products', verbose_name='کافه', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'محصول'
        verbose_name_plural = 'محصولات'
        ordering = ['name']
        unique_together = [['name', 'cafe']]

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
    request = models.OneToOneField(PurchaseRequest, on_delete=models.CASCADE, related_name='purchase_order', verbose_name='درخواست خرید')
    supplier = models.ForeignKey('Supplier', on_delete=models.CASCADE, related_name='purchase_orders', verbose_name='تأمین‌کننده')
    delivery_date = models.DateField(null=True, blank=True, verbose_name='تاریخ تحویل برنامه‌ریزی شده')
    payment_terms = models.TextField(blank=True, verbose_name='شرایط پرداخت')
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='وضعیت')
    
    # Delivery tracking fields
    actual_delivery_date = models.DateField(null=True, blank=True, verbose_name='تاریخ تحویل واقعی')
    delivery_status = models.CharField(max_length=20, choices=DELIVERY_STATUS_CHOICES, default='pending', verbose_name='وضعیت تحویل')
    delivery_notes = models.TextField(blank=True, verbose_name='یادداشت‌های تحویل')
    received_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='received_orders', verbose_name='دریافت شده توسط')
    
    # Quality control fields
    quality_status = models.CharField(max_length=20, choices=QUALITY_STATUS_CHOICES, default='pending', verbose_name='وضعیت کیفیت')
    quality_inspector = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='inspected_orders', verbose_name='بازرس کیفیت')
    inspection_date = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ بازرسی')
    quality_notes = models.TextField(blank=True, verbose_name='یادداشت‌های کیفیت')
    
    # Tracking fields
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ ارسال')
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تأیید')
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تحویل')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تکمیل')
    
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name='created_orders', verbose_name='ایجاد شده توسط')
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
            return timezone.now().date() > self.delivery_date
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
    request_item = models.OneToOneField(PurchaseItem, on_delete=models.CASCADE, null=True, blank=True, related_name='order_item', verbose_name='آیتم درخواست')
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name='purchase_order_items', verbose_name='محصول')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='مقدار')
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='قیمت واحد')
    total_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='قیمت کل')
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
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'آیتم سفارش خرید'
        verbose_name_plural = 'آیتم‌های سفارش خرید'

# Invoice Management Models
class InvoiceManager(models.Manager):
    def create(self, **kwargs):
        if 'order' in kwargs:
            kwargs['purchase_order'] = kwargs.pop('order')
        return super().create(**kwargs)

    def filter(self, *args, **kwargs):
        if 'order' in kwargs:
            kwargs['purchase_order'] = kwargs.pop('order')
        return super().filter(*args, **kwargs)

    def get(self, *args, **kwargs):
        if 'order' in kwargs:
            kwargs['purchase_order'] = kwargs.pop('order')
        return super().get(*args, **kwargs)


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
    purchase_order = models.OneToOneField(PurchaseOrder, on_delete=models.CASCADE, related_name='invoice', verbose_name='سفارش خرید', null=True, blank=True)
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices', verbose_name='تأمین‌کننده')
    store_name = models.CharField(max_length=200, blank=True, verbose_name='نام فروشگاه')
    
    # Invoice details
    invoice_date = models.DateField(default=timezone.now, verbose_name='تاریخ فاکتور')
    due_date = models.DateField(verbose_name='تاریخ سررسید')
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='نرخ مالیات (%)')
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='نرخ تخفیف (%)')
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='هزینه حمل و نقل')
    
    # Status and tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='وضعیت')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='unpaid', verbose_name='وضعیت پرداخت')
    
    # Financial tracking
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='جمع جزء')
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='مبلغ مالیات')
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='مبلغ تخفیف اقلام')
    cash_discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, default=0, verbose_name='تخفیف پرداخت نقدی')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='مجموع کل')
    
    # Notes and tracking
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    issues = models.ManyToManyField(InvoiceIssue, blank=True, related_name='invoices', verbose_name='ایرادات')
    
    # Audit fields
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name='created_invoices', verbose_name='ایجاد شده توسط')
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='updated_invoices', verbose_name='ویرایش شده توسط')
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_invoices', verbose_name='تأیید شده توسط')
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

    def save(self, *args, **kwargs):
        # Auto-generate invoice number if not provided
        if not self.invoice_number:
            import uuid
            self.invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
        if not self.due_date:
            self.due_date = self.invoice_date or timezone.now().date()

        super().save(*args, **kwargs)

        if self.pk and self.invoice_items.exists():
            subtotal = sum(
                (getattr(item, 'line_total', None) or getattr(item, 'total_price', 0))
                for item in self.invoice_items.all()
            )
            self.subtotal = subtotal
            self.tax_amount = (self.subtotal * self.tax_rate / 100) if self.tax_rate else 0
            self.discount_amount = (self.subtotal * self.discount_rate / 100) if self.discount_rate else 0
            self.total_amount = self.subtotal + self.tax_amount + self.shipping_cost - self.discount_amount
            super().save(update_fields=['subtotal', 'tax_amount', 'discount_amount', 'total_amount'])

    def is_overdue(self):
        """Check if invoice is overdue"""
        if self.due_date and self.payment_status != 'paid':
            return timezone.now().date() > self.due_date
        return False

    def get_days_overdue(self):
        """Return number of days overdue"""
        if self.due_date and self.payment_status != 'paid':
            days = (timezone.now().date() - self.due_date).days
            return max(0, days)
        return 0

    def can_be_approved(self):
        """Check if invoice can be approved"""
        return self.status == 'sent'

    def can_be_paid(self):
        """Check if invoice can be marked as paid"""
        return self.status in ['sent', 'approved']

    def update_payment_status(self):
        """Update payment status based on payments"""
        total_paid = self.payments.filter(payment_status='completed').aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        
        if total_paid == 0:
            self.payment_status = 'unpaid'
            self.status = 'draft'
        elif total_paid < self.total_amount:
            self.payment_status = 'partial'
            self.status = 'partially_paid'
        elif total_paid == self.total_amount:
            self.payment_status = 'paid'
            self.status = 'paid'
        else:
            self.payment_status = 'overpaid'
            self.status = 'paid'
        
        self.save()

    @property
    def paid_amount(self):
        return self.payments.filter(payment_status='completed').aggregate(
            total=models.Sum('amount')
        )['total'] or 0

    @property
    def remaining_amount(self):
        total = self.total_amount or 0
        remaining = total - (self.paid_amount or 0)
        return remaining if remaining > 0 else 0

    def get_total_paid(self):
        """Get total amount paid"""
        return self.payments.filter(payment_status='completed').aggregate(
            total=models.Sum('amount')
        )['total'] or 0

    def get_remaining_amount(self):
        """Get remaining amount to be paid"""
        return max(0, self.total_amount - self.get_total_paid())

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
        ]

class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='invoice_items', verbose_name='فاکتور')
    order_item = models.OneToOneField(PurchaseOrderItem, on_delete=models.CASCADE, null=True, blank=True, related_name='invoice_item', verbose_name='آیتم سفارش')
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name='invoice_items', verbose_name='محصول')
    
    # Item details (may differ from order)
    description = models.CharField(max_length=200, verbose_name='توضیحات')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='مقدار')
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='قیمت واحد')
    
    # Tax and discount per item
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='مبلغ مالیات')
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='تخفیف پرداخت نقدی')
    
    line_total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='مجموع خط')
    
    # Additional fields
    requester = models.ForeignKey(Requester, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='درخواست‌کننده')
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
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='single_payments', null=True, blank=True, verbose_name='فاکتور')
    invoices = models.ManyToManyField(Invoice, related_name='payments', blank=True, verbose_name='فاکتورها')
    
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='مبلغ پرداخت', validators=[MinValueValidator(0)])
    
    # Payment details
    payment_location = models.ForeignKey('PaymentLocation', null=True, blank=True, on_delete=models.SET_NULL, related_name='payments', verbose_name='محل پرداخت')
    payment_date = models.DateField(default=timezone.now, verbose_name='تاریخ پرداخت')
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
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name='created_payments', verbose_name='ایجاد شده توسط')
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
        invoice_count = self.invoices.count() if self.invoices.exists() else (1 if self.invoice else 0)
        if invoice_count == 1:
            invoice = self.invoice or self.invoices.first()
            return f"پرداخت {self.amount} برای فاکتور {invoice.invoice_number}"
        else:
            return f"پرداخت گروهی {self.amount} برای {invoice_count} فاکتور"

    def get_related_invoices(self):
        """Get all related invoices (both single and multiple)"""
        invoices = []
        if self.invoice:
            invoices.append(self.invoice)
        invoices.extend(list(self.invoices.all()))
        return list(set(invoices))  # Remove duplicates
    
    def distribute_amount_evenly(self):
        """Distribute payment amount evenly among all invoices"""
        invoices = self.get_related_invoices()
        if not invoices:
            return {}
        
        amount_per_invoice = self.amount / len(invoices)
        distribution = {}
        for invoice in invoices:
            distribution[invoice.id] = amount_per_invoice
        return distribution
    
    def distribute_amount_by_remaining(self):
        """Distribute payment amount based on remaining amounts of invoices"""
        invoices = self.get_related_invoices()
        if not invoices:
            return {}
        
        # Calculate remaining amounts
        remaining_amounts = {}
        total_remaining = 0
        for invoice in invoices:
            remaining = invoice.get_remaining_amount()
            remaining_amounts[invoice.id] = remaining
            total_remaining += remaining
        
        if total_remaining == 0:
            # If no remaining amounts, distribute evenly
            return self.distribute_amount_evenly()
        
        # Distribute proportionally
        distribution = {}
        for invoice_id, remaining in remaining_amounts.items():
            proportion = remaining / total_remaining
            distribution[invoice_id] = self.amount * proportion
        
        return distribution

    def save(self, *args, **kwargs):
        # Auto-generate transaction ID if not provided
        if not self.transaction_id:
            import uuid
            self.transaction_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"
        
        # Update invoice payment status when payment is saved
        super().save(*args, **kwargs)
        
        # Update payment status for all related invoices
        if self.invoices.exists():
            for invoice in self.invoices.all():
                invoice.update_payment_status()
        elif self.invoice:
            self.invoice.update_payment_status()

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
            base_code = slugify(self.name) or 'location'
            self.code = base_code[:20]
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'محل پرداخت'
        verbose_name_plural = 'محل‌های پرداخت'
        ordering = ['display_order', 'name']
        unique_together = [['name', 'cafe'], ['code', 'cafe']]

# Supplier Management System
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
    categories = models.ManyToManyField(Category, blank=True, verbose_name='دسته‌بندی‌های تأمین')

    # Audit Fields
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_suppliers', verbose_name='ایجاد شده توسط')
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
        from django.db.models import Avg, Count, Sum

        # Get completed orders
        orders = self.purchase_orders.filter(status='completed')

        if orders.exists():
            self.total_orders = orders.count()
            self.total_amount = sum(order.total_amount for order in orders if order.total_amount) or 0

            # Calculate on-time delivery rate and average delay
            on_time_deliveries = 0
            total_delays = []
            
            for order in orders:
                if order.actual_delivery_date and order.delivery_date:
                    if order.actual_delivery_date <= order.delivery_date:
                        on_time_deliveries += 1
                    
                    # Calculate delay in days
                    delay = (order.actual_delivery_date - order.delivery_date).days
                    total_delays.append(max(0, delay))  # Only positive delays

            if self.total_orders > 0:
                self.on_time_delivery_rate = (on_time_deliveries / self.total_orders) * 100
            
            if total_delays:
                self.avg_delivery_delay_days = sum(total_delays) / len(total_delays)

            # Calculate quality rating from orders (if quality inspection was done)
            quality_ratings = []
            for order in orders:
                if hasattr(order, 'quality_status') and order.quality_status:
                    # Convert quality status to numeric rating
                    quality_map = {
                        'passed': 5,
                        'conditional': 3,
                        'failed': 1
                    }
                    if order.quality_status in quality_map:
                        quality_ratings.append(quality_map[order.quality_status])
            
            if quality_ratings:
                self.quality_rating = sum(quality_ratings) / len(quality_ratings)

        # Update evaluation-based rating
        evaluations = self.evaluations.all()
        if evaluations.exists():
            avg_evaluation_rating = evaluations.aggregate(Avg('overall_rating'))['overall_rating__avg'] or 0
            # Combine order-based and evaluation-based ratings
            if self.quality_rating > 0:
                # Weighted average: 40% delivery/on-time, 30% quality from orders, 30% evaluation rating
                self.rating = (
                    (self.on_time_delivery_rate / 20) * 0.4 +  # Convert percentage to 0-5 scale
                    (self.quality_rating) * 0.3 +
                    avg_evaluation_rating * 0.3
                )
            else:
                # If no quality data from orders, use evaluation rating as primary
                self.rating = avg_evaluation_rating

        # Calculate payment reliability score
        self.update_payment_reliability_score()

        self.last_performance_update = timezone.now()
        self.save()

    def update_payment_reliability_score(self):
        """Calculate payment reliability based on invoice payment history"""
        invoices = Invoice.objects.filter(purchase_order__supplier=self)
        
        if not invoices.exists():
            self.payment_reliability_score = 0
            return

        total_invoices = invoices.count()
        on_time_payments = 0
        total_payment_delay = 0
        paid_invoices_count = 0

        for invoice in invoices:
            if invoice.payment_status == 'paid':
                paid_invoices_count += 1
                
                # Check if paid on time
                payments = invoice.payments.filter(payment_status='completed')
                if payments.exists():
                    first_payment_date = payments.order_by('payment_date').first().payment_date
                    if first_payment_date <= invoice.due_date:
                        on_time_payments += 1
                    
                    # Calculate payment delay
                    delay = (first_payment_date - invoice.due_date).days
                    total_payment_delay += max(0, delay)

        if paid_invoices_count > 0:
            # Payment reliability: 70% on-time payment rate + 30% average delay penalty
            on_time_rate = (on_time_payments / paid_invoices_count) * 100
            avg_delay = total_payment_delay / paid_invoices_count
            
            # Convert delay to score (less delay = higher score)
            delay_score = max(0, 100 - (avg_delay * 2))  # 2 points penalty per day delay
            
            self.payment_reliability_score = (on_time_rate * 0.7) + (delay_score * 0.3)
        else:
            self.payment_reliability_score = 0

    def get_performance_trend(self, months=6):
        """Get performance trend over the last N months"""
        from django.utils import timezone
        from dateutil.relativedelta import relativedelta
        
        end_date = timezone.now().date()
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
        return Invoice.objects.filter(
            purchase_order__supplier=self,
            status__in=['sent', 'approved']
        ).order_by('due_date')

    def get_overdue_invoices(self):
        """Get overdue invoices for this supplier"""
        return Invoice.objects.filter(
            purchase_order__supplier=self,
            status__in=['approved', 'paid'],
            due_date__lt=timezone.now().date()
        ).exclude(payment_status='paid')

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
        ]

# Supplier Evaluation System
class SupplierEvaluation(models.Model):
    EVALUATION_TYPE_CHOICES = [
        ('automatic', 'ارزیابی خودکار'),
        ('manual', 'ارزیابی دستی'),
        ('periodic', 'ارزیابی دوره‌ای'),
    ]

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='evaluations', verbose_name='تأمین‌کننده')
    evaluator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='supplier_evaluations', verbose_name='ارزیاب')

    # Evaluation Details
    evaluation_type = models.CharField(max_length=20, choices=EVALUATION_TYPE_CHOICES, default='manual', verbose_name='نوع ارزیابی')
    evaluation_date = models.DateField(default=timezone.now, verbose_name='تاریخ ارزیابی')

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
    related_order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='سفارش مرتبط')

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
        return f"ارزیابی {self.supplier.name} توسط {self.evaluator.get_full_name()}"

    def save(self, *args, **kwargs):
        ratings = [r for r in [self.quality_rating, self.delivery_rating, self.price_rating, self.communication_rating] if r is not None]
        if self.overall_rating is None and ratings:
            self.overall_rating = sum(ratings) / len(ratings)

        super().save(*args, **kwargs)

        evaluations = SupplierEvaluation.objects.filter(supplier=self.supplier, overall_rating__isnull=False)
        if evaluations.exists():
            avg_rating = evaluations.aggregate(models.Avg('overall_rating'))['overall_rating__avg']
            self.supplier.rating = avg_rating
            self.supplier.save()

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


# Notification System

class NotificationManager(models.Manager):
    def create(self, *args, **kwargs):
        if 'user' in kwargs:
            kwargs['recipient'] = kwargs.pop('user')
        return super().create(*args, **kwargs)

    def filter(self, *args, **kwargs):
        if 'user' in kwargs:
            kwargs['recipient'] = kwargs.pop('user')
        return super().filter(*args, **kwargs)

    def get(self, *args, **kwargs):
        if 'user' in kwargs:
            kwargs['recipient'] = kwargs.pop('user')
        return super().get(*args, **kwargs)

class Notification(models.Model):
    objects = NotificationManager()
    NOTIFICATION_TYPES = [
        ('approval', 'درخواست تأیید'),
        ('alert', 'هشدار'),
        ('info', 'اطلاع‌رسانی'),
        ('success', 'موفقیت'),
        ('warning', 'اخطار'),
        ('error', 'خطا'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'کم'),
        ('medium', 'متوسط'),
        ('high', 'بالا'),
        ('urgent', 'فوری'),
    ]

    # Recipients and Content
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='notifications', verbose_name='کافه', null=True, blank=True)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', verbose_name='گیرنده')
    title = models.CharField(max_length=200, verbose_name='عنوان')
    message = models.TextField(verbose_name='پیام')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='info', verbose_name='نوع اعلان')

    # Metadata
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium', verbose_name='اولویت')
    is_read = models.BooleanField(default=False, verbose_name='خوانده شده')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')

    # Related Objects (Generic Foreign Key)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    related_object = GenericForeignKey('content_type', 'object_id')

    # Additional data
    action_url = models.CharField(max_length=500, blank=True, verbose_name='لینک اقدام')
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ انقضا')

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user is not None:
            self.recipient = user

    @property
    def user(self):
        return self.recipient

    @user.setter
    def user(self, value):
        self.recipient = value

    def __str__(self):
        return f"{self.title} - {self.recipient.username}"

    def mark_as_read(self):
        """Mark notification as read"""
        self.is_read = True
        self.save()

    @property
    def is_expired(self):
        """Check if notification has expired"""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False

    class Meta:
        verbose_name = 'اعلان'
        verbose_name_plural = 'اعلان‌ها'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['is_read', 'recipient']),
            models.Index(fields=['notification_type', 'priority']),
        ]

# Price History Tracking
class PriceHistory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_history', verbose_name='محصول')
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='قیمت')
    date = models.DateTimeField(default=timezone.now, verbose_name='تاریخ و زمان')
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='price_histories', verbose_name='فاکتور')
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='price_histories', verbose_name='تأمین‌کننده')
    
    # Percentage change from previous price
    percent_change = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name='درصد تغییر')
    
    class Meta:
        verbose_name = 'تاریخچه قیمت'
        verbose_name_plural = 'تاریخچه قیمت‌ها'
        ordering = ['-date']
        indexes = [
            models.Index(fields=['product', '-date']),
            models.Index(fields=['date']),
            models.Index(fields=['percent_change']),
        ]

    def __str__(self):
        return f"{self.product.name} - {self.price} ({self.date.date()})"


# Stock Item model - موجودی انبار
class StockItem(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='stock_item', verbose_name='محصول')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='موجودی')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد')
    min_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='حداقل موجودی')
    last_updated = models.DateTimeField(auto_now=True, verbose_name='آخرین بروزرسانی')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')

    def __str__(self):
        return f"{self.product.name} - موجودی: {self.quantity} {self.unit}"

    @property
    def is_low_stock(self):
        """بررسی اینکه موجودی کمتر از حداقل است"""
        return self.quantity <= self.min_quantity

    class Meta:
        verbose_name = 'موجودی انبار'
        verbose_name_plural = 'موجودی‌های انبار'
        indexes = [
            models.Index(fields=['product']),
            models.Index(fields=['last_updated']),
        ]


# Stock Transaction model - تراکنش‌های انبار (ورود/خروج)
class StockTransaction(models.Model):
    TRANSACTION_TYPE_CHOICES = [
        ('in', 'ورود به انبار'),
        ('out', 'خروج از انبار'),
        ('initial', 'موجودی اولیه'),
        ('adjustment', 'تعدیل موجودی'),
    ]
    
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name='transactions', verbose_name='موجودی')
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES, verbose_name='نوع تراکنش')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='مقدار')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد')
    reference_type = models.CharField(max_length=50, blank=True, verbose_name='نوع مرجع')  # 'invoice', 'stock_request', 'initial'
    reference_id = models.PositiveIntegerField(null=True, blank=True, verbose_name='شناسه مرجع')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='ایجاد شده توسط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ تراکنش')
    previous_quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='موجودی قبل')
    new_quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='موجودی جدید')

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.stock_item.product.name} - {self.quantity}"

    class Meta:
        verbose_name = 'تراکنش انبار'
        verbose_name_plural = 'تراکنش‌های انبار'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['stock_item', '-created_at']),
            models.Index(fields=['transaction_type']),
            models.Index(fields=['created_at']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]


# Stock Request model - درخواست تامین از انبار
class StockRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'در انتظار'),
        ('approved', 'تایید شده'),
        ('rejected', 'رد شده'),
        ('fulfilled', 'تامین شده'),
    ]
    
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.CASCADE, related_name='stock_requests', verbose_name='قلم درخواست')
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='درخواست دهنده')
    requested_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ درخواست')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='وضعیت')
    fulfilled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='fulfilled_stock_requests', verbose_name='تامین شده توسط')
    fulfilled_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تامین')
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')

    def __str__(self):
        return f"تامین از انبار - {self.purchase_item.name}"

    class Meta:
        verbose_name = 'درخواست تامین از انبار'
        verbose_name_plural = 'درخواست‌های تامین از انبار'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['status', '-requested_at']),
            models.Index(fields=['purchase_item']),
        ]


# Stock Request Item model - اقلام تامین شده از انبار
class StockRequestItem(models.Model):
    stock_request = models.ForeignKey(StockRequest, on_delete=models.CASCADE, related_name='items', verbose_name='درخواست انبار')
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, verbose_name='موجودی انبار')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='مقدار')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد')

    def __str__(self):
        return f"{self.stock_item.product.name} - {self.quantity}"

    class Meta:
        verbose_name = 'قلم تامین از انبار'
        verbose_name_plural = 'اقلام تامین از انبار'
