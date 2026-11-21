from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    ActivityLog,
    Cafe,
    Category,
    CogsReport,
    Debt,
    DebtPayment,
    Department,
    Invoice,
    InvoiceItem,
    KukerItem,
    KukerItemCostHistory,
    MenuSection,
    Notification,
    OfficerAssignment,
    Payment,
    PaymentAccount,
    PaymentAllocation,
    PaymentLocation,
    PriceHistory,
    Product,
    ProductPriceEntry,
    PurchaseItem,
    PurchaseOrder,
    PurchaseRequest,
    StockItem,
    StockTransaction,
    Supplier,
    Unit,
    User,
    Warehouse,
    WarehouseVoucher,
    WarehouseVoucherItem,
)


@admin.register(Cafe)
class CafeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'phone', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'phone')
    ordering = ('name',)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'cafe', 'warehouse', 'is_department_warehouse', 'is_active')
    list_filter = ('cafe', 'is_active', 'is_department_warehouse')
    list_select_related = ('cafe', 'warehouse')
    raw_id_fields = ('warehouse',)
    search_fields = ('name', 'cafe__name')
    ordering = ('cafe', 'name')


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'secondary_role', 'cafe', 'department', 'is_staff', 'is_active')
    list_filter = ('role', 'secondary_role', 'cafe', 'is_staff', 'is_active')
    list_select_related = ('cafe', 'department')
    raw_id_fields = ('department',)
    fieldsets = BaseUserAdmin.fieldsets + (
        ('نقش کاربری و کافه', {'fields': ('role', 'secondary_role', 'cafe', 'department')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('نقش کاربری و کافه', {'fields': ('role', 'secondary_role', 'cafe', 'department')}),
    )
    search_fields = ('username', 'email', 'first_name', 'last_name')


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'cafe', 'user', 'source', 'status', 'created_at', 'processed_by', 'processed_at')
    list_filter = ('cafe', 'source', 'status', 'created_at')
    list_select_related = ('cafe', 'user', 'processed_by', 'manager_confirmed_by')
    raw_id_fields = ('user', 'processed_by', 'manager_confirmed_by', 'intended_purchasing_officer')
    search_fields = ('user__username',)
    date_hierarchy = 'created_at'


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'quantity', 'unit', 'supply_status', 'request')
    list_filter = ('supply_status', 'unit')
    list_select_related = ('request', 'product')
    raw_id_fields = ('request', 'product', 'source_item', 'received_confirmed_by')
    search_fields = ('name', 'product__name')


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'cafe', 'parent', 'is_active', 'requires_recipe')
    list_filter = ('cafe', 'is_active', 'requires_recipe')
    list_select_related = ('cafe', 'parent')
    raw_id_fields = ('parent',)
    search_fields = ('name', 'description')
    ordering = ('name',)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'symbol', 'cafe', 'is_active')
    list_filter = ('cafe', 'is_active')
    list_select_related = ('cafe',)
    search_fields = ('name', 'symbol')
    ordering = ('name',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'cafe', 'category', 'unit', 'min_stock', 'current_stock', 'is_active', 'is_complete')
    list_filter = ('cafe', 'is_active', 'is_complete', 'category', 'created_at')
    list_select_related = ('cafe', 'category', 'unit')
    raw_id_fields = ('category', 'unit')
    readonly_fields = ('current_stock',)
    search_fields = ('name', 'description')
    list_editable = ('is_active',)
    ordering = ('name',)
    filter_horizontal = ('departments',)
    fieldsets = (
        ('اطلاعات پایه', {
            'fields': ('cafe', 'name', 'category', 'unit', 'departments', 'is_active', 'is_complete')
        }),
        ('قیمت‌گذاری و موجودی', {
            'fields': ('base_unit_price', 'smallest_unit', 'current_stock', 'min_stock')
        }),
        ('توضیحات', {
            'fields': ('description',)
        }),
    )


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'cafe', 'phone', 'email', 'city', 'status')
    list_filter = ('cafe', 'status', 'city')
    list_select_related = ('cafe', 'created_by')
    raw_id_fields = ('created_by',)
    search_fields = ('name', 'phone', 'email', 'card_number', 'sheba_number')
    ordering = ('name',)
    fieldsets = (
        (None, {'fields': ('name', 'supplier_code', 'supplier_type', 'status', 'cafe')}),
        ('اطلاعات تماس', {'fields': ('contact_person', 'phone', 'mobile', 'email', 'website')}),
        ('اطلاعات آدرس', {'fields': ('address', 'city', 'state', 'postal_code', 'country')}),
        ('اطلاعات بانکی', {'fields': ('bank_name', 'bank_account', 'bank_routing', 'card_number', 'sheba_number')}),
        ('سایر', {'fields': ('tax_id', 'registration_number', 'business_license', 'payment_terms', 'credit_limit', 'notes', 'categories')}),
    )


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'supplier', 'status', 'delivery_status', 'quality_status', 'delivery_date', 'created_at')
    list_filter = ('status', 'delivery_status', 'quality_status', 'created_at')
    list_select_related = ('request', 'supplier', 'created_by')
    raw_id_fields = ('request', 'supplier', 'created_by', 'received_by', 'quality_inspector')
    search_fields = ('order_number', 'supplier__name')
    date_hierarchy = 'created_at'


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'cafe', 'supplier', 'total_amount', 'status', 'payment_status', 'created_at')
    list_filter = ('cafe', 'status', 'payment_status', 'created_at')
    list_select_related = ('cafe', 'supplier', 'purchase_order')
    raw_id_fields = ('purchase_order', 'supplier', 'created_by', 'updated_by', 'approved_by')
    search_fields = ('invoice_number', 'supplier__name')
    date_hierarchy = 'created_at'


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ('product', 'description', 'quantity', 'unit_price', 'line_total', 'invoice')
    list_filter = ('invoice__created_at',)
    list_select_related = ('invoice', 'product', 'requester')
    raw_id_fields = ('invoice', 'order_item', 'product', 'requester')
    search_fields = ('product__name', 'description', 'invoice__invoice_number')


