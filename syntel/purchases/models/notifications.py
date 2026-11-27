from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


def _alias_notification_kwargs(kwargs):
    if 'user' in kwargs:
        kwargs['recipient'] = kwargs.pop('user')
    for key in list(kwargs.keys()):
        if key.startswith('user__'):
            kwargs['recipient__' + key[6:]] = kwargs.pop(key)
    return kwargs


class NotificationQuerySet(models.QuerySet):
    def filter(self, *args, **kwargs):
        return super().filter(*args, **_alias_notification_kwargs(dict(kwargs)))

    def exclude(self, *args, **kwargs):
        return super().exclude(*args, **_alias_notification_kwargs(dict(kwargs)))

    def get(self, *args, **kwargs):
        return super().get(*args, **_alias_notification_kwargs(dict(kwargs)))

    def create(self, **kwargs):
        return super().create(**_alias_notification_kwargs(dict(kwargs)))


class NotificationManager(models.Manager):
    def get_queryset(self):
        return NotificationQuerySet(self.model, using=self._db)

    def create(self, *args, **kwargs):
        return self.get_queryset().create(**kwargs)

    def filter(self, *args, **kwargs):
        return self.get_queryset().filter(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.get_queryset().get(*args, **kwargs)


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
    recipient = models.ForeignKey('purchases.User', on_delete=models.CASCADE, related_name='notifications', verbose_name='گیرنده')
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
            models.Index(fields=['cafe', '-created_at']),
        ]
