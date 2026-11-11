"""
Debt and Payment Account Models.
تفکیک‌شده از purchases/models.py برای بهبود ساختار معماری و نگهداری‌پذیری.
"""
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator


# لیست بدهی شرکت‌ها — تعریف توسط ادمین، پرداخت و تایید توسط مدیرعامل
class Debt(models.Model):
    PRIORITY_CHOICES = [
        ('high', 'بالا'),
        ('medium', 'متوسط'),
        ('low', 'پایین'),
    ]
    STATUS_CHOICES = [
        ('pending', 'در انتظار پرداخت'),
        ('paid', 'پرداخت شده'),
    ]

    company = models.CharField(max_length=200, verbose_name='نام تأمین‌کننده')
    # تأمین‌کننده متصل (اختیاری — بدهی‌های قدیمی فقط نام متنی دارند)
    supplier = models.ForeignKey('Supplier', null=True, blank=True, on_delete=models.SET_NULL, related_name='debts', verbose_name='تأمین‌کننده')
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, verbose_name='مبلغ بدهی',
        validators=[MinValueValidator(0)],
    )
    debt_date = models.DateField(default=timezone.now, verbose_name='تاریخ بدهی')
    description = models.TextField(blank=True, verbose_name='توضیحات')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium', verbose_name='اولویت')

    # پرداخت توسط مدیرعامل
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name='وضعیت')
    paid_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        verbose_name='مبلغ پرداخت شده', validators=[MinValueValidator(0)],
    )
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name='تاریخ و زمان پرداخت')
    payment_photo = models.ImageField(upload_to='debts/payments/', blank=True, null=True, verbose_name='تصویر رسید پرداخت')
    paid_by = models.ForeignKey('User', null=True, blank=True, on_delete=models.SET_NULL, related_name='paid_debts', verbose_name='پرداخت توسط')
    # حساب پرداختی که مبلغ از آن واریز شده است (بانک/حساب تعریف‌شده توسط ادمین)
    account = models.ForeignKey('PaymentAccount', null=True, blank=True, on_delete=models.SET_NULL, related_name='debts', verbose_name='حساب پرداخت')
    # پرداخت خارج از لیست بدهی‌ها (ثبت مستقیم توسط مدیرعامل)
    is_off_list = models.BooleanField(default=False, verbose_name='پرداخت خارج از لیست')

    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='debts', verbose_name='کافه', null=True, blank=True)
    created_by = models.ForeignKey('User', null=True, blank=True, on_delete=models.SET_NULL, related_name='created_debts', verbose_name='ایجاد شده توسط')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        return f"بدهی {self.company} ({self.amount})"

    @property
    def paid_total(self):
        """مجموع پرداخت‌های ثبت‌شده برای این بدهی"""
        from django.db.models import Sum
        total = self.payments.aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0")

    @property
    def remaining(self):
        """باقی‌مانده بدهی — اگر باقی‌مانده ۱٬۰۰۰٬۰۰۰ تومان یا کمتر شد بدهی بسته می‌شود"""
        return max(self.amount - self.paid_total, Decimal("0"))

    class Meta:
        verbose_name = 'بدهی'
        verbose_name_plural = 'بدهی‌ها'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['priority']),
            models.Index(fields=['debt_date']),
        ]


# هر ردیف پرداخت یک بدهی — یک بدهی می‌تواند چند پرداخت (پارشیال) داشته باشد
class DebtPayment(models.Model):
    debt = models.ForeignKey(
        Debt, on_delete=models.CASCADE, related_name='payments',
        null=True, blank=True, verbose_name='بدهی',
    )
    company = models.CharField(max_length=200, verbose_name='نام تأمین‌کننده')
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, verbose_name='مبلغ پرداخت',
        validators=[MinValueValidator(0)],
    )
    paid_at = models.DateTimeField(default=timezone.now, verbose_name='تاریخ و زمان پرداخت')
    payment_photo = models.ImageField(
        upload_to='debts/payments/', blank=True, null=True,
        verbose_name='تصویر رسید پرداخت',
    )
    description = models.TextField(blank=True, verbose_name='توضیحات')
    paid_by = models.ForeignKey(
        'User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='debt_payments', verbose_name='پرداخت توسط',
    )
    # حساب پرداختی که مبلغ از آن واریز شده است (بانک/حساب تعریف‌شده توسط ادمین)
    account = models.ForeignKey(
        'PaymentAccount', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='debt_payments', verbose_name='حساب پرداخت',
    )
    cafe = models.ForeignKey(
        'Cafe', on_delete=models.CASCADE, related_name='debt_payments',
        verbose_name='کافه', null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')

    @property
    def is_off_list(self):
        """پرداخت خارج از لیست — به بدهی تعریف‌شده متصل نیست"""
        return self.debt is None

    @property
    def debt_amount(self):
        """مبلغ کل بدهی — برای پرداخت‌های خارج از لیست همان مبلغ پرداخت"""
        return self.debt.amount if self.debt else self.amount

    @property
    def account_display(self):
        """حساب پرداخت — برای پرداخت‌های قدیمی از بدهی متصل می‌آید"""
        return self.account or (self.debt.account if self.debt else None)

    def __str__(self):
        return f"پرداخت {self.company} ({self.amount})"

    class Meta:
        verbose_name = 'پرداخت بدهی'
        verbose_name_plural = 'پرداخت‌های بدهی'
        ordering = ['-paid_at']
        indexes = [
            models.Index(fields=['paid_at']),
            models.Index(fields=['cafe']),
        ]

class PaymentAccount(models.Model):
    """حساب پرداخت کافه/رستوران — بانک و حسابی که پرداخت‌ها از آن انجام می‌شود"""
    bank_name = models.CharField(max_length=100, verbose_name='نام بانک')
    account_name = models.CharField(max_length=200, blank=True, verbose_name='نام حساب / صاحب حساب')
    account_number = models.CharField(max_length=50, blank=True, verbose_name='شماره حساب')
    card_number = models.CharField(max_length=20, blank=True, verbose_name='شماره کارت')
    is_active = models.BooleanField(default=True, verbose_name='فعال')
    cafe = models.ForeignKey('Cafe', on_delete=models.CASCADE, related_name='payment_accounts', verbose_name='کافه', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='تاریخ بروزرسانی')

    def __str__(self):
        label = self.bank_name
        if self.account_name:
            label += f" — {self.account_name}"
        return label

    class Meta:
        verbose_name = 'حساب پرداخت'
        verbose_name_plural = 'حساب‌های پرداخت'
        ordering = ['bank_name']

# Supplier Management System
