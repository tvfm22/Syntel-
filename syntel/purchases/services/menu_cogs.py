# -*- coding: utf-8 -*-
"""Kuker menu (file: menu Kuker.xlsx) parser + cost-of-goods (COGS) engine.

Data model in the workbook:
    * Recipe sheets hold 4-column blocks anchored on the row-5 header "مواد"
      (material, quantity, unit, fee).
    * Row 3 of a block is the item name; row 4 is batch output (qty + unit).
    * Trailing rows "قیمت تمام شده کل/فی" state the workbook's own cost.
    * Sheets "مواد منو" / "مواد اولیه" are the raw-material price list.

Everything here is validated against the workbook's own stated costs:
every item that states a unit cost must match the recursively computed one.
"""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

PREP_SHEETS = ['آماده سازی آشپزخانه', 'آماده سازی بار']
MENU_SHEETS = ['منو فرنگی اصلی', 'منو اصلی بار']
REST_SHEETS = ['رستوران بابا آدم ', 'سفره چی']
RECIPE_SHEETS = PREP_SHEETS + REST_SHEETS + MENU_SHEETS

SHEET_LABELS = {
    'منو فرنگی اصلی': 'منوی فرنگی',
    'منو اصلی بار': 'منوی بار',
    'رستوران بابا آدم ': 'رستوران بابا آدم',
    'سفره چی': 'سفره چی',
    'آماده سازی آشپزخانه': 'آماده‌سازی آشپزخانه',
    'آماده سازی بار': 'آماده‌سازی بار',
}

# Unit typos found in the workbook -> canonical unit
UNIT_FIX = {'پوشن': 'پرس', 'پارت': 'پرس', 'سی ال': 'میل'}

# Name variants of defined (prep/dish) items -> canonical item name.
# Built by matching each reference's stated unit fee against item costs
# plus semantic review of ambiguous cases.
_ITEM_ALIASES = {
    'ذعفران دم شده': 'زعفران دم کرده',
    'برنج کته ایرانی زعفرانی 400 گرمی': 'برنج ایرانی کته',
    'مایه کباب  کوبیده': 'مایه کوبیده',
    'مایه ششلیگ': 'مایه شیشلیگ',
    'مایع چنجه': 'مایه چنجه',
    'خورش قیمه سیب زمینی': 'قیمه سیب زمینی',
    'خورش قیمه بادمجان': 'قیمه بادمجان',
    'خورش قورمه با گوشت': 'قورمه سبزی',
    'کباب برگ': 'مایه کباب برگ',
    'مایه جوجه': 'مایه جوجه کباب زعفرانی ران',
    'خمیر پیتزا': 'خمیر نان و پیتزا',
    'خمیر نان': 'خمیر نان و پیتزا',
    'حور چاتا 150 میل': 'حور چاتا',
    'حمص-همراه': 'حمص',
    'ران مرغ پخته': 'مرغ',
    'مش پوتیتو': 'آماده سازی مش پوتیتو',
    'مایه چیز کیک': 'مایه دسر چیز کیک لوتوس',
    'دیپ پنیر': 'دریپ پنیر',
    'دیپ بلوچیز': 'دریپ پنیر',
    'تارتار سس': 'سس تارتار',
    'تارت کینگ اویستر': 'قارچ کینگ اویستر',
    'قارچ اویستر': 'قارچ کینگ اویستر',
    'قارچ کینگ اویستر اماده شده': 'قارچ کینگ اویستر',
    'سس باربیکیو': 'سس باربیکیو بیس',
    'سس خردل آرتیشو -سالاد آرتیشو': 'سس خردل  آرتیشو',
    'سس مارانیرا': 'سس مارینارا',
    'سس استوای': 'سس استوائی',
    'سس هالوپینو': 'سس هاوپینو پیتزا',
    'سس مایونز سیرلیمو': 'سس سیرو لیمو',
    'سس مایونز سیرولیمو': 'سس سیرو لیمو',
    'خردل عسل': 'سس خردل عسل',
    'کوهان پخته شده 175 گرم': 'آماده سازی کوهان',
    'پوشت پولد بیف 70 گرم': 'گوشت بولدبیف',
    'گوشت پولدبیف': 'گوشت بولدبیف',
    'تره تور-همراه': 'تره تور',
    'پیکل خیار-همراه': 'پیکل خیار',
    'پیگل کاپی': 'پیکل کاپی',
    'چیپس قارچ صدقی': 'چیپس فارچ صدفی',
    'چیس سیب زمینی': 'چیپس سیب زمینی',
    'پوره آلبالو توت فرنگی': 'پوره آلبالو با توت فرنگی',
    'پوره آلبالو و توت فرنگی': 'پوره آلبالو با توت فرنگی',
    'پوره توت فرنگی آلبالو 30 میل': 'پوره آلبالو با توت فرنگی',
    'هویچ بارتز': 'سس هویچ بارتز',
    'باقلوا گرد': 'باقلوا گردوئی',
}

