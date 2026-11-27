from django.urls import path, include
from django.shortcuts import redirect
from . import api_mobile
from .views import accounts
from .views import analytics
from .views import analytics_api
from .views import approvals
from .views import backups
from .views import catalog
from .views import categories
from .views import cogs
from .views import cogs_items
from .views import consumption_reports
from .views import dashboards
from .views import debts
from .views import inventory
from .views import invoicing
from .views import monitoring
from .views import notifications
from .views import orders
from .views import payments
from .views import price_dashboard
from .views import purchasing
from .views import reports
from .views import request_management
from .views import requests
from .views import suppliers
from .views import vouchers
from .views import warehouse
from .views import warehouse_drafts
from .views.helpers import cafe_view
from .urls_assets_management import assets_management_urlpatterns
from .urls_assets_audit import assets_audit_urlpatterns


# Apply wrapper to all views
cv = cafe_view  # shorthand


# مسیرهای بدون کافه (authentication و انتخاب کافه)
auth_urlpatterns = [
    path('', lambda request: redirect('login'), name='home'),
    path('accounts/login/', lambda request: redirect('login'), name='accounts_login_redirect'),
    path('login/', accounts.login_view, name='login'),
    path('logout/', accounts.logout_view, name='logout'),
    path('select-cafe/', accounts.select_cafe, name='select_cafe'),
    # Google Drive OAuth callback — must be an exact, stable redirect URI (no cafe prefix)
    path('backup/drive/callback/', backups.drive_callback, name='drive_callback'),
    # Mobile Android App DRF Authentication API
    path('api/mobile/auth/login/', api_mobile.mobile_login, name='mobile_api_login'),
    path('api/mobile/auth/me/', api_mobile.mobile_me, name='mobile_api_me'),
    path('api/mobile/auth/logout/', api_mobile.mobile_logout, name='mobile_api_logout'),
]

