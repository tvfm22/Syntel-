# -*- coding: utf-8 -*-
"""تست داشبورد موبایل بدهی‌ها — اجرا با PYTHONIOENCODING=utf-8"""
import sys, os
sys.path.insert(0, 'D:/Syntel/syntel')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from purchases.models import Cafe, Debt, DebtPayment, PaymentAccount

User = get_user_model()
ok = lambda cond, msg: print(('PASS' if cond else 'FAIL') + ' - ' + msg)

# ---------- آماده‌سازی ----------
TEST_COMPANIES = ['شرکت تست موبایل', 'شرکت خارج از لیست موبایل', 'شرکت حسابدار موبایل', 'شرکت پرداخت جزئی', 'شرکت حذف موبایل']
DebtPayment.objects.filter(company__in=TEST_COMPANIES).delete()
Debt.objects.filter(company__in=TEST_COMPANIES).delete()
PaymentAccount.objects.filter(bank_name='بانک تست ملت').delete()

cafe = Cafe.objects.filter(code='local').first()
if not cafe:
    cafe = Cafe.objects.create(name='کافه محلی', code='local')

admin, _ = User.objects.get_or_create(username='test_admin_mobile')
admin.role = 'admin'; admin.set_password('x'); admin.is_staff = True; admin.save()
ceo, _ = User.objects.get_or_create(username='test_ceo_mobile')
ceo.role = 'ceo'; ceo.set_password('x'); ceo.save()

# کلاینت دسکتاپ (بدون User-Agent موبایل) و کلاینت موبایل (Android)
MOBILE_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36'
c_admin_d = Client(); c_admin_d.force_login(admin)
c_ceo_d = Client(); c_ceo_d.force_login(ceo)
c_admin_m = Client(HTTP_USER_AGENT=MOBILE_UA); c_admin_m.force_login(admin)
c_ceo_m = Client(HTTP_USER_AGENT=MOBILE_UA); c_ceo_m.force_login(ceo)

MOBILE = '/local/debts/mobile/'

# ---------- ۰) تشخیص خودکار دستگاه (بدون انتخاب دستی) ----------
r = c_admin_d.get('/local/debts/')
ok(r.status_code == 200, f'دسکتاپ: لیست بدهی دسکتاپ می‌آید → {r.status_code}')

r = c_admin_m.get('/local/debts/')
ok(r.status_code == 302 and r['Location'] == MOBILE,
   f'موبایل روی لیست بدهی → خودکار به داشبورد موبایل: {r.status_code} {r.get("Location")}')

r = c_ceo_m.get('/local/debts/ceo/')
ok(r.status_code == 302 and r['Location'] == MOBILE,
   f'موبایل روی پرداخت بدهی‌ها → خودکار به داشبورد موبایل: {r.status_code} {r.get("Location")}')

r = c_ceo_m.get('/local/debts/ceo/?period=year')
ok(r['Location'] == MOBILE + '?period=year', f'فیلتر بازه در ریدایرکت موبایل حفظ می‌شود: {r.get("Location")}')

r = c_admin_d.get(MOBILE)
ok(r.status_code == 302 and r['Location'] == '/local/debts/',
   f'دسکتاپ روی URL موبایل → خودکار به لیست بدهی دسکتاپ: {r.status_code} {r.get("Location")}')

r = c_ceo_d.get(MOBILE)
ok(r.status_code == 302 and r['Location'] == '/local/debts/ceo/',
   f'دسکتاپ روی URL موبایل → خودکار به پرداخت بدهی‌ها: {r.status_code} {r.get("Location")}')

# ---------- ۱) GET هردو نقش (موبایل) → 200 ----------
r = c_admin_m.get(MOBILE)
ok(r.status_code == 200, f'admin GET mobile → {r.status_code}')
body = r.content.decode('utf-8')
ok('بدهی تأمین‌کنندگان' in body, 'هدر موبایل نمایش داده می‌شود')
ok('تعریف بدهی جدید' in body, 'ادمین فرم تعریف بدهی را می‌بیند')
ok('persian-datepicker.min.js' in body, 'اسکریپت تقویم شمسی لود می‌شود')
ok('m-period-chip' not in body and '?period=' not in body,
   'فیلتر بازه زمانی از نسخه موبایل حذف شده است')
ok('/pay/' not in body, 'ادمین فرم پرداخت را نمی‌بیند (role-appropriate)')
ok('نسخه دسکتاپ' not in body, 'گزینه انتخاب دستی نسخه حذف شده است')
ok('location.hash' in body, 'بازیابی شیت باز از هش آدرس (پایداری بین برنامه‌ها)')

r = c_ceo_m.get(MOBILE)
ok(r.status_code == 200, f'ceo GET mobile → {r.status_code}')
body2 = r.content.decode('utf-8')
ok('پرداخت خارج از لیست' in body2, 'مدیرعامل فرم پرداخت خارج از لیست را می‌بیند')
ok('<details' in body2 and 'm-offlist' in body2, 'پرداخت خارج از لیست موبایل کشویی است (با لمس باز می‌شود)')
ok('/local/debts/pay-offlist/?from=mobile' in body2, 'فرم خارج از لیست به URL صحیح با from=mobile پست می‌شود')
ok('تعریف بدهی جدید' not in body2, 'مدیرعامل فرم تعریف بدهی را نمی‌بیند')

# ---------- ۱.۲) خلاصه مینیمال: مجموع بدهی (جایگزین آمار سه‌گانه) ----------
r = c_admin_m.get(MOBILE)
body = r.content.decode('utf-8')
ok('مجموع بدهی' in body, 'خلاصه مینیمال «مجموع بدهی» نمایش داده می‌شود')
ok('m-sum-item' not in body, 'آمار سه‌گانه (بدهی در انتظار / جمع مبلغ / پرداخت‌ها) حذف شده است')
ok('m-total-value' in body, 'مقدار مجموع بدهی مینیمال رندر می‌شود')

