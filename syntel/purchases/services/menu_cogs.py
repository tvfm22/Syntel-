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

from .units import (
    _canonical_family,
    convert_quantity_to_smallest_unit,
    normalize_to_smallest_unit,
    smallest_unit_of,
    units_are_compatible,
)

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
UNIT_FIX = {'پوشن': 'پرس', 'پارت': 'پرس'}

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


def _dec(value):
    if value is None or value == '':
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        dec = Decimal(str(value).strip())
        return dec if dec.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _is_cl_unit(unit):
    if not unit:
        return False
    return str(unit).strip().replace('\u200c', '').replace(' ', '') == 'سیال'


def _convert_cl_line(qty, unit, fee=None):
    """Convert centilitres ('سی ال') × 10 to millilitres ('میل')."""
    qty_dec = _dec(qty)
    fee_dec = _dec(fee)
    if _is_cl_unit(unit):
        new_qty = (qty_dec * Decimal('10')) if qty_dec is not None else None
        new_fee = (fee_dec / Decimal('10')) if fee_dec is not None else None
        return new_qty, 'میل', new_fee
    return qty_dec, (unit or ''), fee_dec


def _convert_qty_between_units(qty, src_unit, dst_unit):
    """Convert ``qty`` from ``src_unit`` to ``dst_unit`` when units are compatible."""
    qty_dec, eff_src, _ = _convert_cl_line(qty, src_unit)
    if qty_dec is None:
        return Decimal('0')
    if not eff_src or not dst_unit:
        return qty_dec
    if not units_are_compatible(eff_src, dst_unit):
        return qty_dec
    smallest_qty, _ = convert_quantity_to_smallest_unit(qty_dec, eff_src)
    if smallest_qty is None:
        return qty_dec
    dst_fam = _canonical_family(dst_unit)
    if dst_fam in ('kg', 'liter'):
        return smallest_qty / Decimal('1000')
    return smallest_qty


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
            raw_out_unit = str(ws.cell(row=4, column=c + 3).value or '').strip()
            d = {
                'name': str(name).strip(), 'key': key, 'sheet': sheet, 'role': sheet_role,
                'category': category,
                'out_qty': _dec(ws.cell(row=4, column=c + 1).value),
                'out_unit': UNIT_FIX.get(raw_out_unit, raw_out_unit),
                'lines': [],
            }
            for r in range(6, ws.max_row + 1):
                m = ws.cell(row=r, column=c).value
                q = _dec(ws.cell(row=r, column=c + 1).value)
                u = str(ws.cell(row=r, column=c + 2).value or '').strip()
                fee = _dec(ws.cell(row=r, column=c + 3).value)
                v2 = _dec(ws.cell(row=r, column=c + 2).value)
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
                fixed_u = UNIT_FIX.get(u, u)
                q, fixed_u, fee = _convert_cl_line(q, fixed_u, fee)
                d['lines'].append((mn, q, fixed_u, fee))
            defs[key] = d
    return defs


