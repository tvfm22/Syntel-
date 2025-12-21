# خلاصه تغییرات - دسته‌بندی کاربران و دسترسی‌ها

## تاریخ: ۲۱ دسامبر ۲۰۲۵

## تغییرات اعمال شده:

### ۱. اضافه کردن نقش‌های جدید به User Model

**فایل**: `purchases/models.py`

نقش‌های جدید اضافه شده:
- ✅ **کانتر (counter)** - نقش قبلاً وجود داشت، برچسب به "کانتر" تغییر کرد
- ✅ **صندوق (cashier)** - نقش جدید
- ✅ **تاسیسات (facilities)** - نقش جدید

```python
ROLE_CHOICES = [
    ('kitchen', 'آشپزخانه'),
    ('bar', 'بار'),
    ('counter', 'کانتر'),
    ('cashier', 'صندوق'),
    ('facilities', 'تاسیسات'),
    ('purchasing', 'خرید'),
    ('manager', 'مدیر'),
]
```

### ۲. بروزرسانی دسترسی صفحه درخواست خرید

**فایل**: `purchases/views.py`

همه نقش‌های زیر اکنون می‌توانند درخواست خرید ثبت کنند:

```python
@role_required(["kitchen", "bar", "counter", "cashier", "facilities", "purchasing", "manager"])
def purchase_request_form(request):
```

### ۳. بروزرسانی اسکریپت‌های ایجاد کاربر

**فایل‌های بروزرسانی شده:**
- ✅ `create_users.py` - اضافه کردن کاربران cashier و facilities
- ✅ `purchases/management/commands/seed_users.py` - اضافه کردن کاربران جدید
- ✅ `purchases/management/commands/setup_users.py` - اضافه کردن کاربران جدید
- ✅ `create_department_users.py` - اسکریپت جدید برای ایجاد کاربران دپارتمان‌ها

### ۴. تنظیم Admin Panel

**فایل**: `purchases/admin.py`

Admin panel برای مدیریت کاربران پیکربندی شد:
- نمایش لیست کاربران با نقش‌ها
- فیلتر بر اساس نقش
- امکان ویرایش نقش کاربران
- جستجوی کاربران

### ۵. Migration

**فایل**: `purchases/migrations/0033_alter_user_role.py`

Migration برای اعمال تغییرات ROLE_CHOICES ایجاد و اجرا شد.

## کاربران ایجاد شده:

### کاربران فعلی در سیستم:
1. **admin** (manager) - مدیر / admin123
2. **purchasing** (purchasing) - خرید / purchase123
3. **kitchen** (kitchen) - آشپزخانه / kitchen123
4. **bar** (bar) - بار / bar123
5. **counter** (counter) - کانتر / counter123
6. **cashier** (cashier) - صندوق / cashier123 ✨ جدید
7. **facilities** (facilities) - تاسیسات / facilities123 ✨ جدید

## نحوه استفاده:

### ۱. ورود با کاربران دپارتمان:

```
آشپزخانه: kitchen / kitchen123
بار: bar / bar123
کانتر: counter / counter123
صندوق: cashier / cashier123
تاسیسات: facilities / facilities123
```

### ۲. ثبت درخواست خرید:

همه کاربران بالا می‌توانند:
1. به صفحه درخواست خرید دسترسی داشته باشند
2. محصولات مورد نیاز را انتخاب کنند
3. تعداد و توضیحات را وارد کنند
4. درخواست را ثبت کنند

### ۳. تعریف کاربر جدید:

**از طریق پنل مدیریت کاربران** (فقط مدیر):
1. به `/users/create/` بروید
2. نام کاربری و رمز عبور وارد کنید
3. نقش مورد نظر را انتخاب کنید (آشپزخانه، بار، کانتر، صندوق، تاسیسات، خرید، مدیر)
4. اطلاعات شخصی را وارد کنید
5. ذخیره کنید

**از طریق Admin Panel**:
1. به `/admin` بروید
2. بخش "Users" را انتخاب کنید
3. "Add User" را کلیک کنید
4. فرم را پر کنید و نقش را انتخاب کنید

**از طریق اسکریپت**:
```bash
python create_department_users.py
```

## دسترسی‌ها:

| نقش | درخواست خرید | لیست درخواست‌ها | تایید | مدیریت کاربران |
|-----|-------------|----------------|-------|----------------|
| آشپزخانه | ✅ | ❌ | ❌ | ❌ |
| بار | ✅ | ❌ | ❌ | ❌ |
| کانتر | ✅ | ❌ | ❌ | ❌ |
| صندوق | ✅ | ❌ | ❌ | ❌ |
| تاسیسات | ✅ | ❌ | ❌ | ❌ |
| خرید | ✅ | ✅ | ✅ | ❌ |
| مدیر | ✅ | ✅ | ✅ | ✅ |

## فایل‌های تغییر یافته:

1. ✅ `purchases/models.py` - اضافه کردن نقش‌های جدید
2. ✅ `purchases/views.py` - بروزرسانی دسترسی‌ها
3. ✅ `purchases/admin.py` - تنظیم Admin Panel
4. ✅ `create_users.py` - اضافه کردن کاربران جدید
5. ✅ `purchases/management/commands/seed_users.py` - اضافه کردن کاربران
6. ✅ `purchases/management/commands/setup_users.py` - اضافه کردن کاربران
7. ✅ `create_department_users.py` - اسکریپت جدید (ایجاد شد)
8. ✅ `USER_ROLES_GUIDE.md` - مستندات (ایجاد شد)
9. ✅ `purchases/migrations/0033_alter_user_role.py` - Migration

## تست و بررسی:

```bash
# بررسی سیستم
python manage.py check
✅ System check identified no issues (0 silenced).

# بررسی کاربران
python check_users.py
✅ Total users in database: 8

# اجرای Migration
python manage.py migrate
✅ Applying purchases.0033_alter_user_role... OK
```

## وضعیت: ✅ تکمیل شد

همه تغییرات با موفقیت اعمال شده و تست شده‌اند.
