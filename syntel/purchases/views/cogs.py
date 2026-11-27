from __future__ import annotations

from ..models import CogsReport
from ..models import CogsReportLine
from ..models import KukerItem
from ..models import KukerRecipeLine
from ..models import MenuSection
from ..models import Product
from ..services.menu_cogs import MenuGraph
from ..services.menu_cogs import UNCATEGORIZED
from ..services.menu_cogs import compute_cogs
from ..services.menu_cogs import ensure_default_menu_sections
from ..services.menu_cogs import normalize_name
from ..services.menu_cogs import record_cost_snapshots_for_cafe
from ..services.product_sync import sync_raw_material
from ..services.units import convert_quantity_to_smallest_unit
from ..services.units import units_are_compatible
from .cogs_helpers import KIND_LABELS
from .cogs_helpers import MENU_CHOICES
from .cogs_helpers import PREP_SECTION_NAMES
from .cogs_helpers import UNIT_CHOICES
from .cogs_helpers import _as_int
from .cogs_helpers import _back
from .cogs_helpers import _current_jalali_month
from .cogs_helpers import _group_items
from .cogs_helpers import _has_cycle
from .cogs_helpers import _item_cost
from .cogs_helpers import _jalali_text
from .cogs_helpers import _material_options
from .cogs_helpers import _read_jalali
from .cogs_helpers import _sections_payload
from .cogs_helpers import _to_decimal
from .cogs_helpers import _unique_key
from .cogs_helpers import _usage_location_label
from .cogs_helpers import _wants_json
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from urllib.parse import urlencode
import jdatetime
import json


