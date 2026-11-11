"""قاعدهٔ «تایید دریافتِ تحویلهای قبلی، پیش از ثبت درخواست جدید».

تنها منبع حقیقت برای «چه چیزی تحویل شده ولی تایید نشده»:
تخصیصهای تامینشدهٔ مسئول انبار (``OfficerAssignment.status='fulfilled'`` و
``target_role='warehouse'``) روی اقلام درخواستِ خود کاربر، منهای اقلامی که
``PurchaseItem.received_confirmed_at`` دارند. دقیقاً همان مبنای صفحهٔ
«کالاهای دریافتی من» و نشان داشبورد است تا عدد نمایشدادهشده و قفل یکی باشند.

توابع اینجا فقط میخوانند و به ``request`` وابسته نیستند؛ ویوها و تستها هر دو
از یک جا استفاده میکنند.
"""
from __future__ import annotations

from django.urls import reverse

from ..models import OfficerAssignment

# نقشهای درخواستدهنده (همان نقشهای داشبورد بخشها، بهعلاوهٔ «انبار» قدیمی که
# فرم درخواست کالا همچنان آن را میپذیرد). نقشهای ستادی/مدیریتی مشمول قفل
# نیستند: آنها درخواست را از طرف بخش ثبت میکنند، نه برای تحویل خودشان.
RECEIPT_GATE_ROLES = frozenset({
    'kitchen', 'bar', 'counter', 'cashier', 'facilities', 'store', 'requester',
})


def receipt_gate_applies(user) -> bool:
    """آیا قفل «تایید دریافت قبل از درخواست جدید» برای این کاربر اعمال میشود؟"""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return False
    return getattr(user, 'role', '') in RECEIPT_GATE_ROLES


def pending_receipt_ids(user, cafe=None) -> list[int]:
    """شناسهٔ اقلام تحویلشدهٔ تاییدنشدهٔ یک کاربر (بدون تکرار).

    ``cafe`` در صورت داشتن، همانند صفحهٔ «کالاهای دریافتی من» محدوده را به
    کافهٔ جاری میبندد؛ تحویلهای کافهٔ دیگر نباید جلوی درخواست در این کافه
    را بگیرند.
    """
    if not receipt_gate_applies(user):
        return []
    qs = OfficerAssignment.objects.filter(
        target_role='warehouse',
        status='fulfilled',
        item__request__user=user,
        item__received_confirmed_at__isnull=True,
    )
    if cafe is not None:
        qs = qs.filter(item__request__cafe=cafe)
    return list(qs.values_list('item_id', flat=True).distinct())


def pending_receipt_count(user, cafe=None) -> int:
    return len(pending_receipt_ids(user, cafe))


def receipt_gate_context(user, cafe, cafe_code) -> dict | None:
    """زمینهٔ بنر هشدار فرمها؛ ``None`` یعنی هیچ مانعی وجود ندارد."""
    count = pending_receipt_count(user, cafe)
    if not count:
        return None
    return {
        'pending_count': count,
        'confirm_url': reverse('my_received_items',
                               kwargs={'cafe_code': cafe_code}),
    }


def receipt_gate_message(count: int) -> str:
    return (f'قبل از ثبت درخواست جدید، باید دریافت {count} قلم کالای تحویلشدهٔ '
            f'درخواستهای قبلیتان را تایید کنید.')
