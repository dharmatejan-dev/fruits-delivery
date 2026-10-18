from django.contrib import admin
from .models import DeliveryAssignment, GPSPing


@admin.register(DeliveryAssignment)
class DeliveryAssignmentAdmin(admin.ModelAdmin):
    list_display = [
        'order', 'partner', 'status', 'assigned_by', 'assigned_at', 'picked_up_at', 'delivered_at'
    ]
    list_filter = ['status', 'assigned_at']
    search_fields = ['order__order_number', 'partner__username', 'partner__full_name']
    raw_id_fields = ['order', 'assigned_by']
    autocomplete_fields = ['partner']
    readonly_fields = ['id', 'assigned_at', 'created_at', 'updated_at']
    ordering = ['-assigned_at']

    fieldsets = (
        ('Assignment Core', {
            'fields': ('id', 'order', 'partner', 'assigned_by', 'status')
        }),
        ('Timestamps', {
            'fields': ('assigned_at', 'picked_up_at', 'delivered_at', 'created_at', 'updated_at')
        }),
        ('Notes & Failure', {
            'fields': ('partner_notes', 'failure_reason'),
            'classes': ('collapse',)
        }),
    )


@admin.register(GPSPing)
class GPSPingAdmin(admin.ModelAdmin):
    list_display = ['assignment', 'latitude', 'longitude', 'heading_degrees', 'recorded_at', 'received_at']
    list_filter = ['received_at']
    search_fields = ['assignment__order__order_number', 'assignment__partner__username']
    raw_id_fields = ['assignment']
    readonly_fields = ['id', 'received_at']
    ordering = ['-received_at']
