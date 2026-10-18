from django.contrib import admin
from .models import AuditLog, DeliveryPartnerProfile, QualityClaim, ClaimPhoto


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'actor', 'action', 'object_type', 'object_id', 'summary']
    list_filter = ['action', 'object_type', 'created_at']
    search_fields = ['summary', 'object_id', 'actor__username']
    readonly_fields = ['id', 'actor', 'action', 'object_type', 'object_id', 'summary', 'metadata', 'created_at']
    ordering = ['-created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DeliveryPartnerProfile)
class DeliveryPartnerProfileAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'vehicle_type', 'vehicle_number', 'is_available',
        'total_deliveries_completed', 'created_at'
    ]
    list_filter = ['is_available', 'created_at']
    search_fields = ['user__username', 'user__full_name', 'user__phone_number', 'vehicle_number']
    raw_id_fields = ['user']
    readonly_fields = ['id', 'total_deliveries_completed', 'created_at', 'updated_at']

    fieldsets = (
        ('Account', {
            'fields': ('id', 'user', 'is_available')
        }),
        ('Vehicle', {
            'fields': ('vehicle_type', 'vehicle_number')
        }),
        ('Identity Verification', {
            'fields': ('id_proof_type', 'id_proof_number'),
            'classes': ('collapse',)
        }),
        ('Emergency Contact', {
            'fields': ('emergency_contact_name', 'emergency_contact_phone'),
            'classes': ('collapse',)
        }),
        ('Stats & Notes', {
            'fields': ('total_deliveries_completed', 'notes')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


class ClaimPhotoInline(admin.TabularInline):
    model = ClaimPhoto
    extra = 0
    readonly_fields = ['id', 'uploaded_at']


@admin.register(QualityClaim)
class QualityClaimAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'order', 'customer', 'status', 'resolution_type', 'reviewed_by', 'created_at'
    ]
    list_filter = ['status', 'resolution_type', 'created_at']
    search_fields = ['order__order_number', 'customer__phone_number', 'customer__full_name']
    raw_id_fields = ['order', 'customer', 'reviewed_by']
    readonly_fields = ['id', 'reviewed_at', 'created_at', 'updated_at']
    inlines = [ClaimPhotoInline]

    fieldsets = (
        ('Claim Core', {
            'fields': ('id', 'order', 'customer', 'description', 'status')
        }),
        ('Resolution', {
            'fields': ('resolution_type', 'manual_refund_amount_inr', 'resolution_notes', 'reviewed_by', 'reviewed_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
