from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('variant', 'product_name_snapshot', 'unit_quantity_snapshot', 'unit_price_inr', 'quantity', 'line_total_inr')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer', 'status', 'payment_method', 'payment_status', 'total_payable_inr', 'placed_at')
    list_filter = ('status', 'payment_method', 'payment_status', 'placed_at')
    search_fields = ('order_number', 'customer__phone_number', 'customer__username')
    readonly_fields = ('placed_at', 'updated_at', 'shipping_address_snapshot')
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product_name_snapshot', 'unit_quantity_snapshot', 'unit_price_inr', 'quantity', 'line_total_inr')
    search_fields = ('order__order_number', 'product_name_snapshot')
