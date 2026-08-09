from django.urls import path, include
from django.shortcuts import redirect
from functools import wraps
from . import views
from . import views_dashboard
from . import views_analytics


def cafe_view(view_func):
    """
    Wrapper که پارامتر cafe_code را از kwargs حذف می‌کند.
    اطلاعات کافه از middleware در request.cafe قرار دارد.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        kwargs.pop('cafe_code', None)
        return view_func(request, *args, **kwargs)
    return wrapper


# Apply wrapper to all views
cv = cafe_view  # shorthand


# مسیرهای بدون کافه (authentication و انتخاب کافه)
auth_urlpatterns = [
    path('', lambda request: redirect('login'), name='home'),
    path('accounts/login/', lambda request: redirect('login'), name='accounts_login_redirect'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('select-cafe/', views.select_cafe, name='select_cafe'),
    path('health/', views.system_health_check, name='system_health_check'),
]

# مسیرهای داخل کافه (با prefix cafe_code)
cafe_urlpatterns = [
    path('dashboard/', cv(views.dashboard), name='dashboard'),
    path('mobile-dashboard/', cv(views.mobile_dashboard), name='mobile_dashboard'),
    path('purchasing-dashboard/', cv(views.purchasing_dashboard), name='purchasing_dashboard'),
    path('purchasing/requests/', cv(views.purchasing_requests_list), name='purchasing_requests_list'),
    path('purchasing/request/<int:request_id>/process/', cv(views.process_purchase_request), name='process_purchase_request'),
    # Warehouse Request URLs (درخواست از انبار)
    path('warehouse-request/', cv(views.warehouse_request_form), name='warehouse_request_form'),
    path('warehouse-request/success/', cv(views.warehouse_request_success), name='warehouse_request_success'),
    path('warehouse-requests/', cv(views.warehouse_requests_list), name='warehouse_requests_list'),
    path('warehouse-requests/purchasing/', cv(views.purchasing_warehouse_requests_list), name='purchasing_warehouse_requests_list'),
    path('warehouse-request/<int:request_id>/process/', cv(views.process_warehouse_request), name='process_warehouse_request'),
    path('purchase-list/stock-supply/', cv(views.purchase_list_with_stock_supply), name='purchase_list_with_stock_supply'),
    path('warehouse-dashboard/', cv(views.warehouse_dashboard), name='warehouse_dashboard'),
    path('warehouse/available/', cv(views.warehouse_available_products), name='warehouse_available_products'),
    path('api/stock-items/search/', cv(views.api_search_stock_items), name='api_search_stock_items'),
    path('finance-dashboard/', cv(views.finance_dashboard), name='finance_dashboard'),
    path('finance-manager-dashboard/', cv(views.finance_manager_dashboard), name='finance_manager_dashboard'),
    path('purchase-request/', cv(views.purchase_request_form), name='purchase_request'),
    path('purchase-request-success/', cv(views.purchase_request_success), name='purchase_request_success'),
    path('purchase-requests/', cv(views.purchase_requests_list), name='purchase_requests_list'),
    path('purchase-request/<int:request_id>/', cv(views.purchase_request_detail), name='purchase_request_detail'),
    path('purchase-request/<int:request_id>/update/', cv(views.update_purchase_request), name='update_purchase_request'),
    path('reports/', cv(views.reports_dashboard), name='reports_dashboard'),
    path('reports/export/', cv(views.export_to_excel), name='export_to_excel'),
    path('reports/export/pdf/', cv(views.export_to_pdf), name='export_to_pdf'),
    path('reports/export/enhanced-excel/', cv(views.export_enhanced_excel), name='export_enhanced_excel'),
    path('analytics/<str:analytics_type>/export/pdf/', cv(views.export_analytics_to_pdf), name='export_analytics_to_pdf'),
    # Backup and Migration URLs
    path('backup/', cv(views.backup_management), name='backup_management'),
    path('backup/database/', cv(views.backup_database), name='backup_database'),
    path('backup/export/json/', cv(views.export_data_json), name='export_data_json'),
    path('backup/export/csv/', cv(views.export_data_csv), name='export_data_csv'),
    path('backup/import/json/', cv(views.import_data_json), name='import_data_json'),
    path('backup/migration/create/', cv(views.create_migration_script), name='create_migration_script'),
    path('backup/migration/run/', cv(views.run_migration_script), name='run_migration_script'),
    path('system/health/', cv(views.system_health_check), name='system_health_check_cafe'),
    # Purchase Lists URLs
    path('purchase-lists/', cv(views.purchase_lists_by_date), name='purchase_lists_by_date'),
    path('purchase-lists/detail/', cv(views.purchase_day_detail), name='purchase_day_detail'),
    path('purchase-lists/export-pdf/', cv(views.export_purchase_day_pdf), name='export_purchase_day_pdf'),
    path('purchase-lists/request/<int:request_id>/edit/', cv(views.purchase_list_edit_request), name='purchase_list_edit_request'),
    path('purchase-lists/request/<int:request_id>/confirm/', cv(views.purchase_list_confirm_request), name='purchase_list_confirm_request'),
    # Product Management URLs
    path('products/', cv(views.product_list), name='product_list'),
    path('products/create/', cv(views.product_create), name='product_create'),
    path('products/<int:product_id>/update/', cv(views.product_update), name='product_update'),
    path('products/<int:product_id>/delete/', cv(views.product_delete), name='product_delete'),
    # Product Brand (واریانت تجاری) URLs
    path('products/<int:product_id>/brands/add/', cv(views.product_brand_add), name='product_brand_add'),
    path('products/brands/<int:brand_id>/update/', cv(views.product_brand_update), name='product_brand_update'),
    path('products/brands/<int:brand_id>/delete/', cv(views.product_brand_delete), name='product_brand_delete'),
    # Unit Management URLs
    path('units/', cv(views.unit_list), name='unit_list'),
    path('units/create/', cv(views.unit_create), name='unit_create'),
    path('units/<int:unit_id>/update/', cv(views.unit_update), name='unit_update'),
    path('units/<int:unit_id>/delete/', cv(views.unit_delete), name='unit_delete'),
    # Category Management URLs
    path('categories/', cv(views.category_list), name='category_list'),
    path('categories/create/', cv(views.category_create), name='category_create'),
    path('categories/<int:category_id>/update/', cv(views.category_update), name='category_update'),
    path('categories/<int:category_id>/delete/', cv(views.category_delete), name='category_delete'),
    # Payment Type Management URLs
    path('payment-types/', cv(views.payment_type_list), name='payment_type_list'),
    path('payment-types/create/', cv(views.payment_type_create), name='payment_type_create'),
    path('payment-types/<int:payment_type_id>/update/', cv(views.payment_type_update), name='payment_type_update'),
    path('payment-types/<int:payment_type_id>/delete/', cv(views.payment_type_delete), name='payment_type_delete'),
    # User Management URLs
    path('users/', cv(views.user_list), name='user_list'),
    path('users/create/', cv(views.user_create), name='user_create'),
    path('users/<int:user_id>/update/', cv(views.user_update), name='user_update'),
    path('users/<int:user_id>/delete/', cv(views.user_delete), name='user_delete'),
    path('users/<int:user_id>/reset-password/', cv(views.user_reset_password), name='user_reset_password'),
    # Purchase Order Management URLs
    path('purchase-orders/', cv(views.purchase_orders_list), name='purchase_orders_list'),
    path('purchase-orders/create/<int:request_id>/', cv(views.purchase_order_create), name='purchase_order_create'),
    path('purchase-orders/<int:order_id>/', cv(views.purchase_order_detail), name='purchase_order_detail'),
    path('purchase-orders/<int:order_id>/update/', cv(views.purchase_order_update), name='purchase_order_update'),
    path('purchase-orders/<int:order_id>/send/', cv(views.purchase_order_send), name='purchase_order_send'),
    path('purchase-orders/<int:order_id>/confirm/', cv(views.purchase_order_confirm), name='purchase_order_confirm'),
    path('purchase-orders/<int:order_id>/in-transit/', cv(views.purchase_order_mark_in_transit), name='purchase_order_mark_in_transit'),
    path('purchase-orders/<int:order_id>/deliver/', cv(views.purchase_order_deliver), name='purchase_order_deliver'),
    path('purchase-orders/<int:order_id>/inspect/', cv(views.purchase_order_inspect), name='purchase_order_inspect'),
    path('purchase-orders/<int:order_id>/complete/', cv(views.purchase_order_complete), name='purchase_order_complete'),
    path('purchase-orders/<int:order_id>/cancel/', cv(views.purchase_order_cancel), name='purchase_order_cancel'),
    path('approved-requests-for-orders/', cv(views.approved_requests_for_orders), name='approved_requests_for_orders'),
    # Invoice Management URLs
    path('invoices/', cv(views.invoices_list), name='invoices_list'),
    path('all-invoices/', cv(views.all_invoices_list), name='all_invoices_list'),
    path('invoices/bulk-delete/', cv(views.invoice_bulk_delete), name='invoice_bulk_delete'),
    path('invoices/bulk-payment/', cv(views.invoice_bulk_payment), name='invoice_bulk_payment'),
    path('invoices/register/', cv(views.invoice_register), name='invoice_register'),
    path('invoices/search-products/', cv(views.search_supplier_products), name='search_supplier_products'),
    path('api/products/autocomplete/', cv(views.search_products_autocomplete), name='products_autocomplete'),
    path('api/products/<int:product_id>/brands/', cv(views.product_brands_api), name='product_brands_api'),
    path('api/products/quick-add/', cv(views.quick_add_product), name='quick_add_product'),
    path('api/departments/quick-add/', cv(views.quick_add_department), name='quick_add_department'),
    path('api/categories/autocomplete/', cv(views.search_categories_autocomplete), name='categories_autocomplete'),
    path('all-departments-requests/', cv(views.all_departments_requests), name='all_departments_requests'),
    path('purchase-officer/dashboard/', cv(views.purchase_officer_dashboard), name='purchase_officer_dashboard'),
    path('warehouse-officer/dashboard/', cv(views.warehouse_officer_dashboard), name='warehouse_officer_dashboard'),
    path('purchase-orders/status/', cv(views.purchase_orders_status), name='purchase_orders_status'),
    path('invoices/create/<int:order_id>/', cv(views.invoice_create), name='invoice_create'),
    path('invoices/<int:invoice_id>/', cv(views.invoice_detail), name='invoice_detail'),
    path('invoices/<int:invoice_id>/update/', cv(views.invoice_update), name='invoice_update'),
    path('invoices/<int:invoice_id>/send/', cv(views.invoice_send), name='invoice_send'),
    path('invoices/<int:invoice_id>/cancel/', cv(views.invoice_cancel), name='invoice_cancel'),
    path('invoices/<int:invoice_id>/delete/', cv(views.invoice_delete), name='invoice_delete'),
    path('invoices/finance-manager-edited/', cv(views.finance_manager_edited_invoices), name='finance_manager_edited_invoices'),
    path('completed-orders-for-invoices/', cv(views.completed_orders_for_invoices), name='completed_orders_for_invoices'),
    # Payment Management URLs
    path('payments/', cv(views.payment_list), name='payment_list'),
    path('payments/create/', cv(views.payment_create), name='payment_create'),
    path('payments/create/<int:invoice_id>/', cv(views.payment_create), name='payment_create_for_invoice'),
    path('payments/<int:payment_id>/', cv(views.payment_detail), name='payment_detail'),
    path('payments/<int:payment_id>/update/', cv(views.payment_update), name='payment_update'),
    path('payments/<int:payment_id>/delete/', cv(views.payment_delete), name='payment_delete'),
    path('payment-methods/', cv(views.payment_location_list), name='payment_location_list'),
    path('payment-methods/create/', cv(views.payment_location_create), name='payment_location_create'),
    path('payment-methods/<int:method_id>/update/', cv(views.payment_location_update), name='payment_location_update'),
    # Debt Management URLs (لیست بدهی شرکت‌ها)
    path('debts/', cv(views.debt_list), name='debt_list'),
    path('debts/ceo/', cv(views.ceo_debt_payments), name='ceo_debt_payments'),
    path('debts/mobile/', cv(views.mobile_debt_dashboard), name='mobile_debt_dashboard'),
    path('debts/<int:debt_id>/pay/', cv(views.debt_pay), name='debt_pay'),
    path('debts/pay-offlist/', cv(views.debt_pay_offlist), name='debt_pay_offlist'),
    path('debts/<int:debt_id>/edit/', cv(views.debt_edit), name='debt_edit'),
    path('debts/<int:debt_id>/delete/', cv(views.debt_delete), name='debt_delete'),
    path('debts/payments/<int:payment_id>/edit/', cv(views.debt_payment_edit), name='debt_payment_edit'),
    path('payment-accounts/', cv(views.payment_accounts), name='payment_accounts'),
    # Supplier Management URLs
    path('suppliers/', cv(views.supplier_list), name='supplier_list'),
    path('suppliers/select/', cv(views.supplier_select), name='supplier_select'),
    path('suppliers/create/', cv(views.supplier_create), name='supplier_create'),
    path('suppliers/<int:supplier_id>/', cv(views.supplier_detail), name='supplier_detail'),
    path('suppliers/<int:supplier_id>/update/', cv(views.supplier_update), name='supplier_update'),
    path('suppliers/<int:supplier_id>/delete/', cv(views.supplier_delete), name='supplier_delete'),
    path('suppliers/<int:supplier_id>/activate/', cv(views.supplier_activate), name='supplier_activate'),
    path('suppliers/<int:supplier_id>/deactivate/', cv(views.supplier_deactivate), name='supplier_deactivate'),
    path('suppliers/<int:supplier_id>/evaluations/', cv(views.supplier_evaluation_list), name='supplier_evaluation_list'),
    path('suppliers/<int:supplier_id>/evaluations/create/', cv(views.supplier_evaluation_create), name='supplier_evaluation_create'),
    path('supplier-evaluations/', cv(views.supplier_evaluation_all_list), name='supplier_evaluation_all_list'),
    path('supplier-evaluations/create/', cv(views.supplier_evaluation_create_general), name='supplier_evaluation_create_general'),
    path('supplier-performance/', cv(views.supplier_performance_report), name='supplier_performance_report'),
    # Requester Management URLs
    path('requesters/', cv(views.requester_list), name='requester_list'),
    path('requesters/create/', cv(views.requester_create), name='requester_create'),
    path('requesters/<int:requester_id>/delete/', cv(views.requester_delete), name='requester_delete'),
    # Invoice Issue Management URLs
    path('invoice-issues/', cv(views.invoice_issue_list), name='invoice_issue_list'),
    path('invoice-issues/create/', cv(views.invoice_issue_create), name='invoice_issue_create'),
    path('invoice-issues/<int:issue_id>/delete/', cv(views.invoice_issue_delete), name='invoice_issue_delete'),
    path('purchase-reports/', cv(views.purchase_reports), name='purchase_reports'),
    path('payment-analytics/', cv(views.payment_analytics), name='payment_analytics'),
    path('inventory-reports/', cv(views.inventory_reports), name='inventory_reports'),
    # Advanced Analytics URLs
    path('analytics/', cv(views.analytics_dashboard), name='analytics_dashboard'),
    path('analytics/purchase/', cv(views.purchase_analytics), name='purchase_analytics'),
    path('analytics/financial/', cv(views.financial_analytics), name='financial_analytics'),
    path('analytics/suppliers/', cv(views.supplier_analytics), name='supplier_analytics'),
    path('analytics/inventory/', cv(views.inventory_analytics), name='inventory_analytics'),
    path('analytics/executive/', cv(views.executive_summary), name='executive_summary'),
    path('analytics/category-analysis/', cv(views.purchase_category_analysis), name='purchase_category_analysis'),
    
    # Management Dashboard
    path('management-dashboard/', cv(views.management_dashboard), name='management_dashboard'),
    path('management/pending-requests/', cv(views.manager_pending_requests), name='manager_pending_requests'),
    path('management/approve-request/<int:request_id>/', cv(views.manager_approve_request), name='manager_approve_request'),
    path('management/reject-request/<int:request_id>/', cv(views.manager_reject_request), name='manager_reject_request'),
    path('management/approve-purchase-requests/', cv(views.approve_purchase_requests), name='approve_purchase_requests'),

    # Analytics API URLs (Original)
    path('api/analytics/expenses/departments/', cv(views_analytics.department_expenses_api), name='api_department_expenses_old'),
    path('api/analytics/expenses/categories/<int:department_id>/', cv(views_analytics.category_expenses_api), name='api_category_expenses_old'),
    path('api/analytics/expenses/products/<int:department_id>/<int:category_id>/', cv(views_analytics.product_list_api), name='api_product_list_old'),
    
    # New Bubble Chart Analytics API URLs
    path('api/analytics/departments/expenses/', cv(views_analytics.api_department_expenses), name='api_analytics_departments'),
    path('api/analytics/department/<int:dept_id>/categories/', cv(views_analytics.api_department_categories), name='api_analytics_categories'),
    path('api/analytics/category/<int:cat_id>/subcategories/', cv(views_analytics.api_category_subcategories), name='api_analytics_subcategories'),
    path('api/analytics/category/<int:cat_id>/products/', cv(views_analytics.api_category_products), name='api_analytics_products'),
    
    # Line Chart Analytics API URLs (نمودار خطی)
    path('api/analytics/trend/', cv(views_analytics.api_expense_trend), name='api_expense_trend'),
    path('api/analytics/department/<int:dept_id>/trend/', cv(views_analytics.api_department_trend), name='api_department_trend'),
    path('api/analytics/comparison/', cv(views_analytics.api_comparison_trend), name='api_comparison_trend'),
    
    # Analytics Page: Line Chart (صفحه نمودار خطی)
    path('analytics/line-chart/', cv(views.analytics_line_chart), name='analytics_line_chart'),
    
    # Analytics Page: Price Monitor (صفحه نظارت بر قیمت)
    path('analytics/prices/', cv(views.analytics_price_monitor), name='analytics_price_monitor'),
    
    # Invoice Analysis API URLs (تحلیل خرید بر اساس دسته‌بندی)
    path('api/invoices/subcategories/', cv(views_analytics.api_invoice_subcategories), name='api_invoice_subcategories'),
    path('api/invoices/suppliers/', cv(views_analytics.api_invoice_suppliers), name='api_invoice_suppliers'),
    path('api/invoices/product-history/', cv(views_analytics.api_product_purchase_history), name='api_product_purchase_history'),
    
    # Category Analysis API URLs
    path('api/category-analysis/stats/', cv(views_analytics.api_category_analysis_stats), name='api_category_analysis_stats'),
    path('api/category-analysis/data/', cv(views_analytics.api_category_analysis_data), name='api_category_analysis_data'),
    
    # Management Dashboard API URLs
    path('api/management/category-expenses/', cv(views_analytics.api_management_category_expenses), name='api_management_category_expenses'),
    path('api/management/price-increases/', cv(views_analytics.api_management_price_increases), name='api_management_price_increases'),
    
    # Finance Manager Dashboard API URLs
    path('api/finance/stats/', cv(views_analytics.api_finance_stats), name='api_finance_stats'),
    path('api/finance/recent-payments/', cv(views_analytics.api_finance_recent_payments), name='api_finance_recent_payments'),
    path('api/finance/pending-invoices/', cv(views_analytics.api_finance_pending_invoices), name='api_finance_pending_invoices'),
    
    # Test page
    path('test/filter/', cv(views.test_filter_page), name='test_filter_page'),
    
    # System Monitoring URLs
    path('system/monitoring/', cv(views.system_monitoring), name='system_monitoring'),
    path('system/monitoring/api/', cv(views.system_monitoring_api), name='system_monitoring_api'),
    # Notification System URLs
    path('notifications/', cv(views.notifications_list), name='notifications_list'),
    path('notifications/<int:notification_id>/', cv(views.notification_detail), name='notification_detail'),
    path('notifications/<int:notification_id>/mark-read/', cv(views.mark_notification_read), name='mark_notification_read'),
    path('notifications/<int:notification_id>/delete/', cv(views.delete_notification), name='delete_notification'),
    path('notifications/unread-count/', cv(views.get_unread_notifications_count), name='unread_notifications_count'),
    path('notifications/recent/', cv(views.get_recent_notifications), name='recent_notifications'),

    # Price Dashboard Page
    path('dashboard/prices/', cv(views_dashboard.price_dashboard_view), name='price_dashboard'),
    path('dashboard/prices/test/', cv(views_dashboard.price_test_view), name='price_test'),

    # Dashboard API URLs
    path('api/dashboard/products/changes/', cv(views_dashboard.ProductChangeListAPIView.as_view()), name='api_product_changes'),
    path('api/dashboard/products/high-increase/', cv(views_dashboard.HighIncreaseProductsAPIView.as_view()), name='api_high_increase'),
    path('api/dashboard/products/<int:pk>/history/', cv(views_dashboard.PriceHistoryAPIView.as_view()), name='api_price_history'),
    path('api/dashboard/stats/', cv(views_dashboard.DashboardStatsAPIView.as_view()), name='api_dashboard_stats'),
    path('api/dashboard/filters/', cv(views_dashboard.DashboardFiltersAPIView.as_view()), name='api_dashboard_filters'),
    path('api/dashboard/export/', cv(views_dashboard.DashboardExportAPIView.as_view()), name='api_dashboard_export'),
    
    # Stock Management URLs (مدیریت انبار)
    path('stock/initial-register/', cv(views.initial_stock_register), name='initial_stock_register'),
    path('stock/list/', cv(views.stock_list), name='stock_list'),
    path('stock/<int:stock_id>/transactions/', cv(views.stock_transactions), name='stock_transactions'),
    path('stock/item/<int:item_id>/mark-for-supply/', cv(views.mark_item_for_stock_supply), name='mark_item_for_stock_supply'),
    path('stock/supply-list/', cv(views.stock_supply_list), name='stock_supply_list'),
    path('stock/request/<int:request_id>/fulfill/', cv(views.fulfill_stock_request), name='fulfill_stock_request'),

    # Warehouse Management URLs (مدیریت انبارها)
    path('warehouses/', cv(views.warehouse_management), name='warehouse_management'),
    path('warehouses/create/', cv(views.warehouse_create), name='warehouse_create'),
    path('warehouses/<int:warehouse_id>/edit/', cv(views.warehouse_edit), name='warehouse_edit'),
    path('warehouses/<int:warehouse_id>/toggle-status/', cv(views.warehouse_toggle_status), name='warehouse_toggle_status'),

    # Warehouse Product Definition URLs (تعریف کالا برای انبار)
    path('warehouses/<int:warehouse_id>/products/', cv(views.warehouse_products), name='warehouse_products'),
    path('warehouses/<int:warehouse_id>/products/create/', cv(views.warehouse_product_create), name='warehouse_product_create'),
    path('warehouses/<int:warehouse_id>/products/<int:stock_item_id>/update/', cv(views.warehouse_product_update), name='warehouse_product_update'),
    path('warehouses/<int:warehouse_id>/products/<int:stock_item_id>/delete/', cv(views.warehouse_product_delete), name='warehouse_product_delete'),

    # Warehouse Inventory Check URLs (انبارگردانی)
    path('warehouses/<int:warehouse_id>/inventory-check/', cv(views.warehouse_inventory_check), name='warehouse_inventory_check'),
    path('warehouses/<int:warehouse_id>/inventory-submit/', cv(views.warehouse_inventory_submit), name='warehouse_inventory_submit'),

    # Warehouse Voucher URLs (حواله‌های انبار)
    path('vouchers/', cv(views.voucher_list), name='voucher_list'),
    path('vouchers/<int:warehouse_id>/in/', cv(views.voucher_create), {'voucher_type': 'in'}, name='voucher_create_in'),
    path('vouchers/<int:warehouse_id>/out/', cv(views.voucher_create), {'voucher_type': 'out'}, name='voucher_create_out'),
    path('vouchers/detail/<int:voucher_id>/', cv(views.voucher_detail), name='voucher_detail'),

    # Stock Category URLs (موجودی انبار بر اساس دسته‌بندی)
    path('stock/categories/', cv(views.stock_by_category), name='stock_by_category'),
    path('stock/categories/<int:category_id>/items/', cv(views.stock_category_items), name='stock_category_items'),

    # API URLs for inventory history
    path('api/warehouses/<int:warehouse_id>/products/<int:stock_item_id>/history/', cv(views.api_product_stock_history), name='api_product_stock_history'),
]

# ترکیب مسیرها - مسیرهای احراز هویت + مسیرهای کافه با prefix
urlpatterns = auth_urlpatterns + [
    path('<str:cafe_code>/', include(cafe_urlpatterns)),
]