@admin.register(PaymentLocation)
class PaymentLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'cafe', 'is_active', 'display_order')
    list_filter = ('cafe', 'is_active')
    list_select_related = ('cafe',)
    search_fields = ('name', 'code')
    ordering = ('display_order', 'name')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'invoice', 'amount', 'payment_date', 'payment_status', 'payment_location')
    list_filter = ('payment_status', 'payment_date')
    list_select_related = ('invoice', 'payment_location', 'created_by')
    raw_id_fields = ('invoice', 'payment_location', 'created_by')
    search_fields = ('invoice__invoice_number', 'reference_number', 'transaction_id')
    date_hierarchy = 'payment_date'


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    list_display = ('id', 'payment', 'invoice', 'amount', 'created_at')
    list_select_related = ('payment', 'invoice')
    raw_id_fields = ('payment', 'invoice')
    search_fields = ('invoice__invoice_number', 'payment__transaction_id')


@admin.register(PaymentAccount)
class PaymentAccountAdmin(admin.ModelAdmin):
    list_display = ('bank_name', 'account_name', 'account_number', 'cafe', 'is_active')
    list_filter = ('cafe', 'is_active')
    list_select_related = ('cafe',)
    search_fields = ('bank_name', 'account_name', 'account_number', 'card_number')


@admin.register(Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = ('company', 'cafe', 'amount', 'status', 'priority', 'debt_date', 'is_off_list')
    list_filter = ('cafe', 'status', 'priority', 'is_off_list')
    list_select_related = ('cafe', 'supplier', 'account', 'created_by', 'paid_by')
    raw_id_fields = ('supplier', 'account', 'created_by', 'paid_by')
    search_fields = ('company', 'supplier__name')
    date_hierarchy = 'debt_date'


@admin.register(DebtPayment)
class DebtPaymentAdmin(admin.ModelAdmin):
    list_display = ('company', 'cafe', 'amount', 'debt', 'account', 'paid_at')
    list_filter = ('cafe', 'paid_at')
    list_select_related = ('cafe', 'debt', 'account', 'paid_by')
    raw_id_fields = ('debt', 'account', 'paid_by')
    search_fields = ('company',)
    date_hierarchy = 'paid_at'


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ('product', 'price', 'percent_change', 'supplier', 'invoice', 'date')
    list_filter = ('date',)
    list_select_related = ('product', 'supplier', 'invoice')
    raw_id_fields = ('product', 'supplier', 'invoice')
    search_fields = ('product__name', 'supplier__name')
    date_hierarchy = 'date'


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'recipient', 'cafe', 'notification_type', 'priority', 'is_read', 'created_at')
    list_filter = ('cafe', 'notification_type', 'priority', 'is_read')
    list_select_related = ('cafe', 'recipient')
    raw_id_fields = ('recipient',)
    search_fields = ('title', 'message', 'recipient__username')
    date_hierarchy = 'created_at'


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'request', 'created_at')
    list_filter = ('action', 'created_at')
    list_select_related = ('user', 'request')
    raw_id_fields = ('user', 'request')
    search_fields = ('user__username', 'description')
    date_hierarchy = 'created_at'


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'cafe', 'is_active', 'is_supply_enabled')
    list_filter = ('cafe', 'is_active', 'is_supply_enabled')
    list_select_related = ('cafe',)
    search_fields = ('name', 'code')
    ordering = ('cafe', 'name')


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'quantity', 'unit', 'min_quantity', 'last_updated')
    list_filter = ('warehouse__cafe', 'warehouse')
    list_select_related = ('product', 'warehouse', 'unit')
    raw_id_fields = ('product', 'warehouse', 'unit')
    search_fields = ('product__name', 'warehouse__name')


