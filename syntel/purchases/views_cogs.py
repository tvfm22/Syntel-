# -*- coding: utf-8 -*-
"""بهای تمام شده (Cost of Goods Sold) بر اساس منو و رسپی‌های کوکر.

صفحه دو بخش دارد:
  ۱) ورود تعداد فروش هر غذا در یک بازه (و ذخیره آن به‌عنوان گزارش)
  ۲) نمایش محاسبه: مصرف هر کالا/ماده اولیه و نیمه‌آماده در آن بازه + بهای تمام شده
"""
import jdatetime
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .models import CogsReport, CogsReportLine, KukerItem
from .services.menu_cogs import SHEET_LABELS, MenuGraph, compute_cogs
from .views import custom_login_required, role_required


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


@custom_login_required
@role_required(['admin'])  # فقط مدیرکل (admin) و superuser
def cogs_dashboard(request):
    graph = MenuGraph.from_db()
    if not graph.items:
        messages.warning(request, 'هنوز منویی وارد نشده است. یک بار دستور '
                                  '«import_kuker_menu» را روی سرور اجرا کنید.')
        return render(request, 'cogs_dashboard.html', {'has_menu': False})

    today = date.today()
    default_start, _ = _current_jalali_month()

    # ---------- ذخیره گزارش جدید ----------
    if request.method == 'POST':
        start = _read_jalali(request.POST, 'start') or default_start
        end = _read_jalali(request.POST, 'end') or today
        if start > end:
            start, end = end, start
        sold = {}
        for dish_key in graph.dishes():
            value = _to_decimal(request.POST.get(f'qty__{dish_key}'))
            if value and value > 0:
                sold[dish_key] = value
        if not sold:
            messages.warning(request, 'هیچ تعدادی برای غذاها وارد نشده است.')
            return redirect(request.path)
        report = CogsReport.objects.create(period_start=start, period_end=end,
                                           created_by=request.user)
        CogsReportLine.objects.bulk_create([
            CogsReportLine(report=report, item_id=int(key), qty=qty)
            for key, qty in sold.items()
        ])
        messages.success(request, 'گزارش بهای تمام شده دانوی شد.')
        return redirect(f'{request.path}?report={report.id}')

    # ---------- نمایش ----------
    start, end = default_start, today
    sold = {}
    report = None
    report_id = request.GET.get('report')
    if report_id:
        report = get_object_or_404(CogsReport, pk=report_id)
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

    cogs = compute_cogs(sold)

    # گروه‌بندی غذاهای فروشنی بر اساس منوی منبع
    groups = []
    current_sheet, rows = None, []
    for row in cogs['dishes']:
        sheet = row['item'].get('sheet') or ''
        if sheet != current_sheet:
            if rows:
                groups.append({'label': SHEET_LABELS.get(current_sheet, current_sheet), 'rows': rows})
            current_sheet, rows = sheet, []
        rows.append(row)
    if rows:
        groups.append({'label': SHEET_LABELS.get(current_sheet, current_sheet), 'rows': rows})

    # فهرست نیمه‌آماده‌ها برای مشاهده/ویرایش رسپی
    prep_items = []
    for key, it in graph.items.items():
        if it['kind'] != 'prep':
            continue
        uc = graph.unit_cost(key)
        prep_items.append({
            'id': it.get('id'),
            'name': it['name'],
            'sheet': SHEET_LABELS.get(it.get('sheet'), it.get('sheet') or ''),
            'unit': it.get('out_unit') or '',
            'out_qty': it.get('out_qty'),
            'lines_count': len(it.get('lines') or []),
            'cost': uc,
        })
    prep_items.sort(key=lambda r: (r['sheet'], r['name']))

    # مواد مصرفی هر نیمه‌آماده، به نسبت مقدار مصرف‌شده در بازه
    for row in cogs['prep_rows']:
        item = row['item']
        try:
            out_qty = Decimal(str(item.get('out_qty') or 1))
        except InvalidOperation:
            out_qty = Decimal('1')
        scale = (row['qty'] / out_qty) if out_qty else Decimal('0')
        scaled = []
        for mkey, qty, unit, fee in item.get('lines') or []:
            material = graph.items.get(mkey)
            if material is None:
                continue
            scaled.append({'name': material['name'],
                           'qty': Decimal(str(qty)) * scale,
                           'unit': unit})
        row['scaled_lines'] = scaled

    def _jalali(value):
        return jdatetime.date.fromgregorian(date=value)

    sj, ej = _jalali(start), _jalali(end)
    sold_total = sum((r['qty'] for r in cogs['dishes']), Decimal('0'))

    history = []
    for rep in CogsReport.objects.order_by('-created_at')[:10]:
        history.append({
            'id': rep.id,
            'start': jdatetime.date.fromgregorian(date=rep.period_start),
            'end': jdatetime.date.fromgregorian(date=rep.period_end),
            'created_at': rep.created_at,
            'by': rep.created_by,
        })

    context = {
        'has_menu': True,
        'report': report,
        'start_date': start.isoformat(), 'end_date': end.isoformat(),
        'start_year': sj.year, 'start_month': sj.month, 'start_day': sj.day,
        'end_year': ej.year, 'end_month': ej.month, 'end_day': ej.day,
        'groups': groups,
        'raw_rows': cogs['raw_rows'],
        'prep_rows': cogs['prep_rows'],
        'bad_rows': cogs['bad_rows'],
        'warnings': cogs['warnings'],
        'total_cost': cogs['total_cost'],
        'sold_total': sold_total,
        'avg_cost': (cogs['total_cost'] / sold_total) if sold_total else None,
        'dishes_sold': sum(1 for r in cogs['dishes'] if r['qty']),
        'raw_count': len(cogs['raw_rows']),
        'history': history,
        'prep_items': prep_items,
        'unit_choices': UNIT_CHOICES,
        'menu_choices': [(s, s.strip()) for s in MENU_CHOICES],
    }
    return render(request, 'cogs_dashboard.html', context)


