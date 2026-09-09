# ─────────────────────────────────────────────────────────────────────────────
# سرویس مرکزی انبار — تنها مسیر نهایی‌سازی و برگشت حواله‌های انبار
# ─────────────────────────────────────────────────────────────────────────────
# مسئولیت: اعمال اثر نهایی یک حوالهٔ انبار (WarehouseVoucher) روی موجودی و ثبت
# ردیف‌های تغییرناپذیر StockTransaction، و برگشت (reverse) حواله‌ها.
#
# قواعد:
#  1. نهایی‌سازی:
#     - نوع‌های روتین (issue, receipt, return, transfer, waste, sale,
#       direct_delivery): از وضعیت «ثبت‌شده» (submitted) پذیرفته می‌شوند؛ اگر
#       حواله تأیید را هم طی کرده باشد («تأییدشده») نیز پذیرفته است — تأیید برای
#       این نوع‌ها اختیاری است و لازم نیست هر حواله‌ای از approved بگذرد.
#     - حوالهٔ تعدیل (adjustment) و هر حوالهٔ «نیازمند تأیید» (requires_approval —
#       مثلاً ضایعات بالای آستانه): فقط از وضعیت «تأییدشده» (با approver و
#       approved_at) پذیرفته می‌شوند؛ در غیر این صورت رد می‌شود.
#  2. همهٔ عملیات در transaction.atomic() با select_for_update روی خود حواله و
#     قلم‌های موجودیِ درگیر انجام می‌شود؛ اعتبارسنجی کامل (کافه، انبار،
#     محصول+برند، واحد، نرخ تبدیل، مقدار، کفایت موجودی) پیش از هر نوشتن.
#  3. ایدمپوتنت: نهایی‌سازیِ حوالهٔ از قبل نهایی‌شده بدون هیچ اثری بازگردانده
#     می‌شود (duplicate POST، refresh، retry و race همگی بی‌اثرند).
#  4. موجودی منفی مجاز نیست مگر قانونِ نام‌گذاری‌شدهٔ ALLOW_NEGATIVE_STOCK.
#  5. برای هر (قلم، موجودی) یک StockTransaction تغییرناپذیر ساخته می‌شود که با
#     FK و reference_type='warehouse_voucher' به همان حواله ارجاع دارد.
#  6. برگشت: یک حوالهٔ آینه‌ای می‌سازد (همان نوع و اقلام، reverses → حوالهٔ اصلی)
#     و آن را نهایی می‌کند؛ اثر روی موجودی معکوسِ حوالهٔ اصلی است و حوالهٔ اصلی
#     به «برگشت‌خورده» می‌رود.
#
# خارج از دامنه: مسیر تامین درخواست انبار (process_warehouse_request در views)
# از این سرویس استفاده نمی‌کند و روی مکانیزم فعلی خودش (تراکنش اتمیک + گارد
# کفایت موجودی) دست‌نخورده می‌ماند.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from purchases.models import (
    StockItem,
    StockTransaction,
    WarehouseVoucher,
    WarehouseVoucherItem,
)

logger = logging.getLogger(__name__)

# تلورانس مقایسهٔ اعشار (مقدار پایه در مقابل مقدار تجاری × نرخ تبدیل)
_DECIMAL_EPSILON = Decimal('0.0001')


class InventoryServiceError(Exception):
    """خطای پایهٔ سرویس انبار"""


class VoucherStateError(InventoryServiceError):
    """حواله در وضعیتی نیست که این عملیات روی آن معنا داشته باشد"""


class VoucherValidationError(InventoryServiceError):
    """اعتبارسنجی سند/قلم‌ها رد شد (کافه، انبار، برند، واحد، تبدیل، مقدار)"""


class InsufficientStockError(VoucherValidationError):
    """موجودی انبار برای این حرکت کافی نیست"""


# ── قانونِ نام‌گذاری‌شدهٔ موجودی منفی ──────────────────────────────────────────
# هیچ قانون تجاری فعلی اجازهٔ موجودی منفی نمی‌دهد: هر حرکت خروجی (حوالهٔ خروج،
# فروش، ضایعات، سمتِ خروجِ انتقال، برگشتِ یک ورود، تعدیلِ منفی) باید با موجودیِ
# فعلی پوشش داده شود. اگر روزی قانونی اضافه شد (مثلاً «تلورانس منفی برای تعدیلِ
# اصلاحی»)، باید همین‌جا با نام به این مجموعه افزوده شود؛ هرگز بی‌نام و بی‌صدا.
ALLOW_NEGATIVE_STOCK: frozenset[str] = frozenset()



