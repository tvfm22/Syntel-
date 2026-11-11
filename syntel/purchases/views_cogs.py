# -*- coding: utf-8 -*-
"""بهای تمام شده (Cost of Goods Sold) — سه زیرصفحه، همه با پیشوند کافه.

  ۱) `cogs_menu_list`        — /<cafe>/cogs/             لیست غذاهای منو و نیمه‌آماده‌ها
                                                          + ویرایش رسپی + نمودار تاریخچه بها
  ۲) `cogs_manage_items`     — /<cafe>/cogs/items/       افزودن آیتم و قیمت‌گذاری کالاها
  ۳) `cogs_consumption_calc` — /<cafe>/cogs/consumption/ محاسبه مصرف مواد اولیه در بازه فروش

قواعد ثابت این ماژول:
  * همهٔ کوئری‌ها با کافهٔ فعال محدود می‌شوند (`cafe` از request/session).
  * قیمت مواد اولیه همیشه «به ازای کوچک‌ترین واحد» ذخیره می‌شود (گرم / میل).
  * بهای هر غذا از روی رسپی محاسبه می‌شود (`computed_unit_cost`) و هر تغییرش
    در `KukerItemCostHistory` ثبت می‌گردد.
"""
import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

import jdatetime
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

# Fallback-safe references for COGS menu workbook
MENU_WORKBOOK_PATH = getattr(settings, 'MENU_WORKBOOK_PATH', None)
PROJECT_ROOT = getattr(settings, 'PROJECT_ROOT', None)

from .models import (
    Category, CogsReport, CogsReportLine, Department, KukerItem, KukerRecipeLine,
    MenuSection, Product, ProductPriceEntry,
)
from .services.menu_cogs import (
    SHEET_LABELS, UNCATEGORIZED, MenuGraph, compute_cogs, ensure_default_menu_sections,
    normalize_name, record_cost_snapshots_for_cafe, section_for_sheet,
)
from .services.product_sync import sync_raw_material
from .services.units import (
    is_bigger_than_smallest, normalize_package_price, normalize_to_smallest_unit,
    smallest_unit_of, units_are_compatible,
)
from .views import (
    custom_login_required, get_cafe_from_request, get_scoped_object_or_404, role_required,
)

# بخش‌های ثابت آماده‌سازی — همیشه در صفحه نمایش داده می‌شوند، حتی اگر خالی باشند
PREP_SECTION_NAMES = list(MenuSection.DEFAULT_PREP_SECTIONS)

MENU_CHOICES = ['منو فرنگی اصلی', 'منو اصلی بار', 'رستوران بابا آدم ', 'سفره چی']
UNIT_CHOICES = ['گرم', 'عدد', 'میل', 'پرس', 'اسلایس', 'کیلوگرم', 'لیتر']

KIND_LABELS = {
    KukerItem.KIND_RAW: 'کالا / ماده اولیه',
    KukerItem.KIND_PREP: 'نیمه‌آماده',
    KukerItem.KIND_DISH: 'غذای منو',
}


# ---------------------------------------------------------------------------
# ابزارهای کوچک
# ---------------------------------------------------------------------------
def _to_decimal(value):
    if value is None:
        return None
    text = str(value).strip().replace(',', '').replace('،', '')
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _as_int(value):
    """Positive integer primary key from form input, or None (never raises)."""
    text = str(value or '').strip()
    return int(text) if text.isdigit() else None


def _read_jalali(source, prefix):
    """Read a Jalali date from <prefix>_year/_month/_day fields."""
    try:
        y = int(source.get(f'{prefix}_year', '') or 0)
        m = int(source.get(f'{prefix}_month', '') or 0)
        d = int(source.get(f'{prefix}_day', '') or 0)
    except (TypeError, ValueError):
        return None
    if not (y and m and d):
        return None
    try:
        return jdatetime.date(y, m, d).togregorian()
    except ValueError:
        return None


def _current_jalali_month():
    today = date.today()
    j_today = jdatetime.date.fromgregorian(date=today)
    start = jdatetime.date(j_today.year, j_today.month, 1).togregorian()
    return start, today


def _jalali_text(dt, with_time=False):
    """Jalali string for a (timezone-aware) datetime."""
    try:
        local = timezone.localtime(dt)
    except (ValueError, AttributeError):
        local = dt
    try:
        jd = jdatetime.datetime.fromgregorian(datetime=local)
    except (ValueError, OverflowError):
        return ''
    text = f'{jd.year}/{jd.month:02d}/{jd.day:02d}'
    if with_time:
        text += f' {jd.hour:02d}:{jd.minute:02d}'
    return text