def parse_master_lists(workbook):
    """Raw material price list (per-unit price), item codes and groups."""
    prices, codes, groups = {}, {}, {}
    for r in workbook['مواد منو'].iter_rows(min_row=4, values_only=True):
        name, qty, unit, price = r[4], r[5], r[6], r[7]
        if name and qty and price:
            q_dec = _dec(qty)
            p_dec = _dec(price)
            if q_dec and p_dec is not None:
                prices[normalize_name(name)] = (p_dec / q_dec, str(unit).strip())
        if name and r[2]:
            groups[normalize_name(name)] = str(r[2]).strip()
    for r in workbook['مواد اولیه'].iter_rows(min_row=4, values_only=True):
        name, code = r[4], r[3]
        if name and code:
            codes.setdefault(normalize_name(name), str(code).strip())
    return prices, codes, groups


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
    def from_db(cls, cafe=None):
        """Build the graph from the database.

        ``cafe`` scopes everything to one cafe (menu items and their recipes).
        ``cafe=None`` means "all rows" and exists only for legacy scripts/tests;
        every request-driven caller must pass the active cafe.
        """
        from ..models import KukerItem, KukerRecipeLine
        graph = cls()
        items_qs = KukerItem.objects.all()
        lines_qs = KukerRecipeLine.objects.select_related('material')
        if cafe is not None:
            items_qs = items_qs.filter(cafe=cafe)
            lines_qs = lines_qs.filter(item__cafe=cafe, material__cafe=cafe)
        for it in items_qs:
            graph.items[str(it.id)] = {
                'id': it.id, 'is_custom': it.is_custom, 'cafe_id': it.cafe_id,
                'product_id': it.product_id, 'section_id': it.section_id,
                'name': it.name, 'kind': it.kind, 'sheet': it.sheet, 'category': it.category,
                'out_qty': it.out_qty, 'out_unit': it.out_unit,
                'price': it.price, 'code': it.code, 'group': it.group,
                'stated_total': it.stated_total, 'stated_fee': it.stated_fee, 'lines': [],
            }
            graph.by_name[normalize_name(it.name)].append(str(it.id))
        for ln in lines_qs:
            item = graph.items.get(str(ln.item_id))
            if item is not None:
                item['lines'].append((str(ln.material_id), ln.quantity, ln.unit, ln.fee))
        return graph

    # -- helpers ------------------------------------------------------------
    def _new_item(self, key, name, kind, sheet, category, out_qty, out_unit, stated_total, stated_fee):
        item = dict(name=name, kind=kind, sheet=sheet, category=category,
                    out_qty=out_qty, out_unit=out_unit, stated_total=stated_total,
                    stated_fee=stated_fee, price=None, price_unit='', code='', group='', lines=[])
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
        if price:
            # store the price per smallest unit (گرم / میل) so recipe quantities
            # can be multiplied straight in — see services/units.py
            item['price'], item['price_unit'] = normalize_to_smallest_unit(price[0], price[1])
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
    def _raw_line_cost(self, mkey, qty, unit):
        """Cost of a raw-material line priced from the stored (smallest-unit) price."""
        qty_eff, eff_unit, _ = _convert_cl_line(qty, unit)
        q_s, _smallest = convert_quantity_to_smallest_unit(qty_eff, eff_unit)
        q_dec = _dec(q_s) or Decimal('0')
        price = _dec(self.items[mkey].get('price'))
        if price is None:
            return Decimal('0'), q_dec
        return q_dec * price, q_dec

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
            q_eff, u_eff, f_eff = _convert_cl_line(qty, unit, fee)
            if f_eff is not None:
                total += (q_eff or Decimal('0')) * f_eff
            elif mat['kind'] == 'raw':
                line_cost, _q = self._raw_line_cost(mkey, q_eff, u_eff)
                total += line_cost
            else:
                unit_price = self.unit_cost(mkey, stack) or _dec(mat.get('price')) or Decimal('0')
                conv_q = _convert_qty_between_units(q_eff, u_eff, mat.get('out_unit') or '')
                total += conv_q * unit_price
        return total / _dec(item['out_qty'])

    def _computed_unit_cost_inner(self, key, _stack, _cache):
        """Returns (cost, hit_cycle) so cycle-truncated nodes are never cached."""
        item = self.items.get(key)
        if item is None:
            return None, False
        if item['kind'] == 'raw':
            return _dec(item['price']), False
        if key in _cache:
            return _cache[key], False
        if key in _stack:
            return None, True
        out_qty = _dec(item.get('out_qty'))
        if not out_qty:
            return None, False
        stack = _stack | {key}
        total = Decimal('0')
        had_cycle = False
        for mkey, qty, unit, fee in item['lines']:
            mat = self.items.get(mkey)
            if mat is None:
                continue
            q_eff, u_eff, f_eff = _convert_cl_line(qty, unit, fee)
            if mat['kind'] == 'raw':
                if mat.get('price') is not None:
                    line_cost, _q = self._raw_line_cost(mkey, q_eff, u_eff)
                    total += line_cost
                elif f_eff is not None:
                    total += (q_eff or Decimal('0')) * f_eff
            else:
                sub, sub_cycle = self._computed_unit_cost_inner(mkey, stack, _cache)
                if sub_cycle:
                    had_cycle = True
                if sub is not None:
                    conv_q = _convert_qty_between_units(q_eff, u_eff, mat.get('out_unit') or '')
                    total += conv_q * sub
                elif f_eff is not None:
                    total += (q_eff or Decimal('0')) * f_eff
        result = total / out_qty
        if not had_cycle:
            _cache[key] = result
        return result, had_cycle

    def computed_unit_cost(self, key, _stack=None, _cache=None):
        """Cost rolled up from the recipe only — ignores the workbook's stated costs."""
        if _cache is None:
            _cache = {}
        if _stack is None:
            _stack = set()
        cost, _ = self._computed_unit_cost_inner(key, _stack, _cache)
        return cost

    def validate_against_workbook(self):
        """Every item stating a unit cost must match the computed one within 1%."""
        bad = []
        cache = {}
        for key, item in self.items.items():
            if not item.get('stated_fee') or item['kind'] == 'raw':
                continue
            uc = self.computed_unit_cost(key, _cache=cache)
            if uc is None:
                continue
            stated = _dec(item['stated_fee'])
            if stated is None:
                continue
            if abs(uc - stated) / max(abs(stated), Decimal('1e-9')) > Decimal('0.01'):
                bad.append((item['name'], float(stated), float(uc)))
        return bad

    # -- per-unit consumption --------------------------------------------------
    def _unit_consumption_inner(self, key, _cache, _stack):
        item = self.items.get(key)
        if item is None or item['kind'] == 'raw':
            return {'raw': {}, 'prep': {}, 'bad': {}, 'warn': []}, False
        if key in _cache:
            return _cache[key], False
        if key in _stack:
            return {'raw': {}, 'prep': {}, 'bad': {}, 'warn': [f'چرخه در رسپی: {item["name"]}']}, True
        stack = _stack | {key}
        out_qty = _dec(item.get('out_qty')) or Decimal('1')
        agg_raw, agg_prep, agg_bad, warns = {}, {}, {}, []
        had_cycle = False
        for mkey, qty, unit, fee in item['lines']:
            mat = self.items[mkey]
            q_eff, u_eff, f_eff = _convert_cl_line(qty, unit, fee)
            if mat['kind'] == 'raw':
                if mat.get('price') is not None:
                    line_cost, qty_conv = self._raw_line_cost(mkey, q_eff, u_eff)
                    line_unit = smallest_unit_of(u_eff) or u_eff
                    cost = line_cost / out_qty
                elif f_eff is not None:
                    line_unit = u_eff
                    qty_conv = q_eff or Decimal('0')
                    cost = qty_conv * f_eff / out_qty
                else:
                    line_cost, qty_conv = self._raw_line_cost(mkey, q_eff, u_eff)
                    line_unit = smallest_unit_of(u_eff) or u_eff
                    cost = line_cost / out_qty
                slot = agg_raw.setdefault((mkey, line_unit), [Decimal('0'), Decimal('0')])
                slot[0] += qty_conv / out_qty
                slot[1] += cost
                continue

            mat_out_unit = mat.get('out_unit') or ''
            same_unit = (not u_eff) or (not mat_out_unit) or units_are_compatible(u_eff, mat_out_unit)
            conv_qty = _convert_qty_between_units(q_eff, u_eff, mat_out_unit) if same_unit else (q_eff or Decimal('0'))
            line_fee = f_eff if f_eff is not None else _dec(mat.get('price'))
            cost = conv_qty * (line_fee or Decimal('0')) / out_qty
            uc = self.unit_cost(mkey, stack)
            if not same_unit and uc is not None and f_eff is not None:
                if abs(f_eff - uc) / max(abs(f_eff), Decimal('1e-9')) < Decimal('0.01'):
                    same_unit = True
                    conv_qty = q_eff or Decimal('0')
                    cost = conv_qty * f_eff / out_qty
            if same_unit and mat.get('out_qty'):
                sub, sub_cycle = self._unit_consumption_inner(mkey, _cache, stack)
                if sub_cycle:
                    had_cycle = True
                factor = conv_qty / out_qty
                sub_raw_cost = sum((sv[1] for sv in sub['raw'].values()), Decimal('0'))
                sub_bad_cost = sum((sv[1] for sv in sub['bad'].values()), Decimal('0'))
                split_fee = bool(cost) and sub_raw_cost > 0
                for sk, sv in sub['raw'].items():
                    slot = agg_raw.setdefault(sk, [Decimal('0'), Decimal('0')])
                    slot[0] += sv[0] * factor
                    if split_fee:
                        slot[1] += cost * (sv[1] / sub_raw_cost)
                    else:
                        slot[1] += sv[1] * factor
                for sk, sv in sub['prep'].items():
                    slot = agg_prep.setdefault(sk, [Decimal('0'), Decimal('0')])
                    slot[0] += sv[0] * factor
                    slot[1] += sv[1] * factor
                for sk, sv in sub['bad'].items():
                    slot = agg_bad.setdefault(sk, [Decimal('0'), Decimal('0')])
                    slot[1] += sv[1] * factor
                prep_cost = cost if split_fee else ((sub_raw_cost + sub_bad_cost) * factor)
                pslot = agg_prep.setdefault((mkey, mat_out_unit or u_eff or ''),
                                            [Decimal('0'), Decimal('0')])
                pslot[0] += factor
                pslot[1] += prep_cost
                warns.extend(sub['warn'])
            else:
                warns.append(
                    f'«{mat["name"]}» در رسپی «{item["name"]}» با واحد «{u_eff or "؟"}» '
                    f'مقابل خروجی «{mat.get("out_qty")} {mat_out_unit}» آمده؛ '
                    f'هزینه با بهای کامل حساب شد، ولی مقدار دقیق آن محاسبه نشد.')
                agg_bad.setdefault((mkey, u_eff), [Decimal('0'), Decimal('0')])[1] += cost
        result = {'raw': agg_raw, 'prep': agg_prep, 'bad': agg_bad, 'warn': warns}
        if not had_cycle:
            _cache[key] = result
        return result, had_cycle

    def unit_consumption(self, key, _cache=None, _stack=None):
        """Raw-material usage per one output unit of `key`."""
        if _cache is None:
            _cache = {}
        if _stack is None:
            _stack = set()
        result, _ = self._unit_consumption_inner(key, _cache, _stack)
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
def resolve_cafe(cafe=None):
    """The cafe an import/graph belongs to.

    Falls back to the cafe whose code is 'baba_adam' (the menu workbook's home),
    then to the first cafe. Returns None only when no cafe exists at all.
    """
    from ..models import Cafe
    if cafe is not None:
        return cafe
    return Cafe.objects.filter(code='baba_adam').first() or Cafe.objects.order_by('id').first()


