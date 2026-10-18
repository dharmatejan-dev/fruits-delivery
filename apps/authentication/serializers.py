import re
from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import CustomUser, UserAddress
from .services.msg91 import normalize_indian_phone

PINCODE_REGEX = re.compile(r'^\d{6}$')


class OTPRequestSerializer(serializers.Serializer):
    """Payload for requesting an OTP."""
    phone_number = serializers.CharField(
        max_length=15,
        help_text="10-digit Indian mobile number (e.g., 9876543210 or +919876543210)"
    )

    def validate_phone_number(self, value):
        return normalize_indian_phone(value)


class OTPVerifySerializer(serializers.Serializer):
    """Payload for verifying an OTP and issuing JWT tokens."""
    phone_number = serializers.CharField(max_length=15)
    otp = serializers.CharField(min_length=4, max_length=6)

    def validate_phone_number(self, value):
        return normalize_indian_phone(value)


class StaffLoginSerializer(serializers.Serializer):
    """Credentials login for Delivery Partners and Admins."""
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')

        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError({"detail": "Invalid username or password."})

        if not user.is_active:
            raise serializers.ValidationError({"detail": "This account has been deactivated."})

        if user.user_type == CustomUser.UserType.CUSTOMER:
            raise serializers.ValidationError({
                "detail": "Customer accounts must authenticate via phone OTP."
            })

        attrs['user'] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    """Password update serializer (mandatory for delivery partners on first login)."""
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        if attrs['old_password'] == attrs['new_password']:
            raise serializers.ValidationError({
                "new_password": ["New password cannot be the same as the old password."]
            })
        return attrs


class UserProfileSerializer(serializers.ModelSerializer):
    """User profile serializer for customers and staff."""
    is_profile_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = CustomUser
        fields = [
            'id',
            'username',
            'phone_number',
            'email',
            'full_name',
            'user_type',
            'is_temp_password',
            'is_profile_complete',
            'date_joined',
        ]
        read_only_fields = [
            'id',
            'username',
            'phone_number',
            'user_type',
            'is_temp_password',
            'date_joined',
        ]


class UserAddressSerializer(serializers.ModelSerializer):
    """Customer delivery address serializer with Indian PIN code validation."""
    class Meta:
        model = UserAddress
        fields = [
            'id',
            'recipient_name',
            'recipient_phone',
            'address_line1',
            'address_line2',
            'landmark',
            'locality',
            'city',
            'state',
            'pincode',
            'latitude',
            'longitude',
            'address_type',
            'is_default',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_pincode(self, value):
        cleaned = str(value).strip()
        if not PINCODE_REGEX.match(cleaned):
            raise serializers.ValidationError("Enter a valid 6-digit Indian PIN code.")
        return cleaned

    def validate_recipient_phone(self, value):
        return normalize_indian_phone(value)

    def create(self, validated_data):
        user = self.context['request'].user
        is_default = validated_data.get('is_default', False)

        # If this is the user's first address, make it default automatically
        if not user.addresses.filter(is_deleted=False).exists():
            validated_data['is_default'] = True
        elif is_default:
            # Unset default on previous addresses
            user.addresses.filter(is_default=True).update(is_default=False)

        validated_data['user'] = user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        user = self.context['request'].user
        if validated_data.get('is_default', False):
            user.addresses.exclude(id=instance.id).filter(is_default=True).update(is_default=False)

        return super().update(instance, validated_data)
