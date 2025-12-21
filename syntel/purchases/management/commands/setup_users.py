from django.core.management.base import BaseCommand
from purchases.models import User

class Command(BaseCommand):
    help = 'Check existing users and create default admin if none exist'

    def handle(self, *args, **options):
        users = User.objects.all()
        self.stdout.write(f"Total users in database: {users.count()}")

        if users.count() > 0:
            self.stdout.write("\nExisting users:")
            for user in users:
                self.stdout.write(f"  Username: {user.username}")
                self.stdout.write(f"  Role: {user.role}")
                self.stdout.write(f"  Active: {user.is_active}")
                self.stdout.write(f"  Name: {user.first_name} {user.last_name}")
                self.stdout.write("  ---")
        else:
            self.stdout.write("\nNo users found. Creating default users...")

            # Create admin user
            try:
                admin = User.objects.create_user(
                    username='admin',
                    password='admin123',
                    first_name='مدیر',
                    last_name='سیستم',
                    role='manager'
                )
                self.stdout.write(self.style.SUCCESS("Created admin user:"))
                self.stdout.write("  Username: admin")
                self.stdout.write("  Password: admin123")
                self.stdout.write("  Role: manager")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating admin: {e}"))

            # Create purchasing user
            try:
                purchasing = User.objects.create_user(
                    username='purchasing',
                    password='purchase123',
                    first_name='مسئول',
                    last_name='خرید',
                    role='purchasing'
                )
                self.stdout.write(self.style.SUCCESS("Created purchasing user:"))
                self.stdout.write("  Username: purchasing")
                self.stdout.write("  Password: purchase123")
                self.stdout.write("  Role: purchasing")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating purchasing user: {e}"))

            # Create kitchen user
            try:
                kitchen = User.objects.create_user(
                    username='kitchen',
                    password='kitchen123',
                    first_name='سرآشپز',
                    last_name='آشپزخانه',
                    role='kitchen'
                )
                self.stdout.write(self.style.SUCCESS("Created kitchen user:"))
                self.stdout.write("  Username: kitchen")
                self.stdout.write("  Password: kitchen123")
                self.stdout.write("  Role: kitchen")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating kitchen user: {e}"))

            # Create bar user
            try:
                bar = User.objects.create_user(
                    username='bar',
                    password='bar123',
                    first_name='مسئول',
                    last_name='بار',
                    role='bar'
                )
                self.stdout.write(self.style.SUCCESS("Created bar user:"))
                self.stdout.write("  Username: bar")
                self.stdout.write("  Password: bar123")
                self.stdout.write("  Role: bar")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating bar user: {e}"))

            # Create counter user
            try:
                counter = User.objects.create_user(
                    username='counter',
                    password='counter123',
                    first_name='مسئول',
                    last_name='کانتر',
                    role='counter'
                )
                self.stdout.write(self.style.SUCCESS("Created counter user:"))
                self.stdout.write("  Username: counter")
                self.stdout.write("  Password: counter123")
                self.stdout.write("  Role: counter")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating counter user: {e}"))

            # Create cashier user
            try:
                cashier = User.objects.create_user(
                    username='cashier',
                    password='cashier123',
                    first_name='مسئول',
                    last_name='صندوق',
                    role='cashier'
                )
                self.stdout.write(self.style.SUCCESS("Created cashier user:"))
                self.stdout.write("  Username: cashier")
                self.stdout.write("  Password: cashier123")
                self.stdout.write("  Role: cashier")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating cashier user: {e}"))

            # Create facilities user
            try:
                facilities = User.objects.create_user(
                    username='facilities',
                    password='facilities123',
                    first_name='مسئول',
                    last_name='تاسیسات',
                    role='facilities'
                )
                self.stdout.write(self.style.SUCCESS("Created facilities user:"))
                self.stdout.write("  Username: facilities")
                self.stdout.write("  Password: facilities123")
                self.stdout.write("  Role: facilities")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating facilities user: {e}"))