# ---------- ۱.۲.۱) رنگ اعداد، تیک پرداخت و تعداد پرداخت‌ها (موبایل) ----------
r = c_ceo_m.get(MOBILE)
body = r.content.decode('utf-8')
ok('m-debt-amount.green' not in body and 'm-count-badge green' not in body,
   'عدد سبز پرداخت و نشانگر تعداد پرداخت‌ها حذف شدند')
ok('color: var(--accent-indigo); margin-top: 0.25rem' not in body,
   'عدد کارت بدهی دیگر بنفش نیست (مشکی شد)')
ok('i.fa-check-circle { color: var(--accent-green); }' in body,
   'تیک پرداخت (چک‌دایره) سبز — هم‌رنگ دکمه «ثبت پرداخت»')
_close_rule = body.split('.m-sheet-close {')[1].split('}')[0]
ok('position: absolute' not in _close_rule,
   'دکمه ضربدر شیت مطلق نیست — روی اولویت پرداخت نمی‌افتد')

# ---------- ۱.۳) خلاصه مینیمال دسکتاپ: مجموع بدهی (جایگزین آمار) ----------
r = c_admin_d.get('/local/debts/')
body = r.content.decode('utf-8')
ok('مجموع بدهی' in body and 'debt-total-value' in body, 'ادمین دسکتاپ: خلاصه مینیمال «مجموع بدهی» هست')
ok('mini-stat' not in body and 'پرداخت در این بازه' not in body, 'ادمین دسکتاپ: آمار سه‌گانه حذف شده است')

r = c_ceo_d.get('/local/debts/ceo/')
body_d = r.content.decode('utf-8')
ok('مجموع بدهی' in body_d and 'debt-total-value' in body_d, 'مدیرعامل دسکتاپ: خلاصه مینیمال «مجموع بدهی» هست')
ok('پرداخت ثبت شده' not in body_d and 'مجموع مبلغ بدهی‌ها' not in body_d, 'مدیرعامل دسکتاپ: آمار سه‌گانه حذف شده است')

# منوی سایدبار آیتم نسخه موبایل ندارد
r = c_admin_d.get('/local/debts/')
ok('نسخه موبایل' not in r.content.decode('utf-8'), 'آیتم منوی «نسخه موبایل» از سایدبار حذف شده است')

# ---------- ۱.۵) دسکتاپ مدیرعامل: کشویی خارج از لیست ----------
r = c_ceo_d.get('/local/debts/ceo/')
ok(r.status_code == 200, f'desktop ceo GET → {r.status_code}')
body_d = r.content.decode('utf-8')
ok('<details' in body_d and 'offlist-details' in body_d, 'پرداخت خارج از لیست دسکتاپ کشویی است (با لمس باز می‌شود)')
ok('/local/debts/pay-offlist/' in body_d, 'فرم کشویی دسکتاپ به URL صحیح پست می‌شود')
i_row = body_d.find('class="debt-top-row"')
ok(i_row != -1 and body_d.find('class="debt-total"', i_row) > i_row
   and body_d.find('offlist-details', i_row) > i_row,
   'مدیرعامل دسکتاپ: باکس پرداخت خارج از لیست کنار باکس مجموع بدهی است')
ok('align-self: start' in body_d,
   'مدیرعامل دسکتاپ: با باز شدن کشویی خارج از لیست، باکس مجموع بدهی تغییر ارتفاع نمی‌دهد')
ok('درخواست کالا' in body_d and 'پرداخت بدهی‌ها' in body_d,
   'منوی مدیرعامل: گزینه‌های «درخواست کالا» (تایید درخواست‌ها) و «پرداخت بدهی‌ها» هستند')
ok('داشبورد مدیریت' in body_d and 'درخواست کالا' in body_d,
   'منوی مدیرعامل: منوی کامل برگشته (داشبورد مدیریت، درخواست کالا و…)')

# ---------- ۱.۶) هدر بالایی موبایل: برای همه کاربران فقط Kuker + دکمه منوی همبرگری ----------
# ادمین (غیر سوپرادمین) — نام کافه و تغییر کافه از هدر حذف و به سایدبار منتقل شده‌اند
r = c_admin_m.get(MOBILE)
body = r.content.decode('utf-8')
ok('<span class="mobile-top-bar-title">Kuker</span>' in body,
   'ادمین: نوشته برند هدر موبایل انگلیسی است (Kuker)')
ok('id="menuToggle"' in body, 'ادمین: دکمه منوی همبرگری در هدر هست')
ok('<span class="mobile-top-bar-cafe"' not in body and 'class="mobile-top-bar-actions"' not in body,
   'ادمین: هدر بالایی فقط Kuker و دکمه منو است (بدون نام کافه و تغییر کافه)')
i_menu = body.find('id="menuToggle"')
i_title = body.find('<span class="mobile-top-bar-title"')
ok('grid-template-columns: 1fr auto 1fr' in body,
   'هدر موبایل: عنوان Kuker وسط هدر است (گرید 1fr auto 1fr)')
ok(i_menu < i_title, 'ترتیب هدر موبایل: دکمه منو سپس Kuker')
ok('class="sidebar-switch-cafe"' in body and '/select-cafe/' in body,
   'ادمین: گزینه «تغییر کافه» در منوی همبرگری (سایدبار) به صفحه انتخاب کافه می‌رود')

# دسکتاپ: اسم کافه در سایدبار کاربر (هدر ثابت صفحات دسکتاپ)
r = c_admin_d.get('/local/debts/')
body = r.content.decode('utf-8')
ok('class="user-cafe"' in body and cafe.name in body, 'ادمین: اسم کافه در هدر دسکتاپ (سایدبار) هست')

# مدیرعامل (سوپرادمین) — هدر موبایل مثل بقیه فقط Kuker + دکمه منو؛ تغییر کافه در منوی همبرگری
r = c_ceo_m.get(MOBILE)
body_ceo = r.content.decode('utf-8')
ok('<span class="mobile-top-bar-title">Kuker</span>' in body_ceo and 'id="menuToggle"' in body_ceo,
   'مدیرعامل: هدر موبایل Kuker + دکمه منوی همبرگری دارد')
ok('<span class="mobile-top-bar-cafe"' not in body_ceo and 'class="mobile-top-bar-actions"' not in body_ceo,
   'مدیرعامل: هدر موبایل نام کافه و دکمه تغییر کافه ندارد')