def ensure_default_menu_sections(cafe):
    """Create the default prep sections for ``cafe`` (idempotent).

    Dish sections are created on demand by :func:`section_for_sheet`.
    """
    from ..models import MenuSection
    if cafe is None:
        return {}
    created = {}
    for order, name in enumerate(MenuSection.DEFAULT_PREP_SECTIONS):
        section, _ = MenuSection.objects.get_or_create(
            cafe=cafe, name=name, kind=MenuSection.KIND_PREP,
            defaults={'display_order': order, 'is_active': True})
        created[name] = section
    return created


def section_for_sheet(cafe, sheet, kind):
    """MenuSection for an Excel sheet, created on demand (dish sections are custom)."""
    from ..models import MenuSection
    if cafe is None:
        return None
    if kind == MenuSection.KIND_PREP:
        name = (sheet or '').strip() or MenuSection.DEFAULT_PREP_SECTIONS[0]
        section_kind = MenuSection.KIND_PREP
    else:
        name = (SHEET_LABELS.get(sheet, sheet) or '').strip() or 'منوی اصلی'
        section_kind = MenuSection.KIND_DISH
    order = list(SHEET_LABELS.values()).index(name) if name in SHEET_LABELS.values() else 50
    section, _ = MenuSection.objects.get_or_create(
        cafe=cafe, name=name, kind=section_kind,
        defaults={'display_order': order, 'is_active': True})
    return section


