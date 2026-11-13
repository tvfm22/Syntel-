# ─────────────────────────────────────────────────────────────────────────────
# سرویس مرکزی انبار — ناوردهای نهایی‌سازی و برگشت حواله
#
# چرا این فایل لازم است: تا پیش از این، finalize_voucher و reverse_voucher هیچ
# تستِ مستقیمی نداشتند — همهٔ تست‌ها از راه HTTP آن‌ها را صدا می‌زدند. ناوردهای
# حیاتیِ خودِ سرویس (ایدمپوتنسی، دروازهٔ وضعیت، تمام‌یا-هیچ بودن، اعتبارسنجی
# پیش از اولین نوشتن) در آن مسیر قابل مشاهده نیستند و بی‌تست مانده بودند.
#
# محورهای پوشش:
#   ۱. دروازهٔ وضعیت: draft/submitted/approved و انواع نیازمند تأیید
#   ۲. ایدمپوتنسی: نهایی‌سازی دوباره = بدون اثر
#   ۳. اعتبارسنجیِ پیش از نوشتن و تمام‌یا-هیچ بودن (اتمیک)
#   ۴. ساختِ خودکار موجودیِ انبار برای سمتِ ورود
#   ۵. شماره‌گذاری حواله و حوالهٔ برگشتی
#   ۶. برگشت: آینه‌ای‌بودن، وارونگیِ اثر، و مرزهای مجاز
# ─────────────────────────────────────────────────────────────────────────────
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from purchases.models import (
    Cafe, Department, Product, StockItem, StockTransaction, Unit,
    Warehouse, WarehouseVoucher, WarehouseVoucherItem,
)
from purchases.services.inventory import (
    InsufficientStockError, VoucherStateError, VoucherValidationError,
    finalize_voucher, next_voucher_number, reverse_voucher,
)

User = get_user_model()

D = Decimal

# فرستادنِ صریحِ None با پیش‌فرضِ «مشخص‌نشده» فرق دارد — برای ساختِ قلمِ ناقص
_UNSET = object()


