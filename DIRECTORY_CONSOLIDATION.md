# Directory Consolidation Guide

## Current Problem

The project has **duplicate configuration files** scattered across two layers:

```
D:\Syntel\                    (root entry point)
├── manage.py                  wrapper — adds syntel/ to sys.path
├── requirements.txt           UTF-16 encoded, broken on Linux
├── liara.json                 simple version (no disk mounts)

D:\Syntel\syntel\              (Django project root)
├── manage.py                  standard manage.py
├── requirements.txt           UTF-16 encoded, identical to root copy
├── liara.json                 full version (with disk mounts)
├── syntel/                    Django project config package
├── purchases/                 main app
├── frontend/                  templates & static
└── scripts/                   utility scripts
```

This confuses package managers, deployment pipelines (Liara), and developers who expect a standard layout.

---

## Recommended Target Layout

```
D:\Syntel\                    ← STANDARD Django project root
├── manage.py                  ← SINGLE entry point (keep root version)
├── requirements.txt           ← SINGLE, clean UTF-8
├── liara.json                 ← SINGLE, full config (merged from syntel/liara.json)
├── syntel/                    ← Django project config package
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── purchases/                 ← main app
├── frontend/                  ← templates & static
├── scripts/                   ← utility scripts
├── staticfiles/               ← collected static (build output, unchanged)
└── data/                      ← ALL writable data (DATA_PATH, default /app/data)
    ├── db/                    ← SQLite database (db.sqlite3)
    ├── uploads/               ← user uploads (media)
    ├── logs/                  ← application logs (django.log)
    ├── cache/                 ← file-based cache
    ├── tmp/                   ← upload temp files
    ├── sessions/              ← session files
    └── backups/               ← database backups
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── ...
```

## Execution Steps

### Step 1 — Delete `syntel/manage.py`

The root `manage.py` is the correct entry point. It patches `sys.path` to find `syntel.settings`. The inner `syntel/manage.py` is unused when running from the root.

```bash
rm syntel/manage.py
```

### Step 2 — Delete `syntel/requirements.txt`

The root `requirements.txt` is authoritative. The inner copy is identical but will confuse Docker/Liara builds that set `WORKDIR /app/syntel`.

```bash
rm syntel/requirements.txt
```

### Step 3 — Consolidate `liara.json`

The inner `syntel/liara.json` has the more complete config (includes persistent disk mounts). Merge it to root, then remove the inner copy:

```bash
# Replace root liara.json with the full version from syntel/
cp syntel/liara.json liara.json
rm syntel/liara.json
```

### Step 4 — Clean up `syntel/syntel/liara.json`

This appears to be a stale artifact — it was copied into the project config package unintentionally:

```bash
rm syntel/syntel/liara.json
```

### Step 5 — Update Dockerfile (if needed)

Ensure your Dockerfile's `WORKDIR` is set to `/app` (the root), NOT `/app/syntel`:

```dockerfile
WORKDIR /app
COPY manage.py requirements.txt ./
COPY syntel/ ./syntel/
COPY purchases/ ./purchases/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/
```

If your Dockerfile currently does `WORKDIR /app/syntel`, change it to `/app` so the root `manage.py` and `requirements.txt` are found correctly.

### Step 6 — Update Liara deployment config

In your Liara dashboard or `liara.json`, set the **application path** to the root (where the new consolidated `liara.json` lives).

---

## What This Changes

| File | Action | Reason |
|------|--------|--------|
| `syntel/manage.py` | **DELETE** | Redundant; root `manage.py` is the entry point |
| `syntel/requirements.txt` | **DELETE** | Duplicate of root; causes confusion in Docker builds |
| `syntel/liara.json` | **MOVE to root → DELETE** | Full config merged to root, inner copy removed |
| `syntel/syntel/liara.json` | **DELETE** | Stale artifact in project config package |
| Root `manage.py` | **KEEP** | Correct entry point with sys.path patch |
| Root `requirements.txt` | **KEEP** (re-encoded as UTF-8) | Canonical dependency file |

## How to Verify

After executing the steps:

```bash
# 1. root manage.py still works
python manage.py check

# 2. Only one manage.py exists
ls -la manage.py
# → syntel/manage.py should NOT exist

# 3. Only one requirements.txt exists
ls -la requirements.txt
# → syntel/requirements.txt should NOT exist

# 4. Only one liara.json at root
ls -la liara.json
# → syntel/liara.json and syntel/syntel/liara.json should NOT exist

# 5. Django can find all modules
python manage.py showmigrations
```
