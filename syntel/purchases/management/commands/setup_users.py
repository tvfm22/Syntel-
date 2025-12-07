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