def _back(request, fallback='cogs_menu_list'):
    """Redirect back to a COGS page inside the current cafe."""
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt:
        return redirect(nxt)
    cafe = getattr(request, 'cafe', None) or get_cafe_from_request(request)
    code = getattr(cafe, 'code', None)
    if code:
        try:
            return redirect(reverse(fallback, kwargs={'cafe_code': code}))
        except Exception:  # noqa: BLE001 - بدترین حالت: ریشه سایت
            return redirect('/')
    return redirect('/')


def _wants_json(request):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    return 'application/json' in (request.headers.get('Accept') or '')


def _unique_key(name, cafe=None):
    """Normalized, unique key for a user-created item inside one cafe."""
    base = normalize_name(name)
    if not base:
        base = 'item'
    key = base
    i = 2
    while KukerItem.objects.filter(cafe=cafe, key=key).exists():
        key = f'{base}#{i}'
        i += 1
    return key


def _has_cycle(start_id, cafe=None):
    """True if the recipe graph contains a cycle reachable from start_id."""
    edges = {}
    lines = KukerRecipeLine.objects.all()
    if cafe is not None:
        lines = lines.filter(item__cafe=cafe)
    for item_id, material_id in lines.values_list('item_id', 'material_id'):
        edges.setdefault(item_id, set()).add(material_id)
    stack, seen = [(start_id, {start_id})], set()
    while stack:
        node, path = stack.pop()
        for nxt in edges.get(node, ()):
            if nxt in path:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, path | {nxt}))
    return False


def _item_cost(graph, key, cost_cache):
    """بهای محاسبه‌شده از رسپی؛ اگر رسپی نداشت، بهای اعلامی اکسل."""
    cost = graph.computed_unit_cost(key, _cache=cost_cache)
    if cost is None:
        cost = graph.unit_cost(key)
    return cost


def _material_options(cafe, graph=None, cost_cache=None, exclude_id=None):
    """Ingredient picker payload: raw materials + semi-prepared items."""
    if graph is None:
        graph = MenuGraph.from_db(cafe=cafe)
    if cost_cache is None:
        cost_cache = {}
    options = []
    for key, it in graph.items.items():
        if it['kind'] == KukerItem.KIND_DISH:
            continue
        if exclude_id and it.get('id') == exclude_id:
            continue
        if it['kind'] == KukerItem.KIND_RAW:
            cost = it.get('price')
        else:
            cost = _item_cost(graph, key, cost_cache)
        options.append({
            'id': it.get('id'),
            'name': it['name'],
            'kind': it['kind'],
            'kind_label': KIND_LABELS.get(it['kind'], ''),
            'unit': it.get('out_unit') or '',
            'price': str(it.get('price')) if it.get('price') is not None else '',
            'cost': str(cost) if cost is not None else '',
        })
    options.sort(key=lambda o: (o['kind'] != KukerItem.KIND_RAW, o['name']))
    return options


def _sections_payload(cafe, kind=None):
    qs = MenuSection.objects.filter(cafe=cafe)
    if kind:
        qs = qs.filter(kind=kind)
    return [{'id': s.id, 'name': s.name, 'kind': s.kind,
             'display_order': s.display_order} for s in qs]


def _group_items(rows, sections, kind, fixed_names):
    """Bucket item rows under their MenuSection, in a stable display order.

    Fixed prep sections are always present (even when empty) so the two
    «آماده‌سازی آشپزخانه / بار» blocks never disappear from the page.
    """
    buckets = {}
    for row in rows:
        if row['kind'] != kind:
            continue
        buckets.setdefault(row['section_id'], []).append(row)

    groups, used = [], set()
    for name in fixed_names:
        sec = next((s for s in sections if s.name == name and s.kind == kind), None)
        sid = sec.id if sec else None
        groups.append({'id': sid, 'name': name, 'fixed': True,
                       'items': buckets.pop(sid, [])})
        used.add(sid)

    for sec in sections:
        if sec.kind != kind or sec.id in used:
            continue
        items = buckets.pop(sec.id, [])
        if not items:          # بخش‌های خالی غذای منو نمایش داده نمی‌شوند
            continue
        groups.append({'id': sec.id, 'name': sec.name, 'fixed': False, 'items': items})

    rest = []
    for items in buckets.values():
        rest.extend(items)
    if rest:
        groups.append({'id': None, 'name': 'بدون بخش', 'fixed': False, 'items': rest})

    for g in groups:
        g['count'] = len(g['items'])
        g['total'] = sum((i['cost'] for i in g['items'] if i['has_cost']), Decimal('0'))
        g['priced'] = sum(1 for i in g['items'] if i['has_cost'])
    return groups


