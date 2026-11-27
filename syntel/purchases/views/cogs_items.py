from __future__ import annotations

from ..models import Category
from ..models import Department
from ..models import KukerItem
from ..models import KukerRecipeLine
from ..models import MenuSection
from ..models import Product
from ..models import ProductPriceEntry
from ..services.menu_cogs import SHEET_LABELS
from ..services.menu_cogs import ensure_default_menu_sections
from ..services.menu_cogs import record_cost_snapshots_for_cafe
from ..services.menu_cogs import section_for_sheet
from ..services.product_sync import sync_raw_material
from ..services.units import is_bigger_than_smallest
from ..services.units import normalize_package_price
from ..services.units import normalize_to_smallest_unit
from ..services.units import smallest_unit_of
from ..services.units import units_are_compatible
from .cogs_helpers import KIND_LABELS
from .cogs_helpers import MENU_CHOICES
from .cogs_helpers import PREP_SECTION_NAMES
from .cogs_helpers import UNIT_CHOICES
from .cogs_helpers import _as_int
from .cogs_helpers import _back
from .cogs_helpers import _to_decimal
from .cogs_helpers import _unique_key
from .cogs_helpers import _usage_location_choices
from .cogs_helpers import _wants_json
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import role_required
from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_HALF_UP
from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST


#: گزینه‌های «واحد خرید» در فرم ورود قیمت.
#: `unit` واحدی است که کاربر قیمت را برای آن وارد می‌کند؛
#: `package` یعنی «بستهٔ دلخواه» و اندازهٔ بسته را جدا می‌پرسیم.
PRICE_UNIT_OPTIONS = [
    {'value': 'kg',      'label': '1 کیلوگرم',   'unit': 'کیلوگرم'},
    {'value': 'gram',    'label': '1 گرم',       'unit': 'گرم'},
    {'value': 'liter',   'label': '1 لیتر',      'unit': 'لیتر'},
    {'value': 'mil',     'label': '1 میل',       'unit': 'میل'},
    {'value': 'piece',   'label': '1 عدد',       'unit': 'عدد'},
    {'value': 'package', 'label': 'بستهٔ دلخواه', 'unit': ''},
]


#: واحد پایهٔ ماده اولیه همیشه «کوچک‌ترین واحد» است.
BASE_UNIT_CHOICES = ['گرم', 'میل', 'عدد', 'پرس', 'اسلایس']


