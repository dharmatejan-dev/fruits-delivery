from rest_framework import serializers
from apps.inventory.models import InventoryRecord, StockAdjustmentLog


class InventoryStatusSerializer(serializers.ModelSerializer):
    """Detailed inventory status report for admins."""
    variant_id = serializers.UUIDField(source='variant.id', read_only=True)
    product_name = serializers.CharField(source='variant.product.name', read_only=True)
    product_slug = serializers.CharField(source='variant.product.slug', read_only=True)
    display_unit = serializers.CharField(source='variant.display_unit', read_only=True)
    is_in_stock = serializers.BooleanField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = InventoryRecord
        fields = [
            'id',
            'variant_id',
            'product_name',
            'product_slug',
            'display_unit',
            'available_stock',
            'reserved_stock',
            'low_stock_threshold',
            'is_in_stock',
            'is_low_stock',
            'allow_backorders',
            'updated_at',
        ]


class StockAdjustmentRequestSerializer(serializers.Serializer):
    """Administrative stock adjustment payload."""
    variant_id = serializers.UUIDField(required=True)
    new_available_stock = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    adjustment_type = serializers.ChoiceField(
        choices=StockAdjustmentLog.AdjustmentType.choices,
        default=StockAdjustmentLog.AdjustmentType.MANUAL_CORRECTION
    )
    reason = serializers.CharField(
        min_length=5,
        max_length=500,
        required=True,
        help_text="Mandatory audit explanation for the stock adjustment."
    )


class StockAdjustmentLogSerializer(serializers.ModelSerializer):
    """Log entry serializer for stock audit history."""
    variant_display = serializers.CharField(source='variant.__str__', read_only=True)
    adjusted_by_name = serializers.CharField(source='adjusted_by.username', read_only=True)

    class Meta:
        model = StockAdjustmentLog
        fields = [
            'id',
            'variant',
            'variant_display',
            'adjusted_by',
            'adjusted_by_name',
            'previous_stock',
            'new_stock',
            'adjustment_type',
            'reason',
            'created_at',
        ]
        read_only_fields = fields
