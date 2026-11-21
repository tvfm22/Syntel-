"""Shared runtime data directory resolution and first-boot seeding."""
import os
from pathlib import Path
import shutil
import tempfile
import time

SUBDIR_NAMES = ('logs', 'db', 'uploads', 'cache', 'tmp', 'sessions', 'backups')


def _is_windows_drive_path(raw: str) -> bool:
    return len(raw) >= 2 and raw[1] == ':' and raw[0].isalpha()


def resolve_data_path(project_root: Path) -> Path:
    """Resolve a writable DATA_PATH.

    If DATA_PATH is explicitly configured via environment variable and cannot be
    created or written to, raise a fatal error instead of silently falling back
    to ephemeral container disk.
    """
    default_path = (project_root / 'data').resolve()
    raw = (os.environ.get('DATA_PATH') or '').strip()
    if not raw:
        return default_path

    if os.name != 'nt' and _is_windows_drive_path(raw):
        raise RuntimeError(
            f"Configured DATA_PATH={raw!r} is a Windows path and cannot be used on {os.name}."
        )

    candidate = Path(raw).resolve()
    try:
        (candidate / 'logs').mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix='.write_probe_', dir=str(candidate / 'logs'))
        os.close(fd)
        os.remove(probe)
    except OSError as exc:
        raise RuntimeError(
            f"Configured DATA_PATH={candidate} is not writable ({exc})."
        ) from exc
    return candidate


def build_data_subdirs(data_path: Path) -> dict[str, Path]:
    """Return the standard writable subdirectory mapping under DATA_PATH."""
    return {name: data_path / name for name in SUBDIR_NAMES}


def _find_seed_root(project_root: Path, data_path: Path) -> Path | None:
    """Locate a non-shadowed seed directory containing db/db.sqlite3."""
    for candidate in (project_root / 'seed_data', project_root / 'data'):
        resolved = candidate.resolve()
        if resolved != data_path and (resolved / 'db' / 'db.sqlite3').exists():
            return resolved
    return None


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy src to dst atomically via a temp file in dst.parent + os.replace."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{dst.name}.', dir=str(dst.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(src, tmp_path)
        os.replace(tmp_path, dst)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def ensure_data_dirs(
    data_subdirs: dict[str, Path],
    project_root: Path | None = None,
    data_path: Path | None = None,
) -> None:
    """Create writable data directories and seed DB/uploads into a fresh DATA_PATH atomically."""
    for path in data_subdirs.values():
        path.mkdir(parents=True, exist_ok=True)

    if project_root is None or data_path is None:
        return

    target_db = data_subdirs['db'] / 'db.sqlite3'
    if target_db.exists():
        return

    seed_root = _find_seed_root(project_root, data_path)
    if seed_root is None:
        return

    lock_path = data_subdirs['db'] / '.seed.lock'
    acquired = False
    for _ in range(50):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            if target_db.exists():
                return
            time.sleep(0.1)
        except OSError:
            break

    if not acquired:
        return

    try:
        if not target_db.exists():
            _atomic_copy_file(seed_root / 'db' / 'db.sqlite3', target_db)
            seed_uploads = seed_root / 'uploads'
            if seed_uploads.exists():
                shutil.copytree(seed_uploads, data_subdirs['uploads'], dirs_exist_ok=True)
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass
