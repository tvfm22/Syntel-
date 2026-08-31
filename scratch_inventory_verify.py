# ── بررسی سرویس مرکزی انبار (Prompt 3) — همهٔ داده‌ها موقت و در پایان برگشت می‌خورند ──
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from purchases.models import (
    Cafe, Product, StockItem, StockTransaction, Unit, Warehouse, WarehouseVoucher,
    WarehouseVoucherItem,
)
from purchases.services import inventory
from purchases.views import _change_stock_quantity as views_ccq
from purchases.services.inventory import (
    ALLOW_NEGATIVE_STOCK, FinalizeResult, InsufficientStockError,
    VoucherStateError, VoucherValidationError, finalize_voucher, reverse_voucher,
)

failures = []
def check(cond, msg):
    print(('  OK  ' if cond else '  FAIL') + ' — ' + msg)
    if not cond:
        failures.append(msg)

baseline_tx = StockTransaction.objects.count()
baseline_vouchers = WarehouseVoucher.objects.count()
baseline_items = StockItem.objects.count()
print(f'baseline: txs={baseline_tx} vouchers={baseline_vouchers} stock_items={baseline_items}')

# ── همان شیء تابع: مسیر تامین قدیمی روی همان مکانیزم (فقط منتقل‌شده) ──
check(views_ccq is inventory._change_stock_quantity,
      'views._change_stock_quantity همان تابع سرویس است (بدون مسیر موازی)')
check(ALLOW_NEGATIVE_STOCK == frozenset(),
      f'قانون موجودی منفی: هیچ نوعی مجاز نیست ({ALLOW_NEGATIVE_STOCK})')

suffix = uuid.uuid4().hex[:8]
cafe = Cafe.objects.first()
user = get_user_model().objects.first()

