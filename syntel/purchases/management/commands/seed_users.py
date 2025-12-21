from django.core.management.base import BaseCommand
from purchases.models import User

class Command(BaseCommand):
    help = 'Create initial users'

    def handle(self, *args, **options):
        if not User.objects.filter(username='kitchen').exists():
            User.objects.create_user(username='kitchen', password='pass123', role='kitchen')
        if not User.objects.filter(username='bar').exists():
            User.objects.create_user(username='bar', password='pass123', role='bar')
        if not User.objects.filter(username='counter').exists():
            User.objects.create_user(username='counter', password='pass123', role='counter')
        if not User.objects.filter(username='cashier').exists():
            User.objects.create_user(username='cashier', password='pass123', role='cashier')
        if not User.objects.filter(username='facilities').exists():
            User.objects.create_user(username='facilities', password='pass123', role='facilities')
        if not User.objects.filter(username='purchasing').exists():
            User.objects.create_user(username='purchasing', password='pass123', role='purchasing')
        if not User.objects.filter(username='manager').exists():
            User.objects.create_user(username='manager', password='pass123', role='manager')
        self.stdout.write(self.style.SUCCESS('Initial users created'))