@custom_login_required
@role_required(['admin'])
def cogs_menu_list(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
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

        row_depts = row.get('usage_locations') or [row['usage_location'] or 'general']
        for dval in row_depts:
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
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    graph = MenuGraph.from_db(cafe=cafe)
    if not graph.items:
        messages.warning(request, 'هنوز منویی برای این کافه وارد نشده است. یک بار دستور '
                                  '«import_kuker_menu» را روی سرور اجرا کنید.')
        return render(request, 'cogs_consumption_calc.html',
                      {'cogs_page': 'consumption', 'has_menu': False})

    today = timezone.localdate()
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
        locs = row.get('usage_locations') or [row['usage_location'] or 'general']
        row['usage_locations'] = locs
        row['usage_locations_csv'] = '|'.join(locs)
        row['usage_label'] = '، '.join(_usage_location_label(v) for v in locs)
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
        and (not dept_filter or dept_filter in (r.get('usage_locations') or [r['usage_location'] or 'general']))
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
        'unit_choices': UNIT_CHOICES,
    }
    return render(request, 'cogs_consumption_calc.html', context)


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
        extra_materials = []
        seen_extra = set()
        for ln in item.lines.select_related('material').order_by('id'):
            mat_key = str(ln.material_id)
            if ln.material.kind == KukerItem.KIND_RAW:
                line_cost = None
                eff_unit = ln.unit or ln.material.out_unit or ''
                if ln.fee is not None:
                    conv_qty, _ = convert_quantity_to_smallest_unit(ln.quantity, eff_unit)
                    if conv_qty is not None:
                        line_cost = conv_qty * ln.fee
                elif graph.items.get(mat_key, {}).get('price') is not None:
                    line_cost, _ = graph._raw_line_cost(mat_key, ln.quantity, eff_unit)
            else:
                sub = _item_cost(graph, mat_key, cost_cache)
                line_cost = (Decimal(str(ln.quantity)) * sub) if sub is not None else None
                if ln.material.kind == KukerItem.KIND_DISH and ln.material_id not in seen_extra:
                    seen_extra.add(ln.material_id)
                    extra_materials.append({
                        'id': ln.material_id,
                        'name': ln.material.name,
                        'kind': ln.material.kind,
                        'kind_label': KIND_LABELS.get(ln.material.kind, ''),
                        'unit': ln.material.out_unit or '',
                        'cost': str(sub) if sub is not None else '',
                    })
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
            'extra_materials': extra_materials,
            'sections': _sections_payload(cafe, item.kind),
            'unit_choices': UNIT_CHOICES,
        })

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'متد پشتیبانی نمی‌شود.'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'داده ارسالی معتبر نیست.'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'ok': False, 'error': 'داده ارسالی معتبر نیست.'}, status=400)

    name = (payload.get('name') or '').strip()[:200]
    if name and name != item.name:
        clash = KukerItem.objects.filter(cafe=cafe, name=name).exclude(pk=item.id).exists()
        if clash:
            return JsonResponse({'ok': False,
                                 'error': f'آیتم دیگری با نام «{name}» وجود دارد.'}, status=400)

    raw_out_qty = payload.get('out_qty')
    if raw_out_qty is not None and str(raw_out_qty).strip() != '':
        out_qty = _to_decimal(raw_out_qty)
        if out_qty is None or out_qty <= 0:
            return JsonResponse({'ok': False, 'error': 'مقدار خروجی باید بزرگ‌تر از صفر باشد.'}, status=400)
    else:
        out_qty = Decimal('1')
    out_unit = (payload.get('out_unit') or '').strip()[:20]

    raw_section_id = payload.get('section_id')
    section_id = _as_int(raw_section_id)
    section = None
    if raw_section_id not in (None, ''):
        if not section_id:
            return JsonResponse({'ok': False, 'error': 'بخش انتخاب‌شده معتبر نیست.'}, status=400)
        section = MenuSection.objects.filter(
            cafe=cafe, pk=section_id, kind=item.kind).first()
        if section is None:
            return JsonResponse({'ok': False, 'error': 'بخش انتخاب‌شده معتبر نیست.'}, status=400)

    raw_lines = payload.get('lines')
    if raw_lines is None:
        raw_lines = []
    if not isinstance(raw_lines, list):
        return JsonResponse({'ok': False, 'error': 'قالب سطرهای رسپی نامعتبر است.'}, status=400)

    cleaned, errors = [], []
    for idx, line in enumerate(raw_lines, 1):
        if not isinstance(line, dict):
            errors.append(f'سطر {idx}: قالب سطر نامعتبر است.')
            continue
        qty = _to_decimal(line.get('qty'))
        unit = (line.get('unit') or '').strip()[:20]
        fee = _to_decimal(line.get('fee'))
        raw_mat_id = line.get('material_id')
        material_id = _as_int(raw_mat_id)
        line_name = (line.get('name') or '').strip()
        if qty is None or qty <= 0:
            errors.append(f'سطر {idx}: مقدار باید بزرگ‌تر از صفر باشد.')
            continue
        if fee is not None and fee < 0:
            errors.append(f'سطر {idx}: فی نمی‌تواند منفی باشد.')
            continue
        material = None
        new_raw_spec = None
        if raw_mat_id not in (None, ''):
            if not material_id:
                errors.append(f'سطر {idx}: کالای انتخاب‌شده معتبر نیست.')
                continue
            material = KukerItem.objects.filter(cafe=cafe, pk=material_id).first()
            if material is None:
                errors.append(f'سطر {idx}: کالای انتخاب‌شده پیدا نشد.')
                continue
            if material.kind == KukerItem.KIND_DISH:
                already_dish_line = (
                    item.kind == KukerItem.KIND_DISH
                    and KukerRecipeLine.objects.filter(item=item, material=material).exists()
                )
                if not already_dish_line:
                    errors.append(f'سطر {idx}: غذای منو را نمی‌توان به‌عنوان ماده اولیه انتخاب کرد.')
                    continue
        elif line_name:
            material = (KukerItem.objects.filter(cafe=cafe, name=line_name).first()
                        or KukerItem.objects.filter(
                            cafe=cafe, key=normalize_name(line_name)).first())
            if material is None:
                new_raw_spec = (line_name, unit, fee)
        else:
            errors.append(f'سطر {idx}: نام کالا خالی است.')
            continue

        if material is not None:
            if material.id == item.id:
                errors.append(f'سطر {idx}: یک آیتم نمی‌تواند خودش را مصرف کند.')
                continue
            if (
                material.kind == KukerItem.KIND_RAW
                and material.out_unit
                and unit
                and not units_are_compatible(material.out_unit, unit)
            ):
                errors.append(
                    f'سطر {idx}: واحد «{unit}» با واحد پایه «{material.out_unit}» کالای «{material.name}» هم‌خوان نیست.'
                )
                continue

        cleaned.append((material, new_raw_spec, qty, unit, fee))

    if errors:
        return JsonResponse({'ok': False, 'error': ' '.join(errors[:3])}, status=400)

    with transaction.atomic():
        resolved_lines = []
        for material, new_raw_spec, qty, unit, fee in cleaned:
            if material is None and new_raw_spec is not None:
                r_name, r_unit, r_fee = new_raw_spec
                material = (KukerItem.objects.filter(cafe=cafe, name=r_name).first()
                            or KukerItem.objects.filter(
                                cafe=cafe, key=normalize_name(r_name)).first())
                if material is None:
                    material = KukerItem(
                        cafe=cafe, name=r_name, key=_unique_key(r_name, cafe),
                        kind=KukerItem.KIND_RAW, out_unit=r_unit or '',
                        price=r_fee if r_fee is not None else None, is_custom=True)
                    material.save()
                    if r_fee is not None:
                        sync_raw_material(material, price=r_fee, unit=r_unit)
            resolved_lines.append((material, qty, unit, fee))

        used_product_ids = {m.product_id for m, *_ in resolved_lines if m.product_id}
        # محصولاتی که پیش‌تر در هیچ رسپیِ این کافه نبودند و الان به رسپی اضافه می‌شوند
        # در سفارش‌گذاری هم به‌صورت خودکار فعال می‌شوند.
        newly_used_product_ids = set()
        if used_product_ids:
            already_used = set(
                KukerRecipeLine.objects.filter(
                    material__product_id__in=used_product_ids,
                    item__cafe=cafe,
                ).values_list('material__product_id', flat=True)
            )
            newly_used_product_ids = used_product_ids - already_used

        KukerRecipeLine.objects.filter(item=item).delete()
        KukerRecipeLine.objects.bulk_create([
            KukerRecipeLine(item=item, material=material, quantity=qty, unit=unit, fee=fee)
            for material, qty, unit, fee in resolved_lines
        ])
        if newly_used_product_ids:
            Product.objects.filter(id__in=newly_used_product_ids, is_active=False).update(is_active=True)
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


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_section_save(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'ok': False, 'error': 'کافهٔ فعال یافت نشد.'}, status=404) if _wants_json(request) else _back(request)
    kind = request.POST.get('kind')
    if kind not in (MenuSection.KIND_DISH, MenuSection.KIND_PREP):
        kind = MenuSection.KIND_DISH
    name = (request.POST.get('name') or '').strip()[:120]
    raw_section_id = request.POST.get('section_id')
    section_id = _as_int(raw_section_id)

    if raw_section_id and not section_id:
        payload = {'ok': False, 'error': 'شناسه بخش معتبر نیست.'}
        return JsonResponse(payload, status=400) if _wants_json(request) else _back(request)

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
        if section.name in PREP_SECTION_NAMES and name != section.name:
            payload = {'ok': False, 'error': 'بخش‌های ثابت آماده‌سازی قابل تغییر نام نیستند.'}
            if _wants_json(request):
                return JsonResponse(payload, status=400)
            messages.error(request, payload['error'])
            return _back(request)
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