@admin.register(StockTransaction)
class StockTransactionAdmin(admin.ModelAdmin):
    list_display = ('stock_item', 'transaction_type', 'quantity', 'previous_quantity', 'new_quantity', 'voucher', 'created_at')
    list_filter = ('transaction_type', 'created_at')
    list_select_related = ('stock_item', 'stock_item__product', 'stock_item__warehouse', 'unit', 'voucher', 'created_by')
    raw_id_fields = ('stock_item', 'unit', 'voucher', 'created_by')
    search_fields = ('stock_item__product__name', 'description')
    date_hierarchy = 'created_at'


@admin.register(WarehouseVoucher)
class WarehouseVoucherAdmin(admin.ModelAdmin):
    list_display = ('voucher_number', 'voucher_type', 'cafe', 'status', 'source_warehouse', 'destination_warehouse', 'date')
    list_filter = ('cafe', 'voucher_type', 'status')
    list_select_related = ('cafe', 'source_warehouse', 'destination_warehouse', 'department', 'created_by', 'approver')
    raw_id_fields = ('source_warehouse', 'destination_warehouse', 'department', 'created_by', 'approver', 'reverses')
    search_fields = ('voucher_number', 'description')
    date_hierarchy = 'date'


@admin.register(WarehouseVoucherItem)
class WarehouseVoucherItemAdmin(admin.ModelAdmin):
    list_display = ('voucher', 'product', 'stock_item', 'base_quantity', 'unit', 'unit_price')
    list_select_related = ('voucher', 'product', 'stock_item', 'unit')
    raw_id_fields = ('voucher', 'product', 'stock_item', 'unit')
    search_fields = ('voucher__voucher_number', 'product__name')


@admin.register(OfficerAssignment)
class OfficerAssignmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'target_role', 'assigned_to', 'status', 'assigned_at')
    list_filter = ('target_role', 'status', 'assigned_at')
    list_select_related = ('item', 'assigned_to', 'assigned_by', 'stock_item')
    raw_id_fields = ('item', 'assigned_to', 'assigned_by', 'stock_item')
    search_fields = ('item__name', 'assigned_to__username')
    date_hierarchy = 'assigned_at'


@admin.register(MenuSection)
class MenuSectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'cafe', 'display_order', 'is_active')
    list_filter = ('kind', 'cafe', 'is_active')
    list_select_related = ('cafe',)
    search_fields = ('name',)
    ordering = ('cafe', 'kind', 'display_order', 'name')


@admin.register(KukerItem)
class KukerItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'cafe', 'out_unit', 'price', 'product', 'is_custom')
    list_filter = ('kind', 'cafe', 'is_custom')
    list_select_related = ('cafe', 'product', 'section')
    search_fields = ('name', 'key', 'code')
    ordering = ('cafe', 'kind', 'name')
    raw_id_fields = ('product', 'section')


@admin.register(KukerItemCostHistory)
class KukerItemCostHistoryAdmin(admin.ModelAdmin):
    list_display = ('item', 'cost_per_unit', 'recorded_at', 'note')
    list_filter = ('item__cafe', 'item__kind')
    list_select_related = ('item',)
    raw_id_fields = ('item',)
    search_fields = ('item__name', 'note')
    date_hierarchy = 'recorded_at'
    ordering = ('-recorded_at', '-id')


@admin.register(ProductPriceEntry)
class ProductPriceEntryAdmin(admin.ModelAdmin):
    """تاریخچهٔ «وارد کردن قیمت» مواد اولیه از صفحهٔ قیمت‌گذاری."""
    list_display = ('product', 'entered_amount', 'entered_unit',
                    'price_per_smallest_unit', 'smallest_unit', 'recorded_at')
    list_filter = ('cafe', 'smallest_unit')
    list_select_related = ('product', 'cafe', 'recorded_by')
    search_fields = ('product__name', 'note')
    date_hierarchy = 'recorded_at'
    ordering = ('-recorded_at', '-id')
    raw_id_fields = ('product', 'recorded_by')


@admin.register(CogsReport)
class CogsReportAdmin(admin.ModelAdmin):
    list_display = ('period_start', 'period_end', 'cafe', 'created_by', 'created_at')
    list_filter = ('cafe', 'created_at')
    list_select_related = ('cafe', 'created_by')
    raw_id_fields = ('created_by',)
    date_hierarchy = 'created_at'
