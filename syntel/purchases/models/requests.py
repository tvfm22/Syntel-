from django.db import models
from ..numbering import request_number as request_number_for


# Purchase Request model
class PurchaseRequest(models.Model):
    STATUS_CHOICES = [
        ('draft', 'پیش‌نویس'),
        ('pending_manager_approval', 'منتظر تایید مدیر'),
        ('pending', 'در انتظار خرید'),
        ('purchased', 'خریداری شده'),
        ('partial', 'تکمیل جزئی'),
        ('rejected', 'رد شده'),
    ]
    # منبع ایجاد درخواست: معمول (درخواست‌دهنده) یا صفحه «درخواست خرید» مسئول انبار
    SOURCE_CHOICES = [
        ('requester', 'درخواست‌دهنده'),
        ('warehouse_purchase', 'درخواست خرید مسئول انبار'),
    ]
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='requester', verbose_name='منبع درخواست')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='purchase_requests', verbose_name='کافه', null=True, blank=True)
    user: models.ForeignKey = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests')
    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)
    status: models.CharField = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending_manager_approval')
    processed_by: models.ForeignKey = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_requests')
    processed_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)
    manager_confirmed: models.BooleanField = models.BooleanField(default=False, verbose_name='تایید نهایی مدیر')
    manager_confirmed_at: models.DateTimeField = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تایید مدیر')
    manager_confirmed_by: models.ForeignKey = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='confirmed_requests', verbose_name='تایید کننده')
    intended_purchasing_officer: models.ForeignKey = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='intended_purchase_requests',
        verbose_name='مسئول خرید انتخاب‌شده',
    )
    rejection_reason: models.TextField = models.TextField(blank=True, null=True, verbose_name='دلیل رد')
    # زمان تحویل درخواست‌شده: تاریخ مشخص یا تحویل فوری
    delivery_date = models.DateField(null=True, blank=True, verbose_name='زمان تحویل (تاریخ)')
    delivery_immediate = models.BooleanField(default=False, verbose_name='تحویل فوری')

    @property
    def request_number(self):
        """Human-facing number, starting at 100000 (see purchases.numbering)."""
        return request_number_for(self.pk)

    def __str__(self) -> str:
        uname = self.user.username if self.user else '—'
        return f"Request by {uname} - {self.status}"

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['processed_at']),
            models.Index(fields=['created_at', 'status']),
            models.Index(fields=['cafe', 'status']),
            models.Index(fields=['cafe', '-created_at']),
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
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=0, verbose_name='مقدار')
    unit = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    purchased_quantity = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True, verbose_name='مقدار خریداری شده')  # Actual purchased
    supply_status = models.CharField(max_length=20, choices=SUPPLY_STATUS_CHOICES, default='pending', verbose_name='وضعیت تامین')
    # قلم اصلیِ درخواست‌دهنده که با «ارسال» پیش‌نویس به این درخواست خرید کپی شده است
    # (درخواست خرید مسئول انبار). تا وقتی این کپی در جریان است، قلم مبدأ در داشبورد
    # انبار قابل تغییر وضعیت نیست.
    source_item = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='purchase_copies', verbose_name='قلم منبع')
    # تاییدِ دریافت توسط درخواست‌دهنده («کالاهای دریافتی من») — فقط اعلامِ وصول است و
    # هیچ اثری روی موجودی انبار ندارد (کسرِ موجودی هنگام تحویلِ مسئول انبار انجام شده).
    received_confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تایید دریافت')
    received_confirmed_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True,
                                              related_name='confirmed_received_items',
                                              verbose_name='تاییدکنندهٔ دریافت')

    def __str__(self):
        return f"{self.name} - {self.quantity} {self.unit}"

    class Meta:
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['product']),
        ]


