from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, OTPRecord, UserAddress


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'phone_number', 'user_type', 'is_temp_password', 'is_active', 'is_staff', 'date_joined')
    list_filter = ('user_type', 'is_active', 'is_staff', 'is_temp_password')
    search_fields = ('username', 'phone_number', 'full_name', 'email')
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined',)
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal Info', {'fields': ('full_name', 'phone_number', 'email')}),
        ('Role & Permissions', {
            'fields': ('user_type', 'is_temp_password', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Important Dates', {'fields': ('last_login', 'date_joined')}),
    )


@admin.register(OTPRecord)
class OTPRecordAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'is_verified', 'attempts_count', 'expires_at', 'created_at', 'ip_address')
    list_filter = ('is_verified', 'created_at')
    search_fields = ('phone_number', 'session_id')
    readonly_fields = ('otp_hash', 'created_at')


@admin.register(UserAddress)
class UserAddressAdmin(admin.ModelAdmin):
    list_display = ('recipient_name', 'user', 'city', 'pincode', 'address_type', 'is_default', 'is_deleted')
    list_filter = ('address_type', 'is_default', 'is_deleted', 'city')
    search_fields = ('recipient_name', 'recipient_phone', 'address_line1', 'pincode')
