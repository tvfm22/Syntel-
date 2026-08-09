"""
کاربران فعلی مسئول خرید، عملاً مسئول خرید و انبار هستند (کاربر مسئول خرید فعلی
همان کاربر مسئول انبار است). برای حفظ محدوده کاری فعلی آن‌ها، نقش دوم انبار
(secondary_role='warehouse') به آن‌ها اضافه می‌شود تا هر دو داشبورد جدید را ببینند.
"""
from django.db import migrations


def make_purchasing_users_dual(apps, schema_editor):
    User = apps.get_model('purchases', 'User')
    updated = 0
    for user in User.objects.filter(role='purchasing'):
        if not user.secondary_role:
            user.secondary_role = 'warehouse'
            user.save(update_fields=['secondary_role'])
            updated += 1
    print(f'  → {updated} کاربر مسئول خرید، نقش دوم انبار گرفتند')


def unmake_purchasing_users_dual(apps, schema_editor):
    User = apps.get_model('purchases', 'User')
    User.objects.filter(role='purchasing', secondary_role='warehouse').update(secondary_role=None)


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0068_officerassignment'),
    ]

    operations = [
        migrations.RunPython(make_purchasing_users_dual, unmake_purchasing_users_dual),
    ]