# WarehouseRequest model - درخواست از انبار
class WarehouseRequest(models.Model):
    """درخواست از انبار - درخواست‌دهندگان می‌توانند درخواست تامین از انبار ثبت کنند"""
    STATUS_CHOICES = [
        ('pending', 'در انتظار بررسی'),
        ('approved_from_stock', 'تامین از انبار'),
        ('approved_purchase', 'ارسال به لیست خرید'),
        ('partial', 'تامین جزئی'),
        ('declined', 'عدم تامین'),
        ('fulfilled', 'تامین شده'),
    ]

    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='warehouse_requests', verbose_name='کافه', null=True, blank=True)
    user = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='warehouse_requests', verbose_name='درخواست‌دهنده')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ثبت')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending', verbose_name='وضعیت')
    processed_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_warehouse_requests', verbose_name='پردازش‌کننده')
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ پردازش')
    notes = models.TextField(blank=True, verbose_name='یادداشت')
    # اگر به لیست خرید ارجاع شد
    converted_to_purchase = models.ForeignKey(PurchaseRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name='from_warehouse_request', verbose_name='تبدیل به درخواست خرید')

    @property
    def request_number(self):
        """Human-facing number, starting at 100000 (see purchases.numbering)."""
        return request_number_for(self.pk)

    def __str__(self):
        uname = self.user.username if self.user else '—'
        return f"درخواست انبار {self.request_number} - {uname} - {self.get_status_display()}"

    class Meta:
        verbose_name = 'درخواست از انبار'
        verbose_name_plural = 'درخواست‌های از انبار'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['cafe', 'status']),
        ]


# WarehouseRequestItem model - اقلام درخواست از انبار
class WarehouseRequestItem(models.Model):
    """اقلام درخواست از انبار"""
    SUPPLY_STATUS_CHOICES = [
        ('pending', 'در انتظار'),
        ('from_stock', 'تامین از انبار'),
        ('to_purchase', 'ارجاع به خرید'),
        ('not_supplied', 'تامین نشده'),
        ('declined', 'عدم تامین'),
    ]

    warehouse_request = models.ForeignKey(WarehouseRequest, on_delete=models.CASCADE, related_name='items', verbose_name='درخواست انبار')
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='محصول')
    name = models.CharField(max_length=100, verbose_name='نام کالا')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='مقدار درخواستی')
    unit = models.CharField(max_length=20, verbose_name='واحد')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    supply_status = models.CharField(max_length=20, choices=SUPPLY_STATUS_CHOICES, default='pending', verbose_name='وضعیت تامین')
    # موجودی انبار انتخاب‌شده برای تامین
    stock_item = models.ForeignKey('StockItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='warehouse_request_items', verbose_name='موجودی انبار')
    supplied_quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='مقدار تامین‌شده')

    def __str__(self):
        return f"{self.name} - {self.quantity} {self.unit}"

    class Meta:
        verbose_name = 'قلم درخواست انبار'
        verbose_name_plural = 'اقلام درخواست انبار'


# Stock Request model - درخواست تامین از انبار
class StockRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'در انتظار'),
        ('approved', 'تایید شده'),
        ('rejected', 'رد شده'),
        ('fulfilled', 'تامین شده'),
    ]
    
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.CASCADE, related_name='stock_requests', verbose_name='قلم درخواست')
    requested_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, verbose_name='درخواست دهنده')
    requested_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ درخواست')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='وضعیت')
    warehouse = models.ForeignKey('Warehouse', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_requests', verbose_name='انبار تامین‌کننده')
    fulfilled_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='fulfilled_stock_requests', verbose_name='تامین شده توسط')
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
    stock_item = models.ForeignKey('purchases.StockItem', on_delete=models.CASCADE, verbose_name='موجودی انبار')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='مقدار')
    unit = models.ForeignKey('purchases.Unit', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد')

    def __str__(self):
        return f"{self.stock_item.product.name} - {self.quantity}"

    class Meta:
        verbose_name = 'قلم تامین از انبار'
        verbose_name_plural = 'اقلام تامین از انبار'


class PurchaseDraft(models.Model):
    """پیش‌نویس درخواست خرید مسئول انبار.

    وقتی مسئول انبار کالایی را «ارجاع به خرید» می‌کند، آن کالا به‌جای برگشت
    مستقیم به مدیرعامل، در پیش‌نویس این صفحه جمع می‌شود؛ مسئول انبار می‌تواند
    اقلام را ویرایش/حذف کند یا کالای جدید اضافه کند و در پایان با «ارسال»،
    درخواست خرید برای تایید مدیرعامل ثبت شود.
    """

    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='purchase_drafts', verbose_name='کافه', null=True, blank=True)
    created_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_drafts', verbose_name='سازنده')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='آخرین بروزرسانی')

    def __str__(self):
        return f"پیش‌نویس خرید #{self.id} - {self.cafe.code if self.cafe else '-'}"

    class Meta:
        verbose_name = 'پیش‌نویس درخواست خرید'
        verbose_name_plural = 'پیش‌نویس‌های درخواست خرید'
        ordering = ['-created_at']