def import_menu(path, cafe=None):
    """Load the workbook into KukerItem / KukerRecipeLine for one cafe (idempotent).

    Only the cafe's own imported items are replaced; hand-made items
    (``is_custom=True``) and every other cafe's menu stay untouched.
    Raw-material prices are stored per smallest unit (گرم / میل).
    """
    from ..models import KukerItem, KukerRecipeLine
    from django.db import transaction

    cafe = resolve_cafe(cafe)
    graph = MenuGraph.from_workbook(path)
    bad = graph.validate_against_workbook()
    if bad:
        raise RuntimeError('عدم تطابق بهای تمام‌شده با اکسل: ' +
                           ' | '.join(f'{n} (اکسل={s:,.0f} محاسبه={c:,.0f})' for n, s, c in bad[:5]))

    ensure_default_menu_sections(cafe)

    with transaction.atomic():
        # اقلامی که کاربر دستی ساخته با ایمپورت بعدی پاک نمی‌شوند.
        # ردیف‌های موجود به‌روز می‌شوند (نه حذف/ساخت مجدد) تا شناسه‌ها و
        # تاریخچه بهایشان از دست نرود.
        existing = {obj.key: obj for obj in KukerItem.objects.filter(cafe=cafe, is_custom=False)}
        custom_keys = set(
            KukerItem.objects.filter(cafe=cafe, is_custom=True).values_list('key', flat=True))
        created = {}
        for key, it in graph.items.items():
            price, price_unit = normalize_to_smallest_unit(it.get('price'), it.get('price_unit') or '')
            out_unit = it.get('out_unit') or ''
            if it['kind'] == KukerItem.KIND_RAW:
                out_unit = price_unit or out_unit
            # اگر کاربر قلمی با همین کلید دستی ساخته، کلید ایمپورت جدا می‌شود
            db_key = key
            if db_key not in existing and db_key in custom_keys:
                db_key = f'{key}#import'
            obj = existing.get(db_key) or KukerItem(cafe=cafe, key=db_key)
            obj.name = it['name']
            obj.kind = it['kind']
            obj.sheet = it['sheet']
            obj.section = section_for_sheet(cafe, it['sheet'], it['kind'])
            obj.category = it['category']
            obj.group = it.get('group') or ''
            obj.code = it.get('code') or ''
            obj.out_qty = it.get('out_qty')
            obj.out_unit = out_unit
            obj.price = price
            obj.stated_total = it.get('stated_total')
            obj.stated_fee = it.get('stated_fee')
            obj.is_custom = False
            obj.save()
            created[key] = obj

        # ابتدا سطرهای رسپی قبلی حذف شوند (ماده دارای PROTECT است)
        line_count = 0
        KukerRecipeLine.objects.filter(item__cafe=cafe, item__is_custom=False).delete()
        lines_to_create = []
        for key, it in graph.items.items():
            for mkey, qty, unit, fee in it['lines']:
                lines_to_create.append(
                    KukerRecipeLine(
                        item=created[key],
                        material=created[mkey],
                        quantity=qty,
                        unit=unit or '',
                        fee=fee,
                    )
                )
                line_count += 1
        KukerRecipeLine.objects.bulk_create(lines_to_create)

        # اقلامی که دیگر در اکسل نیستند (فقط اگر در رسپی دستی یا گزارش COGS ارجاع نداشته باشند)
        from ..models import CogsReportLine
        obsolete_qs = KukerItem.objects.filter(cafe=cafe, is_custom=False).exclude(
            pk__in=[obj.pk for obj in created.values()]
        )
        protected_ids = set(
            KukerRecipeLine.objects.filter(material__in=obsolete_qs).values_list('material_id', flat=True)
        ) | set(
            CogsReportLine.objects.filter(item__in=obsolete_qs).values_list('item_id', flat=True)
        )
        obsolete_qs.exclude(pk__in=protected_ids).delete()

    # عکس‌برداری اولیه: بعد از ایمپورت، بهای همه غذاها تازه محاسبه شده است
    record_cost_snapshots_for_cafe(cafe, note='ایمپورت منو از اکسل')

    return {'items': len(created), 'lines': line_count,
            'dishes': sum(1 for it in graph.items.values() if it['kind'] == 'dish'),
            'raws': sum(1 for it in graph.items.values() if it['kind'] == 'raw'),
            'cafe': cafe.code if cafe else None}