# مسیرهای داخل کافه (با prefix cafe_code)
cafe_urlpatterns = [
    path('dashboard/', cv(dashboards.dashboard), name='dashboard'),
    path('mobile-dashboard/', cv(dashboards.mobile_dashboard), name='mobile_dashboard'),
    path('purchasing-dashboard/', cv(dashboards.purchasing_dashboard), name='purchasing_dashboard'),
    path('purchasing/requests/', cv(purchasing.purchasing_requests_list), name='purchasing_requests_list'),
    path('purchasing/request/<int:request_id>/process/', cv(purchasing.process_purchase_request), name='process_purchase_request'),
    # Warehouse Request URLs (درخواست از انبار)
    path('warehouse-request/', cv(requests.warehouse_request_form), name='warehouse_request_form'),
    path('warehouse-request/success/', cv(requests.warehouse_request_success), name='warehouse_request_success'),
    path('warehouse-requests/', cv(requests.warehouse_requests_list), name='warehouse_requests_list'),
    path('warehouse-requests/purchasing/', cv(purchasing.purchasing_warehouse_requests_list), name='purchasing_warehouse_requests_list'),
    path('warehouse-request/<int:request_id>/process/', cv(warehouse.process_warehouse_request), name='process_warehouse_request'),
    path('purchase-list/stock-supply/', cv(warehouse.purchase_list_with_stock_supply), name='purchase_list_with_stock_supply'),
    path('warehouse-dashboard/', cv(dashboards.warehouse_dashboard), name='warehouse_dashboard'),
    path('warehouse/available/', cv(warehouse.warehouse_available_products), name='warehouse_available_products'),
    path('api/stock-items/search/', cv(warehouse.api_search_stock_items), name='api_search_stock_items'),
    path('finance-dashboard/', cv(dashboards.finance_dashboard), name='finance_dashboard'),
    path('finance-manager-dashboard/', cv(dashboards.finance_manager_dashboard), name='finance_manager_dashboard'),
    path('purchase-request/', cv(requests.purchase_request_form), name='purchase_request'),
    path('purchase-request-success/', cv(requests.purchase_request_success), name='purchase_request_success'),
    path('purchase-requests/', cv(requests.purchase_requests_list), name='purchase_requests_list'),
    path('purchase-request/<int:request_id>/', cv(requests.purchase_request_detail), name='purchase_request_detail'),
    path('purchase-request/<int:request_id>/update/', cv(requests.update_purchase_request), name='update_purchase_request'),
    path('my-requests/<int:request_id>/edit/', cv(requests.edit_my_request), name='edit_my_request'),
    path('my-received-items/', cv(requests.my_received_items), name='my_received_items'),
    path('reports/', cv(reports.reports_dashboard), name='reports_dashboard'),
    path('reports/export/', cv(reports.export_to_excel), name='export_to_excel'),
    path('reports/export/pdf/', cv(reports.export_to_pdf), name='export_to_pdf'),
    path('reports/export/enhanced-excel/', cv(reports.export_enhanced_excel), name='export_enhanced_excel'),
    path('analytics/<str:analytics_type>/export/pdf/', cv(reports.export_analytics_to_pdf), name='export_analytics_to_pdf'),
    # Backup and Migration URLs
    path('backup/', cv(backups.backup_management), name='backup_management'),
    path('backup/database/', cv(backups.backup_database), name='backup_database'),
    path('backup/export/json/', cv(backups.export_data_json), name='export_data_json'),
    path('backup/export/csv/', cv(backups.export_data_csv), name='export_data_csv'),
    path('backup/export/folder/', cv(backups.export_data_folder), name='export_data_folder'),
    path('backup/drive/connect/', cv(backups.drive_connect), name='drive_connect'),
    path('backup/drive/disconnect/', cv(backups.drive_disconnect), name='drive_disconnect'),
    path('backup/drive/upload/', cv(backups.drive_backup_now), name='drive_backup_now'),
    path('backup/drive/save-config/', cv(backups.drive_save_config), name='drive_save_config'),
    path('backup/import/json/', cv(backups.import_data_json), name='import_data_json'),
    path('backup/download/db/', cv(backups.download_database_file), name='download_database_file'),
    path('backup/restore/db/', cv(backups.restore_database_file), name='restore_database_file'),
    path('backup/download/<str:filename>/', cv(backups.download_backup_file), name='download_backup_file'),
    path('backup/delete/<str:filename>/', cv(backups.delete_backup_file), name='delete_backup_file'),
    path('backup/migration/create/', cv(monitoring.create_migration_script), name='create_migration_script'),
    path('backup/migration/run/', cv(monitoring.run_migration_script), name='run_migration_script'),
    path('system/health/', cv(monitoring.system_health_check), name='system_health_check_cafe'),
    # Purchase Lists URLs
    path('purchase-lists/', cv(purchasing.purchase_lists_by_date), name='purchase_lists_by_date'),
    path('purchase-lists/detail/', cv(purchasing.purchase_day_detail), name='purchase_day_detail'),
    path('purchase-lists/export-pdf/', cv(purchasing.export_purchase_day_pdf), name='export_purchase_day_pdf'),
    path('purchase-lists/request/<int:request_id>/edit/', cv(purchasing.purchase_list_edit_request), name='purchase_list_edit_request'),
    path('purchase-lists/request/<int:request_id>/confirm/', cv(purchasing.purchase_list_confirm_request), name='purchase_list_confirm_request'),
    # Product Management URLs
    path('products/', cv(catalog.product_list), name='product_list'),
    path('products/create/', cv(catalog.product_create), name='product_create'),
    path('products/<int:product_id>/update/', cv(catalog.product_update), name='product_update'),
    path('products/<int:product_id>/toggle-active/', cv(catalog.product_toggle_active), name='product_toggle_active'),
    path('products/<int:product_id>/delete/', cv(catalog.product_delete), name='product_delete'),
    # Unit Management URLs
    path('units/', cv(categories.unit_list), name='unit_list'),
    path('units/create/', cv(categories.unit_create), name='unit_create'),
    path('units/<int:unit_id>/update/', cv(categories.unit_update), name='unit_update'),
    path('units/<int:unit_id>/delete/', cv(categories.unit_delete), name='unit_delete'),
    # Category Management URLs
    path('categories/', cv(categories.category_list), name='category_list'),
    path('categories/create/', cv(categories.category_create), name='category_create'),
    path('categories/<int:category_id>/update/', cv(categories.category_update), name='category_update'),
    path('categories/<int:category_id>/delete/', cv(categories.category_delete), name='category_delete'),
    # Payment Type Management URLs
    path('payment-types/', cv(invoicing.payment_type_list), name='payment_type_list'),
    path('payment-types/create/', cv(invoicing.payment_type_create), name='payment_type_create'),
    path('payment-types/<int:payment_type_id>/update/', cv(invoicing.payment_type_update), name='payment_type_update'),
    path('payment-types/<int:payment_type_id>/delete/', cv(invoicing.payment_type_delete), name='payment_type_delete'),
    # User Management URLs
    path('users/', cv(accounts.user_list), name='user_list'),
    path('users/exit-impersonation/', cv(accounts.user_impersonate_stop), name='user_impersonate_stop'),
    path('users/create/', cv(accounts.user_create), name='user_create'),
    path('users/<int:user_id>/update/', cv(accounts.user_update), name='user_update'),
    path('users/<int:user_id>/delete/', cv(accounts.user_delete), name='user_delete'),
    path('users/<int:user_id>/reset-password/', cv(accounts.user_reset_password), name='user_reset_password'),
    path('users/<int:user_id>/login-as/', cv(accounts.user_impersonate), name='user_impersonate'),
    # Department (بخش) Management URLs — از صفحه مدیریت کاربران
    path('departments/', cv(accounts.department_list), name='department_list'),
    path('departments/create/', cv(accounts.department_create), name='department_create'),
    path('departments/<int:department_id>/update/', cv(accounts.department_update), name='department_update'),
    path('departments/<int:department_id>/delete/', cv(accounts.department_delete), name='department_delete'),
    # Purchase Order Management URLs
    path('purchase-orders/', cv(orders.purchase_orders_list), name='purchase_orders_list'),
    path('purchase-orders/create/<int:request_id>/', cv(orders.purchase_order_create), name='purchase_order_create'),
    path('purchase-orders/<int:order_id>/', cv(orders.purchase_order_detail), name='purchase_order_detail'),
    path('purchase-orders/<int:order_id>/update/', cv(orders.purchase_order_update), name='purchase_order_update'),
    path('purchase-orders/<int:order_id>/send/', cv(orders.purchase_order_send), name='purchase_order_send'),
    path('purchase-orders/<int:order_id>/confirm/', cv(orders.purchase_order_confirm), name='purchase_order_confirm'),
    path('purchase-orders/<int:order_id>/in-transit/', cv(orders.purchase_order_mark_in_transit), name='purchase_order_mark_in_transit'),
    path('purchase-orders/<int:order_id>/deliver/', cv(orders.purchase_order_deliver), name='purchase_order_deliver'),
    path('purchase-orders/<int:order_id>/inspect/', cv(orders.purchase_order_inspect), name='purchase_order_inspect'),
    path('purchase-orders/<int:order_id>/complete/', cv(orders.purchase_order_complete), name='purchase_order_complete'),
    path('purchase-orders/<int:order_id>/cancel/', cv(orders.purchase_order_cancel), name='purchase_order_cancel'),
    path('approved-requests-for-orders/', cv(orders.approved_requests_for_orders), name='approved_requests_for_orders'),
    # Invoice Management URLs
    path('invoices/', cv(invoicing.invoices_list), name='invoices_list'),
    path('all-invoices/', cv(invoicing.all_invoices_list), name='all_invoices_list'),
    path('invoices/bulk-delete/', cv(invoicing.invoice_bulk_delete), name='invoice_bulk_delete'),
    path('invoices/bulk-payment/', cv(invoicing.invoice_bulk_payment), name='invoice_bulk_payment'),
    path('invoices/register/', cv(invoicing.invoice_register), name='invoice_register'),
    path('invoices/search-products/', cv(catalog.search_supplier_products), name='search_supplier_products'),
    path('api/products/autocomplete/', cv(catalog.search_products_autocomplete), name='products_autocomplete'),
    path('api/products/check-availability/', cv(catalog.check_product_availability), name='check_product_availability'),
    path('api/products/quick-add/', cv(catalog.quick_add_product), name='quick_add_product'),
    path('api/departments/quick-add/', cv(categories.quick_add_department), name='quick_add_department'),
    path('api/categories/autocomplete/', cv(categories.search_categories_autocomplete), name='categories_autocomplete'),
    path('api/categories/all/products/', cv(catalog.category_products_api), {'category_id': 'all'}, name='all_category_products_api'),
    path('api/categories/<int:category_id>/products/', cv(catalog.category_products_api), name='category_products_api'),
    path('all-departments-requests/', cv(request_management.all_departments_requests), name='all_departments_requests'),
    path('request-management/', cv(request_management.request_management_dashboard), name='request_management_dashboard'),
    path('request-management/export-csv/', cv(request_management.request_management_csv), name='request_management_csv'),
    path('request-management/warehouse-pdf/', cv(request_management.request_management_warehouse_pdf), name='request_management_warehouse_pdf'),
    path('purchase-officer/dashboard/', cv(purchasing.purchase_officer_dashboard), name='purchase_officer_dashboard'),
    path('purchase-officer/history/', cv(purchasing.purchase_officer_history), name='purchase_officer_history'),
    path('warehouse-officer/dashboard/', cv(warehouse_drafts.warehouse_officer_dashboard), name='warehouse_officer_dashboard'),
    path('warehouse-officer/purchase-request/', cv(warehouse_drafts.warehouse_purchase_draft), name='warehouse_purchase_draft'),
    path('warehouse-officer/my-purchases/', cv(warehouse.warehouse_my_purchases), name='warehouse_my_purchases'),
    path('warehouse-officer/my-purchases/history/', cv(warehouse.warehouse_my_purchases_history), name='warehouse_my_purchases_history'),
    path('purchase-orders/status/', cv(orders.purchase_orders_status), name='purchase_orders_status'),
    path('invoices/create/<int:order_id>/', cv(invoicing.invoice_create), name='invoice_create'),
    path('invoices/<int:invoice_id>/', cv(invoicing.invoice_detail), name='invoice_detail'),
    path('invoices/<int:invoice_id>/update/', cv(invoicing.invoice_update), name='invoice_update'),
    path('invoices/<int:invoice_id>/send/', cv(invoicing.invoice_send), name='invoice_send'),
    path('invoices/<int:invoice_id>/approve/', cv(invoicing.invoice_approve), name='invoice_approve'),
    path('invoices/<int:invoice_id>/cancel/', cv(invoicing.invoice_cancel), name='invoice_cancel'),
    path('invoices/<int:invoice_id>/delete/', cv(invoicing.invoice_delete), name='invoice_delete'),
    path('invoices/finance-manager-edited/', cv(invoicing.finance_manager_edited_invoices), name='finance_manager_edited_invoices'),
    path('completed-orders-for-invoices/', cv(invoicing.completed_orders_for_invoices), name='completed_orders_for_invoices'),
    # Payment Management URLs
    path('payments/', cv(payments.payment_list), name='payment_list'),
    path('payments/create/', cv(payments.payment_create), name='payment_create'),
    path('payments/create/<int:invoice_id>/', cv(payments.payment_create), name='payment_create_for_invoice'),
    path('payments/<int:payment_id>/', cv(payments.payment_detail), name='payment_detail'),
    path('payments/<int:payment_id>/update/', cv(payments.payment_update), name='payment_update'),
    path('payments/<int:payment_id>/delete/', cv(payments.payment_delete), name='payment_delete'),
    path('payment-methods/', cv(payments.payment_location_list), name='payment_location_list'),
    path('payment-methods/create/', cv(payments.payment_location_create), name='payment_location_create'),
    path('payment-methods/<int:method_id>/update/', cv(payments.payment_location_update), name='payment_location_update'),
    # Debt Management URLs (لیست بدهی شرکت‌ها)
    path('debts/', cv(debts.debt_list), name='debt_list'),
    path('debts/ceo/', cv(debts.ceo_debt_payments), name='ceo_debt_payments'),
    path('debts/mobile/', cv(debts.mobile_debt_dashboard), name='mobile_debt_dashboard'),
    path('debts/<int:debt_id>/pay/', cv(debts.debt_pay), name='debt_pay'),
    path('debts/pay-offlist/', cv(debts.debt_pay_offlist), name='debt_pay_offlist'),
    path('debts/<int:debt_id>/edit/', cv(debts.debt_edit), name='debt_edit'),
    path('debts/<int:debt_id>/delete/', cv(debts.debt_delete), name='debt_delete'),
    path('debts/payments/<int:payment_id>/edit/', cv(debts.debt_payment_edit), name='debt_payment_edit'),
    path('debts/payments/<int:payment_id>/delete/', cv(debts.debt_payment_delete), name='debt_payment_delete'),
    path('payment-accounts/', cv(debts.payment_accounts), name='payment_accounts'),
    # Supplier Management URLs
    path('suppliers/', cv(suppliers.supplier_list), name='supplier_list'),
    path('suppliers/select/', cv(suppliers.supplier_select), name='supplier_select'),
    path('suppliers/create/', cv(suppliers.supplier_create), name='supplier_create'),
    path('suppliers/<int:supplier_id>/', cv(suppliers.supplier_detail), name='supplier_detail'),
    path('suppliers/<int:supplier_id>/update/', cv(suppliers.supplier_update), name='supplier_update'),
    path('suppliers/<int:supplier_id>/delete/', cv(suppliers.supplier_delete), name='supplier_delete'),
    path('suppliers/<int:supplier_id>/activate/', cv(suppliers.supplier_activate), name='supplier_activate'),
    path('suppliers/<int:supplier_id>/deactivate/', cv(suppliers.supplier_deactivate), name='supplier_deactivate'),
    path('suppliers/<int:supplier_id>/evaluations/', cv(suppliers.supplier_evaluation_list), name='supplier_evaluation_list'),
    path('suppliers/<int:supplier_id>/evaluations/create/', cv(suppliers.supplier_evaluation_create), name='supplier_evaluation_create'),
    path('supplier-evaluations/', cv(suppliers.supplier_evaluation_all_list), name='supplier_evaluation_all_list'),
    path('supplier-evaluations/create/', cv(suppliers.supplier_evaluation_create_general), name='supplier_evaluation_create_general'),
    path('supplier-performance/', cv(suppliers.supplier_performance_report), name='supplier_performance_report'),
    # Requester Management URLs
    path('requesters/', cv(accounts.requester_list), name='requester_list'),
    path('requesters/create/', cv(accounts.requester_create), name='requester_create'),
    path('requesters/<int:requester_id>/delete/', cv(accounts.requester_delete), name='requester_delete'),
    # Invoice Issue Management URLs
    path('invoice-issues/', cv(suppliers.invoice_issue_list), name='invoice_issue_list'),
    path('invoice-issues/create/', cv(suppliers.invoice_issue_create), name='invoice_issue_create'),
    path('invoice-issues/<int:issue_id>/delete/', cv(suppliers.invoice_issue_delete), name='invoice_issue_delete'),
    path('purchase-reports/', cv(reports.purchase_reports), name='purchase_reports'),
    path('payment-analytics/', cv(reports.payment_analytics), name='payment_analytics'),
    path('inventory-reports/', cv(reports.inventory_reports), name='inventory_reports'),
    path('reports/consumption/', cv(consumption_reports.consumption_report), name='consumption_report'),
    path('reports/consumption/product/<int:product_id>/history/',
         cv(consumption_reports.consumption_product_history_api), name='consumption_product_history_api'),

    # بهای تمام شده (کالاهای مصرفی منو بر اساس فروش)
    # سه زیرصفحه ماژول: لیست منو ← قیمت‌گذاری اقلام ← محاسبه مصرف
    path('cogs/', cv(cogs.cogs_menu_list), name='cogs_menu_list'),
    path('cogs/items/', cv(cogs_items.cogs_manage_items), name='cogs_manage_items'),
    path('cogs/consumption/', cv(cogs.cogs_consumption_calc), name='cogs_consumption_calc'),
    # نام قدیمی «cogs_dashboard» → همان صفحهٔ محاسبهٔ مصرف (لینک‌های قدیمی نمی‌شکنند)
    path('cogs/report/', cv(cogs.cogs_consumption_calc), name='cogs_dashboard'),
    path('cogs/recipe/<int:item_id>/', cv(cogs.cogs_recipe_api), name='cogs_recipe_api'),
    path('cogs/item/<int:item_id>/cost-history/',
         cv(cogs.cogs_item_cost_history), name='cogs_item_cost_history'),
    path('cogs/section/save/', cv(cogs.cogs_section_save), name='cogs_section_save'),
    path('cogs/item/price/', cv(cogs_items.cogs_item_price_update), name='cogs_item_price_update'),
    # صفحهٔ ۲ — اندپوینت‌های JSON: پیش‌نمایش قیمت، تعریف ماده اولیه، ورود قیمت، ساخت آیتم
    path('cogs/price/preview/', cv(cogs_items.cogs_price_preview), name='cogs_price_preview'),
    path('cogs/price/entry/', cv(cogs_items.cogs_price_entry), name='cogs_price_entry'),
    path('cogs/raw-material/create/', cv(cogs_items.cogs_raw_material_create),
         name='cogs_raw_material_create'),
    path('cogs/menu-item/create/', cv(cogs_items.cogs_menu_item_create),
         name='cogs_menu_item_create'),
    path('cogs/item/add/', cv(cogs_items.cogs_item_add), name='cogs_item_add'),
    path('cogs/menu-item/add/', cv(cogs_items.cogs_menu_item_add), name='cogs_menu_item_add'),
    # Advanced Analytics URLs
    path('analytics/', cv(analytics.analytics_dashboard), name='analytics_dashboard'),
    path('analytics/purchase/', cv(analytics.purchase_analytics), name='purchase_analytics'),
    path('analytics/financial/', cv(analytics.financial_analytics), name='financial_analytics'),
    path('analytics/suppliers/', cv(analytics.supplier_analytics), name='supplier_analytics'),
    path('analytics/inventory/', cv(analytics.inventory_analytics), name='inventory_analytics'),
    path('analytics/executive/', cv(analytics.executive_summary), name='executive_summary'),
    path('analytics/category-analysis/', cv(analytics.purchase_category_analysis), name='purchase_category_analysis'),
    
    # Management Dashboard
    path('management-dashboard/', cv(dashboards.management_dashboard), name='management_dashboard'),
    path('management/pending-requests/', cv(approvals.manager_pending_requests), name='manager_pending_requests'),
    path('management/approve-request/<int:request_id>/', cv(approvals.manager_approve_request), name='manager_approve_request'),
    path('management/reject-request/<int:request_id>/', cv(approvals.manager_reject_request), name='manager_reject_request'),

    # Analytics API URLs (Original)
    path('api/analytics/expenses/departments/', cv(analytics.department_expenses_api), name='api_department_expenses_old'),
    path('api/analytics/expenses/categories/<int:department_id>/', cv(analytics.category_expenses_api), name='api_category_expenses_old'),
    path('api/analytics/expenses/products/<int:department_id>/<int:category_id>/', cv(analytics.product_list_api), name='api_product_list_old'),
    
    # New Bubble Chart Analytics API URLs
    path('api/analytics/departments/expenses/', cv(analytics.api_department_expenses), name='api_analytics_departments'),
    path('api/analytics/department/<int:dept_id>/categories/', cv(analytics.api_department_categories), name='api_analytics_categories'),
    path('api/analytics/category/<int:cat_id>/subcategories/', cv(analytics.api_category_subcategories), name='api_analytics_subcategories'),
    path('api/analytics/category/<int:cat_id>/products/', cv(analytics.api_category_products), name='api_analytics_products'),
    
    # Line Chart Analytics API URLs (نمودار خطی)
    path('api/analytics/trend/', cv(analytics_api.api_expense_trend), name='api_expense_trend'),
    path('api/analytics/department/<int:dept_id>/trend/', cv(analytics_api.api_department_trend), name='api_department_trend'),
    path('api/analytics/comparison/', cv(analytics_api.api_comparison_trend), name='api_comparison_trend'),
    
    # Analytics Page: Line Chart (صفحه نمودار خطی)
    path('analytics/line-chart/', cv(analytics.analytics_line_chart), name='analytics_line_chart'),
    
    # Analytics Page: Price Monitor (صفحه نظارت بر قیمت)
    path('analytics/prices/', cv(analytics.analytics_price_monitor), name='analytics_price_monitor'),
    
    # Invoice Analysis API URLs (تحلیل خرید بر اساس دسته‌بندی)
    path('api/invoices/subcategories/', cv(analytics_api.api_invoice_subcategories), name='api_invoice_subcategories'),
    path('api/invoices/suppliers/', cv(analytics_api.api_invoice_suppliers), name='api_invoice_suppliers'),
    path('api/invoices/product-history/', cv(analytics_api.api_product_purchase_history), name='api_product_purchase_history'),
    
    # Category Analysis API URLs
    path('api/category-analysis/stats/', cv(analytics_api.api_category_analysis_stats), name='api_category_analysis_stats'),
    path('api/category-analysis/data/', cv(analytics_api.api_category_analysis_data), name='api_category_analysis_data'),
    
    # Management Dashboard API URLs
    path('api/management/category-expenses/', cv(analytics_api.api_management_category_expenses), name='api_management_category_expenses'),
    path('api/management/price-increases/', cv(analytics_api.api_management_price_increases), name='api_management_price_increases'),
    
    # Finance Manager Dashboard API URLs
    path('api/finance/stats/', cv(analytics_api.api_finance_stats), name='api_finance_stats'),
    path('api/finance/recent-payments/', cv(analytics_api.api_finance_recent_payments), name='api_finance_recent_payments'),
    path('api/finance/pending-invoices/', cv(analytics_api.api_finance_pending_invoices), name='api_finance_pending_invoices'),
    
    # System Monitoring URLs
    path('system/monitoring/', cv(monitoring.system_monitoring), name='system_monitoring'),
    path('system/monitoring/api/', cv(monitoring.system_monitoring_api), name='system_monitoring_api'),
    # Notification System URLs
    path('notifications/', cv(notifications.notifications_list), name='notifications_list'),
    path('notifications/<int:notification_id>/', cv(notifications.notification_detail), name='notification_detail'),
    path('notifications/<int:notification_id>/mark-read/', cv(notifications.mark_notification_read), name='mark_notification_read'),
    path('notifications/<int:notification_id>/delete/', cv(notifications.delete_notification), name='delete_notification'),
    path('notifications/unread-count/', cv(notifications.get_unread_notifications_count), name='unread_notifications_count'),
    path('notifications/recent/', cv(notifications.get_recent_notifications), name='recent_notifications'),

    # Price Dashboard Page
    path('dashboard/prices/', cv(price_dashboard.price_dashboard_view), name='price_dashboard'),

    # Dashboard API URLs
    path('api/dashboard/products/changes/', cv(price_dashboard.ProductChangeListAPIView.as_view()), name='api_product_changes'),
    path('api/dashboard/products/high-increase/', cv(price_dashboard.HighIncreaseProductsAPIView.as_view()), name='api_high_increase'),
    path('api/dashboard/products/<int:pk>/history/', cv(price_dashboard.PriceHistoryAPIView.as_view()), name='api_price_history'),
    path('api/dashboard/stats/', cv(price_dashboard.DashboardStatsAPIView.as_view()), name='api_dashboard_stats'),
    path('api/dashboard/filters/', cv(price_dashboard.DashboardFiltersAPIView.as_view()), name='api_dashboard_filters'),
    path('api/dashboard/export/', cv(price_dashboard.DashboardExportAPIView.as_view()), name='api_dashboard_export'),
    
    # Stock Management URLs (مدیریت انبار)
    path('stock/list/', cv(inventory.stock_list), name='stock_list'),
    path('stock/<int:stock_id>/transactions/', cv(inventory.stock_transactions), name='stock_transactions'),

    # Warehouse Management URLs (مدیریت انبارها)
    path('warehouses/', cv(inventory.warehouse_management), name='warehouse_management'),
    path('warehouses/create/', cv(inventory.warehouse_create), name='warehouse_create'),
    path('warehouses/<int:warehouse_id>/edit/', cv(inventory.warehouse_edit), name='warehouse_edit'),
    path('warehouses/<int:warehouse_id>/toggle-status/', cv(inventory.warehouse_toggle_status), name='warehouse_toggle_status'),

    # Warehouse Product Definition URLs (تعریف کالا برای انبار)

    # Warehouse Inventory Check URLs (انبارگردانی)
    path('warehouses/<int:warehouse_id>/inventory-check/', cv(inventory.warehouse_inventory_check), name='warehouse_inventory_check'),
    path('warehouses/<int:warehouse_id>/inventory-submit/', cv(inventory.warehouse_inventory_submit), name='warehouse_inventory_submit'),

    # Warehouse Voucher URLs (حواله‌های انبار)
    path('vouchers/', cv(vouchers.voucher_list), name='voucher_list'),
    path('vouchers/create/<str:voucher_type>/', cv(vouchers.voucher_create), name='voucher_create'),
    path('vouchers/detail/<int:voucher_id>/', cv(vouchers.voucher_detail), name='voucher_detail'),
    path('vouchers/<int:voucher_id>/edit/', cv(vouchers.voucher_edit), name='voucher_edit'),
    path('vouchers/<int:voucher_id>/cancel/', cv(vouchers.voucher_cancel), name='voucher_cancel'),
    path('vouchers/<int:voucher_id>/approve/', cv(vouchers.voucher_approve), name='voucher_approve'),
    path('vouchers/<int:voucher_id>/reverse/', cv(vouchers.voucher_reverse), name='voucher_reverse'),

    # Stock Movements URLs (سوابق حرکت موجودی)
    path('stock/movements/', cv(inventory.stock_movements), name='stock_movements'),

    # Stock Category URLs (موجودی انبار بر اساس دسته‌بندی)
    path('stock/categories/', cv(inventory.stock_by_category), name='stock_by_category'),
    path('stock/categories/<int:category_id>/items/', cv(inventory.stock_category_items), name='stock_category_items'),

    # API URLs for inventory history
    path('api/warehouses/<int:warehouse_id>/products/<int:stock_item_id>/history/', cv(inventory.api_product_stock_history), name='api_product_stock_history'),

    # Mobile Android App DRF API (cafe-scoped)
    path('api/mobile/requester/summary/', api_mobile.mobile_requester_summary, name='mobile_api_requester_summary'),
    path('api/mobile/requester/products/', api_mobile.mobile_requester_products, name='mobile_api_requester_products'),
    path('api/mobile/requester/requests/', api_mobile.mobile_requester_requests, name='mobile_api_requester_requests'),
    path('api/mobile/requester/requests/<int:request_id>/', api_mobile.mobile_requester_edit_request, name='mobile_api_requester_edit_request'),
    path('api/mobile/requester/received-items/', api_mobile.mobile_received_items, name='mobile_api_received_items'),
    path('api/mobile/requester/received-items/confirm/', api_mobile.mobile_confirm_received_items, name='mobile_api_confirm_received_items'),
    path('api/mobile/warehouse/dashboard/', api_mobile.mobile_warehouse_dashboard, name='mobile_api_warehouse_dashboard'),
    path('api/mobile/warehouse/items/<int:item_id>/action/', api_mobile.mobile_warehouse_item_action, name='mobile_api_warehouse_item_action'),
    path('api/mobile/warehouse/draft/', api_mobile.mobile_warehouse_draft, name='mobile_api_warehouse_draft'),
    path('api/mobile/warehouse/draft/items/', api_mobile.mobile_warehouse_draft_add, name='mobile_api_warehouse_draft_add'),
    path('api/mobile/warehouse/draft/items/<int:item_id>/', api_mobile.mobile_warehouse_draft_item_detail, name='mobile_api_warehouse_draft_item_detail'),
    path('api/mobile/warehouse/draft/send/', api_mobile.mobile_warehouse_draft_send, name='mobile_api_warehouse_draft_send'),
    path('api/mobile/warehouse/stock/', api_mobile.mobile_warehouse_stock, name='mobile_api_warehouse_stock'),
    path('api/mobile/warehouse/stock/<int:stock_id>/transactions/', api_mobile.mobile_stock_transactions, name='mobile_api_stock_transactions'),
    path('api/mobile/warehouse/vouchers/', api_mobile.mobile_warehouse_vouchers, name='mobile_api_warehouse_vouchers'),
    path('api/mobile/purchasing/dashboard/', api_mobile.mobile_purchasing_dashboard, name='mobile_api_purchasing_dashboard'),
    path('api/mobile/purchasing/action/', api_mobile.mobile_purchasing_action, name='mobile_api_purchasing_action'),
    path('api/mobile/purchasing/history/', api_mobile.mobile_purchasing_history, name='mobile_api_purchasing_history'),
    *assets_management_urlpatterns,
    *assets_audit_urlpatterns,
]

# ترکیب مسیرها - مسیرهای احراز هویت + مسیرهای کافه با prefix
urlpatterns = auth_urlpatterns + [
    path('<str:cafe_code>/', include(cafe_urlpatterns)),
]