# Spelling variants of raw materials that must be merged into one item.
_RAW_ALIASES = {
    'هویج': ['هویچ', 'هویح', 'هوی کری'],
    'پیاز': ['پیاز سفید', 'پیاز سغید', 'پیاز خام سفید'],
    'سیر': ['سیرخام'],
    'شکر': ['شکر سفید'],
    'آب': ['آب جوش', 'آّب جوش'],
    'یخ': ['یح', 'بخ'],
    'فلفل سیاه': ['فلفل', 'فلفل میکس', 'فلفل سیاه میکس', 'فلفل سیاه دان', 'فلفل سیا میکس'],
    'لیمو ترش': ['لیمو تاره'],
}


def normalize_name(value):
    """Normalize a Persian name for matching (whitespace-free key)."""
    if value is None:
        return ''
    s = str(value).strip()
    for a, b in (('ي', 'ی'), ('ك', 'ک'), ('ة', 'ه'), ('أ', 'ا'),
                 ('إ', 'ا'), ('آ', 'ا'), ('ذ', 'ز')):
        s = s.replace(a, b)
    s = re.sub(r'[\u200c\u200f\u200e\u0640]', '', s)
    s = re.sub(r'\s+', '', s)
    return s


ITEM_ALIASES = {normalize_name(k): normalize_name(v) for k, v in _ITEM_ALIASES.items()}
RAW_ALIASES = {normalize_name(k): normalize_name(v) for v, variants in _RAW_ALIASES.items()
               for k in variants}