# -- cost history -------------------------------------------------------------
def record_cost_snapshots_for_cafe(cafe, note=''):
    """Snapshot the computed unit cost of every dish/prep item of ``cafe``.

    A new :class:`KukerItemCostHistory` row is written only when the computed
    cost differs from the item's latest snapshot, so the history stays a
    change log rather than a copy of every recalculation.

    Returns ``{'checked': n, 'created': m, 'items': [...]}``.
    """
    from ..models import KukerItem, KukerItemCostHistory
    from django.db import transaction

    cafe = resolve_cafe(cafe)
    graph = MenuGraph.from_db(cafe=cafe)
    created = []
    checked = 0
    cost_cache = {}   # مشترک بین همه اقلام — هر زیررسپی یک بار حساب می‌شود
    note_text = note or 'محاسبه مجدد بهای تمام شده'
    candidate_ids = [
        item['id']
        for item in graph.items.values()
        if item['kind'] in (KukerItem.KIND_DISH, KukerItem.KIND_PREP) and item.get('id')
    ]
    latest_by_item = {}
    if candidate_ids:
        for h in (
            KukerItemCostHistory.objects.filter(item_id__in=candidate_ids)
            .order_by('item_id', '-recorded_at', '-id')
        ):
            if h.item_id not in latest_by_item:
                latest_by_item[h.item_id] = h.cost_per_unit

    to_create = []
    for key, item in graph.items.items():
        if item['kind'] not in (KukerItem.KIND_DISH, KukerItem.KIND_PREP):
            continue
        item_id = item.get('id')
        if not item_id:
            continue
        checked += 1
        cost = graph.computed_unit_cost(key, _cache=cost_cache)
        if cost is None:
            continue
        cost = cost.quantize(Decimal('0.0001'))
        if latest_by_item.get(item_id) == cost:
            continue
        to_create.append(
            KukerItemCostHistory(
                item_id=item_id,
                cost_per_unit=cost,
                note=note_text,
            )
        )
        created.append((item_id, cost))

    if to_create:
        with transaction.atomic():
            KukerItemCostHistory.objects.bulk_create(to_create)
    return {'checked': checked, 'created': len(created), 'items': created}


