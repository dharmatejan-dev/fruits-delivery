import uuid
from decimal import Decimal
from django.db import models
from ..orders.models import Order


class PaymentRecord(models.Model):
    """
    Payment gateway audit record linked to an Order.
    Tracks Razorpay transaction telemetry and webhook confirmations.
    """
    class PaymentStatus(models.TextChoices):
        CREATED = 'CREATED', 'Created'
        AUTHORIZED = 'AUTHORIZED', 'Authorized'
        CAPTURED = 'CAPTURED', 'Captured'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name='payment_record'
    )
    razorpay_order_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        db_index=True
    )
    razorpay_payment_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        db_index=True
    )
    razorpay_signature = models.CharField(
        max_length=255,
        null=True,
        blank=True
    )
    amount_inr = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=5, default='INR')
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.CREATED
    )
    raw_webhook_payload = models.JSONField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Payment Record'
        verbose_name_plural = 'Payment Records'
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment for {self.order.order_number}: ₹{self.amount_inr} [{self.get_status_display()}]"