class _VoucherFactoryMixin:
    """زیرساخت مشترک: کافه/انبار/محصول/موجودی + سازندهٔ حواله و قلم"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='store_svc', password='testpass123', role='store')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.other_cafe = Cafe.objects.create(name='Other', code='other', is_active=True)
        self.user.cafe = self.cafe
        self.user.save()

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.unit_other = Unit.objects.create(
            name='Gram', symbol='g', cafe=self.other_cafe)

        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe, code='whA', name='انبار مرکزی', is_active=True)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe, code='whB', name='انبار بخش', is_active=True)
        self.wh_other = Warehouse.objects.create(
            cafe=self.other_cafe, code='whO', name='انبار کافهٔ دیگر', is_active=True)

        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=1)
        self.product_other = Product.objects.create(
            name='Tea', unit=self.unit_other, cafe=self.other_cafe, min_stock=1)

        self.stock_a = StockItem.objects.create(
            product=self.product, warehouse=self.wh_a, quantity=D('10'),
            unit=self.unit)
        self.stock_b = StockItem.objects.create(
            product=self.product, warehouse=self.wh_b, quantity=D('0'),
            unit=self.unit)

        self.dept = Department.objects.create(
            name='بخش پذیرایی', cafe=self.cafe, is_active=True)

    # ── سازنده‌ها ──
    def _voucher(self, voucher_type, *, source=None, dest=None, department=None,
                 status='submitted', cafe=None, **extra):
        cafe = cafe or self.cafe
        return WarehouseVoucher.objects.create(
            cafe=cafe, voucher_type=voucher_type, status=status,
            voucher_number=next_voucher_number(cafe.id, voucher_type),
            source_warehouse=source, destination_warehouse=dest,
            department=department, created_by=self.user, **extra)

    def _item(self, voucher, *, product=_UNSET, unit=_UNSET, stock_item=None,
              base='5'):
        return WarehouseVoucherItem.objects.create(
            voucher=voucher,
            product=self.product if product is _UNSET else product,
            stock_item=stock_item,
            unit=self.unit if unit is _UNSET else unit,
            base_quantity=D(base),
            unit_price=D('1000'),
            description='قلم آزمایشی',
        )

    def _tx_count(self):
        return StockTransaction.objects.count()

    def _assert_no_effect(self, stock, expected, msg=''):
        """موجودی دست‌نخورده و هیچ ردیف تراکنشی — برای مسیرهای ردشده"""
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, D(expected), msg)
        self.assertEqual(self._tx_count(), 0, msg)


# ═══════════════════════════════════════════════════════════════════════════
# ۱. دروازهٔ وضعیت
# ═══════════════════════════════════════════════════════════════════════════
class FinalizeStateGateTests(_VoucherFactoryMixin, TestCase):
    """کدام وضعیت‌ها اجازهٔ نهایی‌سازی دارند — و کدام‌ها نه"""

    def test_draft_cannot_be_finalized(self):
        """پیش‌نویس هرگز نهایی نمی‌شود — باید اول «ثبت» شود"""
        v = self._voucher('issue', source=self.wh_a, status='draft')
        self._item(v, stock_item=self.stock_a)
        with self.assertRaises(VoucherStateError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')
        v.refresh_from_db()
        self.assertEqual(v.status, 'draft')

    def test_submitted_issue_finalizes(self):
        """نوع روتین از «ثبت‌شده» نهایی می‌شود"""
        v = self._voucher('issue', source=self.wh_a, status='submitted')
        self._item(v, stock_item=self.stock_a, base='4')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('6'))
        v.refresh_from_db()
        self.assertEqual(v.status, 'finalized')
        self.assertIsNotNone(v.finalized_at)

    def test_approved_issue_also_finalizes(self):
        """نوع روتینی که مسیر تأیید را هم طی کرده، از «تأییدشده» هم نهایی می‌شود"""
        v = self._voucher('issue', source=self.wh_a, status='approved')
        self._item(v, stock_item=self.stock_a, base='4')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('6'))

    def test_adjustment_from_submitted_rejected(self):
        """تعدیل بدون تأیید نهایی نمی‌شود (نوعِ نیازمند تأیید)"""
        v = self._voucher('adjustment', source=self.wh_a, status='submitted')
        self._item(v, stock_item=self.stock_a, base='+3')
        with self.assertRaises(VoucherStateError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')

    def test_adjustment_approved_without_approver_rejected(self):
        """وضعیت «تأییدشده» بدون تأییدکننده/تاریخ کافی نیست — گاردِ سختگیرانه"""
        v = self._voucher('adjustment', source=self.wh_a, status='approved')
        self._item(v, stock_item=self.stock_a, base='+3')
        with self.assertRaises(VoucherStateError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')

    def test_adjustment_fully_approved_finalizes(self):
        """تعدیل با تأییدکننده و تاریخ تأیید نهایی می‌شود"""
        from django.utils import timezone
        v = self._voucher('adjustment', source=self.wh_a, status='approved',
                          approver=self.user, approved_at=timezone.now())
        self._item(v, stock_item=self.stock_a, base='+3')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('13'))

    def test_requires_approval_flag_gates_a_routine_type(self):
        """پرچم «نیازمند تأیید» روی نوعِ روتین هم دروازه می‌سازد (ضایعات بالای آستانه)"""
        v = self._voucher('waste', source=self.wh_a, status='submitted',
                          requires_approval=True)
        self._item(v, stock_item=self.stock_a, base='1')
        with self.assertRaises(VoucherStateError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')

    def test_requires_approval_flag_approved_finalizes(self):
        from django.utils import timezone
        v = self._voucher('waste', source=self.wh_a, status='approved',
                          requires_approval=True, approver=self.user,
                          approved_at=timezone.now())
        self._item(v, stock_item=self.stock_a, base='1')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('9'))

    def test_cancelled_cannot_be_finalized(self):
        v = self._voucher('issue', source=self.wh_a, status='cancelled')
        self._item(v, stock_item=self.stock_a)
        with self.assertRaises(VoucherStateError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')

    def test_unsaved_voucher_rejected(self):
        """حوالهٔ ذخیره‌نشده (بدون pk) قابل نهایی‌سازی نیست"""
        v = WarehouseVoucher(cafe=self.cafe, voucher_type='issue',
                             status='submitted', voucher_number='ISS-X',
                             source_warehouse=self.wh_a)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)


# ═══════════════════════════════════════════════════════════════════════════
# ۲. ایدمپوتنسی
# ═══════════════════════════════════════════════════════════════════════════
class FinalizeIdempotencyTests(_VoucherFactoryMixin, TestCase):
    """نهایی‌سازی دوباره (POST تکراری، refresh، retry، race) باید بی‌اثر باشد"""

    def test_second_finalize_is_noop(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='4')
        first = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(first.applied)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('6'))
        tx_after_first = self._tx_count()

        second = finalize_voucher(v, finalized_by=self.user)
        self.assertFalse(second.applied)          # ایدمپوتنت
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('6'))   # موجودی دوباره کم نشد
        self.assertEqual(self._tx_count(), tx_after_first)  # ردیف ممیزی تکراری نشد

    def test_third_finalize_still_noop(self):
        v = self._voucher('receipt', dest=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='4')
        for _ in range(3):
            result = finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('14'))  # فقط یک بار ۱۰+۴


# ═══════════════════════════════════════════════════════════════════════════
# ۳. اعتبارسنجیِ پیش از نوشتن — تمام‌یا-هیچ
# ═══════════════════════════════════════════════════════════════════════════
class FinalizeValidationTests(_VoucherFactoryMixin, TestCase):
    """هر ردشدنی باید *پیش از* اولین نوشتن رخ دهد: نه کسری، نه ردیف ممیزی"""

    def test_voucher_without_items_rejected(self):
        v = self._voucher('issue', source=self.wh_a)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)
        self.assertEqual(self._tx_count(), 0)

    def test_item_without_product_or_stock_item_rejected(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, product=None, stock_item=None)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_nonpositive_quantity_rejected_for_routine_type(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='0')
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_zero_quantity_rejected_for_adjustment(self):
        """تعدیلِ صفر بی‌معناست — باید رد شود، نه اینکه سند بی‌اثر بسازد"""
        from django.utils import timezone
        v = self._voucher('adjustment', source=self.wh_a, status='approved',
                          approver=self.user, approved_at=timezone.now())
        self._item(v, stock_item=self.stock_a, base='0')
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_negative_quantity_rejected_for_routine_type(self):
        """منفی فقط برای تعدیل معنا دارد (جهتش را از علامت می‌گیرد)"""
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='-3')
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_item_without_unit_rejected(self):
        v = self._voucher('receipt', dest=self.wh_a)
        self._item(v, stock_item=self.stock_a, unit=None)
        # unit فیلد اجباریِ مدل است؛ مسیر واقعی: قلم بدون واحد از فرم رد می‌شود.
        # اینجا مستقیماً نبودِ واحد را می‌سنجیم.
        v.items.update(unit=None)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_unit_from_other_cafe_rejected(self):
        v = self._voucher('receipt', dest=self.wh_a)
        self._item(v, stock_item=None, product=self.product, unit=self.unit_other)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_unit_mismatch_with_stock_item_rejected(self):
        """واحد قلم باید با واحد موجودیِ انبار یکی باشد"""
        alt_unit = Unit.objects.create(
            name='Liter', symbol='L', cafe=self.cafe)
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, unit=alt_unit)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')

    def test_product_mismatch_with_stock_item_rejected(self):
        """محصول قلم با محصول موجودیِ انتخابی باید یکی باشد"""
        other_product = Product.objects.create(
            name='Flour', unit=self.unit, cafe=self.cafe, min_stock=0)
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, product=other_product, stock_item=self.stock_a)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')

    def test_foreign_cafe_warehouse_rejected(self):
        """انبارِ کافهٔ دیگر — حتی اگر شناسه‌اش درست ارسال شود"""
        v = self._voucher('issue', source=self.wh_other)
        self._item(v, stock_item=None, product=self.product_other,
                   unit=self.unit_other)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)
        self.assertEqual(self._tx_count(), 0)

    def test_insufficient_stock_rejected(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='99')
        with self.assertRaises(InsufficientStockError):
            finalize_voucher(v, finalized_by=self.user)
        self._assert_no_effect(self.stock_a, '10')
        v.refresh_from_db()
        self.assertEqual(v.status, 'submitted')  # وضعیت هم تغییر نکرد

    def test_partial_failure_applies_nothing(self):
        """یک قلمِ ناکافی کل سند را برمی‌گرداند — قلمِ سالمِ قبلی هم اعمال نمی‌شود"""
        ok_product = Product.objects.create(
            name='Flour', unit=self.unit, cafe=self.cafe, min_stock=0)
        ok_stock = StockItem.objects.create(
            product=ok_product, warehouse=self.wh_a, quantity=D('50'),
            unit=self.unit)
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, product=ok_product, stock_item=ok_stock, base='5')
        self._item(v, stock_item=self.stock_a, base='99')  # ناکافی
        with self.assertRaises(InsufficientStockError):
            finalize_voucher(v, finalized_by=self.user)
        ok_stock.refresh_from_db()
        self.assertEqual(ok_stock.quantity, D('50'))   # دست‌نخورده
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))
        self.assertEqual(self._tx_count(), 0)

    def test_transfer_same_warehouse_rejected(self):
        v = self._voucher('transfer', source=self.wh_a, dest=self.wh_a)
        self._item(v, stock_item=self.stock_a)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_transfer_without_dest_rejected(self):
        v = self._voucher('transfer', source=self.wh_a, dest=None)
        self._item(v, stock_item=self.stock_a)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_receipt_without_dest_rejected(self):
        v = self._voucher('receipt', dest=None)
        self._item(v, stock_item=self.stock_a)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_unknown_voucher_type_rejected(self):
        v = self._voucher('bogus', source=self.wh_a)
        self._item(v, stock_item=self.stock_a)
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)

    def test_adjustment_without_source_warehouse_rejected(self):
        """تعدیل باید بداند کدام انبار را تعدیل می‌کند"""
        from django.utils import timezone
        v = self._voucher('adjustment', source=None, status='approved',
                          approver=self.user, approved_at=timezone.now())
        self._item(v, stock_item=self.stock_a, base='+3')
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)


# ═══════════════════════════════════════════════════════════════════════════
# ۴. ساختِ خودکار موجودیِ انبار (سمتِ ورود)
# ═══════════════════════════════════════════════════════════════════════════
class CreateIfMissingTests(_VoucherFactoryMixin, TestCase):
    """سمتِ «ورود» برای کالای بدون موجودیِ از پیش‌تعریف‌شده، خودش ردیف می‌سازد"""

    def setUp(self):
        super().setUp()
        self.fresh = Product.objects.create(
            name='Flour', unit=self.unit, cafe=self.cafe, min_stock=0)

    def _assert_fresh_stock(self, qty):
        si = StockItem.objects.get(product=self.fresh, warehouse=self.wh_a)
        self.assertEqual(si.quantity, D(qty))
        self.assertEqual(si.unit_id, self.unit.id)
        return si

    def test_receipt_creates_missing_stock_item(self):
        v = self._voucher('receipt', dest=self.wh_a)
        self._item(v, product=self.fresh, stock_item=None, base='5')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self._assert_fresh_stock('5')

    def test_return_creates_missing_stock_item(self):
        v = self._voucher('return', source=self.wh_a)
        self._item(v, product=self.fresh, stock_item=None, base='5')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self._assert_fresh_stock('5')

    def test_direct_delivery_creates_missing_stock_item(self):
        """کالای تازه‌خریدِ تحویل مستقیم: ردیف ساخته می‌شود و خالص صفر می‌ماند"""
        v = self._voucher('direct_delivery', dest=self.wh_a, department=self.dept)
        self._item(v, product=self.fresh, stock_item=None, base='4')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self._assert_fresh_stock('0')
        self.assertEqual(self._tx_count(), 2)  # ورود و بلافاصله خروج

    def test_issue_does_not_create_missing_stock_item(self):
        """خروجِ کالای بدون موجودی: خطا — نه ساختنِ ردیفِ صفر و بعد شکست"""
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, product=self.fresh, stock_item=None, base='5')
        with self.assertRaises(VoucherValidationError):
            finalize_voucher(v, finalized_by=self.user)
        self.assertFalse(StockItem.objects.filter(
            product=self.fresh, warehouse=self.wh_a).exists())
        self.assertEqual(self._tx_count(), 0)

    def test_created_stock_is_reused_on_second_receipt(self):
        """رسید دوم همان ردیف را استفاده می‌کند — ردیف تکراری ساخته نمی‌شود"""
        v1 = self._voucher('receipt', dest=self.wh_a)
        self._item(v1, product=self.fresh, stock_item=None, base='5')
        finalize_voucher(v1, finalized_by=self.user)

        v2 = self._voucher('receipt', dest=self.wh_a)
        self._item(v2, product=self.fresh, stock_item=None, base='3')
        finalize_voucher(v2, finalized_by=self.user)

        rows = StockItem.objects.filter(product=self.fresh, warehouse=self.wh_a)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().quantity, D('8'))


# ═══════════════════════════════════════════════════════════════════════════
# ۵. شماره‌گذاری
# ═══════════════════════════════════════════════════════════════════════════
class VoucherNumberTests(_VoucherFactoryMixin, TestCase):

    def test_prefix_per_type(self):
        cases = {
            'receipt': 'RCV', 'issue': 'ISS', 'return': 'RET', 'sale': 'SALE',
            'waste': 'WST', 'transfer': 'TRF', 'adjustment': 'ADJ',
            'direct_delivery': 'DLV',
        }
        for voucher_type, prefix in cases.items():
            with self.subTest(voucher_type=voucher_type):
                number = next_voucher_number(self.cafe.id, voucher_type)
                self.assertTrue(number.startswith(prefix + '-'),
                                f'{voucher_type} → {number} (انتظار {prefix}-)')

    def test_number_is_unique_after_creation(self):
        first = next_voucher_number(self.cafe.id, 'issue')
        WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number=first, source_warehouse=self.wh_a)
        second = next_voucher_number(self.cafe.id, 'issue')
        self.assertNotEqual(first, second)

    def test_numbers_are_scoped_per_cafe(self):
        """هر کافه شماره‌گذاری خودش را دارد — شماره‌ها بین کافه‌ها مشترک نیست"""
        a = next_voucher_number(self.cafe.id, 'issue')
        b = next_voucher_number(self.other_cafe.id, 'issue')
        self.assertEqual(a, b)  # هر دو از 00001 شروع می‌کنند
        WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number=a, source_warehouse=self.wh_a)
        # شمارهٔ کافهٔ دیگر با وجود شمارهٔ یکسان در کافهٔ A آزاد است
        self.assertEqual(next_voucher_number(self.other_cafe.id, 'issue'), b)

    def test_reversal_number_gets_R_suffix(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='2')
        finalize_voucher(v, finalized_by=self.user)
        reverse_voucher(v, created_by=self.user)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        self.assertEqual(mirror.voucher_number, f'{v.voucher_number}-R')

    def test_reversal_number_collision_gets_R2(self):
        """اگر «-R» اشغال باشد، «-R2» انتخاب می‌شود — تکراری نمی‌شود"""
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='2')
        finalize_voucher(v, finalized_by=self.user)
        WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number=f'{v.voucher_number}-R', source_warehouse=self.wh_a)
        reverse_voucher(v, created_by=self.user)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        self.assertEqual(mirror.voucher_number, f'{v.voucher_number}-R2')


# ═══════════════════════════════════════════════════════════════════════════
# ۶. برگشت
# ═══════════════════════════════════════════════════════════════════════════
class ReverseVoucherTests(_VoucherFactoryMixin, TestCase):

    def _finalized_issue(self, base='4'):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base=base)
        finalize_voucher(v, finalized_by=self.user)
        return v

    def test_reverse_inverts_effect_and_marks_original(self):
        v = self._finalized_issue(base='4')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('6'))

        result = reverse_voucher(v, created_by=self.user)
        self.assertTrue(result.applied)
        self.assertTrue(result.is_reversal)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))  # به حالت اول برگشت

        v.refresh_from_db()
        self.assertEqual(v.status, 'reversed')
        self.assertIsNotNone(v.reversed_at)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        self.assertEqual(mirror.status, 'finalized')
        self.assertEqual(mirror.cafe_id, self.cafe.id)

    def test_mirror_transaction_type_is_opposite(self):
        """تراکنشِ برگشت نوعش از جهتِ واقعیِ حرکت می‌آید: برگشتِ خروج = ورود"""
        v = self._finalized_issue(base='4')
        reverse_voucher(v, created_by=self.user)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        tx = StockTransaction.objects.get(voucher=mirror)
        self.assertEqual(tx.transaction_type, 'in')
        self.assertEqual(tx.quantity, D('4'))
        self.assertEqual(tx.previous_quantity, D('6'))
        self.assertEqual(tx.new_quantity, D('10'))

    def test_reverse_submitted_rejected(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='4')
        with self.assertRaises(VoucherStateError):
            reverse_voucher(v, created_by=self.user)
        self._assert_no_effect(self.stock_a, '10')

    def test_reverse_draft_rejected(self):
        v = self._voucher('issue', source=self.wh_a, status='draft')
        self._item(v, stock_item=self.stock_a, base='4')
        with self.assertRaises(VoucherStateError):
            reverse_voucher(v, created_by=self.user)

    def test_reverse_twice_rejected(self):
        v = self._finalized_issue()
        reverse_voucher(v, created_by=self.user)
        with self.assertRaises(VoucherStateError):
            reverse_voucher(v, created_by=self.user)
        # فقط یک حوالهٔ آینه‌ای ساخته شده و موجودی دوباره برنگشته
        self.assertEqual(WarehouseVoucher.objects.filter(reverses=v).count(), 1)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))

    def test_reverse_of_mirror_rejected(self):
        """حوالهٔ برگشتی خودش قابل برگشت نیست"""
        v = self._finalized_issue()
        reverse_voucher(v, created_by=self.user)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        with self.assertRaises(VoucherStateError):
            reverse_voucher(mirror, created_by=self.user)

    def test_reversal_cannot_be_undone_by_any_route(self):
        """مستندسازیِ یک محدودیت واقعی: برگشت، بازگشت‌پذیر نیست.

        نه آینه قابل برگشت است (چون خودش برگشتی است) و نه اصلی (چون وضعیتش
        «برگشت‌خورده» است و سرویس فقط «نهایی‌شده» را می‌پذیرد). یعنی یک برگشتِ
        اشتباه از راه سرویس جبران نمی‌شود و باید حوالهٔ جدید ثبت کرد.
        اگر روزی «برگشتِ برگشت» اضافه شد، این تست باید عوض شود.
        """
        v = self._finalized_issue(base='4')
        reverse_voucher(v, created_by=self.user)
        mirror = WarehouseVoucher.objects.get(reverses=v)

        with self.assertRaises(VoucherStateError):
            reverse_voucher(mirror, created_by=self.user)
        with self.assertRaises(VoucherStateError):
            reverse_voucher(v, created_by=self.user)

        # موجودی همان‌جایی که بعد از برگشت بود می‌ماند
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))

    def test_reverse_adjustment_sets_approver_on_mirror(self):
        """تعدیل: خودِ اقدامِ برگشت، تأییدِ آینه است (جریان یک‌گامه)"""
        from django.utils import timezone
        v = self._voucher('adjustment', source=self.wh_a, status='approved',
                          approver=self.user, approved_at=timezone.now())
        self._item(v, stock_item=self.stock_a, base='+3')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('13'))

        reverse_voucher(v, created_by=self.user)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        self.assertEqual(mirror.status, 'finalized')
        self.assertEqual(mirror.approver_id, self.user.id)
        self.assertIsNotNone(mirror.approved_at)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))

    def test_reverse_adjustment_negative_direction(self):
        """تعدیلِ منفی برگشت‌خورده باید موجودی را برگرداند (افزایش)"""
        from django.utils import timezone
        v = self._voucher('adjustment', source=self.wh_a, status='approved',
                          approver=self.user, approved_at=timezone.now())
        self._item(v, stock_item=self.stock_a, base='-4')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('6'))

        reverse_voucher(v, created_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))

    def test_reverse_transfer_inverts_both_sides(self):
        v = self._voucher('transfer', source=self.wh_a, dest=self.wh_b)
        self._item(v, stock_item=self.stock_a, base='3')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('7'))
        self.assertEqual(self.stock_b.quantity, D('3'))

        reverse_voucher(v, created_by=self.user)
        self.stock_a.refresh_from_db()
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))
        self.assertEqual(self.stock_b.quantity, D('0'))

    def test_reverse_receipt_after_stock_spent_is_rejected(self):
        """برگشتِ ورود وقتی موجودی خرج شده: کفایت موجودی نمی‌گذارد → بدون تغییر

        این حالت واقعی است: کالای رسیده فروش رفته و موجودی کافی برای برگشت نیست.
        """
        v = self._voucher('receipt', dest=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('15'))

        # موجودی خرج می‌شود (مستقیم، بدون حواله — صرفاً برای ساختن وضعیت)
        StockItem.objects.filter(id=self.stock_a.id).update(quantity=D('0'))

        with self.assertRaises(InsufficientStockError):
            reverse_voucher(v, created_by=self.user)

        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('0'))   # دست‌نخورده
        v.refresh_from_db()
        self.assertEqual(v.status, 'finalized')            # اصلی برنگشت‌خورده نشد
        self.assertEqual(WarehouseVoucher.objects.filter(reverses=v).count(), 0)

    def test_reverse_is_atomic_no_partial_mirror(self):
        """شکستِ برگشت نباید حوالهٔ آینه‌ایِ نیمه‌ساخته باقی بگذارد"""
        v = self._voucher('transfer', source=self.wh_a, dest=self.wh_b)
        self._item(v, stock_item=self.stock_a, base='3')
        finalize_voucher(v, finalized_by=self.user)
        # مقصد را خالی می‌کنیم تا برگشتِ سمتِ مقصد ناکافی شود
        StockItem.objects.filter(id=self.stock_b.id).update(quantity=D('0'))

        with self.assertRaises(InsufficientStockError):
            reverse_voucher(v, created_by=self.user)
        self.assertEqual(WarehouseVoucher.objects.filter(reverses=v).count(), 0)

    def test_reverse_creates_mirror_items_matching_original(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='4')
        finalize_voucher(v, finalized_by=self.user)
        reverse_voucher(v, created_by=self.user)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        original_item = v.items.get()
        mirror_item = mirror.items.get()
        self.assertEqual(mirror_item.product_id, original_item.product_id)
        self.assertEqual(mirror_item.stock_item_id, original_item.stock_item_id)
        self.assertEqual(mirror_item.unit_id, original_item.unit_id)
        self.assertEqual(mirror_item.base_quantity, original_item.base_quantity)


# ═══════════════════════════════════════════════════════════════════════════
# ۷. اثر خالص هر نوع روی موجودی
# ═══════════════════════════════════════════════════════════════════════════
class VoucherTypeNetEffectTests(_VoucherFactoryMixin, TestCase):
    """جدولِ جهتِ هر نوع حواله — مرجعِ رفتاری در یک نگاه"""

    def test_receipt_increases_destination_only(self):
        v = self._voucher('receipt', dest=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('15'))
        self.assertEqual(self.stock_b.quantity, D('0'))

    def test_issue_decreases_source_only(self):
        v = self._voucher('issue', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('5'))
        self.assertEqual(self.stock_b.quantity, D('0'))

    def test_return_increases_source(self):
        """برگشتِ کالا به انبار = ورود به انبارِ مبدأ"""
        v = self._voucher('return', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('15'))

    def test_sale_decreases_source(self):
        v = self._voucher('sale', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('5'))

    def test_waste_decreases_source(self):
        v = self._voucher('waste', source=self.wh_a)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('5'))

    def test_transfer_moves_both_sides(self):
        v = self._voucher('transfer', source=self.wh_a, dest=self.wh_b)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('5'))
        self.assertEqual(self.stock_b.quantity, D('5'))

    def test_adjustment_positive_and_negative(self):
        from django.utils import timezone
        v = self._voucher('adjustment', source=self.wh_a, status='approved',
                          approver=self.user, approved_at=timezone.now())
        self._item(v, stock_item=self.stock_a, base='+5')
        finalize_voucher(v, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('15'))

        v2 = self._voucher('adjustment', source=self.wh_a, status='approved',
                           approver=self.user, approved_at=timezone.now())
        self._item(v2, stock_item=self.stock_a, base='-3')
        finalize_voucher(v2, finalized_by=self.user)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('12'))

    def test_direct_delivery_is_net_zero_with_in_then_out_order(self):
        """تحویل مستقیم: کالا از انبار می‌گذرد — ورود کامل و بلافاصله خروج کامل"""
        v = self._voucher('direct_delivery', dest=self.wh_a, department=self.dept)
        self._item(v, stock_item=self.stock_a, base='5')
        finalize_voucher(v, finalized_by=self.user)

        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, D('10'))  # خالص صفر
        txs = list(StockTransaction.objects.filter(voucher=v).order_by('id'))
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0].transaction_type, 'in')   # اول ورود
        self.assertEqual(txs[0].previous_quantity, D('10'))
        self.assertEqual(txs[0].new_quantity, D('15'))
        self.assertEqual(txs[1].transaction_type, 'out')  # بعد خروج
        self.assertEqual(txs[1].previous_quantity, D('15'))
        self.assertEqual(txs[1].new_quantity, D('10'))

    def test_direct_delivery_works_from_zero_stock(self):
        """چون ورود اول اعمال می‌شود، از موجودیِ صفر هم قابل اجراست"""
        v = self._voucher('direct_delivery', dest=self.wh_b, department=self.dept)
        self._item(v, stock_item=self.stock_b, base='5')
        result = finalize_voucher(v, finalized_by=self.user)
        self.assertTrue(result.applied)
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, D('0'))
