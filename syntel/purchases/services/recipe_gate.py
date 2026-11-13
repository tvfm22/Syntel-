"""قواعد اتصال «رسپی منو» ↔ «مدیریت محصولات» ↔ «سفارش درخواست‌کننده».

سه قاعده‌ای که این ماژول نگه می‌دارد:

1. کالایی که دسته‌اش مواد غذایی است (``requires_recipe`` روی خودش یا یکی از
   والدینش) فقط وقتی برای درخواست‌کننده قابل انتخاب است که در دست‌کم یک سطر
   رسپی (``KukerRecipeLine``) در همان کافه مصرف شده باشد.
2. کالای دسته‌های غیرغذایی (شوینده، مصرفی، ...) بدون رسپی هم قابل سفارش است.
3. کالای تازه‌ای که درخواست‌کننده بیرون از فهرست تعریف می‌کند، همیشه در دستهٔ
   «تحقیق و توسعه» همان کافه ثبت می‌شود تا مدیریت بعداً بررسی و جابه‌جا کند.

همهٔ توابع اینجا فقط داده می‌خوانند (به‌جز ساخت دستهٔ «تحقیق و توسعه») و هیچ
وابستگی‌ای به request ندارند تا هم در ویو و هم در تست قابل استفاده باشند.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q

from ..models import Category, KukerRecipeLine, Product

# نام و توضیح دستهٔ مقصد کالاهای تعریف‌شده توسط درخواست‌کننده
RD_CATEGORY_NAME = 'تحقیق و توسعه'
RD_CATEGORY_DESCRIPTION = 'کالاهای تعریف‌شده توسط درخواست‌کنندگان (تحقیق و توسعه)'

# نقش‌هایی که قاعدهٔ «فقط کالای داخل رسپی» برایشان اعمال نمی‌شود: این‌ها همان
# کسانی هستند که رسپی/محصول را تعریف و اصلاح می‌کنند، پس باید کل کاتالوگ را ببینند.
PRODUCT_MANAGER_ROLES = frozenset({
    'purchasing', 'warehouse', 'admin', 'manager',
    'accounting', 'finance_manager', 'ceo',
})


def bypasses_recipe_gate(user) -> bool:
    """آیا این کاربر مجاز است کالای غذاییِ بدون رسپی را هم ببیند/سفارش دهد؟"""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return getattr(user, 'role', '') in PRODUCT_MANAGER_ROLES


def category_requires_recipe(category) -> bool:
    """آیا این دسته (یا یکی از والدینش) مواد غذایی است؟"""
    seen = set()
    while category is not None and category.pk not in seen:
        if category.requires_recipe:
            return True
        seen.add(category.pk)
        category = category.parent
    return False


def recipe_required_category_ids(cafe=None) -> set:
    """شناسهٔ همهٔ دسته‌های غذاییِ کافه (با ارث‌بری از والد).

    دسته‌های بدون کافه (دادهٔ قدیمی) هم حساب می‌شوند؛ ``requires_recipe`` یک
    پرچمِ داده است نه مرزِ امنیتی، پس محافظه‌کارانه‌تر است که شامل شود.
    """
    qs = Category.objects.all()
    if cafe is not None:
        qs = qs.filter(Q(cafe=cafe) | Q(cafe__isnull=True))
    rows = list(qs.values('id', 'parent_id', 'requires_recipe'))
    by_id = {row['id']: row for row in rows}

    food_ids = set()
    for row in rows:
        if row['requires_recipe']:
            food_ids.add(row['id'])
            continue
        # بالا رفتن از زنجیرهٔ والدها تا ریشه — با محافظِ حلقهٔ بسته
        seen = {row['id']}
        parent_id = row['parent_id']
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                break
            if parent['requires_recipe']:
                food_ids.add(row['id'])
                break
            parent_id = parent['parent_id']
    return food_ids


def is_used_in_cogs_recipe(product) -> bool:
    """آیا این کالا در دست‌کم یک سطر رسپیِ همین کافه به‌عنوان ماده مصرف می‌شود؟"""
    if product is None or product.pk is None:
        return False
    return KukerRecipeLine.objects.filter(
        material__product=product,
        item__cafe=product.cafe,
    ).exists()


def annotate_recipe_usage(queryset):
    """افزودن ستون ``used_in_cogs_recipe`` به کوئریِ محصولات (بدون N+1)."""
    return queryset.annotate(
        used_in_cogs_recipe=Exists(
            KukerRecipeLine.objects.filter(
                material__product=OuterRef('pk'),
                item__cafe=OuterRef('cafe'),
            )
        )
    )


def filter_requestable_products(queryset, cafe=None):
    """حذف کالاهای غذاییِ بدون رسپی از یک کوئریِ محصولات.

    کالای بدون دسته و کالای دسته‌های غیرغذایی دست‌نخورده می‌مانند.
    """
    qs = annotate_recipe_usage(queryset)
    food_ids = recipe_required_category_ids(cafe)
    if not food_ids:
        return qs
    return qs.filter(
        Q(used_in_cogs_recipe=True)
        | Q(category_id__isnull=True)
        | ~Q(category_id__in=food_ids)
    )


def inactive_product_error(product):
    """پیام خطای فارسی اگر کالا در سفارش‌گذاری غیرفعال شده باشد؛ در غیر این صورت None."""
    if product is not None and not getattr(product, 'is_active', True):
        return f'کالای «{product.name}» در سفارش‌گذاری غیرفعال است و قابل درخواست نیست.'
    return None


def recipe_gate_error(product, cafe=None):
    """پیام خطای فارسی اگر این کالا نباید سفارش داده شود؛ در غیر این صورت None.

    برای اعتبارسنجیِ سمت سرورِ فرم‌های درخواست استفاده می‌شود تا فیلترِ رابط
    کاربری با فرستادن شناسهٔ خامِ کالا دور زده نشود.
    """
    if product is None:
        return None
    if not category_requires_recipe(product.category):
        return None
    if is_used_in_cogs_recipe(product):
        return None
    return (
        f'کالای «{product.name}» در هیچ رسپی منو مصرف نمی‌شود. '
        'کالاهای غذایی باید اول در رسپی یک غذا یا نیمه‌آماده استفاده شوند، '
        'وگرنه قابل درخواست نیستند.'
    )


def get_or_create_rd_category(cafe):
    """دستهٔ «تحقیق و توسعه» کافه را برمی‌گرداند و اگر نبود می‌سازد."""
    qs = Category.objects.filter(name=RD_CATEGORY_NAME)
    qs = qs.filter(cafe=cafe) if cafe is not None else qs.filter(cafe__isnull=True)
    category = qs.first()
    if category is not None:
        if not category.is_active:
            category.is_active = True
            category.save(update_fields=['is_active'])
        return category

    try:
        with transaction.atomic():
            return Category.objects.create(
                name=RD_CATEGORY_NAME,
                cafe=cafe,
                description=RD_CATEGORY_DESCRIPTION,
                is_active=True,
                requires_recipe=False,
            )
    except IntegrityError:
        # دو درخواست هم‌زمان — همان رکوردی که رقیب ساخته را برمی‌گردانیم
        return qs.first() or Category.objects.filter(name=RD_CATEGORY_NAME).first()


def assign_rd_category(product, cafe=None):
    """کالای تازهٔ درخواست‌کننده را به «تحقیق و توسعه» می‌برد (همیشه، بی‌استثنا).

    دستهٔ انتخابیِ کاربر عمداً نادیده گرفته می‌شود: کالای تعریف‌شده بیرون از
    فهرست تا بررسی مدیریت در «تحقیق و توسعه» می‌ماند.
    """
    category = get_or_create_rd_category(cafe if cafe is not None else product.cafe)
    if category is None:
        return None
    if product.category_id != category.id:
        product.category = category
        product.save(update_fields=['category'])
    return category


def is_rd_product(product) -> bool:
    """آیا این کالا در دستهٔ «تحقیق و توسعه» است؟"""
    return bool(product and product.category_id and
                getattr(product.category, 'name', '') == RD_CATEGORY_NAME)
