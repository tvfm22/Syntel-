from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    User, PurchaseRequest, PurchaseItem, Category, Product,
    Unit, Supplier, Invoice, InvoiceItem, Payment, OfficerAssignment,
    KukerItem, KukerItemCostHistory, MenuSection, CogsReport, ProductPriceEntry,
)

# Custom User Admin
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('نقش کاربری', {'fields': ('role',)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('نقش کاربری', {'fields': ('role',)}),
    )
    search_fields = ('username', 'email', 'first_name', 'last_name')

# Register other models
@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'status', 'created_at', 'processed_by', 'processed_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username',)
    date_hierarchy = 'created_at'

@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'quantity', 'unit', 'request')
    list_filter = ('unit',)
    search_fields = ('name',)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'is_active', 'requires_recipe')
    list_filter = ('is_active', 'requires_recipe')
    search_fields = ('name', 'description')
    ordering = ('name',)

@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'symbol', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'symbol')
    ordering = ('name',)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'unit', 'min_stock', 'current_stock', 'is_active')
    list_filter = ('is_active', 'category', 'created_at')
    search_fields = ('name', 'description')
    list_editable = ('is_active',)
    ordering = ('name',)
    fieldsets = (
        ('اطلاعات پایه', {
            'fields': ('name', 'category', 'unit', 'is_active')
        }),
        ('موجودی', {
            'fields': ('current_stock', 'min_stock')
        }),
        ('توضیحات', {
            'fields': ('description',)
        }),
    )

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'email', 'city', 'status')
    list_filter = ('status', 'city')
    search_fields = ('name', 'phone', 'email', 'card_number', 'sheba_number')
    ordering = ('name',)
    fieldsets = (
        (None, {'fields': ('name', 'supplier_code', 'supplier_type', 'status', 'cafe')}),
        ('اطلاعات تماس', {'fields': ('contact_person', 'phone', 'mobile', 'email', 'website')}),
        ('اطلاعات آدرس', {'fields': ('address', 'city', 'state', 'postal_code', 'country')}),
        ('اطلاعات بانکی', {'fields': ('bank_name', 'bank_account', 'bank_routing', 'card_number', 'sheba_number')}),
        ('سایر', {'fields': ('tax_id', 'registration_number', 'business_license', 'payment_terms', 'credit_limit', 'notes', 'categories')}),
    )

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'supplier', 'total_amount', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('invoice_number', 'supplier__name')
    date_hierarchy = 'created_at'

@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ('product', 'quantity', 'unit_price', 'total_price', 'invoice')
    list_filter = ('invoice__created_at',)
    search_fields = ('product__name', 'invoice__invoice_number')

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'amount', 'payment_date', 'payment_status')
    list_filter = ('payment_status', 'payment_date')
    search_fields = ('invoice__invoice_number', 'reference_number')
    date_hierarchy = 'payment_date'

@admin.register(OfficerAssignment)
class OfficerAssignmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'target_role', 'assigned_to', 'status', 'assigned_at')
    list_filter = ('target_role', 'status', 'assigned_at')
    search_fields = ('item__name', 'assigned_to__username')
    date_hierarchy = 'assigned_at'


@admin.register(MenuSection)
class MenuSectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'cafe', 'display_order', 'is_active')
    list_filter = ('kind', 'cafe', 'is_active')
    search_fields = ('name',)
    ordering = ('cafe', 'kind', 'display_order', 'name')


@admin.register(KukerItem)
class KukerItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'cafe', 'out_unit', 'price', 'product', 'is_custom')
    list_filter = ('kind', 'cafe', 'is_custom')
    search_fields = ('name', 'key', 'code')
    ordering = ('cafe', 'kind', 'name')
    raw_id_fields = ('product', 'section')


@admin.register(KukerItemCostHistory)
class KukerItemCostHistoryAdmin(admin.ModelAdmin):
    list_display = ('item', 'cost_per_unit', 'recorded_at', 'note')
    list_filter = ('item__cafe', 'item__kind')
    search_fields = ('item__name', 'note')
    date_hierarchy = 'recorded_at'
    ordering = ('-recorded_at', '-id')


@admin.register(ProductPriceEntry)
class ProductPriceEntryAdmin(admin.ModelAdmin):
    """تاریخچهٔ «وارد کردن قیمت» مواد اولیه از صفحهٔ قیمت‌گذاری."""
    list_display = ('product', 'entered_amount', 'entered_unit',
                    'price_per_smallest_unit', 'smallest_unit', 'recorded_at')
    list_filter = ('cafe', 'smallest_unit')
    search_fields = ('product__name', 'note')
    date_hierarchy = 'recorded_at'
    ordering = ('-recorded_at', '-id')
    raw_id_fields = ('product',)


@admin.register(CogsReport)
class CogsReportAdmin(admin.ModelAdmin):
    list_display = ('period_start', 'period_end', 'cafe', 'created_by', 'created_at')
    list_filter = ('cafe', 'created_at')
    date_hierarchy = 'created_at'
