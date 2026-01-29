from django.urls import path
from django.shortcuts import redirect
from . import views
from . import views_dashboard
from . import views_analytics

urlpatterns = [
    path('', lambda request: redirect('login'), name='home'),
    path('accounts/login/', lambda request: redirect('login'), name='accounts_login_redirect'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('mobile-dashboard/', views.mobile_dashboard, name='mobile_dashboard'),
    path('purchasing-dashboard/', views.purchasing_dashboard, name='purchasing_dashboard'),
    path('purchasing/requests/', views.purchasing_requests_list, name='purchasing_requests_list'),
    path('purchasing/request/<int:request_id>/process/', views.process_purchase_request, name='process_purchase_request'),
    path('finance-dashboard/', views.finance_dashboard, name='finance_dashboard'),
    path('finance-manager-dashboard/', views.finance_manager_dashboard, name='finance_manager_dashboard'),
    path('purchase-request/', views.purchase_request_form, name='purchase_request'),
    path('purchase-request-success/', views.purchase_request_success, name='purchase_request_success'),
    path('purchase-requests/', views.purchase_requests_list, name='purchase_requests_list'),
    path('purchase-request/<int:request_id>/', views.purchase_request_detail, name='purchase_request_detail'),
    path('purchase-request/<int:request_id>/update/', views.update_purchase_request, name='update_purchase_request'),
    path('reports/', views.reports_dashboard, name='reports_dashboard'),
    path('reports/export/', views.export_to_excel, name='export_to_excel'),
    path('reports/export/pdf/', views.export_to_pdf, name='export_to_pdf'),
    path('reports/export/enhanced-excel/', views.export_enhanced_excel, name='export_enhanced_excel'),
    path('analytics/<str:analytics_type>/export/pdf/', views.export_analytics_to_pdf, name='export_analytics_to_pdf'),
    # Backup and Migration URLs
    path('backup/', views.backup_management, name='backup_management'),
    path('backup/database/', views.backup_database, name='backup_database'),
    path('backup/export/json/', views.export_data_json, name='export_data_json'),
    path('backup/export/csv/', views.export_data_csv, name='export_data_csv'),
    path('backup/import/json/', views.import_data_json, name='import_data_json'),
    path('backup/migration/create/', views.create_migration_script, name='create_migration_script'),
    path('backup/migration/run/', views.run_migration_script, name='run_migration_script'),
    path('system/health/', views.system_health_check, name='system_health_check'),
    # Purchase Lists URLs
    path('purchase-lists/', views.purchase_lists_by_date, name='purchase_lists_by_date'),
    path('purchase-lists/detail/', views.purchase_day_detail, name='purchase_day_detail'),
    path('purchase-lists/export-pdf/', views.export_purchase_day_pdf, name='export_purchase_day_pdf'),
    path('purchase-lists/request/<int:request_id>/edit/', views.purchase_list_edit_request, name='purchase_list_edit_request'),
    path('purchase-lists/request/<int:request_id>/confirm/', views.purchase_list_confirm_request, name='purchase_list_confirm_request'),
    # Product Management URLs
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/<int:product_id>/update/', views.product_update, name='product_update'),
    path('products/<int:product_id>/delete/', views.product_delete, name='product_delete'),
    # Unit Management URLs
    path('units/', views.unit_list, name='unit_list'),
    path('units/create/', views.unit_create, name='unit_create'),
    path('units/<int:unit_id>/update/', views.unit_update, name='unit_update'),
    path('units/<int:unit_id>/delete/', views.unit_delete, name='unit_delete'),
    # Category Management URLs
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/<int:category_id>/update/', views.category_update, name='category_update'),
    path('categories/<int:category_id>/delete/', views.category_delete, name='category_delete'),
    # Payment Type Management URLs
    path('payment-types/', views.payment_type_list, name='payment_type_list'),
    path('payment-types/create/', views.payment_type_create, name='payment_type_create'),
    path('payment-types/<int:payment_type_id>/update/', views.payment_type_update, name='payment_type_update'),
    path('payment-types/<int:payment_type_id>/delete/', views.payment_type_delete, name='payment_type_delete'),
    # User Management URLs
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:user_id>/update/', views.user_update, name='user_update'),
    path('users/<int:user_id>/delete/', views.user_delete, name='user_delete'),
    path('users/<int:user_id>/reset-password/', views.user_reset_password, name='user_reset_password'),
    # Purchase Order Management URLs
    path('purchase-orders/', views.purchase_orders_list, name='purchase_orders_list'),
    path('purchase-orders/create/<int:request_id>/', views.purchase_order_create, name='purchase_order_create'),
    path('purchase-orders/<int:order_id>/', views.purchase_order_detail, name='purchase_order_detail'),
    path('purchase-orders/<int:order_id>/update/', views.purchase_order_update, name='purchase_order_update'),
    path('purchase-orders/<int:order_id>/send/', views.purchase_order_send, name='purchase_order_send'),
    path('purchase-orders/<int:order_id>/confirm/', views.purchase_order_confirm, name='purchase_order_confirm'),
    path('purchase-orders/<int:order_id>/in-transit/', views.purchase_order_mark_in_transit, name='purchase_order_mark_in_transit'),
    path('purchase-orders/<int:order_id>/deliver/', views.purchase_order_deliver, name='purchase_order_deliver'),
    path('purchase-orders/<int:order_id>/inspect/', views.purchase_order_inspect, name='purchase_order_inspect'),
    path('purchase-orders/<int:order_id>/complete/', views.purchase_order_complete, name='purchase_order_complete'),
    path('purchase-orders/<int:order_id>/cancel/', views.purchase_order_cancel, name='purchase_order_cancel'),
    path('approved-requests-for-orders/', views.approved_requests_for_orders, name='approved_requests_for_orders'),
    # Invoice Management URLs
    path('invoices/', views.invoices_list, name='invoices_list'),
    path('all-invoices/', views.all_invoices_list, name='all_invoices_list'),
    path('invoices/bulk-delete/', views.invoice_bulk_delete, name='invoice_bulk_delete'),
    path('invoices/register/', views.invoice_register, name='invoice_register'),
    path('invoices/search-products/', views.search_supplier_products, name='search_supplier_products'),
    path('api/products/autocomplete/', views.search_products_autocomplete, name='products_autocomplete'),
    path('api/categories/autocomplete/', views.search_categories_autocomplete, name='categories_autocomplete'),
    path('invoices/create/<int:order_id>/', views.invoice_create, name='invoice_create'),
    path('invoices/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:invoice_id>/update/', views.invoice_update, name='invoice_update'),
    path('invoices/<int:invoice_id>/send/', views.invoice_send, name='invoice_send'),
    path('invoices/<int:invoice_id>/approve/', views.invoice_approve, name='invoice_approve'),
    path('invoices/<int:invoice_id>/reject/', views.invoice_reject, name='invoice_reject'),
    path('invoices/<int:invoice_id>/request-revision/', views.invoice_request_revision, name='invoice_request_revision'),
    path('invoices/<int:invoice_id>/cancel/', views.invoice_cancel, name='invoice_cancel'),
    path('invoices/<int:invoice_id>/delete/', views.invoice_delete, name='invoice_delete'),
    path('invoices/finance-manager-edited/', views.finance_manager_edited_invoices, name='finance_manager_edited_invoices'),
    path('pending-approvals/', views.pending_invoice_approvals, name='pending_invoice_approvals'),
    path('approval-history/', views.approval_history, name='approval_history'),
    path('completed-orders-for-invoices/', views.completed_orders_for_invoices, name='completed_orders_for_invoices'),
    # Payment Management URLs
    path('payments/', views.payment_list, name='payment_list'),
    path('payments/create/', views.payment_create, name='payment_create'),
    path('payments/create/<int:invoice_id>/', views.payment_create, name='payment_create_for_invoice'),
    path('payments/<int:payment_id>/', views.payment_detail, name='payment_detail'),
    path('payments/<int:payment_id>/update/', views.payment_update, name='payment_update'),
    path('payments/<int:payment_id>/delete/', views.payment_delete, name='payment_delete'),
    path('payment-methods/', views.payment_location_list, name='payment_location_list'),
    path('payment-methods/create/', views.payment_location_create, name='payment_location_create'),
    path('payment-methods/<int:method_id>/update/', views.payment_location_update, name='payment_location_update'),
    # Supplier Management URLs
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/select/', views.supplier_select, name='supplier_select'),
    path('suppliers/create/', views.supplier_create, name='supplier_create'),
    path('suppliers/<int:supplier_id>/', views.supplier_detail, name='supplier_detail'),
    path('suppliers/<int:supplier_id>/update/', views.supplier_update, name='supplier_update'),
    path('suppliers/<int:supplier_id>/delete/', views.supplier_delete, name='supplier_delete'),
    path('suppliers/<int:supplier_id>/activate/', views.supplier_activate, name='supplier_activate'),
    path('suppliers/<int:supplier_id>/deactivate/', views.supplier_deactivate, name='supplier_deactivate'),
    path('suppliers/<int:supplier_id>/evaluations/', views.supplier_evaluation_list, name='supplier_evaluation_list'),
    path('suppliers/<int:supplier_id>/evaluations/create/', views.supplier_evaluation_create, name='supplier_evaluation_create'),
    path('supplier-evaluations/', views.supplier_evaluation_all_list, name='supplier_evaluation_all_list'),
    path('supplier-evaluations/create/', views.supplier_evaluation_create_general, name='supplier_evaluation_create_general'),
    path('supplier-performance/', views.supplier_performance_report, name='supplier_performance_report'),
    # Requester Management URLs
    path('requesters/', views.requester_list, name='requester_list'),
    path('requesters/create/', views.requester_create, name='requester_create'),
    path('requesters/<int:requester_id>/delete/', views.requester_delete, name='requester_delete'),
    # Invoice Issue Management URLs
    path('invoice-issues/', views.invoice_issue_list, name='invoice_issue_list'),
    path('invoice-issues/create/', views.invoice_issue_create, name='invoice_issue_create'),
    path('invoice-issues/<int:issue_id>/delete/', views.invoice_issue_delete, name='invoice_issue_delete'),
    path('purchase-reports/', views.purchase_reports, name='purchase_reports'),
    path('payment-analytics/', views.payment_analytics, name='payment_analytics'),
    path('inventory-reports/', views.inventory_reports, name='inventory_reports'),
    # Advanced Analytics URLs
    path('analytics/', views.analytics_dashboard, name='analytics_dashboard'),
    path('analytics/purchase/', views.purchase_analytics, name='purchase_analytics'),
    path('analytics/financial/', views.financial_analytics, name='financial_analytics'),
    path('analytics/suppliers/', views.supplier_analytics, name='supplier_analytics'),
    path('analytics/inventory/', views.inventory_analytics, name='inventory_analytics'),
    path('analytics/executive/', views.executive_summary, name='executive_summary'),
    path('analytics/category-analysis/', views.purchase_category_analysis, name='purchase_category_analysis'),

    # Analytics API URLs (Original)
    path('api/analytics/expenses/departments/', views_analytics.department_expenses_api, name='api_department_expenses_old'),
    path('api/analytics/expenses/categories/<int:department_id>/', views_analytics.category_expenses_api, name='api_category_expenses_old'),
    path('api/analytics/expenses/products/<int:department_id>/<int:category_id>/', views_analytics.product_list_api, name='api_product_list_old'),
    
    # New Bubble Chart Analytics API URLs
    path('api/analytics/departments/expenses/', views_analytics.api_department_expenses, name='api_analytics_departments'),
    path('api/analytics/department/<int:dept_id>/categories/', views_analytics.api_department_categories, name='api_analytics_categories'),
    path('api/analytics/category/<int:cat_id>/subcategories/', views_analytics.api_category_subcategories, name='api_analytics_subcategories'),
    path('api/analytics/category/<int:cat_id>/products/', views_analytics.api_category_products, name='api_analytics_products'),
    
    # Line Chart Analytics API URLs (نمودار خطی)
    path('api/analytics/trend/', views_analytics.api_expense_trend, name='api_expense_trend'),
    path('api/analytics/department/<int:dept_id>/trend/', views_analytics.api_department_trend, name='api_department_trend'),
    path('api/analytics/comparison/', views_analytics.api_comparison_trend, name='api_comparison_trend'),
    
    # Analytics Page: Line Chart (صفحه نمودار خطی)
    path('analytics/line-chart/', views.analytics_line_chart, name='analytics_line_chart'),
    
    # Analytics Page: Price Monitor (صفحه نظارت بر قیمت)
    path('analytics/prices/', views.analytics_price_monitor, name='analytics_price_monitor'),
    
    # Invoice Analysis API URLs (تحلیل خرید بر اساس دسته‌بندی)
    path('api/invoices/subcategories/', views_analytics.api_invoice_subcategories, name='api_invoice_subcategories'),
    path('api/invoices/suppliers/', views_analytics.api_invoice_suppliers, name='api_invoice_suppliers'),
    path('api/invoices/product-history/', views_analytics.api_product_purchase_history, name='api_product_purchase_history'),
    
    # Test page
    path('test/filter/', views.test_filter_page, name='test_filter_page'),
    
    # System Monitoring URLs
    path('system/monitoring/', views.system_monitoring, name='system_monitoring'),
    path('system/monitoring/api/', views.system_monitoring_api, name='system_monitoring_api'),
    # Notification System URLs
    path('notifications/', views.notifications_list, name='notifications_list'),
    path('notifications/<int:notification_id>/', views.notification_detail, name='notification_detail'),
    path('notifications/<int:notification_id>/mark-read/', views.mark_notification_read, name='mark_notification_read'),
    path('notifications/<int:notification_id>/delete/', views.delete_notification, name='delete_notification'),
    path('notifications/unread-count/', views.get_unread_notifications_count, name='unread_notifications_count'),
    path('notifications/recent/', views.get_recent_notifications, name='recent_notifications'),

    # Price Dashboard Page
    path('dashboard/prices/', views_dashboard.price_dashboard_view, name='price_dashboard'),
    path('dashboard/prices/test/', views_dashboard.price_test_view, name='price_test'),

    # Dashboard API URLs
    path('api/dashboard/products/changes/', views_dashboard.ProductChangeListAPIView.as_view(), name='api_product_changes'),
    path('api/dashboard/products/high-increase/', views_dashboard.HighIncreaseProductsAPIView.as_view(), name='api_high_increase'),
    path('api/dashboard/products/<int:pk>/history/', views_dashboard.PriceHistoryAPIView.as_view(), name='api_price_history'),
    path('api/dashboard/stats/', views_dashboard.DashboardStatsAPIView.as_view(), name='api_dashboard_stats'),
    path('api/dashboard/filters/', views_dashboard.DashboardFiltersAPIView.as_view(), name='api_dashboard_filters'),
    path('api/dashboard/export/', views_dashboard.DashboardExportAPIView.as_view(), name='api_dashboard_export'),
    
    # Stock Management URLs (مدیریت انبار)
    path('stock/initial-register/', views.initial_stock_register, name='initial_stock_register'),
    path('stock/list/', views.stock_list, name='stock_list'),
    path('stock/<int:stock_id>/transactions/', views.stock_transactions, name='stock_transactions'),
    path('stock/item/<int:item_id>/mark-for-supply/', views.mark_item_for_stock_supply, name='mark_item_for_stock_supply'),
    path('stock/supply-list/', views.stock_supply_list, name='stock_supply_list'),
    path('stock/request/<int:request_id>/fulfill/', views.fulfill_stock_request, name='fulfill_stock_request'),
]