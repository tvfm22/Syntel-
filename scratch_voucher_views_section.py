# ─────────────────────────────────────────────────────────────────────────────
# Warehouse Voucher Views - حواله‌های انبار (Prompt 6: UI کامل روی شِمای جدید)
#
# جریان: پیش‌نویس → ثبت → (تأیید برای تعدیل) → نهایی‌سازی → برگشت / لغو
#   - نوع‌های روتین: «ثبت و نهایی‌سازی» یک‌گامه (اثر فوری مثل قبل).
#   - تعدیل (نیازمند تأیید): ثبت → صفحهٔ تأیید (مدیر) → تأیید + نهایی‌سازی.
#   - برگشت: فقط حوالهٔ نهایی‌شده، با دلیلِ اجباری.
# ─────────────────────────────────────────────────────────────────────────────

VOUCHER_TYPE_LABELS = dict(WarehouseVoucher.VOUCHER_TYPE_CHOICES)
VOUCHER_STATUS_LABELS = dict(WarehouseVoucher.STATUS_CHOICES)

# کدام فیلدهای انبار برای هر نوع در فرم ایجاد نمایش داده می‌شود
VOUCHER_WAREHOUSE_FIELDS = {
    'receipt':    ['destination'],
    'issue':      ['source'],
    'return':     ['source'],
    'sale':       ['source'],
    'waste':      ['source'],
    'transfer':   ['source', 'destination'],
    'adjustment': ['source'],
}


def _voucher_form_options(cafe, warehouse_id):
    """گزینه‌های فرم حواله — فقط داده‌های کافهٔ فعلی"""
    products = (Product.objects.filter(cafe=cafe, is_active=True)
                .select_related('category', 'unit').order_by('category__name', 'name'))
    units = Unit.objects.filter(cafe=cafe)
    brands_by_product = {}
    for b in (ProductBrand.objects.filter(product__cafe=cafe, is_active=True)
              .select_related('product').order_by('brand_name')):
        brands_by_product.setdefault(b.product_id, []).append(
            {'id': b.id, 'name': b.brand_name})
    stock_items = (StockItem.objects.filter(warehouse_id=warehouse_id,
                                            warehouse__cafe=cafe)
                   .select_related('product', 'product_brand', 'unit', 'warehouse')
                   .order_by('product__name'))
    return {
        'products': products,
        'units': units,
        'brands_json': json.dumps(brands_by_product, ensure_ascii=False),
        'stock_items': stock_items,
    }


