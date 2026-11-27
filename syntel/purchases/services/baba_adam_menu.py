# -*- coding: utf-8 -*-
"""Import service for Cafe Baba Adam's corrected menu workbook (v2).

Loads Baba Adam's 7 menu/prep sheets into MenuSection / KukerItem /
KukerRecipeLine, creates any missing canonical warehouse Products used in the
menu, grants recipe-derived Department access (preserving existing access and
merging both bar menu and bar prep under «بار»), and syncs raw material Rial
prices and smallest units to Product and ProductPriceEntry.
"""
import ast
import logging
import operator
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import openpyxl
from django.conf import settings
from django.db import transaction

from ..models import Cafe, Category, Department, Product, ProductPriceEntry, Unit
from ..models.cogs import CogsReportLine, KukerItem, KukerItemCostHistory, KukerRecipeLine, MenuSection
from .menu_cogs import (
    MenuGraph,
    UNIT_FIX,
    ensure_default_menu_sections,
    normalize_name,
    record_cost_snapshots_for_cafe,
)
from .units import convert_quantity_to_smallest_unit, smallest_unit_of, unit_family

logger = logging.getLogger(__name__)

BABA_ADAM_MENU_PATH = (
    Path(settings.BASE_DIR).parent / 'reference' / 'menu' / 'baba_adam_menu_v2.xlsx'
)
CATALOG_SOURCE_XLSX_HINT = (
    Path(settings.BASE_DIR).parent / 'reference' / 'catalog' / 'baba_adam_products.csv'
)

# (workbook_sheet_name, kuker_kind, menu_section_name, warehouse_department_name)
BABA_ADAM_SHEETS = [
    ('آماده سازی آشپزخانه', KukerItem.KIND_PREP, 'آماده سازی آشپزخانه', 'آماده سازی'),
    ('آماده سازی بار', KukerItem.KIND_PREP, 'آماده سازی بار', 'بار'),
    ('صبحانه', KukerItem.KIND_DISH, 'صبحانه', 'صبحانه'),
    ('سالاد و پیش غذا', KukerItem.KIND_DISH, 'سالاد و پیش غذا', 'سالاد و پیش غذا'),
    ('فرنگی', KukerItem.KIND_DISH, 'فرنگی', 'فرنگی'),
    ('ایرانی', KukerItem.KIND_DISH, 'ایرانی', 'ایرانی'),
    ('منو اصلی بار', KukerItem.KIND_DISH, 'منو اصلی بار', 'بار'),
]

REF_CELL_RE = re.compile(r"^='?([^'!]+)'?!\$?([A-Z]+)\$?(\d+)(.*)$")
L_CODE_RE = re.compile(r'L#(\d+)')

# Missing canonical products in baba_adam catalog that appear in menu recipes:
# (canonical_name, category_name, order_unit_name, cogs_smallest_unit)
NEW_MENU_PRODUCTS = [
    ('پسته خام', 'خشکبار، مغزیجات، میوه خشک و دانه‌ها', 'گرم', 'گرم'),
    ('چای لیمو', 'نوشیدنی، قهوه، چای و سیروپ', 'گرم', 'گرم'),
    ('دمنوش فرش قرمز', 'نوشیدنی، قهوه، چای و سیروپ', 'گرم', 'گرم'),
    ('غلاف وانیل', 'ادویه‌جات، نمک، عطاری و عرقیات', 'گرم', 'گرم'),
    ('نسکافه گلد', 'نوشیدنی، قهوه، چای و سیروپ', 'گرم', 'گرم'),
    ('آب گاز دار سودا 250 میل', 'نوشیدنی، قهوه، چای و سیروپ', 'بطری', 'بطری'),
    ('آرگو مالت قوطی 330 CC', 'نوشیدنی، قهوه، چای و سیروپ', 'قوطی', 'قوطی'),
    ('نکتار انار سن ایچ پاکت', 'نوشیدنی، قهوه، چای و سیروپ', 'لیتر', 'میل'),
    ('برنج پاک شده هاشمی دودی سه بار الک', 'برنج، حبوبات، غلات، آرد، نان و پاستا', 'کیلوگرم', 'گرم'),
    ('برنج ریزوتو (آربوریو)', 'برنج، حبوبات، غلات، آرد، نان و پاستا', 'کیلوگرم', 'گرم'),
    ('بلغور گندم', 'برنج، حبوبات، غلات، آرد، نان و پاستا', 'کیلوگرم', 'گرم'),
    ('خمیر باقلوا', 'برنج، حبوبات، غلات، آرد، نان و پاستا', 'گرم', 'گرم'),
    ('رشته پلوئی', 'برنج، حبوبات، غلات، آرد، نان و پاستا', 'گرم', 'گرم'),
    ('روغن مایع سویا پخت و پز', 'روغن، سس، چاشنی، ترشی، مربا و کنسروجات', 'لیتر', 'میل'),
    ('خیار شور', 'روغن، سس، چاشنی، ترشی، مربا و کنسروجات', 'گرم', 'گرم'),
    ('بیسکوئیت پتی پور', 'شیرینی، شکلات، دسر، قند و مواد قنادی', 'گرم', 'گرم'),
    ('بیسکوئیت لیدی فینگر- تیرامیسو', 'شیرینی، شکلات، دسر، قند و مواد قنادی', 'گرم', 'گرم'),
    ('مربا انجیر سیاه', 'روغن، سس، چاشنی، ترشی، مربا و کنسروجات', 'گرم', 'گرم'),
    ('مربا (فصل / صبحانه)', 'روغن، سس، چاشنی، ترشی، مربا و کنسروجات', 'گرم', 'گرم'),
    ('کرب (خرچنگ / سوریمی)', 'گوشت، مرغ، آبزیان و فرآورده‌های پروتئینی', 'کیلوگرم', 'گرم'),
    ('گوشت - لاشه گوسفندی', 'گوشت، مرغ، آبزیان و فرآورده‌های پروتئینی', 'کیلوگرم', 'گرم'),
    ('پنیر پارمسان قالبی', 'لبنیات و تخم‌مرغ', 'گرم', 'گرم'),
    ('پنیر ماسکارپونه', 'لبنیات و تخم‌مرغ', 'گرم', 'گرم'),
    ('پودر خامه', 'شیرینی، شکلات، دسر، قند و مواد قنادی', 'گرم', 'گرم'),
    ('انار آب گیری', 'میوه، صیفی‌جات، سبزیجات و قارچ', 'کیلوگرم', 'گرم'),
    ('رزبری', 'میوه، صیفی‌جات، سبزیجات و قارچ', 'گرم', 'گرم'),
    ('رزماری', 'میوه، صیفی‌جات، سبزیجات و قارچ', 'گرم', 'گرم'),
    ('سبزی شوید', 'میوه، صیفی‌جات، سبزیجات و قارچ', 'کیلوگرم', 'گرم'),
    ('فلفل قرمز', 'ادویه‌جات، نمک، عطاری و عرقیات', 'گرم', 'گرم'),
    ('مویز', 'خشکبار، مغزیجات، میوه خشک و دانه‌ها', 'گرم', 'گرم'),
]