# ---------------------------------------------------------------------------
# ویرایش رسپی / افزودن کالا و آیتم منو
# ---------------------------------------------------------------------------
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import transaction
from .services.menu_cogs import normalize_name

MENU_CHOICES = ['منو فرنگی اصلی', 'منو اصلی بار', 'رستوران بابا آدم ', 'سفره چی']
UNIT_CHOICES = ['گرم', 'عدد', 'میل', 'پرس', 'اسلایس', 'کیلوگرم', 'لیتر']


def _back(request):
    """Redirect back to the COGS page inside the current cafe."""
    from django.urls import reverse
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt:
        return redirect(nxt)
    cafe = getattr(request, 'cafe', None)
    code = getattr(cafe, 'code', None)
    if code:
        return redirect(reverse('cogs_dashboard', kwargs={'cafe_code': code}))
    return redirect('/')


def _unique_key(name):
    """Normalized, unique key for a user-created item."""
    base = normalize_name(name)
    if not base:
        base = 'item'
    key = base
    i = 2
    while KukerItem.objects.filter(key=key).exists():
        key = f'{base}#{i}'
        i += 1
    return key


def _has_cycle(start_id):
    """True if the recipe graph contains a cycle reachable from start_id."""
    from .models import KukerRecipeLine
    edges = {}
    for item_id, material_id in KukerRecipeLine.objects.values_list('item_id', 'material_id'):
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