def _fa_num(value, places=4):
    """عدد → رشته با جداکنندهٔ هزارگان، ارقام انگلیسی و بدون صفر انتهایی."""
    if value is None or value == '':
        return '—'
    try:
        quant = Decimal(1).scaleb(-int(places))
        dec = Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    text = format(dec.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    whole, _, frac = text.partition('.')
    if whole.lstrip('-').isdigit():
        whole = f'{int(whole):,}'
    text = f'{whole}.{frac}' if frac else whole
    return text


def _resolve_price_input(amount, unit_key, package_size=None, package_unit=None):
    """ورودی کاربر (مبلغ + واحد خرید) → قیمت به ازای کوچک‌ترین واحد.

    ``unit_key`` یا یکی از کلیدهای :data:`PRICE_UNIT_OPTIONS` است (kg/gram/…)
    یا برچسب خام فارسی (برای لینک‌ها و فرم‌های قدیمی). خروجی ``None`` یعنی
    ورودی نامعتبر است.
    """
    price = _to_decimal(amount)
    if price is None or price < 0:
        return None

    key = (unit_key or '').strip()
    option = next((o for o in PRICE_UNIT_OPTIONS if o['value'] == key), None)

    if option is None:                      # برچسب خام: «کیلوگرم»، «گرم»، ...
        normalized, smallest = normalize_to_smallest_unit(price, key)
        return {
            'entered_amount': price,
            'entered_unit': key,
            'package_size': None,
            'package_unit': '',
            'price': normalized,
            'smallest_unit': smallest or '',
        }

    if option['value'] == 'package':
        size = _to_decimal(package_size)
        base = (package_unit or '').strip()[:20]
        if size is None or size <= 0 or not base:
            return None
        normalized, smallest = normalize_package_price(price, size, base)
        if normalized is None:
            return None
        return {
            'entered_amount': price,
            'entered_unit': f'بستهٔ {_fa_num(size, 3)} {base}',
            'package_size': size,
            'package_unit': base,
            'price': normalized,
            'smallest_unit': smallest or '',
        }

    normalized, smallest = normalize_to_smallest_unit(price, option['unit'])
    return {
        'entered_amount': price,
        'entered_unit': option['unit'],
        'package_size': None,
        'package_unit': '',
        'price': normalized,
        'smallest_unit': smallest or '',
    }


def _price_preview(resolved, item_name=''):
    """پیش‌نمایش «چه چیزی وارد شد ← چه چیزی ذخیره می‌شود»."""
    if resolved is None:
        return {'ok': False, 'error': 'قیمت یا واحد خرید معتبر نیست.'}

    amount = resolved['entered_amount']
    if resolved['package_size']:
        entered = (f'{_fa_num(amount)} ریال برای بستهٔ '
                   f'{_fa_num(resolved["package_size"], 3)} {resolved["package_unit"]}')
    else:
        entered = f'{_fa_num(amount)} ریال برای 1 {resolved["entered_unit"]}'

    stored = f'{_fa_num(resolved["price"])} ریال به ازای هر 1 {resolved["smallest_unit"]}'
    return {
        'ok': True,
        'item': item_name,
        'entered_amount': str(amount),
        'entered_unit': resolved['entered_unit'],
        'package_size': str(resolved['package_size']) if resolved['package_size'] else '',
        'package_unit': resolved['package_unit'],
        'stored_price': str(resolved['price']),
        'smallest_unit': resolved['smallest_unit'],
        'entered_text': entered,
        'stored_text': stored,
        'preview_text': f'قیمت وارد شده: {entered} ← ثبت به صورت {stored}',
        'needs_division': bool(is_bigger_than_smallest(resolved['entered_unit'])),
    }


def _product_usage_info(product):
    """برگرداندن (usage_location_str, usage_label_str, usage_locations_list) برای محصول."""
    if product is None:
        return 'general', 'عمومی', ['general']
    depts = list(product.departments.all())
    if depts:
        names = [d.name for d in depts]
        return (names[0] if len(names) == 1 else 'multi'), '، '.join(names), names
    ul = product.usage_location or 'general'
    if ul == 'general':
        return 'general', 'عمومی کل', ['general']
    if ul == 'general_kitchen_bar':
        return 'general_kitchen_bar', 'عمومی آشپزخانه و بار', ['general_kitchen_bar']
    if ul == 'general_other':
        return 'general_other', 'عمومی سایر بخش‌ها', ['general_other']
    if ul == 'none':
        return 'none', 'بدون بخش', []
    mapped = Product.OLD_USAGE_KEY_MAP.get(ul, ul)
    return ul, mapped, [mapped]


def _category_options(cafe):
    """دسته‌بندی‌های کافه که روی مواد اولیه استفاده شده‌اند یا مواد غذایی‌اند."""
    return list(Category.objects.filter(cafe=cafe, is_active=True)
                .order_by('name').values('id', 'name', 'requires_recipe'))


def _create_raw_material(cafe, data, user=None):
    """تعریف ماده اولیه: یک `Product` و یک `KukerItem(kind='raw')` با هم.

    `data` می‌تواند request.POST یا یک dict باشد. کلیدها:
      name, code, group, category_id, category_name, usage_location,
      base_unit, price, price_unit, package_size, package_unit, note

    خروجی ``(item, product, error)``؛ در صورت خطا `item` و `product` تهی‌اند.
    """
    name = (data.get('name') or '').strip()[:200]
    if not name:
        return None, None, 'نام ماده اولیه الزامی است.'
    if KukerItem.objects.filter(cafe=cafe, name=name).exists():
        return None, None, f'کالایی با نام «{name}» از قبل در این کافه وجود دارد.'
    if Product.objects.filter(cafe=cafe, name=name).exists():
        return None, None, f'محصولی با نام «{name}» از قبل در انبار این کافه وجود دارد.'

    # ---------- دسته‌بندی: انتخاب موجود یا ساخت درجا ----------
    category = None
    category_id = _as_int(data.get('category_id') or data.get('category'))
    if category_id:
        category = Category.objects.filter(cafe=cafe, pk=category_id).first()
    category_name = (data.get('category_name') or '').strip()[:100]
    if category is None and category_name:
        category, _created = Category.objects.get_or_create(
            cafe=cafe, name=category_name, parent=None,
            defaults={'requires_recipe': True})

    # ---------- واحد پایه (کوچک‌ترین واحد) ----------
    base_unit = (data.get('base_unit') or '').strip()[:20]
    # `unit` نام قدیمی همان واحد خرید است (فرم‌های قبل از این تغییر)
    price_unit = (data.get('price_unit') or data.get('unit') or '').strip()
    if not base_unit:
        # اگر واحد پایه نیامده باشد، از واحد خرید حدس می‌زنیم (کیلو → گرم)
        if price_unit == 'package':
            base_unit = smallest_unit_of(data.get('package_unit') or '')
        else:
            option = next((o for o in PRICE_UNIT_OPTIONS if o['value'] == price_unit), None)
            base_unit = smallest_unit_of(option['unit'] if option else price_unit)
    base_unit = base_unit or 'گرم'

    raw_price = data.get('price')
    has_price = raw_price is not None and str(raw_price).strip() != ''

    resolved = None
    if has_price:
        resolved = _resolve_price_input(
            raw_price, price_unit or base_unit,
            data.get('package_size'), data.get('package_unit'))
        if resolved is None:
            return None, None, 'قیمت یا واحد خرید معتبر نیست.'
        if not units_are_compatible(base_unit, resolved['smallest_unit']):
            return None, None, ('واحد پایه با واحد خرید هم‌خوان نیست: '
                                f'«{base_unit}» در مقابل «{resolved["entered_unit"]}».')

    raw_locs = (data.getlist('usage_locations') if hasattr(data, 'getlist')
                else data.get('usage_locations')) or []
    if isinstance(raw_locs, str):
        raw_locs = [raw_locs]
    if not raw_locs and data.get('usage_location'):
        raw_locs = [data.get('usage_location')]

    cleaned_locs = [str(x).strip() for x in raw_locs if str(x).strip()]
    depts_by_name = {
        d.name.strip(): d
        for d in Department.objects.filter(cafe=cafe, is_active=True).order_by('name')
    }
    matched_depts = []
    if cleaned_locs and not any(x in ('general', 'general_kitchen_bar', 'general_other') for x in cleaned_locs):
        for val in cleaned_locs:
            dep = depts_by_name.get(val)
            if dep is None and val.isdigit():
                dep = next((d for d in depts_by_name.values() if d.id == int(val)), None)
            if dep is not None and dep not in matched_depts:
                matched_depts.append(dep)

    if len(matched_depts) == 1:
        usage_location = matched_depts[0].name[:20]
    elif len(matched_depts) > 1:
        usage_location = 'multi'
    else:
        usage_location = next(
            (x for x in cleaned_locs if x in ('general', 'general_kitchen_bar', 'general_other')),
            'general',
        )

    with transaction.atomic():
        product = Product.objects.create(
            cafe=cafe, name=name, category=category,
            usage_location=usage_location,
            smallest_unit=base_unit,
            base_unit_price=resolved['price'] if resolved else None,
            is_active=True, is_complete=True,
        )
        if matched_depts:
            product.departments.set(matched_depts)
        item = KukerItem.objects.create(
            cafe=cafe, name=name, key=_unique_key(name, cafe), kind=KukerItem.KIND_RAW,
            out_unit=base_unit,
            price=resolved['price'] if resolved else None,
            category=category.name if category else '',
            code=(data.get('code') or '').strip()[:30],
            group=(data.get('group') or '').strip()[:120],
            product=product, is_custom=True,
        )
        # قیمت/واحد بین Product و KukerItem از همین‌جا هم‌گام می‌شود
        sync_raw_material(item)
        if resolved:
            ProductPriceEntry.objects.create(
                product=product, cafe=cafe,
                entered_amount=resolved['entered_amount'],
                entered_unit=resolved['entered_unit'],
                package_size=resolved['package_size'],
                package_unit=resolved['package_unit'],
                price_per_smallest_unit=resolved['price'],
                smallest_unit=resolved['smallest_unit'],
                note=(data.get('note') or '').strip()[:255],
                recorded_by=user if getattr(user, 'pk', None) else None,
            )

    if resolved:
        record_cost_snapshots_for_cafe(cafe, note=f'قیمت اولیهٔ «{name}»')
    return item, product, None


def _save_price_entry(cafe, item, resolved, user=None, note=''):
    """ثبت قیمت یک ماده اولیه در هر دو طرف + تاریخچه + عکس‌برداری بها."""
    if resolved is None:
        return None
    product = item.product

    with transaction.atomic():
        item.price = resolved['price']
        item.out_unit = resolved['smallest_unit'] or item.out_unit
        item.save(update_fields=['price', 'out_unit'])
        # هم‌گام‌سازی با کالای انبار (اگر product نداشت، همین‌جا ساخته می‌شود)
        _item, product = sync_raw_material(item)

        if product is not None:
            entry = ProductPriceEntry.objects.create(
                product=product, cafe=cafe,
                entered_amount=resolved['entered_amount'],
                entered_unit=resolved['entered_unit'],
                package_size=resolved['package_size'],
                package_unit=resolved['package_unit'],
                price_per_smallest_unit=resolved['price'],
                smallest_unit=resolved['smallest_unit'],
                note=(note or '').strip()[:255],
                recorded_by=user if getattr(user, 'pk', None) else None,
            )
        else:
            entry = None

    record_cost_snapshots_for_cafe(cafe, note=f'تغییر قیمت «{item.name}»')
    return entry


def _create_menu_item(cafe, data):
    """ساخت غذای منو یا نیمه‌آماده، با بخش موجود یا بخش تازه‌ساخته.

    خروجی ``(item, error)``.
    """
    kind = data.get('kind')
    if kind not in (KukerItem.KIND_DISH, KukerItem.KIND_PREP):
        kind = KukerItem.KIND_DISH

    name = (data.get('name') or '').strip()[:200]
    if not name:
        return None, 'نام آیتم الزامی است.'
    if KukerItem.objects.filter(cafe=cafe, name=name).exists():
        return None, f'آیتمی با نام «{name}» از قبل وجود دارد.'

    out_qty = _to_decimal(data.get('out_qty')) or Decimal('1')
    if out_qty <= 0:
        return None, 'مقدار خروجی باید بزرگ‌تر از صفر باشد.'
    out_unit = (data.get('out_unit') or '').strip()[:20] or (
        'پرس' if kind == KukerItem.KIND_DISH else 'گرم')

    section = None
    section_id = _as_int(data.get('section_id'))
    section_name = (data.get('section_name') or '').strip()[:120]

    if kind == KukerItem.KIND_PREP:
        ensure_default_menu_sections(cafe)
        fixed = MenuSection.objects.filter(
            cafe=cafe, kind=MenuSection.KIND_PREP,
            name__in=PREP_SECTION_NAMES).order_by('display_order', 'id')
        if section_id:
            section = fixed.filter(pk=section_id).first()
            if section is None:
                return None, 'نیمه‌آماده فقط در «آماده‌سازی آشپزخانه» یا «آماده‌سازی بار» ساخته می‌شود.'
        elif section_name:
            section = fixed.filter(name=section_name).first()
            if section is None:
                return None, 'نیمه‌آماده فقط در «آماده‌سازی آشپزخانه» یا «آماده‌سازی بار» ساخته می‌شود.'
        else:
            section = fixed.first()
    else:
        if section_id:
            section = MenuSection.objects.filter(
                cafe=cafe, pk=section_id, kind=MenuSection.KIND_DISH).first()
            if section is None:
                return None, 'بخش انتخاب‌شده معتبر نیست.'
        elif section_name:
            section, _created = MenuSection.objects.get_or_create(
                cafe=cafe, name=section_name, kind=MenuSection.KIND_DISH,
                defaults={'display_order': 60, 'is_active': True})

    sheet = data.get('sheet') if data.get('sheet') in MENU_CHOICES else ''
    if section is None and kind == KukerItem.KIND_DISH and sheet:
        # مسیر قدیمی صفحهٔ ۱: بخش از روی برگهٔ منو حدس زده می‌شود
        section = section_for_sheet(cafe, sheet, kind)
    if not sheet and section is not None:
        sheet = next((k for k, v in SHEET_LABELS.items() if v == section.name), '')

    item = KukerItem.objects.create(
        cafe=cafe, name=name, key=_unique_key(name, cafe), kind=kind,
        sheet=sheet, section=section, out_qty=out_qty, out_unit=out_unit,
        is_custom=True)
    return item, None


@custom_login_required
@role_required(['admin'])
def cogs_manage_items(request):
    """صفحهٔ ۲ — افزودن آیتم، مدیریت بخش‌ها و ورود قیمت مواد اولیه."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')

    if request.method == 'POST':
        action = (request.POST.get('action') or 'add').strip()
        if action == 'price':
            return _update_item_price(request, cafe)
        if action == 'item':
            return _create_item_from_page(request, cafe)
        return _create_raw_item(request, cafe)

    ensure_default_menu_sections(cafe)

    query = (request.GET.get('q') or '').strip()
    state = (request.GET.get('state') or 'all').strip()
    category_filter = (request.GET.get('cat') or '').strip()
    if category_filter and category_filter != 'none' and not category_filter.isdigit():
        category_filter = ''      # شناسهٔ نامعتبر نباید صفحه را ۵۰۰ کند

    items_qs = KukerItem.objects.filter(cafe=cafe, kind=KukerItem.KIND_RAW)
    if query:
        items_qs = items_qs.filter(Q(name__icontains=query) |
                                   Q(product__category__name__icontains=query) |
                                   Q(group__icontains=query) |
                                   Q(code__icontains=query))
    if state == 'priced':
        items_qs = items_qs.filter(price__isnull=False)
    elif state == 'unpriced':
        items_qs = items_qs.filter(price__isnull=True)
    if category_filter:
        if category_filter == 'none':
            items_qs = items_qs.filter(product__category__isnull=True)
        else:
            items_qs = items_qs.filter(product__category_id=category_filter)

    # چند رسپی مختلف از هر ماده استفاده می‌کنند (نه تعداد سطرها)
    used_counts = dict(
        KukerRecipeLine.objects.filter(material__cafe=cafe)
        .values('material_id')
        .annotate(n=Count('item_id', distinct=True))
        .values_list('material_id', 'n')
    )
    used_raw_ids = set(
        KukerRecipeLine.objects
        .filter(material__cafe=cafe, material__kind=KukerItem.KIND_RAW)
        .values_list('material_id', flat=True)
    )

    rows = []
    for it in (items_qs.select_related('product', 'product__category')
               .prefetch_related('product__departments').order_by('name')):
        product = it.product
        usage_loc, usage_label, _usage_locs = _product_usage_info(product)
        rows.append({
            'obj': it,
            'product': product,
            'category': (product.category.name if product and product.category
                         else (it.category or '')),
            'usage_location': usage_loc,
            'usage_label': usage_label,
            'base_unit': it.out_unit or (product.smallest_unit if product else '') or '—',
            # فقط بهای خود قلم — همان عددی که موتور بهای تمام شده استفاده می‌کند
            'price': it.price,
            'used_in': used_counts.get(it.id, 0),
            'is_linked': product is not None,
        })

    # تب‌های دسته‌بندی — فقط دسته‌هایی که واقعاً روی مواد اولیه نشسته‌اند
    cat_counts = {}
    raw_for_cats = (KukerItem.objects.filter(cafe=cafe, kind=KukerItem.KIND_RAW)
                    .select_related('product', 'product__category'))
    for it in raw_for_cats:
        if it.product and it.product.category_id:
            key = str(it.product.category_id)
            cat_counts.setdefault(key, {'id': it.product.category_id,
                                        'name': it.product.category.name, 'count': 0})
            cat_counts[key]['count'] += 1
    categories = sorted(cat_counts.values(), key=lambda c: c['name'])

    all_raw = KukerItem.objects.filter(cafe=cafe, kind=KukerItem.KIND_RAW)
    total = all_raw.count()
    priced = all_raw.filter(price__isnull=False).count()

    dish_sections = list(MenuSection.objects.filter(
        cafe=cafe, kind=MenuSection.KIND_DISH).order_by('display_order', 'name'))
    prep_sections = list(MenuSection.objects.filter(
        cafe=cafe, kind=MenuSection.KIND_PREP,
        name__in=PREP_SECTION_NAMES).order_by('display_order', 'id'))

    auto_open = request.GET.get('recipe')
    auto_open_item = None
    if auto_open and str(auto_open).isdigit():
        item = KukerItem.objects.filter(
            cafe=cafe, pk=auto_open).exclude(kind=KukerItem.KIND_RAW).first()
        auto_open_item = item.id if item else None

    context = {
        'cogs_page': 'items',
        'rows': rows,
        'items': rows,
        'query': query,
        'state': state,
        'category_filter': category_filter,
        'categories': categories,
        'dish_sections': dish_sections,
        'prep_sections': prep_sections,
        'prep_section_names': PREP_SECTION_NAMES,
        'usage_locations': _usage_location_choices(cafe),
        'category_options': _category_options(cafe),
        'price_unit_options': PRICE_UNIT_OPTIONS,
        'base_unit_choices': BASE_UNIT_CHOICES,
        'unit_choices': UNIT_CHOICES,
        'menu_choices': [(s, s.strip()) for s in MENU_CHOICES],
        'auto_open_item': auto_open_item,
        'stats': {
            'total': total,
            'priced': priced,
            'unpriced': total - priced,
            'used': len(used_raw_ids),
            'dishes': KukerItem.objects.filter(
                cafe=cafe, kind=KukerItem.KIND_DISH).count(),
            'preps': KukerItem.objects.filter(
                cafe=cafe, kind=KukerItem.KIND_PREP).count(),
            'sections': len(dish_sections) + len(prep_sections),
        },
    }
    return render(request, 'cogs_manage_items.html', context)


def _create_item_from_page(request, cafe):
    """POST غیر-AJAX ساخت آیتم منو/نیمه‌آماده از فرم صفحهٔ ۲."""
    item, error = _create_menu_item(cafe, request.POST)
    if error:
        messages.error(request, error)
        return _back(request, 'cogs_manage_items')

    messages.success(
        request,
        f'«{item.name}» ساخته شد؛ حالا مواد اولیه‌اش را وارد کنید.')
    if _wants_json(request):
        return JsonResponse({'ok': True, 'item': {
            'id': item.id, 'name': item.name, 'kind': item.kind,
            'section_id': item.section_id,
            'recipe_url': reverse('cogs_recipe_api',
                                  kwargs={'cafe_code': cafe.code, 'item_id': item.id}),
        }})
    code = getattr(cafe, 'code', None)
    if code:
        return redirect(f"{reverse('cogs_manage_items', kwargs={'cafe_code': code})}"
                        f'?recipe={item.id}')
    return _back(request, 'cogs_manage_items')


def _create_raw_item(request, cafe):
    """تعریف کالای جدید (ماده اولیه) + هم‌گام‌سازی با کالای انبار."""
    item, product, error = _create_raw_material(cafe, request.POST, request.user)
    if error:
        if _wants_json(request):
            return JsonResponse({'ok': False, 'error': error}, status=400)
        messages.error(request, error)
        return _back(request, 'cogs_manage_items')

    messages.success(
        request,
        f'کالای «{item.name}» با واحد «{item.out_unit}» تعریف شد.')
    if _wants_json(request):
        return JsonResponse({'ok': True, 'item': _raw_item_payload(item, product)})
    return _back(request, 'cogs_manage_items')


def _update_item_price(request, cafe):
    """به‌روزرسانی قیمت یک ماده اولیه + هم‌گام‌سازی انبار + ثبت تاریخچه بها."""
    item_pk = _as_int(request.POST.get('item_id'))
    item = (
        KukerItem.objects.filter(cafe=cafe, pk=item_pk, kind=KukerItem.KIND_RAW).first()
        if item_pk else None
    )
    if item is None:
        if _wants_json(request):
            return JsonResponse({'ok': False, 'error': 'کالای موردنظر پیدا نشد.'},
                                status=404)
        messages.error(request, 'کالای موردنظر پیدا نشد.')
        return _back(request, 'cogs_manage_items')

    resolved = _resolve_price_input(
        request.POST.get('price'), request.POST.get('unit') or item.out_unit or 'گرم',
        request.POST.get('package_size'), request.POST.get('package_unit'))
    if resolved is None:
        if _wants_json(request):
            return JsonResponse({'ok': False, 'error': 'قیمت یا واحد خرید معتبر نیست.'},
                                status=400)
        messages.error(request, f'قیمت «{item.name}» معتبر نیست.')
        return _back(request, 'cogs_manage_items')

    if not units_are_compatible(item.out_unit, resolved['smallest_unit']):
        error = (f'واحد خرید «{resolved["entered_unit"]}» با واحد پایهٔ '
                 f'«{item.out_unit}» این کالا هم‌خوان نیست.')
        if _wants_json(request):
            return JsonResponse({'ok': False, 'error': error}, status=400)
        messages.error(request, error)
        return _back(request, 'cogs_manage_items')

    old_price = item.price
    _save_price_entry(cafe, item, resolved, request.user)
    item.refresh_from_db()

    changed = old_price != item.price
    verb = 'به‌روز شد' if changed else 'تغییری نکرد'
    messages.success(
        request,
        f'قیمت «{item.name}» {verb} — {_fa_num(item.price)} ریال به ازای هر 1 '
        f'{item.out_unit or "واحد"}.')
    if _wants_json(request):
        return JsonResponse({
            'ok': True,
            'item': _raw_item_payload(item, item.product),
            'changed': changed,
            'preview': _price_preview(resolved, item.name),
        })
    return _back(request, 'cogs_manage_items')


def _raw_item_payload(item, product):
    return {
        'id': item.id,
        'name': item.name,
        'kind': item.kind,
        'price': str(item.price) if item.price is not None else '',
        'base_unit': item.out_unit or '',
        'price_display': _fa_num(item.price) if item.price is not None else '',
        'product_id': product.id if product else None,
        'category': product.category.name if product and product.category else '',
        'usage_location': product.usage_location if product else 'general',
    }


@custom_login_required
@role_required(['admin'])
def cogs_price_preview(request):
    """پیش‌نمایش زندهٔ تبدیل قیمت، بدون ذخیره‌کردن چیزی."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'ok': False, 'error': 'کافهٔ فعال یافت نشد.'}, status=404)
    resolved = _resolve_price_input(
        request.GET.get('price'), request.GET.get('unit') or 'kg',
        request.GET.get('package_size'), request.GET.get('package_unit'))
    payload = _price_preview(resolved, (request.GET.get('name') or '').strip())
    return JsonResponse(payload, status=200 if payload.get('ok') else 400)


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_raw_material_create(request):
    """ساخت ماده اولیه (Product + KukerItem) از مودال صفحهٔ ۲."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'ok': False, 'error': 'کافهٔ فعال یافت نشد.'}, status=404)
    item, product, error = _create_raw_material(cafe, request.POST, request.user)
    if error:
        return JsonResponse({'ok': False, 'error': error}, status=400)
    return JsonResponse({'ok': True, 'item': _raw_item_payload(item, product)})


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_price_entry(request):
    """ثبت قیمت ماده اولیه با نرمال‌سازی + تاریخچه + عکس‌برداری بها."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'ok': False, 'error': 'کافهٔ فعال یافت نشد.'}, status=404)
    item_pk = _as_int(request.POST.get('item_id'))
    item = (
        KukerItem.objects.filter(cafe=cafe, pk=item_pk, kind=KukerItem.KIND_RAW).first()
        if item_pk else None
    )
    if item is None:
        return JsonResponse({'ok': False, 'error': 'کالای موردنظر پیدا نشد.'}, status=404)

    resolved = _resolve_price_input(
        request.POST.get('price'), request.POST.get('unit') or item.out_unit or 'گرم',
        request.POST.get('package_size'), request.POST.get('package_unit'))
    if resolved is None:
        return JsonResponse({'ok': False, 'error': 'قیمت یا واحد خرید معتبر نیست.'},
                            status=400)
    if not units_are_compatible(item.out_unit, resolved['smallest_unit']):
        return JsonResponse({'ok': False, 'error':
                             f'واحد خرید با واحد پایهٔ «{item.out_unit}» هم‌خوان نیست.'},
                            status=400)

    entry = _save_price_entry(cafe, item, resolved, request.user)
    item.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'item': _raw_item_payload(item, item.product),
        'preview': _price_preview(resolved, item.name),
        'entry_id': entry.id if entry else None,
    })


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_menu_item_create(request):
    """ساخت غذای منو / نیمه‌آماده و برگرداندن شناسه برای باز کردن ویرایشگر رسپی."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'ok': False, 'error': 'کافهٔ فعال یافت نشد.'}, status=404)
    item, error = _create_menu_item(cafe, request.POST)
    if error:
        return JsonResponse({'ok': False, 'error': error}, status=400)
    return JsonResponse({
        'ok': True,
        'item': {
            'id': item.id,
            'name': item.name,
            'kind': item.kind,
            'kind_label': KIND_LABELS.get(item.kind, ''),
            'section_id': item.section_id,
            'out_qty': str(item.out_qty or 1),
            'out_unit': item.out_unit or '',
        },
        'recipe_url': reverse('cogs_recipe_api',
                              kwargs={'cafe_code': cafe.code, 'item_id': item.id}),
    })


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_item_price_update(request):
    """ویرایش قیمت یک ماده اولیه از صفحهٔ قیمت‌گذاری."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'ok': False, 'error': 'کافهٔ فعال یافت نشد.'}, status=404) if _wants_json(request) else _back(request)
    return _update_item_price(request, cafe)


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_item_add(request):
    """تعریف کالای جدید (ماده اولیه) — نام قدیمی، حالا روی صفحهٔ قیمت‌گذاری."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    return _create_raw_item(request, cafe)


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_menu_item_add(request):
    """تعریف غذای جدید یا نیمه‌آماده جدید در منو."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    name = (request.POST.get('name') or '').strip()
    if not name:
        messages.error(request, 'نام آیتم الزامی است.')
        return _back(request)
    if KukerItem.objects.filter(cafe=cafe, name=name).exists():
        messages.warning(request, f'آیتمی با نام «{name}» از قبل وجود دارد.')
        return _back(request)
    kind = request.POST.get('kind') if request.POST.get('kind') in (
        KukerItem.KIND_DISH, KukerItem.KIND_PREP) else KukerItem.KIND_DISH
    sheet = request.POST.get('sheet') if request.POST.get('sheet') in MENU_CHOICES else MENU_CHOICES[0]
    raw_out_qty = (request.POST.get('out_qty') or '').strip()
    if raw_out_qty:
        out_qty = _to_decimal(raw_out_qty)
        if out_qty is None or out_qty <= 0:
            messages.error(request, 'مقدار خروجی باید بزرگ‌تر از صفر باشد.')
            return _back(request)
    else:
        out_qty = Decimal('1')
    out_unit = (request.POST.get('out_unit') or 'پرس').strip()[:20]
    # بخش منو: بخش دلخواه کاربر ساخته می‌شود، وگرنه از روی برگه منو
    section = None
    section_name = (request.POST.get('section') or '').strip()[:120]
    if section_name:
        section, _ = MenuSection.objects.get_or_create(
            cafe=cafe, name=section_name, kind=kind,
            defaults={'display_order': 60, 'is_active': True})
    else:
        section = section_for_sheet(cafe, sheet, kind)
    KukerItem.objects.create(
        cafe=cafe, name=name, key=_unique_key(name, cafe), kind=kind, sheet=sheet,
        section=section,
        out_qty=out_qty, out_unit=out_unit, is_custom=True)
    messages.success(request, f'آیتم «{name}» تعریف شد؛ با کلیک روی آن رسپی‌اش را وارد کنید.')
    return _back(request)