# Non-warehouse consumable only used in COGS calculations (price=0)
COGS_ONLY_CONSUMABLE = 'آب شهری / یخ'

# Canned goods ordered by قوطی in warehouse but measured in grams in recipes
RAW_OUT_UNIT_OVERRIDES = {
    'کمپوت آناناس': 'گرم',
    'کنسرو آرتیشو': 'گرم',
    'کنسرو خردل': 'گرم',
    'کنسرو لوبیا': 'گرم',
    'کنسرو نخود فرنگی': 'گرم',
    'آناناس': 'عدد',
}

# For generic multi-L# rows in «مواد منو», resolve each recipe line to the most
# accurate canonical Product on the site (exact match on col 5 + context).
SPICE_ROSE_PETAL_BLOCKS = {'ادویه کپسه', 'ادویه میکس کپه', 'ادویه مراکشی'}
SEA_SALT_LINES = {'نمک کریستال', 'نمک دریا'}
COOKING_WALNUT_BLOCKS = {
    'سالسا گردو',
    'گوشت کپه',
    'سس ناتی',
    '*فیلینگ استافد چیکن',
    'زیتون پرورده (بچ ۷ پرس)',
    'باقلوا گردوئی',
    'سوت لاوا',
}

# Explicit fixes for sub-recipe output units so MenuGraph expands raw quantities
# without unit-mismatch warnings and matches Excel batch costs accurately.
SUB_BLOCK_OUTPUT_OVERRIDES = {
    ('آماده سازی آشپزخانه', 'ادویه مراکشی'): (Decimal('100'), 'گرم'),
    ('آماده سازی آشپزخانه', 'پوت بال'): (Decimal('1200'), 'گرم'),
    ('آماده سازی آشپزخانه', 'مرینیت فیله استیک'): (Decimal('8'), 'پرس'),
    ('آماده سازی بار', 'آب هویج'): (Decimal('380'), 'میل'),
    ('آماده سازی بار', 'ذعفران دم شده'): (Decimal('300'), 'میل'),  # 15 x 20ml shots
    ('آماده سازی بار', 'سیروپ جین'): (Decimal('600'), 'میل'),       # 10 x 60ml pours
    ('آماده سازی بار', 'سیروپ لیمون گرس و لیموعمانی'): (Decimal('600'), 'میل'),
    ('آماده سازی بار', 'سیروپ تمبر هندی و سماق'): (Decimal('500'), 'گرم'),  # 10 x 50g
    ('آماده سازی بار', 'پوره آلبالو با توت فرنگی'): (Decimal('10'), 'پرس'),
    ('آماده سازی بار', 'شامپاین'): (Decimal('20'), 'عدد'),
    ('آماده سازی بار', 'شراب تخمیری'): (Decimal('18'), 'پرس'),
    ('آماده سازی بار', 'پیتر آنگوسترا'): (Decimal('1'), 'قطره'),
    ('سالاد و پیش غذا', 'نان محلی'): (Decimal('1'), 'عدد'),
    ('منو اصلی بار', 'آب میوه فصل1'): (Decimal('1'), 'پرس'),
    ('منو اصلی بار', 'آب میوه فصل2'): (Decimal('1'), 'پرس'),
    ('منو اصلی بار', 'آب میوه فصل3'): (Decimal('1'), 'پرس'),
}