ok('class="sidebar-switch-cafe"' in body_ceo,
   'مدیرعامل: گزینه «تغییر کافه» داخل منوی همبرگری (سایدبار) است')
ok('/select-cafe/' not in body_ceo,
   'مدیرعامل: گزینه «تغییر کافه» به صفحه انتخاب کافه نمی‌رود (مستقیم)')
other_cafe_obj = Cafe.objects.exclude(id=cafe.id).filter(is_active=True).first()
ok(other_cafe_obj is not None, 'کافه «دیگر» برای تست تغییر مستقیم وجود دارد')
if other_cafe_obj:
    ok(f'href="/{other_cafe_obj.code}/all-departments-requests/"' in body_ceo,
       f'تغییر کافه مستقیم به کافه دیگر می‌رود: /{other_cafe_obj.code}/all-departments-requests/')
    r_switch = c_ceo_m.get(f'/{other_cafe_obj.code}/all-departments-requests/')
    body_other = r_switch.content.decode('utf-8')
    ok(r_switch.status_code == 200 and other_cafe_obj.name in body_other,
       f'کلیک «تغییر کافه» → مستقیم در کافه {other_cafe_obj.name} ({r_switch.status_code})')
ok(cafe.name in body_ceo, f'نام کافه ({cafe.name}) همچنان در سایدبار دسکتاپ نمایش داده می‌شود')

# ---------- ۱.۶.۱) پیش‌فرض مدیرعامل بعد از ورود: «درخواست کالا (همه بخش‌ها)» ----------
r_login = Client(HTTP_USER_AGENT=MOBILE_UA).post('/login/', {
    'username': 'test_ceo_mobile', 'password': 'x', 'cafe_code': 'local'})
ok(r_login.status_code == 302
   and '/local/all-departments-requests/' in r_login['Location'],
   f'مدیرعامل بعد از ورود → مستقیم به «درخواست کالا (همه بخش‌ها)»: {r_login.get("Location")}')
i_brand = body_ceo.find('class="sidebar-brand"')
ok(i_brand != -1 and 'href="/local/all-departments-requests/"' in body_ceo[i_brand-400:i_brand],
   'مدیرعامل: لوگوی Kuker به «درخواست کالا (همه بخش‌ها)» می‌رود')

# درخواست‌دهنده (kitchen با کافه) — نباید ببیند
u4, _ = User.objects.get_or_create(username='test_requester_header')
u4.role = 'kitchen'; u4.cafe = cafe; u4.save()
c4 = Client(HTTP_USER_AGENT=MOBILE_UA); c4.force_login(u4)
r = c4.get('/local/mobile-dashboard/')
body4 = r.content.decode('utf-8')
ok(r.status_code == 200, f'kitchen به داشبورد موبایل دسترسی دارد → {r.status_code}')
ok('<span class="mobile-top-bar-cafe"' not in body4 and 'class="user-cafe"' not in body4,
   'درخواست‌دهنده (kitchen) اسم کافه را در هدر نمی‌بیند')

# ---------- ۲) ادمین بدهی ثبت می‌کند با ?from=mobile → بازگشت به موبایل ----------
r = c_admin_m.post('/local/debts/?from=mobile', {
    'company': 'شرکت تست موبایل',
    'amount': '15000000',
    'debt_date': '1404/05/17',  # شمسی
    'priority': 'high',
    'description': 'بدهی برای تست موبایل',
})
ok(r.status_code == 302, f'admin POST بدهی → {r.status_code}')
ok(r['Location'] == MOBILE, f'بازگشت به داشبورد موبایل: {r["Location"]}')
d = Debt.objects.get(company='شرکت تست موبایل')
ok(d.status == 'pending' and d.priority == 'high', 'بدهی با وضعیت در انتظار و اولویت بالا ثبت شد')
ok(d.cafe == cafe, 'بدهی به کافه درست متصل است')

# ---------- ۲.۵) کارت خلاصه + شیت جزئیات بعد از ایجاد داده ----------
r = c_admin_m.get(MOBILE)
body = r.content.decode('utf-8')
ok(f'data-sheet="debt-{d.id}"' in body, 'کارت بدهی خلاصه لمسی است و به شیت متصل است')
ok(f'data-sheet-content="debt-{d.id}"' in body, 'جزئیات کامل بدهی در شیت سمت سرور رندر شده است')
ok('اولویت بالا' in body, 'اولویت در شیت جزئیات نمایش داده می‌شود')
ok('بدهی برای تست موبایل' in body, 'توضیحات در شیت جزئیات نمایش داده می‌شود')

r = c_ceo_m.get(MOBILE)
body2 = r.content.decode('utf-8')
ok(f'data-sheet="debt-{d.id}"' in body2, 'مدیرعامل کارت خلاصه بدهی را می‌بیند')
ok('/pay/?from=mobile' in body2, 'فرم ثبت پرداخت داخل شیت جزئیات است')
ok('ثبت پرداخت این بدهی' in body2, 'فرم پرداخت داخل شیت عنوان دارد')

# ---------- ۳) ادمین بدون from=mobile (دسکتاپ) → دسکتاپ ----------
r = c_admin_d.post('/local/debts/', {
    'company': 'شرکت تست موبایل', 'amount': '500000', 'priority': 'low',
})
ok(r['Location'] == '/local/debts/', f'دسکتاپ بعد از POST به دسکتاپ می‌رود: {r["Location"]}')

# ---------- ۴) مدیرعامل پرداخت را ثبت می‌کند ?from=mobile → بازگشت به موبایل ----------
r = c_ceo_m.post(f'/local/debts/{d.id}/pay/?from=mobile', {
    'paid_amount': '15000000',
})
ok(r.status_code == 302, f'ceo POST پرداخت → {r.status_code}')
ok(r['Location'] == MOBILE, f'بازگشت به داشبورد موبایل: {r["Location"]}')
d.refresh_from_db()
ok(d.status == 'paid' and d.paid_at is not None, 'پرداخت تایید شد و تاریخ به صورت سیستمی ثبت شد')
ok(d.paid_by == ceo, 'پرداخت توسط مدیرعامل ثبت شد')
p = DebtPayment.objects.get(debt=d)
ok(p.amount == 15000000 and p.paid_by == ceo, 'پرداخت کامل یک ردیف در لیست پرداخت‌ها ساخت')