def _parse_voucher_lines(request, cafe, voucher_type):
    """پارس قلم‌های فرم — هر شناسهٔ ارسالی (محصول/برند/موجودی/واحد) فقط در
    کافهٔ فعلی معتبر است؛ خروجی (خطوط معتبر، خطاها)"""
    product_ids = request.POST.getlist('product_id[]')
    brand_ids   = request.POST.getlist('product_brand_id[]')
    stock_ids   = request.POST.getlist('stock_item_id[]')
    unit_ids    = request.POST.getlist('unit_id[]')
    quantities  = request.POST.getlist('base_quantity[]')
    prices      = request.POST.getlist('unit_price[]')
    item_descs  = request.POST.getlist('item_description[]')

    lines = []
    errors = []
    for idx, pid in enumerate(product_ids):
        if not pid:
            continue
        try:
            product = Product.objects.get(id=int(pid), cafe=cafe)
        except (Product.DoesNotExist, ValueError, TypeError):
            errors.append(f'قلم {idx + 1}: محصول نامعتبر یا متعلق به کافهٔ دیگر است')
            continue

        brand = None
        if idx < len(brand_ids) and brand_ids[idx]:
            try:
                brand = ProductBrand.objects.select_related('product').get(
                    id=int(brand_ids[idx]), product__cafe=cafe)
            except (ProductBrand.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: برند نامعتبر یا متعلق به کافهٔ دیگر است')
                continue
            if brand.product_id != product.id:
                errors.append(f'قلم {idx + 1}: برند «{brand.brand_name}» متعلق به این محصول نیست')
                continue

        stock_item = None
        if idx < len(stock_ids) and stock_ids[idx]:
            try:
                stock_item = StockItem.objects.select_related('warehouse').get(
                    id=int(stock_ids[idx]), warehouse__cafe=cafe)
            except (StockItem.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: موجودی انبار نامعتبر یا متعلق به کافهٔ دیگر است')
                continue

        unit_obj = None
        if idx < len(unit_ids) and unit_ids[idx]:
            try:
                unit_obj = Unit.objects.get(id=int(unit_ids[idx]), cafe=cafe)
            except (Unit.DoesNotExist, ValueError, TypeError):
                errors.append(f'قلم {idx + 1}: واحد نامعتبر یا متعلق به کافهٔ دیگر است')
                continue
        if unit_obj is None:
            unit_obj = stock_item.unit if stock_item else product.unit

        def _dec(vals, default=None):
            s = (vals[idx] if idx < len(vals) else '').replace(',', '').strip()
            if not s:
                return default
            try:
                return Decimal(s)
            except Exception:
                return 'INVALID'

        base_qty = _dec(quantities)
        if base_qty == 'INVALID':
            errors.append(f'قلم {idx + 1}: مقدار پایه نامعتبر است')
            continue
        if base_qty is None:
            errors.append(f'قلم {idx + 1}: مقدار پایه الزامی است')
            continue
        if base_qty == 0:
            errors.append(f'قلم {idx + 1}: مقدار پایه نباید صفر باشد')
            continue
        if base_qty < 0 and voucher_type != 'adjustment':
            errors.append(f'قلم {idx + 1}: مقدار پایه باید مثبت باشد')
            continue

        price = _dec(prices, default=Decimal('0'))
        if price == 'INVALID':
            price = Decimal('0')

        lines.append({
            'product': product,
            'product_brand': brand,
            'stock_item': stock_item,
            'unit': unit_obj,
            'base_quantity': base_qty,
            'trade_quantity': None,
            'conversion_rate': brand.conversion_rate if brand else None,
            'unit_price': price,
            'description': (item_descs[idx] if idx < len(item_descs) else '').strip(),
        })
    return lines, errors


def _render_voucher_form(request, cafe, voucher_type, warehouse, extra=None):
    """رندر فرم ایجاد/ویرایش حواله با گزینه‌های کافهٔ فعلی"""
    opts = _voucher_form_options(cafe, warehouse.id if warehouse else None)
    ctx = {
        'voucher_type': voucher_type,
        'type_label': VOUCHER_TYPE_LABELS.get(voucher_type, ''),
        'warehouse': warehouse,
        'warehouses': Warehouse.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'warehouse_fields': VOUCHER_WAREHOUSE_FIELDS.get(voucher_type, []),
        'cafe_code': cafe.code if cafe else '',
        'products': opts['products'],
        'units': opts['units'],
        'brands_json': opts['brands_json'],
        'stock_items': opts['stock_items'],
    }
    if extra:
        ctx.update(extra)
    return render(request, 'stock/voucher_create.html', ctx)


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting'])
def voucher_list(request: HttpRequest) -> HttpResponse:
    """لیست حواله‌های انبار با فیلترهای کامل (نوع، وضعیت، بازهٔ تاریخ، انبار
    مبدأ/مقصد، محصول، بخش، ایجادکننده، شماره)"""
    cafe = get_cafe_from_request(request)
    vouchers = WarehouseVoucher.objects.select_related(
        'source_warehouse', 'destination_warehouse', 'created_by', 'approver'
    ).filter(cafe=cafe)

    f = {
        'type': request.GET.get('type'),
        'status': request.GET.get('status'),
        'date_from': request.GET.get('date_from', '').strip(),
        'date_to': request.GET.get('date_to', '').strip(),
        'source_warehouse': request.GET.get('source_warehouse'),
        'dest_warehouse': request.GET.get('dest_warehouse'),
        'product': request.GET.get('product'),
        'department': request.GET.get('department'),
        'creator': request.GET.get('creator'),
        'search': request.GET.get('search', '').strip(),
    }

    if f['type'] in VOUCHER_TYPE_LABELS:
        vouchers = vouchers.filter(voucher_type=f['type'])
    if f['status'] in VOUCHER_STATUS_LABELS:
        vouchers = vouchers.filter(status=f['status'])
    if f['date_from']:
        try:
            vouchers = vouchers.filter(date__gte=datetime.strptime(f['date_from'], '%Y-%m-%d').date())
        except ValueError:
            pass
    if f['date_to']:
        try:
            vouchers = vouchers.filter(date__lte=datetime.strptime(f['date_to'], '%Y-%m-%d').date())
        except ValueError:
            pass
    if f['source_warehouse']:
        vouchers = vouchers.filter(source_warehouse_id=f['source_warehouse'])
    if f['dest_warehouse']:
        vouchers = vouchers.filter(destination_warehouse_id=f['dest_warehouse'])
    if f['product']:
        vouchers = vouchers.filter(items__product_id=f['product'])
    if f['department']:
        # بخش از طریق انبارِ مرتبط با بخش — مبدأ یا مقصدِ حواله
        dept_wh_ids = Department.objects.filter(cafe=cafe, id=f['department']).values_list('warehouse_id', flat=True)
        vouchers = vouchers.filter(
            Q(source_warehouse_id__in=dept_wh_ids) | Q(destination_warehouse_id__in=dept_wh_ids))
    if f['creator']:
        vouchers = vouchers.filter(created_by_id=f['creator'])
    if f['search']:
        vouchers = vouchers.filter(voucher_number__icontains=f['search'])

    vouchers = vouchers.distinct().order_by('-created_at')
    paginator = Paginator(vouchers, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'stock/voucher_list.html', {
        'page_obj': page_obj,
        'warehouses': Warehouse.objects.filter(cafe=cafe),
        'departments': Department.objects.filter(cafe=cafe).order_by('name'),
        'creators': (User.objects.filter(created_warehouse_vouchers__cafe=cafe)
                     .distinct().order_by('username')),
        'products': Product.objects.filter(cafe=cafe, is_active=True).order_by('name'),
        'voucher_types': VOUCHER_TYPE_LABELS,
        'statuses': VOUCHER_STATUS_LABELS,
        'f': f,
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo'])
def voucher_create(request: HttpRequest, voucher_type: str) -> HttpResponse:
    """فرم یکپارچهٔ ایجاد حواله — همهٔ ۷ نوع در یک فرم؛ انتخاب نوع فیلدهای
    انبارِ مرتبط را نشان می‌دهد و اعتبارسنجی حساس به نوع در سرویس مرکزی است.

    - «ثبت پیش‌نویس» → وضعیت draft (قابل ویرایش/لغو)
    - «ثبت و نهایی‌سازی» → نوع‌های روتین همان لحظه نهایی می‌شوند (اثر فوری مثل
      قبل)؛ تعدیل «ثبت‌شده» می‌ماند تا مدیر تأییدش کند.
    """
    cafe = get_cafe_from_request(request)
    if voucher_type not in VOUCHER_TYPE_LABELS:
        raise Http404('نوع حواله نامعتبر است')

    if request.method == 'POST':
        action = request.POST.get('action', 'submit')
        try:
            src_id = int(request.POST.get('source_warehouse')) if request.POST.get('source_warehouse') else None
            dst_id = int(request.POST.get('dest_warehouse')) if request.POST.get('dest_warehouse') else None
        except (ValueError, TypeError):
            src_id = dst_id = None

        src_wh = get_scoped_object_or_404(Warehouse, request, id=src_id) if src_id else None
        dst_wh = get_scoped_object_or_404(Warehouse, request, id=dst_id) if dst_id else None

        lines, errors = _parse_voucher_lines(request, cafe, voucher_type)
        if errors:
            for e in errors:
                messages.error(request, e)
            return _render_voucher_form(request, cafe, voucher_type, src_wh or dst_wh,
                                        {'form_errors': errors})

        vdate = timezone.now()
        date_str = request.POST.get('date', '').strip()
        if date_str:
            try:
                vdate = timezone.make_aware(datetime.strptime(date_str, '%Y-%m-%dT%H:%M'))
            except Exception:
                pass

        description = request.POST.get('description', '').strip()

        try:
            with transaction.atomic():
                voucher = WarehouseVoucher(
                    cafe=cafe,
                    voucher_type=voucher_type,
                    status='draft' if action == 'draft' else 'submitted',
                    voucher_number=next_voucher_number(cafe.id, voucher_type),
                    source_warehouse=src_wh,
                    destination_warehouse=dst_wh,
                    date=vdate,
                    description=description,
                    created_by=request.user,
                )
                try:
                    voucher.full_clean(exclude=['voucher_number', 'status', 'created_by', 'approver', 'approved_at'])
                except ValidationError as exc:
                    raise VoucherValidationError(f'سند ناسازگار است: {exc}')
                voucher.save()

                for line in lines:
                    WarehouseVoucherItem.objects.create(voucher=voucher, **line)

                if action != 'draft':
                    if voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES:
                        # ثبت شد؛ نهایی‌سازی بعد از تأیید مدیر
                        pass
                    else:
                        finalize_voucher(voucher, finalized_by=request.user)
        except InventoryServiceError as exc:
            messages.error(request, f'حواله ثبت نشد: {exc}')
            return _render_voucher_form(request, cafe, voucher_type, src_wh or dst_wh,
                                        {'form_errors': [str(exc)]})

        if action == 'draft':
            messages.success(request, f'پیش‌نویس حواله {voucher.voucher_number} ثبت شد.')
        elif voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES:
            messages.success(request, f'حوالهٔ {voucher.voucher_number} ثبت شد و در انتظار تأیید مدیر است.')
        else:
            messages.success(request, f'حوالهٔ {voucher.voucher_number} نهایی‌سازی شد و اثر آن روی موجودی اعمال شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return _render_voucher_form(request, cafe, voucher_type, None)


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo', 'finance_manager', 'accounting'])
def voucher_detail(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """جزئیات حواله: سند، اقلام، اثر روی موجودی، خط زمانی، سندِ منبع و اقدامات"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    items = voucher.items.select_related(
        'product', 'product_brand', 'unit', 'stock_item__warehouse').all()
    movements = StockTransaction.objects.filter(voucher=voucher).select_related(
        'stock_item__product', 'stock_item__warehouse', 'created_by').order_by('created_at')

    is_manager = request.user.role in ('manager', 'admin', 'ceo')
    return render(request, 'stock/voucher_detail.html', {
        'voucher': voucher,
        'items': items,
        'movements': movements,
        'can_edit': (voucher.status == 'draft'
                     and (voucher.created_by_id == request.user.id or is_manager)),
        'can_cancel': voucher.status in ('draft', 'submitted'),
        'can_approve': (is_manager and voucher.status == 'submitted'
                        and (voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
                             or voucher.requires_approval)),
        'can_reverse': (voucher.status == 'finalized' and not voucher.reversed_at
                        and not voucher.reverses_id),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo'])
def voucher_edit(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """ویرایش پیش‌نویس حواله — فقط draft و فقط توسط ایجادکننده (یا مدیر)"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    is_manager = request.user.role in ('manager', 'admin', 'ceo')
    if voucher.status != 'draft':
        messages.error(request, 'فقط پیش‌نویس قابل ویرایش است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    if voucher.created_by_id != request.user.id and not is_manager:
        raise Http404('فقط ایجادکنندهٔ پیش‌نویس می‌تواند آن را ویرایش کند')

    if request.method == 'POST':
        try:
            src_id = int(request.POST.get('source_warehouse')) if request.POST.get('source_warehouse') else None
            dst_id = int(request.POST.get('dest_warehouse')) if request.POST.get('dest_warehouse') else None
        except (ValueError, TypeError):
            src_id = dst_id = None
        src_wh = get_scoped_object_or_404(Warehouse, request, id=src_id) if src_id else None
        dst_wh = get_scoped_object_or_404(Warehouse, request, id=dst_id) if dst_id else None

        lines, errors = _parse_voucher_lines(request, cafe, voucher.voucher_type)
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('voucher_edit', cafe_code=cafe.code, voucher_id=voucher.id)

        date_str = request.POST.get('date', '').strip()
        vdate = timezone.now()
        if date_str:
            try:
                vdate = timezone.make_aware(datetime.strptime(date_str, '%Y-%m-%dT%H:%M'))
            except Exception:
                pass

        with transaction.atomic():
            voucher.source_warehouse = src_wh
            voucher.destination_warehouse = dst_wh
            voucher.date = vdate
            voucher.description = request.POST.get('description', '').strip()
            voucher.save()
            voucher.items.all().delete()
            for line in lines:
                WarehouseVoucherItem.objects.create(voucher=voucher, **line)

        messages.success(request, f'پیش‌نویس {voucher.voucher_number} به‌روزرسانی شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    items = voucher.items.select_related('product', 'product_brand', 'unit').all()
    return _render_voucher_form(request, cafe, voucher.voucher_type,
                                voucher.source_warehouse or voucher.destination_warehouse,
                                {'editing': True, 'edit_voucher': voucher,
                                 'edit_items': items, 'edit_date': voucher.date})


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo'])
def voucher_cancel(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """لغو پیش‌نویس یا حوالهٔ ثبت‌شده (پیش از نهایی‌سازی)"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'متد نامعتبر'}, status=405)
    if voucher.status not in ('draft', 'submitted'):
        messages.error(request, 'فقط پیش‌نویس یا حوالهٔ ثبت‌شدهٔ نهایی‌نشده قابل لغو است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    voucher.status = 'cancelled'
    voucher.cancelled_at = timezone.now()
    voucher.save()
    messages.success(request, f'حوالهٔ {voucher.voucher_number} لغو شد.')
    return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)


@custom_login_required
@role_required(['manager', 'admin', 'ceo'])
def voucher_approve(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """تأیید و نهایی‌سازی حوالهٔ نیازمندِ تأیید (تعدیل) — اقدامِ مدیر = تأیید + اثر فوری"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    if voucher.status != 'submitted' or not (
            voucher.voucher_type in WarehouseVoucher.APPROVAL_REQUIRED_TYPES
            or voucher.requires_approval):
        messages.error(request, 'این حواله در وضعیت نیازمند تأیید نیست.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    if request.method == 'POST':
        try:
            with transaction.atomic():
                v = WarehouseVoucher.objects.select_for_update().get(pk=voucher.pk)
                v.status = 'approved'
                v.approver = request.user
                v.approved_at = timezone.now()
                v.save()
                finalize_voucher(v, finalized_by=request.user)
        except InventoryServiceError as exc:
            messages.error(request, f'تأیید انجام نشد: {exc}')
            return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
        messages.success(request, f'حوالهٔ {voucher.voucher_number} تأیید و نهایی‌سازی شد — اثر روی موجودی اعمال شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return render(request, 'stock/voucher_approve.html', {
        'voucher': voucher,
        'items': voucher.items.select_related('product', 'product_brand', 'unit').all(),
        'cafe_code': cafe.code if cafe else '',
    })


@custom_login_required
@role_required(['store', 'purchasing', 'manager', 'admin', 'ceo'])
def voucher_reverse(request: HttpRequest, voucher_id: int) -> HttpResponse:
    """برگشت حوالهٔ نهایی‌شده — دلیلِ اجباری؛ حوالهٔ آینه‌ای ساخته و نهایی می‌شود"""
    cafe = get_cafe_from_request(request)
    voucher = get_scoped_object_or_404(WarehouseVoucher, request, id=voucher_id)
    if voucher.reverses_id:
        messages.error(request, 'حوالهٔ برگشتی قابل برگشت نیست — حوالهٔ اصلی را برگشت بزنید.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)
    if voucher.status != 'finalized':
        messages.error(request, 'فقط حوالهٔ نهایی‌شده قابل برگشت است.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'دلیل برگشت الزامی است — بدون دلیل برگشت ثبت نمی‌شود.')
            return render(request, 'stock/voucher_reverse.html', {
                'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})
        try:
            reversal = reverse_voucher(voucher, created_by=request.user, description=reason)
        except InventoryServiceError as exc:
            messages.error(request, f'برگشت انجام نشد: {exc}')
            return render(request, 'stock/voucher_reverse.html', {
                'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})
        messages.success(request, f'حوالهٔ {voucher.voucher_number} برگشت خورد — حوالهٔ برگشتی {reversal.voucher_number} نهایی شد.')
        return redirect('voucher_detail', cafe_code=cafe.code, voucher_id=voucher.id)

    return render(request, 'stock/voucher_reverse.html', {
        'voucher': voucher, 'cafe_code': cafe.code if cafe else ''})