class PurchaseDraftItem(models.Model):
    """قلم پیش‌نویس درخواست خرید — از ارجاع به خرید یا افزودن دستی"""

    draft = models.ForeignKey(PurchaseDraft, on_delete=models.CASCADE, related_name='items', verbose_name='پیش‌نویس')
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_draft_items', verbose_name='محصول')
    name = models.CharField(max_length=100, verbose_name='نام کالا')
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=1, verbose_name='مقدار')
    unit = models.CharField(max_length=20, default='عدد', verbose_name='واحد')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    # قلم درخواست اصلی که با «ارجاع به خرید» به اینجا آمد (برای ردیابی)
    source_item = models.ForeignKey('PurchaseItem', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='purchase_draft_items', verbose_name='قلم منبع')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')

    def __str__(self):
        return f"{self.name} - {self.quantity} {self.unit}"

    class Meta:
        verbose_name = 'قلم پیش‌نویس خرید'
        verbose_name_plural = 'اقلام پیش‌نویس خرید'
        ordering = ['created_at']


class OfficerAssignment(models.Model):
    """تخصیص یک قلم درخواست کالا به مسئول خرید یا مسئول انبار.

    جریان جدید:
    - کالاهایی که در انبار موجودی دارند به‌صورت خودکار برای مسئول انبار ارسال می‌شوند
      (بدون نیاز به تایید مدیرعامل) و مسئول انبار آن‌ها را تامین، ارجاع به خرید یا
      «عدم موجودی» می‌کند.
    - کالاهایی که در انبار موجود نیستند برای مدیرعامل می‌روند؛ مدیرعامل آن‌ها را
      انتخاب کرده و به مسئول خرید یا انبار مشخصی ارسال می‌کند.
    - مسئول خرید فقط اقلام ارسال‌شده توسط مدیرعامل را می‌بیند و می‌تواند آن‌ها را
      خریداری کند یا به لیست مدیرعامل برگرداند.
    """

    TARGET_ROLE_CHOICES = [
        ('purchasing', 'مسئول خرید'),
        ('warehouse', 'مسئول انبار'),
    ]
    STATUS_CHOICES = [
        ('pending', 'در انتظار بررسی'),
        ('purchased', 'خریداری شده'),
        ('not_purchased', 'خریداری نشده'),
        ('returned', 'بازگشت به مدیرعامل'),
        ('fulfilled', 'تامین از انبار'),
        ('not_in_stock', 'عدم موجودی'),
        ('reopened', 'بازگشایی شده'),
    ]

    item = models.ForeignKey(PurchaseItem, on_delete=models.CASCADE, related_name='officer_assignments', verbose_name='قلم درخواست')
    target_role = models.CharField(max_length=20, choices=TARGET_ROLE_CHOICES, verbose_name='نقش مقصد')
    # اگر null باشد یعنی به «هر مسئول» با آن نقش (صندوق مشترک) ارسال شده است
    assigned_to = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='officer_assignments', verbose_name='مسئول')
    assigned_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_officer_assignments', verbose_name='تخصیص‌دهنده')
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ تخصیص')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ انجام/بازگشت')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='وضعیت')
    notes = models.TextField(blank=True, verbose_name='یادداشت')
    # اطلاعات تامین از انبار (وقتی مسئول انبار تامین می‌کند)
    stock_item = models.ForeignKey('StockItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='officer_assignments', verbose_name='موجودی انبار')
    supplied_quantity = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True, verbose_name='مقدار تامین‌شده')

    def __str__(self):
        return f"{self.item.name} ← {self.get_target_role_display()} ({self.get_status_display()})"

    class Meta:
        verbose_name = 'تخصیص قلم درخواست'
        verbose_name_plural = 'تخصیص‌های قلم درخواست'
        ordering = ['-assigned_at']
        indexes = [
            models.Index(fields=['target_role', 'status']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['item', 'status']),
        ]