# ---------- ۵) پرداخت خارج از لیست ?from=mobile ----------
r = c_ceo_m.post('/local/debts/pay-offlist/?from=mobile', {
    'company': 'شرکت خارج از لیست موبایل',
    'amount': '3000000',
    'description': 'پرداخت نقدی خارج از لیست',
})
ok(r.status_code == 302 and r['Location'] == MOBILE, f'ثبت خارج از لیست → بازگشت به موبایل: {r["Location"]}')
off = Debt.objects.get(company='شرکت خارج از لیست موبایل')
ok(off.is_off_list and off.status == 'paid' and off.paid_at is not None, 'پرداخت خارج از لیست ثبت شد')
off_p = DebtPayment.objects.get(debt__isnull=True, company='شرکت خارج از لیست موبایل')
ok(off_p.amount == 3000000 and off_p.paid_by == ceo, 'ردیف پرداخت خارج از لیست (بدون اتصال به بدهی) ثبت شد')

# ---------- ۶) نمایش در داشبورد موبایل ----------
r = c_ceo_m.get(MOBILE + '?period=week')
body = r.content.decode('utf-8')
ok('شرکت تست موبایل' in body, 'بدهی پرداخت‌شده در لیست پرداخت‌ها نمایش داده می‌شود')
ok('خارج از لیست' in body, 'نشان «خارج از لیست» نمایش داده می‌شود')
ok('15000000' not in body and '۱۵' in body, 'مبالغ با اعداد فارسی و جداکننده نمایش داده می‌شوند')
ok('jalali' not in body, 'تاریخ‌ها شمسی هستند (بدون فیلتر خام میلادی)')
ok(f'data-sheet="paid-{p.id}"' in body, 'کارت پرداخت خلاصه لمسی است و به شیت متصل است')
ok(f'data-sheet-content="paid-{p.id}"' in body, 'جزئیات کامل پرداخت در شیت رندر شده است')
ok('تاریخ و زمان پرداخت' in body, 'تاریخ و ساعت سیستمی پرداخت در شیت نمایش داده می‌شود')

r = c_admin_m.get(MOBILE + '?period=year')
body = r.content.decode('utf-8')
ok('شرکت تست موبایل' in body, 'ادمین پرداخت مدیر را در لیست پرداخت‌ها می‌بیند')
ok('پرداخت توسط' in body, 'نام پرداخت‌کننده نمایش داده می‌شود')

# ---------- ۶.۴) پرداخت جزئی: باقی‌مانده در لیست بدهی + پرداخت مجدد به عنوان پرداخت جدید ----------
r = c_admin_m.post('/local/debts/?from=mobile', {
    'company': 'شرکت پرداخت جزئی',
    'amount': '10,000,000',  # با کاما — سرور جداکننده سه‌رقمی را حذف می‌کند
    'priority': 'medium',
})
ok(r.status_code == 302 and r['Location'] == MOBILE, f'بدهی با مبلغ کامادار ثبت شد → {r["Location"]}')
d3 = Debt.objects.get(company='شرکت پرداخت جزئی')
ok(d3.amount == 10000000, 'مبلغ کامادار (10,000,000) درست ذخیره شد')

# پرداخت اول: ۵۰٪ با کاما → بدهی باز می‌ماند و باقی‌مانده نمایش داده می‌شود
r = c_ceo_m.post(f'/local/debts/{d3.id}/pay/?from=mobile', {'paid_amount': '5,000,000'})
ok(r.status_code == 302, f'پرداخت جزئی اول → {r.status_code}')
d3.refresh_from_db()
ok(d3.status == 'pending' and d3.remaining == 5000000, 'پرداخت ۵۰٪ → بدهی باز می‌ماند (باقی‌مانده ۵٬۰۰۰٬۰۰۰)')
p1 = DebtPayment.objects.get(debt=d3, amount=5000000)

body3 = c_ceo_m.get(MOBILE + '?period=week').content.decode('utf-8')
ok(f'data-sheet="debt-{d3.id}"' in body3, 'بدهی جزئی با باقی‌مانده در لیست بدهی‌ها مانده است')
ok('پرداخت شده' in body3, 'نشان پرداخت جزئی روی کارت بدهی نمایش داده می‌شود')
ok(f'data-sheet="paid-{p1.id}"' in body3, 'پرداخت جزئی در لیست پرداخت‌ها آمده است')

# پرداخت دوم: ۳٬۹۹۹٬۹۹۹ → باقی‌مانده دقیقاً ۱٬۰۰۰٬۰۰۱ → نه کمتر یا مساوی ۱٬۰۰۰٬۰۰۰ → باز در لیست بدهی می‌ماند
r = c_ceo_m.post(f'/local/debts/{d3.id}/pay/?from=mobile', {'paid_amount': '3999999'})
d3.refresh_from_db()
ok(d3.status == 'pending' and d3.remaining == 1000001,
   'باقی‌مانده ۱٬۰۰۰٬۰۰۱ → هنوز در لیست بدهی می‌ماند (قانون: بستن فقط با باقی‌مانده ≤ ۱٬۰۰۰٬۰۰۰)')

# پرداخت سوم: بقیه + رسید → باقی‌مانده دقیقاً ۱٬۰۰۰٬۰۰۰ → بدهی بسته می‌شود؛ هر سه پرداخت در لیست پرداخت‌ها می‌مانند
photo_path = os.path.join(os.path.dirname(__file__), 'test_receipt.jpg')
p3_data = {'paid_amount': '1'}
if os.path.exists(photo_path):
    from django.core.files.uploadedfile import SimpleUploadedFile
    with open(photo_path, 'rb') as f:
        p3_data['payment_photo'] = SimpleUploadedFile('receipt_del_test.jpg', f.read(), content_type='image/jpeg')