@dataclass(frozen=True)
class FinalizeResult:
    """نتیجهٔ نهایی‌سازی/برگشت.

    applied=False یعنی ایدمپوتنت: حواله از قبل نهایی‌شده بود و چیزی اعمال نشد.
    """
    voucher: WarehouseVoucher
    applied: bool = True
    stock_transactions: tuple = ()
    is_reversal: bool = False


@dataclass
class _PlannedMovement:
    """یک حرکت واحد: قلم حواله → موجودی انبار با دلتای علامت‌دار (مثبت=افزایش)"""
    item: WarehouseVoucherItem
    stock_item: StockItem
    delta: Decimal
    tx_type: str

    @property
    def stock_item_id(self):
        return self.stock_item.id


def _change_stock_quantity(stock_item_id, delta):
    """اعمال اتمیک دلتا روی موجودی انبار (واحد پایه).

    باید داخل transaction.atomic() اجرا شود. با یک دستور UPDATE و گارد کفایت
    انجام می‌شود تا آپدیت همزمان دو کاربر (read-modify-write بدون قلم) گم نشود
    و موجودی منفی نشود — روی SQLite و PostgreSQL هر دو امن است.

    بازگشت: (موفق, موجودی قبلی, موجودی جدید) — در عدم کفایت (False, None, None)
    """
    qs = StockItem.objects.filter(id=stock_item_id)
    if delta >= 0:
        updated = qs.update(quantity=models.F('quantity') + delta)
    else:
        updated = qs.filter(quantity__gte=-delta).update(
            quantity=models.F('quantity') + delta)
    if not updated:
        return False, None, None
    fresh = StockItem.objects.select_related('product', 'product_brand').get(id=stock_item_id)
    return True, fresh.quantity - delta, fresh.quantity


_VOUCHER_PREFIX = {
    'receipt': 'RCV',
    'issue': 'ISS',
    'return': 'RET',
    'sale': 'SALE',
    'waste': 'WST',
    'transfer': 'TRF',
    'adjustment': 'ADJ',
    'direct_delivery': 'DLV',
}


def next_voucher_number(cafe_id, voucher_type):
    """شمارهٔ یکتای حواله برای این کافه (پیشوند بر اساس نوع) — با retry روی برخورد.

    شماره‌ها ترتیبی‌اند ولی فقط یکتایی تضمین می‌شود (حذف/برگشت ممکن است پرش
    ایجاد کند). برخوردِ همزمانِ نادر، توسط unique_together مدل رد می‌شود و
    درخواست‌کننده می‌تواند دوباره تلاش کند.
    """
    prefix = _VOUCHER_PREFIX.get(voucher_type, 'VC')
    for attempt in range(10):
        count = WarehouseVoucher.objects.filter(
            cafe_id=cafe_id, voucher_type=voucher_type).count()
        candidate = f'{prefix}-{count + 1 + attempt:05d}'
        if not WarehouseVoucher.objects.filter(
                cafe_id=cafe_id, voucher_number=candidate).exists():
            return candidate
    return f'{prefix}-{timezone.now().strftime("%Y%m%d%H%M%S")}'


def _reversal_number(cafe_id, original_number):
    """شمارهٔ یکتای حوالهٔ برگشتی: «شماره‌ی اصلی-R» و در تصادم «-R2»، «-R3» و..."""
    base = f'{original_number}-R'
    number = base
    counter = 2
    while WarehouseVoucher.objects.filter(cafe_id=cafe_id, voucher_number=number).exists():
        number = f'{base}{counter}'
        counter += 1
    return number


