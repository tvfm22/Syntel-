# -*- coding: utf-8 -*-
"""بازبینی چیدمان PDF «درخواست کالا از انبار» — راستبهچپ، عنوان، تاریخ، امضا، ارتفاع ردیفها

نکتهٔ استخراج متن: pymupdf متن فارسی را با فرمهای نمایشی عربی و به ترتیب منطقی
برمیگرداند؛ اینجا با NFKC نرمالسازی میشود.
"""
import sys, os, unicodedata
sys.path.insert(0, 'D:/Syntel/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
import django
django.setup()
import pymupdf

doc = pymupdf.open(r'D:\Syntel\preview_warehouse.pdf')
page = doc[0]
W, H = page.rect.width, page.rect.height
ok = lambda cond, msg: print(('PASS' if cond else 'FAIL') + ' - ' + msg)

ok(abs(W - 595.27) < 1 and abs(H - 419.53) < 1, f'اندازه برگه A5 افقی → {W:.1f}×{H:.1f}')

# همهٔ spans با موقعیت
spans = []
for b in page.get_text('dict')['blocks']:
    for l in b.get('lines', []):
        for s in l['spans']:
            spans.append((unicodedata.normalize('NFKC', s['text']), s['bbox'][0], s['bbox'][1], s['bbox'][2]))

full = ' '.join(t for t, _, _, _ in spans)
ok('تاریخ:' not in full, 'کلمهٔ «تاریخ» در برگه نیست')
ok('2026' not in full and '2026-08-11' not in full, 'تاریخ میلادی در برگه نیست')

# عنوان وسط + نام کافه داخل پرانتز
title_spans = [(t, x, y, x1) for t, x, y, x1 in spans if 'درخواست کالا از انبار' in t]
ok(bool(title_spans), 'عنوان «درخواست کالا از انبار» روی برگه هست')
if title_spans:
    t, x, y, x1 = title_spans[0]
    ok('کافه لوکال' in t, f'نام کافه داخل پرانتز در عنوان هست → {t!r}')
    ok(abs((x + x1) / 2 - W / 2) < 40, f'عنوان وسط صفحه است (مرکز x={(x+x1)/2:.1f}، صفحه {W/2:.1f})')

# تاریخ شمسی گوشه چپ، روبه‌روی عنوان (بدون کلمهٔ تاریخ)
date_spans = [(t, x, y, x1) for t, x, y, x1 in spans if 'مرداد' in t]
if date_spans:
    t, x, y, _ = date_spans[0]
    ok('۱۴۰۵' in t or '1405' in t, f'سال شمسی در تاریخ هست → {t!r}')
    ok(x < 60, f'تاریخ گوشه چپ بالای برگه است (x={x:.1f})')
else:
    ok(False, 'تاریخ شمسی پیدا نشد')

# امضاها: پایین‌تر از ۸۸٪ ارتفاع، جدا از هم
eighty8 = 0.88 * H
sig_spans = [(t, x, y, x1) for t, x, y, x1 in spans if 'امضای' in t]
ok(len(sig_spans) == 3, f'هر سه امضا هست → {len(sig_spans)}')
for t, x, y, _ in sig_spans:
    ok(y > eighty8, f'«{t}» پایین‌تر از ۸۸٪ ارتفاع است (y={y:.1f} > {eighty8:.1f})')
xs = sorted(x for _, x, _, _ in sig_spans)
ok(len(xs) == 3 and xs[1] - xs[0] > 80 and xs[2] - xs[1] > 80,
   f'امضاها جدا از هم هستند → x={[round(v,1) for v in xs]}')

# بدون باکس در نوار امضا (زیر ۸۸٪ هیچ رسمی نباشد)
draws = page.get_drawings()
ok(sum(1 for d in draws if d['rect'].y0 > eighty8) == 0, 'زیر خط ۸۸٪ هیچ باکس/خطی نیست (امضاها بدون محیط)')

# راست‌به‌چپ جدول: «ردیف» سمت راست «توضیحات»
row_sp = [(x, x1) for t, x, _, x1 in spans if t == 'ردیف']
desc_sp = [(x, x1) for t, x, _, x1 in spans if 'توضیحات' in t and t != 'ردیف']
if row_sp and desc_sp:
    ok(row_sp[0][0] > desc_sp[0][0], f'ستون ردیف سمت راست ستون توضیحات است (ردیف x={row_sp[0][0]:.1f}، توضیحات x={desc_sp[0][0]:.1f})')
else:
    ok(False, f'سرستونها پیدا نشدند (ردیف {bool(row_sp)}، توضیحات {bool(desc_sp)})')

# ارتفاع ردیف‌ها: ۱۲ ردیف تکبرند + ردیف چندبرند بلندتر (~۳ برابر)
h_lines = sorted({round(d['rect'].y0, 1) for d in draws if d['type'] == 's' and d['rect'].y0 == d['rect'].y1})
gaps = [round(b - a, 1) for a, b in zip(h_lines, h_lines[1:])]
ok(len(gaps) >= 12, f'حداقل ۱۲ ردیف کالا روی برگه جا شده → {len(gaps)} ردیف')
if len(gaps) >= 12:
    singles = sorted(gaps)
    multi = singles[-1]
    single = min(gaps)
    ok(multi >= single * 2.4, f'ردیف چندبرند بلندتر از تکبرند است (تک {single}، چندبرند {multi})')
    ok(sum(1 for g in gaps if abs(g - single) < 1.5) >= 12, f'بقیهٔ ردیفها همارتفاعاند → {gaps}')
    # جدول بالای نوار امضا تمام میشود
    ok(h_lines[-1] < H - (H * 0.12 + 0.4 * 28.35) + 1, f'جدول بالای نوار امضا تمام میشود (ته جدول y={h_lines[-1]:.1f})')

ok(len(doc) == 1, f'با ۱۲ کالا همه در یک برگه جا میشوند → {len(doc)} برگه')

# عرض ستونها = بزرگترین رشته (بدون بیرونزدگی)
v_lines = sorted({round(d['rect'].x0, 1) for d in draws if d['type'] == 's' and d['rect'].x0 == d['rect'].x1})
if len(v_lines) >= 3:
    cw = [round(b - a, 1) for a, b in zip(v_lines, v_lines[1:])]
    ok(abs(sum(cw) - (W - 2 * 34.02)) < 1.5, f'مجموع عرض ستونها دقیقاً محتوای برگه است → مجموع {sum(cw):.1f}')
    ok(min(cw) >= 22, f'عرضها با سقف بیرون نمیزنند → {cw}')
print('PAGES:', len(doc))