try:
    with transaction.atomic():
        unit = Unit.objects.create(name=f'SCR-{suffix}', cafe=cafe)
        unit_other = Unit.objects.create(name=f'SCR-other-{suffix}', cafe=cafe)
        wh_a = Warehouse.objects.create(name='SCR-A', code=f'SCR-A-{suffix}', cafe=cafe)
        wh_b = Warehouse.objects.create(name='SCR-B', code=f'SCR-B-{suffix}', cafe=cafe)
        prod = Product.objects.create(name=f'SCR-{suffix}', cafe=cafe, unit=unit)
        prod_other = Product.objects.create(name=f'SCR-other-{suffix}',
                                            cafe=Cafe.objects.exclude(id=cafe.id).first())
        prod_nostock = Product.objects.create(name=f'SCR-nostock-{suffix}', cafe=cafe, unit=unit)
        si_a = StockItem.objects.create(product=prod, warehouse=wh_a, quantity=Decimal('100'), unit=unit)
        si_b = StockItem.objects.create(product=prod, warehouse=wh_b, quantity=Decimal('50'), unit=unit)

        def make_voucher(vtype, status, wh_from=None, wh_to=None, number=None, extra=None):
            kwargs = dict(cafe=cafe, voucher_type=vtype, status=status,
                          voucher_number=number or f'SCR-{vtype}-{uuid.uuid4().hex[:6]}',
                          source_warehouse=wh_from, destination_warehouse=wh_to)
            if extra:
                kwargs.update(extra)
            return WarehouseVoucher.objects.create(**kwargs)

        def add_item(v, stock_item=None, product=None, base='10', trade=None, rate=None,
                     unit_of_item=None, description=''):
            product = product or (stock_item.product if stock_item else None)
            chosen_unit = unit_of_item or (stock_item.unit if stock_item else unit)
            return WarehouseVoucherItem.objects.create(
                voucher=v, product=product, stock_item=stock_item,
                unit=chosen_unit,
                base_quantity=Decimal(base), trade_quantity=Decimal(trade) if trade else None,
                conversion_rate=Decimal(rate) if rate else None, description=description,
            )

        def refresh():
            si_a.refresh_from_db()
            si_b.refresh_from_db()

        # ── ۱) ورود (receipt): نهایی‌سازی، اثر روی موجودی، تراکنش با ارجاع به حواله ──
        r1 = make_voucher('receipt', 'submitted', wh_to=wh_a, number=f'SCR-R1-{suffix}')
        add_item(r1, stock_item=si_a, base='25')
        res = finalize_voucher(r1, finalized_by=user)
        check(res.applied is True and res.voucher.status == 'finalized', 'ورود: نهایی شد')
        refresh()
        check(si_a.quantity == Decimal('125'), f'ورود: موجودی +25 ({si_a.quantity})')
        tx = StockTransaction.objects.filter(reference_type='warehouse_voucher', reference_id=r1.id)
        check(tx.count() == 1, 'ورود: دقیقاً ۱ تراکنش')
        check(tx.first().voucher_id == r1.id, 'تراکنش به حواله ارجاع دارد (FK)')
        check(tx.first().transaction_type == 'in' and tx.first().quantity == Decimal('25.0000'),
              'تراکنش: نوع in، مقدار 25')
        check(tx.first().previous_quantity == Decimal('100') and tx.first().new_quantity == Decimal('125'),
              'تراکنش: موجودی قبل/بعد درست')
        check(tx.first().created_by == user, 'تراکنش: ایجادکننده (finalized_by) ثبت شد')

        # ── ۲) ایدمپوتنسی: نهایی‌سازیِ دوباره = بدون اثر ──
        n_tx = StockTransaction.objects.count()
        res2 = finalize_voucher(r1, finalized_by=user)
        check(res2.applied is False, 'ایدمپوتنت: نهایی‌سازی دوباره applied=False')
        refresh()
        check(StockTransaction.objects.count() == n_tx and si_a.quantity == Decimal('125'),
              'ایدمپوتنت: تراکنش/موجودی دوباره اعمال نشدند')

        # ── ۳) وضعیت‌های غیرمجاز ──
        for bad_status in ('draft', 'cancelled', 'reversed'):
            v = make_voucher('receipt', bad_status, wh_to=wh_a)
            add_item(v, stock_item=si_a, base='1')
            try:
                finalize_voucher(v)
                check(False, f'وضعیت {bad_status}: باید رد شود')
            except VoucherStateError:
                check(True, f'وضعیت {bad_status}: رد شد')
            except Exception as exc:
                check(False, f'وضعیت {bad_status}: خطای اشتباه {type(exc).__name__}')

        # ── ۴) خروج (issue) + کمبود موجودی ──
        i1 = make_voucher('issue', 'submitted', wh_from=wh_a, number=f'SCR-I1-{suffix}')
        add_item(i1, stock_item=si_a, base='30')
        finalize_voucher(i1, finalized_by=user)
        refresh()
        check(si_a.quantity == Decimal('95'), f'خروج: موجودی -30 ({si_a.quantity})')
        over = make_voucher('issue', 'submitted', wh_from=wh_a)
        add_item(over, stock_item=si_a, base='200')
        n_tx = StockTransaction.objects.count()
        try:
            finalize_voucher(over)
            check(False, 'خروجِ بیش از موجودی: باید InsufficientStockError بدهد')
        except InsufficientStockError:
            check(True, 'خروجِ بیش از موجودی: رد شد')
        refresh()
        check(si_a.quantity == Decimal('95') and StockTransaction.objects.count() == n_tx,
              'خروجِ بیش از موجودی: هیچ تغییری اعمال نشد')

        # ── ۵) تعدیل: نیازمند تأیید؛ علامتِ منفی = کاهش ──
        adj_bad = make_voucher('adjustment', 'submitted', wh_from=wh_a)
        add_item(adj_bad, stock_item=si_a, base='5')
        try:
            finalize_voucher(adj_bad)
            check(False, 'تعدیلِ ثبت‌شده: باید رد شود (نیازمند تأیید)')
        except VoucherStateError:
            check(True, 'تعدیلِ ثبت‌شده (بدون تأیید): رد شد')

        adj_nouser = make_voucher('adjustment', 'approved', wh_from=wh_a)
        add_item(adj_nouser, stock_item=si_a, base='5')
        try:
            finalize_voucher(adj_nouser)
            check(False, 'تعدیلِ تأییدشده بدون approver: باید رد شود')
        except VoucherStateError:
            check(True, 'تعدیلِ «approved» بدون approver: رد شد')

        adj = make_voucher('adjustment', 'approved', wh_from=wh_a, extra={'approver': user, 'approved_at': timezone.now()})
        add_item(adj, stock_item=si_a, base='5')
        finalize_voucher(adj, finalized_by=user)
        refresh()
        check(si_a.quantity == Decimal('100'), f'تعدیلِ مثبت: +5 ({si_a.quantity})')
        check(StockTransaction.objects.filter(reference_id=adj.id).first().transaction_type == 'adjustment',
              'تعدیل: تراکنش از نوع adjustment')

        adj_neg = make_voucher('adjustment', 'approved', wh_from=wh_a, extra={'approver': user, 'approved_at': timezone.now()})
        add_item(adj_neg, stock_item=si_a, base='-500')
        try:
            finalize_voucher(adj_neg)
            check(False, 'تعدیلِ منفیِ بیش از موجودی: باید رد شود')
        except InsufficientStockError:
            check(True, 'تعدیلِ منفیِ بیش از موجودی: رد شد')

        # ── ۶) ضایعاتِ نیازمند تأیید (requires_approval) ──
        waste = make_voucher('waste', 'submitted', wh_from=wh_a, extra={'requires_approval': True})
        add_item(waste, stock_item=si_a, base='10')
        try:
            finalize_voucher(waste)
            check(False, 'ضایعاتِ نیازمند تأیید (submitted): باید رد شود')
        except VoucherStateError:
            check(True, 'ضایعاتِ نیازمند تأیید (submitted): رد شد')
        waste.status, waste.approver, waste.approved_at = 'approved', user, timezone.now()
        waste.save()
        finalize_voucher(waste, finalized_by=user)
        refresh()
        check(si_a.quantity == Decimal('90'), f'ضایعاتِ تأییدشده: -10 ({si_a.quantity})')

        # ── ۷) انتقال: حل‌وفصلِ مقصد با محصول/برند ──
        t1 = make_voucher('transfer', 'submitted', wh_from=wh_a, wh_to=wh_b, number=f'SCR-T1-{suffix}')
        add_item(t1, product=prod, base='20')  # بدون موجودیِ صریح → resolve
        finalize_voucher(t1, finalized_by=user)
        refresh()
        check(si_a.quantity == Decimal('70') and si_b.quantity == Decimal('70'),
              f'انتقال: -20 مبدأ +20 مقصد ({si_a.quantity}/{si_b.quantity})')
        txs = StockTransaction.objects.filter(reference_type='warehouse_voucher', reference_id=t1.id)
        check(txs.count() == 2 and {t.transaction_type for t in txs} == {'in', 'out'},
              'انتقال: ۲ تراکنش (in/out)')

        # ── ۸) برگشتی (return) = ورود به انبارِ مبدأ ──
        ret = make_voucher('return', 'submitted', wh_from=wh_a, number=f'SCR-RET-{suffix}')
        add_item(ret, stock_item=si_a, base='8')
        finalize_voucher(ret, finalized_by=user)
        refresh()
        check(si_a.quantity == Decimal('78'), f'برگشتی: +8 ({si_a.quantity})')

        # ── ۹) برگشت (reversal) ──
        rev_res = reverse_voucher(r1, created_by=user)
        check(rev_res.applied is True and rev_res.is_reversal is True, 'برگشت: اعمال شد')
        refresh()
        mirror = rev_res.voucher
        check(mirror.reverses_id == r1.id, 'حوالهٔ آینه‌ای به اصلی پیوند دارد')
        check(mirror.voucher_number == f'SCR-R1-{suffix}-R', f'شمارهٔ آینه‌ای: {mirror.voucher_number}')
        check(mirror.voucher_type == 'receipt' and mirror.status == 'finalized',
              'آینه‌ای: همان نوع، نهایی‌شده')
        r1.refresh_from_db()
        check(r1.status == 'reversed' and r1.reversed_at is not None, 'اصلی: «برگشت‌خورده» شد')
        check(list(r1.reversed_by.all()) == [mirror], 'رابطهٔ reversed_by برقرار است')
        check(si_a.quantity == Decimal('53'), f'برگشتِ ورود: -25 ({si_a.quantity})')
        mtx = StockTransaction.objects.filter(reference_type='warehouse_voucher', reference_id=mirror.id).first()
        check(mtx is not None and mtx.transaction_type == 'out' and mtx.voucher_id == mirror.id,
              'تراکنشِ برگشت: از نوع out با ارجاع به آینه‌ای')
        check(mtx.description.startswith('برگشتِ'), 'توضیحِ تراکنشِ برگشت: «برگشتِ...»')

        try:
            reverse_voucher(r1, created_by=user)
            check(False, 'برگشتِ دوباره: باید خطا بدهد')
        except VoucherStateError:
            check(True, 'برگشتِ دوباره: رد شد')
        try:
            reverse_voucher(mirror, created_by=user)
            check(False, 'برگشتِ خودِ آینه‌ای: باید رد شود')
        except VoucherStateError:
            check(True, 'برگشتِ خودِ آینه‌ای: رد شد')

        # ── ۱۰) کمبودِ موجودی در برگشتِ ورودِ بزرگ ──
        r3 = make_voucher('receipt', 'submitted', wh_to=wh_a)
        add_item(r3, stock_item=si_a, base='100')
        finalize_voucher(r3, finalized_by=user)
        i3 = make_voucher('issue', 'submitted', wh_from=wh_a)
        add_item(i3, stock_item=si_a, base='150')
        finalize_voucher(i3, finalized_by=user)   # موجودی: 53+100-150 = 3
        refresh()
        try:
            reverse_voucher(r3, created_by=user)
            check(False, 'برگشتِ ورودی که سهمش مصرف شده: باید رد شود')
        except InsufficientStockError:
            check(True, 'برگشتِ ورودیِ مصرف‌شده: کمبودِ موجودی رد شد')
        refresh()
        check(si_a.quantity == Decimal('3'), f'بدون تغییر: موجودی {si_a.quantity}')

        # ── ۱۱) کافهٔ ناهماهنگ ──
        cross = make_voucher('receipt', 'submitted', wh_to=wh_a)
        add_item(cross, product=prod_other, base='1')
        try:
            finalize_voucher(cross)
            check(False, 'محصول از کافهٔ دیگر: باید رد شود')
        except VoucherValidationError:
            check(True, 'محصول از کافهٔ دیگر: رد شد')
        w_cross = Warehouse.objects.create(name='SCR-X', code=f'SCR-X-{suffix}',
                                           cafe=Cafe.objects.exclude(id=cafe.id).first())
        cross2 = make_voucher('issue', 'submitted', wh_from=w_cross)
        add_item(cross2, product=prod, base='1')
        try:
            finalize_voucher(cross2)
            check(False, 'انبار از کافهٔ دیگر: باید رد شود')
        except VoucherValidationError:
            check(True, 'انبار از کافهٔ دیگر: رد شد')

        # ── ۱۲) واحد و نرخ تبدیل ──
        bad_unit = make_voucher('issue', 'submitted', wh_from=wh_a)
        add_item(bad_unit, stock_item=si_a, base='1', unit_of_item=unit_other)
        try:
            finalize_voucher(bad_unit)
            check(False, 'واحدِ ناهماهنگ: باید رد شود')
        except VoucherValidationError:
            check(True, 'واحدِ ناهماهنگ: رد شد')
        bad_rate = make_voucher('issue', 'submitted', wh_from=wh_a)
        add_item(bad_rate, stock_item=si_a, base='10', trade='3', rate='3')  # 10 ≠ 9
        try:
            finalize_voucher(bad_rate)
            check(False, 'نرخ تبدیلِ ناسازگار: باید رد شود')
        except VoucherValidationError:
            check(True, 'نرخ تبدیلِ ناسازگار: رد شد')
        StockItem.objects.filter(id=si_a.id).update(quantity=Decimal('20'))  # تنظیمِ دستی برای این سناریو
        refresh()
        good_rate = make_voucher('issue', 'submitted', wh_from=wh_a)
        add_item(good_rate, stock_item=si_a, base='9', trade='3', rate='3')
        finalize_voucher(good_rate, finalized_by=user)  # 3 × 3 = 9 → درست
        refresh()
        check(si_a.quantity == Decimal('11'), f'نرخ تبدیلِ درست: 9×3 پذیرفته و اعمال شد ({si_a.quantity})')

        # ── ۱۳) محصولِ بدون موجودیِ انبار ──
        nostock = make_voucher('issue', 'submitted', wh_from=wh_a)
        add_item(nostock, product=prod_nostock, base='1')
        try:
            finalize_voucher(nostock)
            check(False, 'بدون موجودیِ انبار در مبدأ: باید رد شود')
        except VoucherValidationError:
            check(True, 'بدون موجودیِ انبار در مبدأ: رد شد')

        # ── ۱۴) حذفِ حوالهٔ نهایی‌شده با تراکنش: ممنوع (PROTECT) ──
        try:
            r1.delete()
            check(False, 'حذفِ حوالهٔ نهایی‌شده: باید PROTECT شود')
        except ProtectedError:
            check(True, 'حذفِ حوالهٔ نهایی‌شده: PROTECT')

        raise RuntimeError('__ROLLBACK__')
except RuntimeError:
    pass

print()
print('final: vouchers =', WarehouseVoucher.objects.count(), '(baseline', baseline_vouchers, ')')
print('final: stock_tx =', StockTransaction.objects.count(), '(baseline', baseline_tx, ')')
print('final: stock_items =', StockItem.objects.count(), '(baseline', baseline_items, ')')
print()
if failures:
    print(f'{len(failures)} FAILURE(S):')
    for f in failures:
        print(' -', f)
    raise SystemExit(1)
print('ALL CHECKS PASSED')
