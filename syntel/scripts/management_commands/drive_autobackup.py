"""Send the whole data folder to Google Drive automatically (every 12h by default).

Runs the same due-check as the backup page: if Drive is connected and the
interval (GOOGLE_DRIVE_AUTO_INTERVAL_HOURS, default 12) has passed since the
last automatic backup, it uploads a ZIP of DATA_PATH to Drive.

Schedule it from cron, e.g. every hour (the due-check makes the real upload
happen only once per interval):

    0 * * * * cd /app && python manage.py drive_autobackup
"""
from django.core.management.base import BaseCommand

from purchases.views import run_auto_backup_if_due


class Command(BaseCommand):
    help = 'پشتیبان خودکار پوشه دادهها به گوگلدرایو (هر ۱۲ ساعت یکبار بهصورت پیشفرض)'

    def handle(self, *args, **options):
        ran, message = run_auto_backup_if_due()
        if not message:
            self.stdout.write('هنوز زمان پشتیبان خودکار نرسیده است.')
        elif ran:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stderr.write(self.style.ERROR(message))