# ---------------------------------------------------------------------------
# صفحه ۱ — لیست غذاهای منو و نیمه‌آماده‌ها
# ---------------------------------------------------------------------------
@custom_login_required
@role_required(['admin'])
def cogs_menu_list(request):
    cafe = get_cafe_from_request(request)
    ensure_default_menu_sections(cafe)

    graph = MenuGraph.from_db(cafe=cafe)
    cost_cache = {}
    sections = list(MenuSection.objects.filter(cafe=cafe).order_by('kind', 'display_order', 'name'))

    rows = []
    for key, it in graph.items.items():
        if it['kind'] == KukerItem.KIND_RAW:
            continue
        cost = _item_cost(graph, key, cost_cache)
        rows.append({
            'id': it.get('id'),
            'key': key,
            'name': it['name'],
            'kind': it['kind'],
            'section_id': it.get('section_id'),
            'out_qty': it.get('out_qty'),
            'out_unit': it.get('out_unit') or '',
            'lines_count': len(it.get('lines') or []),
            'cost': cost,
            'has_cost': cost is not None,
            'is_custom': bool(it.get('is_custom')),
        })

    dish_groups = _group_items(rows, sections, KukerItem.KIND_DISH, [])
    prep_groups = _group_items(rows, sections, KukerItem.KIND_PREP, PREP_SECTION_NAMES)

    dish_rows = [r for r in rows if r['kind'] == KukerItem.KIND_DISH]
    prep_rows = [r for r in rows if r['kind'] == KukerItem.KIND_PREP]

    stats = {
        'dishes': len(dish_rows),
        'preps': len(prep_rows),
        'no_recipe': sum(1 for r in rows if not r['lines_count']),
        'unpriced': sum(1 for r in rows if not r['has_cost']),
        'sections': len(sections),
    }

    context = {
        'cogs_page': 'menu',
        'has_menu': bool(rows),
        'dish_groups': dish_groups,
        'prep_groups': prep_groups,
        'stats': stats,
        'sections': sections,
        'sections_json': json.dumps(_sections_payload(cafe), ensure_ascii=False),
        'unit_choices': UNIT_CHOICES,
        'menu_choices': [(s, s.strip()) for s in MENU_CHOICES],
        'prep_section_names': PREP_SECTION_NAMES,
    }
    return render(request, 'cogs_menu_list.html', context)


# ---------------------------------------------------------------------------
# صفحه ۲ — افزودن آیتم و قیمت‌گذاری کالاها
# ---------------------------------------------------------------------------
#: گزینه‌های «واحد خرید» در فرم ورود قیمت.
#: `unit` واحدی است که کاربر قیمت را برای آن وارد می‌کند؛
#: `package` یعنی «بستهٔ دلخواه» و اندازهٔ بسته را جدا می‌پرسیم.
PRICE_UNIT_OPTIONS = [
    {'value': 'kg',      'label': '۱ کیلوگرم',   'unit': 'کیلوگرم'},
    {'value': 'gram',    'label': '۱ گرم',       'unit': 'گرم'},
    {'value': 'liter',   'label': '۱ لیتر',      'unit': 'لیتر'},
    {'value': 'mil',     'label': '۱ میل',       'unit': 'میل'},
    {'value': 'piece',   'label': '۱ عدد',       'unit': 'عدد'},
    {'value': 'package', 'label': 'بستهٔ دلخواه', 'unit': ''},
]

#: واحد پایهٔ ماده اولیه همیشه «کوچک‌ترین واحد» است.
BASE_UNIT_CHOICES = ['گرم', 'میل', 'عدد', 'پرس', 'اسلایس']

_FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def _fa_num(value, places=4):
    """عدد → رشتهٔ فارسی با جداکنندهٔ هزارگان و بدون صفر انتهایی."""
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
    return text.translate(_FA_DIGITS)


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
        entered = (f'{_fa_num(amount)} تومان برای بستهٔ '
                   f'{_fa_num(resolved["package_size"], 3)} {resolved["package_unit"]}')
    else:
        entered = f'{_fa_num(amount)} تومان برای ۱ {resolved["entered_unit"]}'

    stored = f'{_fa_num(resolved["price"])} تومان به ازای هر ۱ {resolved["smallest_unit"]}'
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


def _usage_location_choices(cafe):
    """محل استفادهٔ ماده اولیه: «عمومی» + بخش‌های فعال همین کافه."""
    options, seen = [{'value': 'general', 'label': 'عمومی'}], {'general'}
    for name in (Department.objects.filter(cafe=cafe, is_active=True)
                 .order_by('name').values_list('name', flat=True)):
        value = name.strip()[:20]
        if not value or value in seen:
            continue
        seen.add(value)
        options.append({'value': value, 'label': value})
    return options