r = c_ceo_m.post(f'/local/debts/{d3.id}/pay/?from=mobile', p3_data)
d3.refresh_from_db()
ok(d3.status == 'paid' and d3.remaining == 1000000,
   'باقی‌مانده ۱٬۰۰۰٬۰۰۰ → بدهی بسته و از لیست بدهی‌ها حذف شد (باقی‌مانده ۱٬۰۰۰٬۰۰۰)')
ok(d3.payments.count() == 3, 'هر سه پرداخت ردیف‌های جداگانه در لیست پرداخت‌ها هستند')
p3 = d3.payments.order_by('-paid_at', '-id').first()
if os.path.exists(photo_path):
    ok(bool(p3.payment_photo), 'رسید تصویر روی پرداخت ذخیره شد')

body3 = c_ceo_m.get(MOBILE + '?period=week').content.decode('utf-8')
ok(f'data-sheet="paid-{p1.id}"' in body3, 'پرداخت اول به عنوان ردیف جداگانه در لیست پرداخت‌ها هست')
ok(f'data-sheet="debt-{d3.id}"' not in body3, 'بدهی بسته‌شده دیگر در لیست بدهی‌ها نیست')

# ---------- ۶.۴.۱) حذف پرداخت توسط مدیرعامل: حذف ردیف + باز شدن مجدد بدهی + حذف فایل رسید ----------
if os.path.exists(photo_path):
    photo_file = p3.payment_photo.path

    # صفحه تأیید حذف (GET) — مدیرعامل/مدیر
    r = c_ceo_m.get(f'/local/debts/payments/{p3.id}/delete/')
    ok(r.status_code == 200 and 'تأیید حذف پرداخت بدهی' in r.content.decode('utf-8'),
       f'صفحه تأیید حذف پرداخت باز می‌شود → {r.status_code}')

    # POST حذف → پرداخت و فایل رسیدش حذف می‌شوند؛ باقی‌مانده ۱٬۰۰۰٬۰۰۱ → بدهی دوباره باز می‌شود
    r = c_ceo_m.post(f'/local/debts/payments/{p3.id}/delete/')
    ok(r.status_code == 302, f'حذف پرداخت → {r.status_code}')
    ok(not DebtPayment.objects.filter(id=p3.id).exists(), 'ردیف پرداخت از دیتابیس حذف شد')
    ok(not os.path.exists(photo_file), 'فایل تصویر رسید از دیسک حذف شد')
    d3.refresh_from_db()
    ok(d3.status == 'pending' and d3.remaining == 1000001,
       'حذف پرداخت بستن → بدهی دوباره باز شد (باقی‌مانده ۱٬۰۰۰٬۰۰۱)')
else:
    print('SKIP - فایل test_receipt.jpg وجود ندارد؛ بخش حذف پرداخت بدون رسید تست می‌شود')

# پرداخت اول (۵٬۰۰۰٬۰۰۰) حذف می‌شود → بدهی در لیست بدهی‌ها می‌ماند
r = c_ceo_m.post(f'/local/debts/payments/{p1.id}/delete/')
ok(r.status_code == 302, f'حذف پرداخت اول → {r.status_code}')
ok(not DebtPayment.objects.filter(id=p1.id).exists(), 'ردیف پرداخت اول حذف شد')
d3.refresh_from_db()
rem_after = 6000001 if os.path.exists(photo_path) else 6000000
ok(d3.status == 'pending' and d3.remaining == rem_after,
   f'حذف پرداخت → بدهی باز مانده (باقی‌مانده {rem_after:,})')
body3 = c_ceo_m.get(MOBILE + '?period=week').content.decode('utf-8')
ok(f'data-sheet="debt-{d3.id}"' in body3, 'بدهی بازشده دوباره در لیست بدهی‌ها آمده است')

# ---------- ۶.۵) حساب‌های پرداخت (R8): تعریف توسط ادمین + انتخاب بانک در ثبت پرداخت ----------
# ادمین (دسکتاپ) حساب پرداخت تعریف می‌کند → بازگشت به لیست بدهی
r = c_admin_d.post('/local/payment-accounts/', {
    'bank_name': 'بانک تست ملت',
    'account_name': 'حساب جاری کافه',
    'account_number': '123456',
    'card_number': '6037991234567890',
})
ok(r.status_code == 302 and r['Location'] == '/local/debts/',
   f'ادمین حساب پرداخت می‌سازد → بازگشت به لیست بدهی: {r["Location"]}')
acc = PaymentAccount.objects.get(bank_name='بانک تست ملت')
ok(acc.cafe == cafe and acc.is_active, 'حساب پرداخت برای کافه درست ثبت شد')

# دکمه گوشه ادمین دسکتاپ + نمایش حساب در مودال
r = c_admin_d.get('/local/debts/')
body = r.content.decode('utf-8')
ok('openAccountsModal' in body, 'دکمه گوشه «تعریف حساب‌های پرداخت» در دسکتاپ هست')
ok('بانک تست ملت' in body and 'شماره حساب: 123456' in body, 'حساب در مودال دسکتاپ نمایش داده می‌شود')

# دکمه گوشه ادمین موبایل + شیت حساب‌ها
r = c_admin_m.get(MOBILE)
body = r.content.decode('utf-8')
ok('openAccSheet' in body and 'accSheet' in body, 'دکمه گوشه «حساب‌ها» و شیت حساب‌ها در موبایل هست')
ok('بانک تست ملت' in body, 'حساب در شیت موبایل نمایش داده می‌شود')

# ادمین بدهی جدید ثبت می‌کند تا فرم پرداخت داخل شیت رندر شود
r = c_admin_m.post('/local/debts/?from=mobile', {
    'company': 'شرکت حسابدار موبایل',
    'amount': '5000000',
    'priority': 'medium',
})
ok(r['Location'] == MOBILE, 'بدهی دوم ثبت شد و به موبایل برگشت')
d2 = Debt.objects.get(company='شرکت حسابدار موبایل')

# مدیرعامل: سلکت بانک در فرم‌های پرداخت (موبایل و دسکتاپ)
r = c_ceo_m.get(MOBILE)
body2 = r.content.decode('utf-8')
ok(body2.count('name="account_id"') >= 2,
   f'سلکت حساب در فرم خارج از لیست و شیت پرداخت هست ({body2.count("name=\"account_id\"")} مورد)')
