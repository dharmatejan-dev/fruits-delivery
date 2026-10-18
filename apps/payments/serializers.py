from rest_framework import serializers
from .models import PaymentRecord


class PaymentVerifyRequestSerializer(serializers.Serializer):
    """Payload submitted by mobile client following Razorpay checkout."""
    order_id = serializers.UUIDField(required=True)
    razorpay_order_id = serializers.CharField(required=True)
    razorpay_payment_id = serializers.CharField(required=True)
    razorpay_signature = serializers.CharField(required=True)


class PaymentRecordSerializer(serializers.ModelSerializer):
    """Payment record serializer."""
    order_number = serializers.CharField(source='order.order_number', read_only=True)

    class Meta:
        model = PaymentRecord
        fields = [
            'id',
            'order_number',
            'razorpay_order_id',
            'razorpay_payment_id',
            'amount_inr',
            'currency',
            'status',
            'verified_at',
            'created_at',
        ]
        read_only_fields = fields
