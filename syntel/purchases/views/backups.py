from __future__ import annotations

from .. import drive_backup
from ..middleware import get_cafe_code
from ..models import Invoice
from ..models import Payment
from ..models import Product
from ..models import PurchaseRequest
from ..models import Supplier
from ..models import User
from .helpers import _backup_admin_required
from .helpers import _human_size
from .helpers import _is_sqlite_engine
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import role_required
from datetime import datetime
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.management import call_command
from django.db import connections
from django.db import transaction
from django.db.models import DecimalField
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.http import FileResponse
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from pathlib import Path
import csv
import io
import json
import logging
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
import zipfile

logger = logging.getLogger(__name__)


LOCK_BACKUP_OPERATION = 'lock:backup_operation_running'

LOCK_AUTO_BACKUP = LOCK_BACKUP_OPERATION

LOCK_SERVER_BACKUP = LOCK_BACKUP_OPERATION

LOCK_DRIVE_BACKUP = LOCK_BACKUP_OPERATION


BACKUP_LOCK_TIMEOUT = 600


_EXCLUDED_FOLDER_BACKUP_DIRS = {'backups', 'cache', 'logs', 'tmp', 'sessions'}


_EXCLUDED_BACKUP_FILENAMES = {
    'drive_token.json',
    'drive_config.json',
    'drive_autobackup.json',
}


_EXCLUDED_BACKUP_SUFFIXES = ('.key', '.pem')


_ALLOWED_CSV_MODELS = {'products', 'suppliers', 'purchases', 'invoices'}


_FIXTURE_EXCLUDED_MODELS = {'contenttypes.contenttype', 'auth.permission', 'sessions.session'}


