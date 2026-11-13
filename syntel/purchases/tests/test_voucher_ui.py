# ─────────────────────────────────────────────────────────────────────────────
# Prompt 6 — UI حواله‌های انبار روی شِمای جدید
#
#  - فرم یکپارچهٔ ایجاد: هر ۷ نوع (رسید/خروج/برگشتی/فروش/ضایعات/انتقال/تعدیل)
#  - مسیر تأیید تعدیل: ثبت → در انتظار مدیر → تأیید و نهایی‌سازی (فقط مدیر)
#  - پیش‌نویس: ویرایش توسط ایجادکننده (دیگران → 404)، لغو پیش از نهایی‌سازی
#  - برگشت: فقط نهایی‌شده، با دلیلِ اجباری؛ حوالهٔ آینه‌ای + وارون اثر
#  - فیلترهای لیست حواله + صفحهٔ سوابق حرکت با لینک حواله
#  - IDOR: نقاط ورود جدید (ویرایش/لغو/تأیید/برگشت) با شناسهٔ کافهٔ دیگر → 404
# ─────────────────────────────────────────────────────────────────────────────
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import (
    Cafe, Department, Product, StockItem, StockTransaction, Unit,
    Warehouse, WarehouseVoucher,
)

User = get_user_model()


def _set_cafe_session(client, cafe):
    session = client.session
    session['selected_cafe_id'] = cafe.id
    session['selected_cafe_code'] = cafe.code
    session.save()


