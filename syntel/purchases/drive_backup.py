"""Google Drive backup helpers (پشتیبان‌گیری گوگل‌درایو).

Uses the Drive REST API directly with `requests` (no heavy Google SDK).
The OAuth token is stored as JSON under DATA_PATH so it survives restarts
and deploys (DATA_PATH is a persistent volume on Liara).

Token file contains: access_token, refresh_token, expires_at (unix ts).
"""
from __future__ import annotations

import json
import os
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


def _data_path():
    path = getattr(settings, 'DATA_PATH', None)
    return Path(path) if path else Path('/app/data')


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
    """Persist runtime config so it takes effect without a restart."""
    os.makedirs(_data_path(), exist_ok=True)
    with open(_config_path(), 'w', encoding='utf-8') as f:
        json.dump({
            'client_id': client_id.strip(),
            'client_secret': client_secret.strip(),
            'folder_id': folder_id.strip(),
        }, f)


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
    os.makedirs(_data_path(), exist_ok=True)
    with open(auto_state_path(), 'w', encoding='utf-8') as f:
        json.dump(state, f)


def auto_backup_interval_hours() -> int:
    try:
        return int(os.environ.get('GOOGLE_DRIVE_AUTO_INTERVAL_HOURS', '12'))
    except ValueError:
        return 12


def is_connected() -> bool:
    return is_configured() and os.path.exists(token_path())


def save_token(data: dict) -> None:
    os.makedirs(_data_path(), exist_ok=True)
    with open(token_path(), 'w', encoding='utf-8') as f:
        json.dump(data, f)


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
    resp = requests.post(TOKEN_URL, data={
        'code': code,
        'client_id': get_client_id(),
        'client_secret': get_client_secret(),
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    data['expires_at'] = time.time() + int(data.get('expires_in', 3600)) - 60
    # Best-effort: remember the connected account email for display
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
    resp = requests.post(TOKEN_URL, data={
        'refresh_token': token['refresh_token'],
        'client_id': get_client_id(),
        'client_secret': get_client_secret(),
        'grant_type': 'refresh_token',
    }, timeout=30)
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


def _upload_resumable(access_token: str, filename: str, data: bytes) -> str:
    """Upload bytes to Drive using the resumable protocol; returns the file id."""
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
        'X-Upload-Content-Length': str(len(data)),
    }
    session = requests.Session()
    start = session.post(
        f'{UPLOAD_URL}?uploadType=resumable',
        headers=headers,
        data=json.dumps(metadata),
        timeout=30,
    )
    if start.status_code == 401:
        # Token expired — refresh once and retry the whole upload
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

    upload = session.put(upload_url, data=data, timeout=300)
    upload.raise_for_status()
    return upload.json().get('id', '')


def upload_backup(filename: str, data: bytes) -> str:
    """Upload a backup payload to Google Drive; returns the remote file id."""
    access_token = get_access_token()
    return _upload_resumable(access_token, filename, data)


def upload_file_to_drive(filename: str, filepath) -> str:
    """Upload a local file to Google Drive; returns the remote file id."""
    with open(filepath, 'rb') as f:
        data = f.read()
    return upload_backup(filename, data)


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