def _to_float(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_workbook(workbook):
    """Parse all recipe blocks. Returns {name_key: def-dict}; role-split definitions."""
    defs = {}
    for sheet in RECIPE_SHEETS:
        ws = workbook[sheet]
        category = str(ws.cell(row=2, column=3).value or '').strip()
        sheet_role = 'prep' if sheet in PREP_SHEETS else ('dish' if sheet in MENU_SHEETS else 'rest')
        anchors = [c.column for c in ws[5] if c.value and str(c.value).strip() == 'مواد']
        for c in anchors:
            name = ws.cell(row=3, column=c).value
            if not name or not str(name).strip():
                continue
            key = (normalize_name(name), sheet_role)
            if key in defs:
                continue  # first definition wins (restaurants share identical menus)
            d = {
                'name': str(name).strip(), 'key': key, 'sheet': sheet, 'role': sheet_role,
                'category': category,
                'out_qty': _to_float(ws.cell(row=4, column=c + 1).value),
                'out_unit': UNIT_FIX.get(str(ws.cell(row=4, column=c + 3).value or '').strip(),
                                         str(ws.cell(row=4, column=c + 3).value or '').strip()),
                'lines': [],
            }
            for r in range(6, ws.max_row + 1):
                m = ws.cell(row=r, column=c).value
                q = _to_float(ws.cell(row=r, column=c + 1).value)
                u = str(ws.cell(row=r, column=c + 2).value or '').strip()
                fee = _to_float(ws.cell(row=r, column=c + 3).value)
                v2 = _to_float(ws.cell(row=r, column=c + 2).value)
                if m is None or not str(m).strip():
                    continue
                mn = str(m).strip()
                if mn.startswith('قیمت تمام شده'):
                    if 'کل' in mn:
                        d['stated_total'] = v2 if v2 is not None else q
                    else:
                        d['stated_fee'] = v2 if v2 is not None else q
                    continue
                if q is None or q == 0:
                    continue
                d['lines'].append((mn, q, UNIT_FIX.get(u, u), fee))
            defs[key] = d
    return defs


def parse_master_lists(workbook):
    """Raw material price list (per-unit price), item codes and groups."""
    prices, codes, groups = {}, {}, {}
    for r in workbook['مواد منو'].iter_rows(min_row=4, values_only=True):
        name, qty, unit, price = r[4], r[5], r[6], r[7]
        if name and qty and price:
            try:
                prices[normalize_name(name)] = (float(price) / float(qty), str(unit).strip())
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        if name and r[2]:
            groups[normalize_name(name)] = str(r[2]).strip()
    for r in workbook['مواد اولیه'].iter_rows(min_row=4, values_only=True):
        name, code = r[4], r[3]
        if name and code:
            codes.setdefault(normalize_name(name), str(code).strip())
    return prices, codes, groups


def _dec(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class MenuGraph:
    """In-memory menu model shared by the importer and the COGS engine.

    Every recipe line is stored as (material_key, qty, unit, fee).
    Material references are resolved once at import time (see _resolve_ref);
    at runtime the same graph is rebuilt from the database via from_db().
    """

    def __init__(self):
        self.items = {}                 # item_key -> item dict
        self.by_name = defaultdict(list)  # normalized display name -> [item_key]
        self.defs = {}
        self.prices = {}
        self.codes = {}
        self.groups = {}
        self.line_refs = []             # (item_key, ref_name, material_key)

    # -- construction from the workbook -----------------------------------
    @classmethod
    def from_workbook(cls, path):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        graph = cls()
        graph.defs = parse_workbook(wb)
        graph.prices, graph.codes, graph.groups = parse_master_lists(wb)
        def_ids = {}
        for (nk, role), d in graph.defs.items():
            kind = {'prep': 'prep', 'dish': 'dish', 'rest': 'dish'}[role]
            item_key = nk if nk not in graph.items else f'{nk}|{kind}'
            def_ids[(nk, role)] = item_key
            graph._new_item(item_key, d['name'], kind, d['sheet'], d['category'],
                            d['out_qty'], d['out_unit'], d.get('stated_total'), d.get('stated_fee'))
            graph.by_name[nk].append(item_key)
        for (nk, role), d in graph.defs.items():
            item = graph.items[def_ids[(nk, role)]]
            for ref_name, qty, unit, fee in d['lines']:
                mkey = graph._resolve_ref(ref_name, unit, fee, current=def_ids[(nk, role)])
                item['lines'].append((mkey, qty, unit, fee))
                graph.line_refs.append((def_ids[(nk, role)], ref_name, mkey))
        graph._classify()
        return graph

    # -- construction from the database ------------------------------------
    @classmethod
    def from_db(cls):
        from ..models import KukerItem, KukerRecipeLine
        graph = cls()
        for it in KukerItem.objects.all():
            graph.items[str(it.id)] = {
                'id': it.id, 'is_custom': it.is_custom,
                'name': it.name, 'kind': it.kind, 'sheet': it.sheet, 'category': it.category,
                'out_qty': it.out_qty, 'out_unit': it.out_unit,
                'price': it.price, 'code': it.code, 'group': it.group,
                'stated_total': it.stated_total, 'stated_fee': it.stated_fee, 'lines': [],
            }
            graph.by_name[normalize_name(it.name)].append(str(it.id))
        for ln in KukerRecipeLine.objects.select_related('material'):
            item = graph.items.get(str(ln.item_id))
            if item is not None:
                item['lines'].append((str(ln.material_id), float(ln.quantity), ln.unit,
                                      float(ln.fee) if ln.fee is not None else None))
        return graph

    # -- helpers ------------------------------------------------------------
    def _new_item(self, key, name, kind, sheet, category, out_qty, out_unit, stated_total, stated_fee):
        item = dict(name=name, kind=kind, sheet=sheet, category=category,
                    out_qty=out_qty, out_unit=out_unit, stated_total=stated_total,
                    stated_fee=stated_fee, price=None, code='', group='', lines=[])
        self.items[key] = item
        return item

    def _resolve_ref(self, ref_name, unit, fee, current=None):
        """Map a material reference to an item key (defined item or raw material)."""
        k = normalize_name(ref_name)
        k = ITEM_ALIASES.get(k, k)
        k = RAW_ALIASES.get(k, k)
        if k in self.by_name:
            picked = self._pick_candidate(self.by_name[k], unit, fee)
            if picked != current:
                return picked
            # a block listing its own name means the RAW ingredient (e.g. «قارچ کینگ اویستر»)
            k = f'{k}#raw'
        elif k == current:
            k = f'{k}#raw'
        if k in self.items:          # raw material created earlier
            return k
        # brand-new raw material
        nk_raw = normalize_name(ref_name)
        base = k[:-4] if k.endswith('#raw') else k
        price = self.prices.get(base) or self.prices.get(nk_raw)
        item = self._new_item(k, ref_name.strip(), 'raw', '', '', None, '', None, None)
        item['price'] = price[0] if price else None
        item['code'] = self.codes.get(k) or self.codes.get(nk_raw) or ''
        item['group'] = self.groups.get(k) or self.groups.get(nk_raw) or ''
        return k

    def _pick_candidate(self, cands, unit, fee):
        """Same-name definitions (prep batch vs menu dish): pick by unit, then by fee."""
        if len(cands) <= 1:
            return cands[0]
        same_unit = [c for c in cands if (self.items[c].get('out_unit') or '') == unit]
        pool = same_unit or cands
        if fee is not None:
            for c in pool:
                stated = self.items[c].get('stated_fee')
                if stated and abs(Decimal(str(stated)) - Decimal(str(fee))) / max(abs(Decimal(str(fee))), Decimal('1e-9')) < Decimal('0.01'):
                    return c
        return pool[0]

    def _classify(self):
        """Restaurant-sheet items that other recipes build on become intermediates."""
        referenced = {mkey for _, _, mkey in self.line_refs}
        for key, item in self.items.items():
            if item['kind'] == 'dish' and item['sheet'] in REST_SHEETS and key in referenced:
                item['kind'] = 'prep'

    # -- costs ---------------------------------------------------------------
    def unit_cost(self, key, _stack=None):
        """Cost of one output unit of `key` (workbook's own stated cost when present)."""
        item = self.items[key]
        if item['kind'] == 'raw':
            return _dec(item['price'])
        if item.get('stated_fee'):
            return _dec(item['stated_fee'])
        if item.get('stated_total') and item.get('out_qty'):
            return _dec(item['stated_total']) / _dec(item['out_qty'])
        if _stack is None:
            _stack = set()
        if key in _stack or not item.get('out_qty'):
            return None
        stack = _stack | {key}
        total = Decimal('0')
        for mkey, qty, unit, fee in item['lines']:
            mat = self.items[mkey]
            if fee is not None:
                unit_price = _dec(fee)
            else:
                unit_price = self.unit_cost(mkey, stack) or _dec(mat.get('price')) or Decimal('0')
            total += _dec(qty) * unit_price
        return total / _dec(item['out_qty'])

    def validate_against_workbook(self):
        """Every item stating a unit cost must match the computed one within 1%."""
        bad = []
        for key, item in self.items.items():
            if not item.get('stated_fee') or item['kind'] == 'raw':
                continue
            uc = self.unit_cost(key)
            if uc is None:
                continue
            stated = _dec(item['stated_fee'])
            if abs(uc - stated) / max(abs(stated), Decimal('1e-9')) > Decimal('0.01'):
                bad.append((item['name'], float(stated), float(uc)))
        return bad

    # -- per-unit consumption --------------------------------------------------
    def unit_consumption(self, key, _cache=None, _stack=None):
        """Raw-material usage per one output unit of `key`.

        Returns {'raw': {(key, unit): [qty, cost]}, 'prep': {...},
                  'bad': {...} (unit mismatch: cost only), 'warn': [...]}
        """
        if _cache is None:
            _cache = {}
        if _stack is None:
            _stack = set()
        item = self.items.get(key)
        if item is None or item['kind'] == 'raw':
            return {'raw': {}, 'prep': {}, 'bad': {}, 'warn': []}
        if key in _cache:
            return _cache[key]
        if key in _stack:
            return {'raw': {}, 'prep': {}, 'bad': {}, 'warn': [f'چرخه در رسپی: {item["name"]}']}
        stack = _stack | {key}
        out_qty = _dec(item.get('out_qty')) or Decimal('1')
        agg_raw, agg_prep, agg_bad, warns = {}, {}, {}, []
        for mkey, qty, unit, fee in item['lines']:
            mat = self.items[mkey]
            line_fee = fee if fee is not None else mat.get('price')
            cost = _dec(qty) * (_dec(line_fee) or Decimal('0')) / out_qty
            if mat['kind'] == 'raw':
                slot = agg_raw.setdefault((mkey, unit), [Decimal('0'), Decimal('0')])
                slot[0] += _dec(qty) / out_qty
                slot[1] += cost
                continue
            uc = self.unit_cost(mkey, stack)
            same_unit = (unit or '') == (mat.get('out_unit') or '')
            if not same_unit and uc is not None and fee is not None:
                if abs(_dec(fee) - uc) / max(abs(_dec(fee)), Decimal('1e-9')) < Decimal('0.01'):
                    same_unit = True  # workbook prices it as a whole unit of the prep
            if same_unit and mat.get('out_qty'):
                sub = self.unit_consumption(mkey, _cache, stack)
                factor = _dec(qty) / out_qty
                # The prep line is paid once (qty x fee). Its raw materials inherit
                # quantities; the line fee is split between them proportionally so
                # raw costs always add up to the workbook's own dish cost.
                sub_raw_cost = sum((sv[1] for sv in sub['raw'].values()), Decimal('0'))
                for sk, sv in sub['raw'].items():
                    slot = agg_raw.setdefault(sk, [Decimal('0'), Decimal('0')])
                    slot[0] += sv[0] * factor
                    if sub_raw_cost > 0:
                        slot[1] += cost * (sv[1] / sub_raw_cost)
                for sk, sv in sub['prep'].items():
                    slot = agg_prep.setdefault(sk, [Decimal('0'), Decimal('0')])
                    slot[0] += sv[0] * factor
                    slot[1] += sv[1] * factor
                for sk, sv in sub['bad'].items():
                    slot = agg_bad.setdefault(sk, [Decimal('0'), Decimal('0')])
                    slot[1] += sv[1] * factor
                pslot = agg_prep.setdefault((mkey, mat.get('out_unit') or unit or ''),
                                            [Decimal('0'), Decimal('0')])
                pslot[0] += factor
                pslot[1] += cost
                warns.extend(sub['warn'])
            else:
                # unit mismatch (e.g. slice vs portion): keep cost, quantity unknown
                warns.append(
                    f'«{mat["name"]}» در رسپی «{item["name"]}» با واحد «{unit or "؟"}» '
                    f'مقابل خروجی «{mat.get("out_qty")} {mat.get("out_unit")}» آمده؛ '
                    f'هزینه با بهای کامل حساب شد، ولی مقدار دقیق آن محاسبه نشد.')
                agg_bad.setdefault((mkey, unit), [Decimal('0'), Decimal('0')])[1] += cost
        result = {'raw': agg_raw, 'prep': agg_prep, 'bad': agg_bad, 'warn': warns}
        _cache[key] = result
        return result

    def dishes(self):
        """Sellable menu items, grouped by menu sheet."""
        keys = [k for k, it in self.items.items() if it['kind'] == 'dish']
        order = {s: i for i, s in enumerate(SHEET_LABELS)}
        return sorted(keys, key=lambda k: (order.get(self.items[k]['sheet'], 99),
                                           self.items[k]['category'], self.items[k]['name']))

    def unit_cost_display(self, key):
        uc = self.unit_cost(key)
        return uc if uc is not None else Decimal('0')


# -- database import ----------------------------------------------------------
def import_menu(path):
    """Load the workbook into KukerItem / KukerRecipeLine (idempotent)."""
    from ..models import KukerItem, KukerRecipeLine
    from django.db import transaction

    graph = MenuGraph.from_workbook(path)
    bad = graph.validate_against_workbook()
    if bad:
        raise RuntimeError('عدم تطابق بهای تمام‌شده با اکسل: ' +
                           ' | '.join(f'{n} (اکسل={s:,.0f} محاسبه={c:,.0f})' for n, s, c in bad[:5]))

    with transaction.atomic():
        # اقلامی که کاربر دستی ساخته با ایمپورت بعدی پاک نمی‌شوند
        KukerItem.objects.filter(is_custom=False).delete()
        created = {}
        for key, it in graph.items.items():
            created[key] = KukerItem.objects.create(
                name=it['name'], key=key, kind=it['kind'], sheet=it['sheet'],
                category=it['category'], group=it.get('group') or '',
                code=it.get('code') or '',
                out_qty=it.get('out_qty'), out_unit=it.get('out_unit') or '',
                price=it.get('price'),
                stated_total=it.get('stated_total'), stated_fee=it.get('stated_fee'))
        line_count = 0
        for key, it in graph.items.items():
            for mkey, qty, unit, fee in it['lines']:
                KukerRecipeLine.objects.create(
                    item=created[key], material=created[mkey],
                    quantity=qty, unit=unit or '', fee=fee)
                line_count += 1

    return {'items': len(created), 'lines': line_count,
            'dishes': sum(1 for it in graph.items.values() if it['kind'] == 'dish'),
            'raws': sum(1 for it in graph.items.values() if it['kind'] == 'raw')}


# -- COGS computation ----------------------------------------------------------
def compute_cogs(sold):
    """Compute period consumption from sold quantities.

    `sold`: {item_key (str id or graph key): quantity}. Returns dict with
    per-dish rows, raw-material usage, prep usage, warnings and total cost.
    """
    graph = MenuGraph.from_db()
    cache = {}
    totals_raw, totals_prep, totals_bad = {}, {}, {}
    dish_rows, warns = [], []
    total_cost = Decimal('0')

    for dish_key in graph.dishes():
        item = graph.items[dish_key]
        qty = _dec(sold.get(dish_key, 0)) or Decimal('0')
        cons = graph.unit_consumption(dish_key, cache)
        cost_per = (sum((s[1] for s in cons['raw'].values()), Decimal('0'))
                    + sum((s[1] for s in cons['bad'].values()), Decimal('0')))
        warns.extend(cons['warn'])
        if qty:
            for sk, sv in cons['raw'].items():
                slot = totals_raw.setdefault(sk, [Decimal('0'), Decimal('0')])
                slot[0] += sv[0] * qty
                slot[1] += sv[1] * qty
            for sk, sv in cons['prep'].items():
                slot = totals_prep.setdefault(sk, [Decimal('0'), Decimal('0')])
                slot[0] += sv[0] * qty
                slot[1] += sv[1] * qty
            for sk, sv in cons['bad'].items():
                slot = totals_bad.setdefault(sk, [Decimal('0'), Decimal('0')])
                slot[1] += sv[1] * qty
        dish_rows.append({'item': item, 'key': dish_key, 'qty': qty,
                          'cost_per': cost_per, 'cost_total': cost_per * qty})
        if qty:
            total_cost += cost_per * qty

    def _rows(totals, kind):
        rows = []
        for (key, unit), (qty, cost) in totals.items():
            it = graph.items[key]
            rows.append({'item': it, 'unit': unit, 'qty': qty, 'cost': cost,
                         'unit_price': (cost / qty) if qty else None})
        rows.sort(key=lambda r: r['cost'], reverse=True)
        return rows

    return {'dishes': dish_rows, 'raw_rows': _rows(totals_raw, 'raw'),
            'prep_rows': _rows(totals_prep, 'prep'), 'bad_rows': _rows(totals_bad, 'bad'),
            'warnings': warns, 'total_cost': total_cost, 'graph': graph}
