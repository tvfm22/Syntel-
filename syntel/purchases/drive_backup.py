"""Google Drive backup helpers (پشتیبان‌گیری گوگل‌درایو).

Uses the Drive REST API directly with `requests` (no heavy Google SDK).
The OAuth token is stored as JSON under DATA_PATH so it survives restarts
and deploys (DATA_PATH is a persistent volume on Liara).

Token file contains: access_token, refresh_token, expires_at (unix ts).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests
from django.conf import settings

SCOPES = ['https://www.googleapis.com/auth/drive.file']
TOKEN_FILENAME = 'drive_token.json'
CONFIG_FILENAME = 'drive_config.json'
AUTO_STATE_FILENAME = 'drive_autobackup.json'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
UPLOAD_URL = 'https://www.googleapis.com/upload/drive/v3/files'
DRIVE_API = 'https://www.googleapis.com/drive/v3'

# 4 MiB chunks (multiple of 256 KiB required by Google Drive resumable upload)
UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024


def _data_path():
    path = getattr(settings, 'DATA_PATH', None)
    if path:
        return Path(path)
    project_root = getattr(settings, 'PROJECT_ROOT', Path(settings.BASE_DIR).parent)
    return Path(project_root) / 'data'


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write JSON with restrictive (0o600 on POSIX) permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        if os.name == 'posix':
            os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
        if os.name == 'posix':
            os.chmod(path, 0o600)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def token_path():
    return _data_path() / TOKEN_FILENAME


def _config_path():
    return _data_path() / CONFIG_FILENAME


def load_config() -> dict:
    """Runtime config (saved from the backup page) with env vars as fallback."""
    file_config = {}
    if os.path.exists(_config_path()):
        try:
            with open(_config_path(), 'r', encoding='utf-8') as f:
                file_config = json.load(f)
        except (json.JSONDecodeError, OSError):
            file_config = {}
    return {
        'client_id': file_config.get('client_id') or getattr(settings, 'GOOGLE_DRIVE_CLIENT_ID', None) or '',
        'client_secret': file_config.get('client_secret') or getattr(settings, 'GOOGLE_DRIVE_CLIENT_SECRET', None) or '',
        'folder_id': file_config.get('folder_id') or getattr(settings, 'GOOGLE_DRIVE_FOLDER_ID', None) or '',
    }


def save_config(client_id: str, client_secret: str, folder_id: str = '') -> None:
    """Persist runtime config atomically so it takes effect without a restart."""
    _atomic_write_json(
        _config_path(),
        {
            'client_id': client_id.strip(),
            'client_secret': client_secret.strip(),
            'folder_id': folder_id.strip(),
        },
    )


def clear_config() -> None:
    """Remove the runtime config so values fall back to environment vars."""
    try:
        os.remove(_config_path())
    except OSError:
        pass


def get_client_id() -> str:
    return load_config()['client_id']


def get_client_secret() -> str:
    return load_config()['client_secret']


def get_folder_id() -> str:
    return load_config()['folder_id']


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg['client_id'] and cfg['client_secret'])


def auto_state_path():
    return _data_path() / AUTO_STATE_FILENAME


def get_auto_state() -> dict:
    if os.path.exists(auto_state_path()):
        try:
            with open(auto_state_path(), 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def set_auto_state(**kwargs) -> None:
    state = get_auto_state()
    state.update(kwargs)
    _atomic_write_json(auto_state_path(), state)


def auto_backup_interval_hours() -> int:
    try:
        return int(os.environ.get('GOOGLE_DRIVE_AUTO_INTERVAL_HOURS', '12'))
    except ValueError:
        return 12


def is_connected() -> bool:
    return is_configured() and os.path.exists(token_path())


def save_token(data: dict) -> None:
    _atomic_write_json(token_path(), data)


def load_token():
    path = token_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def clear_token() -> None:
    try:
        os.remove(token_path())
    except OSError:
        pass


def build_authorize_url(state: str, redirect_uri: str) -> str:
    params = {
        'client_id': get_client_id(),
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',  # force a refresh_token on first connect
        'state': state,
    }
    query = '&'.join(f'{k}={quote(str(v), safe="")}' for k, v in params.items())
    return f'{AUTH_URL}?{query}'


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange the OAuth authorization code for tokens."""
    resp = requests.post(
        TOKEN_URL,
        data={
            'code': code,
            'client_id': get_client_id(),
            'client_secret': get_client_secret(),
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    data['expires_at'] = time.time() + int(data.get('expires_in', 3600)) - 60
    try:
        data['email'] = get_account_email(data['access_token'])
    except Exception:
        pass
    save_token(data)
    return data


def get_connected_email() -> str:
    """Return the email of the connected account, if we stored it."""
    token = load_token()
    if not token:
        return ''
    return token.get('email', '')


def _refresh_access_token() -> str:
    """Refresh the access token using the stored refresh_token."""
    token = load_token()
    if not token or not token.get('refresh_token'):
        raise ValueError('اتصال گوگل‌درایو منقضی شده است. دوباره اتصال را برقرار کنید.')
    resp = requests.post(
        TOKEN_URL,
        data={
            'refresh_token': token['refresh_token'],
            'client_id': get_client_id(),
            'client_secret': get_client_secret(),
            'grant_type': 'refresh_token',
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token['access_token'] = data['access_token']
    token['expires_at'] = time.time() + int(data.get('expires_in', 3600)) - 60
    save_token(token)
    return token['access_token']


def get_access_token() -> str:
    token = load_token()
    if not token or not token.get('access_token'):
        raise ValueError('اتصال گوگل‌درایو برقرار نیست.')
    if token.get('expires_at', 0) <= time.time():
        return _refresh_access_token()
    return token['access_token']


def _start_resumable_session(session: requests.Session, access_token: str, filename: str, total_size: int) -> str:
    metadata = {
        'name': filename,
        'mimeType': 'application/octet-stream',
    }
    folder_id = get_folder_id()
    if folder_id:
        metadata['parents'] = [folder_id]

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Type': 'application/octet-stream',
        'X-Upload-Content-Length': str(total_size),
    }
    start = session.post(
        f'{UPLOAD_URL}?uploadType=resumable',
        headers=headers,
        data=json.dumps(metadata),
        timeout=30,
    )
    if start.status_code == 401:
        access_token = _refresh_access_token()
        headers['Authorization'] = f'Bearer {access_token}'
        start = session.post(
            f'{UPLOAD_URL}?uploadType=resumable',
            headers=headers,
            data=json.dumps(metadata),
            timeout=30,
        )
    start.raise_for_status()
    upload_url = start.headers.get('Location')
    if not upload_url:
        raise ValueError('پاسخ نامعتبر از گوگل‌درایو دریافت شد.')
    return upload_url


def _upload_resumable(access_token: str, filename: str, data: bytes) -> str:
    """Upload bytes to Drive using the resumable protocol; returns the file id."""
    session = requests.Session()
    upload_url = _start_resumable_session(session, access_token, filename, len(data))
    upload = session.put(upload_url, data=data, timeout=300)
    upload.raise_for_status()
    return upload.json().get('id', '')


def _upload_resumable_file(access_token: str, filename: str, filepath: Path) -> str:
    """Stream a file to Drive in fixed-size chunks without loading it all into RAM."""
    total_size = os.path.getsize(filepath)
    session = requests.Session()
    upload_url = _start_resumable_session(session, access_token, filename, total_size)

    if total_size == 0:
        upload = session.put(upload_url, data=b'', timeout=300)
        upload.raise_for_status()
        return upload.json().get('id', '')

    offset = 0
    with open(filepath, 'rb') as f:
        while offset < total_size:
            chunk = f.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            end = offset + len(chunk) - 1
            headers = {
                'Content-Length': str(len(chunk)),
                'Content-Range': f'bytes {offset}-{end}/{total_size}',
            }
            resp = session.put(upload_url, headers=headers, data=chunk, timeout=300)
            if resp.status_code == 308:
                offset += len(chunk)
                continue
            resp.raise_for_status()
            return resp.json().get('id', '')

    raise ValueError('آپلود فایل به گوگل‌درایو ناتمام ماند.')


def upload_backup(filename: str, data: bytes) -> str:
    """Upload a backup payload to Google Drive; returns the remote file id."""
    access_token = get_access_token()
    return _upload_resumable(access_token, filename, data)


def upload_file_to_drive(filename: str, filepath) -> str:
    """Stream a local file to Google Drive in chunks; returns the remote file id."""
    access_token = get_access_token()
    return _upload_resumable_file(access_token, filename, Path(filepath))


def get_account_email(access_token: str = '') -> str:
    """Return the connected Google account email (for display)."""
    if not access_token:
        access_token = get_access_token()
    resp = requests.get(
        f'{DRIVE_API}/about?fields=user(emailAddress)',
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=30,
    )
    if resp.status_code == 401:
        access_token = _refresh_access_token()
        resp = requests.get(
            f'{DRIVE_API}/about?fields=user(emailAddress)',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=30,
        )
    if resp.status_code != 200:
        return ''
    return resp.json().get('user', {}).get('emailAddress', '')
