# Reference inputs

Source files used to build or seed the application. These are not runtime data
and must not be edited by the application.

- `menu/منو کوکر.xlsx` — recipe and cost workbook imported by
  `python manage.py import_kuker_menu`.
- `legacy/prd_fixed.xlsm` — old product/category import workbook kept for
  historical reference only.

Writable runtime data (database, uploads, logs, sessions, backups) lives under
`data/`.