def _resolve_stock_item(item, warehouse, allow_explicit, create_if_missing=False):
    """موجودی انبارِ هدف یک حرکت را تعیین می‌کند.

    - اگر قلم صریحاً موجودی انبار دارد: فقط سمتِ مبدأ از آن استفاده می‌شود
      (موجودیِ صریح یعنی «الان کجاست»). سمتِ مقصد (انتقال) با محصول/برند در
      انبارِ مقصد یافت می‌شود.
    - وگرنه با (محصول، برند، انبار) جست‌وجو می‌شود؛ چندتا = خطا (قلم باید
      صریحاً به موجودی انبار متصل شود).
    - نایافتنی: اگر create_if_missing باشد (سمتِ «ورود»ِ رسید/برگشتی) موجودیِ
      انبارِ نو با موجودیِ صفر ساخته می‌شود — رسیدِ کالای تازه به موجودیِ از
      پیش‌تعریف‌شده وابسته نیست؛ وگرنه خطای اعتبارسنجی.
    """
    if allow_explicit and item.stock_item_id:
        si = item.stock_item
        if si.warehouse_id != warehouse.id:
            raise VoucherValidationError(
                f'قلم «{item}»: موجودیِ انبارِ انتخابی در انبار «{warehouse.name}» نیست.')
        return si
    product_id = item.product_id or (item.stock_item_id and item.stock_item.product_id)
    brand_id = item.product_brand_id or (item.stock_item_id and item.stock_item.product_brand_id)
    qs = StockItem.objects.filter(warehouse_id=warehouse.id).select_related('product', 'product_brand')
    if product_id:
        qs = qs.filter(product_id=product_id)
    if brand_id:
        qs = qs.filter(product_brand_id=brand_id)
    else:
        # قلمِ بدون برند فقط به موجودیِ بدون برند متصل می‌شود
        qs = qs.filter(product_brand_id__isnull=True)
    matches = list(qs)
    if not matches:
        if create_if_missing and product_id:
            return StockItem.objects.create(
                product_id=product_id,
                product_brand_id=brand_id,
                warehouse=warehouse,
                unit_id=item.unit_id,
                quantity=Decimal('0'),
            )
        raise VoucherValidationError(
            f'قلم «{item}»: موجودیِ انبار برای این محصول/برند در انبار «{warehouse.name}» یافت نشد.')
    if len(matches) > 1:
        raise VoucherValidationError(
            f'قلم «{item}»: چند موجودیِ انبار در انبار «{warehouse.name}» یافت شد — '
            f'قلم را صریحاً به موجودیِ انبار متصل کنید.')
    return matches[0]