class VoucherUIBaseline(TestCase):
    """زیرساخت مشترک: کافه + انبارها + محصول + موجودی"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='store_ui', password='testpass123', role='store')
        self.manager = User.objects.create_user(
            username='manager_ui', password='testpass123', role='manager')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.user.cafe = self.cafe
        self.user.save()
        self.manager.cafe = self.cafe
        self.manager.save()
        _set_cafe_session(self.client, self.cafe)
        self.client.login(username='store_ui', password='testpass123')

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.wh_a = Warehouse.objects.create(
            cafe=self.cafe, code='whA', name='انبار مرکزی', is_active=True)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe, code='whB', name='انبار بخش', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=1)
        self.stock_a = StockItem.objects.create(
            product=self.product, warehouse=self.wh_a, quantity=10, unit=self.unit)
        # موجودیِ مقصدِ انتقال: سرویس برای سمت مقصد، موجودیِ از پیش‌تعریف‌شده می‌خواهد
        self.stock_b = StockItem.objects.create(
            product=self.product, warehouse=self.wh_b, quantity=0, unit=self.unit)
        self.dept = Department.objects.create(
            name='بخش پذیرایی', cafe=self.cafe, is_active=True)

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'main', **kwargs})

    def _create_post(self, voucher_type, source=None, dest=None, qty='5',
                     stock_item=None, action='submit',
                     department=None, product=None, date=None):
        data = {
            'action': action,
            'product_id[]': [str((product or self.product).id)],
            'stock_item_id[]': [str(stock_item.id) if stock_item else ''],
            'unit_id[]': [str(self.unit.id)],
            'base_quantity[]': [qty],
            'unit_price[]': ['1000'],
            'item_description[]': ['قلم آزمایشی'],
        }
        if source:
            data['source_warehouse'] = str(source.id)
        if dest:
            data['dest_warehouse'] = str(dest.id)
        if department is not None:
            data['department'] = str(department.id)
        if date is not None:
            data['date'] = date
        return self.client.post(
            self._url('voucher_create', voucher_type=voucher_type), data=data)


class VoucherProductTypeaheadTests(VoucherUIBaseline):
    """انتخاب تایپی محصول در قلم‌ها (مثل باکس «نام کالا»ی درخواست‌ها)

    فیلد ارسالی همان product_id[] می‌ماند تا بک‌اند تغییری نکند؛ فقط سلکتِ
    هر ردیف به باکس «تایپ + انتخاب از فهرست» تبدیل شده و فهرست کامل محصولات
    یک‌بار در JS درج می‌شود (نه در هر ردیف). جعبهٔ جستجوی سراسری حذف شده.
    """

    def test_create_page_has_typeahead_row_and_single_products_list(self):
        r = self.client.get(self._url('voucher_create', voucher_type='receipt'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        # باکس تایپی + شناسهٔ مخفی با همان نام ارسالی قبلی
        self.assertIn('row-product-name', html)
        self.assertIn('row-product-id', html)
        self.assertIn('name="product_id[]"', html)
        self.assertIn('prod-ac-drop', html)
        # سلکتِ قدیمی هر ردیف و جعبهٔ جستجوی سراسری دیگر وجود ندارند
        self.assertNotIn('class="row-product"', html)
        self.assertNotIn('productSearch', html)
        # فهرست کامل محصولات فقط یک‌بار (در PRODUCTS) درج شده و نام محصول هست
        self.assertIn('{ id: %d, name: "Sugar" }' % self.product.id, html)
        self.assertEqual(html.count('{ id: '), 1)

    def test_edit_draft_prefills_product_name_in_typeahead(self):
        r = self._create_post('receipt', dest=self.wh_a, qty='5', action='draft')
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='receipt', status='draft')
        r = self.client.get(self._url('voucher_edit', voucher_id=v.id))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        # قلم پیش‌نویس: شناسهٔ مخفی + نام محصول از قبل در باکس تایپی پر شده
        self.assertIn('product_id: %d' % self.product.id, html)
        self.assertIn('product_name: "Sugar"', html)


class VoucherCreateEachTypeTests(VoucherUIBaseline):
    """ایجاد هر ۷ نوع از فرم یکپارچه + اثر صحیح روی موجودی"""

    def test_create_receipt_finalizes_and_increases_stock(self):
        r = self._create_post('receipt', dest=self.wh_a, qty='5')
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        self.assertEqual(v.status, 'finalized')
        self.assertEqual(v.destination_warehouse_id, self.wh_a.id)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('15'))  # 10 + 5
        tx = StockTransaction.objects.get(voucher=v)
        self.assertEqual(tx.transaction_type, 'in')
        self.assertEqual(tx.quantity, Decimal('5'))
        self.assertEqual(tx.new_quantity, Decimal('15'))

    def test_create_issue_decreases_stock(self):
        r = self._create_post('issue', source=self.wh_a, qty='3',
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='issue')
        self.assertEqual(v.status, 'finalized')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('7'))
        tx = StockTransaction.objects.get(voucher=v)
        self.assertEqual(tx.transaction_type, 'out')
        self.assertEqual(tx.quantity, Decimal('3'))
        self.assertEqual(tx.previous_quantity, Decimal('10'))
        self.assertEqual(tx.new_quantity, Decimal('7'))

    def test_create_issue_insufficient_stock_rejected(self):
        """کمبود موجودی: حواله ساخته نمی‌شود (تمام‌یا-هیچ)"""
        r = self._create_post('issue', source=self.wh_a, qty='99',
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 200)  # فرم با خطا دوباره رندر می‌شود
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))

    def test_create_return_adds_to_source(self):
        r = self._create_post('return', source=self.wh_a, qty='2')
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='return')
        self.assertEqual(v.status, 'finalized')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('12'))
        tx = StockTransaction.objects.get(voucher=v)
        self.assertEqual(tx.transaction_type, 'in')

    def test_create_sale_decreases_source(self):
        r = self._create_post('sale', source=self.wh_a, qty='4',
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('6'))

    def test_create_waste_decreases_source(self):
        r = self._create_post('waste', source=self.wh_a, qty='1',
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 302)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('9'))

    def test_create_transfer_moves_between_warehouses(self):
        r = self._create_post('transfer', source=self.wh_a, dest=self.wh_b, qty='3',
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='transfer')
        self.assertEqual(v.status, 'finalized')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('7'))  # −3 از مبدأ
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, Decimal('3'))  # +3 به مقصد
        txs = list(StockTransaction.objects.filter(voucher=v).order_by('id'))
        self.assertEqual(len(txs), 2)
        self.assertEqual({t.transaction_type for t in txs}, {'in', 'out'})

    def test_create_adjustment_waits_for_approval(self):
        """تعدیل: ثبت → «ثبت‌شده» بدون اثر؛ نهایی‌سازی فقط با تأیید مدیر"""
        r = self._create_post('adjustment', source=self.wh_a, qty='+3',
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='adjustment')
        self.assertEqual(v.status, 'submitted')
        self.assertIsNone(v.approver)
        # بدون اثر روی موجودی
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        self.assertEqual(StockTransaction.objects.count(), 0)

    def test_adjustment_negative_direction_reduces_stock(self):
        r = self._create_post('adjustment', source=self.wh_a, qty='-3',
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='adjustment')
        self.assertEqual(v.status, 'submitted')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))  # هنوز اثری نیست

    def test_transfer_same_warehouse_rejected(self):
        r = self._create_post('transfer', source=self.wh_a, dest=self.wh_a, qty='1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)

    def test_invalid_voucher_type_404(self):
        r = self.client.get(self._url('voucher_create', voucher_type='bogus'))
        self.assertEqual(r.status_code, 404)


class AdjustmentApprovalFlowTests(VoucherUIBaseline):
    """مسیر تأیید: فقط مدیر (admin/ceo/manager) می‌تواند تعدیل را نهایی کند"""

    def _submit_adjustment(self, qty='+3'):
        r = self._create_post('adjustment', source=self.wh_a, qty=qty,
                              stock_item=self.stock_a)
        self.assertEqual(r.status_code, 302)
        return WarehouseVoucher.objects.get(voucher_type='adjustment')

    def test_store_cannot_approve(self):
        v = self._submit_adjustment()
        r = self.client.get(self._url('voucher_approve', voucher_id=v.id))
        # role_required کاربرِ غیرمدیر را به لاگین هدایت می‌کند (قرارداد موجود)
        self.assertEqual(r.status_code, 302)

    def test_manager_approve_finalizes_with_effect(self):
        v = self._submit_adjustment('+3')
        # خروج از حساب store و ورود مدیر
        self.client.login(username='manager_ui', password='testpass123')
        r = self.client.get(self._url('voucher_approve', voucher_id=v.id))
        self.assertEqual(r.status_code, 200)  # صفحهٔ تأیید
        r = self.client.post(self._url('voucher_approve', voucher_id=v.id))
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.status, 'finalized')
        self.assertEqual(v.approver, self.manager)
        self.assertIsNotNone(v.approved_at)
        self.assertIsNotNone(v.finalized_at)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('13'))  # 10 + 3
        tx = StockTransaction.objects.get(voucher=v)
        self.assertEqual(tx.transaction_type, 'adjustment')

    def test_approve_only_for_submitted_adjustment(self):
        """حوالهٔ نوع روتینِ نهایی‌شده را نمی‌توان تأیید کرد"""
        self._create_post('receipt', dest=self.wh_a, qty='1')
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        self.client.login(username='manager_ui', password='testpass123')
        r = self.client.post(self._url('voucher_approve', voucher_id=v.id))
        self.assertEqual(r.status_code, 302)  # خطا → بازگشت به جزئیات
        v.refresh_from_db()
        self.assertEqual(v.status, 'finalized')  # بدون تغییر


class DraftEditCancelTests(VoucherUIBaseline):
    """پیش‌نویس: ویرایش فقط توسط ایجادکننده (یا مدیر) + لغو پیش از نهایی‌سازی"""

    def _make_draft(self):
        r = self._create_post('receipt', dest=self.wh_a, qty='5', action='draft')
        self.assertEqual(r.status_code, 302)
        return WarehouseVoucher.objects.get(voucher_type='receipt')

    def test_draft_has_no_stock_effect(self):
        v = self._make_draft()
        self.assertEqual(v.status, 'draft')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        self.assertEqual(StockTransaction.objects.count(), 0)

    def test_edit_draft_updates_items_and_description(self):
        v = self._make_draft()
        r = self.client.post(self._url('voucher_edit', voucher_id=v.id), data={
            'dest_warehouse': str(self.wh_a.id),
            'product_id[]': [str(self.product.id)],
            'stock_item_id[]': [''],
            'unit_id[]': [str(self.unit.id)],
            'base_quantity[]': ['7'],
            'unit_price[]': ['2000'],
            'item_description[]': ['ویرایش‌شده'],
            'description': 'توضیح جدید',
        })
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.description, 'توضیح جدید')
        self.assertEqual(v.items.count(), 1)
        self.assertEqual(v.items.first().base_quantity, Decimal('7'))
        self.assertEqual(v.items.first().unit_price, Decimal('2000'))
        # هنوز بدون اثر
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))

    def test_edit_by_other_user_404(self):
        v = self._make_draft()
        other = User.objects.create_user(
            username='other_store', password='testpass123', role='store')
        other.cafe = self.cafe
        other.save()
        self.client.login(username='other_store', password='testpass123')
        r = self.client.get(self._url('voucher_edit', voucher_id=v.id))
        self.assertEqual(r.status_code, 404)

    def test_edit_finalized_not_allowed(self):
        self._create_post('receipt', dest=self.wh_a, qty='1')
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        r = self.client.get(self._url('voucher_edit', voucher_id=v.id))
        self.assertEqual(r.status_code, 302)  # خطا → بازگشت به جزئیات

    def test_cancel_draft(self):
        v = self._make_draft()
        r = self.client.post(self._url('voucher_cancel', voucher_id=v.id))
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.status, 'cancelled')
        self.assertIsNotNone(v.cancelled_at)

    def test_cancel_submitted_adjustment(self):
        self._create_post('adjustment', source=self.wh_a, qty='+3',
                          stock_item=self.stock_a)
        v = WarehouseVoucher.objects.get(voucher_type='adjustment')
        self.assertEqual(v.status, 'submitted')
        r = self.client.post(self._url('voucher_cancel', voucher_id=v.id))
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.status, 'cancelled')

    def test_cancel_finalized_not_allowed(self):
        self._create_post('receipt', dest=self.wh_a, qty='1')
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        r = self.client.post(self._url('voucher_cancel', voucher_id=v.id))
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.status, 'finalized')  # دست‌نخورده


class VoucherReversalTests(VoucherUIBaseline):
    """برگشت: فقط نهایی‌شده؛ دلیل اجباری؛ حوالهٔ آینه‌ای + وارون اثر"""

    def _make_finalized_receipt(self, qty='5'):
        self._create_post('receipt', dest=self.wh_a, qty=qty)
        return WarehouseVoucher.objects.get(voucher_type='receipt')

    def test_reverse_without_reason_rejected(self):
        v = self._make_finalized_receipt()
        r = self.client.post(self._url('voucher_reverse', voucher_id=v.id), data={})
        self.assertEqual(r.status_code, 200)  # فرم با خطای «دلیل الزامی است»
        self.assertEqual(WarehouseVoucher.objects.filter(reverses_id=v.id).count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('15'))  # دست‌نخورده

    def test_reverse_with_reason_inverts_effect(self):
        v = self._make_finalized_receipt('5')
        r = self.client.post(self._url('voucher_reverse', voucher_id=v.id),
                             data={'reason': 'اشتباه در ثبت'})
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.status, 'reversed')
        self.assertIsNotNone(v.reversed_at)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        self.assertEqual(mirror.status, 'finalized')
        self.assertEqual(mirror.voucher_type, 'receipt')
        self.assertTrue(mirror.voucher_number.endswith('-R'))
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))  # 15 − 5
        tx = StockTransaction.objects.get(voucher=mirror)
        self.assertEqual(tx.transaction_type, 'out')
        self.assertEqual(tx.quantity, Decimal('5'))

    def test_reverse_not_finalized_rejected(self):
        self._create_post('adjustment', source=self.wh_a, qty='+3',
                          stock_item=self.stock_a)
        v = WarehouseVoucher.objects.get(voucher_type='adjustment')
        r = self.client.post(self._url('voucher_reverse', voucher_id=v.id),
                             data={'reason': 'چرا'})
        self.assertEqual(r.status_code, 302)  # خطا → بازگشت به جزئیات
        self.assertEqual(WarehouseVoucher.objects.filter(reverses_id=v.id).count(), 0)

    def test_reverse_of_reversal_rejected(self):
        v = self._make_finalized_receipt('5')
        self.client.post(self._url('voucher_reverse', voucher_id=v.id),
                         data={'reason': 'اشتباه'})
        mirror = WarehouseVoucher.objects.get(reverses=v)
        r = self.client.post(self._url('voucher_reverse', voucher_id=mirror.id),
                             data={'reason': 'برگشت دوباره'})
        self.assertEqual(r.status_code, 302)  # خطا → بازگشت
        self.assertEqual(WarehouseVoucher.objects.filter(reverses_id=mirror.id).count(), 0)


class VoucherListFiltersTests(VoucherUIBaseline):
    """فیلترهای لیست حواله: نوع، وضعیت، محصول، جستجوی شماره"""

    def _seed(self):
        self._create_post('receipt', dest=self.wh_a, qty='1')
        self._create_post('issue', source=self.wh_a, qty='1',
                          stock_item=self.stock_a)
        v2 = WarehouseVoucher.objects.get(voucher_type='issue')
        return v2

    def test_list_shows_all(self):
        self._seed()
        r = self.client.get(self._url('voucher_list'))
        self.assertEqual(r.status_code, 200)
        for v in WarehouseVoucher.objects.all():
            self.assertContains(r, v.voucher_number)

    def test_filter_by_type(self):
        self._seed()
        # تخلیهٔ پیام‌های موفقیتِ ثبت (شمارهٔ حوالهٔ دیگر در بنرِ message ننشیند)
        self.client.get(self._url('voucher_list'))
        r = self.client.get(self._url('voucher_list'), {'type': 'receipt'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, WarehouseVoucher.objects.get(voucher_type='receipt').voucher_number)
        self.assertNotContains(r, WarehouseVoucher.objects.get(voucher_type='issue').voucher_number)

    def test_filter_by_search_number(self):
        v2 = self._seed()
        r = self.client.get(self._url('voucher_list'), {'search': v2.voucher_number[:6]})
        self.assertContains(r, v2.voucher_number)

    def test_filter_by_product(self):
        self._seed()
        r = self.client.get(self._url('voucher_list'), {'product': str(self.product.id)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseVoucher.objects.count(), 2)
        for v in WarehouseVoucher.objects.all():
            self.assertContains(r, v.voucher_number)

    def test_filter_by_status(self):
        self._seed()
        r = self.client.get(self._url('voucher_list'), {'status': 'draft'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'هیچ حواله‌ای یافت نشد')


class StockMovementsPageTests(VoucherUIBaseline):
    """صفحهٔ سوابق حرکت: همهٔ انواع با لینک حواله"""

    def test_movements_page_lists_all_with_voucher_links(self):
        self._create_post('receipt', dest=self.wh_a, qty='5')
        self._create_post('issue', source=self.wh_a, qty='2',
                          stock_item=self.stock_a)
        r = self.client.get(self._url('stock_movements'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(StockTransaction.objects.count(), 2)
        for v in WarehouseVoucher.objects.all():
            self.assertContains(r, v.voucher_number)

    def test_movements_filter_by_tx_type(self):
        self._create_post('receipt', dest=self.wh_a, qty='5')
        self._create_post('issue', source=self.wh_a, qty='2',
                          stock_item=self.stock_a)
        # تخلیهٔ پیام‌های موفقیتِ ثبت
        self.client.get(self._url('stock_movements'))
        r = self.client.get(self._url('stock_movements'), {'tx_type': 'out'})
        self.assertContains(r, 'خروج از انبار')
        self.assertNotContains(r, WarehouseVoucher.objects.get(voucher_type='receipt').voucher_number)


class VoucherDetailPageTests(VoucherUIBaseline):
    """صفحهٔ جزئیات: سند، اقلام، اثر، خط زمانی و اقداماتِ مجاز"""

    def test_detail_renders_document_and_items(self):
        self._create_post('receipt', dest=self.wh_a, qty='5')
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        r = self.client.get(self._url('voucher_detail', voucher_id=v.id))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, v.voucher_number)
        self.assertContains(r, 'نهایی‌سازی')
        self.assertContains(r, 'قلم')

    def test_detail_has_edit_and_cancel_actions_for_draft(self):
        self._create_post('receipt', dest=self.wh_a, qty='5', action='draft')
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        r = self.client.get(self._url('voucher_detail', voucher_id=v.id))
        self.assertContains(r, 'ویرایش پیش‌نویس')
        self.assertContains(r, 'لغو سند')

    def test_detail_has_reverse_action_for_finalized(self):
        self._create_post('receipt', dest=self.wh_a, qty='5')
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        r = self.client.get(self._url('voucher_detail', voucher_id=v.id))
        self.assertContains(r, 'برگشت حواله')


class NewEndpointsIDORTests(VoucherUIBaseline):
    """نقاط ورود جدید (ویرایش/لغو/تأیید/برگشت) با شناسهٔ کافهٔ دیگر → 404"""

    def setUp(self):
        super().setUp()
        self.cafe_b = Cafe.objects.create(name='B', code='b', is_active=True)
        self.wh_b = Warehouse.objects.create(
            cafe=self.cafe_b, code='whB2', name='انبار B', is_active=True)
        self.voucher_b = WarehouseVoucher.objects.create(
            cafe=self.cafe_b, voucher_type='receipt', status='submitted',
            voucher_number='RCV-FOREIGN', destination_warehouse=self.wh_b)

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'main', **kwargs})

    def test_edit_foreign_voucher_404(self):
        r = self.client.get(self._url('voucher_edit', voucher_id=self.voucher_b.id))
        self.assertEqual(r.status_code, 404)

    def test_cancel_foreign_voucher_404(self):
        r = self.client.post(self._url('voucher_cancel', voucher_id=self.voucher_b.id))
        self.assertEqual(r.status_code, 404)

    def test_approve_foreign_voucher_404(self):
        # مدیر (با نقش مجاز) تا به خود view برسد؛ حوالهٔ کافهٔ دیگر → 404
        self.client.login(username='manager_ui', password='testpass123')
        r = self.client.post(self._url('voucher_approve', voucher_id=self.voucher_b.id))
        self.assertEqual(r.status_code, 404)

    def test_reverse_foreign_voucher_404(self):
        r = self.client.post(self._url('voucher_reverse', voucher_id=self.voucher_b.id),
                             data={'reason': 'x'})
        self.assertEqual(r.status_code, 404)

    def test_movements_scoped_to_cafe(self):
        """صفحهٔ سوابق حرکت فقط تراکنش‌های کافهٔ خود را نشان می‌دهد"""
        StockTransaction.objects.create(
            stock_item=self.stock_a, transaction_type='in', quantity=Decimal('1'),
            previous_quantity=Decimal('10'), new_quantity=Decimal('11'),
            reference_type='warehouse_voucher')
        r = self.client.get(self._url('stock_movements'))
        self.assertContains(r, self.product.name)
        self.assertNotContains(r, 'RCV-FOREIGN')


class WarehouseOfficerDashboardTests(TestCase):
    """داشبورد افسر انبار

    بخش‌های حواله‌ها / هشدار کمبود / موجودی انبارها از داشبورد حذف شده‌اند
    (۲۰۲۶-۰۹-۰۵ به درخواست کاربر) — این تست‌ها فقط رندر صفحه را چک می‌کنند.
    رگرسیون تاریخی: فیلتر هشدار قبلاً با مقایسهٔ مستقیم دو F نوشته شده بود
    (F('quantity') <= F('product__min_stock')) که TypeError می‌داد —
    حالا quantity__lte=F(...) است.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='officer_ui', password='testpass123', role='warehouse')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.user.cafe = self.cafe
        self.user.save()
        _set_cafe_session(self.client, self.cafe)
        self.client.login(username='officer_ui', password='testpass123')
        self.url = reverse('warehouse_officer_dashboard',
                           kwargs={'cafe_code': 'main'})

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.wh = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=5)
        self.stock = StockItem.objects.create(
            product=self.product, warehouse=self.wh, quantity=3, unit=self.unit)

    def test_dashboard_renders(self):
        """رندر 200 — صفحه اصلی داشبورد بدون بخش‌های حذف‌شده

        GET بدون day به ‎?day=<امروز> ریدایرکت می‌شود؛ با follow دنبال می‌کنیم.
        """
        r = self.client.get(self.url, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('stock_alerts', r.context)
        self.assertNotIn('voucher_type_shortcuts', r.context)
        self.assertNotIn('stock_overview', r.context)


class WarehouseRoleAccessTests(TestCase):
    """دسترسی نقش «warehouse» (مسئول انبار) به صفحات موجودی و حواله

    رگرسیون: نقش مسئول انبار در هیچ نقطهٔ ورود جدید (موجودی انبار،
    انبارگردانی، حواله‌ها، سوابق حرکت) نبود — همهٔ لینک‌های داشبورد به
    لاگین برمی‌گشت. حالا این نقش به این صفحات اضافه شده؛ تأیید حواله
    همچنان فقط مدیر/ادمین/مدیرعامل است.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='officer_acc', password='testpass123', role='warehouse')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        self.user.cafe = self.cafe
        self.user.save()
        _set_cafe_session(self.client, self.cafe)
        self.client.login(username='officer_acc', password='testpass123')

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg', cafe=self.cafe)
        self.wh = Warehouse.objects.create(
            cafe=self.cafe, code='wh1', name='انبار اصلی', is_active=True)
        self.product = Product.objects.create(
            name='Sugar', unit=self.unit, cafe=self.cafe, min_stock=5)
        self.stock = StockItem.objects.create(
            product=self.product, warehouse=self.wh, quantity=10, unit=self.unit)
        self.voucher = WarehouseVoucher.objects.create(
            cafe=self.cafe, voucher_type='issue', status='submitted',
            voucher_number='ISS-00001', source_warehouse=self.wh,
            created_by=self.user)

    def _url(self, name, **kwargs):
        return reverse(name, kwargs={'cafe_code': 'main', **kwargs})

    def test_officer_can_open_inventory_pages(self):
        """صفحهٔ موجودی انبار + مدیریت انبارها + انبارگردانی در دسترس"""
        r = self.client.get(self._url('warehouse_management'))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(self._url('warehouse_inventory_check',
                                      warehouse_id=self.wh.id))
        self.assertEqual(r.status_code, 200)
        # صفحهٔ موجودی انبار و سوابق موجودی — درخواست کاربر (دسترسی بازدید)
        r = self.client.get(self._url('stock_list'))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(self._url('stock_transactions', stock_id=self.stock.id))
        self.assertEqual(r.status_code, 200)

    def test_officer_menu_no_old_warehouse_dashboard(self):
        """گزینهٔ «داشبورد انبار» از منوی مسئول انبار حذف شده و
        «موجودی انبار» همچنان در منو هست (رگرسیون منوی کناری)"""
        r = self.client.get(self._url('stock_list'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self._url('stock_list'))
        self.assertNotContains(
            r, reverse('warehouse_dashboard', kwargs={'cafe_code': 'main'}))

    def test_officer_can_open_voucher_pages(self):
        """صفحه‌های حواله (ایجاد/لیست/جزئیات/سوابق حرکت) در دسترس"""
        r = self.client.get(self._url('voucher_create', voucher_type='issue'))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(self._url('voucher_list'))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(self._url('stock_movements'))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(self._url('voucher_detail', voucher_id=self.voucher.id))
        self.assertEqual(r.status_code, 200)

    def test_officer_cannot_approve_voucher(self):
        """تأیید حواله همچنان فقط مدیر/ادمین/مدیرعامل — رد نقش → لاگین"""
        r = self.client.get(self._url('voucher_approve', voucher_id=self.voucher.id))
        self.assertEqual(r.status_code, 302)


class ReceiptStockCreationTests(VoucherUIBaseline):
    """حوالهٔ ورود همهٔ محصولات را می‌پذیرد — اگر موجودیِ انباری ثبت نشده،
    هنگام نهایی‌سازی خودکار ساخته می‌شود (فقط سمتِ «ورود»: رسید و برگشتی)"""

    def _post(self, voucher_type, source=None, dest=None, product=None, qty='5'):
        data = {
            'action': 'submit',
            'product_id[]': [str((product or self.product).id)],
            'stock_item_id[]': [''],
            'unit_id[]': [str(self.unit.id)],
            'base_quantity[]': [qty],
            'unit_price[]': ['0'],
            'item_description[]': ['ورود کالای تازه'],
        }
        if source:
            data['source_warehouse'] = str(source.id)
        if dest:
            data['dest_warehouse'] = str(dest.id)
        return self.client.post(
            self._url('voucher_create', voucher_type=voucher_type), data=data)

    def test_receipt_new_product_creates_stock_item(self):
        """رسیدِ محصولِ بدونِ موجودیِ قبلی → موجودیِ انبار ساخته و پر می‌شود"""
        tea = Product.objects.create(name='Tea', unit=self.unit, cafe=self.cafe)
        self.assertEqual(StockItem.objects.filter(product=tea).count(), 0)
        r = self._post('receipt', dest=self.wh_b, product=tea, qty='5')
        self.assertEqual(r.status_code, 302)
        si = StockItem.objects.get(product=tea, warehouse=self.wh_b)
        self.assertEqual(si.quantity, Decimal('5'))
        v = WarehouseVoucher.objects.get(voucher_type='receipt')
        self.assertEqual(v.status, 'finalized')
        tx = StockTransaction.objects.get(voucher=v)
        self.assertEqual(tx.transaction_type, 'in')
        self.assertEqual(tx.previous_quantity, Decimal('0'))
        self.assertEqual(tx.new_quantity, Decimal('5'))

    def test_return_new_product_creates_stock_item(self):
        """برگشتی هم سمتِ ورود است — موجودیِ نبوده در انبارِ مبدأ را می‌سازد"""
        tea = Product.objects.create(name='Tea', unit=self.unit, cafe=self.cafe)
        r = self._post('return', source=self.wh_a, product=tea, qty='4')
        self.assertEqual(r.status_code, 302)
        si = StockItem.objects.get(product=tea, warehouse=self.wh_a)
        self.assertEqual(si.quantity, Decimal('4'))
        v = WarehouseVoucher.objects.get(voucher_type='return')
        self.assertEqual(v.status, 'finalized')

    def test_receipt_existing_stock_used_not_duplicated(self):
        """رسیدِ محصولِ دارایِ موجودی → همان موجودی پر می‌شود، موردِ تازه ساخته نمی‌شود"""
        before = StockItem.objects.filter(product=self.product).count()
        r = self._post('receipt', dest=self.wh_b, qty='5')  # stock_b موجود است (صفر)
        self.assertEqual(r.status_code, 302)
        self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_b.quantity, Decimal('5'))
        self.assertEqual(StockItem.objects.filter(product=self.product).count(), before)

    def test_transfer_missing_dest_stock_still_rejected(self):
        """انتقال به انبارِ بدونِ موجودیِ محصول → خطای اعتبارسنجی، هیچ ساخته نمی‌شود"""
        salt = Product.objects.create(name='Salt', unit=self.unit, cafe=self.cafe)
        StockItem.objects.create(product=salt, warehouse=self.wh_a,
                                 quantity=10, unit=self.unit)
        before = WarehouseVoucher.objects.count()
        r = self.client.post(self._url('voucher_create', voucher_type='transfer'), data={
            'action': 'submit',
            'source_warehouse': str(self.wh_a.id),
            'dest_warehouse': str(self.wh_b.id),
            'product_id[]': [str(salt.id)],
            'stock_item_id[]': [''],
            'unit_id[]': [str(self.unit.id)],
            'base_quantity[]': ['3'],
            'unit_price[]': ['0'],
            'item_description[]': ['انتقال'],
        })
        self.assertEqual(r.status_code, 200)  # خطا → رندر دوبارهٔ فرم
        self.assertEqual(StockItem.objects.filter(product=salt,
                                                  warehouse=self.wh_b).count(), 0)
        self.assertEqual(WarehouseVoucher.objects.count(), before)


# ─────────────────────────────────────────────────────────────────────────────
# حوالهٔ «تحویل مستقیم» — ارتقا از test_scratch_direct_delivery.py (2026-09-11)
#
# کالای تازه‌خرید بدون ورودِ ماندگار به انبار: از انبارِ انتخابی (محل عبور)
# می‌گذرد — ورود کامل و بلافاصله خروج کامل به بخشِ گیرنده. خالصِ صفر روی انبار،
# ولی هر دو حرکت در سابقه ثبت می‌شود.
# ─────────────────────────────────────────────────────────────────────────────
class DirectDeliveryCreateTests(VoucherUIBaseline):
    """ایجاد حوالهٔ تحویل مستقیم"""

    def test_create_finalizes_with_pass_through_net_zero(self):
        r = self._create_post('direct_delivery', dest=self.wh_a, qty='5',
                              stock_item=self.stock_a, department=self.dept)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        self.assertEqual(v.status, 'finalized')
        self.assertTrue(v.voucher_number.startswith('DLV-'))
        self.assertEqual(v.department_id, self.dept.id)
        self.assertEqual(v.destination_warehouse_id, self.wh_a.id)
        self.assertIsNone(v.source_warehouse_id)
        # موجودیِ خالص تغییر نمی‌کند؛ هر دو حرکت ثبت می‌شود
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        txs = list(StockTransaction.objects.filter(voucher=v).order_by('id'))
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0].transaction_type, 'in')
        self.assertEqual(txs[0].quantity, Decimal('5'))
        self.assertEqual(txs[0].previous_quantity, Decimal('10'))
        self.assertEqual(txs[0].new_quantity, Decimal('15'))
        self.assertEqual(txs[1].transaction_type, 'out')
        self.assertEqual(txs[1].quantity, Decimal('5'))
        self.assertEqual(txs[1].previous_quantity, Decimal('15'))
        self.assertEqual(txs[1].new_quantity, Decimal('10'))

    def test_create_on_other_warehouse_untouched(self):
        r = self._create_post('direct_delivery', dest=self.wh_b, qty='2',
                              stock_item=self.stock_b, department=self.dept)
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        self.assertEqual(v.destination_warehouse_id, self.wh_b.id)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))  # انبار دیگر دست‌نخورده

    def test_fresh_product_gets_zero_stock_row_after_pass(self):
        fresh = Product.objects.create(
            name='Flour', unit=self.unit, cafe=self.cafe, min_stock=0)
        r = self._create_post('direct_delivery', dest=self.wh_a, qty='4',
                              product=fresh, department=self.dept)
        self.assertEqual(r.status_code, 302)
        si = StockItem.objects.get(product=fresh, warehouse=self.wh_a)
        self.assertEqual(si.quantity, Decimal('0'))  # ساخته شد و خالصِ صفر ماند
        txs = StockTransaction.objects.filter(voucher__voucher_type='direct_delivery')
        self.assertEqual(txs.count(), 2)

    def test_missing_department_rejected(self):
        r = self._create_post('direct_delivery', dest=self.wh_a, qty='5',
                              stock_item=self.stock_a, department=None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)
        self.assertContains(r, 'بخش گیرنده را انتخاب کنید')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        self.assertEqual(StockTransaction.objects.count(), 0)

    def test_draft_action_leaves_no_movement(self):
        r = self._create_post('direct_delivery', dest=self.wh_a, qty='5',
                              stock_item=self.stock_a, department=self.dept,
                              action='draft')
        self.assertEqual(r.status_code, 302)
        v = WarehouseVoucher.objects.get(voucher_type='direct_delivery')
        self.assertEqual(v.status, 'draft')
        self.assertEqual(StockTransaction.objects.count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))

    def test_create_page_shows_department_field_only_for_this_type(self):
        r = self.client.get(self._url('voucher_create',
                                      voucher_type='direct_delivery'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'بخش گیرنده')
        # فرمِ رسید نباید فیلد بخش را نشان دهد
        r2 = self.client.get(self._url('voucher_create', voucher_type='receipt'))
        self.assertEqual(r2.status_code, 200)
        self.assertNotContains(r2, 'بخش گیرنده')

    def test_create_without_destination_warehouse_rejected(self):
        """انبارِ محل عبور الزامی است — بدون آن سند ساخته نمی‌شود"""
        r = self._create_post('direct_delivery', dest=None, qty='5',
                              stock_item=self.stock_a, department=self.dept)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseVoucher.objects.count(), 0)


class DirectDeliveryListAndReverseTests(VoucherUIBaseline):
    """نمایش و برگشتِ حوالهٔ تحویل مستقیم"""

    def _created(self, qty='3'):
        r = self._create_post('direct_delivery', dest=self.wh_a, qty=qty,
                              stock_item=self.stock_a, department=self.dept)
        self.assertEqual(r.status_code, 302)
        return WarehouseVoucher.objects.get(voucher_type='direct_delivery')

    def test_list_shows_department_and_department_filter_finds_it(self):
        v = self._created()
        page = self.client.get(self._url('voucher_list'))
        self.assertContains(page, v.voucher_number)
        self.assertContains(page, f'تحویل به: {self.dept.name}')
        filtered = self.client.get(
            self._url('voucher_list'), {'department': str(self.dept.id)})
        self.assertContains(filtered, v.voucher_number)

    def test_detail_shows_delivered_department(self):
        v = self._created()
        page = self.client.get(self._url('voucher_detail', voucher_id=v.id))
        self.assertContains(page, self.dept.name)
        self.assertContains(page, 'کالا تحویل داده شد')

    def test_reverse_mirror_keeps_department_and_net_zero(self):
        v = self._created(qty='5')
        r = self.client.post(
            self._url('voucher_reverse', voucher_id=v.id),
            data={'reason': 'ثبت اشتباه'})
        self.assertEqual(r.status_code, 302)
        mirror = WarehouseVoucher.objects.get(reverses=v)
        self.assertEqual(mirror.voucher_type, 'direct_delivery')
        self.assertTrue(mirror.voucher_number.endswith('-R'))
        self.assertEqual(mirror.department_id, self.dept.id)
        self.assertEqual(mirror.destination_warehouse_id, self.wh_a.id)
        self.assertEqual(mirror.status, 'finalized')
        v.refresh_from_db()
        self.assertEqual(v.status, 'reversed')
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity, Decimal('10'))
        # هر دو سند دو حرکت داشته‌اند — چهار ردیف سابقه، موجودی همان ۱۰
        txs = list(StockTransaction.objects.filter(voucher=mirror).order_by('id'))
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0].transaction_type, 'in')
        self.assertEqual(txs[0].new_quantity, Decimal('15'))
        self.assertEqual(txs[1].transaction_type, 'out')
        self.assertEqual(txs[1].new_quantity, Decimal('10'))