# Preferred مواد منو row when two rows point to the same canonical L# product
DUPLICATE_L_PRIMARY_ROW = {
    33: 47,    # دارچین قلم سرخی -> دارچین قلم (1000 گرم = 4,000,000)
    70: 142,   # اسپاگتی -> اسپاگتی (500 گرم = 1,500,000)
    216: 264,  # پنیر فتا -> پنیر شاهی- لیقوان (900 گرم = 6,390,000)
    245: 335,  # کاهو چینی -> کاهو لیتل جم (1000 گرم = 1,550,000)
    250: 99,   # نعنا -> نعنا خشک (180 گرم = 2,200,000)
    284: 291,  # آووکادو -> اوکادو - معادل 4 عدد (20 عدد = 34,000,000)
}

# Rows in مواد منو whose column G header erroneously says 'گرم' instead of piece unit
PACK_UNIT_FIX_BY_ROW = {
    209: 'عدد',   # کپسول خامه: 10 عدد = 8,500,000
    269: 'عدد',   # تخم مرغ: 180 عدد = 23,000,000
    104: 'بطری',  # آب گاز دار سودا 250 میل: 1 بطری (250 میل) = 187,501
    107: 'قوطی',  # آرگو مالت قوطی 330 CC: 24 قوطی = 5,785,718
    282: 'عدد',   # آناناس: 1 عدد = 3,000,000
}
PACK_QTY_OVERRIDE_BY_ROW = {
    104: Decimal('1'),  # Excel lists 250ml; DB tracks per 250ml بطری
}

_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_SAFE_UNARY = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _clean_space(text):
    return ' '.join(str(text or '').strip().split())