def _plan_movements(v):
    """برنامهٔ حرکات سند: برای هر قلم، هر (انبار، دلتا) را به موجودیِ انبار پیوند می‌زند.

    جهتِ حرکت بر اساس نوع حواله:
      receipt  → +انبار مقصد          (ورود)
      issue    → −انبار مبدأ          (خروج)
      return   → +انبار مبدأ          (برگشتِ کالا به انبار = ورود)
      sale     → −انبار مبدأ          (فروش = خروج)
      waste    → −انبار مبدأ          (ضایعات = خروج)
      transfer → −مبدأ و +مقصد
      adjustment → علامتِ خودِ مقدار پایهٔ قلم (مثبت=افزایش، منفی=کاهش) روی انبار مبدأ
      direct_delivery → عبور از انبارِ مقصد (محل ورودِ کالای تازه‌خرید): +مقدار و
        سپس −مقدار روی همان انبار — خالصِ صفر، ولی سابقهٔ ورود و خروجِ کامل ثبت
        می‌شود و بخشِ گیرنده روی سند می‌ماند (تحویل مستقیم).
    حوالهٔ برگشتی (reverses): همهٔ علائم معکوس می‌شوند — اثر خالصِ «اصلی + برگشت» صفر است.
    """
    if v.voucher_type == 'adjustment':
        if not v.source_warehouse_id:
            raise VoucherValidationError(
                'حوالهٔ تعدیل باید انبارِ مبدأ (انبارِ موردِ تعدیل) داشته باشد.')

    # قفلِ قلم‌ها با حواله (ویرایشِ همزمانِ قلم‌ها با نهایی‌سازی تداخل نکند)
    items = list(v.items.select_for_update().select_related(
        'product', 'product_brand', 'unit',
        'stock_item__product', 'stock_item__product_brand',
        'stock_item__warehouse',
    ))
    if not items:
        raise VoucherValidationError('حوالهٔ بدون قلم قابل نهایی‌سازی نیست.')

    cafe_id = v.cafe_id
    for wh in (v.source_warehouse, v.destination_warehouse):
        if wh and wh.cafe_id != cafe_id:
            raise VoucherValidationError(f'انبار «{wh.name}» متعلق به کافهٔ دیگری است.')

    sign = -1 if v.reverses_id else 1
    plans: list[_PlannedMovement] = []

    for item in items:
        # ── پیوند و برند ──
        if not (item.stock_item_id or item.product_id):
            raise VoucherValidationError(f'قلم «{item}» به محصول یا موجودیِ انبار متصل نیست.')
        si = item.stock_item
        if item.product_id and si and si.product_id != item.product_id:
            raise VoucherValidationError(f'قلم «{item}»: محصول قلم با موجودیِ انبارِ انتخابی مطابقت ندارد.')
        if si and item.product_brand_id:
            if si.product_brand_id and si.product_brand_id != item.product_brand_id:
                raise VoucherValidationError(f'قلم «{item}»: برند قلم با برند موجودیِ انبار مطابقت ندارد.')
            if not si.product_brand_id:
                raise VoucherValidationError(f'قلم «{item}»: برند انتخاب شده ولی موجودیِ انبار بدون برند است.')

        # ── واحد ──
        if not item.unit_id:
            raise VoucherValidationError(f'قلم «{item}» واحد ندارد.')
        if item.unit and item.unit.cafe_id and item.unit.cafe_id != cafe_id:
            raise VoucherValidationError(f'قلم «{item}»: واحد متعلق به کافهٔ دیگری است.')
        if si:
            if si.unit_id and si.unit_id != item.unit_id:
                raise VoucherValidationError(
                    f'قلم «{item}»: واحد قلم با واحدِ موجودیِ انبار مطابقت ندارد.')
        elif item.product and item.product.unit_id and item.product.unit_id != item.unit_id:
            raise VoucherValidationError(
                f'قلم «{item}»: واحد قلم با واحدِ اصلیِ محصول مطابقت ندارد.')

        # ── نرخ تبدیل و مقدار (همهٔ مقادیر اعشاری؛ هیچ truncation‌ای) ──
        base = item.base_quantity
        rate = item.conversion_rate
        trade = item.trade_quantity
        if rate is not None and rate <= 0:
            raise VoucherValidationError(f'قلم «{item}»: نرخ تبدیل باید مثبت باشد.')
        if trade is not None:
            if rate is not None:
                expected = (trade * rate).quantize(_DECIMAL_EPSILON)
                if base != expected:
                    raise VoucherValidationError(
                        f'قلم «{item}»: مقدار پایه با «مقدار تجاری × نرخ تبدیل» مغایرت دارد '
                        f'({base} ≠ {trade} × {rate}).')
            elif base != trade:
                raise VoucherValidationError(
                    f'قلم «{item}»: نرخ تبدیل ثبت نشده ولی مقدار پایه و تجاری متفاوت‌اند.')
        if v.voucher_type == 'adjustment':
            if base == 0:
                raise VoucherValidationError(f'قلم «{item}»: مقدار تعدیل نباید صفر باشد.')
            # علامتِ خودِ مقدارِ پایه جهت تعدیل است: مثبت = افزایش، منفی = کاهش
        elif base <= 0:
            raise VoucherValidationError(f'قلم «{item}»: مقدار باید مثبت باشد.')

        # ── حرکات ──
        vt = v.voucher_type
        if vt == 'receipt':
            moves = [(v.destination_warehouse, sign * base)]
        elif vt == 'issue':
            moves = [(v.source_warehouse, -sign * base)]
        elif vt == 'return':
            moves = [(v.source_warehouse, sign * base)]
        elif vt == 'sale':
            moves = [(v.source_warehouse, -sign * base)]
        elif vt == 'waste':
            moves = [(v.source_warehouse, -sign * base)]
        elif vt == 'transfer':
            moves = [(v.source_warehouse, -sign * base), (v.destination_warehouse, sign * base)]
        elif vt == 'adjustment':
            moves = [(v.source_warehouse, sign * base)]
        elif vt == 'direct_delivery':
            # تحویل مستقیم: کالای تازه‌خرید از انبارِ مقصد (محل عبور) می‌گذرد —
            # ورودِ کامل و بلافاصله خروجِ کامل به بخشِ گیرنده؛ خالصِ صفر روی انبار.
            moves = [(v.destination_warehouse, sign * base),
                     (v.destination_warehouse, -sign * base)]
        else:
            raise VoucherValidationError(f'نوع حوالهٔ «{vt}» شناخته‌شده نیست.')

        for wh, delta in moves:
            if wh is None:
                raise VoucherValidationError(
                    f'قلم «{item}»: انبارِ حرکتِ «{v.get_voucher_type_display()}» تعریف نشده است.')
            if wh.cafe_id != cafe_id:
                raise VoucherValidationError(f'انبار «{wh.name}» متعلق به کافهٔ دیگری است.')
            # موجودیِ صریح قلم = «الان کجاست» → فقط سمتِ مبدأ؛ سمتِ مقصدِ انتقال
            # همیشه با محصول/برند در انبارِ مقصد یافت می‌شود.
            allow_explicit = not (vt == 'transfer' and wh.id != v.source_warehouse_id)
            # سمتِ «ورود» (رسید/برگشتی/تحویل مستقیم): اگر موجودیِ انباری ثبت
            # نشده، نهایی‌سازی خودکار می‌سازدش — همهٔ محصولات برای ورود به انبار
            # قابل‌قبول‌اند (کالای تازه‌خریدِ تحویل مستقیم هم همین‌طور).
            create_if_missing = delta > 0 and vt in ('receipt', 'return', 'direct_delivery')
            stock = _resolve_stock_item(item, wh, allow_explicit=allow_explicit,
                                        create_if_missing=create_if_missing)
            plans.append(_PlannedMovement(
                item=item, stock_item=stock, delta=delta,
                tx_type=_tx_type_for(vt, delta),
            ))

    return plans


