"""
مسیرهای انبارگردانی و ۶ گزارش ماژول اموال و اثاثیه سکشن‌ها.
برای استفاده در purchases/urls.py از `assets_audit_urlpatterns` خروجی بگیرید.
"""
from django.urls import path

from .views import assets_audit_reports
from .views.helpers import cafe_view

cv = cafe_view

assets_audit_urlpatterns = [
    # انبارگردانی اموال
    path('assets/audits/', cv(assets_audit_reports.asset_audit_list), name='asset_audit_list'),
    path('assets/audits/create/', cv(assets_audit_reports.asset_audit_create), name='asset_audit_create'),
    path('assets/audits/<int:audit_id>/count/', cv(assets_audit_reports.asset_audit_count), name='asset_audit_count'),
    path(
        'assets/audits/<int:audit_id>/items/<int:item_id>/save/',
        cv(assets_audit_reports.asset_audit_save_item),
        name='asset_audit_save_item',
    ),
    path(
        'assets/audits/<int:audit_id>/add-unexpected/',
        cv(assets_audit_reports.asset_audit_add_unexpected),
        name='asset_audit_add_unexpected',
    ),
    path(
        'assets/audits/<int:audit_id>/submit/',
        cv(assets_audit_reports.asset_audit_submit),
        name='asset_audit_submit',
    ),
    path(
        'assets/audits/<int:audit_id>/review/',
        cv(assets_audit_reports.asset_audit_review),
        name='asset_audit_review',
    ),
    path(
        'assets/audits/<int:audit_id>/recount/',
        cv(assets_audit_reports.asset_audit_recount),
        name='asset_audit_recount',
    ),
    path(
        'assets/audits/<int:audit_id>/approve/',
        cv(assets_audit_reports.asset_audit_approve),
        name='asset_audit_approve',
    ),
    # مرکز گزارش‌ها و ۶ گزارش تحلیلی اموال
    path('assets/reports/', cv(assets_audit_reports.asset_reports_dashboard), name='asset_reports_dashboard'),
    path('assets/reports/valuation/', cv(assets_audit_reports.asset_report_valuation), name='asset_report_valuation'),
    path(
        'assets/reports/valuation/export/<str:fmt>/',
        cv(assets_audit_reports.asset_report_valuation_export),
        name='asset_report_valuation_export',
    ),
    path('assets/reports/variance/', cv(assets_audit_reports.asset_report_variance), name='asset_report_variance'),
    path(
        'assets/reports/variance/export/<str:fmt>/',
        cv(assets_audit_reports.asset_report_variance_export),
        name='asset_report_variance_export',
    ),
    path('assets/reports/breakage-trend/', cv(assets_audit_reports.asset_report_breakage), name='asset_report_breakage'),
    path('assets/reports/shortage/', cv(assets_audit_reports.asset_report_shortage), name='asset_report_shortage'),
    path(
        'assets/reports/repairs-warranty/',
        cv(assets_audit_reports.asset_report_repairs),
        name='asset_report_repairs',
    ),
    path('assets/reports/kardex/', cv(assets_audit_reports.asset_report_kardex), name='asset_report_kardex'),
    path(
        'assets/reports/kardex/export/excel/',
        cv(assets_audit_reports.asset_report_kardex_export),
        name='asset_report_kardex_export',
    ),
]
