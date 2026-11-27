from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# Warehouse model - مدیریت انبارها
class Warehouse(models.Model):
    """مدل انبار برای مدیریت چند انبار"""
    name = models.CharField(max_length=200, verbose_name='نام انبار')
    code = models.CharField(max_length=20, verbose_name='کد انبار')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='warehouses', verbose_name='کافه')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    location = models.CharField(max_length=300, blank=True, verbose_name='محل انبار')
    manager_name = models.CharField(max_length=100, blank=True, verbose_name='مسئول انبار')
    manager_phone = models.CharField(max_length=20, blank=True, verbose_name='تلفن مسئول')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    is_supply_enabled = models.BooleanField(default=True, verbose_name='قابل تامین (در درخواست‌های خرید قابل انتخاب باشد)')
    allow_negative_stock = models.BooleanField(
        default=False,
        verbose_name='مجاز بودن موجودی منفی',
        help_text='در صورت فعال بودن، خروج کالا از این انبار حتی با موجودی صفر یا ناکافی مجاز است و موجودی منفی ثبت می‌شود.',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return f"{self.name} ({self.code})"

    class Meta:
        verbose_name = 'انبار'
        verbose_name_plural = 'انبارها'
        unique_together = [['cafe', 'code']]
        ordering = ['name']


# Stock Item model - موجودی انبار
class StockItem(models.Model):
    product = models.ForeignKey('purchases.Product', on_delete=models.PROTECT, related_name='stock_items', verbose_name='محصول')
    warehouse = models.ForeignKey('Warehouse', on_delete=models.PROTECT, related_name='stock_items', verbose_name='انبار', null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0, verbose_name='موجودی (واحد پایه)')
    unit = models.ForeignKey('purchases.Unit', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد')
    min_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='حداقل موجودی')
    last_updated = models.DateTimeField(auto_now=True, verbose_name='آخرین بروزرسانی')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')

    def __str__(self):
        warehouse_name = self.warehouse.name if self.warehouse else 'نامشخص'
        return f"{self.product.name} [{warehouse_name}] - موجودی: {self.quantity} {self.unit}"

    @property
    def is_low_stock(self):
        """بررسی اینکه موجودی کمتر از حداقل است"""
        return self.quantity <= self.min_quantity

    class Meta:
        verbose_name = 'موجودی انبار'
        verbose_name_plural = 'موجودی‌های انبار'
        unique_together = [['product', 'warehouse']]
        indexes = [
            models.Index(fields=['product']),
            models.Index(fields=['warehouse']),
            models.Index(fields=['last_updated']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['product'],
                condition=models.Q(warehouse__isnull=True),
                name='uniq_stock_item_product_no_warehouse',
            ),
        ]


# Stock Transaction model - تراکنش‌های انبار (ورود/خروج)
class StockTransaction(models.Model):
    TRANSACTION_TYPE_CHOICES = [
        ('in', 'ورود به انبار'),
        ('out', 'خروج از انبار'),
        ('initial', 'موجودی اولیه'),
        ('adjustment', 'تعدیل موجودی'),
    ]
    
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name='transactions', verbose_name='موجودی')
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES, verbose_name='نوع تراکنش')
    quantity = models.DecimalField(max_digits=14, decimal_places=4, verbose_name='مقدار')
    unit = models.ForeignKey('purchases.Unit', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد')
    reference_type = models.CharField(max_length=50, blank=True, verbose_name='نوع مرجع')  # 'invoice', 'stock_request', 'initial', 'warehouse_voucher'
    reference_id = models.PositiveIntegerField(null=True, blank=True, verbose_name='شناسه مرجع')
    # حوالهٔ انبارِ مبدأ — برای تراکنش‌های سرویس انبار؛ تغییرناپذیرند و با PROTECT
    # حوالهٔ نهایی‌شده‌ای که تراکنش دارد قابل حذف نیست (ردیف ممیزی محفوظ می‌ماند)
    voucher = models.ForeignKey(
        'WarehouseVoucher', on_delete=models.PROTECT, null=True, blank=True,
        related_name='stock_transactions', verbose_name='حوالهٔ انبار',
        help_text='حوالهٔ انبارِ مبدأ این تراکنش (برای ردیف‌های سرویس انبار).',
    )
    description = models.TextField(blank=True, verbose_name='توضیحات')
    created_by = models.ForeignKey('purchases.User', on_delete=models.SET_NULL, null=True, verbose_name='ایجاد شده توسط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ تراکنش')
    previous_quantity = models.DecimalField(max_digits=14, decimal_places=4, verbose_name='موجودی قبل')
    new_quantity = models.DecimalField(max_digits=14, decimal_places=4, verbose_name='موجودی جدید')

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


class WarehouseVoucher(models.Model):
    """حواله انبار - سند ثبت جابجایی کالا (ورود، خروج، برگشت، فروش، ضایعات، انتقال، تعدیل)"""

    VOUCHER_TYPE_CHOICES = [
        ('receipt',     'حواله ورود'),
        ('issue',       'حواله خروج'),
        ('return',      'حواله برگشتی'),
        ('sale',        'حواله فروش'),
        ('waste',       'حواله ضایعات'),
        ('transfer',    'حواله انتقال'),
        ('adjustment',  'حواله تعدیل'),
        ('direct_delivery', 'حواله تحویل مستقیم'),
    ]

    STATUS_CHOICES = [
        ('draft',      'پیش‌نویس'),
        ('submitted',  'ثبت‌شده'),
        ('approved',   'تأییدشده'),
        ('finalized',  'نهایی‌شده'),
        ('cancelled',  'لغوشده'),
        ('reversed',   'برگشت‌خورده'),
    ]

    # فقط این نوع‌ها مسیر تأیید (approved) را طی می‌کنند؛ بقیه از ثبت مستقیم به
    # نهایی‌سازی می‌روند. حواله ضایعات در صورت عبور از آستانهٔ مشخص هم می‌تواند
    # نیازمند تأیید شود — تشخیص آستانه در لایه منطق انجام می‌شود.
    APPROVAL_REQUIRED_TYPES = {'adjustment'}

    cafe                 = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='warehouse_vouchers', verbose_name='کافه')
    source_warehouse     = models.ForeignKey('Warehouse', null=True, blank=True, on_delete=models.SET_NULL, related_name='source_vouchers', verbose_name='انبار مبدأ')
    destination_warehouse = models.ForeignKey('Warehouse', null=True, blank=True, on_delete=models.SET_NULL, related_name='destination_vouchers', verbose_name='انبار مقصد')
    # بخش گیرنده — فقط برای «تحویل مستقیم»؛ جنسِ تازه‌خرید بدون ورودِ ماندگار،
    # همان لحظه از انبارِ مقصد (محل عبور) به این بخش تحویل داده می‌شود
    department           = models.ForeignKey('Department', null=True, blank=True, on_delete=models.SET_NULL, related_name='warehouse_vouchers', verbose_name='بخش گیرنده')
    voucher_type         = models.CharField(max_length=20, choices=VOUCHER_TYPE_CHOICES, verbose_name='نوع حواله')
    status               = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='وضعیت')
    # برای حواله‌هایی که طبق آستانه نیازمند تأییدند (مثلاً ضایعات بالای آستانه)؛
    # اگر فعال باشد، نهایی‌سازی فقط از وضعیت «تأییدشده» مجاز است.
    requires_approval    = models.BooleanField(
        default=False, verbose_name='نیازمند تأیید',
        help_text='اگر فعال باشد، این حواله پیش از نهایی‌سازی باید «تأییدشده» شود (مثلاً ضایعات بالای آستانه).',
    )
    voucher_number       = models.CharField(max_length=50, verbose_name='شماره حواله')
    date                 = models.DateTimeField(default=timezone.now, verbose_name='تاریخ')
    description          = models.TextField(blank=True, verbose_name='توضیحات')

    # سند منبع (فاکتور، فروش، ضایعات، انبارگردانی و...) — اختیاری
    source_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='نوع سند منبع')
    source_object_id    = models.PositiveIntegerField(null=True, blank=True, verbose_name='شناسه سند منبع')
    source_object       = GenericForeignKey('source_content_type', 'source_object_id')

    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_warehouse_vouchers', verbose_name='ایجادکننده')
    # تأییدکننده/نهایی‌کننده — فقط وقتی گام تأیید واقعاً انجام شده باشد پر می‌شود
    approver   = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_warehouse_vouchers', verbose_name='تأییدکننده/نهایی‌کننده')
    approved_at  = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تأیید')
    finalized_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ نهایی‌سازی')
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ لغو')
    reversed_at  = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ برگشت')
    # حوالهٔ اصلی که این حواله آن را برگشت زده است (برگشتِ برگشت مجاز است)
    reverses   = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='reversed_by', verbose_name='حوالهٔ اصلی')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='آخرین بروزرسانی')

    def __str__(self):
        return f"{self.get_voucher_type_display()} - {self.voucher_number}"

    def clean(self):
        """قواعد انبار مبدأ/مقصد بر اساس نوع حواله و هم‌خوانی کافه"""
        super().clean()
        if self.reverses_id:
            if self.pk and self.reverses_id == self.pk:
                raise ValidationError({'reverses': 'حواله نمی‌تواند خودش را برگشت بزند.'})
            if self.cafe_id and self.reverses.cafe_id != self.cafe_id:
                raise ValidationError({'reverses': 'حوالهٔ برگشتی باید متعلق به همان کافه باشد.'})
        if self.cafe_id:
            if self.source_warehouse_id and self.source_warehouse.cafe_id != self.cafe_id:
                raise ValidationError({'source_warehouse': 'انبار مبدأ باید متعلق به همان کافه باشد.'})
            if self.destination_warehouse_id and self.destination_warehouse.cafe_id != self.cafe_id:
                raise ValidationError({'destination_warehouse': 'انبار مقصد باید متعلق به همان کافه باشد.'})
            if self.department_id and self.department.cafe_id != self.cafe_id:
                raise ValidationError({'department': 'بخش گیرنده باید متعلق به همان کافه باشد.'})
        if self.voucher_type == 'transfer':
            if not (self.source_warehouse_id and self.destination_warehouse_id):
                raise ValidationError({'source_warehouse': 'حواله انتقال به هر دو انبار مبدأ و مقصد نیاز دارد.'})
            if self.source_warehouse_id == self.destination_warehouse_id:
                raise ValidationError({'destination_warehouse': 'انبار مبدأ و مقصد نمی‌توانند یکی باشند.'})
        elif self.voucher_type == 'receipt':
            if not self.destination_warehouse_id:
                raise ValidationError({'destination_warehouse': 'حواله ورود باید انبار مقصد داشته باشد.'})
        elif self.voucher_type == 'direct_delivery':
            # انبارِ مقصد اینجا «محل عبور» کالای تازه‌خرید است: به موجودیِ همان
            # انبار اضافه و بلافاصله با تحویل به بخشِ گیرنده کم می‌شود (خالص صفر)
            if not self.destination_warehouse_id:
                raise ValidationError({'destination_warehouse': 'حواله تحویل مستقیم باید انبار (محل عبور کالا) داشته باشد.'})
            if not self.department_id:
                raise ValidationError({'department': 'حواله تحویل مستقیم باید بخش گیرنده داشته باشد.'})
        elif self.voucher_type == 'adjustment':
            if not self.source_warehouse_id:
                raise ValidationError({'source_warehouse': 'حواله تعدیل باید انبار مبدأ داشته باشد.'})
            if self.destination_warehouse_id:
                raise ValidationError({'destination_warehouse': 'حواله تعدیل نباید انبار مقصد داشته باشد.'})
        elif self.voucher_type in ('issue', 'return', 'sale', 'waste'):
            if not self.source_warehouse_id:
                raise ValidationError({'source_warehouse': 'این نوع حواله باید انبار مبدأ داشته باشد.'})

    class Meta:
        verbose_name        = 'حواله انبار'
        verbose_name_plural = 'حواله‌های انبار'
        ordering            = ['-created_at']
        unique_together     = [['cafe', 'voucher_number']]
        indexes             = [
            models.Index(fields=['cafe', 'voucher_type', '-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['source_warehouse', '-created_at']),
            models.Index(fields=['destination_warehouse', '-created_at']),
            models.Index(fields=['source_content_type', 'source_object_id']),
        ]
        constraints         = [
            models.UniqueConstraint(
                fields=['reverses'],
                condition=models.Q(reverses__isnull=False),
                name='uniq_warehouse_voucher_reverses',
            ),
        ]


class WarehouseVoucherItem(models.Model):
    """قلم (ردیف) حواله انبار — محصول یا موجودی انبار + مقدار و قیمت"""

    voucher = models.ForeignKey(WarehouseVoucher, on_delete=models.CASCADE, related_name='items', verbose_name='حواله')
    # هر قلم باید به محصول یا موجودی انبار متصل باشد (در clean بررسی می‌شود)
    product         = models.ForeignKey('Product', null=True, blank=True, on_delete=models.SET_NULL, verbose_name='محصول')
    stock_item      = models.ForeignKey('StockItem', null=True, blank=True, on_delete=models.SET_NULL, related_name='warehouse_voucher_items', verbose_name='موجودی انبار')
    unit            = models.ForeignKey('Unit', null=True, blank=True, on_delete=models.SET_NULL, verbose_name='واحد')
    # همه مقادیر اعشاری هستند؛ هیچ truncation صحیحی انجام نمی‌شود
    base_quantity   = models.DecimalField(max_digits=14, decimal_places=4, default=0, verbose_name='مقدار (واحد پایه)')
    unit_price      = models.DecimalField(max_digits=16, decimal_places=2, default=0, verbose_name='قیمت واحد (ریال)')
    description     = models.TextField(blank=True, verbose_name='توضیحات')

    @property
    def total_price(self):
        return self.base_quantity * self.unit_price

    def __str__(self):
        if self.product:
            name = self.product.name
        elif self.stock_item:
            name = self.stock_item.product.name
        else:
            name = '—'
        return f"{name} - {self.base_quantity}"

    def clean(self):
        """هر قلم باید به محصول یا موجودی انبار متصل باشد"""
        super().clean()
        if not (self.stock_item_id or self.product_id):
            raise ValidationError('هر قلم حواله باید به محصول یا موجودی انبار متصل باشد.')
        if self.stock_item_id and self.product_id:
            stock_item = self.stock_item
            if stock_item and stock_item.product_id != self.product_id:
                raise ValidationError({'product': 'محصول قلم با موجودی انبار انتخاب‌شده مطابقت ندارد.'})

    class Meta:
        verbose_name        = 'قلم حواله انبار'
        verbose_name_plural = 'اقلام حواله انبار'
        indexes             = [
            models.Index(fields=['voucher']),
        ]