def _usage_location_label(value):
    if not value or value == 'general':
        return 'عمومی'
    return value


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

    usage_location = (data.get('usage_location') or 'general').strip()[:20] or 'general'
    allowed = {o['value'] for o in _usage_location_choices(cafe)}
    if usage_location not in allowed:
        usage_location = 'general'

    with transaction.atomic():
        product = Product.objects.create(
            cafe=cafe, name=name, category=category,
            usage_location=usage_location,
            smallest_unit=base_unit,
            base_unit_price=resolved['price'] if resolved else None,
            is_active=True, is_complete=True,
        )
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
    for it in items_qs.select_related('product', 'product__category').order_by('name'):
        product = it.product
        rows.append({
            'obj': it,
            'product': product,
            'category': (product.category.name if product and product.category
                         else (it.category or '')),
            'usage_location': (product.usage_location if product else 'general') or 'general',
            'usage_label': _usage_location_label(
                product.usage_location if product else 'general'),
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
    item = KukerItem.objects.filter(
        cafe=cafe, pk=request.POST.get('item_id'), kind=KukerItem.KIND_RAW).first()
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
        f'قیمت «{item.name}» {verb} — {_fa_num(item.price)} تومان به ازای هر ۱ '
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


# ---------------------------------------------------------------------------
# اندپوینت‌های JSON صفحهٔ ۲ (همه کافه‌محور)
# ---------------------------------------------------------------------------
@custom_login_required
@role_required(['admin'])
def cogs_price_preview(request):
    """پیش‌نمایش زندهٔ تبدیل قیمت، بدون ذخیره‌کردن چیزی."""
    get_cafe_from_request(request)      # فقط برای اطمینان از کافهٔ فعال
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
    item = KukerItem.objects.filter(
        cafe=cafe, pk=request.POST.get('item_id'), kind=KukerItem.KIND_RAW).first()
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
    return _update_item_price(request, cafe)


# ---------------------------------------------------------------------------
# صفحه ۳ — محاسبه میزان مصرف مواد اولیه در یک بازه فروش
# ---------------------------------------------------------------------------
def _consumption_url(request, cafe, report_id=None):
    """آدرس همین صفحه، با حفظ گزارش فعال و فیلترهای انتخاب‌شده."""
    url = reverse('cogs_consumption_calc', kwargs={'cafe_code': cafe.code})
    params = {}
    if report_id:
        params['report'] = report_id
    for key in ('cat', 'dept'):
        value = (request.POST.get(key) or request.GET.get(key) or '').strip()
        if value:
            params[key] = value
    return f'{url}?{urlencode(params)}' if params else url


def _read_sold_qty(source, graph):
    """{dish_key: qty} از فیلدهای qty__<id> — فقط غذاهای فروشنی همین کافه."""
    sold = {}
    for dish_key in graph.dishes():
        value = _to_decimal(source.get(f'qty__{dish_key}'))
        if value and value > 0:
            sold[dish_key] = value
    return sold


def _replace_report_lines(report, sold):
    """سطرهای فروش گزارش را با مقادیر تازه جایگزین می‌کند (بدون تکرار)."""
    report.lines.all().delete()
    CogsReportLine.objects.bulk_create([
        CogsReportLine(report=report, item_id=int(key), qty=qty)
        for key, qty in sold.items()
    ])


def _consumption_groups(cogs, cafe):
    """غذاهای فروشنی، گروه‌بندی‌شده بر اساس «بخش منو»."""
    sections = list(MenuSection.objects.filter(cafe=cafe, kind=MenuSection.KIND_DISH)
                    .order_by('display_order', 'id'))
    buckets = {}
    for row in cogs['dishes']:
        buckets.setdefault(row['item'].get('section_id'), []).append(row)

    groups = []
    for sec in sections:
        rows = buckets.pop(sec.id, None)
        if rows:
            groups.append({'id': sec.id, 'label': sec.name, 'rows': rows})

    leftovers = []
    for rows in buckets.values():
        leftovers.extend(rows)
    if leftovers:
        leftovers.sort(key=lambda r: r['item']['name'])
        groups.append({'id': None, 'label': 'بدون بخش', 'rows': leftovers})
    return groups


def _prep_groups(prep_rows, cafe):
    """نیمه‌آماده‌های مصرف‌شده، جدا برای «آماده‌سازی آشپزخانه» و «آماده‌سازی بار».

    بخش‌های ثابت همیشه می‌آیند (حتی وقتی در این بازه مصرفی نداشته‌اند) تا گزارش
    صریحاً بگوید «بار چیزی مصرف نکرده» و با صفحهٔ ۱ هم‌دست باشد.
    """
    sections = list(MenuSection.objects.filter(cafe=cafe, kind=MenuSection.KIND_PREP)
                    .order_by('display_order', 'id'))
    buckets = {}
    for row in prep_rows:
        buckets.setdefault(row['item'].get('section_id'), []).append(row)

    groups, used = [], set()
    for name in PREP_SECTION_NAMES:
        sec = next((s for s in sections if s.name == name), None)
        sid = sec.id if sec else None
        groups.append({'id': sid, 'label': name, 'fixed': True,
                       'rows': buckets.pop(sid, [])})
        used.add(sid)

    for sec in sections:
        if sec.id in used:
            continue
        rows = buckets.pop(sec.id, [])
        if rows:
            groups.append({'id': sec.id, 'label': sec.name, 'fixed': False, 'rows': rows})

    leftovers = []
    for rows in buckets.values():
        leftovers.extend(rows)
    if leftovers:
        leftovers.sort(key=lambda r: r['cost'], reverse=True)
        groups.append({'id': None, 'label': 'بدون بخش', 'fixed': False, 'rows': leftovers})
    return groups


def _filter_options(raw_rows):
    """گزینه‌های دو فیلتر، فقط از اقلامی که واقعاً در همین بازه مصرف شده‌اند."""
    cats, depts = {}, {}
    for row in raw_rows:
        cid = row['category_id'] or UNCATEGORIZED
        slot = cats.setdefault(cid, {
            'id': cid, 'name': row['category_name'] or 'بدون دسته‌بندی',
            'count': 0, 'cost': Decimal('0')})
        slot['count'] += 1
        slot['cost'] += row['cost']

        dval = row['usage_location'] or 'general'
        slot = depts.setdefault(dval, {
            'value': dval, 'label': _usage_location_label(dval),
            'count': 0, 'cost': Decimal('0')})
        slot['count'] += 1
        slot['cost'] += row['cost']

    cat_list = sorted(cats.values(), key=lambda c: (c['id'] == UNCATEGORIZED, c['name']))
    dept_list = sorted(depts.values(), key=lambda d: (d['value'] == 'general', d['label']))
    return cat_list, dept_list


@custom_login_required
@role_required(['admin'])
def cogs_consumption_calc(request):
    cafe = get_cafe_from_request(request)
    graph = MenuGraph.from_db(cafe=cafe)
    if not graph.items:
        messages.warning(request, 'هنوز منویی برای این کافه وارد نشده است. یک بار دستور '
                                  '«import_kuker_menu» را روی سرور اجرا کنید.')
        return render(request, 'cogs_consumption_calc.html',
                      {'cogs_page': 'consumption', 'has_menu': False})

    today = date.today()
    default_start, _ = _current_jalali_month()

    report = None
    preview = False
    start, end = default_start, today
    sold = {}

    if request.method == 'POST':
        action = (request.POST.get('action') or 'save').strip()
        report_id = _as_int(request.POST.get('report_id'))
        start = _read_jalali(request.POST, 'start') or default_start
        end = _read_jalali(request.POST, 'end') or today
        if start > end:
            start, end = end, start
        sold = _read_sold_qty(request.POST, graph)

        if action == 'delete':
            target = (CogsReport.objects.filter(cafe=cafe, pk=report_id).first()
                      if report_id else None)
            if target is None:
                messages.error(request, 'گزارشی برای حذف پیدا نشد.')
            else:
                target.delete()
                messages.success(request, 'گزارش حذف شد.')
            return redirect(_consumption_url(request, cafe))

        if not sold:
            messages.warning(request, 'هیچ تعدادی برای غذاها وارد نشده است.')
            return redirect(_consumption_url(request, cafe, report_id))

        if action == 'preview':
            # فقط محاسبه و نمایش؛ هیچ گزارشی ذخیره نمی‌شود. اگر کاربر وسط ویرایش
            # یک گزارش است، همان گزارش در context می‌ماند تا دکمه‌های بروزرسانی
            # و حذف از دست نروند.
            preview = True
            if report_id:
                report = CogsReport.objects.filter(cafe=cafe, pk=report_id).first()
        elif action == 'update':
            report = (CogsReport.objects.filter(cafe=cafe, pk=report_id).first()
                      if report_id else None)
            if report is None:
                messages.warning(request, 'گزارش قبلی پیدا نشد؛ یک گزارش تازه ذخیره شد.')
            with transaction.atomic():
                if report is None:
                    report = CogsReport.objects.create(
                        cafe=cafe, period_start=start, period_end=end,
                        created_by=request.user)
                else:
                    report.period_start = start
                    report.period_end = end
                    report.save(update_fields=['period_start', 'period_end'])
                _replace_report_lines(report, sold)
            messages.success(request, 'گزارش بروزرسانی شد.')
            return redirect(_consumption_url(request, cafe, report.id))
        else:   # save | save_as_new
            with transaction.atomic():
                report = CogsReport.objects.create(
                    cafe=cafe, period_start=start, period_end=end, created_by=request.user)
                _replace_report_lines(report, sold)
            messages.success(request, 'گزارش بهای تمام شده ذخیره شد.')
            return redirect(_consumption_url(request, cafe, report.id))
    else:
        report_id = _as_int(request.GET.get('report'))
        if report_id:
            report = get_object_or_404(CogsReport, pk=report_id, cafe=cafe)
            start, end = report.period_start, report.period_end
            sold = {str(line.item_id): line.qty for line in report.lines.all()}
        else:
            param_start = _read_jalali(request.GET, 'start')
            param_end = _read_jalali(request.GET, 'end')
            if param_start:
                start = param_start
            if param_end:
                end = param_end
            if start > end:
                start, end = end, start
            # پیش‌نمایش قابل اشتراک: تعدادها می‌توانند در آدرس صفحه بیایند
            sold = _read_sold_qty(request.GET, graph)
            preview = bool(sold)

    cogs = compute_cogs(sold, cafe=cafe)
    groups = _consumption_groups(cogs, cafe)
    prep_groups = _prep_groups(cogs['prep_rows'], cafe)

    raw_rows = cogs['raw_rows']
    for row in raw_rows:
        row['usage_label'] = _usage_location_label(row['usage_location'])
    categories, departments = _filter_options(raw_rows)

    cat_filter = (request.GET.get('cat') or request.POST.get('cat') or '').strip()
    dept_filter = (request.GET.get('dept') or request.POST.get('dept') or '').strip()
    if cat_filter and cat_filter not in {c['id'] for c in categories}:
        cat_filter = ''
    if dept_filter and dept_filter not in {d['value'] for d in departments}:
        dept_filter = ''

    filtered_rows = [
        r for r in raw_rows
        if (not cat_filter or (r['category_id'] or UNCATEGORIZED) == cat_filter)
        and (not dept_filter or (r['usage_location'] or 'general') == dept_filter)
    ]
    filtered_total = sum((r['cost'] for r in filtered_rows), Decimal('0'))

    def _jalali(value):
        return jdatetime.date.fromgregorian(date=value)

    sj, ej = _jalali(start), _jalali(end)
    sold_total = sum((r['qty'] for r in cogs['dishes']), Decimal('0'))

    history = []
    for rep in CogsReport.objects.filter(cafe=cafe).order_by('-created_at')[:10]:
        history.append({
            'id': rep.id,
            'start': jdatetime.date.fromgregorian(date=rep.period_start),
            'end': jdatetime.date.fromgregorian(date=rep.period_end),
            'created_at': rep.created_at,
            'by': rep.created_by,
        })

    context = {
        'cogs_page': 'consumption',
        'has_menu': True,
        'report': report,
        'preview': preview,
        'show_results': bool(sold),
        'start_date': start.isoformat(), 'end_date': end.isoformat(),
        'start_year': sj.year, 'start_month': sj.month, 'start_day': sj.day,
        'end_year': ej.year, 'end_month': ej.month, 'end_day': ej.day,
        'groups': groups,
        'raw_rows': filtered_rows,
        'raw_count': len(raw_rows),
        'filtered_total': filtered_total,
        'filtered_count': len(filtered_rows),
        'is_filtered': bool(cat_filter or dept_filter),
        'categories': categories,
        'departments': departments,
        'cat_filter': cat_filter,
        'dept_filter': dept_filter,
        'prep_rows': cogs['prep_rows'],
        'prep_groups': prep_groups,
        'bad_rows': cogs['bad_rows'],
        'warnings': cogs['warnings'],
        'total_cost': cogs['total_cost'],
        'sold_total': sold_total,
        'avg_cost': (cogs['total_cost'] / sold_total) if sold_total else None,
        'dishes_sold': sum(1 for r in cogs['dishes'] if r['qty']),
        'history': history,
    }
    return render(request, 'cogs_consumption_calc.html', context)


# ---------------------------------------------------------------------------
# ویرایش رسپی یک قلم (غذا یا نیمه‌آماده)
# ---------------------------------------------------------------------------
@custom_login_required
@role_required(['admin'])
def cogs_recipe_api(request, item_id):
    """GET: رسپی یک قلم + فهرست مواد قابل انتخاب. POST: ذخیرهٔ رسپی."""
    cafe = get_cafe_from_request(request)
    item = get_scoped_object_or_404(KukerItem, request, pk=item_id)

    if request.method == 'GET':
        graph = MenuGraph.from_db(cafe=cafe)
        cost_cache = {}
        key = str(item.id)
        cost_per = _item_cost(graph, key, cost_cache)

        lines = []
        for ln in item.lines.select_related('material').order_by('id'):
            mat_key = str(ln.material_id)
            if ln.material.kind == KukerItem.KIND_RAW:
                line_cost = None
                if ln.fee is not None:
                    line_cost = Decimal(str(ln.quantity)) * ln.fee
                elif graph.items.get(mat_key, {}).get('price') is not None:
                    line_cost = Decimal(str(ln.quantity)) * graph.items[mat_key]['price']
            else:
                sub = _item_cost(graph, mat_key, cost_cache)
                line_cost = (Decimal(str(ln.quantity)) * sub) if sub is not None else None
            lines.append({
                'id': ln.id,
                'material_id': ln.material_id,
                'material': ln.material.name,
                'kind': ln.material.kind,
                'kind_label': KIND_LABELS.get(ln.material.kind, ''),
                'qty': str(ln.quantity),
                'unit': ln.unit,
                'fee': str(ln.fee) if ln.fee is not None else '',
                'cost': str(line_cost) if line_cost is not None else '',
            })

        return JsonResponse({
            'ok': True,
            'item': {
                'id': item.id,
                'name': item.name,
                'kind': item.kind,
                'kind_label': KIND_LABELS.get(item.kind, ''),
                'sheet': item.sheet,
                'section_id': item.section_id,
                'out_qty': str(item.out_qty or 1),
                'out_unit': item.out_unit or '',
                'cost_per': str(cost_per) if cost_per is not None else '',
                'is_custom': item.is_custom,
            },
            'lines': lines,
            'materials': _material_options(cafe, graph, cost_cache, exclude_id=item.id),
            'sections': _sections_payload(cafe, item.kind),
            'unit_choices': UNIT_CHOICES,
        })

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'متد پشتیبانی نمی‌شود.'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'داده ارسالی معتبر نیست.'}, status=400)

    name = (payload.get('name') or '').strip()[:200]
    if name and name != item.name:
        clash = KukerItem.objects.filter(cafe=cafe, name=name).exclude(pk=item.id).exists()
        if clash:
            return JsonResponse({'ok': False,
                                 'error': f'آیتم دیگری با نام «{name}» وجود دارد.'}, status=400)

    out_qty = _to_decimal(payload.get('out_qty')) or Decimal('1')
    out_unit = (payload.get('out_unit') or '').strip()[:20]
    if out_qty <= 0:
        return JsonResponse({'ok': False, 'error': 'مقدار خروجی باید بزرگ‌تر از صفر باشد.'}, status=400)

    section_id = payload.get('section_id') or None
    section = None
    if section_id:
        section = MenuSection.objects.filter(
            cafe=cafe, pk=section_id, kind=item.kind).first()
        if section is None:
            return JsonResponse({'ok': False, 'error': 'بخش انتخاب‌شده معتبر نیست.'}, status=400)

    cleaned, errors = [], []
    for idx, line in enumerate(payload.get('lines') or [], 1):
        qty = _to_decimal(line.get('qty'))
        unit = (line.get('unit') or '').strip()[:20]
        fee = _to_decimal(line.get('fee'))
        material_id = line.get('material_id')
        line_name = (line.get('name') or '').strip()
        if qty is None or qty <= 0:
            errors.append(f'سطر {idx}: مقدار باید بزرگ‌تر از صفر باشد.')
            continue
        if material_id:
            material = KukerItem.objects.filter(cafe=cafe, pk=material_id).first()
            if material is None:
                errors.append(f'سطر {idx}: کالای انتخاب‌شده پیدا نشد.')
                continue
            if material.kind == KukerItem.KIND_DISH:
                errors.append(f'سطر {idx}: غذای منو را نمی‌توان به‌عنوان ماده اولیه انتخاب کرد.')
                continue
        elif line_name:
            material = (KukerItem.objects.filter(cafe=cafe, name=line_name).first()
                        or KukerItem.objects.filter(
                            cafe=cafe, key=normalize_name(line_name)).first())
            if material is None:
                # کالای جدید همین‌جا ساخته می‌شود و با کالای انبار هم‌گام می‌شود
                material = KukerItem(
                    cafe=cafe, name=line_name, key=_unique_key(line_name, cafe),
                    kind=KukerItem.KIND_RAW, out_unit=unit or '',
                    price=fee if fee is not None else None, is_custom=True)
                material.save()
                if fee is not None:
                    sync_raw_material(material, price=fee, unit=unit)
        else:
            errors.append(f'سطر {idx}: نام کالا خالی است.')
            continue
        if material.id == item.id:
            errors.append(f'سطر {idx}: یک آیتم نمی‌تواند خودش را مصرف کند.')
            continue
        cleaned.append((material, qty, unit, fee))

    if errors:
        return JsonResponse({'ok': False, 'error': ' '.join(errors[:3])}, status=400)

    with transaction.atomic():
        KukerRecipeLine.objects.filter(item=item).delete()
        KukerRecipeLine.objects.bulk_create([
            KukerRecipeLine(item=item, material=material, quantity=qty, unit=unit, fee=fee)
            for material, qty, unit, fee in cleaned
        ])
        update_fields = ['out_qty', 'out_unit']
        item.out_qty = out_qty
        item.out_unit = out_unit
        if name:
            item.name = name
            update_fields.append('name')
        if section is not None:
            item.section = section
            update_fields.append('section')
        item.save(update_fields=update_fields)
        if _has_cycle(item.id, cafe):
            transaction.set_rollback(True)
            return JsonResponse({'ok': False,
                                 'error': 'این تغییر یک چرخه در رسپی‌ها ایجاد می‌کند.'}, status=400)

    graph = MenuGraph.from_db(cafe=cafe)
    cost_per = _item_cost(graph, str(item.id), {})
    # ویرایش رسپی ممکن است بهای خیلی از غذاها را تغییر دهد → ثبت تاریخچه
    record_cost_snapshots_for_cafe(cafe, note=f'ویرایش رسپی «{item.name}»')
    return JsonResponse({
        'ok': True,
        'cost_per': str(cost_per) if cost_per is not None else '',
        'lines_count': len(cleaned),
        'item': {
            'id': item.id,
            'name': item.name,
            'section_id': item.section_id,
            'out_qty': str(item.out_qty or 1),
            'out_unit': item.out_unit or '',
        },
    })


