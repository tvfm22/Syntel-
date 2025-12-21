from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    User, PurchaseRequest, PurchaseItem, Category, Product, 
    Unit, Supplier, Invoice, InvoiceItem, Payment
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
    list_display = ('name', 'parent', 'is_active')
    list_filter = ('is_active',)
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
    search_fields = ('name', 'phone', 'email')
    ordering = ('name',)

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

# Register your models here.