def _safe_eval_arithmetic(value):
    """Evaluate a plain number or pure arithmetic formula like '=(3480000+1700000)/2'."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if text.startswith('='):
        text = text[1:].strip()
    try:
        node = ast.parse(text, mode='eval').body
    except SyntaxError:
        return None

    def _eval(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return Decimal(str(n.value))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _SAFE_UNARY:
            return _SAFE_UNARY[type(n.op)](_eval(n.operand))
        if isinstance(n, ast.BinOp) and type(n.op) in _SAFE_BINOPS:
            left, right = _eval(n.left), _eval(n.right)
            if type(n.op) is ast.Div and right == 0:
                raise ZeroDivisionError
            return _SAFE_BINOPS[type(n.op)](left, right)
        raise ValueError(f'Unsupported expression node: {type(n)}')

    try:
        return _eval(node)
    except Exception as exc:
        logger.warning('Failed to evaluate arithmetic expression %r: %s', value, exc)
        return None


def _load_l_code_map():
    """Map integer L# index -> canonical Product name from baba_adam_products.csv."""
    import csv

    l_map = {}
    with open(CATALOG_SOURCE_XLSX_HINT, 'r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            l_map[int(row['ردیف'])] = row['نام_محصول'].strip()
    return l_map


def _resolve_multi_l_product(m_row, block_name, mat_raw):
    """Resolve one line referencing a multi-L# generic row in مواد منو."""
    clean_mat = _clean_space(mat_raw)
    if m_row == 8:  # اویشن -> برگ آویشن / پودر آویشن
        return 'پودر آویشن'
    if m_row == 33:  # تمبر هندی -> تمبر هندی ترش / ملس / شیرین
        return 'تمبر هندی ترش'
    if m_row == 39:  # چای توئینیگز -> آبی / زرد
        return 'چای تویینینگز زرد'
    if m_row == 74:  # فیله خرما -> پیارم / خشک / کباب
        return 'خرما پیارم'
    if m_row == 83:  # کنجد ایرانی -> سفید / سیاه
        return 'کنجد سفید'
    if m_row == 84:  # گل محمدی -> غنچه / گلبرگ
        return 'گلبرگ محمدی' if block_name in SPICE_ROSE_PETAL_BLOCKS else 'غنچه گل محمدی'
    if m_row == 100:  # نمک -> نمک دریا / نمک ید دار
        return 'نمک دریا' if clean_mat in SEA_SALT_LINES else 'نمک ید دار'
    if m_row == 101:  # هل -> هل سبز / سیاه / شکسته
        return 'هل سبز'
    if m_row == 170:  # گردو ایرانی -> اغلا / دندانه
        return 'مغز گردو دندانه' if block_name in COOKING_WALNUT_BLOCKS else 'مغز گردو اغلا'
    if m_row == 232:  # ران ممتاز مرغ -> ران مرغ / با پوست
        return 'ران مرغ'
    if m_row == 248:  # مغز راسته گوسفندی -> راسته مغز بره / مغزی گوسفندی
        return 'راسته مغز بره'
    if m_row == 306:  # خیار -> بوته ای / درختی
        if 'پیکل' in block_name or 'بوته' in clean_mat:
            return 'خیار بوته ای'
        return 'خیار درختی'
    if m_row == 315:  # سبزی ریحان -> بنفش / سبز
        return 'ریحان بنفش'
    if m_row == 325:  # فلفل دلمه -> سبز / رنگی
        if 'سبز' in clean_mat:
            return 'فلفل دلمه ای سبز'
        return 'فلفل دلمه ای رنگی'
    raise ValueError(f'Unhandled multi-L# row {m_row} in {block_name}: {mat_raw}')


def _read_mavad_menu_rows(wb_f, wb_v):
    """Parse rows 5..356 of «مواد منو» with formula evaluation."""
    ws_f = wb_f['مواد منو']
    ws_v = wb_v['مواد منو']
    rows = {}
    for r in range(5, 357):
        excel_name = _clean_space(ws_v.cell(r, 5).value)
        std_name = _clean_space(ws_v.cell(r, 12).value)
        l_code = _clean_space(ws_v.cell(r, 13).value)
        if not (excel_name or std_name):
            continue
        pack_qty = PACK_QTY_OVERRIDE_BY_ROW.get(
            r,
            _safe_eval_arithmetic(ws_f.cell(r, 6).value or ws_v.cell(r, 6).value),
        )
        pack_unit = PACK_UNIT_FIX_BY_ROW.get(r, _clean_space(ws_v.cell(r, 7).value))
        pack_price = _safe_eval_arithmetic(ws_f.cell(r, 8).value)
        if pack_price is None:
            pack_price = _safe_eval_arithmetic(ws_v.cell(r, 8).value)
        l_nums = [int(x) for x in L_CODE_RE.findall(l_code)]
        rows[r] = {
            'row': r,
            'group': _clean_space(ws_v.cell(r, 3).value),
            'code': _clean_space(ws_v.cell(r, 4).value),
            'excel_name': excel_name,
            'std_name': std_name,
            'l_code': l_code,
            'l_nums': l_nums,
            'pack_qty': pack_qty,
            'pack_unit': pack_unit,
            'pack_price': pack_price,
        }
    return rows


def _scan_baba_adam_blocks(wb_f, wb_v):
    """Parse all 276 recipe blocks across Baba Adam's 7 sheets."""
    blocks = []
    block_by_fee_ref = {}
    for sheet_name, kind, section_name, dept_name in BABA_ADAM_SHEETS:
        ws_v = wb_v[sheet_name]
        ws_f = wb_f[sheet_name]
        anchor_cols = [
            c for c in range(1, ws_v.max_column + 1)
            if _clean_space(ws_v.cell(5, c).value) == 'مواد'
        ]
        for c in anchor_cols:
            name = str(ws_v.cell(3, c).value or '').strip()
            if not name:
                continue
            group = _clean_space(ws_v.cell(2, c).value)
            out_qty_raw = _safe_eval_arithmetic(ws_v.cell(4, c + 1).value)
            out_unit_raw = _clean_space(ws_v.cell(4, c + 3).value)
            if (sheet_name, name) in SUB_BLOCK_OUTPUT_OVERRIDES:
                out_qty, out_unit = SUB_BLOCK_OUTPUT_OVERRIDES[(sheet_name, name)]
            else:
                out_qty = out_qty_raw or Decimal('1')
                out_unit = UNIT_FIX.get(out_unit_raw, out_unit_raw) or 'پرس'

            raw_lines = []
            for r in range(6, min(ws_v.max_row + 1, 45)):
                mat_label = str(ws_v.cell(r, c).value or '').strip()
                prev_label = str(ws_v.cell(r, c - 1).value or '').strip() if c > 1 else ''
                if 'قیمت تمام شده' in mat_label or 'قیمت تمام شده' in prev_label:
                    continue
                qty = _safe_eval_arithmetic(ws_v.cell(r, c + 1).value)
                unit = UNIT_FIX.get(_clean_space(ws_v.cell(r, c + 2).value), _clean_space(ws_v.cell(r, c + 2).value))
                fee_fml = str(ws_f.cell(r, c + 3).value or '').strip()
                if not mat_label and qty is None and not fee_fml:
                    continue
                if qty is None or qty <= 0:
                    continue  # e.g. مایه کوبیده 0g placeholder line
                raw_lines.append({
                    'row': r,
                    'mat_raw': mat_label,
                    'qty': qty,
                    'unit': unit,
                    'fee_fml': fee_fml,
                })

            blk = {
                'sheet': sheet_name,
                'kind': kind,
                'section_name': section_name,
                'dept_name': dept_name,
                'group': group,
                'name': name,
                'out_qty': out_qty,
                'out_unit': out_unit,
                'lines': raw_lines,
            }
            blocks.append(blk)
            from openpyxl.utils import get_column_letter
            for off in (2, 3):
                block_by_fee_ref[(sheet_name, get_column_letter(c + off))] = blk
    return blocks, block_by_fee_ref


def _unique_item_key(name, kind, used_keys):
    nk = normalize_name(name)
    key = nk if nk not in used_keys else f'{nk}|{kind}'
    idx = 2
    while key in used_keys:
        key = f'{nk}|{kind}|{idx}'
        idx += 1
    used_keys.add(key)
    return key


def _compute_raw_unit_price(m_row_data, target_unit):
    """Return (price_per_smallest_unit_decimal, price_entry_kwargs) or (None, None)."""
    pack_price = m_row_data['pack_price']
    pack_qty = m_row_data['pack_qty']
    if pack_price is None or pack_qty is None or pack_qty <= 0:
        return None, None
    pack_unit = m_row_data['pack_unit'] or target_unit
    if pack_unit == 'CC':
        pack_unit = 'میل'
    if target_unit == 'میل' and pack_unit == 'گرم':
        pack_unit = 'میل'
    eff_qty = pack_qty
    if unit_family(pack_unit) in ('kg', 'liter'):
        conv_qty, conv_unit = convert_quantity_to_smallest_unit(pack_qty, pack_unit)
        if conv_qty is not None and conv_qty > 0:
            eff_qty = conv_qty
            pack_unit = conv_unit
    unit_price = (pack_price / eff_qty).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    return unit_price, {
        'entered_amount': pack_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        'entered_unit': target_unit if eff_qty == 1 else 'بسته',
        'package_size': None if eff_qty == 1 else eff_qty.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP),
        'package_unit': '' if eff_qty == 1 else pack_unit,
        'price_per_smallest_unit': unit_price,
        'smallest_unit': target_unit,
    }