ok('بانک تست ملت' in body2, 'حساب ادمین در سلکت بانک مدیرعامل (موبایل) هست')

r = c_ceo_d.get('/local/debts/ceo/')
body_d = r.content.decode('utf-8')
ok('pay-account-select' in body_d, 'سلکت بانک در فرم پرداخت دسکتاپ هست')
ok('بانک تست ملت' in body_d, 'حساب ادمین در سلکت دسکتاپ هست')

# پرداخت داخل شیت با انتخاب حساب → حساب روی بدهی ذخیره می‌شود
r = c_ceo_m.post(f'/local/debts/{d2.id}/pay/?from=mobile', {
    'paid_amount': '5000000',
    'account_id': str(acc.id),
})
ok(r.status_code == 302 and r['Location'] == MOBILE, f'پرداخت با حساب → {r["Location"]}')
d2.refresh_from_db()
ok(d2.status == 'paid' and d2.account == acc, 'حساب پرداخت روی بدهی ذخیره شد')
p2 = DebtPayment.objects.get(debt=d2)
ok(p2.account == acc, 'حساب روی ردیف پرداخت (DebtPayment) ذخیره شد')

# پرداخت خارج از لیست با انتخاب حساب
r = c_ceo_m.post('/local/debts/pay-offlist/?from=mobile', {
    'company': 'شرکت حسابدار موبایل',
    'amount': '2500000',
    'account_id': str(acc.id),
})
ok(r.status_code == 302 and r['Location'] == MOBILE, f'پرداخت خارج از لیست با حساب → {r["Location"]}')
off2 = Debt.objects.filter(company='شرکت حسابدار موبایل', is_off_list=True).first()
ok(off2 is not None and off2.account == acc, 'حساب روی پرداخت خارج از لیست ذخیره شد')
off2_p = DebtPayment.objects.get(debt__isnull=True, company='شرکت حسابدار موبایل')
ok(off2_p.account == acc, 'حساب روی ردیف پرداخت خارج از لیست ذخیره شد')

# ستون «حساب پرداخت» در دسکتاپ (ادمین + مدیرعامل)
r = c_admin_d.get('/local/debts/?period=year')
body = r.content.decode('utf-8')
ok('حساب پرداخت' in body and 'بانک تست ملت' in body, 'ستون «حساب پرداخت» در لیست پرداخت‌های دسکتاپ ادمین هست')

r = c_ceo_d.get('/local/debts/ceo/?period=year')
body_d = r.content.decode('utf-8')
ok('حساب پرداخت' in body_d and 'بانک تست ملت' in body_d, 'ستون «حساب پرداخت» در دسکتاپ مدیرعامل هست')

# ردیف «پرداخت از حساب» در شیت پرداخت موبایل
r = c_ceo_m.get(MOBILE + '?period=week')
body2 = r.content.decode('utf-8')
ok('<span>پرداخت از حساب</span>' in body2 and 'بانک تست ملت' in body2,
   'ردیف «پرداخت از حساب» در شیت پرداخت موبایل هست')

# ---------- ۶.۹) جداکننده کاما در فرم پرداخت مدیرعامل (دسکتاپ و موبایل) ----------
r = c_ceo_d.get('/local/debts/ceo/')
body_d = r.content.decode('utf-8')
ok('js-comma' in body_d, 'فرم پرداخت دسکتاپ جداکننده سه‌رقمی (کاما) دارد')

r = c_ceo_m.get(MOBILE)
body2 = r.content.decode('utf-8')
ok('js-comma' in body2, 'فرم‌های پرداخت موبایل (شیت + خارج از لیست) جداکننده سه‌رقمی دارند')

# ---------- ۶.۱۰) ویرایش پرداخت توسط مدیرعامل (R10) ----------
# دکمه‌های ویرایش/حذف پرداخت: دسکتاپ (ستون عملیات) و موبایل (داخل شیت پرداخت)
r = c_ceo_d.get('/local/debts/ceo/')
body_d = r.content.decode('utf-8')
ok('عملیات' in body_d and f'/local/debts/payments/{p2.id}/edit/' in body_d
   and f'/local/debts/payments/{p2.id}/delete/' in body_d,
   'ستون عملیات + لینک ویرایش/حذف پرداخت در جدول دسکتاپ مدیرعامل هست')

r = c_ceo_m.get(MOBILE)
body2 = r.content.decode('utf-8')
ok('ویرایش پرداخت' in body2 and 'حذف پرداخت' in body2,
   'دکمه‌های «ویرایش/حذف پرداخت» در شیت پرداخت موبایل هست')
ok('حذف بدهی' not in body2, 'مدیرعامل دکمه حذف بدهی را نمی‌بیند (فقط ادمین)')

# صفحه ویرایش پرداخت (GET) — فرم با فیلدها
r = c_ceo_d.get(f'/local/debts/payments/{p2.id}/edit/')
ok(r.status_code == 200 and 'ویرایش پرداخت' in r.content.decode('utf-8'),
   f'صفحه ویرایش پرداخت برای مدیرعامل می‌آید → {r.status_code}')

# ویرایش: مبلغ با کاما + حذف حساب → بدهی دوباره باز می‌شود (باقی‌مانده ۲٬۰۰۰٬۰۰۰ > ۱٬۰۰۰٬۰۰۰)
r = c_ceo_d.post(f'/local/debts/payments/{p2.id}/edit/', {
    'amount': '3,000,000',
    'description': 'پرداخت ویرایش‌شده',
    'account_id': '',
})
ok(r.status_code == 302 and r['Location'] == '/local/debts/ceo/',
   f'مدیرعامل پرداخت را ویرایش می‌کند → {r["Location"]}')
p2.refresh_from_db()
ok(p2.amount == 3000000 and p2.description == 'پرداخت ویرایش‌شده', 'مبلغ و توضیحات پرداخت ویرایش شد')
d2.refresh_from_db()
ok(d2.status == 'pending' and d2.remaining == 2000000,
   'کاهش مبلغ پرداخت → بدهی دوباره باز شد (باقی‌مانده ۲٬۰۰۰٬۰۰۰)')