# -- COGS computation ----------------------------------------------------------
UNCATEGORIZED = 'none'


def product_metadata(product_ids):
    """Category / requester department(s) of the inventory products behind raw rows.

    Returns ``{product_id: {'category_id', 'category_name', 'usage_location', 'usage_locations'}}``.
    Materials with no linked product are reported as ``category_id='none'`` and
    ``usage_location='general'`` so the page filters stay exhaustive.
    """
    from ..models import Product

    ids = {pid for pid in product_ids if pid}
    if not ids:
        return {}
    meta = {}
    qs = (Product.objects.filter(pk__in=ids)
          .select_related('category')
          .prefetch_related('departments'))
    for p in qs:
        depts = [d.name for d in p.departments.all()]
        if depts:
            usage_locations = depts
            usage_location = depts[0] if len(depts) == 1 else '، '.join(depts)
        else:
            ul = p.usage_location or 'general'
            usage_locations = [ul]
            usage_location = ul
        meta[p.id] = {
            'category_id': str(p.category_id) if p.category_id else UNCATEGORIZED,
            'category_name': p.category.name if p.category else '',
            'usage_location': usage_location,
            'usage_locations': usage_locations,
        }
    return meta


def _prep_breakdown(row, graph, cache):
    """Direct ingredients of a consumed prep batch + the raw materials it yields.

    ``row['qty']`` is already expressed in the prep's own output unit, so the
    per-unit consumption of the prep is simply scaled by it. Nested prep items
    are expanded here too — the raw list is the full recursive roll-up.
    """
    item = row['item']
    key = str(item.get('id'))
    out_qty = _dec(item.get('out_qty')) or Decimal('1')
    scale = (row['qty'] / out_qty) if out_qty else Decimal('0')

    direct = []
    for mkey, qty, unit, _fee in item.get('lines') or []:
        material = graph.items.get(mkey)
        if material is None:
            continue
        direct.append({'id': material.get('id'), 'name': material['name'],
                       'qty': _dec(qty) * scale, 'unit': unit, 'kind': material['kind']})

    raws = []
    if key in graph.items:
        cons = graph.unit_consumption(key, cache)
        for (mkey, unit), (qty, cost) in cons['raw'].items():
            material = graph.items.get(mkey)
            if material is None:
                continue
            raws.append({'name': material['name'], 'qty': qty * row['qty'],
                         'unit': unit, 'cost': cost * row['qty']})
        raws.sort(key=lambda r: r['cost'], reverse=True)
    row['direct_lines'] = direct
    row['raw_lines'] = raws
    return row


