from django.contrib import admin
from apps.inventory.models import InventoryRecord, StockAdjustmentLog


@admin.register(InventoryRecord)
class InventoryRecordAdmin(admin.ModelAdmin):
    list_display = ('variant', 'available_stock', 'reserved_stock', 'low_stock_threshold', 'allow_backorders', 'updated_at')
    list_filter = ('allow_backorders',)
    search_fields = ('variant__product__name',)
    readonly_fields = ('updated_at',)


@admin.register(StockAdjustmentLog)
class StockAdjustmentLogAdmin(admin.ModelAdmin):
    list_display = ('variant', 'adjusted_by', 'previous_stock', 'new_stock', 'adjustment_type', 'created_at')
    list_filter = ('adjustment_type', 'created_at')
    search_fields = ('variant__product__name', 'reason')
    readonly_fields = ('created_at',)