ok(d2.account == acc, 'حساب قبلی بدهی بعد از ویرایش پرداخت حفظ شد')

# ---------- ۶.۱۱) ویرایش و حذف بدهی توسط ادمین (R10) ----------
# دکمه‌های عملیات در جدول دسکتاپ ادمین
r = c_admin_d.get('/local/debts/')
body = r.content.decode('utf-8')
ok('عملیات' in body and f'/local/debts/{d2.id}/edit/' in body and f'/local/debts/{d2.id}/delete/' in body,
   'ستون عملیات + ویرایش/حذف در جدول دسکتاپ ادمین هست')

# دکمه‌های ویرایش/حذف در شیت بدهی موبایل (فقط ادمین)
r = c_admin_m.get(MOBILE)
body = r.content.decode('utf-8')
ok('ویرایش بدهی' in body and 'حذف بدهی' in body, 'دکمه‌های ویرایش/حذف در شیت بدهی موبایل ادمین هست')

# صفحه ویرایش بدهی (GET) — فرم با مقادیر قبلی
r = c_admin_d.get(f'/local/debts/{d2.id}/edit/')
body_edit = r.content.decode('utf-8')
ok(r.status_code == 200 and 'ویرایش بدهی' in body_edit and 'شرکت حسابدار موبایل' in body_edit,
   f'صفحه ویرایش بدهی با مقادیر قبلی می‌آید → {r.status_code}')

# ویرایش بدهی (مبلغ با کاما + اولویت بالا) → برای مدیرعامل هم اعمال می‌شود
r = c_admin_d.post(f'/local/debts/{d2.id}/edit/', {
    'company': 'شرکت حسابدار موبایل',
    'amount': '5,500,000',
    'debt_date': '1404/05/17',
    'priority': 'high',
    'description': 'بدهی ویرایش‌شده',
})
ok(r.status_code == 302 and r['Location'] == '/local/debts/',
   f'ادمین بدهی را ویرایش می‌کند → {r["Location"]}')
d2.refresh_from_db()
ok(d2.amount == 5500000 and d2.priority == 'high' and d2.description == 'بدهی ویرایش‌شده',
   'فیلدهای بدهی (مبلغ/اولویت/توضیحات) ویرایش شد')

body2 = c_ceo_m.get(MOBILE).content.decode('utf-8')
ok('بدهی ویرایش‌شده' in body2, 'ویرایش بدهی برای مدیرعامل هم اعمال شد')

# حذف بدهی پرداخت‌شده ممنوع است
r = c_admin_d.post(f'/local/debts/{d3.id}/delete/')
ok(r.status_code == 302 and Debt.objects.filter(id=d3.id).exists(),
   'بدهی پرداخت‌شده قابل حذف نیست (محافظت)')

# ادمین بدهی جدید ثبت می‌کند → مدیرعامل می‌بیند → ادمین حذف می‌کند → از هر دو طرف حذف می‌شود
r = c_admin_d.post('/local/debts/', {
    'company': 'شرکت حذف موبایل', 'amount': '1000000', 'priority': 'low',
})
ok(r.status_code == 302, f'بدهی جدید برای تست حذف ثبت شد → {r.status_code}')
dx = Debt.objects.get(company='شرکت حذف موبایل')
ok(dx.status == 'pending', 'بدهی جدید در انتظار پرداخت ساخت')
ok('شرکت حذف موبایل' in c_ceo_m.get(MOBILE).content.decode('utf-8'),
   'بدهی جدید در داشبورد مدیرعامل هست')

r = c_admin_d.post(f'/local/debts/{dx.id}/delete/')
ok(r.status_code == 302 and not Debt.objects.filter(id=dx.id).exists(),
   f'ادمین بدهی بدون پرداخت را حذف کرد → {r["Location"]}')
ok('شرکت حذف موبایل' not in c_ceo_m.get(MOBILE).content.decode('utf-8'),
   'حذف بدهی برای مدیرعامل هم اعمال شد')

# دسترسی: مدیرعامل به ویرایش/حذف بدهی دسترسی ندارد؛ ادمین به ویرایش پرداخت دسترسی ندارد
r = c_ceo_d.get(f'/local/debts/{d2.id}/edit/')
ok(r.status_code in (302, 403), f'مدیرعامل به ویرایش بدهی دسترسی ندارد → {r.status_code}')
r = c_ceo_d.post(f'/local/debts/{d2.id}/delete/')
ok(r.status_code in (302, 403), f'مدیرعامل به حذف بدهی دسترسی ندارد → {r.status_code}')
# طبق قانون پروژه (views.role_required) ادمین به همه صفحات دسترسی دارد — اما دکمه ویرایش پرداخت
# فقط برای مدیرعامل/مدیر در رابط کاربری نمایش داده می‌شود
r = c_admin_d.get(f'/local/debts/payments/{p2.id}/edit/')
ok(r.status_code == 200, f'ادمین طبق قانون پروژه به ویرایش پرداخت دسترسی دارد → {r.status_code}')

# ---------- ۷) دسکتاپ: لیست بدهی و پرداخت‌ها بدون ریدایرکت، با فیلتر بازه ----------
r = c_admin_d.get('/local/debts/?period=year')
body = r.content.decode('utf-8')
ok('شرکت تست موبایل' in body and 'خارج از لیست' in body, 'دسکتاپ ادمین لیست بدهی و پرداخت‌ها را می‌بیند')

# ---------- ۸) دسترسی نقش نامعتبر ----------
# kitchen کاربر عادی است و به کافه محلی تعلق ندارد → مiddleware کافه 404 می‌دهد
u3 = User.objects.get_or_create(username='test_waiter_mobile')[0]
u3.role = 'kitchen'; u3.save()
c3 = Client(HTTP_USER_AGENT=MOBILE_UA); c3.force_login(u3)
r = c3.get(MOBILE)
ok(r.status_code in (302, 404), f'نقش kitchen به موبایل دسترسی ندارد → {r.status_code}')