def _sqlite_online_backup(src_path: Path, dst_path: Path) -> None:
    """Create a consistent SQLite backup using the SQLite online backup API."""
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src_path))
    try:
        dst_conn = sqlite3.connect(str(dst_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _atomic_replace_file(src_path: Path, dst_path: Path) -> None:
    """Atomically replace dst_path with src_path (staging in dst_path.parent if cross-device)."""
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if src_path.parent.resolve() == dst_path.parent.resolve():
        os.replace(src_path, dst_path)
        return

    fd, staging_name = tempfile.mkstemp(prefix=f'.{dst_path.name}.', dir=str(dst_path.parent))
    os.close(fd)
    staging_path = Path(staging_name)
    try:
        shutil.copyfile(src_path, staging_path)
        os.replace(staging_path, dst_path)
    finally:
        if staging_path.exists():
            try:
                staging_path.unlink()
            except OSError:
                pass


def _snapshot_db_before_restore():
    """Copy the current SQLite DB to BACKUPS_DIR safely before a destructive restore."""
    if not _is_sqlite_engine():
        return None
    raw_name = str(settings.DATABASES['default']['NAME'])
    if raw_name == ':memory:' or raw_name.startswith('file:'):
        return None
    db_path = Path(raw_name)
    if not os.path.exists(db_path):
        return None
    os.makedirs(settings.BACKUPS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    snapshot_path = Path(settings.BACKUPS_DIR) / f'autorestore_{timestamp}.sqlite3'
    _sqlite_online_backup(db_path, snapshot_path)
    return snapshot_path


def _is_excluded_backup_file(filename: str) -> bool:
    lower = filename.lower()
    if lower in _EXCLUDED_BACKUP_FILENAMES:
        return True
    if lower.startswith('.env'):
        return True
    if lower.endswith(_EXCLUDED_BACKUP_SUFFIXES):
        return True
    return False


def _build_data_folder_zip(zip_path):
    """Zip DATA_PATH (db, uploads, ...) while excluding secrets, sessions, and transient dirs."""
    root = Path(settings.DATA_PATH)
    is_sqlite = _is_sqlite_engine()
    raw_db_name = str(settings.DATABASES['default']['NAME'])
    live_db_path = None
    if is_sqlite and raw_db_name != ':memory:' and not raw_db_name.startswith('file:'):
        live_db_path = Path(raw_db_name).resolve()

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_FOLDER_BACKUP_DIRS]
            rel_dir = Path(dirpath).relative_to(root)
            for fname in filenames:
                if _is_excluded_backup_file(fname):
                    continue
                full_path = Path(dirpath) / fname
                arcname = (rel_dir / fname).as_posix()

                # Never copy raw SQLite WAL/SHM files or read live SQLite DB mid-write
                if live_db_path is not None:
                    resolved = full_path.resolve()
                    if any(str(resolved) == str(live_db_path) + sfx for sfx in ('-wal', '-shm', '-journal')):
                        continue
                    if resolved == live_db_path:
                        os.makedirs(settings.TMP_DIR, exist_ok=True)
                        fd, tmp_db_name = tempfile.mkstemp(prefix='zip_db_', suffix='.sqlite3', dir=str(settings.TMP_DIR))
                        os.close(fd)
                        tmp_db = Path(tmp_db_name)
                        try:
                            _sqlite_online_backup(live_db_path, tmp_db)
                            zf.write(tmp_db, arcname)
                        except (OSError, sqlite3.Error):
                            logger.exception("Failed online SQLite backup for zip archive")
                        finally:
                            try:
                                os.remove(tmp_db)
                            except OSError:
                                pass
                        continue

                try:
                    zf.write(full_path, arcname)
                except OSError:
                    continue


class _TempBackupFile:
    """File-like wrapper that deletes its temp file when the response closes it."""

    def __init__(self, path):
        self._file = open(path, 'rb')
        self._path = path
        self.name = path

    def __iter__(self):
        return iter(self._file)

    def __getattr__(self, item):
        return getattr(self._file, item)

    def close(self):
        try:
            self._file.close()
        finally:
            try:
                os.remove(self._path)
            except OSError:
                pass


def run_auto_backup_if_due():
    """If Drive is connected and the interval (default 12h) has passed,
    automatically back up the data folder to Google Drive.
    Returns (ran, message). Protected by cache lock to prevent concurrent executions."""
    if not drive_backup.is_configured() or not drive_backup.is_connected():
        return False, ''

    state = drive_backup.get_auto_state()
    interval_hours = drive_backup.auto_backup_interval_hours()
    last_raw = state.get('last_auto')
    if last_raw:
        try:
            last_run = datetime.fromisoformat(last_raw)
            if (datetime.now() - last_run).total_seconds() < interval_hours * 3600:
                return False, ''
        except ValueError:
            pass

    if not cache.add(LOCK_AUTO_BACKUP, '1', timeout=BACKUP_LOCK_TIMEOUT):
        logger.info("Auto backup skipped: another backup operation is currently running.")
        return False, 'پشتیبان‌گیری خودکار در حال حاضر در حال اجرا است.'

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    os.makedirs(settings.TMP_DIR, exist_ok=True)
    zip_path = Path(settings.TMP_DIR) / f'syntel_data_autobackup_{timestamp}.zip'
    try:
        _build_data_folder_zip(zip_path)
        drive_backup.upload_file_to_drive(zip_path.name, zip_path)
        drive_backup.set_auto_state(
            last_auto=datetime.now().isoformat(timespec='seconds'),
            status='ok',
            last_error='',
        )
        logger.info("Auto backup '%s' successfully uploaded to Google Drive.", zip_path.name)
        return True, f'پشتیبان خودکار «{zip_path.name}» با موفقیت به گوگل‌درایو ارسال شد.'
    except Exception as e:
        drive_backup.set_auto_state(status='error', last_error=str(e))
        logger.exception("Error during auto backup: %s", e)
        return False, f'خطا در پشتیبان خودکار: {str(e)}'
    finally:
        cache.delete(LOCK_AUTO_BACKUP)
        try:
            os.remove(zip_path)
        except OSError:
            pass


def trigger_auto_backup_async():
    """Trigger run_auto_backup_if_due in a background daemon thread protected by cache lock."""
    if not drive_backup.is_configured() or not drive_backup.is_connected():
        return False

    state = drive_backup.get_auto_state()
    interval_hours = drive_backup.auto_backup_interval_hours()
    last_raw = state.get('last_auto')
    if last_raw:
        try:
            last_run = datetime.fromisoformat(last_raw)
            if (datetime.now() - last_run).total_seconds() < interval_hours * 3600:
                return False
        except ValueError:
            pass

    def _worker():
        try:
            run_auto_backup_if_due()
        except Exception as exc:
            logger.exception("Unhandled error in background auto backup: %s", exc)

    thread = threading.Thread(target=_worker, name="auto_backup_daemon", daemon=True)
    thread.start()
    return True


@custom_login_required
@_backup_admin_required
def backup_management(request):
    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.all()
    inv_qs = Invoice.objects.all()
    sup_qs = Supplier.objects.all()
    if cafe:
        pr_qs = pr_qs.filter(cafe=cafe)
        inv_qs = inv_qs.filter(cafe=cafe)
        sup_qs = sup_qs.filter(cafe=cafe)

    drive_cfg = drive_backup.load_config()
    auto_state = drive_backup.get_auto_state()
    interval_hours = drive_backup.auto_backup_interval_hours()
    last_auto = auto_state.get('last_auto', '')
    next_auto = ''
    if last_auto:
        try:
            next_auto = (datetime.fromisoformat(last_auto) + timedelta(hours=interval_hours)).strftime('%Y-%m-%d %H:%M')
        except ValueError:
            pass

    is_sqlite = _is_sqlite_engine()
    db_path = Path(settings.DATABASES['default']['NAME'])
    db_size = os.path.getsize(db_path) if is_sqlite and os.path.exists(db_path) else 0

    backups = []
    if os.path.isdir(settings.BACKUPS_DIR):
        for name in sorted(os.listdir(settings.BACKUPS_DIR), reverse=True):
            full_path = os.path.join(settings.BACKUPS_DIR, name)
            if os.path.isfile(full_path):
                stat = os.stat(full_path)
                backups.append({
                    'name': name,
                    'size': stat.st_size,
                    'size_display': _human_size(stat.st_size),
                    'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                })

    context = {
        "title": "مدیریت پشتیبان‌گیری و داده‌ها",
        "total_users": User.objects.count(),
        "total_requests": pr_qs.count(),
        "total_invoices": inv_qs.count(),
        "total_payments": Payment.objects.count(),
        "total_suppliers": sup_qs.count(),
        "database_name": os.path.basename(db_path),
        "database_host": settings.DATABASES['default'].get('HOST') or 'localhost',
        "database_type": 'SQLite' if is_sqlite else 'PostgreSQL',
        "database_path": str(db_path),
        "database_size": _human_size(db_size) if db_size else '-',
        "is_sqlite": is_sqlite,
        "backups": backups,
        "drive_configured": drive_backup.is_configured(),
        "drive_connected": drive_backup.is_connected(),
        "drive_email": drive_backup.get_connected_email(),
        "drive_client_id": drive_cfg['client_id'],
        "drive_folder_id": drive_cfg['folder_id'],
        "drive_has_file_config": os.path.exists(settings.DATA_PATH / drive_backup.CONFIG_FILENAME),
        "auto_interval_hours": interval_hours,
        "auto_last_run": last_auto,
        "auto_next_run": next_auto,
        "auto_status": auto_state.get('status', ''),
        "auto_last_error": auto_state.get('last_error', ''),
    }
    return render(request, "backup_management.html", context)


@custom_login_required
@_backup_admin_required
def backup_database(request):
    """Create a server-side backup of the database (JSON dump) asynchronously. Accepts only POST."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if not cache.add(LOCK_SERVER_BACKUP, '1', timeout=BACKUP_LOCK_TIMEOUT):
        messages.warning(request, "عملیات پشتیبان‌گیری سرور در حال حاضر در حال اجرا است.")
        return get_cafe_redirect_url('backup_management', request)

    def _worker():
        tmp_path = None
        try:
            backup_dir = settings.BACKUPS_DIR
            os.makedirs(backup_dir, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'backup_{timestamp}.json'
            filepath = os.path.join(backup_dir, filename)

            fd, tmp_path = tempfile.mkstemp(prefix=f'.{filename}.', suffix='.tmp', dir=str(backup_dir))
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                call_command(
                    'dumpdata',
                    natural_foreign=True,
                    natural_primary=True,
                    exclude=['auth.permission', 'contenttypes', 'sessions'],
                    stdout=f,
                )
            os.replace(tmp_path, filepath)
            tmp_path = None

            logger.info("Server-side backup '%s' completed successfully.", filename)
        except Exception as e:
            logger.exception("Error creating server-side backup: %s", e)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            cache.delete(LOCK_SERVER_BACKUP)

    threading.Thread(target=_worker, name="backup_database_worker", daemon=True).start()
    messages.success(request, "ایجاد نسخه پشتیبان روی سرور در پس‌زمینه آغاز شد.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@_backup_admin_required
def download_database_file(request):
    """Download a consistent online snapshot of the SQLite database file."""
    if not _is_sqlite_engine():
        messages.error(request, "دانلود فایل دیتابیس فقط برای دیتابیس SQLite پشتیبانی می‌شود. برای PostgreSQL از دانلود JSON استفاده کنید.")
        return get_cafe_redirect_url('backup_management', request)

    db_path = Path(settings.DATABASES['default']['NAME'])
    if not os.path.exists(db_path):
        messages.error(request, "فایل دیتابیس یافت نشد.")
        return get_cafe_redirect_url('backup_management', request)

    os.makedirs(settings.TMP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    fd, tmp_name = tempfile.mkstemp(prefix='db_dl_', suffix='.sqlite3', dir=str(settings.TMP_DIR))
    os.close(fd)
    temp_db_path = Path(tmp_name)
    try:
        _sqlite_online_backup(db_path, temp_db_path)
    except Exception as e:
        try:
            os.remove(temp_db_path)
        except OSError:
            pass
        logger.exception("Failed to create online SQLite snapshot for download: %s", e)
        messages.error(request, "خطا در تهیه نسخه پشتیبان دیتابیس.")
        return get_cafe_redirect_url('backup_management', request)

    return FileResponse(
        _TempBackupFile(temp_db_path),
        content_type='application/x-sqlite3',
        as_attachment=True,
        filename=f'syntel_db_backup_{timestamp}.sqlite3',
    )


@custom_login_required
@_backup_admin_required
def restore_database_file(request):
    """Restore the database by uploading a validated SQLite database file (full restore)."""
    if not _is_sqlite_engine():
        messages.error(
            request,
            "بازیابی فایل SQLite برای دیتابیس PostgreSQL پشتیبانی نمی‌شود. برای PostgreSQL از بازیابی JSON استفاده کنید.",
        )
        return get_cafe_redirect_url('backup_management', request)

    if request.method != 'POST' or not request.FILES.get('db_file'):
        messages.error(request, "فایلی انتخاب نشده است.")
        return get_cafe_redirect_url('backup_management', request)

    db_file = request.FILES['db_file']

    ext = Path(db_file.name).suffix.lower()
    if ext not in ('.db', '.sqlite', '.sqlite3'):
        messages.error(request, "فرمت فایل نامعتبر است. لطفاً فایل دیتابیس (.sqlite3 یا .db) انتخاب کنید.")
        return get_cafe_redirect_url('backup_management', request)

    header = db_file.read(16)
    db_file.seek(0)
    if header[:15] != b'SQLite format 3':
        messages.error(request, "فایل انتخاب‌شده یک فایل دیتابیس SQLite معتبر نیست.")
        return get_cafe_redirect_url('backup_management', request)

    if not cache.add(LOCK_BACKUP_OPERATION, '1', timeout=BACKUP_LOCK_TIMEOUT):
        messages.warning(request, "عملیات پشتیبان‌گیری یا بازیابی دیگری در حال حاضر در حال اجرا است.")
        return get_cafe_redirect_url('backup_management', request)

    db_path = Path(settings.DATABASES['default']['NAME'])
    os.makedirs(settings.TMP_DIR, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='restore_db_', suffix='.sqlite3', dir=str(settings.TMP_DIR))
    os.close(fd)
    temp_path = Path(tmp_name)

    try:
        with open(temp_path, 'wb') as destination:
            for chunk in db_file.chunks():
                destination.write(chunk)

        # Validate SQLite integrity before touching the live database
        try:
            check_conn = sqlite3.connect(str(temp_path))
            try:
                row = check_conn.execute('PRAGMA integrity_check').fetchone()
            finally:
                check_conn.close()
        except sqlite3.Error:
            row = None

        if not row or str(row[0]).lower() != 'ok':
            messages.error(request, "فایل دیتابیس خراب یا نامعتبر است (بررسی یکپارچگی ناموفق بود).")
            return get_cafe_redirect_url('backup_management', request)

        snapshot_path = _snapshot_db_before_restore()

        connections.close_all()
        for suffix in ('-wal', '-shm'):
            stale = Path(str(db_path) + suffix)
            if stale.exists():
                stale.unlink()
        _atomic_replace_file(temp_path, db_path)

        try:
            call_command('migrate', verbosity=0, interactive=False)
            note = f" نسخه قبلی قبل از بازیابی ذخیره شد: {snapshot_path.name}" if snapshot_path else ""
            messages.success(request, f"دیتابیس با موفقیت بازیابی شد.{note}")
        except Exception as e:
            connections.close_all()
            for suffix in ('-wal', '-shm'):
                stale = Path(str(db_path) + suffix)
                if stale.exists():
                    stale.unlink()
            if snapshot_path and os.path.exists(snapshot_path):
                _atomic_replace_file(snapshot_path, db_path)
            messages.error(request, f"خطا در اعمال دیتابیس جدید ({e}). دیتابیس قبلی برگردانده شد.")
    except Exception as e:
        logger.exception("Error restoring SQLite database: %s", e)
        messages.error(request, f"خطا در بازیابی دیتابیس: {str(e)}")
    finally:
        cache.delete(LOCK_BACKUP_OPERATION)
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@_backup_admin_required
def download_backup_file(request, filename):
    """Download a server-side backup file from BACKUPS_DIR."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(settings.BACKUPS_DIR, safe_name)
    if not os.path.isfile(filepath):
        raise Http404("فایل پشتیبان یافت نشد")
    return FileResponse(
        open(filepath, 'rb'),
        as_attachment=True,
        filename=safe_name,
    )


@custom_login_required
@_backup_admin_required
def delete_backup_file(request, filename):
    """Delete a server-side backup file."""
    if request.method == 'POST':
        safe_name = os.path.basename(filename)
        filepath = os.path.join(settings.BACKUPS_DIR, safe_name)
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
                messages.success(request, f"فایل پشتیبان حذف شد: {safe_name}")
            except OSError as e:
                messages.error(request, f"خطا در حذف فایل: {e}")
        else:
            messages.error(request, "فایل پشتیبان یافت نشد.")
    return get_cafe_redirect_url('backup_management', request)


def _drive_back_to_backup(request):
    cafe_code = get_cafe_code(request)
    if cafe_code:
        return redirect('backup_management', cafe_code=cafe_code)
    return redirect('login')


@custom_login_required
@_backup_admin_required
def drive_connect(request):
    """Start the Google OAuth flow to connect Google Drive."""
    if not drive_backup.is_configured():
        messages.error(request, "گوگل‌درایو تنظیم نشده است. ابتدا GOOGLE_DRIVE_CLIENT_ID و GOOGLE_DRIVE_CLIENT_SECRET را در تنظیمات سرور قرار دهید.")
        return get_cafe_redirect_url('backup_management', request)
    state = secrets.token_urlsafe(32)
    request.session['drive_oauth_state'] = state
    redirect_uri = request.build_absolute_uri(reverse('drive_callback'))
    return redirect(drive_backup.build_authorize_url(state, redirect_uri))


@custom_login_required
@_backup_admin_required
def drive_callback(request):
    """OAuth callback — exchange the code and store the token."""
    error = request.GET.get('error')
    if error:
        messages.error(request, f"اتصال به گوگل‌درایو انجام نشد: {error}")
        return _drive_back_to_backup(request)

    code = request.GET.get('code')
    state = request.GET.get('state')
    expected_state = request.session.pop('drive_oauth_state', None)
    if not code or not state or not expected_state or state != expected_state:
        messages.error(request, "پاسخ نامعتبر از گوگل دریافت شد. دوباره تلاش کنید.")
        return _drive_back_to_backup(request)

    try:
        redirect_uri = request.build_absolute_uri(reverse('drive_callback'))
        drive_backup.exchange_code(code, redirect_uri)
        email = drive_backup.get_connected_email()
        messages.success(request, f"اتصال گوگل‌درایو برقرار شد{(' (' + email + ')') if email else ''}.")
    except Exception as e:
        messages.error(request, f"خطا در اتصال به گوگل‌درایو: {str(e)}")
    return _drive_back_to_backup(request)


@custom_login_required
@_backup_admin_required
def drive_save_config(request):
    """Save Google Drive credentials from the page (no server restart needed)."""
    if request.method == 'POST':
        if request.POST.get('clear_config'):
            drive_backup.clear_config()
            messages.success(request, "تنظیمات ذخیره‌شده گوگل‌درایو حذف شد؛ مقادیر محیطی (در صورت وجود) استفاده می‌شوند.")
            return get_cafe_redirect_url('backup_management', request)

        client_id = request.POST.get('client_id', '').strip()
        client_secret = request.POST.get('client_secret', '').strip()
        folder_id = request.POST.get('folder_id', '').strip()

        if not client_id and not client_secret:
            drive_backup.clear_config()
            messages.success(request, "تنظیمات ذخیره‌شده گوگل‌درایو حذف شد؛ مقادیر محیطی (در صورت وجود) استفاده می‌شوند.")
        elif not client_id:
            messages.error(request, "وارد کردن Client ID الزامی است.")
        elif not client_secret:
            existing = drive_backup.load_config()['client_secret']
            if existing:
                drive_backup.save_config(client_id, existing, folder_id)
                messages.success(request, "تنظیمات گوگل‌درایو ذخیره شد و بلافاصله اعمال می‌شود.")
            else:
                messages.error(request, "وارد کردن Client Secret الزامی است.")
        else:
            drive_backup.save_config(client_id, client_secret, folder_id)
            messages.success(request, "تنظیمات گوگل‌درایو ذخیره شد و بلافاصله اعمال می‌شود.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@_backup_admin_required
def drive_disconnect(request):
    """Disconnect Google Drive (removes the stored token)."""
    if request.method == 'POST':
        drive_backup.clear_token()
        messages.success(request, "اتصال گوگل‌درایو قطع شد.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@_backup_admin_required
def drive_backup_now(request):
    """Send a fresh backup of the database to Google Drive asynchronously. Accepts only POST."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    if not drive_backup.is_configured():
        messages.error(request, "گوگل‌درایو تنظیم نشده است.")
        return get_cafe_redirect_url('backup_management', request)
    if not drive_backup.is_connected():
        messages.error(request, "ابتدا به گوگل‌درایو متصل شوید.")
        return get_cafe_redirect_url('backup_management', request)

    if not cache.add(LOCK_DRIVE_BACKUP, '1', timeout=BACKUP_LOCK_TIMEOUT):
        messages.warning(request, "عملیات ارسال به گوگل‌درایو در حال حاضر در حال اجرا است.")
        return get_cafe_redirect_url('backup_management', request)

    backup_type = request.POST.get('type', 'json')
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    def _worker():
        temp_file = None
        try:
            if backup_type == 'folder':
                os.makedirs(settings.TMP_DIR, exist_ok=True)
                zip_path = Path(settings.TMP_DIR) / f'syntel_data_backup_{timestamp}.zip'
                temp_file = zip_path
                _build_data_folder_zip(zip_path)
                filename = zip_path.name
                drive_backup.upload_file_to_drive(filename, zip_path)
            elif backup_type == 'db' and _is_sqlite_engine():
                db_path = Path(settings.DATABASES['default']['NAME'])
                if not os.path.exists(db_path):
                    raise ValueError('فایل دیتابیس یافت نشد.')
                os.makedirs(settings.TMP_DIR, exist_ok=True)
                temp_db = Path(settings.TMP_DIR) / f'syntel_db_backup_{timestamp}.sqlite3'
                temp_file = temp_db
                _sqlite_online_backup(db_path, temp_db)
                filename = temp_db.name
                drive_backup.upload_file_to_drive(filename, temp_db)
            else:
                buffer = io.StringIO()
                call_command(
                    'dumpdata',
                    natural_foreign=True,
                    natural_primary=True,
                    exclude=['auth.permission', 'contenttypes', 'sessions'],
                    stdout=buffer,
                )
                filename = f'syntel_export_{timestamp}.json'
                payload = buffer.getvalue().encode('utf-8')
                drive_backup.upload_backup(filename, payload)

            drive_backup.set_auto_state(
                last_auto=datetime.now().isoformat(timespec='seconds'),
                status='ok',
                last_error='',
            )
            logger.info("Drive backup '%s' successfully uploaded to Google Drive.", filename)
        except Exception as e:
            drive_backup.set_auto_state(status='error', last_error=str(e))
            logger.exception("Error uploading backup to Google Drive: %s", e)
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            cache.delete(LOCK_DRIVE_BACKUP)

    threading.Thread(target=_worker, name="drive_backup_worker", daemon=True).start()
    messages.success(request, "ارسال نسخه پشتیبان به گوگل‌درایو در پس‌زمینه آغاز شد.")
    return get_cafe_redirect_url('backup_management', request)


@custom_login_required
@_backup_admin_required
def export_data_folder(request):
    """Download the DATA_PATH folder (db + uploads, excluding sessions/secrets) as a ZIP."""
    os.makedirs(settings.TMP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    zip_path = Path(settings.TMP_DIR) / f'syntel_data_backup_{timestamp}.zip'
    try:
        _build_data_folder_zip(zip_path)
    except Exception as e:
        messages.error(request, f"خطا در ساخت پشتیبان: {str(e)}")
        return get_cafe_redirect_url('backup_management', request)
    return FileResponse(
        _TempBackupFile(zip_path),
        content_type='application/zip',
        as_attachment=True,
        filename=zip_path.name,
    )


@custom_login_required
@_backup_admin_required
def export_data_json(request):
    """Download a full database backup as JSON."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'syntel_export_{timestamp}.json'

    buffer = io.StringIO()
    call_command(
        'dumpdata',
        natural_foreign=True,
        natural_primary=True,
        exclude=['auth.permission', 'contenttypes', 'sessions'],
        stdout=buffer,
    )
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _sanitize_csv_cell(value):
    """Neutralize Excel formula injection prefixes (=, +, -, @, tab, CR)."""
    if value is None:
        return ''
    text = str(value)
    if text and text[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + text
    return text


@custom_login_required
@role_required(["manager"])
def export_data_csv(request):
    """Export specific cafe-scoped data models to CSV."""
    model_name = (request.GET.get('model') or 'products').strip()
    if model_name not in _ALLOWED_CSV_MODELS:
        return HttpResponse('Invalid model specified', status=400, content_type='text/plain; charset=utf-8')

    cafe = get_cafe_from_request(request)
    if not request.user.is_superuser and cafe is None:
        return HttpResponseForbidden('ابتدا کافه را انتخاب کنید.')

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{model_name}_{timestamp}.csv"'
    response.write(b'\xef\xbb\xbf')

    writer = csv.writer(response)

    if model_name == 'products':
        writer.writerow(['ID', 'Name', 'Category', 'Unit', 'Price', 'Stock', 'Description'])
        products_qs = Product.objects.select_related('category', 'unit').annotate(
            real_stock=Coalesce(Sum('stock_items__quantity'), Value(0, output_field=DecimalField())),
        )
        if cafe:
            products_qs = products_qs.filter(cafe=cafe)
        for obj in products_qs:
            writer.writerow([
                obj.id,
                _sanitize_csv_cell(obj.name),
                _sanitize_csv_cell(obj.category.name if obj.category else ''),
                _sanitize_csv_cell(obj.unit.name if obj.unit else ''),
                getattr(obj, 'current_price', obj.base_unit_price if obj.base_unit_price is not None else 0),
                obj.real_stock,
                _sanitize_csv_cell(obj.description),
            ])

    elif model_name == 'suppliers':
        writer.writerow(['ID', 'Name', 'Contact Person', 'Phone', 'Email', 'Address', 'Rating'])
        suppliers_qs = Supplier.objects.all()
        if cafe:
            suppliers_qs = suppliers_qs.filter(cafe=cafe)
        for obj in suppliers_qs:
            writer.writerow([
                obj.id,
                _sanitize_csv_cell(obj.name),
                _sanitize_csv_cell(obj.contact_person),
                _sanitize_csv_cell(obj.phone),
                _sanitize_csv_cell(obj.email),
                _sanitize_csv_cell(obj.address),
                obj.rating,
            ])

    elif model_name == 'purchases':
        writer.writerow(['ID', 'Requester', 'Status', 'Date Created', 'Total Items'])
        purchases_qs = PurchaseRequest.objects.select_related('user').prefetch_related('items').all()
        if cafe:
            purchases_qs = purchases_qs.filter(cafe=cafe)
        for obj in purchases_qs:
            writer.writerow([
                obj.id,
                _sanitize_csv_cell(obj.user.username if obj.user else ''),
                _sanitize_csv_cell(obj.get_status_display()),
                obj.created_at.strftime('%Y-%m-%d %H:%M'),
                obj.items.count(),
            ])

    elif model_name == 'invoices':
        writer.writerow(['ID', 'Invoice Number', 'Supplier', 'Amount', 'Status', 'Date'])
        invoices_qs = Invoice.objects.select_related('supplier').all()
        if cafe:
            invoices_qs = invoices_qs.filter(cafe=cafe)
        for obj in invoices_qs:
            writer.writerow([
                obj.id,
                _sanitize_csv_cell(obj.invoice_number),
                _sanitize_csv_cell(obj.supplier.name if obj.supplier else (obj.store_name or '')),
                obj.total_amount,
                _sanitize_csv_cell(obj.get_status_display()),
                obj.invoice_date,
            ])

    return response


@custom_login_required
@_backup_admin_required
def import_data_json(request):
    """Restore data from a JSON backup file (replaces current data safely)."""
    if request.method != 'POST' or not request.FILES.get('backup_file'):
        messages.error(request, "فایلی انتخاب نشده است.")
        return get_cafe_redirect_url('backup_management', request)

    if not cache.add(LOCK_BACKUP_OPERATION, '1', timeout=BACKUP_LOCK_TIMEOUT):
        messages.warning(request, "عملیات پشتیبان‌گیری یا بازیابی دیگری در حال حاضر در حال اجرا است.")
        return get_cafe_redirect_url('backup_management', request)

    backup_file = request.FILES['backup_file']
    temp_dir = settings.TMP_DIR
    os.makedirs(temp_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='restore_', suffix='.json', dir=str(temp_dir))
    os.close(fd)

    try:
        with open(temp_path, 'wb') as destination:
            for chunk in backup_file.chunks():
                destination.write(chunk)

        with open(temp_path, 'r', encoding='utf-8') as f:
            fixture = json.load(f)
        if not isinstance(fixture, list):
            raise ValueError("فرمت فایل JSON نامعتبر است (لیست اشیاء مورد انتظار است).")

        # Strip contenttypes/auth.permission/sessions to prevent PK corruption on loaddata
        sanitized_fixture = [
            obj for obj in fixture
            if isinstance(obj, dict) and str(obj.get('model', '')).lower() not in _FIXTURE_EXCLUDED_MODELS
        ]
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(sanitized_fixture, f)

        try:
            snapshot_path = _snapshot_db_before_restore()
        except Exception as snap_err:
            raise ValueError(f"امکان تهیه نسخه پشتیبان ایمن پیش از بازیابی وجود نداشت: {snap_err}") from snap_err

        with transaction.atomic():
            call_command('flush', verbosity=0, interactive=False)
            call_command('loaddata', temp_path, verbosity=0)

        note = f" نسخه قبلی قبل از بازیابی ذخیره شد: {snapshot_path.name}" if snapshot_path else ""
        messages.success(request, f"داده‌ها با موفقیت بازیابی شد و جایگزین داده‌های قبلی شد.{note}")
    except Exception as e:
        logger.exception("Error importing JSON backup: %s", e)
        messages.error(request, f"خطا در بازیابی داده‌ها: {str(e)}")
    finally:
        cache.delete(LOCK_BACKUP_OPERATION)
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return get_cafe_redirect_url('backup_management', request)