def compute_cogs(sold, cafe=None):
    """Compute period consumption from sold quantities.

    `sold`: {item_key (str id or graph key): quantity}. Returns dict with
    per-dish rows, raw-material usage, prep usage, warnings and total cost.
    `cafe` scopes the whole computation to one cafe's menu.

    Semi-prepared items are never sold directly; every dish's
    :meth:`MenuGraph.unit_consumption` already recurses through nested prep
    batches, so ``raw_rows`` holds the flattened raw-material totals of the
    whole chain and ``total_cost`` matches it.
    """
    from ..models import KukerItem

    graph = MenuGraph.from_db(cafe=cafe)
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

    meta = product_metadata(
        graph.items[key].get('product_id') for (key, _unit) in totals_raw)

    def _rows(totals, kind):
        rows = []
        for (key, unit), (qty, cost) in totals.items():
            it = graph.items[key]
            row = {'item': it, 'unit': unit, 'qty': qty, 'cost': cost,
                   'unit_price': (cost / qty) if qty else None}
            if kind == KukerItem.KIND_RAW:
                info = meta.get(it.get('product_id')) or {}
                row['category_id'] = info.get('category_id') or UNCATEGORIZED
                row['category_name'] = info.get('category_name') or ''
                row['usage_location'] = info.get('usage_location') or 'general'
                row['usage_locations'] = info.get('usage_locations') or [row['usage_location']]
            rows.append(row)
        rows.sort(key=lambda r: r['cost'], reverse=True)
        return rows

    raw_rows = _rows(totals_raw, KukerItem.KIND_RAW)
    prep_rows = [_prep_breakdown(r, graph, cache) for r in _rows(totals_prep, 'prep')]

    return {'dishes': dish_rows, 'raw_rows': raw_rows,
            'prep_rows': prep_rows, 'bad_rows': _rows(totals_bad, 'bad'),
            'warnings': list(dict.fromkeys(warns)),
            'total_cost': total_cost, 'graph': graph}