@custom_login_required
@role_required(['admin'])
def cogs_recipe_api(request, item_id):
    """GET: recipe of one item. POST: replace its recipe lines."""
    item = get_object_or_404(KukerItem, pk=item_id)

    if request.method == 'GET':
        graph = MenuGraph.from_db()
        key = str(item.id)
        cons = graph.unit_consumption(key)
        cost_per = (sum((s[1] for s in cons['raw'].values()), Decimal('0'))
                    + sum((s[1] for s in cons['bad'].values()), Decimal('0')))
        materials = [{'id': it.id, 'name': it.name, 'unit': it.out_unit or '',
                      'kind': it.kind,
                      'price': str(it.price) if it.price is not None else ''}
                     for it in KukerItem.objects.exclude(pk=item.id).order_by('name')]
        return JsonResponse({
            'ok': True,
            'item': {'id': item.id, 'name': item.name, 'kind': item.kind,
                     'sheet': item.sheet, 'out_qty': str(item.out_qty or 1),
                     'out_unit': item.out_unit or '', 'cost_per': str(cost_per),
                     'is_custom': item.is_custom},
            'lines': [{'id': ln.id, 'material_id': ln.material_id,
                       'material': ln.material.name, 'qty': str(ln.quantity),
                       'unit': ln.unit, 'fee': str(ln.fee) if ln.fee is not None else ''}
                      for ln in item.lines.select_related('material').order_by('id')],
            'materials': materials,
        })

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'متد پشتیبانی نمی‌شود.'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'داده ارسالی معتبر نیست.'}, status=400)

    lines = payload.get('lines') or []
    out_qty = _to_decimal(payload.get('out_qty')) or Decimal('1')
    out_unit = (payload.get('out_unit') or '').strip()[:20]
    if out_qty <= 0:
        return JsonResponse({'ok': False, 'error': 'مقدار خروجی باید بزرگ‌تر از صفر باشد.'}, status=400)

    cleaned = []
    errors = []
    for idx, line in enumerate(lines, 1):
        qty = _to_decimal(line.get('qty'))
        unit = (line.get('unit') or '').strip()[:20]
        fee = _to_decimal(line.get('fee'))
        material_id = line.get('material_id')
        name = (line.get('name') or '').strip()
        if qty is None or qty <= 0:
            errors.append(f'سطر {idx}: مقدار باید بزرگ‌تر از صفر باشد.')
            continue
        if material_id:
            material = KukerItem.objects.filter(pk=material_id).first()
            if material is None:
                errors.append(f'سطر {idx}: کالای انتخاب‌شده پیدا نشد.')
                continue
        elif name:
            material = (KukerItem.objects.filter(name=name).first()
                        or KukerItem.objects.filter(key=normalize_name(name)).first())
            if material is None:
                # کالای جدید همین‌جا ساخته می‌شود
                material = KukerItem.objects.create(
                    name=name, key=_unique_key(name), kind=KukerItem.KIND_RAW,
                    out_unit=unit or '', price=fee if fee is not None else None,
                    is_custom=True)
        else:
            errors.append(f'سطر {idx}: نام کالا خالی است.')
            continue
        if material.id == item.id:
            errors.append(f'سطر {idx}: یک آیتم نمی‌تواند خودش را مصرف کند.')
            continue
        cleaned.append((material, qty, unit, fee))

    if errors:
        return JsonResponse({'ok': False, 'error': ' '.join(errors[:3])}, status=400)

    from .models import KukerRecipeLine
    with transaction.atomic():
        KukerRecipeLine.objects.filter(item=item).delete()
        KukerRecipeLine.objects.bulk_create([
            KukerRecipeLine(item=item, material=material, quantity=qty, unit=unit, fee=fee)
            for material, qty, unit, fee in cleaned
        ])
        item.out_qty = out_qty
        item.out_unit = out_unit
        item.save(update_fields=['out_qty', 'out_unit'])
        if _has_cycle(item.id):
            transaction.set_rollback(True)
            return JsonResponse({'ok': False,
                                 'error': 'این تغییر یک چرخه در رسپی‌ها ایجاد می‌کند.'}, status=400)

    graph = MenuGraph.from_db()
    key = str(item.id)
    cons = graph.unit_consumption(key)
    cost_per = (sum((s[1] for s in cons['raw'].values()), Decimal('0'))
                + sum((s[1] for s in cons['bad'].values()), Decimal('0')))
    return JsonResponse({'ok': True, 'cost_per': str(cost_per),
                         'lines_count': len(cleaned)})


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_item_add(request):
    """تعریف کالای جدید (ماده اولیه) از داخل صفحه بهای تمام شده."""
    name = (request.POST.get('name') or '').strip()
    if not name:
        messages.error(request, 'نام کالا الزامی است.')
        return _back(request)
    if KukerItem.objects.filter(name=name).exists():
        messages.warning(request, f'کالایی با نام «{name}» از قبل وجود دارد.')
        return _back(request)
    unit = (request.POST.get('unit') or '').strip()[:20]
    price = _to_decimal(request.POST.get('price'))
    KukerItem.objects.create(
        name=name, key=_unique_key(name), kind=KukerItem.KIND_RAW,
        out_unit=unit, price=price, code=(request.POST.get('code') or '').strip()[:30],
        group=(request.POST.get('group') or '').strip()[:120], is_custom=True)
    messages.success(request, f'کالای «{name}» اضافه شد.')
    return _back(request)


@custom_login_required
@role_required(['admin'])
@require_POST
def cogs_menu_item_add(request):
    """تعریف غذای جدید یا نیمه‌آماده جدید در منو."""
    name = (request.POST.get('name') or '').strip()
    if not name:
        messages.error(request, 'نام آیتم الزامی است.')
        return _back(request)
    if KukerItem.objects.filter(name=name).exists():
        messages.warning(request, f'آیتمی با نام «{name}» از قبل وجود دارد.')
        return _back(request)
    kind = request.POST.get('kind') if request.POST.get('kind') in (
        KukerItem.KIND_DISH, KukerItem.KIND_PREP) else KukerItem.KIND_DISH
    sheet = request.POST.get('sheet') if request.POST.get('sheet') in MENU_CHOICES else MENU_CHOICES[0]
    out_qty = _to_decimal(request.POST.get('out_qty')) or Decimal('1')
    out_unit = (request.POST.get('out_unit') or 'پرس').strip()[:20]
    KukerItem.objects.create(
        name=name, key=_unique_key(name), kind=kind, sheet=sheet,
        out_qty=out_qty, out_unit=out_unit, is_custom=True)
    messages.success(request, f'آیتم «{name}» تعریف شد؛ با کلیک روی آن رسپی‌اش را وارد کنید.')
    return _back(request)