def _tx_type_for(vt, delta):
    """نوع تراکنش انبار برای یک حرکت — بر اساس جهتِ واقعیِ حرکت.

    (حوالهٔ برگشتی جهتِ معکوس دارد؛ پس نوع تراکنش را از علامتِ دلتا می‌گیریم،
    نه از نوعِ حواله — «برگشتِ ورود» یعنی خروج و «برگشتِ خروج» یعنی ورود.)
    """
    if vt == 'adjustment':
        return 'adjustment'
    return 'in' if delta > 0 else 'out'


def _tx_description(v, movement):
    """توضیح فارسیِ تراکنش — با نام کالا و متن قلم"""
    item = movement.item
    name = (item.product.name if item.product_id else movement.stock_item.product.name)
    base = f'حوالهٔ {v.get_voucher_type_display()} #{v.voucher_number} - {name}'
    if item.description:
        base += f' ({item.description})'
    if v.reverses_id:
        base = 'برگشتِ ' + base
    return base


def finalize_voucher(voucher, finalized_by=None):
    """نهایی‌سازی یک حوالهٔ انبار — اتمیک، قفل‌دار، ایدمپوتنت.

    - نوع‌های روتین: از «ثبت‌شده» (یا در صورت طیِ تأیید «تأییدشده») پذیرفته می‌شوند.
    - تعدیل و حواله‌های «نیازمند تأیید»: فقط از «تأییدشده» (با approver و approved_at).
    - همهٔ اعتبارسنجی‌ها پیش از اولین نوشتن؛ سپس اعمال و ثبت ردیف‌های تراکنش و
      به‌روزرسانی وضعیت حواله — همگی در یک transaction.atomic().
    - اگر حواله برگشتی باشد (reverses مشخص شده)، اثر معکوسِ حوالهٔ اصلی اعمال و
      حوالهٔ اصلی به «برگشت‌خورده» می‌رود.
    """
    if voucher.pk is None:
        raise VoucherValidationError('حوالهٔ ذخیره‌نشده قابل نهایی‌سازی نیست.')

    with transaction.atomic():
        v = (WarehouseVoucher.objects.select_for_update()
             .select_related('cafe', 'source_warehouse', 'destination_warehouse', 'reverses')
             .get(pk=voucher.pk))

        # ── ایدمپوتنسی: نهایی‌سازیِ دوباره = بدون اثر ──
        if v.status == 'finalized':
            return FinalizeResult(v, applied=False, is_reversal=bool(v.reverses_id))

        # ── وضعیت مجاز ──
        approval_required = (
            v.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES or v.requires_approval
        )
        if approval_required:
            if v.status != 'approved' or not (v.approver_id and v.approved_at):
                raise VoucherStateError(
                    'این حواله نیازمند تأیید است و باید پیش از نهایی‌سازی «تأییدشده» باشد '
                    '(با تأییدکننده و تاریخ تأیید).')
        elif v.status not in ('submitted', 'approved'):
            raise VoucherStateError(
                'فقط حوالهٔ «ثبت‌شده» (یا در صورت طیِ تأیید «تأییدشده») قابل نهایی‌سازی است.')

        # ── حوالهٔ برگشتی: حوالهٔ اصلی را قفل و اعتبارسنجی کن ──
        original = None
        if v.reverses_id:
            original = (WarehouseVoucher.objects.select_for_update()
                        .select_related('cafe').get(pk=v.reverses_id))
            if v.voucher_type != original.voucher_type:
                raise VoucherValidationError('حوالهٔ برگشتی باید همان نوعِ حوالهٔ اصلی باشد.')
            if original.status != 'finalized':
                raise VoucherStateError('فقط حوالهٔ «نهایی‌شده» قابل برگشت است.')
            if original.reversed_at or original.status == 'reversed':
                raise VoucherStateError('این حواله قبلاً برگشت خورده است.')

        # ── ساختار سند (قواعد انبار مبدأ/مقصد مدل) و برنامهٔ حرکات (اعتبارسنجی کامل) ──
        try:
            v.full_clean()
        except ValidationError as exc:
            raise VoucherValidationError(f'سند ناسازگار است: {exc}') from exc
        plans = _plan_movements(v)

        # ── قفلِ قلم‌های موجودی و جمعِ خالص هر قلم ──
        net: dict[int, Decimal] = {}
        for p in plans:
            net[p.stock_item_id] = net.get(p.stock_item_id, Decimal('0')) + p.delta
        locked = {
            si.id: si
            for si in StockItem.objects.select_for_update()
            .select_related('product', 'product_brand', 'warehouse')
            .filter(id__in=[p.stock_item_id for p in plans])
        }
        if len(locked) != len(net):
            raise VoucherValidationError('برخی موجودی‌های انبارِ این حواله یافت نشدند.')

        # ── کفایت موجودی (پیش از هر نوشتن؛ تحت قلم) ──
        for si_id, d in net.items():
            if d < 0 and v.voucher_type not in ALLOW_NEGATIVE_STOCK:
                si = locked[si_id]
                if si.quantity < -d:
                    raise InsufficientStockError(
                        f'موجودی «{si.product.name}» در انبار «{si.warehouse.name}» کافی نیست '
                        f'(نیاز: {-d}، موجودی: {si.quantity}).')

        # ── اعمال دلتاها و ثبت ردیف‌های تغییرناپذیر تراکنش ──
        running = {si_id: si.quantity for si_id, si in locked.items()}
        txs = []
        # تحویل مستقیم هر دو حرکت را روی یک موجودی دارد (+ و سپس −): «ورود» باید
        # اول اعمال شود تا از موجودیِ صفر هم قابل‌اجرا باشد (برای اصلی و برگشتی یکسان)
        ordered = sorted(plans, key=lambda p: (p.stock_item_id, p.delta < 0))
        for p in ordered:
            ok, _prev, _new = _change_stock_quantity(p.stock_item_id, p.delta)
            if not ok:
                # آخرین نگهبان؛ پس از اعتبارسنجی زیر قلم نباید رخ دهد
                raise InsufficientStockError(
                    f'موجودی «{p.stock_item.product.name}» کافی نیست.')
            prev = running[p.stock_item_id]
            new = prev + p.delta
            running[p.stock_item_id] = new
            rate = p.item.conversion_rate
            if rate is None and p.stock_item.product_brand_id:
                rate = p.stock_item.product_brand.conversion_rate
            tx = StockTransaction.objects.create(
                stock_item_id=p.stock_item_id,
                transaction_type=p.tx_type,
                quantity=abs(p.delta),
                conversion_rate=rate,
                unit_id=p.item.unit_id,
                reference_type='warehouse_voucher',
                reference_id=v.id,
                voucher=v,
                description=_tx_description(v, p),
                created_by=finalized_by or v.approver or v.created_by,
                previous_quantity=prev,
                new_quantity=new,
            )
            txs.append(tx)

        # ── وضعیت سند ──
        now = timezone.now()
        v.status = 'finalized'
        v.finalized_at = now
        v.save(update_fields=['status', 'finalized_at', 'updated_at'])

        if original:
            original.status = 'reversed'
            original.reversed_at = now
            original.save(update_fields=['status', 'reversed_at', 'updated_at'])

        logger.info(
            'حوالهٔ %s #%s نهایی شد (%d قلم، %d تراکنش)',
            v.get_voucher_type_display(), v.voucher_number, len(plans), len(txs),
        )
        return FinalizeResult(v, applied=True, stock_transactions=tuple(txs),
                              is_reversal=bool(v.reverses_id))


