from rest_framework import serializers
from .models import Order, OrderItem


class CartItemInputSerializer(serializers.Serializer):
    """Line item in cart validation and order creation."""
    variant_id = serializers.UUIDField(required=True)
    quantity = serializers.IntegerField(required=True, min_value=1)


class CartValidationRequestSerializer(serializers.Serializer):
    """Cart payload for pre-checkout validation."""
    items = CartItemInputSerializer(many=True, required=True)


class OrderCreateRequestSerializer(serializers.Serializer):
    """Customer checkout payload."""
    address_id = serializers.UUIDField(required=True)
    idempotency_key = serializers.UUIDField(required=False)
    items = CartItemInputSerializer(many=True, required=True)
    payment_method = serializers.ChoiceField(
        choices=Order.PaymentMethod.choices,
        default=Order.PaymentMethod.RAZORPAY
    )
    customer_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500
    )


class OrderItemDetailSerializer(serializers.ModelSerializer):
    """Detail of a fruit item within an order."""
    class Meta:
        model = OrderItem
        fields = [
            'id',
            'variant',
            'product_name_snapshot',
            'unit_quantity_snapshot',
            'unit_price_inr',
            'quantity',
            'line_total_inr',
        ]


class OrderDetailSerializer(serializers.ModelSerializer):
    """Full order detail serializer with frozen address and line items."""
    items = OrderItemDetailSerializer(many=True, read_only=True)
    is_cancellable_by_customer = serializers.BooleanField(read_only=True)
    razorpay_order_id = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id',
            'order_number',
            'status',
            'payment_method',
            'payment_status',
            'subtotal_inr',
            'delivery_charge_inr',
            'discount_inr',
            'total_payable_inr',
            'shipping_address_snapshot',
            'customer_notes',
            'is_cancellable_by_customer',
            'cancellation_reason',
            'cancelled_at',
            'razorpay_order_id',
            'items',
            'placed_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_razorpay_order_id(self, obj):
        payment = getattr(obj, 'payment_record', None)
        return payment.razorpay_order_id if payment else None


class OrderCancelRequestSerializer(serializers.Serializer):
    """Customer or staff order cancellation request."""
    reason = serializers.CharField(
        required=True,
        min_length=3,
        max_length=500,
        help_text="Reason for order cancellation."
    )


class AdminOrderStatusUpdateSerializer(serializers.Serializer):
    """Admin desk order status progression."""
    status = serializers.ChoiceField(choices=Order.OrderStatus.choices)