# ---------------------------------------------------------------------------
# تاریخچه بهای یک قلم (برای نمودار)
# ---------------------------------------------------------------------------
@custom_login_required
@role_required(['admin'])
def cogs_item_cost_history(request, item_id):
    """JSON: تاریخچه بهای واحد یک قلم، مرتب از قدیم به جدید برای نمودار."""
    cafe = get_cafe_from_request(request)
    item = get_scoped_object_or_404(KukerItem, request, pk=item_id)

    entries = list(item.cost_history.all().order_by('-recorded_at', '-id')[:120])
    entries.reverse()

    points = []
    for entry in entries:
        points.append({
            'cost': str(entry.cost_per_unit),
            'at': _jalali_text(entry.recorded_at),
            'at_full': _jalali_text(entry.recorded_at, with_time=True),
            'iso': entry.recorded_at.isoformat(),
            'note': entry.note,
        })

    costs = [Decimal(p['cost']) for p in points]
    current = _item_cost(MenuGraph.from_db(cafe=cafe), str(item.id), {})

    return JsonResponse({
        'ok': True,
        'item': {
            'id': item.id,
            'name': item.name,
            'kind': item.kind,
            'kind_label': KIND_LABELS.get(item.kind, ''),
            'out_unit': item.out_unit or '',
        },
        'points': points,
        'count': len(points),
        'min': str(min(costs)) if costs else '',
        'max': str(max(costs)) if costs else '',
        'latest': str(costs[-1]) if costs else '',
        'current': str(current) if current is not None else '',
    })


