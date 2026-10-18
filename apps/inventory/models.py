import uuid
from django.db import models
from django.conf import settings
from apps.catalog.models import ProductVariant


class InventoryRecord(models.Model):
    """
    Live inventory tracking for a product variant.
    Includes check constraints to ensure stock can never drop below zero.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.OneToOneField(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name='inventory'
    )
    available_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Quantity currently available for customer ordering."
    )
    reserved_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Quantity reserved in active carts/confirmed orders before dispatch."
    )
    low_stock_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=5.00,
        help_text="Threshold below which administrative low-stock alerts are triggered."
    )
    allow_backorders = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Inventory Record'
        verbose_name_plural = 'Inventory Records'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(available_stock__gte=0),
                name='inventory_available_stock_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_stock__gte=0),
                name='inventory_reserved_stock_non_negative'
            ),
        ]

    @property
    def is_in_stock(self):
        return self.available_stock > 0

    @property
    def is_low_stock(self):
        return 0 < self.available_stock <= self.low_stock_threshold

    def __str__(self):
        return f"{self.variant}: Available: {self.available_stock} (Reserved: {self.reserved_stock})"


class StockAdjustmentLog(models.Model):
    """
    Audit trail for any stock modification (harvest intake, disposal, manual correction).
    """
    class AdjustmentType(models.TextChoices):
        RESTOCK = 'RESTOCK', 'Restock / New Harvest Arrival'
        MANUAL_CORRECTION = 'MANUAL_CORRECTION', 'Manual Correction'
        DAMAGED_DISPOSAL = 'DAMAGED_DISPOSAL', 'Damaged / Spoiled Disposal'
        AUDIT_COUNT = 'AUDIT_COUNT', 'Periodic Audit Count'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name='stock_adjustments'
    )
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_adjustments_performed'
    )
    previous_stock = models.DecimalField(max_digits=10, decimal_places=2)
    new_stock = models.DecimalField(max_digits=10, decimal_places=2)
    adjustment_type = models.CharField(
        max_length=30,
        choices=AdjustmentType.choices,
        default=AdjustmentType.MANUAL_CORRECTION
    )
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Stock Adjustment Log'
        verbose_name_plural = 'Stock Adjustment Logs'
        ordering = ['-created_at']

    def __str__(self):
        return f"Adjustment {self.variant} ({self.previous_stock} -> {self.new_stock}) by {self.adjusted_by}"
