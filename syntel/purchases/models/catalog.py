from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


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
    # دسته‌های مواد غذایی: کالای این دسته‌ها باید در رسپی منو مصرف شود
    requires_recipe = models.BooleanField(
        default=False, verbose_name='نیازمند استفاده در رسپی (مواد غذایی)',
        help_text='اگر فعال باشد، کالاهای این دسته در رسپی‌های منو (بهای تمام شده) مصرف می‌شوند.',
    )
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='categories', verbose_name='کافه', null=True, blank=True)

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name

    def clean(self):
        super().clean()
        seen = {self.pk} if self.pk else set()
        parent = self.parent
        while parent is not None:
            if parent.pk in seen:
                raise ValidationError({'parent': 'دسته‌بندی نمی‌تواند والد یا نیای خودش باشد.'})
            if parent.pk is not None:
                seen.add(parent.pk)
            parent = parent.parent

    @property
    def product_count(self):
        """Return the count of active products in this category"""
        return self.product_set.filter(is_active=True).count()
    
    @property
    def full_path(self):
        """Return the full hierarchical path of the category"""
        parts = [self.name]
        seen = {self.pk} if self.pk else set()
        parent = self.parent
        while parent is not None and parent.pk not in seen:
            if parent.pk is not None:
                seen.add(parent.pk)
            parts.append(parent.name)
            parent = parent.parent
        return ' > '.join(reversed(parts))

    class Meta:
        verbose_name = 'دسته‌بندی'
        verbose_name_plural = 'دسته‌بندی‌ها'
        unique_together = [['name', 'parent', 'cafe']]
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'cafe'],
                condition=models.Q(parent__isnull=True),
                name='uniq_root_category_name_per_cafe',
            ),
        ]


DEPARTMENT_CHOICES = [
    ('general', 'عمومی کل (همه بخش‌ها)'),
    ('general_kitchen_bar', 'عمومی آشپزخانه و بار'),
    ('general_other', 'عمومی سایر بخش‌ها'),
]


# Product model for base products
class Product(models.Model):
    name = models.CharField(max_length=200, verbose_name='نام محصول')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='دسته‌بندی')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='واحد اصلی')
    min_stock = models.PositiveIntegerField(default=0, verbose_name='حداقل موجودی')
    current_stock = models.PositiveIntegerField(default=0, verbose_name="موجودی فعلی")
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True, verbose_name='عمر مفید (روز)',
                                                  help_text='مدت زمان ماندگاری محصول به روز؛ مثال: 180')
    usage_location = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES, default='general', verbose_name='محل استفاده')
    departments = models.ManyToManyField(
        'Department',
        blank=True,
        related_name='products',
        verbose_name='بخش‌های درخواست',
    )
    description = models.TextField(blank=True, verbose_name='توضیحات')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    # --- قیمت واحد کوچک‌ترین واحد (همگام با KukerItem مواد اولیه) -------------
    # قیمت همیشه به «کوچک‌ترین واحد» نرمال می‌شود: کیلوگرم→گرم، لیتر→میل.
    # مثال: ۲۵۰٬۰۰۰ تومان برای هر کیلو → base_unit_price=250 و smallest_unit='گرم'
    base_unit_price = models.DecimalField(
        max_digits=20, decimal_places=4, null=True, blank=True,
        verbose_name='بهای واحد کوچک‌ترین واحد',
        help_text='قیمت به ازای یک واحد از smallest_unit (مثلاً یک گرم یا یک میل).',
    )
    smallest_unit = models.CharField(
        max_length=20, blank=True, verbose_name='کوچک‌ترین واحد',
        help_text='واحد قیمت‌گذاری؛ مثل گرم، میل، عدد، پرس.',
    )

    # کالای جدیدی که از فرم درخواست ثبت شده و اطلاعات کامل آن (دسته‌بندی، واحد، ...)
    # هنوز توسط مدیریت محصولات تکمیل نشده — تا تکمیل، در لیست کالاهای پیشنهادی نمایش داده نمی‌شود.
    is_complete = models.BooleanField(default=True, verbose_name='اطلاعات کامل', help_text='کالای ناقص تا تکمیل اطلاعات در پیشنهادهای درخواست نمایش داده نمی‌شود.')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='products', verbose_name='کافه', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    OLD_USAGE_KEY_MAP = {
        'kitchen': 'آشپزخانه',
        'bar': 'بار',
        'counter': 'کانتر',
        'cashier': 'صندوق',
        'facilities': 'تاسیسات',
    }

    GENERAL_SCOPE_LABELS = {
        'general': 'عمومی کل',
        'general_kitchen_bar': 'عمومی آشپزخانه و بار',
        'general_other': 'عمومی سایر بخش‌ها',
    }

    @property
    def request_section_labels(self):
        """نام بخش‌های درخواست انتخاب‌شده برای این محصول؛ برای عمومی برچسب دامنه عمومی و برای بدون بخش ['بدون بخش']."""
        if self.pk:
            depts = list(self.departments.all())
            if depts:
                return [d.name for d in depts]
        if self.usage_location == 'none':
            return ['بدون بخش']
        if not self.usage_location or self.usage_location in self.GENERAL_SCOPE_LABELS:
            return [self.GENERAL_SCOPE_LABELS.get(self.usage_location or 'general', 'عمومی کل')]
        if self.usage_location == 'multi':
            return ['چند بخش']
        return [self.OLD_USAGE_KEY_MAP.get(self.usage_location, self.usage_location)]

    @property
    def request_sections_display(self):
        return '، '.join(self.request_section_labels)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'محصول'
        verbose_name_plural = 'محصولات'
        ordering = ['name']
        unique_together = [['name', 'cafe']]
        indexes = [
            models.Index(fields=['cafe', 'is_active']),
            models.Index(fields=['cafe', '-created_at']),
        ]


# Price History Tracking
class PriceHistory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_history', verbose_name='محصول')
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='قیمت')
    date = models.DateTimeField(default=timezone.now, verbose_name='تاریخ و زمان')
    invoice = models.ForeignKey('purchases.Invoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='price_histories', verbose_name='فاکتور')
    supplier = models.ForeignKey('purchases.Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='price_histories', verbose_name='تأمین‌کننده')
    
    # Percentage change from previous price
    percent_change = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='درصد تغییر')
    
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
