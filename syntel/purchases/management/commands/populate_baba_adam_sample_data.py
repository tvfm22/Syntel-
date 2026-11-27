"""Populate comprehensive sample data for cafe 'baba_adam' (کافه رستوران بابا آدم).

Covers:
1. Users across all sections (departments) and operational/management roles.
2. Suppliers.
3. Warehouses stocked via finalized receipt vouchers (StockItem + StockTransaction).
4. All 8 WarehouseVoucher types (receipt, issue, return, sale, waste, transfer,
   adjustment, direct_delivery), including draft, submitted (awaiting approval),
   finalized, cancelled, and a reversed pair via reverse_voucher().
5. PurchaseRequests across sections (today + recent days) with OfficerAssignments:
   - pending in warehouse dashboard (enough stock, short stock, out of stock)
   - fulfilled from stock (both unconfirmed and confirmed by requester in my_received_items)
   - referred to purchase draft (returned -> PurchaseDraftItem)
   - not_in_stock
   - pending_manager_approval / rejected / partial / purchased
6. Warehouse PurchaseDraft (referred + manual items, normal + CEO-required) and
   sent PurchaseRequests (source='warehouse_purchase') at all stages, plus
   purchasing queue & history and PurchaseOrders.
7. WarehouseRequests (درخواست از انبار) in pending, fulfilled, partial, declined states.
"""
from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from purchases.models import (
    ActivityLog,
    Cafe,
    Department,
    Notification,
    OfficerAssignment,
    Product,
    PurchaseDraft,
    PurchaseDraftItem,
    PurchaseItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequest,
    StockItem,
    StockTransaction,
    Supplier,
    User,
    Warehouse,
    WarehouseRequest,
    WarehouseRequestItem,
    WarehouseVoucher,
    WarehouseVoucherItem,
)
from purchases.services.inventory import (
    apply_stock_delta,
    finalize_voucher,
    next_voucher_number,
    reverse_voucher,
    save_voucher_with_number,
)

SEED_TAG = '[داده نمونه]'


