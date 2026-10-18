from rest_framework import serializers
from ..authentication.models import CustomUser
from .models import DeliveryAssignment, GPSPing


class PartnerBriefSerializer(serializers.ModelSerializer):
    """Compact delivery partner representation for dispatch responses."""

    class Meta:
        model = CustomUser
        fields = ['id', 'full_name', 'username', 'phone_number']


class PartnerOrderItemSerializer(serializers.Serializer):
    product_name = serializers.CharField(source='product_name_snapshot')
    unit = serializers.CharField(source='unit_quantity_snapshot')
    quantity = serializers.IntegerField()


class DeliveryAssignmentSerializer(serializers.ModelSerializer):
    """
    Full assignment detail with nested partner info and order reference.
    Used by admin and partner APIs.
    """
    partner = PartnerBriefSerializer(read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    order_status = serializers.CharField(source='order.get_status_display', read_only=True)
    delivery_address = serializers.JSONField(source='order.shipping_address_snapshot', read_only=True)
    payment_method = serializers.CharField(source='order.payment_method', read_only=True)
    amount_due_inr = serializers.DecimalField(source='order.total_payable_inr', max_digits=10, decimal_places=2, read_only=True)
    order_items = PartnerOrderItemSerializer(source='order.items', many=True, read_only=True)

    class Meta:
        model = DeliveryAssignment
        fields = [
            'id',
            'order',
            'order_number',
            'order_status',
            'delivery_address',
            'payment_method',
            'amount_due_inr',
            'order_items',
            'partner',
            'status',
            'partner_notes',
            'failure_reason',
            'assigned_at',
            'picked_up_at',
            'delivered_at',
        ]
        read_only_fields = [
            'id', 'order', 'order_number', 'order_status',
            'partner', 'assigned_at',
        ]


class AssignPartnerRequestSerializer(serializers.Serializer):
    """Request body for admin manually assigning a partner to an order."""
    order_id = serializers.UUIDField(help_text="UUID of the order to assign (must be in PACKED status).")
    partner_id = serializers.UUIDField(help_text="UUID of the delivery partner user.")


class PartnerStatusTransitionSerializer(serializers.Serializer):
    """
    Request body for a delivery partner advancing assignment status.
    Valid transitions managed by DispatchService.
    """
    class TransitionAction(serializers.ChoiceField):
        pass

    action = serializers.ChoiceField(
        choices=[
            ('ACCEPT', 'Accept assignment'),
            ('PICKUP', 'Confirm parcel picked up from facility'),
            ('START_DELIVERY', 'Mark out for delivery'),
            ('DELIVER', 'Mark delivered to customer'),
            ('FAIL', 'Mark delivery as failed'),
        ],
        help_text="The state transition action to perform."
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        help_text="Optional partner notes or failure reason."
    )


class GPSPingCreateSerializer(serializers.Serializer):
    """
    Request body for partner submitting a live GPS ping.
    Submitted every 10–15 seconds while order is OUT_FOR_DELIVERY.
    """
    latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-90,
        max_value=90,
        help_text="Latitude in WGS84 decimal degrees."
    )
    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        help_text="Longitude in WGS84 decimal degrees."
    )
    heading_degrees = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=359,
        help_text="Compass heading 0–359 degrees."
    )
    accuracy_meters = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text="GPS fix accuracy radius in metres."
    )
    recorded_at = serializers.DateTimeField(
        help_text="Device-local ISO 8601 timestamp when location was sampled."
    )


class GPSPingResponseSerializer(serializers.ModelSerializer):
    """Response representation of a GPS ping (read-only)."""

    class Meta:
        model = GPSPing
        fields = [
            'id',
            'latitude',
            'longitude',
            'heading_degrees',
            'accuracy_meters',
            'recorded_at',
            'received_at',
        ]


class LiveLocationResponseSerializer(serializers.Serializer):
    """
    Customer-facing live location response.
    Only exposes coordinates — no partner-identifying data.
    Includes a freshness flag so the client can show a staleness banner.
    """
    order_id = serializers.UUIDField()
    order_number = serializers.CharField()
    assignment_status = serializers.CharField()
    is_live = serializers.BooleanField(
        help_text="True if a fresh GPS ping was received within the last 120 seconds."
    )
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    heading_degrees = serializers.IntegerField(allow_null=True)
    last_ping_at = serializers.DateTimeField(allow_null=True)
    stale_message = serializers.CharField(allow_null=True)


class AdminLiveMapPartnerSerializer(serializers.Serializer):
    """
    Admin live map entry — one entry per partner currently out for delivery.
    Contains partner identity and their latest GPS coordinates.
    """
    partner_id = serializers.UUIDField()
    partner_name = serializers.CharField()
    partner_phone = serializers.CharField()
    order_id = serializers.UUIDField()
    order_number = serializers.CharField()
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    heading_degrees = serializers.IntegerField(allow_null=True)
    last_ping_at = serializers.DateTimeField(allow_null=True)
    is_live = serializers.BooleanField()
