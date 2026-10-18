from rest_framework import serializers
from decimal import Decimal
from ..authentication.models import CustomUser
from .models import AuditLog, DeliveryPartnerProfile, NotificationEvent, PushDevice, QualityClaim, ClaimPhoto


# ─────────────────────────── Partner Provisioning ────────────────────────────

class DeliveryPartnerCreateSerializer(serializers.Serializer):
    """Admin creates a new delivery partner account with a temporary password."""
    full_name = serializers.CharField(max_length=150)
    username = serializers.CharField(max_length=150)
    phone_number = serializers.CharField(max_length=15)
    temp_password = serializers.CharField(
        min_length=8,
        max_length=128,
        write_only=True,
        help_text="Temporary password; partner must change on first login."
    )
    vehicle_type = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    vehicle_number = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')

    def validate_username(self, value):
        if CustomUser.objects.filter(username=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate_phone_number(self, value):
        if CustomUser.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("A user with this phone number already exists.")
        return value


class DeliveryPartnerProfileSerializer(serializers.ModelSerializer):
    """Full partner profile with user account details."""
    user_id = serializers.UUIDField(source='user.id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    full_name = serializers.CharField(source='user.full_name', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)
    is_active = serializers.BooleanField(source='user.is_active', read_only=True)
    is_temp_password = serializers.BooleanField(source='user.is_temp_password', read_only=True)

    class Meta:
        model = DeliveryPartnerProfile
        fields = [
            'id', 'user_id', 'username', 'full_name', 'phone_number',
            'is_active', 'is_temp_password', 'is_available',
            'vehicle_type', 'vehicle_number',
            'id_proof_type', 'id_proof_number',
            'emergency_contact_name', 'emergency_contact_phone',
            'total_deliveries_completed', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'user_id', 'username', 'full_name', 'phone_number',
                            'is_active', 'is_temp_password', 'total_deliveries_completed',
                            'created_at', 'updated_at']


class PartnerAvailabilityUpdateSerializer(serializers.Serializer):
    """Admin toggles partner availability."""
    is_available = serializers.BooleanField()


class PartnerActivationSerializer(serializers.Serializer):
    """Admin activates or deactivates a partner account."""
    is_active = serializers.BooleanField()


# ─────────────────────────── Quality Claims ──────────────────────────────────

class ClaimPhotoSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ClaimPhoto
        fields = ['id', 'image_url', 'uploaded_at']

    def get_image_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class QualityClaimSerializer(serializers.ModelSerializer):
    """Full claim detail with photos."""
    photos = ClaimPhotoSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    customer_phone = serializers.CharField(source='customer.phone_number', read_only=True)
    customer_name = serializers.CharField(source='customer.full_name', read_only=True)

    class Meta:
        model = QualityClaim
        fields = [
            'id', 'order', 'order_number',
            'customer', 'customer_phone', 'customer_name',
            'description', 'status',
            'resolution_type', 'resolution_notes',
            'manual_refund_amount_inr', 'manual_credit_amount_inr',
            'reviewed_by', 'reviewed_at',
            'photos', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'order', 'order_number', 'customer',
            'customer_phone', 'customer_name',
            'status', 'resolution_type', 'resolution_notes',
            'manual_refund_amount_inr', 'manual_credit_amount_inr',
            'reviewed_by', 'reviewed_at',
            'created_at', 'updated_at',
        ]


class QualityClaimCreateSerializer(serializers.Serializer):
    """
    Customer submits a claim within 24 hours of delivery with photo evidence.
    """
    order_id = serializers.UUIDField(help_text="UUID of the delivered order to claim against.")
    description = serializers.CharField(
        min_length=20,
        max_length=2000,
        help_text="Customer description of the quality issue (min 20 characters)."
    )
    photos = serializers.ListField(
        child=serializers.ImageField(),
        max_length=5,
        min_length=1,
        required=True,
        help_text="At least 1 photo is required; up to 5 are accepted."
    )


class QualityClaimResolveSerializer(serializers.Serializer):
    """Admin resolves a quality claim."""
    resolution_type = serializers.ChoiceField(
        choices=QualityClaim.ResolutionType.choices,
        help_text="Refunds and credits are recorded for manual processing; replacements are approved for manual fulfillment."
    )
    resolution_notes = serializers.CharField(
        max_length=2000,
        help_text="Explanation of the resolution, visible to the customer."
    )
    refund_amount_inr = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.01'), required=False,
        help_text='Required for a refund decision. The approved amount is recorded for manual processing only.'
    )
    credit_amount_inr = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.01'), required=False,
        help_text='Required for a credit decision. The approved amount is recorded for manual credit entry; no wallet is updated.'
    )
    new_status = serializers.ChoiceField(
        choices=[
            (QualityClaim.ClaimStatus.RESOLVED, 'Resolved'),
            (QualityClaim.ClaimStatus.REJECTED, 'Rejected'),
        ],
        default=QualityClaim.ClaimStatus.RESOLVED,
        help_text="Final claim status. Refund/credit approvals are recorded for manual processing; replacement fulfillment is manual."
    )


class CustomerAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'full_name', 'phone_number', 'is_active', 'date_joined']
        read_only_fields = fields


class CustomerActivationSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.full_name', read_only=True, allow_null=True)
    actor_username = serializers.CharField(source='actor.username', read_only=True, allow_null=True)

    class Meta:
        model = AuditLog
        fields = ['id', 'actor', 'actor_name', 'actor_username', 'action', 'object_type',
                  'object_id', 'summary', 'metadata', 'created_at']
        read_only_fields = fields


class NotificationEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationEvent
        fields = ['id', 'recipient', 'event_key', 'event_type', 'object_type', 'object_id',
                  'payload', 'status', 'attempts', 'last_error', 'created_at', 'sent_at']
        read_only_fields = fields


class PushDeviceRegistrationSerializer(serializers.Serializer):
    registration_token = serializers.CharField(max_length=4096, trim_whitespace=True)
    platform = serializers.ChoiceField(choices=PushDevice.Platform.choices)
    app_id = serializers.CharField(max_length=80, required=False, allow_blank=True, default='')


class PushDeviceDeleteSerializer(serializers.Serializer):
    registration_token = serializers.CharField(max_length=4096, trim_whitespace=True)
