"""Check dashboard_layout.html for key elements"""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
sys.stdout.reconfigure(encoding='utf-8')

import django
django.setup()

from django.template.loader import get_template

# Check management dashboard chain
tmpl = get_template('management_dashboard.html')
print('management_dashboard.html loaded OK')

# Get the full source by going through the template chain
# Check dashboard_layout.html directly
layout_tmpl = get_template('dashboard_layout.html')
src = layout_tmpl.template.source

checks = [
    ('id="sidebar"', 'sidebar element'),
    ('id="menuToggle"', 'menu toggle button'),
    ('toggleSidebar()', 'toggle function call'),
    ('function toggleSidebar', 'toggle function definition'),
    ('sidebar-overlay', 'sidebar overlay'),
    ('mobile-top-bar', 'mobile top bar'),
    ('mobile-menu-btn', 'mobile menu button class'),
    ('nav-item', 'nav items'),
]

for pattern, name in checks:
    if pattern in src:
        print(f'  OK: {name}')
    else:
        print(f'  FAIL: {name} NOT FOUND')

# Check if/else/endif balance
opens = src.count('{% if ')
elses = src.count('{% else %}')
endifs = src.count('{% endif %}')
print(f'\nConditional balance:')
print(f'  {{% if %}}: {opens}')
print(f'  {{% else %}}: {elses}')
print(f'  {{% endif %}}: {endifs}')
print(f'  Balanced: {opens == endifs}')

# Check doctype count
print(f'  DOCTYPE count: {src.count("<!DOCTYPE html>")} (should be 1)')
print(f'  Template length: {len(src)} chars')
print('\nDone')