def _normalize_raw_line_qty_unit(product_name, product, qty, unit):
    """Normalize known unit discrepancies against canonical Product units."""
    clean_u = unit or ''
    # Missing unit in سالاد هاوائی ('سینه مرغ استیکی': 55 '')
    if not clean_u:
        clean_u = product.smallest_unit or smallest_unit_of(product.unit.name if product.unit else '') or 'گرم'
    # Explicit metric CL -> ML conversion (e.g. 5 سی ال -> 50 میل)
    if clean_u.replace('\u200c', '').replace(' ', '') == 'سیال':
        return qty * Decimal('10'), 'میل'
    # Explicitly defined workbook conversions
    if product_name == 'آووکادو' and clean_u == 'گرم':
        # مواد منو row 291: 'اوکادو - معادل 4 عدد' -> 250g/piece
        return (qty / Decimal('250')).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP), 'عدد'
    if product_name == 'کشک' and clean_u == 'عدد':
        # مواد منو row 277: 650g per jar ('کشک شیشه ای')
        return qty * Decimal('650'), 'گرم'
    if product_name == 'آب گاز دار سودا 250 میل' and clean_u in ('میل', 'گرم'):
        return (qty / Decimal('250')).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP), 'بطری'
    if product_name == 'آرگو مالت قوطی 330 CC' and clean_u == 'عدد':
        return qty, 'قوطی'
    # Align liquid ingredients (Unit='لیتر', smallest='میل') measured by 'گرم' -> 'میل'
    if product and product.unit and product.unit.name == 'لیتر' and clean_u == 'گرم':
        return qty, 'میل'
    return qty, clean_u


