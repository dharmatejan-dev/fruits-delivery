import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from ..common.models import TimeStampedUUIDModel
from ..catalog.models import ProductVariant


class Order(TimeStampedUUIDModel):
    """
    Customer fruit delivery order in Vizianagaram.
    """
    class OrderStatus(models.TextChoices):
        PENDING_PAYMENT = 'PENDING_PAYMENT', 'Pending Payment'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        PACKED = 'PACKED', 'Packed'
        ASSIGNED = 'ASSIGNED', 'Assigned for Delivery'
        OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', 'Out for Delivery'
        DELIVERED = 'DELIVERED', 'Delivered'
        CANCELLED = 'CANCELLED', 'Cancelled'
        FAILED = 'FAILED', 'Failed Delivery'

    class PaymentMethod(models.TextChoices):
        RAZORPAY = 'RAZORPAY', 'Razorpay Online'
        COD = 'COD', 'Cash on Delivery'

    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        AUTHORIZED = 'AUTHORIZED', 'Authorized'
        CAPTURED = 'CAPTURED', 'Captured'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'

    order_number = models.CharField(
        max_length=30,
        unique=True,
        db_index=True,
        help_text="Human-readable reference e.g., OH-VZM-20261014-8712"
    )
    idempotency_key = models.UUIDField(null=True, blank=True, editable=False)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='orders'
    )
    status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING_PAYMENT,
        db_index=True
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.RAZORPAY
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True
    )
    subtotal_inr = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_charge_inr = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_inr = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_payable_inr = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_address_snapshot = models.JSONField(
        help_text="Frozen snapshot of recipient name, phone, address, and PIN code at checkout."
    )
    customer_notes = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'
        ordering = ['-placed_at']
        constraints = [
            models.UniqueConstraint(
                fields=['customer', 'idempotency_key'],
                name='unique_customer_order_idempotency_key',
            ),
        ]

    @property
    def is_cancellable_by_customer(self):
        """Customers may self-service cancel while order is in CONFIRMED status before packing."""
        return self.status == self.OrderStatus.CONFIRMED

    def __str__(self):
        return f"{self.order_number} ({self.customer.phone_number}) - ₹{self.total_payable_inr} [{self.get_status_display()}]"


class OrderItem(models.Model):
    """
    Individual fruit line item inside an order.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items'
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.RESTRICT,
        related_name='order_items'
    )
    product_name_snapshot = models.CharField(max_length=150)
    unit_quantity_snapshot = models.CharField(max_length=50)
    unit_price_inr = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    line_total_inr = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Order Item'
        verbose_name_plural = 'Order Items'

    def __str__(self):
        return f"{self.quantity}x {self.product_name_snapshot} ({self.unit_quantity_snapshot}) - ₹{self.line_total_inr}"