# ---------- ۹) تأمین‌کننده: شماره کارت/شبا + سرچ و انتخاب در ثبت بدهی ----------
from purchases.models import Supplier
TEST_SUPPLIER = 'تأمین‌کننده تست بانکی'
Supplier.objects.filter(name=TEST_SUPPLIER).delete()

# ایجاد تأمین‌کننده با اطلاعات بانکی (کارت + شبا)
r = c_admin_d.post('/local/suppliers/create/', {
    'name': TEST_SUPPLIER,
    'supplier_type': 'company',
    'bank_name': 'بانک صادرات',
    'card_number': '6037697512345678',
    'sheba_number': 'IR060160000000100000000001',
    'bank_account': '0101234567001',
})
ok(r.status_code == 302, f'ایجاد تأمین‌کننده با کارت/شبا → {r.status_code}')
sup = Supplier.objects.get(name=TEST_SUPPLIER)
ok(sup.card_number == '6037697512345678' and sup.sheba_number == 'IR060160000000100000000001',
   'شماره کارت و شبا تأمین‌کننده ذخیره شد')
ok(sup.bank_name == 'بانک صادرات', 'نام بانک تأمین‌کننده ذخیره شد')

# ویرایش تأمین‌کننده: تغییر شماره کارت
r = c_admin_d.post(f'/local/suppliers/{sup.id}/update/', {
    'name': TEST_SUPPLIER, 'supplier_type': 'company',
    'bank_name': 'بانک صادرات', 'card_number': '6037697512345679',
    'sheba_number': 'IR060160000000100000000001', 'bank_account': '0101234567001',
})
ok(r.status_code == 302, f'ویرایش تأمین‌کننده → {r.status_code}')
sup.refresh_from_db()
ok(sup.card_number == '6037697512345679', 'شماره کارت ویرایش شد')

# فرم ثبت بدهی: انتخاب تأمین‌کننده از لیست (supplier_id) → بدهی متصل می‌شود
r = c_admin_d.post('/local/debts/', {
    'supplier_id': str(sup.id),
    'company': '',  # از نام تأمین‌کننده پر می‌شود
    'amount': '8000000',
    'priority': 'high',
})
ok(r.status_code == 302, f'ثبت بدهی با انتخاب تأمین‌کننده → {r.status_code}')
d_sup = Debt.objects.get(company=TEST_SUPPLIER)
ok(d_sup.supplier == sup and d_sup.status == 'pending', 'بدهی به تأمین‌کننده متصل شد')

# داده تأمین‌کنندگان (json_script) و picker در صفحات بدهی
body = c_admin_d.get('/local/debts/').content.decode('utf-8')
ok('id="suppliers-data"' in body and 'sup-picker' in body, 'ادمین: داده و picker سرچ تأمین‌کننده در فرم ثبت بدهی هست')
ok('6037697512345679' in body, 'داده کارت تأمین‌کننده برای سرچ سمت کلاینت ارسال شده است')

body = c_ceo_d.get('/local/debts/ceo/').content.decode('utf-8')
ok('id="suppliers-data"' in body and 'sup-picker' in body, 'مدیرعامل: picker سرچ در فرم پرداخت خارج از لیست هست')

# مدیرعامل: کلیک روی نام تأمین‌کننده → الرت اطلاعات بانکی (با دکمه کپی)
ok(f'data-name="{TEST_SUPPLIER}"' in body, 'نام تأمین‌کننده در جدول بدهی‌ها قابل کلیک است (data-name)')
ok('data-card="6037697512345679"' in body, 'شماره کارت در دکمه اطلاعات بانکی هست')
ok('data-sheba="IR060160000000100000000001"' in body, 'شماره شبا در دکمه اطلاعات بانکی هست')
ok('supinfo-modal' in body and 'supinfo-copy' in body, 'الرت اطلاعات بانکی و دکمه کپی رندر می‌شود')

# موبایل: اطلاعات بانکی تأمین‌کننده داخل شیت بدهی + picker
body_m = c_ceo_m.get(MOBILE).content.decode('utf-8')
ok('اطلاعات بانکی تأمین‌کننده' in body_m, 'موبایل: بخش اطلاعات بانکی در شیت بدهی هست')
ok('6037697512345679' in body_m and 'IR060160000000100000000001' in body_m, 'موبایل: کارت و شبا در شیت نمایش داده می‌شود')
ok('m-copy-btn' in body_m, 'موبایل: دکمه کپی شماره‌های بانکی هست')
ok('sup-picker' in body_m, 'موبایل: picker سرچ تأمین‌کننده در فرم‌ها هست')

# پرداخت خارج از لیست با انتخاب تأمین‌کننده → نام از تأمین‌کننده پر می‌شود
r = c_ceo_m.post('/local/debts/pay-offlist/?from=mobile', {
    'supplier_id': str(sup.id),
    'company': '',
    'amount': '2000000',
})
ok(r.status_code == 302, f'پرداخت خارج از لیست با تأمین‌کننده → {r.status_code}')
off_sup = Debt.objects.filter(company=TEST_SUPPLIER, is_off_list=True).first()
ok(off_sup is not None and off_sup.supplier == sup, 'پرداخت خارج از لیست به تأمین‌کننده متصل شد')

# ویرایش بدهی: تأمین‌کننده از قبل انتخاب شده است (hidden supplier_id)
body_edit = c_admin_d.get(f'/local/debts/{d_sup.id}/edit/').content.decode('utf-8')
ok(f'name="supplier_id" class="sup-hidden" value="{sup.id}"' in body_edit,
   'ویرایش بدهی: تأمین‌کننده فعلی از قبل انتخاب شده است')

# پاک‌سازی
DebtPayment.objects.filter(company__in=TEST_COMPANIES).delete()
Debt.objects.filter(company__in=TEST_COMPANIES).delete()
Debt.objects.filter(company=TEST_SUPPLIER).delete()
Supplier.objects.filter(name=TEST_SUPPLIER).delete()
PaymentAccount.objects.filter(bank_name='بانک تست ملت').delete()
print('\nتمام تست‌ها انجام شد.')