class Command(BaseCommand):
    help = 'Populate realistic sample data for warehouses, section users, requests, approvals, deliveries, purchase drafts, and vouchers.'

    def add_arguments(self, parser):
        parser.add_argument('--cafe', default='baba_adam', help='Target cafe code (default: baba_adam)')
        parser.add_argument('--password', help='Password for newly created sample users (or set SYNTEL_SEED_PASSWORD)')
        parser.add_argument('--force', action='store_true', help='Allow running when DEBUG is False')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError('Refusing to run when DEBUG=False without --force.')

        password = options.get('password') or os.environ.get('SYNTEL_SEED_PASSWORD')
        if not password or len(password) < 8:
            raise CommandError('Provide a password >= 8 chars via --password or SYNTEL_SEED_PASSWORD.')

        cafe = Cafe.objects.filter(code=options['cafe'], is_active=True).first()
        if cafe is None:
            raise CommandError(f'Active cafe "{options["cafe"]}" not found.')

        if WarehouseVoucher.objects.filter(cafe=cafe, description__contains=SEED_TAG).exists():
            self.stdout.write(self.style.WARNING(
                f'Sample data tagged "{SEED_TAG}" already exists for cafe "{cafe.code}". Skipping duplicate seed.'
            ))
            return

        now = timezone.now()
        today = timezone.localdate()

        with transaction.atomic():
            # ------------------------------------------------------------------
            # 1. Departments & Users across all sections and operational roles
            # ------------------------------------------------------------------
            dept_names = [
                'آماده سازی', 'ایرانی', 'بار', 'خدمات', 'سالاد و پیش غذا',
                'سالن', 'صبحانه', 'فرنگی', 'قلیون بار', 'مالی و صندوق', 'کانتر',
            ]
            depts = {}
            for dname in dept_names:
                dept, _ = Department.objects.get_or_create(
                    cafe=cafe, name=dname, defaults={'is_active': True}
                )
                depts[dname] = dept

            users_spec = [
                # Section requesters (amirhosein already exists in 'بار')
                ('baba_prep', 'requester', 'رضا', 'رضایی', depts['آماده سازی']),
                ('baba_irani', 'requester', 'حسین', 'محمدی', depts['ایرانی']),
                ('baba_salad', 'requester', 'سارا', 'کریمی', depts['سالاد و پیش غذا']),
                ('baba_breakfast', 'requester', 'مریم', 'احمدی', depts['صبحانه']),
                ('baba_farangi', 'requester', 'علی', 'حسینی', depts['فرنگی']),
                ('baba_counter', 'requester', 'نگار', 'موسوی', depts['کانتر']),
                ('baba_hall', 'requester', 'محمد', 'جوادی', depts['سالن']),
                ('baba_hookah', 'requester', 'مهدی', 'صادقی', depts['قلیون بار']),
                ('baba_services', 'requester', 'زهرا', 'نوری', depts['خدمات']),
                ('baba_finance_sec', 'requester', 'فاطمه', 'طاهری', depts['مالی و صندوق']),
                # Operational & management roles (ehsan already exists as warehouse)
                ('baba_purchasing', 'purchasing', 'سعید', 'شریفی', None),
                ('baba_purchasing2', 'purchasing', 'یاسر', 'کاظمی', None),
                ('baba_ceo', 'ceo', 'کامران', 'یوسفی', None),
                ('baba_manager', 'manager', 'نیما', 'اکبری', None),
                ('baba_accounting', 'accounting', 'الهام', 'فرهادی', None),
                ('baba_finance_mgr', 'finance_manager', 'بهرام', 'رحیمی', None),
            ]

            users = {}
            created_users = []
            for uname, role, fname, lname, dept in users_spec:
                user, created = User.objects.get_or_create(
                    username=uname,
                    defaults={
                        'role': role,
                        'first_name': fname,
                        'last_name': lname,
                        'cafe': cafe,
                        'department': dept,
                        'is_active': True,
                    },
                )
                if created:
                    user.set_password(password)
                    user.save(update_fields=['password'])
                    created_users.append(uname)
                users[uname] = user

            # Existing users in baba_adam
            amirhosein = User.objects.filter(username='amirhosein', cafe=cafe).first()
            if amirhosein is None:
                amirhosein = User.objects.create_user(
                    username='amirhosein', password=password, role='requester',
                    first_name='امیرحسین', last_name='عبدی', cafe=cafe, department=depts['بار'],
                )
            ehsan = User.objects.filter(username='ehsan', cafe=cafe).first()
            if ehsan is None:
                ehsan = User.objects.create_user(
                    username='ehsan', password=password, role='warehouse',
                    first_name='احسان', last_name='انباردار', cafe=cafe,
                )
            users['bar'] = amirhosein
            users['warehouse'] = ehsan

            # ------------------------------------------------------------------
            # 2. Suppliers
            # ------------------------------------------------------------------
            suppliers_spec = [
                ('پخش مواد غذایی بهار', 'company', 'محمدرضا بهرامی', '02188123401', '09121112201', 'تهران، میدان بهارستان'),
                ('مجتمع پروتئین و مرغ پارس', 'company', 'حامد پارسا', '02166234502', '09121112202', 'تهران، شهرک صنعتی چهاردانگه'),
                ('لبنیات و نوشیدنی مهر', 'company', 'پویا مهری', '02144345603', '09121112203', 'تهران، جاده مخصوص'),
                ('ملزومات و شوینده آریا', 'company', 'کیوان آریایی', '02177456704', '09121112204', 'تهران، خیابان شریعتی'),
            ]
            suppliers = {}
            for name, stype, contact, phone, mobile, addr in suppliers_spec:
                sup, _ = Supplier.objects.get_or_create(
                    cafe=cafe,
                    name=name,
                    defaults={
                        'supplier_type': stype,
                        'status': 'active',
                        'contact_person': contact,
                        'phone': phone,
                        'mobile': mobile,
                        'address': addr,
                        'city': 'تهران',
                        'payment_terms': 'تسویه ۱۵ روزه',
                        'created_by': users['baba_purchasing'],
                    },
                )
                suppliers[name] = sup

            # ------------------------------------------------------------------
            # 3. Warehouses & Products lookup
            # ------------------------------------------------------------------
            wh_central = Warehouse.objects.get(cafe=cafe, code='11001')   # انبار مرکزی
            wh_pos = Warehouse.objects.get(cafe=cafe, code='11002')       # سردخانه مثبت صفر
            wh_neg = Warehouse.objects.get(cafe=cafe, code='11003')       # سردخانه منفی صفر

            def prod(name: str) -> Product:
                p = Product.objects.filter(cafe=cafe, name=name, is_active=True).select_related('unit').first()
                if p is None:
                    raise CommandError(f'Expected product "{name}" not found in cafe "{cafe.code}".')
                if p.unit is None:
                    raise CommandError(f'Product "{name}" has no unit.')
                return p

            # ------------------------------------------------------------------
            # 4. Initial stock via finalized receipt vouchers (حواله ورود)
            # ------------------------------------------------------------------
            def make_voucher(vtype, *, src=None, dst=None, dept=None, lines, status='submitted',
                             created_by=ehsan, approver=None, requires_approval=False,
                             dt=None, desc=''):
                v = WarehouseVoucher(
                    cafe=cafe,
                    voucher_type=vtype,
                    status='draft' if status == 'draft' else ('approved' if status == 'approved' else 'submitted'),
                    voucher_number=next_voucher_number(cafe.id, vtype),
                    source_warehouse=src,
                    destination_warehouse=dst,
                    department=dept,
                    date=dt or now,
                    description=f'{desc} {SEED_TAG}'.strip(),
                    created_by=created_by,
                    requires_approval=requires_approval,
                )
                if approver is not None:
                    v.approver = approver
                    v.approved_at = (dt or now) + timedelta(minutes=15)
                save_voucher_with_number(v, cafe.id, vtype)
                for p, qty, price, line_desc in lines:
                    si = None
                    if vtype in ('issue', 'sale', 'waste', 'transfer', 'adjustment', 'return'):
                        target_wh = src
                        si = StockItem.objects.filter(warehouse=target_wh, product=p).first()
                    WarehouseVoucherItem.objects.create(
                        voucher=v,
                        product=p,
                        stock_item=si,
                        unit=p.unit,
                        base_quantity=Decimal(str(qty)),
                        unit_price=Decimal(str(price)),
                        description=line_desc,
                    )
                if status in ('finalized', 'approved'):
                    finalize_voucher(v, finalized_by=approver or created_by)
                elif status == 'cancelled':
                    v.status = 'cancelled'
                    v.cancelled_at = now
                    v.save(update_fields=['status', 'cancelled_at'])
                return v

            # Receipt 1: انبار مرکزی (خشکبار، نوشیدنی خشک، ادویه، مصرفی، شوینده، اداری، قلیون)
            make_voucher(
                'receipt',
                dst=wh_central,
                status='finalized',
                dt=now - timedelta(days=4),
                desc='ورود موجودی اولیه اقلام خشک، نوشیدنی و ملزومات به انبار مرکزی',
                lines=[
                    (prod('آب معدنی'), 24, 95000, '۲ باکس ۱۲ عددی'),
                    (prod('آب گاز دار سودا 250 میل'), 60, 187500, 'تامین نوشیدنی بار'),
                    (prod('آب گاز دار (سودا ) یک لیتری'), 30, 345500, 'سودا یک لیتری بار'),
                    (prod('آبلیمو صنعتی'), 25, 850000, 'بسته‌بندی ۵ لیتری'),
                    (prod('آرد خبازی 21 درصد'), 50, 380000, 'کیسه ۲۵ کیلویی'),
                    (prod('آرد سمولینا'), 12, 620000, 'آرد پاستا و صبحانه'),
                    (prod('ادویه آرومات'), 3000, 1850, 'بسته‌های ۱ کیلویی'),
                    (prod('ادویه کاری'), 2500, 20000, 'ادویه ایرانی'),
                    (prod('ارده'), 6000, 6200, 'دبه ۳ کیلویی'),
                    (prod('اسپاگتی'), 4000, 3000, 'پاستا فرنگی'),
                    (prod('اسپری چند منظوره'), 15, 920000, 'گالن ۵ لیتری'),
                    (prod('اسپری شیشه شور'), 12, 780000, 'مخصوص سالن و کانتر'),
                    (prod('ابر'), 40, 280000, 'بسته خدمات'),
                    (prod('جعبه پیتزا'), 150, 145000, 'سایز بزرگ'),
                    (prod('تنباکو دوسیب'), 2000, 9500, 'مصرفی قلیون بار'),
                    (prod('تنباکو بلوبری'), 1500, 9800, 'مصرفی قلیون بار'),
                    (prod('برگه A4'), 10, 1850000, 'بسته ۵۰۰ برگی'),
                    (prod('خودکار'), 30, 120000, 'آبی و مشکی'),
                ],
            )

            # Receipt 2: سردخانه مثبت صفر (لبنیات و میوه و صیفی‌جات تازه)
            make_voucher(
                'receipt',
                dst=wh_pos,
                status='finalized',
                dt=now - timedelta(days=3),
                desc='ورود لبنیات و صیفی‌جات تازه به سردخانه مثبت صفر',
                lines=[
                    (prod('خیار بوته ای'), 25, 420000, 'صیفی‌جات تازه'),
                    (prod('اسفناج'), 18, 360000, 'سبزی تازه صبحانه و سالاد'),
                    (prod('تخم مرغ'), 180, 125000, '۶ شانه ۳۰ عددی'),
                    (prod('خامه صبحانه'), 20, 1950000, 'پاکت ۱ کیلویی'),
                    (prod('خامه حیوانی'), 15, 2400000, 'خامه قنادی و بار'),
                    (prod('آبلیمو صنعتی'), 5, 850000, 'آبلیمو برای بخش سالاد و سردخانه'),
                ],
            )

            # Receipt 3: سردخانه منفی صفر (پروتئین و محصولات منجمد)
            make_voucher(
                'receipt',
                dst=wh_neg,
                status='finalized',
                dt=now - timedelta(days=3),
                desc='ورود اقلام پروتئینی و منجمد به سردخانه منفی صفر',
                lines=[
                    (prod('اکبر جوجه'), 35, 2850000, 'مرغ سایز استاندارد'),
                    (prod('بیکن گوشت'), 12, 6400000, 'وکیوم ۱ کیلویی'),
                    (prod('خرده بره'), 20, 7200000, 'بسته‌بندی ۲ کیلویی'),
                    (prod('خرده گوساله'), 25, 6800000, 'بسته‌بندی ۲ کیلویی'),
                    (prod('آلبالو منجمد'), 16, 1650000, 'آلبالو بدون هسته منجمد'),
                ],
            )

            # Set realistic min_quantity values so low-stock items appear cleanly
            low_stock_thresholds = {
                ('آب معدنی', wh_central.id): Decimal('30'),          # qty 24 <= 30 (کمبود)
                ('اسفناج', wh_pos.id): Decimal('20'),                # qty 18 <= 20 (کمبود)
                ('بیکن گوشت', wh_neg.id): Decimal('15'),             # qty 12 <= 15 (کمبود)
                ('خیار بوته ای', wh_pos.id): Decimal('10'),
                ('آرد خبازی 21 درصد', wh_central.id): Decimal('15'),
                ('تخم مرغ', wh_pos.id): Decimal('60'),
                ('اکبر جوجه', wh_neg.id): Decimal('10'),
                ('تنباکو دوسیب', wh_central.id): Decimal('500'),
                ('برگه A4', wh_central.id): Decimal('4'),
            }
            for (pname, wh_id), min_q in low_stock_thresholds.items():
                StockItem.objects.filter(warehouse_id=wh_id, product__name=pname).update(min_quantity=min_q)

            # ------------------------------------------------------------------
            # 5. All other WarehouseVoucher types & statuses
            # ------------------------------------------------------------------
            # 5a. Transfer (حواله انتقال: انبار مرکزی -> سردخانه مثبت صفر)
            make_voucher(
                'transfer',
                src=wh_central,
                dst=wh_pos,
                status='finalized',
                dt=now - timedelta(days=2),
                desc='انتقال آبلیمو صنعتی از انبار مرکزی به سردخانه مثبت صفر',
                lines=[
                    (prod('آبلیمو صنعتی'), 5, 850000, 'انتقال جهت مصرف سردخانه'),
                ],
            )

            # 5b. Issue (حواله خروج) — 1 finalized + 1 to be reversed
            make_voucher(
                'issue',
                src=wh_neg,
                status='finalized',
                dt=now - timedelta(days=2),
                desc='خروج اقلام پروتئینی برای آماده‌سازی شیفت شام',
                lines=[
                    (prod('خرده گوساله'), 4, 6800000, 'تحویل شیفت عصر'),
                    (prod('اکبر جوجه'), 5, 2850000, 'تحویل آشپزخانه ایرانی'),
                ],
            )
            issue_to_reverse = make_voucher(
                'issue',
                src=wh_central,
                status='finalized',
                dt=now - timedelta(days=1, hours=5),
                desc='خروج اشتباه آرد سمولینا (جهت نمایش حواله برگشت‌خورده)',
                lines=[
                    (prod('آرد سمولینا'), 3, 620000, 'ثبت اشتباه انبار'),
                ],
            )
            reverse_voucher(
                issue_to_reverse,
                created_by=ehsan,
                description=f'اصلاح خطای ثبت خروج {SEED_TAG}',
            )

            # 5c. Direct delivery (حواله تحویل مستقیم به بخش‌ها)
            make_voucher(
                'direct_delivery',
                dst=wh_pos,
                dept=depts['فرنگی'],
                status='finalized',
                dt=now - timedelta(days=1, hours=3),
                desc='تحویل مستقیم پنیر و سبزی تازه خریداری‌شده به بخش فرنگی',
                lines=[
                    (prod('اسفناج'), 4, 360000, 'خرید روز تره‌بار'),
                ],
            )
            make_voucher(
                'direct_delivery',
                dst=wh_central,
                dept=depts['بار'],
                status='finalized',
                dt=now - timedelta(hours=6),
                desc='تحویل مستقیم سودا به بار',
                lines=[
                    (prod('آب گاز دار سودا 250 میل'), 12, 187500, 'تحویل فوری شیفت'),
                ],
            )

            # 5d. Return (حواله برگشتی به انبار)
            make_voucher(
                'return',
                src=wh_central,
                status='finalized',
                dt=now - timedelta(days=1),
                desc='برگشت اقلام مصرف‌نشده از مراسم به انبار مرکزی',
                lines=[
                    (prod('جعبه پیتزا'), 20, 145000, 'اقلام سالم برگشتی'),
                ],
            )

            # 5e. Sale (حواله فروش)
            make_voucher(
                'sale',
                src=wh_central,
                status='finalized',
                dt=now - timedelta(days=1),
                desc='فروش مستقیم نوشیدنی به پرسنل / بیرون‌بر',
                lines=[
                    (prod('آب گاز دار (سودا ) یک لیتری'), 4, 345500, 'فروش خارج از منو'),
                ],
            )

            # 5f. Waste (حواله ضایعات): 1 finalized + 1 submitted awaiting manager approval
            make_voucher(
                'waste',
                src=wh_pos,
                status='finalized',
                dt=now - timedelta(days=1, hours=2),
                desc='ثبت ضایعات خیار بوته‌ای خراب‌شده در سردخانه',
                lines=[
                    (prod('خیار بوته ای'), 2, 420000, 'ضایعات نگهداری'),
                ],
            )
            make_voucher(
                'waste',
                src=wh_neg,
                status='submitted',
                requires_approval=True,
                dt=now - timedelta(hours=2),
                desc='ضایعات بیکن منقضی‌شده (نیازمند تایید مدیر)',
                lines=[
                    (prod('بیکن گوشت'), 2, 6400000, 'ضایعات بالای آستانه — منتظر تایید مدیر'),
                ],
            )

            # 5g. Adjustment (حواله تعدیل): 1 approved+finalized + 1 submitted awaiting manager approval
            make_voucher(
                'adjustment',
                src=wh_central,
                status='approved',
                approver=users['baba_manager'],
                dt=now - timedelta(days=1),
                desc='تعدیل مثبت موجودی بر اساس انبارگردانی دوره‌ای',
                lines=[
                    (prod('خودکار'), 5, 120000, 'مغایرت شمارش انبارگردانی'),
                ],
            )
            make_voucher(
                'adjustment',
                src=wh_pos,
                status='submitted',
                requires_approval=True,
                dt=now - timedelta(hours=1),
                desc='تعدیل کاهشی کسری تخم‌مرغ در سردخانه (در انتظار تایید مدیر)',
                lines=[
                    (prod('تخم مرغ'), -10, 125000, 'شکستگی حین جابجایی'),
                ],
            )

            # 5h. Draft & Cancelled vouchers
            make_voucher(
                'receipt',
                dst=wh_central,
                status='draft',
                dt=now,
                desc='پیش‌نویس حواله ورود خرید هفتگی (تکمیل‌نشده)',
                lines=[
                    (prod('آرد خبازی 21 درصد'), 25, 380000, 'پیش‌نویس ورود آرد'),
                ],
            )
            make_voucher(
                'transfer',
                src=wh_central,
                dst=wh_pos,
                status='draft',
                dt=now,
                desc='پیش‌نویس حواله انتقال ادویه و آبلیمو',
                lines=[
                    (prod('آبلیمو صنعتی'), 3, 850000, 'پیش‌نویس انتقال'),
                ],
            )
            make_voucher(
                'issue',
                src=wh_central,
                status='cancelled',
                dt=now - timedelta(days=2),
                desc='حواله خروج لغوشده به دلیل تغییر برنامه شیفت',
                lines=[
                    (prod('ابر'), 5, 280000, 'لغو توسط انباردار'),
                ],
            )

            # ------------------------------------------------------------------
            # 6. Helper for creating PurchaseRequest with timestamp backdating
            # ------------------------------------------------------------------
            def make_pr(user, status, items_spec, *, days_ago=0, hours_ago=0,
                        source='requester', delivery_date=None, delivery_immediate=False,
                        manager_confirmed=False, confirmed_by=None, rejection_reason=None,
                        intended_officer=None, processed_by=None):
                created_dt = now - timedelta(days=days_ago, hours=hours_ago)
                pr = PurchaseRequest.objects.create(
                    user=user,
                    cafe=cafe,
                    status=status,
                    source=source,
                    delivery_date=delivery_date,
                    delivery_immediate=delivery_immediate,
                    manager_confirmed=manager_confirmed,
                    manager_confirmed_by=confirmed_by,
                    manager_confirmed_at=(created_dt + timedelta(hours=1)) if confirmed_by else None,
                    rejection_reason=rejection_reason,
                    intended_purchasing_officer=intended_officer,
                    processed_by=processed_by,
                    processed_at=(created_dt + timedelta(hours=2)) if processed_by else None,
                )
                PurchaseRequest.objects.filter(pk=pr.pk).update(created_at=created_dt)

                created_items = []
                for spec in items_spec:
                    p = spec.get('product')
                    unit_name = spec.get('unit') or (p.unit.name if p and p.unit else 'عدد')
                    pi = PurchaseItem.objects.create(
                        request=pr,
                        product=p,
                        name=spec.get('name') or (p.name if p else 'کالای متفرقه'),
                        quantity=Decimal(str(spec['qty'])),
                        unit=unit_name,
                        description=spec.get('desc', ''),
                        purchased_quantity=Decimal(str(spec['purchased_qty'])) if spec.get('purchased_qty') is not None else None,
                        supply_status=spec.get('supply_status', 'pending'),
                        source_item=spec.get('source_item'),
                        received_confirmed_at=spec.get('received_confirmed_at'),
                        received_confirmed_by=spec.get('received_confirmed_by'),
                    )
                    created_items.append((pi, spec))

                ActivityLog.objects.create(
                    user=user,
                    request=pr,
                    action='created',
                    description=f'ثبت درخواست توسط {user.get_full_name() or user.username} {SEED_TAG}',
                )
                return pr, created_items

            # ------------------------------------------------------------------
            # 7. Today's Section Requests (visible immediately in warehouse dashboard!)
            # ------------------------------------------------------------------
            tomorrow = today + timedelta(days=1)

            # 7a. بخش بار (amirhosein) — today, mixed enough / short / out of stock
            pr_bar, bar_items = make_pr(
                amirhosein, 'pending',
                [
                    {'product': prod('آب گاز دار سودا 250 میل'), 'qty': 10, 'desc': 'شیفت عصر بار'},
                    {'product': prod('آب گاز دار (سودا ) یک لیتری'), 'qty': 40, 'desc': 'درخواست بیش از موجودی فعلی انبار'},
                    {'product': prod('آلبالو رسمی'), 'qty': 5, 'desc': 'موجود نیست — آماده ارجاع به خرید'},
                ],
                hours_ago=2,
                delivery_immediate=True,
            )
            for pi, _ in bar_items:
                OfficerAssignment.objects.create(
                    item=pi, target_role='warehouse', assigned_to=None,
                    assigned_by=amirhosein, status='pending',
                    notes='ارسال مستقیم از فرم درخواست کالا',
                )

            # 7b. بخش آماده سازی (baba_prep) — today
            pr_prep, prep_items = make_pr(
                users['baba_prep'], 'pending',
                [
                    {'product': prod('آرد خبازی 21 درصد'), 'qty': 15, 'desc': 'آماده‌سازی خمیر پیتزا'},
                    {'product': prod('ادویه آرومات'), 'qty': 500, 'desc': 'مرینیت مرغ'},
                    {'product': prod('آرد گندم کامل'), 'qty': 8, 'desc': 'ناموجود در انبار'},
                ],
                hours_ago=3,
                delivery_date=tomorrow,
            )
            for pi, _ in prep_items:
                OfficerAssignment.objects.create(
                    item=pi, target_role='warehouse', assigned_to=None,
                    assigned_by=users['baba_prep'], status='pending',
                    notes='ارسال مستقیم از فرم درخواست کالا',
                )

            # 7c. بخش ایرانی (baba_irani) — today: 1 fulfilled (unconfirmed delivery!), 1 pending
            pr_irani, irani_items = make_pr(
                users['baba_irani'], 'partial',
                [
                    {'product': prod('آبلیمو صنعتی'), 'qty': 4, 'purchased_qty': 4,
                     'supply_status': 'from_stock', 'desc': 'مصرف کباب و جوجه'},
                    {'product': prod('ادویه کاری'), 'qty': 400, 'desc': 'خورشت روز'},
                ],
                hours_ago=4,
                delivery_date=tomorrow,
            )
            si_lemon = StockItem.objects.get(warehouse=wh_central, product=prod('آبلیمو صنعتی'))
            apply_stock_delta(
                si_lemon, Decimal('-4'), cafe=cafe, created_by=ehsan,
                reference_type='officer_assignment', reference_id=irani_items[0][0].id,
                description=f'تامین کالا برای بخش ایرانی - آبلیمو صنعتی {SEED_TAG}',
            )
            OfficerAssignment.objects.create(
                item=irani_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=users['baba_irani'],
                status='fulfilled', stock_item=si_lemon, supplied_quantity=Decimal('4'),
                completed_at=now - timedelta(hours=1),
                notes='تامین از انبار مرکزی (در انتظار تایید دریافت بخش ایرانی)',
            )
            OfficerAssignment.objects.create(
                item=irani_items[1][0], target_role='warehouse',
                assigned_to=None, assigned_by=users['baba_irani'],
                status='pending', notes='ارسال مستقیم از فرم درخواست کالا',
            )

            # 7d. بخش فرنگی (baba_farangi) — today: 1 referred to purchase draft, 1 pending
            pr_farangi, farangi_items = make_pr(
                users['baba_farangi'], 'pending',
                [
                    {'product': prod('اسپاگتی'), 'qty': 2000, 'desc': 'پاستای آلفردو و بولونز'},
                    {'product': prod('آلبالو منجمد'), 'qty': 5, 'desc': 'سس آلبالو استیک'},
                ],
                hours_ago=3,
                delivery_date=tomorrow,
            )
            OfficerAssignment.objects.create(
                item=farangi_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=users['baba_farangi'],
                status='returned', completed_at=now - timedelta(hours=1),
                notes='ارجاع به درخواست خرید (موجود در پیش‌نویس خرید)',
            )
            OfficerAssignment.objects.create(
                item=farangi_items[1][0], target_role='warehouse',
                assigned_to=None, assigned_by=users['baba_farangi'],
                status='pending', notes='ارسال مستقیم از فرم درخواست کالا',
            )

            # 7e. بخش صبحانه (baba_breakfast) — today: 1 fulfilled AND confirmed by requester, 1 pending
            pr_bkf, bkf_items = make_pr(
                users['baba_breakfast'], 'partial',
                [
                    {'product': prod('خامه صبحانه'), 'qty': 5, 'purchased_qty': 5,
                     'supply_status': 'from_stock', 'desc': 'سرو صبحانه فردا',
                     'received_confirmed_at': now - timedelta(minutes=45),
                     'received_confirmed_by': users['baba_breakfast']},
                    {'product': prod('اسفناج'), 'qty': 6, 'desc': 'املت اسفناج'},
                ],
                hours_ago=4,
                delivery_date=tomorrow,
            )
            si_cream = StockItem.objects.get(warehouse=wh_pos, product=prod('خامه صبحانه'))
            apply_stock_delta(
                si_cream, Decimal('-5'), cafe=cafe, created_by=ehsan,
                reference_type='officer_assignment', reference_id=bkf_items[0][0].id,
                description=f'تامین کالا برای بخش صبحانه - خامه صبحانه {SEED_TAG}',
            )
            OfficerAssignment.objects.create(
                item=bkf_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=users['baba_breakfast'],
                status='fulfilled', stock_item=si_cream, supplied_quantity=Decimal('5'),
                completed_at=now - timedelta(hours=2),
                notes='تامین شد از سردخانه مثبت صفر (دریافت تایید شده)',
            )
            OfficerAssignment.objects.create(
                item=bkf_items[1][0], target_role='warehouse',
                assigned_to=None, assigned_by=users['baba_breakfast'],
                status='pending', notes='ارسال مستقیم از فرم درخواست کالا',
            )

            # 7f. بخش سالاد و پیش غذا (baba_salad) — today: 1 not_in_stock, 1 pending
            pr_salad, salad_items = make_pr(
                users['baba_salad'], 'pending',
                [
                    {'product': prod('آووکادو'), 'qty': 10, 'purchased_qty': 0,
                     'supply_status': 'not_supplied', 'desc': 'سالاد آووکادو'},
                    {'product': prod('ارده'), 'qty': 1500, 'desc': 'سس سالاد'},
                ],
                hours_ago=3,
                delivery_immediate=True,
            )
            OfficerAssignment.objects.create(
                item=salad_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=users['baba_salad'],
                status='not_in_stock', completed_at=now - timedelta(hours=1),
                notes='عدم امکان تأمین ثبت شد (ناموجود در بازار روز)',
            )
            OfficerAssignment.objects.create(
                item=salad_items[1][0], target_role='warehouse',
                assigned_to=None, assigned_by=users['baba_salad'],
                status='pending', notes='ارسال مستقیم از فرم درخواست کالا',
            )

            # 7g. بخش کانتر (baba_counter) — today: 1 referred to purchase draft, 1 pending
            pr_counter, counter_items = make_pr(
                users['baba_counter'], 'pending',
                [
                    {'product': prod('جعبه پیتزا'), 'qty': 100, 'desc': 'مصرفی بیرون‌بر آخر هفته'},
                    {'product': prod('آب معدنی'), 'qty': 12, 'desc': 'سرو کانتر'},
                ],
                hours_ago=2,
            )
            OfficerAssignment.objects.create(
                item=counter_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=users['baba_counter'],
                status='returned', completed_at=now - timedelta(minutes=40),
                notes='ارجاع به درخواست خرید به دلیل کمبود موجودی',
            )
            OfficerAssignment.objects.create(
                item=counter_items[1][0], target_role='warehouse',
                assigned_to=None, assigned_by=users['baba_counter'],
                status='pending', notes='ارسال مستقیم از فرم درخواست کالا',
            )

            # 7h. بخش خدمات (baba_services) — today: 1 fulfilled (unconfirmed delivery), 1 pending
            pr_srv, srv_items = make_pr(
                users['baba_services'], 'partial',
                [
                    {'product': prod('اسپری چند منظوره'), 'qty': 2, 'purchased_qty': 2,
                     'supply_status': 'from_stock', 'desc': 'نظافت آشپزخانه'},
                    {'product': prod('ابر'), 'qty': 10, 'desc': 'شستشو'},
                ],
                hours_ago=5,
            )
            si_spray = StockItem.objects.get(warehouse=wh_central, product=prod('اسپری چند منظوره'))
            apply_stock_delta(
                si_spray, Decimal('-2'), cafe=cafe, created_by=ehsan,
                reference_type='officer_assignment', reference_id=srv_items[0][0].id,
                description=f'تامین کالا برای بخش خدمات - اسپری چند منظوره {SEED_TAG}',
            )
            OfficerAssignment.objects.create(
                item=srv_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=users['baba_services'],
                status='fulfilled', stock_item=si_spray, supplied_quantity=Decimal('2'),
                completed_at=now - timedelta(hours=2),
                notes='تامین شد از انبار مرکزی (منتظر تایید دریافت بخش خدمات)',
            )
            OfficerAssignment.objects.create(
                item=srv_items[1][0], target_role='warehouse',
                assigned_to=None, assigned_by=users['baba_services'],
                status='pending', notes='ارسال مستقیم از فرم درخواست کالا',
            )

            # 7i. بخش قلیون بار (baba_hookah) — today: pending
            pr_hookah, hookah_items = make_pr(
                users['baba_hookah'], 'pending',
                [
                    {'product': prod('تنباکو دوسیب'), 'qty': 500, 'desc': 'شیفت شب'},
                    {'product': prod('تنباکو بلوبری'), 'qty': 500, 'desc': 'شیفت شب'},
                ],
                hours_ago=1,
            )
            for pi, _ in hookah_items:
                OfficerAssignment.objects.create(
                    item=pi, target_role='warehouse', assigned_to=None,
                    assigned_by=users['baba_hookah'], status='pending',
                    notes='ارسال مستقیم از فرم درخواست کالا',
                )

            # 7j. بخش سالن (baba_hall) & مالی و صندوق (baba_finance_sec) — today
            pr_hall, hall_items = make_pr(
                users['baba_hall'], 'pending',
                [
                    {'product': prod('اسپری شیشه شور'), 'qty': 2, 'desc': 'نظافت شیشه‌های سالن'},
                ],
                hours_ago=1,
            )
            OfficerAssignment.objects.create(
                item=hall_items[0][0], target_role='warehouse', assigned_to=None,
                assigned_by=users['baba_hall'], status='pending',
            )

            pr_fin_sec, fin_sec_items = make_pr(
                users['baba_finance_sec'], 'purchased',
                [
                    {'product': prod('برگه A4'), 'qty': 2, 'purchased_qty': 2,
                     'supply_status': 'from_stock', 'desc': 'چاپ فاکتورها و گزارش‌ها',
                     'received_confirmed_at': now - timedelta(minutes=20),
                     'received_confirmed_by': users['baba_finance_sec']},
                ],
                hours_ago=4,
            )
            si_a4 = StockItem.objects.get(warehouse=wh_central, product=prod('برگه A4'))
            apply_stock_delta(
                si_a4, Decimal('-2'), cafe=cafe, created_by=ehsan,
                reference_type='officer_assignment', reference_id=fin_sec_items[0][0].id,
                description=f'تامین کالا برای بخش مالی و صندوق - برگه A4 {SEED_TAG}',
            )
            OfficerAssignment.objects.create(
                item=fin_sec_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=users['baba_finance_sec'],
                status='fulfilled', stock_item=si_a4, supplied_quantity=Decimal('2'),
                completed_at=now - timedelta(hours=1),
                notes='تحویل داده شد و دریافت تایید گردید',
            )

            # ------------------------------------------------------------------
            # 8. Historical Requests (yesterday / 2-3 days ago) + Manager Approvals
            # ------------------------------------------------------------------
            # 8a. Yesterday fulfilled & confirmed request for amirhosein (بار)
            pr_bar_old, bar_old_items = make_pr(
                amirhosein, 'purchased',
                [
                    {'product': prod('خامه حیوانی'), 'qty': 3, 'purchased_qty': 3,
                     'supply_status': 'from_stock', 'desc': 'درخواست دیروز بار',
                     'received_confirmed_at': now - timedelta(days=1),
                     'received_confirmed_by': amirhosein},
                ],
                days_ago=1, hours_ago=4,
            )
            si_hcream = StockItem.objects.get(warehouse=wh_pos, product=prod('خامه حیوانی'))
            apply_stock_delta(
                si_hcream, Decimal('-3'), cafe=cafe, created_by=ehsan,
                reference_type='officer_assignment', reference_id=bar_old_items[0][0].id,
                description=f'تامین کالا دیروز برای بار - خامه حیوانی {SEED_TAG}',
            )
            OfficerAssignment.objects.create(
                item=bar_old_items[0][0], target_role='warehouse',
                assigned_to=ehsan, assigned_by=amirhosein,
                status='fulfilled', stock_item=si_hcream, supplied_quantity=Decimal('3'),
                completed_at=now - timedelta(days=1, hours=2),
                notes='تامین شد از سردخانه مثبت صفر',
            )

            # 8b. Pending manager approval request (تایید درخواست — منتظر تایید مدیر/مدیرعامل)
            make_pr(
                users['baba_prep'], 'pending_manager_approval',
                [
                    {'product': prod('خرده بره'), 'qty': 30, 'desc': 'سفارش عمده مهمانی آخر هفته — منتظر تایید مدیر'},
                    {'product': None, 'name': 'ترازوی دیجیتال صنعتی ۵۰ کیلویی', 'qty': 1, 'unit': 'عدد',
                     'desc': 'تجهیزات جدید بخش آماده‌سازی'},
                ],
                hours_ago=2,
                delivery_date=tomorrow,
            )

            # 8c. Rejected request by manager
            make_pr(
                users['baba_hall'], 'rejected',
                [
                    {'product': prod('اسپری چند منظوره'), 'qty': 25, 'desc': 'درخواست مازاد'},
                ],
                days_ago=2,
                confirmed_by=users['baba_manager'],
                manager_confirmed=False,
                rejection_reason='موجودی انبار مرکزی کافی است؛ مقدار کمتر ثبت شود.',
            )

            # ------------------------------------------------------------------
            # 9. Warehouse PurchaseDraft & Warehouse Purchase Requests (درخواست خرید)
            # ------------------------------------------------------------------
            draft = PurchaseDraft.objects.create(cafe=cafe, created_by=ehsan)
            # Referred from section requests
            PurchaseDraftItem.objects.create(
                draft=draft,
                product=prod('اسپاگتی'),
                name='اسپاگتی',
                quantity=Decimal('2000'),
                unit='گرم',
                description='ارجاع از درخواست بخش فرنگی',
                source_item=farangi_items[0][0],
            )
            PurchaseDraftItem.objects.create(
                draft=draft,
                product=prod('جعبه پیتزا'),
                name='جعبه پیتزا',
                quantity=Decimal('100'),
                unit='عدد',
                description='ارجاع از درخواست بخش کانتر',
                source_item=counter_items[0][0],
            )
            # Manual normal item (direct to purchasing — no CEO needed)
            PurchaseDraftItem.objects.create(
                draft=draft,
                product=prod('آبلیمو صنعتی'),
                name='آبلیمو صنعتی',
                quantity=Decimal('10'),
                unit='لیتر',
                description='تامین ذخیره انبار مرکزی',
            )
            # Manual CEO-triggering item (unlisted product -> needs_ceo=True)
            PurchaseDraftItem.objects.create(
                draft=draft,
                product=None,
                name='دستگاه آسیاب قهوه صنعتی',
                quantity=Decimal('1'),
                unit='عدد',
                description='اقلام سرمایه‌ای خارج از کاتالوگ (نیازمند تایید مدیرعامل)',
            )

            # Sent PurchaseRequest 1: Awaiting CEO approval (warehouse_purchase -> pending_manager_approval)
            make_pr(
                ehsan, 'pending_manager_approval',
                [
                    {'product': prod('آناناس'), 'qty': 200, 'desc': 'ارزش بیش از ۴۰۰ میلیون ریال — نیازمند تایید مدیرعامل'},
                    {'product': None, 'name': 'کولر گازی اسپلیت ۳۰۰۰۰', 'qty': 1, 'unit': 'عدد',
                     'desc': 'کالای تعریف‌نشده در فهرست — نیازمند تایید مدیرعامل'},
                ],
                hours_ago=3,
                source='warehouse_purchase',
                intended_officer=users['baba_purchasing'],
            )

            # Sent PurchaseRequest 2: Approved by CEO / direct -> pending in purchasing officer queue
            pr_buy_pending, buy_pending_items = make_pr(
                ehsan, 'pending',
                [
                    {'product': prod('بیکن گوشت'), 'qty': 20, 'desc': 'تکمیل موجودی سردخانه منفی صفر'},
                    {'product': prod('اکبر جوجه'), 'qty': 25, 'desc': 'سفارش پروتئین هفتگی'},
                ],
                days_ago=1,
                source='warehouse_purchase',
                manager_confirmed=True,
                confirmed_by=users['baba_ceo'],
                intended_officer=users['baba_purchasing'],
            )
            for pi, _ in buy_pending_items:
                OfficerAssignment.objects.create(
                    item=pi, target_role='purchasing',
                    assigned_to=users['baba_purchasing'],
                    assigned_by=ehsan, status='pending',
                    notes='ارسال‌شده به واحد خرید برای خریداری',
                )

            # Sent PurchaseRequest 3: Unassigned pool in purchasing officer queue
            pr_buy_pool, buy_pool_items = make_pr(
                ehsan, 'pending',
                [
                    {'product': prod('آرد گندم کامل'), 'qty': 30, 'desc': 'صندوق مشترک واحد خرید'},
                ],
                hours_ago=5,
                source='warehouse_purchase',
            )
            for pi, _ in buy_pool_items:
                OfficerAssignment.objects.create(
                    item=pi, target_role='purchasing',
                    assigned_to=None,
                    assigned_by=ehsan, status='pending',
                    notes='ارسال مستقیم به صندوق مشترک خرید',
                )

            # Sent PurchaseRequest 4: Self-buy by warehouse officer (ehsan -> خریدهای من)
            pr_self_buy, self_buy_items = make_pr(
                ehsan, 'pending',
                [
                    {'product': prod('اسفناج'), 'qty': 15, 'desc': 'خرید روز تره‌بار توسط مسئول انبار'},
                ],
                hours_ago=2,
                source='warehouse_purchase',
                intended_officer=ehsan,
            )
            for pi, _ in self_buy_items:
                OfficerAssignment.objects.create(
                    item=pi, target_role='purchasing',
                    assigned_to=ehsan,
                    assigned_by=ehsan, status='pending',
                    notes='خرید توسط خودم',
                )

            # Sent PurchaseRequest 5: Purchased (history for purchasing officer)
            pr_purchased, purchased_items = make_pr(
                ehsan, 'purchased',
                [
                    {'product': prod('تخم مرغ'), 'qty': 180, 'purchased_qty': 180,
                     'supply_status': 'purchased', 'desc': 'خرید ۶ شانه تخم‌مرغ'},
                    {'product': prod('خامه حیوانی'), 'qty': 15, 'purchased_qty': 15,
                     'supply_status': 'purchased', 'desc': 'تامین لبنیات'},
                ],
                days_ago=2,
                source='warehouse_purchase',
                manager_confirmed=True,
                confirmed_by=users['baba_ceo'],
                intended_officer=users['baba_purchasing'],
                processed_by=users['baba_purchasing'],
            )
            for pi, _ in purchased_items:
                OfficerAssignment.objects.create(
                    item=pi, target_role='purchasing',
                    assigned_to=users['baba_purchasing'],
                    assigned_by=ehsan, status='purchased',
                    completed_at=now - timedelta(days=2) + timedelta(hours=3),
                    notes='خریداری شد توسط سعید شریفی.',
                )

            # Sent PurchaseRequest 6: Rejected by CEO (appears in warehouse_purchase_draft rejected list)
            make_pr(
                ehsan, 'rejected',
                [
                    {'product': prod('ادویه کاری'), 'qty': 10000, 'desc': 'خرید فله‌ای سالانه'},
                ],
                days_ago=1,
                source='warehouse_purchase',
                manager_confirmed=False,
                confirmed_by=users['baba_ceo'],
                rejection_reason='مقدار درخواستی بیش از نیاز ماهانه است؛ با مقدار ۲ کیلوگرم ثبت شود.',
            )

            # ------------------------------------------------------------------
            # 10. PurchaseOrders (سفارش خرید رسمی)
            # ------------------------------------------------------------------
            po1 = PurchaseOrder.objects.create(
                order_number=f'PO-{today.strftime("%Y%m%d")}-001',
                request=pr_buy_pending,
                supplier=suppliers['مجتمع پروتئین و مرغ پارس'],
                delivery_date=tomorrow,
                status='sent',
                sent_at=now - timedelta(hours=4),
                payment_terms='تسویه ۱۵ روزه',
                notes=f'سفارش پروتئین هفتگی {SEED_TAG}',
                created_by=users['baba_purchasing'],
            )
            for pi in pr_buy_pending.items.all():
                PurchaseOrderItem.objects.create(
                    order=po1, request_item=pi, product=pi.product,
                    quantity=pi.quantity,
                    unit_price=Decimal('6400000') if pi.product.name == 'بیکن گوشت' else Decimal('2850000'),
                )

            po2 = PurchaseOrder.objects.create(
                order_number=f'PO-{today.strftime("%Y%m%d")}-002',
                request=pr_purchased,
                supplier=suppliers['لبنیات و نوشیدنی مهر'],
                delivery_date=today - timedelta(days=1),
                actual_delivery_date=today - timedelta(days=1),
                status='completed',
                delivery_status='full',
                quality_status='passed',
                sent_at=now - timedelta(days=2),
                confirmed_at=now - timedelta(days=2) + timedelta(hours=2),
                delivered_at=now - timedelta(days=1),
                completed_at=now - timedelta(days=1) + timedelta(hours=1),
                received_by=ehsan,
                quality_inspector=ehsan,
                notes=f'تحویل کامل و تایید کیفیت {SEED_TAG}',
                created_by=users['baba_purchasing'],
            )
            for pi in pr_purchased.items.all():
                PurchaseOrderItem.objects.create(
                    order=po2, request_item=pi, product=pi.product,
                    quantity=pi.quantity,
                    unit_price=Decimal('125000') if pi.product.name == 'تخم مرغ' else Decimal('2400000'),
                )

            # ------------------------------------------------------------------
            # 11. WarehouseRequests (درخواست از انبار — pending, fulfilled, partial, declined)
            # ------------------------------------------------------------------
            wr_pending = WarehouseRequest.objects.create(
                cafe=cafe, user=users['baba_prep'], status='pending',
                notes=f'درخواست اقلام خشک شیفت فردا {SEED_TAG}',
            )
            WarehouseRequestItem.objects.create(
                warehouse_request=wr_pending, product=prod('آرد خبازی 21 درصد'),
                name='آرد خبازی 21 درصد', quantity=Decimal('5'), unit='کیلوگرم',
                description='خط نان و پیتزا',
            )
            WarehouseRequestItem.objects.create(
                warehouse_request=wr_pending, product=prod('ادویه آرومات'),
                name='ادویه آرومات', quantity=Decimal('300'), unit='گرم',
                description='مرینیت',
            )

            wr_fulfilled = WarehouseRequest.objects.create(
                cafe=cafe, user=users['baba_breakfast'], status='approved_from_stock',
                processed_by=ehsan, processed_at=now - timedelta(hours=3),
                notes=f'تامین کامل از انبار و سردخانه {SEED_TAG}',
            )
            si_egg = StockItem.objects.get(warehouse=wh_pos, product=prod('تخم مرغ'))
            apply_stock_delta(
                si_egg, Decimal('-30'), cafe=cafe, created_by=ehsan,
                reference_type='warehouse_request', reference_id=wr_fulfilled.id,
                description=f'تامین درخواست انبار #{wr_fulfilled.id} - تخم مرغ {SEED_TAG}',
            )
            WarehouseRequestItem.objects.create(
                warehouse_request=wr_fulfilled, product=prod('تخم مرغ'),
                name='تخم مرغ', quantity=Decimal('30'), unit='عدد',
                supply_status='from_stock', stock_item=si_egg, supplied_quantity=Decimal('30'),
            )

            wr_partial = WarehouseRequest.objects.create(
                cafe=cafe, user=users['baba_salad'], status='partial',
                processed_by=ehsan, processed_at=now - timedelta(hours=2),
                notes=f'تامین جزئی؛ یک قلم عدم تامین {SEED_TAG}',
            )
            si_cuc = StockItem.objects.get(warehouse=wh_pos, product=prod('خیار بوته ای'))
            apply_stock_delta(
                si_cuc, Decimal('-3'), cafe=cafe, created_by=ehsan,
                reference_type='warehouse_request', reference_id=wr_partial.id,
                description=f'تامین درخواست انبار #{wr_partial.id} - خیار بوته ای {SEED_TAG}',
            )
            WarehouseRequestItem.objects.create(
                warehouse_request=wr_partial, product=prod('خیار بوته ای'),
                name='خیار بوته ای', quantity=Decimal('3'), unit='کیلوگرم',
                supply_status='from_stock', stock_item=si_cuc, supplied_quantity=Decimal('3'),
            )
            WarehouseRequestItem.objects.create(
                warehouse_request=wr_partial, product=prod('آووکادو'),
                name='آووکادو', quantity=Decimal('4'), unit='عدد',
                supply_status='declined', description='ناموجود در سردخانه',
            )

            wr_declined = WarehouseRequest.objects.create(
                cafe=cafe, user=users['baba_hookah'], status='declined',
                processed_by=ehsan, processed_at=now - timedelta(days=1),
                notes=f'عدم تامین {SEED_TAG}',
            )
            WarehouseRequestItem.objects.create(
                warehouse_request=wr_declined, product=prod('تنباکو دوسیب'),
                name='تنباکو دوسیب', quantity=Decimal('5000'), unit='گرم',
                supply_status='declined', description='مقدار درخواستی بیش از سقف روزانه بخش',
            )

            # ------------------------------------------------------------------
            # 12. Sample Notifications
            # ------------------------------------------------------------------
            Notification.objects.create(
                recipient=ehsan, cafe=cafe, title='درخواست‌های جدید در انبار',
                message='چند درخواست کالا از بخش‌های بار، آماده‌سازی، ایرانی، فرنگی و قلیون بار ثبت شده است.',
                notification_type='info', priority='medium',
            )
            Notification.objects.create(
                recipient=users['baba_ceo'], cafe=cafe, title='درخواست خرید در انتظار تایید مدیرعامل',
                message='درخواست خرید مسئول انبار (اقلام با ارزش بالا و خارج از فهرست) در انتظار تایید شماست.',
                notification_type='approval', priority='high',
            )
            Notification.objects.create(
                recipient=users['baba_manager'], cafe=cafe, title='حواله‌های انبار نیازمند تایید',
                message='حواله تعدیل و حواله ضایعات بالای آستانه در انتظار تایید مدیریت هستند.',
                notification_type='approval', priority='high',
            )
            Notification.objects.create(
                recipient=users['baba_irani'], cafe=cafe, title='کالا آماده تحویل است',
                message='آبلیمو صنعتی از انبار مرکزی تامین شد؛ لطفاً دریافت را تایید کنید.',
                notification_type='success', priority='medium',
            )
            Notification.objects.create(
                recipient=users['baba_purchasing'], cafe=cafe, title='اقلام جدید در لیست خرید',
                message='اقلام تاییدشده برای خرید در داشبورد مسئول خرید قرار گرفتند.',
                notification_type='info', priority='medium',
            )

        self.stdout.write(self.style.SUCCESS(
            f'Successfully seeded comprehensive sample data for cafe "{cafe.code}" ({cafe.name})!\n'
            f'Created users ({len(created_users)}): {", ".join(created_users)}\n'
            f'StockItems: {StockItem.objects.filter(warehouse__cafe=cafe).count()}, '
            f'Vouchers: {WarehouseVoucher.objects.filter(cafe=cafe).count()}, '
            f'PurchaseRequests: {PurchaseRequest.objects.filter(cafe=cafe).count()}, '
            f'WarehouseRequests: {WarehouseRequest.objects.filter(cafe=cafe).count()}, '
            f'PurchaseOrders: {PurchaseOrder.objects.filter(request__cafe=cafe).count()}'
        ))
