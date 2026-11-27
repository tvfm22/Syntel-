"""
مدیریت اموال و اثاثیه سکشن‌ها (Assets & Furnishings).
کاملاً مجزا از انبار مصرفی (Warehouse / StockItem / Product).
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class AssetCategory(models.Model):
    """دسته‌بندی اموال و اثاثیه (با پشتیبانی از ساختار درختی)."""

    cafe = models.ForeignKey(
        'Cafe',
        on_delete=models.CASCADE,
        related_name='asset_categories',
        verbose_name='کافه',
    )
    name = models.CharField(max_length=120, verbose_name='نام دسته‌بندی')
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subcategories',
        verbose_name='دسته‌بندی والد',
    )
    description = models.TextField(blank=True, verbose_name='توضیحات')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')

    def __str__(self):
        if self.parent_id:
            return f'{self.parent.name} > {self.name}'
        return self.name

    def clean(self):
        super().clean()
        seen = {self.pk} if self.pk else set()
        parent = self.parent
        while parent is not None:
            if parent.pk in seen:
                raise ValidationError({'parent': 'دسته‌بندی نمی‌تواند والد یا نیای خودش باشد.'})
            if self.cafe_id and parent.cafe_id and parent.cafe_id != self.cafe_id:
                raise ValidationError({'parent': 'دسته‌بندی والد باید متعلق به همان کافه باشد.'})
            if parent.pk is not None:
                seen.add(parent.pk)
            parent = parent.parent

    class Meta:
        verbose_name = 'دسته‌بندی اموال'
        verbose_name_plural = 'دسته‌بندی‌های اموال'
        ordering = ['name']
        unique_together = [['cafe', 'parent', 'name']]
        constraints = [
            models.UniqueConstraint(
                fields=['cafe', 'name'],
                condition=Q(parent__isnull=True),
                name='uniq_root_asset_category_name_per_cafe',
            ),
        ]
        indexes = [
            models.Index(fields=['cafe', 'is_active']),
        ]


class AssetCatalog(models.Model):
    """کاتالوگ اموال و تجهیزات کافه (اقلام تعدادی یا پلاک‌دار)."""

    TRACKING_MODE_CHOICES = [
        ('quantity', 'تعدادی (ظروف، صندلی و...)'),
        ('serialized', 'پلاکدار / تکتک (تجهیزات و دستگاهها)'),
    ]

    cafe = models.ForeignKey(
        'Cafe',
        on_delete=models.CASCADE,
        related_name='asset_catalogs',
        verbose_name='کافه',
    )
    category = models.ForeignKey(
        AssetCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        verbose_name='دسته‌بندی',
    )
    name = models.CharField(max_length=200, verbose_name='نام مال / تجهیز')
    code = models.CharField(max_length=50, verbose_name='کد کالا')
    brand_model = models.CharField(max_length=150, blank=True, verbose_name='برند / مدل')
    tracking_mode = models.CharField(
        max_length=20,
        choices=TRACKING_MODE_CHOICES,
        default='quantity',
        verbose_name='روش ردیابی',
    )
    image = models.ImageField(
        upload_to='assets/catalog/',
        null=True,
        blank=True,
        verbose_name='تصویر',
    )
    thumbnail = models.ImageField(
        upload_to='assets/catalog/thumbs/',
        null=True,
        blank=True,
        verbose_name='تصویر بندانگشتی',
    )
    estimated_unit_value = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        default=0,
        verbose_name='ارزش تخمینی واحد',
    )
    expected_life_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='عمر مفید مورد انتظار (ماه)',
    )
    description = models.TextField(blank=True, verbose_name='توضیحات')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return f'{self.name} ({self.code})'

    def clean(self):
        super().clean()
        if self.category_id and self.cafe_id and self.category.cafe_id != self.cafe_id:
            raise ValidationError({'category': 'دسته‌بندی باید متعلق به همان کافه باشد.'})

    class Meta:
        verbose_name = 'کاتالوگ اموال'
        verbose_name_plural = 'کاتالوگ اموال'
        ordering = ['name']
        unique_together = [['cafe', 'code'], ['cafe', 'name']]
        indexes = [
            models.Index(fields=['cafe', 'tracking_mode', 'is_active']),
        ]


class DepartmentAssetStock(models.Model):
    """موجودی اموال هر بخش (برای اقلام تعدادی و جمع همگام‌سازی‌شده اقلام پلاک‌دار)."""

    cafe = models.ForeignKey(
        'Cafe',
        on_delete=models.CASCADE,
        related_name='department_asset_stocks',
        verbose_name='کافه',
    )
    department = models.ForeignKey(
        'Department',
        on_delete=models.CASCADE,
        related_name='asset_stocks',
        verbose_name='بخش',
    )
    asset = models.ForeignKey(
        AssetCatalog,
        on_delete=models.PROTECT,
        related_name='department_stocks',
        verbose_name='کالای اموال',
    )
    quantity_healthy = models.PositiveIntegerField(default=0, verbose_name='تعداد سالم')
    quantity_damaged = models.PositiveIntegerField(default=0, verbose_name='تعداد معیوب')
    min_expected_quantity = models.PositiveIntegerField(
        default=0,
        verbose_name='حد استاندارد/نرمال بخش',
    )
    location_note = models.CharField(max_length=200, blank=True, verbose_name='محل استقرار')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='آخرین بروزرسانی')

    @property
    def total_quantity(self) -> int:
        return (self.quantity_healthy or 0) + (self.quantity_damaged or 0)

    @property
    def shortage_quantity(self) -> int:
        return max(0, (self.min_expected_quantity or 0) - (self.quantity_healthy or 0))

    def clean(self):
        super().clean()
        if self.department_id and self.cafe_id and self.department.cafe_id != self.cafe_id:
            raise ValidationError({'department': 'بخش باید متعلق به همان کافه باشد.'})
        if self.asset_id and self.cafe_id and self.asset.cafe_id != self.cafe_id:
            raise ValidationError({'asset': 'مال انتخاب‌شده باید متعلق به همان کافه باشد.'})
        if self.department_id and self.asset_id and self.department.cafe_id != self.asset.cafe_id:
            raise ValidationError('بخش و مال باید متعلق به یک کافه باشند.')

    def __str__(self):
        return f'{self.department.name} - {self.asset.name}: {self.quantity_healthy}/{self.quantity_damaged}'

    class Meta:
        verbose_name = 'موجودی اموال بخش'
        verbose_name_plural = 'موجودی‌های اموال بخش‌ها'
        unique_together = [['department', 'asset']]
        indexes = [
            models.Index(fields=['cafe', 'department']),
            models.Index(fields=['cafe', 'asset']),
        ]


class AssetUnit(models.Model):
    """واحد تکی و پلاک‌دار اموال (برای اقلام serialized)."""

    STATUS_CHOICES = [
        ('active', 'سالم و فعال'),
        ('needs_repair', 'نیازمند تعمیر'),
        ('in_repair', 'در تعمیرگاه'),
        ('lost', 'مفقودی'),
        ('disposed', 'اسقاط‌شده'),
    ]

    cafe = models.ForeignKey(
        'Cafe',
        on_delete=models.CASCADE,
        related_name='asset_units',
        verbose_name='کافه',
    )
    asset = models.ForeignKey(
        AssetCatalog,
        on_delete=models.PROTECT,
        related_name='units',
        verbose_name='کالای اموال',
    )
    department = models.ForeignKey(
        'Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_units',
        verbose_name='بخش محل استقرار',
    )
    asset_tag = models.CharField(max_length=60, verbose_name='کد پلاک')
    serial_number = models.CharField(max_length=120, blank=True, verbose_name='شماره سریال')
    purchase_date = models.DateField(null=True, blank=True, verbose_name='تاریخ خرید')
    warranty_until = models.DateField(null=True, blank=True, verbose_name='پایان گارانتی')
    purchase_price = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        default=0,
        verbose_name='قیمت خرید',
    )
    invoice = models.ForeignKey(
        'Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_units',
        verbose_name='فاکتور مرتبط',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name='وضعیت',
    )
    custodian = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='custodied_asset_units',
        verbose_name='تحویل‌گیرنده / مسئول',
    )
    photo = models.ImageField(
        upload_to='assets/units/',
        null=True,
        blank=True,
        verbose_name='تصویر',
    )
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def clean(self):
        super().clean()
        if self.asset_id and self.cafe_id and self.asset.cafe_id != self.cafe_id:
            raise ValidationError({'asset': 'مال باید متعلق به همان کافه باشد.'})
        if self.asset_id and self.asset.tracking_mode != 'serialized':
            raise ValidationError({'asset': 'ثبت پلاک فقط برای اقلام پلاک‌دار (serialized) مجاز است.'})
        if self.department_id and self.cafe_id and self.department.cafe_id != self.cafe_id:
            raise ValidationError({'department': 'بخش باید متعلق به همان کافه باشد.'})
        if self.invoice_id and self.cafe_id and self.invoice.cafe_id != self.cafe_id:
            raise ValidationError({'invoice': 'فاکتور باید متعلق به همان کافه باشد.'})

    def __str__(self):
        return f'{self.asset.name} [{self.asset_tag}]'

    class Meta:
        verbose_name = 'پلاک اموال'
        verbose_name_plural = 'پلاک‌های اموال'
        ordering = ['asset__name', 'asset_tag']
        unique_together = [['cafe', 'asset_tag']]
        indexes = [
            models.Index(fields=['cafe', 'status']),
            models.Index(fields=['cafe', 'department']),
        ]


class AssetAudit(models.Model):
    """جلسه انبارگردانی / شمارش اموال بخش."""

    AUDIT_TYPE_CHOICES = [
        ('full', 'انبارگردانی کامل'),
        ('spot', 'شمارش موردی'),
        ('handover', 'تحویل و تحول مسئول'),
    ]
    STATUS_CHOICES = [
        ('draft', 'پیشنویس'),
        ('in_progress', 'در حال شمارش'),
        ('submitted', 'ارسالشده برای بررسی'),
        ('recount_requested', 'ارجاع برای بازشماری'),
        ('approved', 'تأیید و نهاییشده'),
        ('cancelled', 'لغوشده'),
    ]

    cafe = models.ForeignKey(
        'Cafe',
        on_delete=models.CASCADE,
        related_name='asset_audits',
        verbose_name='کافه',
    )
    department = models.ForeignKey(
        'Department',
        on_delete=models.CASCADE,
        related_name='asset_audits',
        verbose_name='بخش',
    )
    title = models.CharField(max_length=200, verbose_name='عنوان انبارگردانی')
    audit_type = models.CharField(
        max_length=20,
        choices=AUDIT_TYPE_CHOICES,
        default='full',
        verbose_name='نوع شمارش',
    )
    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default='in_progress',
        verbose_name='وضعیت',
    )
    blind_count = models.BooleanField(default=False, verbose_name='شمارش کور')
    notes = models.TextField(blank=True, verbose_name='یادداشت‌ها')
    review_notes = models.TextField(blank=True, verbose_name='یادداشت بررسی / بازشماری')
    created_by = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_asset_audits',
        verbose_name='ایجادکننده',
    )
    counted_by = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='counted_asset_audits',
        verbose_name='شمارش‌کننده',
    )
    reviewed_by = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_asset_audits',
        verbose_name='بررسی‌کننده',
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='زمان شروع')
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='زمان ارسال')
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='زمان تأیید نهایی')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def clean(self):
        super().clean()
        if self.department_id and self.cafe_id and self.department.cafe_id != self.cafe_id:
            raise ValidationError({'department': 'بخش باید متعلق به همان کافه باشد.'})

    def __str__(self):
        return f'{self.title} - {self.department.name} ({self.get_status_display()})'

    class Meta:
        verbose_name = 'انبارگردانی اموال'
        verbose_name_plural = 'انبارگردانی‌های اموال'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['cafe', 'status']),
            models.Index(fields=['cafe', 'department', '-created_at']),
        ]


class AssetAuditItem(models.Model):
    """ردیف شمارش در جلسه انبارگردانی اموال."""

    CONDITION_CHOICES = [
        ('active', 'موجود و سالم'),
        ('needs_repair', 'موجود و معیوب'),
        ('in_repair', 'در تعمیرگاه'),
        ('lost', 'مفقود'),
    ]

    audit = models.ForeignKey(
        AssetAudit,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='جلسه انبارگردانی',
    )
    asset = models.ForeignKey(
        AssetCatalog,
        on_delete=models.PROTECT,
        related_name='audit_items',
        verbose_name='کالای اموال',
    )
    asset_unit = models.ForeignKey(
        AssetUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_items',
        verbose_name='پلاک',
    )
    expected_healthy = models.PositiveIntegerField(default=0, verbose_name='تعداد سالم مورد انتظار')
    expected_damaged = models.PositiveIntegerField(default=0, verbose_name='تعداد معیوب مورد انتظار')
    counted_healthy = models.PositiveIntegerField(null=True, blank=True, verbose_name='تعداد سالم شمارش‌شده')
    counted_damaged = models.PositiveIntegerField(null=True, blank=True, verbose_name='تعداد معیوب شمارش‌شده')
    expected_status = models.CharField(max_length=20, blank=True, verbose_name='وضعیت مورد انتظار')
    counted_status = models.CharField(
        max_length=20,
        choices=CONDITION_CHOICES,
        blank=True,
        verbose_name='وضعیت شمارش‌شده',
    )
    is_counted = models.BooleanField(default=False, verbose_name='شمارش شده')
    needs_recount = models.BooleanField(default=False, verbose_name='نیازمند بازشماری')
    is_unexpected_item = models.BooleanField(default=False, verbose_name='قلم یافت‌شده خارج از لیست')
    evidence_photo = models.ImageField(
        upload_to='assets/audit_evidence/',
        null=True,
        blank=True,
        verbose_name='تصویر مستندات',
    )
    note = models.TextField(blank=True, verbose_name='توضیحات')
    counted_at = models.DateTimeField(null=True, blank=True, verbose_name='زمان شمارش')

    @property
    def variance_healthy(self) -> int:
        if self.counted_healthy is None:
            return 0
        return self.counted_healthy - self.expected_healthy

    @property
    def variance_damaged(self) -> int:
        if self.counted_damaged is None:
            return 0
        return self.counted_damaged - self.expected_damaged

    @property
    def variance_total(self) -> int:
        return self.variance_healthy + self.variance_damaged

    @property
    def variance_value(self) -> Decimal:
        unit_val = Decimal('0')
        if self.asset_unit_id and self.asset_unit and self.asset_unit.purchase_price:
            unit_val = self.asset_unit.purchase_price
        elif self.asset_id and self.asset.estimated_unit_value:
            unit_val = self.asset.estimated_unit_value
        return Decimal(self.variance_total) * Decimal(unit_val)

    def __str__(self):
        target = self.asset_unit.asset_tag if self.asset_unit_id else self.asset.name
        return f'{self.audit.title} - {target}'

    class Meta:
        verbose_name = 'ردیف انبارگردانی اموال'
        verbose_name_plural = 'ردیف‌های انبارگردانی اموال'
        ordering = ['id']
        indexes = [
            models.Index(fields=['audit', 'is_counted']),
            models.Index(fields=['audit', 'needs_recount']),
        ]


class AssetMovement(models.Model):
    """سند تغییرناپذیر گردش و تاریخچه اموال."""

    MOVEMENT_TYPE_CHOICES = [
        ('initial', 'موجودی اولیه'),
        ('addition', 'ورود / خرید جدید'),
        ('transfer', 'انتقال بین بخشها'),
        ('disposal', 'شکستگی / اسقاط'),
        ('repair_out', 'ارسال به تعمیر'),
        ('repair_in', 'بازگشت از تعمیر'),
        ('audit_adjustment', 'تعدیل انبارگردانی'),
    ]
    STATUS_CHOICES = [
        ('pending', 'در انتظار تأیید'),
        ('pending_receipt', 'در انتظار تحویل مقصد'),
        ('approved', 'تأیید و اعمالشده'),
        ('rejected', 'ردشده'),
        ('cancelled', 'لغوشده'),
    ]
    CONDITION_BUCKET_CHOICES = [
        ('healthy', 'سالم'),
        ('damaged', 'معیوب'),
    ]

    cafe = models.ForeignKey(
        'Cafe',
        on_delete=models.CASCADE,
        related_name='asset_movements',
        verbose_name='کافه',
    )
    movement_type = models.CharField(
        max_length=25,
        choices=MOVEMENT_TYPE_CHOICES,
        verbose_name='نوع گردش',
    )
    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='وضعیت',
    )
    asset = models.ForeignKey(
        AssetCatalog,
        on_delete=models.PROTECT,
        related_name='movements',
        verbose_name='کالای اموال',
    )
    asset_unit = models.ForeignKey(
        AssetUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements',
        verbose_name='پلاک',
    )
    from_department = models.ForeignKey(
        'Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='outgoing_asset_movements',
        verbose_name='بخش مبدأ',
    )
    to_department = models.ForeignKey(
        'Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incoming_asset_movements',
        verbose_name='بخش مقصد',
    )
    quantity = models.IntegerField(default=1, verbose_name='تعداد')
    condition_bucket = models.CharField(
        max_length=20,
        choices=CONDITION_BUCKET_CHOICES,
        default='healthy',
        verbose_name='وضعیت سلامت',
    )
    unit_value_snapshot = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        default=0,
        verbose_name='ارزش واحد در زمان ثبت',
    )
    reason = models.TextField(blank=True, verbose_name='علت / توضیحات')
    evidence_photo = models.ImageField(
        upload_to='assets/evidence/',
        null=True,
        blank=True,
        verbose_name='تصویر مستندات',
    )
    invoice = models.ForeignKey(
        'Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_movements',
        verbose_name='فاکتور مرتبط',
    )
    audit = models.ForeignKey(
        AssetAudit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements',
        verbose_name='جلسه انبارگردانی',
    )
    created_by = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_asset_movements',
        verbose_name='ثبت‌کننده',
    )
    approved_by = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_asset_movements',
        verbose_name='تأییدکننده',
    )
    received_by = models.ForeignKey(
        'purchases.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_asset_movements',
        verbose_name='تحویل‌گیرنده',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ثبت')
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تأیید')
    received_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ تحویل')

    def __str__(self):
        return f'{self.get_movement_type_display()} - {self.asset.name} ({self.quantity}) [{self.status}]'

    class Meta:
        verbose_name = 'گردش اموال'
        verbose_name_plural = 'گردش‌های اموال'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['cafe', 'movement_type', 'status']),
            models.Index(fields=['cafe', '-created_at']),
            models.Index(fields=['asset', '-created_at']),
        ]