# ---------------------------------------------------------------------------
# بخش‌های منو: ساخت / تغییر نام
# ---------------------------------------------------------------------------
@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_section_save(request):
    cafe = get_cafe_from_request(request)
    kind = request.POST.get('kind')
    if kind not in (MenuSection.KIND_DISH, MenuSection.KIND_PREP):
        kind = MenuSection.KIND_DISH
    name = (request.POST.get('name') or '').strip()[:120]
    section_id = request.POST.get('section_id') or None

    if not name:
        payload = {'ok': False, 'error': 'نام بخش الزامی است.'}
        return JsonResponse(payload, status=400) if _wants_json(request) else _back(request)

    clash = MenuSection.objects.filter(cafe=cafe, kind=kind, name=name)
    if section_id:
        clash = clash.exclude(pk=section_id)
    if clash.exists():
        payload = {'ok': False, 'error': f'بخشی با نام «{name}» از قبل وجود دارد.'}
        return JsonResponse(payload, status=400) if _wants_json(request) else _back(request)

    if section_id:
        section = MenuSection.objects.filter(cafe=cafe, pk=section_id).first()
        if section is None:
            payload = {'ok': False, 'error': 'بخش موردنظر پیدا نشد.'}
            return JsonResponse(payload, status=404) if _wants_json(request) else _back(request)
        old_name = section.name
        section.name = name
        section.save(update_fields=['name'])
        messages.success(request, f'نام بخش «{old_name}» به «{name}» تغییر کرد.')
    else:
        order = (MenuSection.objects.filter(cafe=cafe, kind=kind)
                 .order_by('-display_order').values_list('display_order', flat=True).first() or 0)
        section = MenuSection.objects.create(
            cafe=cafe, name=name, kind=kind, display_order=order + 10, is_active=True)
        messages.success(request, f'بخش «{name}» ساخته شد.')

    if _wants_json(request):
        return JsonResponse({'ok': True, 'section': {
            'id': section.id, 'name': section.name, 'kind': section.kind,
            'display_order': section.display_order}})
    return _back(request)


# ---------------------------------------------------------------------------
# سازگاری با نسخهٔ قبلی (لینک‌های قدیمی و آزمون‌های موجود)
# ---------------------------------------------------------------------------
@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_item_add(request):
    """تعریف کالای جدید (ماده اولیه) — نام قدیمی، حالا روی صفحهٔ قیمت‌گذاری."""
    cafe = get_cafe_from_request(request)
    return _create_raw_item(request, cafe)


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_menu_item_add(request):
    """تعریف غذای جدید یا نیمه‌آماده جدید در منو."""
    cafe = get_cafe_from_request(request)
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
    out_qty = _to_decimal(request.POST.get('out_qty')) or Decimal('1')
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
