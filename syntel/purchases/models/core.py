from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


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
        ('requester', 'درخواست‌دهنده'),
        ('purchasing', 'خرید'),
        ('warehouse', 'انبار'),
        ('admin', 'ادمین'),
        ('manager', 'مدیر'),
        ('accounting', 'دفتر حسابداری'),
        ('finance_manager', 'مدیر مالی'),
        ('ceo', 'مدیرعامل'),
    ]
    role: models.CharField = models.CharField(max_length=20, choices=ROLE_CHOICES, default='requester')
    # نقش دوم - امکان داشتن هر دو نقش مسئول خرید و مسئول انبار به صورت همزمان
    secondary_role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, blank=True, null=True,
        verbose_name='نقش دوم', help_text='اگر کاربر هم مسئول خرید و هم مسئول انبار باشد'
    )
    cafe = models.ForeignKey('Cafe', on_delete=models.SET_NULL, null=True, blank=True, related_name='users', verbose_name='کافه')
    department = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True, blank=True, related_name='users', verbose_name='بخش')
    
    # Roles that can switch between cafes
    ADMIN_ROLES = ['manager', 'ceo']
    # Roles that see cafe selection on login
    CAFE_SELECTION_ROLES = ['admin', 'manager', 'finance_manager', 'accounting', 'ceo']

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"

    @property
    def has_purchasing_role(self):
        """آیا کاربر نقش مسئول خرید دارد"""
        return self.role == 'purchasing' or self.secondary_role == 'purchasing'

    @property
    def has_warehouse_role(self):
        """آیا کاربر نقش مسئول انبار دارد"""
        return self.role == 'warehouse' or self.secondary_role == 'warehouse'

    @property
    def has_dual_role(self):
        """آیا کاربر هر دو نقش مسئول خرید و مسئول انبار را دارد"""
        return self.has_purchasing_role and self.has_warehouse_role

    @property
    def can_switch_cafe(self):
        """
        سوپر ادمین‌ها (cafe=None و نقش ادمین) می‌توانند بین کافه‌ها جابجا شوند
        """
        return self.role in self.CAFE_SELECTION_ROLES and self.cafe is None
    
    @property
    def needs_cafe_selection(self):
        """
        آیا کاربر نیاز به انتخاب کافه در لاگین دارد
        
        فقط کاربرانی که:
        1. نقش ادمین دارند (manager, finance_manager, accounting, ceo)
        2. و کافه مشخصی ندارند (cafe=None)
        
        صفحه انتخاب کافه می‌بینند. این کاربران "سوپر ادمین" هستند.
        کاربران عادی و کاربران ادمین با کافه مشخص، مستقیم به کافه خودشان می‌روند.
        """
        return self.role in self.CAFE_SELECTION_ROLES and self.cafe is None

    @property
    def department_display(self):
        """بخش نمایشی کاربر - برای کاربران درخواست‌دهنده نام بخش را برمی‌گرداند"""
        if self.role == 'requester' and self.department:
            return self.department.name
        # Fallback mapping for old roles removed from ROLE_CHOICES
        old_role_names = {
            'kitchen': 'آشپزخانه',
            'bar': 'بار',
            'counter': 'کانتر',
            'cashier': 'صندوق',
            'facilities': 'تاسیسات',
        }
        return old_role_names.get(self.role, self.get_role_display())


# Department model for user departments
class Department(models.Model):
    name = models.CharField(max_length=100, verbose_name='نام بخش')
    # هر بخش منحصراً به یک کافه تعلق دارد؛ بخش سراسری (cafe=None) دیگر وجود ندارد
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='departments', verbose_name='کافه')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')

    # انبار/محل انبارداری مرتبط با بخش — اختیاری؛ باید متعلق به همان کافه باشد
    warehouse = models.ForeignKey(
        'Warehouse', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='departments', verbose_name='انبار مرتبط',
        help_text='انبار یا محل انبارداری این بخش؛ فقط انبارهای همان کافه قابل انتخاب هستند.',
    )
    # انبارِ بخش (مثلاً یخچال آشپزخانه) در مقابل انبار مرکزی کافه
    is_department_warehouse = models.BooleanField(
        default=False, verbose_name='انبار بخش',
        help_text='اگر فعال باشد، انبار مرتبط انبارِ بخش است؛ اگر غیرفعال باشد انبار مرکزی کافه است.',
    )
    is_recipe_department = models.BooleanField(
        default=False, verbose_name='بخش دارای رسپی (آشپزخانه و بار)',
        help_text='اگر فعال باشد، این بخش جزو بخش‌های آشپزخانه و بار (دارای رسپی) محسوب می‌شود.',
    )

    def clean(self):
        """انبار مرتبط باید متعلق به همان کافهٔ بخش باشد"""
        super().clean()
        if self.warehouse_id and self.cafe_id and self.warehouse.cafe_id != self.cafe_id:
            raise ValidationError({'warehouse': 'انبار مرتبط باید متعلق به همان کافهٔ بخش باشد.'})

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'بخش'
        verbose_name_plural = 'بخش‌ها'
        ordering = ['name']
        unique_together = [['name', 'cafe']]


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


# Activity Log model
class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('created', 'Created Request'),
        ('purchased', 'Marked as Purchased'),
        ('updated', 'Updated Request'),
    ]
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    request = models.ForeignKey('purchases.PurchaseRequest', on_delete=models.CASCADE, related_name='activities')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        uname = self.user.username if self.user else '—'
        return f"{uname} - {self.action} - {self.created_at}"

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['user']),
            models.Index(fields=['action']),
            models.Index(fields=['created_at', 'user']),
            models.Index(fields=['request', 'created_at']),
        ]