def reverse_voucher(voucher, created_by=None, description=None):
    """برگشت یک حوالهٔ «نهایی‌شده» — اتمیک و قفل‌دار.

    یک حوالهٔ آینه‌ای می‌سازد (همان نوع و همان اقلام؛ reverses → حوالهٔ اصلی) و
    آن را نهایی می‌کند: اثر روی موجودی معکوسِ حوالهٔ اصلی است و حوالهٔ اصلی به
    «برگشت‌خورده» می‌رود — همه در یک تراکنش.

    - حوالهٔ برگشتیِ خودش قابل برگشت نیست (باید حوالهٔ اصلی برگشت بخورد).
    - حوالهٔ تعدیل/نیازمندِ تأیید: خودِ اقدامِ برگشت، تأییدِ حوالهٔ آینه‌ای است
      (approver = برگشت‌زننده) تا جریان یک‌گامه بماند.
    - ایدمپوتنت: برگشتِ دوباره خطای VoucherStateError می‌دهد (برگشتِ دوم = خطا،
      نه اعمالِ دوباره).
    """
    with transaction.atomic():
        original = (WarehouseVoucher.objects.select_for_update()
                    .select_related('cafe').get(pk=voucher.pk))
        if original.reverses_id:
            raise VoucherStateError(
                'حوالهٔ برگشتی خودش قابل برگشت نیست — حوالهٔ اصلی را برگشت بزنید.')
        if original.status != 'finalized':
            raise VoucherStateError('فقط حوالهٔ «نهایی‌شده» قابل برگشت است.')
        if original.reversed_at or original.status == 'reversed':
            raise VoucherStateError('این حواله قبلاً برگشت خورده است.')

        approval_required = (
            original.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
            or original.requires_approval
        )
        reversal = WarehouseVoucher(
            cafe=original.cafe,
            voucher_type=original.voucher_type,
            status='approved' if approval_required else 'submitted',
            voucher_number=_reversal_number(original.cafe_id, original.voucher_number),
            source_warehouse=original.source_warehouse,
            destination_warehouse=original.destination_warehouse,
            department=original.department,
            description=description or f'برگشتِ حوالهٔ #{original.voucher_number}',
            created_by=created_by or original.created_by,
            reverses=original,
            requires_approval=original.requires_approval,
        )
        if approval_required:
            reversal.approver = created_by or original.created_by
            reversal.approved_at = timezone.now()
        reversal.save()

        for item in original.items.all():
            WarehouseVoucherItem.objects.create(
                voucher=reversal,
                product=item.product,
                product_brand=item.product_brand,
                stock_item=item.stock_item,
                unit=item.unit,
                base_quantity=item.base_quantity,
                trade_quantity=item.trade_quantity,
                conversion_rate=item.conversion_rate,
                unit_price=item.unit_price,
                description=item.description,
            )

        # نهایی‌سازیِ حوالهٔ آینه‌ای — داخل همان تراکنش؛ اصلی را «برگشت‌خورده» می‌کند
        return finalize_voucher(reversal, finalized_by=created_by)
