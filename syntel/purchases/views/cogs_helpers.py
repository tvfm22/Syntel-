from __future__ import annotations

from ..models import Department
from ..models import KukerItem
from ..models import KukerRecipeLine
from ..models import MenuSection
from ..services.menu_cogs import MenuGraph
from ..services.menu_cogs import normalize_name
from .helpers import _normalize_numeric_str
from .helpers import get_cafe_from_request
from decimal import Decimal
from decimal import InvalidOperation
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
import jdatetime


MENU_WORKBOOK_PATH = getattr(settings, 'MENU_WORKBOOK_PATH', None)


PROJECT_ROOT = getattr(settings, 'PROJECT_ROOT', None)


PREP_SECTION_NAMES = list(MenuSection.DEFAULT_PREP_SECTIONS)


MENU_CHOICES = ['منو فرنگی اصلی', 'منو اصلی بار', 'رستوران بابا آدم ', 'سفره چی']


UNIT_CHOICES = ['گرم', 'عدد', 'میل', 'پرس', 'اسلایس', 'کیلوگرم', 'لیتر']


KIND_LABELS = {
    KukerItem.KIND_RAW: 'کالا / ماده اولیه',
    KukerItem.KIND_PREP: 'نیمه‌آماده',
    KukerItem.KIND_DISH: 'غذای منو',
}


def _to_decimal(value):
    if value is None:
        return None
    text = _normalize_numeric_str(value)
    if not text:
        return None
    try:
        dec = Decimal(text)
        if not dec.is_finite():
            return None
        return dec
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
    today = timezone.localdate()
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
    """Redirect back to a COGS page inside the current cafe (Open Redirect safe)."""
    nxt = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if nxt and url_has_allowed_host_and_scheme(
        nxt,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
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
    """3-color iterative DFS: True if the recipe graph contains a cycle reachable from start_id."""
    edges = {}
    lines = KukerRecipeLine.objects.all()
    if cafe is not None:
        lines = lines.filter(item__cafe=cafe)
    for item_id, material_id in lines.values_list('item_id', 'material_id'):
        edges.setdefault(item_id, []).append(material_id)

    state = {}  # node_id -> 1 (visiting/gray) | 2 (visited/black)
    stack = [(start_id, 0)]
    while stack:
        node, idx = stack.pop()
        neighbors = edges.get(node, ())
        if idx == 0:
            st = state.get(node, 0)
            if st == 2:
                continue
            state[node] = 1
        if idx < len(neighbors):
            stack.append((node, idx + 1))
            nxt = neighbors[idx]
            st = state.get(nxt, 0)
            if st == 1:
                return True
            if st == 0:
                stack.append((nxt, 0))
        else:
            state[node] = 2
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


def _usage_location_choices(cafe):
    """محل استفادهٔ ماده اولیه: دامنه‌های «عمومی» + بخش‌های فعال همین کافه."""
    options = [
        {'value': 'general', 'label': 'عمومی کل'},
        {'value': 'general_kitchen_bar', 'label': 'عمومی آشپزخانه و بار'},
        {'value': 'general_other', 'label': 'عمومی سایر بخش‌ها'},
    ]
    seen = {'general', 'general_kitchen_bar', 'general_other'}
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
        return 'عمومی کل'
    if value == 'general_kitchen_bar':
        return 'عمومی آشپزخانه و بار'
    if value == 'general_other':
        return 'عمومی سایر بخش‌ها'
    return value
