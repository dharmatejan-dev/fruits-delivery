from decimal import Decimal
from django.db import transaction
from rest_framework.exceptions import ValidationError
from apps.inventory.models import InventoryRecord, StockAdjustmentLog
import logging

logger = logging.getLogger(__name__)


class InventoryService:
    """
    Transaction-safe concurrency manager for stock deduction, restoration,
    and administrative inventory adjustments using MySQL SELECT FOR UPDATE locks.
    """

    @classmethod
    def deduct_stock(cls, variant_id, quantity: Decimal) -> InventoryRecord:
        """
        Atomically locks and deducts available stock while incrementing reserved stock.
        Guarantees protection against double-deduction and overselling.
        """
        qty = Decimal(str(quantity))
        if qty <= 0:
            raise ValidationError({"quantity": ["Deduction quantity must be strictly greater than zero."]})

        with transaction.atomic():
            try:
                record = InventoryRecord.objects.select_for_update().get(variant_id=variant_id)
            except InventoryRecord.DoesNotExist:
                raise ValidationError({"variant": ["Inventory record not found for variant."]})

            if record.available_stock < qty and not record.allow_backorders:
                raise ValidationError({
                    "stock": [
                        f"Insufficient stock for {record.variant.product.name} ({record.variant.display_unit}). "
                        f"Requested: {qty}, Available: {record.available_stock}."
                    ]
                })

            record.available_stock -= qty
            record.reserved_stock += qty
            record.save(update_fields=['available_stock', 'reserved_stock', 'updated_at'])

            logger.info(
                "Stock deducted for variant %s: -%s (New Available: %s, Reserved: %s)",
                variant_id, qty, record.available_stock, record.reserved_stock
            )
            return record

    @classmethod
    def restore_stock(cls, variant_id, quantity: Decimal) -> InventoryRecord:
        """
        Atomically restores stock back to available pool upon order cancellation.
        """
        qty = Decimal(str(quantity))
        if qty <= 0:
            raise ValidationError({"quantity": ["Restoration quantity must be strictly greater than zero."]})

        with transaction.atomic():
            try:
                record = InventoryRecord.objects.select_for_update().get(variant_id=variant_id)
            except InventoryRecord.DoesNotExist:
                raise ValidationError({"variant": ["Inventory record not found for variant."]})

            record.available_stock += qty
            record.reserved_stock = max(Decimal('0.00'), record.reserved_stock - qty)
            record.save(update_fields=['available_stock', 'reserved_stock', 'updated_at'])

            logger.info(
                "Stock restored for variant %s: +%s (New Available: %s, Reserved: %s)",
                variant_id, qty, record.available_stock, record.reserved_stock
            )
            return record

    @classmethod
    def adjust_stock(cls, variant_id, new_available_stock: Decimal, user=None, reason: str = "", adjustment_type: str = "MANUAL_CORRECTION"):
        """
        Administrative adjustment with mandatory audit logging.
        """
        new_stock = Decimal(str(new_available_stock))
        if new_stock < 0:
            raise ValidationError({"available_stock": ["Available stock cannot be negative."]})

        if not reason or not reason.strip():
            raise ValidationError({"reason": ["An audit reason is required for administrative stock adjustments."]})

        with transaction.atomic():
            try:
                record = InventoryRecord.objects.select_for_update().get(variant_id=variant_id)
            except InventoryRecord.DoesNotExist:
                raise ValidationError({"variant": ["Inventory record not found for variant."]})

            previous_stock = record.available_stock
            record.available_stock = new_stock
            record.save(update_fields=['available_stock', 'updated_at'])

            log_entry = StockAdjustmentLog.objects.create(
                variant=record.variant,
                adjusted_by=user,
                previous_stock=previous_stock,
                new_stock=new_stock,
                adjustment_type=adjustment_type,
                reason=reason.strip()
            )

            logger.info(
                "Stock adjusted by admin %s for variant %s: %s -> %s (Reason: %s)",
                user, variant_id, previous_stock, new_stock, reason
            )
            return record, log_entry
