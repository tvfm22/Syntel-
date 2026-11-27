"""
مسیرهای مدیریت اموال و اثاثیه سکشن‌ها (کاتالوگ، موجودی بخش‌ها، پلاک‌ها، انتقال دوطرفه،
گزارش خرابی، ورود از فاکتور و ایجاد درخواست خرید جایگزین).
"""
from django.urls import path

from .views import assets_management
from .views.helpers import cafe_view

cv = cafe_view

assets_management_urlpatterns = [
    path('assets/', cv(assets_management.assets_dashboard), name='assets_dashboard'),
    path('assets/categories/', cv(assets_management.asset_category_list), name='asset_category_list'),
    path('assets/catalog/', cv(assets_management.asset_catalog_list), name='asset_catalog_list'),
    path('assets/catalog/create/', cv(assets_management.asset_catalog_create), name='asset_catalog_create'),
    path('assets/catalog/<int:pk>/edit/', cv(assets_management.asset_catalog_edit), name='asset_catalog_edit'),
    path(
        'assets/departments/<int:department_id>/stock/',
        cv(assets_management.asset_department_stock),
        name='asset_department_stock',
    ),
    path(
        'assets/departments/<int:department_id>/allocate/',
        cv(assets_management.asset_stock_allocate),
        name='asset_stock_allocate',
    ),
    path('assets/units/', cv(assets_management.asset_unit_list), name='asset_unit_list'),
    path('assets/units/create/', cv(assets_management.asset_unit_create), name='asset_unit_create'),
    path('assets/units/<int:pk>/', cv(assets_management.asset_unit_detail), name='asset_unit_detail'),
    path(
        'assets/units/<int:pk>/repair/',
        cv(assets_management.asset_unit_repair_action),
        name='asset_unit_repair_action',
    ),
    path('assets/transfers/', cv(assets_management.asset_transfer_list), name='asset_transfer_list'),
    path('assets/transfers/create/', cv(assets_management.asset_transfer_create), name='asset_transfer_create'),
    path(
        'assets/transfers/<int:pk>/confirm/',
        cv(assets_management.asset_transfer_confirm),
        name='asset_transfer_confirm',
    ),
    path(
        'assets/transfers/<int:pk>/reject/',
        cv(assets_management.asset_transfer_reject),
        name='asset_transfer_reject',
    ),
    path(
        'assets/departments/<int:department_id>/report-damage/',
        cv(assets_management.asset_damage_report),
        name='asset_damage_report',
    ),
    path('assets/movements/pending/', cv(assets_management.asset_pending_movements), name='asset_pending_movements'),
    path(
        'assets/movements/<int:pk>/approve/',
        cv(assets_management.asset_movement_approve),
        name='asset_movement_approve',
    ),
    path(
        'assets/movements/<int:pk>/reject/',
        cv(assets_management.asset_movement_reject),
        name='asset_movement_reject',
    ),
    path('assets/import-invoice/', cv(assets_management.asset_import_from_invoice), name='asset_import_from_invoice'),
    path(
        'assets/create-replacement-request/',
        cv(assets_management.asset_create_replacement_request),
        name='asset_create_replacement_request',
    ),
    path(
        'assets/departments/<int:department_id>/create-replacement-request/',
        cv(assets_management.asset_create_replacement_request),
        name='asset_department_create_replacement_request',
    ),
]
