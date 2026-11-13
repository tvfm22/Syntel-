"""Shared runtime data directory resolution and first-boot seeding."""
import os
import shutil
import sys
from pathlib import Path

SUBDIR_NAMES = ('logs', 'db', 'uploads', 'cache', 'tmp', 'sessions', 'backups')


def _is_windows_drive_path(raw: str) -> bool:
    return len(raw) >= 2 and raw[1] == ':' and raw[0].isalpha()


def resolve_data_path(project_root: Path) -> Path:
    """Resolve a writable DATA_PATH, falling back to <project_root>/data on S2I/PaaS."""
    default_path = (project_root / 'data').resolve()
    raw = (os.environ.get('DATA_PATH') or '').strip()
    if not raw:
        return default_path

    # Ignore Windows paths (e.g. from a tracked .env) when running in Linux containers.
    if os.name != 'nt' and _is_windows_drive_path(raw):
        return default_path

    candidate = Path(raw).resolve()
    try:
        (candidate / 'logs').mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"WARNING: DATA_PATH={candidate} is not writable ({exc}); falling back to {default_path}",
            file=sys.stderr,
        )
        return default_path
    return candidate


def build_data_subdirs(data_path: Path) -> dict[str, Path]:
    """Return the standard writable subdirectory mapping under DATA_PATH."""
    return {name: data_path / name for name in SUBDIR_NAMES}


def ensure_data_dirs(
    data_subdirs: dict[str, Path],
    project_root: Path | None = None,
    data_path: Path | None = None,
) -> None:
    """Create writable data directories and seed DB/uploads into a fresh DATA_PATH."""
    for path in data_subdirs.values():
        path.mkdir(parents=True, exist_ok=True)

    if project_root is None or data_path is None:
        return

    shipped_data = (project_root / 'data').resolve()
    if data_path == shipped_data:
        return

    target_db = data_subdirs['db'] / 'db.sqlite3'
    shipped_db = shipped_data / 'db' / 'db.sqlite3'
    if not target_db.exists() and shipped_db.exists():
        shutil.copy2(shipped_db, target_db)
        shipped_uploads = shipped_data / 'uploads'
        if shipped_uploads.exists():
            shutil.copytree(shipped_uploads, data_subdirs['uploads'], dirs_exist_ok=True)
