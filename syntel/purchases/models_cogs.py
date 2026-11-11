"""
COGS, Menu Recipe, and Product Price Entry Models.
تفکیک‌شده از purchases/models.py برای بهبود ساختار معماری و نگهداری‌پذیری.
"""
from decimal import Decimal
from django.db import models
from django.utils import timezone


class MenuSection(models.Model):
    """بخش منو برای یک کافه: یا «غذای منو» یا «نیمه‌آماده».

    بخش‌های پیش‌فرض نیمه‌آماده (آشپزخانه/بار) برای هر کافه ساخته می‌شوند؛
    بخش‌های غذای منو را مدیر می‌تواند آزادانه اضافه کند.
    """
    KIND_DISH = 'dish'
    KIND_PREP = 'prep'
    KIND_CHOICES = [(KIND_DISH, 'غذای منو'), (KIND_PREP, 'نیمه‌آماده')]

    # بخش‌های پیش‌فرضی که برای هر کافه ساخته می‌شوند
    DEFAULT_PREP_SECTIONS = ['آماده سازی آشپزخانه', 'آماده سازی بار']

    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='menu_sections',
                             verbose_name='کافه', null=True, blank=True)
    name = models.CharField(max_length=120, verbose_name='نام بخش')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_DISH,
                            verbose_name='نوع')
    display_order = models.PositiveIntegerField(default=0, verbose_name='ترتیب نمایش')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'بخش منو'
        verbose_name_plural = 'بخش‌های منو'
        ordering = ['kind', 'display_order', 'name']
        unique_together = [['cafe', 'name', 'kind']]

    def __str__(self):
        return f'{self.name} ({self.get_kind_display()})'


class KukerItem(models.Model):
    """یک قلم در سیستم منوی کوکر: ماده اولیه، آیتم نیمه‌آماده یا غذای منو.

    ساختار از فایل اکسل «منو کوکر» استخراج می‌شود (مانجمنت‌کامند import_kuker_menu).
    هر قلم متعلق به یک کافه است؛ کلید نرمال‌شده فقط داخل همان کافه یکتاست.
    مواد اولیه (kind='raw') با یک Product هم‌کافه هم‌گام می‌شوند (فیلد product).
    """
    KIND_RAW  = 'raw'    # ماده اولیه (کالای خام)
    KIND_PREP = 'prep'   # نیمه‌آماده (آماده سازی آشپزخانه/بار)
    KIND_DISH = 'dish'   # غذای نهایی منو

    cafe      = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='kuker_items',
                                  verbose_name='کافه', null=True, blank=True)
    name      = models.CharField(max_length=200, verbose_name='نام')
    key       = models.CharField(max_length=200, db_index=True, verbose_name='کلید نرمال‌شده')
    kind      = models.CharField(max_length=10, choices=[(KIND_RAW,'ماده اولیه'),(KIND_PREP,'نیمه‌آماده'),(KIND_DISH,'غذای منو')], verbose_name='نوع')
    section   = models.ForeignKey('MenuSection', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='items', verbose_name='بخش منو')
    # اتصال به کالای انبار برای مواد اولیه — قیمت و نام بین این دو هم‌گام می‌ماند
    product   = models.OneToOneField('Product', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='cogs_item', verbose_name='کالای انبار')
    sheet     = models.CharField(max_length=60, blank=True, verbose_name='برگه منبع اکسل')
    category  = models.CharField(max_length=120, blank=True, verbose_name='دسته منو')
    group     = models.CharField(max_length=120, blank=True, verbose_name='گروه کالا')
    code      = models.CharField(max_length=30, blank=True, verbose_name='کد کالا')
    # خروجی یک بچ رسپی
    out_qty   = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True, verbose_name='مقدار خروجی')
    out_unit  = models.CharField(max_length=20, blank=True, verbose_name='واحد خروجی')
    # بهای واحد برای مواد اولیه — همیشه به ازای «کوچک‌ترین واحد» ذخیره می‌شود
    price     = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True, verbose_name='بهای واحد کوچک‌ترین واحد (ماده اولیه)')
    # مقادیر «قیمت تمام شده» که خود اکسل اعلام کرده (برای اعتبارسنجی)
    stated_total = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, verbose_name='قیمت تمام شده کل (اکسل)')
    stated_fee   = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, verbose_name='قیمت تمام شده فی (اکسل)')
    is_custom  = models.BooleanField(default=False, verbose_name='دستی تعریف شده')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'قلم منوی کوکر'
        verbose_name_plural = 'اقلام منوی کوکر'
        ordering            = ['kind', 'name']
        unique_together     = [['cafe', 'key']]

    def __str__(self):
        return self.name

    @property
    def is_used_in_recipe(self):
        """آیا این ماده در رسپی هیچ قلم دیگری مصرف شده است؟"""
        return KukerRecipeLine.objects.filter(material_id=self.pk).exists()


class KukerRecipeLine(models.Model):
    """یک سطر رسپی: برای ساخت یک (بچ) از item، از material به مقدار qty واحد نیاز است."""
    item     = models.ForeignKey(KukerItem, on_delete=models.CASCADE, related_name='lines', verbose_name='قلم')
    material = models.ForeignKey(KukerItem, on_delete=models.CASCADE, related_name='used_in', verbose_name='ماده')
    quantity = models.DecimalField(max_digits=14, decimal_places=3, verbose_name='مقدار')
    unit     = models.CharField(max_length=20, blank=True, verbose_name='واحد')
    fee      = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, verbose_name='بهای واحد در رسپی (اکسل)')

    class Meta:
        verbose_name        = 'سطر رسپی کوکر'
        verbose_name_plural = 'سطرهای رسپی کوکر'
        indexes             = [models.Index(fields=['item'])]

    def __str__(self):
        return f"{self.item.name} ← {self.material.name} {self.quantity} {self.unit}"


class CogsReport(models.Model):
    """گزارش بهای تمام شده برای یک بازه: تعداد فروش هر غذا + نتیجه محاسبه."""
    cafe         = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='cogs_reports',
                                     verbose_name='کافه', null=True, blank=True)
    period_start = models.DateField(verbose_name='از تاریخ')
    period_end   = models.DateField(verbose_name='تا تاریخ')
    created_by   = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='ثبت‌کننده')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'گزارش بهای تمام شده'
        verbose_name_plural = 'گزارش‌های بهای تمام شده'
        ordering            = ['-created_at']

    def __str__(self):
        return f"بهای تمام شده {self.period_start} تا {self.period_end}"


class CogsReportLine(models.Model):
    """تعداد فروش هر غذا در یک گزارش."""
    report = models.ForeignKey(CogsReport, on_delete=models.CASCADE, related_name='lines', verbose_name='گزارش')
    item   = models.ForeignKey(KukerItem, on_delete=models.CASCADE, related_name='sales_lines', verbose_name='غذا')
    qty    = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name='تعداد فروش')

    class Meta:
        verbose_name        = 'سطر فروش گزارش'
        verbose_name_plural = 'سطرهای فروش گزارش'
        unique_together     = [('report', 'item')]

    def __str__(self):
        return f"{self.item.name} × {self.qty}"


class KukerItemCostHistory(models.Model):
    """تاریخچه بهای واحد هر غذای منو / نیمه‌آماده.

    هر بار که بهای محاسبه‌شده یک قلم تغییر کند (تغییر قیمت ماده اولیه یا ویرایش
    رسپی) یک ردیف جدید ثبت می‌شود — نه بازنویسی ردیف قبلی.
    """
    item          = models.ForeignKey(KukerItem, on_delete=models.CASCADE,
                                      related_name='cost_history', verbose_name='قلم')
    cost_per_unit = models.DecimalField(max_digits=20, decimal_places=4,
                                        verbose_name='بهای واحد')
    recorded_at   = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ثبت')
    note          = models.CharField(max_length=255, blank=True, verbose_name='توضیح')

    class Meta:
        verbose_name        = 'تاریخچه بهای قلم منو'
        verbose_name_plural = 'تاریخچه بهای اقلام منو'
        ordering            = ['-recorded_at', '-id']
        indexes             = [models.Index(fields=['item', '-recorded_at'])]

    def __str__(self):
        return f'{self.item.name} = {self.cost_per_unit} @ {self.recorded_at:%Y-%m-%d %H:%M}'


class ProductPriceEntry(models.Model):
    """یک «وارد کردن قیمت» برای ماده اولیه — هم ورودی کاربر، هم نتیجهٔ نرمال‌شده.

    `PriceHistory` عمداً دست نخورده ماند: آن جدول به فاکتور و تأمین‌کننده گره خورده
    و قیمت را با دو رقم اعشار نگه می‌دارد، در حالی که بهای هر گرم می‌تواند
    ۰٫۰۰۰۴ تومان باشد. این مدل هر دو عدد را کنار هم نگه می‌دارد:

      * `entered_amount` + `entered_unit` (+ ابعاد بسته) — همان چیزی که کاربر تایپ کرد
      * `price_per_smallest_unit` + `smallest_unit` — همان چیزی که در محاسبات استفاده می‌شود
    """
    product   = models.ForeignKey('Product', on_delete=models.CASCADE,
                                  related_name='price_entries', verbose_name='کالا')
    cafe      = models.ForeignKey('Cafe', on_delete=models.CASCADE, null=True, blank=True,
                                  related_name='product_price_entries', verbose_name='کافه')
    entered_amount = models.DecimalField(max_digits=20, decimal_places=4,
                                         verbose_name='مبلغ وارد شده')
    entered_unit   = models.CharField(max_length=30, blank=True,
                                      verbose_name='واحد وارد شده')
    # فقط برای قیمت «بسته» پر می‌شود: مثلاً بستهٔ ۵ کیلوگرمی
    package_size   = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True,
                                         verbose_name='اندازهٔ بسته')
    package_unit   = models.CharField(max_length=20, blank=True, verbose_name='واحد بسته')
    price_per_smallest_unit = models.DecimalField(max_digits=20, decimal_places=4,
                                                  verbose_name='بهای هر واحد کوچک')
    smallest_unit  = models.CharField(max_length=20, blank=True, verbose_name='کوچک‌ترین واحد')
    note           = models.CharField(max_length=255, blank=True, verbose_name='توضیح')
    recorded_by    = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True,
                                       verbose_name='ثبت‌کننده')
    recorded_at    = models.DateTimeField(default=timezone.now, verbose_name='تاریخ ثبت')

    class Meta:
        verbose_name        = 'تاریخچهٔ قیمت ماده اولیه'
        verbose_name_plural = 'تاریخچهٔ قیمت مواد اولیه'
        ordering            = ['-recorded_at', '-id']
        indexes             = [models.Index(fields=['product', '-recorded_at'])]

    def __str__(self):
        return (f'{self.product.name}: {self.entered_amount} {self.entered_unit} '
                f'→ {self.price_per_smallest_unit}/{self.smallest_unit}')
