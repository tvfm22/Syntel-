"""Tests for the backup/restore page and flows (پشتیبان‌گیری و بازیابی)."""
import io
import json
import os
import sqlite3
import tempfile
import unittest.mock as mock
import zipfile

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from purchases.models import Cafe, Unit, User

CAFE_KW = {'cafe_code': 'main'}


class BackupViewsTests(TestCase):
    """Backup page access, downloads, restore and Drive config handling."""

    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            username='manager_backup', password='testpass123', role='manager')
        self.kitchen = User.objects.create_user(
            username='kitchen_backup', password='testpass123', role='kitchen')
        Unit.objects.create(name='Kilogram', symbol='kg')
        # The cafe middleware requires a selected cafe in the session
        cafe = Cafe.objects.create(name='Test Cafe', code='main', is_active=True)
        self.kitchen.cafe = cafe  # kitchen user belongs to this cafe
        self.kitchen.save()
        session = self.client.session
        session['selected_cafe_id'] = cafe.id
        session['selected_cafe_code'] = cafe.code
        session.save()

    def test_page_access_control(self):
        # Kitchen user → denied
        self.client.login(username='kitchen_backup', password='testpass123')
        resp = self.client.get(reverse('backup_management', kwargs=CAFE_KW))
        self.assertEqual(resp.status_code, 302)

        # Manager → page renders with expected sections
        self.client.login(username='manager_backup', password='testpass123')
        resp = self.client.get(reverse('backup_management', kwargs=CAFE_KW))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'backup_management.html')
        # Drive UI is currently hidden, but the local backup/restore sections render
        self.assertNotContains(resp, 'گوگل‌درایو')
        self.assertContains(resp, 'بازیابی')
        self.assertContains(resp, 'پشتیبان‌گیری')

    def test_json_export_download(self):
        self.client.login(username='manager_backup', password='testpass123')
        resp = self.client.get(reverse('export_data_json', kwargs=CAFE_KW))
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content.decode('utf-8'))
        self.assertIsInstance(data, list)
        models = {obj['model'] for obj in data}
        self.assertIn('purchases.user', models)  # custom user model
        self.assertIn('purchases.unit', models)

    def test_data_folder_zip_download(self):
        self.client.login(username='manager_backup', password='testpass123')
        resp = self.client.get(reverse('export_data_folder', kwargs=CAFE_KW))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('attachment;', resp['Content-Disposition'])
        self.assertIn('syntel_data_backup_', resp['Content-Disposition'])
        content = b''.join(resp.streaming_content)
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
        self.assertGreater(len(names), 0)
        resp.close()

    def test_database_file_download(self):
        # The test DB is in-memory, so point the view at a real temp sqlite file.
        # Patch settings.DATABASES with a *copy*: the live connection keeps its
        # own settings_dict (NAME=file:memorydb...) so close_old_connections
        # (fired by resp.close()) does not destroy the shared in-memory DB.
        tmp = tempfile.mktemp(suffix='.sqlite3')
        sqlite3.connect(tmp).close()
        patched = {'default': {**settings.DATABASES['default'], 'NAME': tmp}}
        with mock.patch.dict('django.conf.settings.DATABASES', patched):
            self.client.login(username='manager_backup', password='testpass123')
            resp = self.client.get(reverse('download_database_file', kwargs=CAFE_KW))
            self.assertEqual(resp.status_code, 200)
            self.assertIn('syntel_db_backup_', resp['Content-Disposition'])
            self.assertEqual(resp['Content-Type'], 'application/x-sqlite3')
            resp.close()  # release the file handle before removing the temp file
        os.remove(tmp)

    def test_restore_database_file_invalid(self):
        self.client.login(username='manager_backup', password='testpass123')
        # A file that is not a SQLite database must be rejected without side effects
        fake = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False)
        fake.write(b'this is not a sqlite database at all')
        fake.close()
        try:
            with open(fake.name, 'rb') as f:
                resp = self.client.post(
                    reverse('restore_database_file', kwargs=CAFE_KW),
                    {'db_file': f}, follow=True)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'فایل دیتابیس SQLite معتبر نیست')
        finally:
            os.remove(fake.name)

    def test_json_restore_replaces_data(self):
        self.client.login(username='manager_backup', password='testpass123')
        fixture = [
            {'model': 'purchases.unit', 'pk': 1,
             'fields': {'name': 'RestoredUnit', 'symbol': 'ru'}},
        ]
        payload = json.dumps(fixture).encode('utf-8')
        resp = self.client.post(
            reverse('import_data_json', kwargs=CAFE_KW),
            {'backup_file': io.BytesIO(payload)})
        self.assertEqual(resp.status_code, 302)  # redirects back to backup page
        # Old data was flushed, only the fixture rows exist
        self.assertEqual(Unit.objects.count(), 1)
        self.assertEqual(Unit.objects.first().name, 'RestoredUnit')
        self.assertEqual(User.objects.count(), 0)  # flush removed all users

    def test_drive_config_save_and_clear(self):
        self.client.login(username='manager_backup', password='testpass123')
        url = reverse('drive_save_config', kwargs=CAFE_KW)

        resp = self.client.post(url, {
            'client_id': 'abc.apps.googleusercontent.com',
            'client_secret': 'secret123',
            'folder_id': 'folder-xyz',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ذخیره شد')

        from purchases import drive_backup
        self.assertTrue(drive_backup.is_configured())
        cfg = drive_backup.load_config()
        self.assertEqual(cfg['client_id'], 'abc.apps.googleusercontent.com')
        self.assertEqual(cfg['folder_id'], 'folder-xyz')

        # Clear back to env defaults
        resp = self.client.post(url, {'clear_config': '1'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(drive_backup.is_configured())