def import_baba_adam_menu(path=None, cafe=None, apply_prices=True):
    """Import Baba Adam's full recipes, missing products, section access, and Rial prices."""
    wb_path = Path(path or BABA_ADAM_MENU_PATH)
    if not wb_path.exists():
        raise FileNotFoundError(f'Workbook not found: {wb_path}')

    if cafe is None:
        cafe = Cafe.objects.get(code='baba_adam')

    l_code_map = _load_l_code_map()
    wb_f = openpyxl.load_workbook(wb_path, data_only=False)
    wb_v = openpyxl.load_workbook(wb_path, data_only=True)

    m_rows = _read_mavad_menu_rows(wb_f, wb_v)
    blocks, block_by_fee_ref = _scan_baba_adam_blocks(wb_f, wb_v)

    units_by_name = {u.name: u for u in Unit.objects.filter(cafe__isnull=True)}
    units_by_name.update({u.name: u for u in Unit.objects.filter(cafe=cafe)})
    cats_by_name = {
        c.name: c for c in Category.objects.filter(cafe=cafe, parent=None)
    }
    depts_by_name = {
        d.name: d for d in Department.objects.filter(cafe=cafe, is_active=True)
    }

    with transaction.atomic():
        # 1. Create any missing canonical Products among the 30 used menu additions
        created_products = []
        existing_prods = {
            p.name: p
            for p in Product.objects.filter(cafe=cafe)
            .select_related('category', 'unit')
            .prefetch_related('departments')
        }
        for pname, cat_name, unit_name, _cogs_su in NEW_MENU_PRODUCTS:
            if pname in existing_prods:
                continue
            unit_obj = units_by_name[unit_name]
            cat_obj = cats_by_name[cat_name]
            prod = Product.objects.create(
                cafe=cafe,
                name=pname,
                category=cat_obj,
                unit=unit_obj,
                smallest_unit=_cogs_su,
                usage_location='general',
                is_active=True,
                is_complete=True,
            )
            existing_prods[pname] = prod
            created_products.append(pname)

        # 2. First pass over all 276 blocks: resolve every raw material reference -> Product name
        #    and accumulate every Department where each Product is consumed.
        used_product_depts = defaultdict(set)
        used_l_row_to_prod = defaultdict(set)
        resolved_block_lines = []  # parallel per block

        for blk in blocks:
            blk_lines = []
            for ln in blk['lines']:
                m = REF_CELL_RE.match(ln['fee_fml'])
                if not m:
                    raise ValueError(
                        f'Missing cell reference in [{blk["sheet"]}/{blk["name"]}] '
                        f'line {ln["row"]}: {ln["fee_fml"]!r}'
                    )
                tsheet, tcol, trow_s, tail = m.group(1), m.group(2), int(m.group(3)), m.group(4)
                if tsheet == 'مواد منو':
                    mr = m_rows.get(trow_s)
                    if not mr:
                        raise ValueError(f'Unknown مواد منو row {trow_s}')
                    if len(mr['l_nums']) > 1:
                        pname = _resolve_multi_l_product(trow_s, blk['name'], ln['mat_raw'])
                    elif len(mr['l_nums']) == 1:
                        pname = l_code_map[mr['l_nums'][0]]
                    elif trow_s == 103:  # آب شهری / یخ (COGS-only consumable)
                        pname = COGS_ONLY_CONSUMABLE
                    else:
                        pname = mr['std_name']

                    if pname != COGS_ONLY_CONSUMABLE:
                        if pname not in existing_prods:
                            raise ValueError(f'Canonical Product "{pname}" missing from DB!')
                        used_product_depts[pname].add(blk['dept_name'])
                        # Associate price metadata row (respecting DUPLICATE_L_PRIMARY_ROW)
                        l_key = mr['l_nums'][0] if len(mr['l_nums']) == 1 else None
                        pref_row = DUPLICATE_L_PRIMARY_ROW.get(l_key, trow_s) if l_key else trow_s
                        used_l_row_to_prod[pname].add(pref_row)
                    blk_lines.append(('raw', pname, ln['qty'], ln['unit'], ln['mat_raw']))
                else:
                    target_blk = block_by_fee_ref.get((tsheet, tcol))
                    if target_blk is None:
                        raise ValueError(
                            f'Unresolved sub-recipe ref in [{blk["sheet"]}/{blk["name"]}]: {ln["fee_fml"]}'
                        )
                    qty = ln['qty']
                    unit = ln['unit']
                    # Special recipe formula in ته چین: ='آماده سازی آشپزخانه'!$JQ$33/400
                    if tail.strip() == '/400':
                        qty = (qty / Decimal('400')).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
                        unit = 'پرس'
                    # Sub-recipe line unit normalization for clean raw expansion
                    if target_blk['name'] == 'ته چین' and unit == 'اسلایس':
                        unit = 'عدد'
                    if target_blk['name'] == 'نان محلی' and unit == 'گرم':
                        unit = 'عدد'
                    if target_blk['name'] == 'پوره آلبالو با توت فرنگی' and blk['name'] == 'تاماریزا':
                        qty, unit = Decimal('1'), 'پرس'
                    if not unit:
                        unit = target_blk['out_unit']
                    blk_lines.append(('ref', (target_blk['kind'], target_blk['name']), qty, unit, ln['mat_raw']))
            resolved_block_lines.append((blk, blk_lines))

        # 3. Grant recipe-derived Department access & activate any previously-inactive used products
        activated_products = []
        dept_updates = 0
        for pname, new_dept_names in used_product_depts.items():
            prod = existing_prods[pname]
            old_dept_names = {d.name for d in prod.departments.all()}
            merged_names = sorted(old_dept_names | new_dept_names)
            merged_depts = [depts_by_name[n] for n in merged_names if n in depts_by_name]
            if len(merged_depts) == 1:
                new_ul = merged_depts[0].name[:20]
            elif len(merged_depts) > 1:
                new_ul = 'multi'
            else:
                new_ul = prod.usage_location or 'general'

            changed = []
            if not prod.is_active:
                prod.is_active = True
                changed.append('is_active')
                activated_products.append(pname)
            if not prod.is_complete:
                prod.is_complete = True
                changed.append('is_complete')
            if prod.usage_location != new_ul:
                prod.usage_location = new_ul
                changed.append('usage_location')
            if changed:
                prod.save(update_fields=changed + ['updated_at'])
            if set(merged_names) != old_dept_names:
                prod.departments.set(merged_depts)
                dept_updates += 1

        # 4. Ensure smallest_unit + optional Rial prices + ProductPriceEntry for active raw Products
        # Map canonical product name -> chosen مواد منو row for price extraction
        price_row_for_prod = {}
        # Single-L# & new-product rows
        for r, mr in m_rows.items():
            if r == 103:
                continue
            if len(mr['l_nums']) == 1:
                l_id = mr['l_nums'][0]
                pref = DUPLICATE_L_PRIMARY_ROW.get(l_id, r)
                pname = l_code_map[l_id]
                if r == pref:
                    price_row_for_prod[pname] = mr
            elif not mr['l_nums']:
                pname = mr['std_name']
                if pname in existing_prods:
                    price_row_for_prod[pname] = mr
        # Multi-L# rows -> apply price onto each target candidate Product when not already priced
        multi_row_to_products = {
            8: ['برگ آویشن', 'پودر آویشن'],
            33: ['تمبر هندی ترش', 'تمبر هندی ملس', 'تمر هندی شیرین'],
            39: ['چای تویینینگز آبی', 'چای تویینینگز زرد'],
            74: ['خرما پیارم'],
            83: ['کنجد سفید', 'کنجد سیاه'],
            84: ['غنچه گل محمدی', 'گلبرگ محمدی'],
            100: ['نمک دریا', 'نمک ید دار'],
            101: ['هل سبز', 'هل سیاه', 'هل شکسته'],
            170: ['مغز گردو اغلا', 'مغز گردو دندانه'],
            232: ['ران مرغ', 'ران مرغ با پوست'],
            248: ['راسته مغز بره'],
            306: ['خیار بوته ای', 'خیار درختی'],
            315: ['ریحان بنفش', 'ریحان سبز'],
            325: ['فلفل دلمه ای رنگی', 'فلفل دلمه ای سبز'],
        }
        for r_idx, cands in multi_row_to_products.items():
            mr = m_rows.get(r_idx)
            if not mr:
                continue
            for pname in cands:
                if pname in existing_prods and pname not in price_row_for_prod:
                    price_row_for_prod[pname] = mr

        # 5. Ensure MenuSections exist in display order
        ensure_default_menu_sections(cafe)
        sections_map = {}
        dish_order = 10
        for sheet_name, kind, section_name, _dept_name in BABA_ADAM_SHEETS:
            if kind == KukerItem.KIND_PREP:
                sec = MenuSection.objects.get(
                    cafe=cafe, name=section_name, kind=MenuSection.KIND_PREP
                )
            else:
                sec, _ = MenuSection.objects.update_or_create(
                    cafe=cafe,
                    name=section_name,
                    kind=MenuSection.KIND_DISH,
                    defaults={'display_order': dish_order, 'is_active': True},
                )
                dish_order += 10
            sections_map[(sheet_name, kind)] = sec

        # 6. Update imported KukerItems in-place (preserve IDs, CogsReportLine, KukerItemCostHistory, and custom items)
        KukerRecipeLine.objects.filter(item__cafe=cafe, item__is_custom=False).delete()

        existing_non_custom = list(KukerItem.objects.filter(cafe=cafe, is_custom=False))
        existing_by_product = {ki.product_id: ki for ki in existing_non_custom if ki.product_id}
        existing_by_kind_name = {(ki.kind, ki.name): ki for ki in existing_non_custom}
        existing_by_key = {ki.key: ki for ki in existing_non_custom}

        custom_keys = set(
            KukerItem.objects.filter(cafe=cafe, is_custom=True).values_list('key', flat=True)
        )
        used_keys = set(custom_keys)
        kept_ids = set()
        raw_kuker_by_name = {}
        priced_count = 0

        # Raw materials: all active products in baba_adam + water/ice COGS-only consumable
        all_active_prods = Product.objects.filter(cafe=cafe, is_active=True).select_related(
            'category', 'unit'
        ).order_by('id')

        for prod in all_active_prods:
            unit_name = prod.unit.name if prod.unit else ''
            cogs_su = RAW_OUT_UNIT_OVERRIDES.get(
                prod.name,
                prod.smallest_unit or smallest_unit_of(unit_name) or unit_name or 'گرم',
            )
            price_val = None
            price_entry_data = None
            if apply_prices and prod.name in price_row_for_prod:
                mr = price_row_for_prod[prod.name]
                price_val, price_entry_data = _compute_raw_unit_price(mr, cogs_su)

            update_p = []
            if prod.smallest_unit != cogs_su:
                prod.smallest_unit = cogs_su
                update_p.append('smallest_unit')
            if apply_prices and price_val is not None and prod.base_unit_price != price_val:
                prod.base_unit_price = price_val
                update_p.append('base_unit_price')
            if update_p:
                prod.save(update_fields=update_p + ['updated_at'])

            mr_meta = price_row_for_prod.get(prod.name, {})
            existing_ki = (
                existing_by_product.get(prod.id)
                or existing_by_kind_name.get((KukerItem.KIND_RAW, prod.name))
            )
            if existing_ki is not None and existing_ki.key not in used_keys:
                key = existing_ki.key
                used_keys.add(key)
            else:
                key = _unique_item_key(prod.name, KukerItem.KIND_RAW, used_keys)
                if existing_ki is None:
                    existing_ki = existing_by_key.get(key)

            effective_price = price_val if apply_prices else (
                existing_ki.price if existing_ki is not None and existing_ki.price is not None else prod.base_unit_price
            )
            if existing_ki is not None:
                existing_ki.name = prod.name
                existing_ki.key = key
                existing_ki.kind = KukerItem.KIND_RAW
                existing_ki.product = prod
                existing_ki.category = prod.category.name if prod.category else ''
                existing_ki.group = mr_meta.get('group', '')[:120]
                existing_ki.code = mr_meta.get('code', '')[:30]
                existing_ki.out_unit = cogs_su
                existing_ki.price = effective_price
                existing_ki.is_custom = False
                existing_ki.save()
                ki = existing_ki
            else:
                ki = KukerItem.objects.create(
                    cafe=cafe,
                    name=prod.name,
                    key=key,
                    kind=KukerItem.KIND_RAW,
                    product=prod,
                    category=prod.category.name if prod.category else '',
                    group=mr_meta.get('group', '')[:120],
                    code=mr_meta.get('code', '')[:30],
                    out_unit=cogs_su,
                    price=effective_price,
                    is_custom=False,
                )
            kept_ids.add(ki.pk)
            raw_kuker_by_name[prod.name] = ki
            if price_entry_data is not None:
                priced_count += 1
                latest_entry = (
                    ProductPriceEntry.objects.filter(product=prod, cafe=cafe)
                    .order_by('-recorded_at', '-id')
                    .first()
                )
                if (
                    latest_entry is None
                    or latest_entry.price_per_smallest_unit != price_entry_data['price_per_smallest_unit']
                    or latest_entry.smallest_unit != price_entry_data['smallest_unit']
                ):
                    ProductPriceEntry.objects.create(
                        product=prod,
                        cafe=cafe,
                        entered_amount=price_entry_data['entered_amount'],
                        entered_unit=price_entry_data['entered_unit'],
                        package_size=price_entry_data['package_size'],
                        package_unit=price_entry_data['package_unit'],
                        price_per_smallest_unit=price_entry_data['price_per_smallest_unit'],
                        smallest_unit=price_entry_data['smallest_unit'],
                        note='ایمپورت قیمت از فایل منوی باباآدم (ریال)',
                    )

        # COGS-only water/ice consumable (no Product)
        water_ki = existing_by_kind_name.get((KukerItem.KIND_RAW, COGS_ONLY_CONSUMABLE))
        if water_ki is not None and water_ki.key not in used_keys:
            water_key = water_ki.key
            used_keys.add(water_key)
        else:
            water_key = _unique_item_key(COGS_ONLY_CONSUMABLE, KukerItem.KIND_RAW, used_keys)
            if water_ki is None:
                water_ki = existing_by_key.get(water_key)
        if water_ki is not None:
            water_ki.name = COGS_ONLY_CONSUMABLE
            water_ki.key = water_key
            water_ki.kind = KukerItem.KIND_RAW
            water_ki.product = None
            water_ki.category = 'نوشیدنی، قهوه، چای و سیروپ'
            water_ki.code = 'مصرفی'
            water_ki.out_unit = 'گرم'
            water_ki.price = Decimal('0.0000')
            water_ki.is_custom = False
            water_ki.save()
        else:
            water_ki = KukerItem.objects.create(
                cafe=cafe,
                name=COGS_ONLY_CONSUMABLE,
                key=water_key,
                kind=KukerItem.KIND_RAW,
                product=None,
                category='نوشیدنی، قهوه، چای و سیروپ',
                code='مصرفی',
                out_unit='گرم',
                price=Decimal('0.0000'),
                is_custom=False,
            )
        kept_ids.add(water_ki.pk)
        raw_kuker_by_name[COGS_ONLY_CONSUMABLE] = water_ki

        # Update or create all 276 prep and dish KukerItems
        menu_kuker_by_ref = {}
        for blk, _ in resolved_block_lines:
            existing_mi = existing_by_kind_name.get((blk['kind'], blk['name']))
            if existing_mi is not None and existing_mi.key not in used_keys:
                m_key = existing_mi.key
                used_keys.add(m_key)
            else:
                m_key = _unique_item_key(blk['name'], blk['kind'], used_keys)
                if existing_mi is None:
                    existing_mi = existing_by_key.get(m_key)
            if existing_mi is not None:
                existing_mi.name = blk['name']
                existing_mi.key = m_key
                existing_mi.kind = blk['kind']
                existing_mi.sheet = blk['sheet']
                existing_mi.section = sections_map[(blk['sheet'], blk['kind'])]
                existing_mi.group = blk['group'][:120]
                existing_mi.out_qty = blk['out_qty']
                existing_mi.out_unit = blk['out_unit']
                existing_mi.is_custom = False
                existing_mi.save()
                ki = existing_mi
            else:
                ki = KukerItem.objects.create(
                    cafe=cafe,
                    name=blk['name'],
                    key=m_key,
                    kind=blk['kind'],
                    sheet=blk['sheet'],
                    section=sections_map[(blk['sheet'], blk['kind'])],
                    group=blk['group'][:120],
                    out_qty=blk['out_qty'],
                    out_unit=blk['out_unit'],
                    is_custom=False,
                )
            kept_ids.add(ki.pk)
            menu_kuker_by_ref[(blk['kind'], blk['name'])] = ki

        # Delete only obsolete unreferenced imported items (never lose reports/history/custom recipes)
        obsolete_qs = KukerItem.objects.filter(cafe=cafe, is_custom=False).exclude(pk__in=kept_ids)
        protected_ids = (
            set(CogsReportLine.objects.filter(item__in=obsolete_qs).values_list('item_id', flat=True))
            | set(KukerItemCostHistory.objects.filter(item__in=obsolete_qs).values_list('item_id', flat=True))
            | set(KukerRecipeLine.objects.filter(material__in=obsolete_qs).values_list('material_id', flat=True))
        )
        obsolete_qs.exclude(pk__in=protected_ids).delete()

        # Create all KukerRecipeLine records
        lines_to_create = []
        for blk, blk_lines in resolved_block_lines:
            parent_item = menu_kuker_by_ref[(blk['kind'], blk['name'])]
            for ltype, target, qty, unit, _orig_label in blk_lines:
                if ltype == 'raw':
                    mat_item = raw_kuker_by_name[target]
                    prod = existing_prods.get(target)
                    n_qty, n_unit = _normalize_raw_line_qty_unit(target, prod, qty, unit)
                else:
                    mat_item = menu_kuker_by_ref[target]
                    n_qty, n_unit = qty, unit
                lines_to_create.append(
                    KukerRecipeLine(
                        item=parent_item,
                        material=mat_item,
                        quantity=n_qty,
                        unit=n_unit or '',
                        fee=None,
                    )
                )
        KukerRecipeLine.objects.bulk_create(lines_to_create)

    snapshots = record_cost_snapshots_for_cafe(cafe, note='ایمپورت کامل منو و رسپی باباآدم')

    # Self-verify COGS graph & warning count
    graph = MenuGraph.from_db(cafe=cafe)
    dish_warnings = []
    for k in graph.dishes():
        cons = graph.unit_consumption(k)
        if cons['warn']:
            dish_warnings.extend(cons['warn'])

    return {
        'cafe': cafe.code,
        'created_products': created_products,
        'activated_products': activated_products,
        'dept_updates': dept_updates,
        'raw_items': len(raw_kuker_by_name),
        'priced_products': priced_count,
        'preps': sum(1 for blk, _ in resolved_block_lines if blk['kind'] == KukerItem.KIND_PREP),
        'dishes': sum(1 for blk, _ in resolved_block_lines if blk['kind'] == KukerItem.KIND_DISH),
        'recipe_lines': len(lines_to_create),
        'snapshots': snapshots,
        'warnings': sorted(set(dish_warnings)),
    }
